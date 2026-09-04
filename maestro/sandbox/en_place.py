"""Le régime **en place** : un projet non versionné se remplit dans sa racine (ticket #839).

Renverse, pour le seul projet **non versionné**, l'option C de la décision D2
([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md)) — « copie du
périmètre + diff soumis à validation ». Décision de l'utilisateur du 2026-08-30,
sur trois faits mesurés (run `cc2d8e447f83`, commentaire du 2026-08-30 sur #703) :

1. l'option C **ne livrait rien, et ne l'a jamais fait** : la copie était refermée
   par le `finally` de `maestro.agents.runtime` avant qu'un diff puisse être
   approuvé — 46 min de run, 8,80 $, **zéro fichier** dans la racine, et pas une
   ligne au journal pour le dire. Le comportement réel n'était pas « copie + diff »
   mais **travail jeté** ;
2. son filet — pouvoir annuler — est le plus faible là où il s'applique : un projet
   non versionné est le plus souvent neuf et vide (p1 l'était), il n'y a rien à
   détruire, donc rien à annuler ;
3. l'autre motif de D2, les **collisions** entre agents simultanés dans un même
   arbre, reste vrai et se traite au lieu d'être ignoré : `maestro.engine.executor`
   **sérialise** les tâches d'un même projet non versionné — une seule à la fois
   dans la racine (`_atelier_projet`, sur le verrou par projet de #705).

Un projet **versionné** ne change pas d'un caractère (`maestro.sandbox.projet` :
worktree + branche `maestro/<tâche>` + fusion, #705), et une tâche sans projet
garde son `mkdtemp()`.

Ce module porte les deux pièces du régime :

- `EspaceEnPlace` — l'espace de travail d'une tâche, qui **est** la racine : rien
  n'est copié, rien n'est retiré à la fermeture, et ce que l'agent écrit est
  visible pendant qu'il l'écrit. Son recensement (empreintes de départ, fichiers
  produits) parcourt la racine **par le périmètre** (`fichiers_du_perimetre`) :
  les chemins exclus ne sont ni relevés ni rendus — un `npm install` de l'agent
  ne fait pas entrer 40 000 fichiers au rapport de run —, et **aucun lien
  symbolique n'est suivi**, la même règle que la copie appliquée à la lecture
  d'un arbre qui n'est plus le nôtre ;
- `FrontiereEcriture` — ce que la copie garantissait par **absence**, la racine
  doit le garantir par **refus** : les outils de fichiers de l'agent sont
  confrontés à la frontière **avant chaque appel** (hook `PreToolUse` de
  `maestro.providers.claude`, celui de la politique de #110). Une écriture qui
  sort de la racine, qui passe par un lien symbolique ou que le périmètre exclut
  est **refusée avec son motif** ; l'agent le lit et poursuit sa tâche, comme
  pour un refus de politique. Une **lecture** n'est refusée que sur ce que le
  périmètre exclut — ce que la copie ne contenait pas, l'agent ne le lit pas non
  plus —, lire hors de la racine n'étant pas le sujet de ce périmètre.

La frontière est **armée par la position, jamais par une option** : `frontiere_de`
la rend si et seulement si l'espace de travail **est** la racine du projet — le
régime en place, et lui seul. Un worktree ou un `mkdtemp()` n'en reçoivent aucune,
et c'est ce qui laisse les deux autres régimes au bit près.

Ce qu'elle **ne couvre pas** est nommé plutôt que tu : `Bash` n'est pas analysé —
un shell peut écrire n'importe où, c'était déjà vrai de la copie et du worktree
(docs/24 §2.5, « `Bash` mal formé »), et c'est ce que le **mode isolé** ferme,
`maestro.sandbox.container` montant la racine avec ses masques. `Glob`/`Grep` ne
sont pas confrontés non plus : ils ne modifient rien, et la rédaction (#109,
`maestro.projets.secrets`) couvre ce qu'ils pourraient citer.

`Perimetre.inclus` ne restreint rien ici : l'inclusion disait ce qu'on **montrait**
à l'agent, or l'agent est dans la racine. Seules les exclusions tiennent — et
elles tiennent à l'écriture. Le worktree ne l'appliquait pas davantage (une copie
conforme de la branche) : depuis ce lot, `inclus` ne restreint aucun espace dérivé.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.projets.modele import Perimetre, Projet
from maestro.projets.perimetre import motifs_compiles
from maestro.projets.racine import RacineRefusee, canonique, chemin_dans_racine
from maestro.sandbox.workspace import Workspace

#: Les outils dont un argument est un chemin de fichier, et le nom de cet
#: argument — ceux du CLI Claude Code (`maestro.agents.runtime.DEFAULT_TOOLS` en
#: expose une partie). Un outil absent d'ici n'est pas confronté à la frontière.
OUTILS_A_CHEMIN: Mapping[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookRead": "notebook_path",
    "NotebookEdit": "notebook_path",
}

#: Ceux des outils ci-dessus qui **écrivent** : pour eux la frontière est entière
#: (racine, liens, exclusions) ; pour les autres seules les exclusions valent.
OUTILS_ECRITURE: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


@dataclass(frozen=True)
class EspaceEnPlace(Workspace):
    """L'espace de travail qui **est** la racine d'un projet non versionné (#839).

    `perimetre` est celui du projet : c'est par lui que l'espace s'énumère. Le
    reste du contrat de `Workspace` tient tel quel — `derive` relève l'empreinte
    de départ, `produced_files` rend ce qui a changé depuis.
    """

    perimetre: Perimetre = field(default_factory=Perimetre)

    def fichiers(self) -> Iterator[Path]:
        """Les fichiers de la racine **par le périmètre** : exclusions sautées, liens ignorés."""
        exclus = motifs_compiles(self.perimetre.exclus)
        for relatif in fichiers_du_perimetre(self.path, exclus):
            yield self.path / relatif


def fichiers_du_perimetre(racine: Path, exclus: tuple[re.Pattern[str], ...]) -> Iterator[str]:
    """Les chemins relatifs (POSIX) des fichiers de `racine` que `exclus` ne retire pas.

    Parcours itératif (pas récursif) : un projet réel a des arborescences
    profondes et la pile de Python n'est pas le bon endroit pour en dépendre. Un
    dossier exclu n'est **même pas parcouru** — ce qui vaut autant pour la sûreté
    (rien sous `secrets/` n'est seulement lu) que pour le temps de relevé
    (`node_modules` n'est jamais énuméré). **Aucun lien symbolique n'est suivi**
    — ni fichier ni dossier : c'est le vecteur d'évasion de docs/24 §2.5, et
    `Path.rglob` le suivrait.

    C'est le parcours que la copie de #224 faisait avant ce lot, ramené à la
    seule question qui reste : *qu'y a-t-il dans la racine que l'agent ait le
    droit de voir ?*
    """
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        base = racine / relatif_dossier if relatif_dossier else racine
        try:
            with os.scandir(base) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:  # dossier devenu illisible : sauté, jamais fatal
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if entree.is_symlink() or _correspond(relatif, exclus):
                continue
            if entree.is_dir():
                pile.append(relatif)
            elif entree.is_file():
                yield relatif


@dataclass(frozen=True)
class FrontiereEcriture:
    """La frontière que les outils de fichiers de l'agent ne franchissent pas (#839, EF-38).

    `racine` est **canonique** (`valider_racine`/`canonique`), `exclus` sont les
    motifs compilés du périmètre. `refus` rend le motif qui interdit un appel
    d'outil, ou `None` s'il passe — c'est la seule question que le hook lui pose.

    Trois refus, dans cet ordre, chacun avec sa phrase :

    1. **hors de la racine** — `chemin_dans_racine` (#221) résout le chemin puis
       le vérifie **sous** la racine : un `..`, un chemin absolu d'ailleurs ou un
       lien pointant dehors sortent par là. Une **lecture** hors racine n'est pas
       refusée : le périmètre borne ce qu'on écrit chez l'utilisateur, pas ce
       qu'un agent peut lire sur le poste — c'était déjà vrai de la copie ;
    2. **par un lien symbolique** — le chemin résolu diffère du chemin demandé
       (à la casse près : Windows en change). Un lien **vers l'intérieur** de la
       racine passerait le premier contrôle et ferait écrire ailleurs que là où
       l'agent croit écrire ; le critère du ticket est « aucun lien symbolique
       n'est suivi », sans distinguer où il mène ;
    3. **exclu du périmètre** — le chemin, **ou l'un de ses dossiers parents**,
       est visé par une exclusion. Le motif `node_modules` ne matche que le
       dossier, et c'est un fichier dessous que l'agent écrit : sans remonter les
       parents, l'exclusion ne vaudrait que pour le nom exact. Vaut en lecture
       comme en écriture — ce que la copie ne contenait pas, l'agent ne le lit
       ni ne l'écrit.
    """

    racine: Path
    exclus: tuple[re.Pattern[str], ...]

    @classmethod
    def pour(cls, racine: Path | str, perimetre: Perimetre) -> FrontiereEcriture:
        """La frontière de `racine` sous `perimetre` — racine canonicalisée, motifs compilés."""
        return cls(racine=canonique(racine), exclus=motifs_compiles(perimetre.exclus))

    def refus(self, outil: str, arguments: Any) -> str | None:
        """Le motif qui interdit l'appel `outil(arguments)`, ou `None` s'il passe.

        `arguments` est le `tool_input` brut du SDK : ce qui n'est pas un objet, ou
        n'a pas l'argument de chemin attendu, passe — un outil sans chemin n'a
        rien à confronter, et deviner en ferait refuser à tort.
        """
        cle = OUTILS_A_CHEMIN.get(outil)
        if cle is None or not isinstance(arguments, Mapping):
            return None
        brut = arguments.get(cle)
        if not isinstance(brut, str) or not brut.strip():
            return None
        return self.refus_chemin(brut, ecriture=outil in OUTILS_ECRITURE)

    def refus_chemin(self, brut: str, *, ecriture: bool) -> str | None:
        """Le motif qui interdit d'atteindre `brut`, ou `None` — `ecriture` dit le geste."""
        geste = "Écriture" if ecriture else "Lecture"
        candidat = self._candidat(brut)
        try:
            cible = chemin_dans_racine(self.racine, candidat)
        except RacineRefusee as refus:
            if not ecriture:
                return None
            return (
                f"{geste} refusée : {brut} sort de la racine du projet ({self.racine}) — "
                f"{refus.motif}. Un projet non versionné se remplit dans sa racine, "
                "jamais au-dessus ni à côté (EF-38)."
            )
        if not _meme_chemin(cible, candidat):
            return (
                f"{geste} refusée : {brut} passe par un lien symbolique (→ {cible}). "
                "Aucun lien n'est suivi dans la racine d'un projet — écris à "
                "l'emplacement réel, ou à un chemin qui n'en traverse pas."
            )
        relatif = cible.relative_to(self.racine).as_posix()
        if _sous_exclusion(relatif, self.exclus):
            return (
                f"{geste} refusée : {relatif} est exclu du périmètre du projet "
                "(`.git`, `node_modules`, `.env`, `**/secrets/**` et les motifs "
                "déclarés). Ces chemins ne sont ni lus ni écrits par un agent."
            )
        return None

    def _candidat(self, brut: str) -> Path:
        """Le chemin **lexical** demandé : joint à la racine s'il est relatif, `..` repliés.

        C'est lui qu'on compare au chemin résolu : la résolution suit les liens,
        le repli lexical non — leur écart est exactement un lien traversé.
        """
        chemin = Path(brut.strip()).expanduser()
        if not chemin.is_absolute():
            chemin = self.racine / chemin
        return Path(os.path.normpath(chemin))


def frontiere_de(workspace: Path | str, projet: Projet | None) -> FrontiereEcriture | None:
    """La frontière à armer sur `workspace`, ou `None` — le régime en place, et lui seul.

    Armée si et seulement si l'espace de travail **est** la racine du projet :
    c'est la définition du régime, et c'est un fait que l'appelant peut vérifier
    sans qu'on lui passe un drapeau. Un worktree (hors racine, par construction)
    et un `mkdtemp()` (sans projet) n'en reçoivent aucune — leur régime ne bouge
    pas —, et un projet non versionné dont l'espace ne serait *pas* la racine non
    plus : armer la frontière de la racine sur un autre chemin ferait refuser
    toute écriture dans l'espace où l'agent travaille.
    """
    if projet is None:
        return None
    if not _meme_chemin(canonique(workspace), canonique(projet.racine)):
        return None
    return FrontiereEcriture.pour(projet.racine, projet.perimetre)


def _meme_chemin(un: Path, autre: Path) -> bool:
    """`un` et `autre` désignent-ils le même chemin, à la casse près (comparaison de l'OS) ?"""
    return os.path.normcase(str(un)) == os.path.normcase(str(autre))


def _correspond(relatif: str, motifs: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il visé par l'un des motifs ?"""
    return any(motif.match(relatif) for motif in motifs)


def _sous_exclusion(relatif: str, exclus: tuple[re.Pattern[str], ...]) -> bool:
    """`relatif` est-il exclu, lui **ou l'un de ses dossiers parents** ?

    Le pendant de « un dossier exclu n'est même pas parcouru » : ce que le
    parcours ne descend pas, la frontière ne le laisse pas écrire.
    """
    morceaux = relatif.split("/")
    return any(
        _correspond("/".join(morceaux[: profondeur + 1]), exclus)
        for profondeur in range(len(morceaux))
    )
