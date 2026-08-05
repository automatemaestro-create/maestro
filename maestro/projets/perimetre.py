"""Application des motifs de périmètre d'un projet (ticket #226, EF-37).

Le socle (#221) **déclare** un périmètre — `Perimetre(inclus=…, exclus=…)`,
motifs relatifs à la racine, `exclus` l'emportant sur `inclus`. Ce module
l'**applique** au disque et répond à la seule question dont le mode isolé a
besoin : *quels chemins du projet le périmètre retire-t-il ?*

C'est cette liste que le conteneur **masque** (`maestro.sandbox.container`) —
« ce qui est exclu n'est pas monté » — et c'est elle aussi qui nomme les
gisements de secrets à couvrir par la rédaction (`maestro.projets.secrets`).

Trois partis pris, tous dictés par l'usage :

1. **le plus haut chemin l'emporte** — un dossier exclu est rendu tel quel, sans
   descendre dedans. Un `node_modules` de 40 000 fichiers coûte une entrée, pas
   quarante mille, et c'est aussi ce qu'un montage sait masquer d'un seul geste ;
2. **aucun lien symbolique n'est suivi ni rendu** — même règle que la copie du
   périmètre (#224) : c'est le vecteur d'évasion de docs/24 §2.5, et un lien vers
   `~/.ssh` n'a pas à devenir un chemin que l'on manipule ;
3. **rien n'est plafonné ici** — la liste est rendue entière et c'est l'appelant
   qui décide ce qu'une liste trop longue signifie pour lui (le conteneur, lui,
   refuse plutôt que de monter à moitié).

Les conventions de motifs sont celles de `.gitignore`, parce que c'est ce qu'un
utilisateur écrit sans y penser : `.` désigne tout, un motif sans `/` vaut à
toute profondeur (`node_modules` attrape `apps/web/node_modules`), et `**`
traverse les séparateurs là où `*` et `?` s'arrêtent au segment.

⚠ Le lot 4 de la phase (#224, livré en parallèle) porte la **même** mécanique de
motifs en privé dans `maestro/sandbox/projet.py`, pour la copie du périmètre :
les deux lots étant marqués « (parallèle) », aucun des deux ne pouvait s'appuyer
sur l'autre. Les comportements sont identiques, motif pour motif — replier la
copie privée sur ce module est un geste du lot final (#220).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.projets.modele import Perimetre


@dataclass(frozen=True)
class Exclu:
    """Un chemin que le périmètre retire : son chemin relatif et sa nature.

    `chemin` est **relatif à la racine analysée**, en POSIX (`apps/web/.env`) :
    c'est la forme qui traverse un `docker run` et un JSON sans échappement.
    `dossier` dit s'il faut le masquer par un dossier vide ou par un fichier
    vide — la distinction se constate ici, sur l'hôte, plutôt que de laisser le
    shim refaire un `stat` sur un chemin qu'il n'a pas énuméré.
    """

    chemin: str
    dossier: bool


def exclusions(racine: Path | str, perimetre: Perimetre) -> tuple[Exclu, ...]:
    """Les chemins de `racine` que `perimetre` exclut, les plus hauts d'abord.

    Parcours itératif (la pile de Python n'est pas le bon endroit où compter sur
    la profondeur d'un projet réel), déterministe (entrées triées), et qui **ne
    descend jamais** dans un chemin déjà exclu : un dossier exclu est un verdict,
    pas une invitation à l'énumérer.

    Rendue vide si la racine n'est pas un dossier lisible : ce module constate,
    il ne juge pas la racine — c'est le travail de `maestro.projets.racine`.
    """
    base = Path(racine)
    motifs = motifs_compiles(perimetre.exclus)
    if not motifs:
        return ()
    trouves: list[Exclu] = []
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        courant = base / relatif_dossier if relatif_dossier else base
        try:
            with os.scandir(courant) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:  # dossier illisible ou disparu : sauté, jamais fatal
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if entree.is_symlink():
                continue
            dossier = entree.is_dir()
            if _correspond(relatif, motifs):
                trouves.append(Exclu(chemin=relatif, dossier=dossier))
            elif dossier:
                pile.append(relatif)
    return tuple(sorted(trouves, key=lambda exclu: exclu.chemin))


def fichiers_exclus(racine: Path | str, perimetre: Perimetre) -> Iterator[Path]:
    """Les **fichiers** que le périmètre retire, y compris sous un dossier exclu.

    Là où `exclusions` s'arrête au plus haut chemin — ce qu'il faut pour masquer
    un montage —, celle-ci descend : un `secrets/` exclu porte des fichiers dont
    la rédaction (#109) doit connaître les valeurs. Les liens symboliques ne sont
    jamais suivis, à aucune profondeur.
    """
    base = Path(racine)
    for exclu in exclusions(base, perimetre):
        chemin = base / exclu.chemin
        if not exclu.dossier:
            yield chemin
            continue
        for trouve in sorted(chemin.rglob("*")):
            if trouve.is_file() and not trouve.is_symlink():
                yield trouve


def motifs_compiles(motifs: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """Compile des motifs de périmètre en expressions régulières ancrées."""
    return tuple(
        _compile(normalise) for motif in motifs for normalise in _normalisations(motif)
    )


def _correspond(relatif: str, motifs: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il visé par l'un des motifs ?"""
    return any(motif.match(relatif) for motif in motifs)


def _normalisations(motif: str) -> tuple[str, ...]:
    """Les formes normalisées d'un motif — une, parfois deux.

    - `.` (et la chaîne vide) désigne **tout** — c'est `INCLUS_DEFAUT` ;
    - un motif **sans `/`** vaut à **toute profondeur** : `.env` attrape
      `services/api/.env` ;
    - un motif finissant par `/**` vise aussi **le dossier lui-même**, sans quoi
      `**/secrets/**` exclurait le contenu de `secrets/` mais pas son nom — et
      c'est le nom qu'un montage masque d'un seul geste.
    """
    normalise = motif.strip().replace("\\", "/")
    while normalise.startswith("./"):
        normalise = normalise[2:]
    normalise = normalise.rstrip("/")
    if normalise in ("", "."):
        return ("**",)
    if "/" not in normalise:
        normalise = f"**/{normalise}"
    if normalise.endswith("/**"):
        return (normalise, normalise[: -len("/**")])
    return (normalise,)


def _compile(normalise: str) -> re.Pattern[str]:
    """Un motif normalisé en expression régulière ancrée sur le chemin entier.

    `**` traverse les séparateurs, `*` et `?` s'arrêtent au segment — la
    distinction qui manque à `fnmatch`, dont le `*` avale les `/` et ferait
    d'`*.py` un motif récursif. Les classes `[…]` ne sont pas gérées : elles ne
    servent à rien dans un périmètre de projet.
    """
    segments = normalise.split("/")
    dernier = len(segments) - 1
    morceaux = [
        (".*" if index == dernier else "(?:[^/]+/)*")
        if segment == "**"
        else _segment(segment) + ("" if index == dernier else "/")
        for index, segment in enumerate(segments)
    ]
    return re.compile("".join(morceaux) + r"\Z")


def _segment(segment: str) -> str:
    """Un segment de motif (sans `/`) en fragment d'expression régulière."""
    return "".join(
        "[^/]*" if caractere == "*" else "[^/]" if caractere == "?" else re.escape(caractere)
        for caractere in segment
    )
