"""Les trois racines où Maestro pose quelque chose sur le poste, hors d'un projet (#1454).

Le 2026-10-08, le poste de référence portait **1 286** dossiers `maestro-*` sous
son répertoire temporaire et une dizaine à la racine du profil : ateliers d'hôte,
espaces de tâches, résidus de l'ancien mode démo, chacun né là où son auteur
l'avait jugé bon, la plupart sans personne pour les ramasser. La règle qui en
répond tient en trois lignes, et ce module en est la seule source :

- `~/Maestro/` (`racine_projets`) — les projets de l'utilisateur, proposés par
  défaut (#1022). Jamais ramassée.
- `~/.maestro/` (`racine_etat`) — l'état du poste : jeton de l'API, journal
  SQLite, profil de navigateur, et sous `ateliers/` (`racine_ateliers`, #1457)
  les ateliers du banc des scénarios et de la relecture visuelle. Seuls ces
  ateliers sont vidés, et par **rétention** — leurs derniers passages restent,
  ce sont les pièces d'un rouge (`maestro.scenarios.projets.retenir`).
- `<temp>/maestro/` (`racine_jetable`) — le jetable : ateliers d'hôte, espaces
  des tâches, copies de vérification. Ramassée (`maestro.sandbox.ramassage`).

Tout autre dossier que le produit crée hors d'un projet est un défaut, et
`tests/test_emplacements.py` le fait rougir : sous `maestro/`, aucun module ne
compose un chemin sous le dossier personnel ni n'ouvre un dossier temporaire
sans passer par ici, hors des lectures que son inventaire nomme avec leur raison.

**Le jetable naît marqué.** `jetable` inscrit le pid du process qui l'ouvre dans
le nom (`marquer`, #992) : la question « quelqu'un l'occupe-t-il encore ? » se
pose alors au système, et le ramassage peut retirer la dépouille d'un process
tué sans attendre qu'elle refroidisse. Le seul jetable qui naît sans marque est
celui dont l'occupant n'est **pas** le process qui l'ouvre — l'atelier d'un hôte
détaché, ouvert par l'API pour un process fils qui n'existe pas encore : son
occupant s'y nomme lui-même dans `TEMOIN_PID`, sitôt lancé.

**Pourquoi un sous-dossier du répertoire temporaire, et non `~/.maestro/`.** Ce
que l'agent appelle `/tmp` dans son Bash est le répertoire temporaire du profil
(montage `usertemp` de MSYS, cf. `maestro.sandbox.ramassage`) : son espace de
travail y reste lisible sous le même nom des deux côtés, et le poste garde la
main sur ce qu'il en fait (nettoyage de disque, quota). Le sous-dossier `maestro/`
est ce qui rend le tout **visible d'un coup d'œil et retirable d'un geste**.

Rien n'est résolu à l'import : chaque racine se lit à l'appel, si bien qu'un test
qui déplace le dossier personnel (`Path.home`, #221) ou le temporaire
(`TMPDIR`) déplace aussi tout ce qui en dépend. Rien n'est créé non plus, sauf
par `jetable`, qui crée ce qu'il rend.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

#: Le dossier des projets sous le profil — proposé, jamais imposé (#1022).
NOM_PROJETS = "Maestro"

#: Le dossier de l'état du poste sous le profil.
NOM_ETAT = ".maestro"

#: Le dossier des ateliers sous l'état du poste (#1457) : un sous-dossier par
#: atelier (`scenarios`, `relecture`), chacun porteur de racines de projet.
NOM_ATELIERS = "ateliers"

#: Le sous-dossier du répertoire temporaire qui porte tout le jetable.
NOM_JETABLE = "maestro"

#: Le témoin par lequel l'occupant d'un jetable **non marqué** se nomme : son pid,
#: en texte. Un fichier et non le nom, parce que l'occupant naît après le dossier
#: (voir l'en-tête) ; jamais dans un espace de tâche, où l'agent le verrait dans
#: son `ls` et où il ressortirait en livrable — ceux-là sont marqués par le nom.
TEMOIN_PID = "occupant.pid"

#: Les variables qui désignent le répertoire temporaire, dans l'ordre où
#: `tempfile` les consulte — le même, pour que la racine retenue reste celle que
#: la bibliothèque standard aurait choisie quand rien ne cloche.
VARIABLES_TEMP: tuple[str, ...] = ("TMPDIR", "TEMP", "TMP")


# ── Le profil ─────────────────────────────────────────────────────────────────


def maison() -> Path:
    """Le dossier personnel — lève `RuntimeError`/`OSError` quand l'OS ne sait pas le dire.

    Chaque appelant traduit l'échec dans sa propre langue (`ConfigError`,
    `RacineRefusee`…) : c'est lui qui sait quel réglage nommer à la place.
    """
    return Path.home()


def racine_projets() -> Path:
    """`~/Maestro` — le répertoire des projets proposé quand rien n'est réglé. Non créé."""
    return maison() / NOM_PROJETS


def racine_etat() -> Path:
    """`~/.maestro` — l'état du poste. Non créé : chaque écrivain pose ce qu'il écrit."""
    return maison() / NOM_ETAT


def racine_ateliers() -> Path:
    """`~/.maestro/ateliers` — sous quoi le banc et la relecture sèment leurs projets. Non créé.

    Sous l'état du poste et non dans le jetable : un atelier porte des **racines de
    projet**, que `valider_racine` refuse sous `AppData` (#221), donc sous le
    répertoire temporaire d'un poste Windows. Voisiner avec le jeton de l'API n'en
    approche aucun agent : sa frontière est la racine de son projet (#839,
    `maestro.portee`), et le jeton en est aussi loin d'ici que de `~/maestro-scenarios`.
    """
    return racine_etat() / NOM_ATELIERS


# ── Le répertoire temporaire ──────────────────────────────────────────────────


def _valeur_msys(brut: str) -> bool:
    """Cette valeur de `TMPDIR` est-elle un chemin MSYS, illisible pour Python ?

    Sous Windows seulement, et sur un seul critère : un chemin **enraciné sans
    lecteur** (`/tmp`). Un chemin Windows porte son lecteur (`C:\\…`) ou son hôte
    (`\\\\serveur\\partage`), jamais une barre oblique seule en tête ; `//serveur/…`
    est une UNC écrite à la POSIX et reste donc lisible.
    """
    if sys.platform != "win32":
        return False
    return brut.startswith("/") and not brut.startswith("//")


def repertoire_temporaire(environnement: Mapping[str, str] | None = None) -> Path:
    """Le répertoire temporaire du poste — celui que le Bash de l'agent appelle `/tmp`.

    La règle de `tempfile`, moins les valeurs MSYS (#992, S13, démontré dans
    `maestro.sandbox.ramassage`) : c'est ce qui fait tomber Python et le Bash de
    l'agent au même endroit sous Windows. Hors Windows rien ne change — aucune
    valeur n'est écartée, et l'absence de toute variable retombe sur
    `tempfile.gettempdir()`, qui garde ses replis (`/tmp`, le répertoire courant…).

    ⚠ **`tempfile.tempdir` n'est pas consulté**, et c'est une décision : ce
    n'est pas seulement le point de surcharge programmatique de `tempfile`, c'est
    aussi son **cache** — le premier `gettempdir()` du process y écrit ce qu'il
    vient de résoudre. Une valeur trouvée là ne dit donc pas si quelqu'un l'a
    voulue ou si la bibliothèque s'en souvient. Qui veut imposer une racine passe
    par l'environnement — là même d'où vient le défaut qu'on répare.
    """
    env = os.environ if environnement is None else environnement
    for nom in VARIABLES_TEMP:
        brut = (env.get(nom) or "").strip()
        if not brut or _valeur_msys(brut):
            continue
        candidat = Path(brut)
        try:
            if candidat.is_dir():
                return candidat
        except OSError:  # pragma: no cover - valeur non représentable
            continue
    return Path(tempfile.gettempdir())


def racine_jetable(environnement: Mapping[str, str] | None = None) -> Path:
    """`<temp>/maestro` — sous quoi naît tout le jetable du produit. Non créé."""
    return repertoire_temporaire(environnement) / NOM_JETABLE


def marquer(prefixe: str) -> str:
    """Le préfixe d'un jetable, augmenté du pid de ce process (`maestro-dev-pid4312-`).

    Dans le **nom** et non dans un fichier témoin : un témoin posé au milieu d'un
    espace de tâche ressortirait en livrable (`Workspace.produced_files` recense
    tout ce qui s'y trouve) et l'agent le verrait dans son `ls`. Le nom, lui, ne
    coûte rien à personne et survit à tout ce que l'agent écrit.
    """
    return f"{prefixe}pid{os.getpid()}-"


def jetable(
    prefixe: str,
    *,
    marque: bool = True,
    environnement: Mapping[str, str] | None = None,
) -> Path:
    """Crée et rend un dossier neuf sous `racine_jetable`, nommé `prefixe` + aléa.

    `marque=False` est réservé au jetable dont l'occupant n'est pas ce process
    (l'atelier d'un hôte détaché) : il devra se nommer dans `TEMOIN_PID`. Lève
    `OSError` si le répertoire temporaire n'accueille rien — à l'appelant de dire
    ce que ça empêche.
    """
    racine = racine_jetable(environnement)
    racine.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=marquer(prefixe) if marque else prefixe, dir=racine))


def se_nommer_occupant(dossier: Path) -> None:
    """Inscrit le pid de ce process dans le `TEMOIN_PID` de `dossier` — best-effort.

    Un témoin qu'on n'a pas su écrire laisse le dossier au seul critère de l'âge
    (`maestro.sandbox.ramassage`) : c'est le régime d'avant, jamais une panne.
    """
    try:
        (dossier / TEMOIN_PID).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def occupant(dossier: Path) -> int | None:
    """Le pid que le `TEMOIN_PID` de `dossier` nomme — `None` s'il n'y en a pas de lisible."""
    try:
        brut = (dossier / TEMOIN_PID).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(brut) if brut.isdigit() else None
