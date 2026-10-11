"""Ce que Maestro a posé sur le poste — montré racine par racine, purgé sur un « oui » (#1458).

    .venv/Scripts/python.exe -m maestro.menage [--check]
    .venv/Scripts/python.exe -m maestro.menage --purger [<nom>…]

Le 2026-10-08, le poste de référence portait des dossiers de Maestro partout : 1 286 sous
son répertoire temporaire, huit à la racine du profil, quatre sous `~/.maestro/` que rien n'y
déclarait. Le chantier #1454 a rangé ce que le produit pose sous **trois racines**
(`maestro.emplacements`) et fait ramasser seul ce qui y est jetable. Restait ce qu'il ne
pouvait pas ranger : ce qui est né **avant**, et ce que des sessions de développement ont
posé au jugé (`~/maestro-essais`, `~/maestro-mesure-882`, `~/.maestro/banc-728`…). Aucun
ramasseur ne peut les reconnaître — ils ne nomment personne —, et en deviner la propriété
serait exactement la faute qu'on répare. Il y faut donc deux choses : une **règle** qui les
empêche de naître (`CLAUDE.md`, docs/24 §2.7), et un **geste humain** pour solder ceux d'avant.
Ce module est ce geste.

**Ce qu'il montre.** Chaque racine déclarée, puis ce qui est hors de toute racine, une ligne
par élément, avec sa taille, son **propriétaire** quand on le connaît, et son **sort** :

- `~/Maestro/` — les projets de l'utilisateur : montrés, **jamais** retirés ;
- `~/.maestro/` — l'état du poste : ce que le produit y déclare (jeton de l'API, journaux
  locaux, ateliers, historique du banc, profils de navigateur) est à sa place ; tout autre
  élément est **non déclaré** ;
- `<temp>/maestro/` — le jetable : chaque dossier avec son occupant, vivant ou non, et le
  verdict du ramassage (`maestro.sandbox.ramassage`), qui le retirera seul ;
- **hors racine** — les `~/maestro-*` du profil et les `maestro-*` des anciennes adresses
  du répertoire temporaire ;
- **les déclarations de projets dont la racine a disparu** (#1477) — une par ligne, avec sa
  copie et sa racine.

**Les sorts**, dans l'ordre où ils se décident :

1. `jamais retiré` — la racine des projets ;
2. `déclaré` — à sa place sous une racine ;
3. `conservé` — il porte un **projet déclaré** ou le répertoire des projets réglé, ou il est
   **vivant** : un occupant qui tourne, un worktree dont le dépôt existe (du travail non
   commité), une activité de moins de six heures. La règle est celle du ramassage
   (`raison_de_garder`), lue et non recopiée ;
4. `à son propriétaire` — un autre mécanisme le possède et le retire (l'état d'une stack,
   l'ancien atelier du banc ou de la relecture, le cache des présentations) ;
5. `ramassable` — un jetable dont l'occupant est mort : le ramassage le retirera ;
6. `à retirer` — ce qu'aucun ramasseur ne connaît, et que seul ce geste solde.

**La purge.** `--purger` montre le même inventaire, puis demande un **« oui »** sur l'entrée
standard : toute autre réponse, ou aucune, ne retire rien (code `3`). Seuls partent les
éléments `à retirer` — tous, ou ceux qu'on nomme après `--purger`. Le sort de chacun est
**rejugé au moment de le retirer** : un dossier devenu vivant entre la liste et la réponse
reste. Jamais `~/Maestro/`, jamais un projet déclaré — dans **aucune** copie du dépôt : les
déclarations vivent dans `core/projets/` et dans le jeu du banc de chaque copie
(`depots_de_declarations`), et un projet déclaré depuis un worktree n'en est pas moins un
projet. Quand une de ces déclarations ne se lit pas, rien n'est retiré à l'aveugle : tout est
`conservé`, et la raison le dit.

**Une déclaration dont la racine a disparu ne protège rien** (#1477). Le 2026-10-11, dix
déclarations du banc, racines parties avec leur passage, gardaient `~/maestro-scenarios`
« porteur d'un projet déclaré », et avec lui les worktrees de ses dépôts. Elle est `à retirer`
à son tour : `--purger` l'oublie par le verbe du produit (`ProjetStore.supprimer`, celui de
`DELETE /api/projets`), jamais en effaçant son fichier, et son identifiant la désigne. Disparue
veut dire **à coup sûr** (`racine_disparue`) : un lecteur débranché, un partage qui ne répond
pas, un accès refusé laissent la racine protéger comme si elle était là. La rétention du banc
oublie d'elle-même celles de ses passages partis (`maestro.scenarios.projets.retenir`).

**Pourquoi un « oui » lu par la commande, et non une option.** La purge est destructive et
porte sur ce que personne n'a déclaré : la seule autorité est la personne qui regarde la
liste. Une option `--oui` se recopie d'une commande à l'autre sans que personne ait rien vu ;
la réponse, elle, suit la liste qu'on vient d'imprimer. Une session qui a reçu ce « oui » en
conversation le transmet sur l'entrée standard, et le dit.

Les noms ne sont **jamais recopiés** (#830, patron de `maestro.controltower.purge`) : les
racines viennent de `maestro.emplacements`, le jeton, les journaux, les ateliers, l'historique
et les familles tierces des modules qui les écrivent. Les deux noms qu'aucun module Python
n'écrit — posés par des scripts — vivent ici, et `tests/test_menage.py` vérifie que les
scripts les posent tels quels.

Codes de sortie : `0` montré, ou purge faite ; `1` un élément a résisté au retrait ; `2`
usage ; `3` purge non confirmée, ou déclarations de projets illisibles.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TextIO

from maestro.config import Settings, load_settings
from maestro.controltower.acces import FICHIER_JETON
from maestro.controltower.donnees import donnees_du_banc
from maestro.controltower.hote_detache import PREFIXE_ATELIER
from maestro.controltower.persistence import SUFFIXE_SQLITE
from maestro.emplacements import (
    NOM_ATELIERS,
    NOM_ETAT,
    NOM_JETABLE,
    NOM_PROJETS,
    maison,
    occupant,
    racine_etat,
    racine_jetable,
    racine_projets,
    repertoire_temporaire,
)
from maestro.fichiers import retirer_arbre
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.projets.store import ProjetStore
from maestro.sandbox.ramassage import (
    FAMILLE_ETAT_STACK,
    FAMILLE_ETAT_STACK_JETABLE,
    FAMILLE_PRESENTATION,
    PREFIXE_COMMUN,
    pid_dans,
    pid_vivant,
    racines_connues,
    raison_de_garder,
    seuil_du_poste,
)
from maestro.scenarios.historique import DOSSIER_HISTORIQUE
from maestro.scenarios.projets import ANCIEN_NOM_ATELIER

#: Le nom sous lequel ce module s'invoque (`python -m …`) — dérivé, jamais écrit.
MODULE = __spec__.name if __spec__ is not None else __name__

_USAGE = f"Usage : python -m {MODULE} [--check] | --purger [<nom>…]"

#: Le profil de navigateur que `scripts/setup.sh` pose sous l'état du poste pour le clone
#: principal, et que `scripts/git/worktree.sh` décline par worktree (`chrome-profile-<iid>`).
#: Écrit ici parce qu'aucun module Python ne le pose ; `tests/test_menage.py` le confronte
#: aux deux scripts.
NOM_PROFIL_NAVIGATEUR = "chrome-profile"

#: L'atelier de la relecture visuelle d'avant #1457, à la racine du profil, que
#: `scripts/design/relecture-visuelle.sh` reprend puis retire vide. Même raison d'être ici.
ANCIEN_ATELIER_RELECTURE = "maestro-relecture"

#: Où vivent les déclarations et les réglages de projets dans une copie du dépôt — les
#: défauts de `ProjetStore.default` et `ReglagesProjetsStore.default`, relus pour **chaque**
#: copie (voir l'en-tête).
DECLARATIONS_DANS_UNE_COPIE = Path("core") / "projets"
REGLAGES_DANS_UNE_COPIE = Path("core") / "reglages"

#: La racine de la copie du dépôt qui joue ce module.
RACINE_DU_DEPOT = Path(__file__).resolve().parents[1]

#: Le délai de `git worktree list`, en secondes : une commande locale, qui rend en
#: millisecondes ; au-delà, on tient la liste des copies pour illisible.
DELAI_GIT_S = 30.0

# Les racines, telles qu'on les écrit à la personne — dérivées des noms déclarés.
RACINE_PROJETS = f"~/{NOM_PROJETS}/"
RACINE_ETAT = f"~/{NOM_ETAT}/"
RACINE_JETABLE = f"<temp>/{NOM_JETABLE}/"
HORS_RACINE = "hors racine"
DECLARATIONS = "déclarations de projets"

# Les sorts (voir l'en-tête), dans l'ordre où ils se décident.
JAMAIS = "jamais retiré"
DECLARE = "déclaré"
CONSERVE = "conservé"
A_SON_PROPRIETAIRE = "à son propriétaire"
RAMASSABLE = "ramassable"
A_RETIRER = "à retirer"

CODE_FAIT = 0
CODE_RESISTANCE = 1
CODE_USAGE = 2
CODE_NON_CONFIRME = 3

#: La seule réponse qui vaut accord, à la casse et aux espaces près.
REPONSE_ACCORD = "oui"

#: Ce que l'inventaire dit d'un élément hors racine qu'aucun mécanisme ne possède.
INCONNU = "inconnu — posé hors des racines"

#: Pourquoi un jetable est ramassable : le process qu'il nommait ne tourne plus.
OCCUPANT_PARTI = "occupant parti"

#: Le mécanisme qui possède chaque famille que le ramassage laisse à un autre
#: (`FAMILLES_TIERCES` aux anciennes adresses, `FAMILLES_TIERCES_JETABLES` sous la racine
#: jetable) — indexé par ses constantes, jamais par un préfixe recopié. Une famille ajoutée
#: là-bas sans propriétaire ici fait rougir `tests/test_menage.py`.
PROPRIETAIRES_TIERS: dict[str, str] = {
    FAMILLE_ETAT_STACK: (
        "état d'une stack à l'ancienne adresse — scripts/controltower/start.sh le retire"
    ),
    FAMILLE_PRESENTATION: "cache des présentations (scripts/presentation/captures.sh)",
}
PROPRIETAIRES_TIERS_JETABLES: dict[str, str] = {
    FAMILLE_ETAT_STACK_JETABLE: (
        "état de la stack d'une copie de travail (scripts/controltower/etat-stack.sh) — "
        "part avec sa copie"
    ),
}

_PROFIL_DE_WORKTREE = re.compile(rf"^{re.escape(NOM_PROFIL_NAVIGATEUR)}-(\d+)$")
_IID_DE_WORKTREE = re.compile(r"^(\d+)(?:-|$)")


@dataclass(frozen=True)
class Orpheline:
    """Une déclaration de projet dont la racine a disparu (#1477) : elle ne protège plus rien."""

    depot: Path
    id: str
    nom: str
    racine: str

    @property
    def fichier(self) -> Path:
        return ProjetStore(self.depot).fichier(self.id)


@dataclass(frozen=True)
class Entree:
    """Un élément posé sur le poste : où, par qui, et ce qui en sera fait."""

    chemin: Path
    racine: str
    proprietaire: str
    sort: str
    raison: str = ""
    taille: int | None = None
    #: La déclaration que l'élément est, quand c'en est une : elle s'oublie par le dépôt.
    declaration: Orpheline | None = None

    @property
    def a_retirer(self) -> bool:
        return self.sort == A_RETIRER


@dataclass(frozen=True)
class Protections:
    """Ce qu'une purge ne touche jamais, lu dans **toutes** les copies du dépôt.

    `projets` porte les racines des projets déclarés et les répertoires des projets
    réglés, `~/Maestro/` compris. `worktrees` porte les iids des worktrees vivants (un
    profil de navigateur part avec le sien). `illisible` dit ce qui n'a pas pu être lu —
    et tant qu'il dit quelque chose, rien n'est retiré. `orphelines` porte les
    déclarations dont la racine a disparu à coup sûr (`racine_disparue`) : elles ne
    protègent rien, et la purge les oublie.
    """

    projets: tuple[Path, ...] = ()
    worktrees: frozenset[str] = frozenset()
    illisible: tuple[str, ...] = field(default_factory=tuple)
    orphelines: tuple[Orpheline, ...] = ()


# ── Les copies du dépôt et leurs déclarations ─────────────────────────────────


def copies_du_depot(racine: Path = RACINE_DU_DEPOT) -> tuple[tuple[Path, ...], str]:
    """Les copies de travail du dépôt (clone principal et worktrees), et ce qui a manqué.

    Par `git worktree list --porcelain`, une seule question. Sans dépôt Git (une
    installation), la copie qui joue est la seule, et rien n'a manqué. Avec un dépôt dont
    Git ne rend pas la liste, la copie qui joue reste lisible mais la raison est rendue :
    un projet déclaré depuis un worktree qu'on ne voit pas n'en serait pas moins un projet.
    """
    if not (racine / ".git").exists():
        return (racine,), ""
    try:
        sortie = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (racine,), f"copies du dépôt illisibles ({exc})"
    if sortie.returncode != 0:
        return (racine,), f"copies du dépôt illisibles (git : {sortie.stderr.strip()})"
    copies = [
        Path(ligne.removeprefix("worktree ").strip())
        for ligne in sortie.stdout.splitlines()
        if ligne.startswith("worktree ")
    ]
    return (tuple(copies) or (racine,)), ""


def depots_de_declarations(
    settings: Settings | None = None, copies: Sequence[Path] | None = None
) -> tuple[tuple[Path, ...], str]:
    """Les dépôts de déclarations de projets du poste, et ce qui a manqué pour les lister.

    Dans **chaque** copie du dépôt : son `core/projets/` et celui de son jeu du banc
    (`donnees_du_banc`, #1164) — le banc nu déclare dans le premier, le banc qui sauve
    son état dans le second —, plus `MAESTRO_PROJETS_DIR` s'il est réglé. La rétention
    du banc y oublie les projets de ses passages partis (#1477), ce module y lit ce qu'il
    protège.
    """
    settings = settings or load_settings()
    manque = ""
    if copies is None:
        copies, manque = copies_du_depot()
    depots: list[Path] = []
    for copie in copies:
        depots.append(copie / DECLARATIONS_DANS_UNE_COPIE)
        depots.append(donnees_du_banc(racine=copie).racines["projets"])
    if settings.projets_dir:
        depots.append(Path(settings.projets_dir))
    return tuple(depots), manque


def racine_disparue(racine: Path) -> bool:
    """Vrai seulement quand la racine d'un projet manque **à coup sûr** (#1477).

    À coup sûr : le système dit qu'elle n'existe pas, **et** son volume répond. Un
    lecteur débranché, un partage qui ne répond pas, un accès refusé laissent la
    question ouverte — et une racine indécidable protège comme si elle était là : un
    projet qu'on ne voit pas n'en est pas moins un projet.
    """
    try:
        os.stat(racine)
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError:
        return False
    else:
        return False
    ancre = racine.anchor
    return bool(ancre) and os.path.isdir(ancre)


def protections(
    settings: Settings | None = None, copies: Sequence[Path] | None = None
) -> Protections:
    """Les projets déclarés, les répertoires des projets et les worktrees, de toutes les copies."""
    settings = settings or load_settings()
    illisible: list[str] = []
    if copies is None:
        copies, manque = copies_du_depot()
        if manque:
            illisible.append(manque)
    depots, _ = depots_de_declarations(settings, copies)
    reglages = [copie / REGLAGES_DANS_UNE_COPIE for copie in copies]
    if settings.reglages_dir:
        reglages.append(Path(settings.reglages_dir))

    projets: list[Path] = []
    orphelines: list[Orpheline] = []
    try:
        projets.append(racine_projets())
    except (RuntimeError, OSError):  # pragma: no cover - dépend de l'environnement
        illisible.append("dossier personnel introuvable")
    for depot in depots:
        store = ProjetStore(depot)
        for id_ in store.ids():
            try:
                projet = store.lire(id_)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                illisible.append(f"déclaration {store.fichier(id_)} illisible ({exc})")
                continue
            if projet is None:
                continue
            if racine_disparue(Path(projet.racine)):
                orphelines.append(Orpheline(depot, id_, projet.nom, projet.racine))
            else:
                projets.append(Path(projet.racine))
    for dossier in reglages:
        repertoire = ReglagesProjetsStore(dossier).lire().repertoire
        if repertoire:
            projets.append(Path(repertoire).expanduser())

    worktrees = frozenset(
        trouve.group(1) for copie in copies if (trouve := _IID_DE_WORKTREE.match(copie.name))
    )
    return Protections(tuple(projets), worktrees, tuple(illisible), tuple(orphelines))


def _cle(chemin: Path) -> str:
    """Un chemin comparable à un autre : absolu, résolu, à la casse du système de fichiers."""
    try:
        resolu = chemin.resolve()
    except OSError:  # pragma: no cover - chemin non représentable
        resolu = chemin.absolute()
    return os.path.normcase(str(resolu))


def _projet_porte(chemin: Path, projets: Iterable[Path]) -> Path | None:
    """Le projet que `chemin` est, contient ou dans lequel il vit — `None` sinon."""
    cle = _cle(chemin)
    for projet in projets:
        autre = _cle(projet)
        if cle == autre or autre.startswith(cle + os.sep) or cle.startswith(autre + os.sep):
            return projet
    return None


# ── La taille ─────────────────────────────────────────────────────────────────


def _est_un_lien(chemin: Path | os.DirEntry[str]) -> bool:
    """Un lien symbolique, ou une jonction Windows — que Python ne nomme qu'à partir de 3.12."""
    jonction = getattr(chemin, "is_junction", None)
    return chemin.is_symlink() or bool(jonction is not None and jonction())


def taille(chemin: Path) -> int:
    """Les octets sous `chemin`, sans suivre un lien ni une jonction — jamais de levée.

    Un lien ou une jonction compte pour lui-même et pas pour sa cible : un worktree lie le
    `.venv` du clone principal, et le compter ferait passer pour sien ce qui ne l'est pas.
    """
    try:
        if _est_un_lien(chemin):
            return 0
        if chemin.is_file():
            return chemin.stat().st_size
    except OSError:
        return 0
    total = 0
    pile = [chemin]
    while pile:
        dossier = pile.pop()
        try:
            entrees = list(os.scandir(dossier))
        except OSError:
            continue
        for entree in entrees:
            try:
                if _est_un_lien(entree):
                    continue
                if entree.is_dir(follow_symlinks=False):
                    pile.append(Path(entree.path))
                else:
                    total += entree.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def lisible(octets: int | None) -> str:
    """Une taille en clair, à la française : `45 o`, `12,3 Ko`, `1,2 Go`."""
    if octets is None:
        return "—"
    valeur = float(octets)
    for unite in ("o", "Ko", "Mo", "Go"):
        if valeur < 1024 or unite == "Go":
            texte = f"{valeur:.0f}" if unite == "o" else f"{valeur:.1f}".replace(".", ",")
            return f"{texte} {unite}"
        valeur /= 1024
    return f"{octets} o"  # pragma: no cover - atteint seulement par la boucle


# ── L'inventaire ──────────────────────────────────────────────────────────────


def _enfants(racine: Path) -> list[Path]:
    """Les éléments de `racine`, triés — vide si elle est absente ou illisible."""
    try:
        return sorted(racine.iterdir())
    except OSError:
        return []


def _occupant_du_jetable(dossier: Path) -> str:
    """Qui occupe un jetable, en clair — le pid de son nom, sinon celui de son témoin."""
    pid = pid_dans(dossier.name) or occupant(dossier)
    if pid is None:
        return "aucun occupant nommé"
    return f"pid {pid} {'vivant' if pid_vivant(pid) else 'mort'}"


def _juger_candidat(
    entree: Entree, gardes: Protections, *, seuil_s: float, maintenant: float | None
) -> Entree:
    """Le sort d'un élément qu'aucune racine ne déclare : conservé, à un propriétaire, à retirer.

    Les questions se posent dans l'ordre de l'en-tête, et la première qui garde l'emporte :
    un projet d'abord (même sous un propriétaire connu, c'est ce qu'on dit en premier), un
    propriétaire ensuite, puis l'illisible, puis la vie.
    """
    projet = _projet_porte(entree.chemin, gardes.projets)
    if projet is not None:
        motif = (
            "c'est un projet déclaré, ou le répertoire des projets"
            if _cle(projet) == _cle(entree.chemin)
            else f"porte le projet {projet.as_posix()}"
        )
        return replace(entree, sort=CONSERVE, raison=motif)
    if entree.sort == A_SON_PROPRIETAIRE:
        return entree
    if gardes.illisible:
        return replace(
            entree,
            sort=CONSERVE,
            raison="rien n'est retiré à l'aveugle : " + " ; ".join(gardes.illisible),
        )
    vie = raison_de_garder(entree.chemin, maintenant=maintenant, seuil_s=seuil_s)
    if vie is not None:
        return replace(entree, sort=CONSERVE, raison=vie)
    return replace(entree, sort=A_RETIRER, raison=entree.raison)


def _etat_du_poste(gardes: Protections) -> list[Entree]:
    """`~/.maestro/` : le déclaré à sa place, le reste en candidat."""
    racine = racine_etat()
    declares = {
        FICHIER_JETON: "jeton de l'API (maestro.controltower.acces)",
        NOM_ATELIERS: "ateliers du banc et de la relecture, gardés par rétention",
        DOSSIER_HISTORIQUE: "historique des passages du banc (maestro.scenarios.historique)",
        NOM_PROFIL_NAVIGATEUR: "profil de navigateur du clone principal (scripts/setup.sh)",
    }
    entrees = []
    for chemin in _enfants(racine):
        nom = chemin.name
        if nom in declares:
            entrees.append(Entree(chemin, RACINE_ETAT, declares[nom], DECLARE))
        elif nom.endswith(SUFFIXE_SQLITE) or f"{SUFFIXE_SQLITE}-" in nom:
            journal = "journal local d'un espace (maestro.controltower.persistence)"
            entrees.append(Entree(chemin, RACINE_ETAT, journal, DECLARE))
        elif trouve := _PROFIL_DE_WORKTREE.match(nom):
            iid = trouve.group(1)
            proprietaire = f"profil de navigateur du worktree #{iid} (scripts/git/worktree.sh)"
            if iid in gardes.worktrees:
                entrees.append(Entree(chemin, RACINE_ETAT, proprietaire, DECLARE))
            else:
                entrees.append(
                    Entree(chemin, RACINE_ETAT, proprietaire, A_RETIRER, "worktree retiré")
                )
        else:
            entrees.append(Entree(chemin, RACINE_ETAT, "non déclaré", A_RETIRER))
    return entrees


def _jetable(
    environnement: Mapping[str, str], seuil_s: float, maintenant: float | None
) -> list[Entree]:
    """`<temp>/maestro/` : chaque jetable, son occupant et le verdict du ramassage."""
    entrees = []
    for chemin in _enfants(racine_jetable(environnement)):
        nom = chemin.name
        tiers = next((t for t in PROPRIETAIRES_TIERS_JETABLES if nom.startswith(t)), None)
        if tiers is not None:
            tiers_ = PROPRIETAIRES_TIERS_JETABLES[tiers]
            entrees.append(Entree(chemin, RACINE_JETABLE, tiers_, A_SON_PROPRIETAIRE))
            continue
        famille = "atelier d'un hôte détaché" if nom.startswith(PREFIXE_ATELIER) else "jetable"
        proprietaire = f"{famille} — {_occupant_du_jetable(chemin)}"
        vie = raison_de_garder(chemin, maintenant=maintenant, seuil_s=seuil_s)
        sort, raison = (RAMASSABLE, OCCUPANT_PARTI) if vie is None else (CONSERVE, vie)
        entrees.append(Entree(chemin, RACINE_JETABLE, proprietaire, sort, raison))
    return entrees


def _hors_racine(
    anciennes_adresses: Iterable[Path], seuil_s: float, maintenant: float | None
) -> list[Entree]:
    """Les `~/maestro-*` du profil, et les `maestro-*` des anciennes adresses du temporaire."""
    entrees = []
    anciens_ateliers = {
        ANCIEN_NOM_ATELIER: (
            "ancien atelier du banc — sa rétention le reprend (maestro.scenarios.projets)"
        ),
        ANCIEN_ATELIER_RELECTURE: (
            "ancien atelier de la relecture — scripts/design/relecture-visuelle.sh le reprend"
        ),
    }
    prefixe = PREFIXE_COMMUN.casefold()
    for chemin in _enfants(maison()):
        nom = chemin.name
        if not nom.casefold().startswith(prefixe):
            continue
        if nom in anciens_ateliers:
            entrees.append(Entree(chemin, HORS_RACINE, anciens_ateliers[nom], A_SON_PROPRIETAIRE))
        else:
            entrees.append(Entree(chemin, HORS_RACINE, INCONNU, A_RETIRER))
    for adresse in anciennes_adresses:
        for chemin in _enfants(adresse):
            nom = chemin.name
            if not nom.startswith(PREFIXE_COMMUN) or not chemin.is_dir():
                continue
            tiers = next((t for t in PROPRIETAIRES_TIERS if nom.startswith(t)), None)
            if tiers is not None:
                entrees.append(
                    Entree(chemin, HORS_RACINE, PROPRIETAIRES_TIERS[tiers], A_SON_PROPRIETAIRE)
                )
                continue
            proprietaire = f"jetable à l'ancienne adresse — {_occupant_du_jetable(chemin)}"
            vie = raison_de_garder(chemin, maintenant=maintenant, seuil_s=seuil_s)
            sort, raison = (RAMASSABLE, OCCUPANT_PARTI) if vie is None else (CONSERVE, vie)
            entrees.append(Entree(chemin, HORS_RACINE, proprietaire, sort, raison))
    return entrees


def _declarations_orphelines(gardes: Protections) -> list[Entree]:
    """Les déclarations dont la racine a disparu : à oublier — sauf si une autre ne se lit pas."""
    entrees = []
    for orpheline in gardes.orphelines:
        sort, raison = A_RETIRER, f"racine disparue : {orpheline.racine}"
        if gardes.illisible:
            sort = CONSERVE
            raison = "rien n'est retiré à l'aveugle : " + " ; ".join(gardes.illisible)
        proprietaire = f"projet « {orpheline.nom} » ({orpheline.id})"
        entrees.append(
            Entree(orpheline.fichier, DECLARATIONS, proprietaire, sort, raison, None, orpheline)
        )
    return entrees


def inventorier(
    gardes: Protections,
    *,
    environnement: Mapping[str, str] | None = None,
    anciennes_adresses: Iterable[Path] | None = None,
    tailles: bool = True,
    maintenant: float | None = None,
) -> tuple[Entree, ...]:
    """Tout ce que Maestro a posé hors d'un projet, racine par racine — lecture seule.

    `anciennes_adresses` est injectable pour les tests ; sans elle, ce sont celles que le
    ramassage balaie (`racines_connues`), qui comprennent le répertoire temporaire lui-même.
    """
    env = os.environ if environnement is None else environnement
    seuil_s = seuil_du_poste(env)
    adresses = racines_connues(env) if anciennes_adresses is None else tuple(anciennes_adresses)
    entrees: list[Entree] = []
    projets = racine_projets()
    if projets.exists():
        entrees.append(Entree(projets, RACINE_PROJETS, "les projets de l'utilisateur", JAMAIS))
    for entree in _etat_du_poste(gardes):
        if entree.sort == A_RETIRER:
            entree = _juger_candidat(entree, gardes, seuil_s=seuil_s, maintenant=maintenant)
        entrees.append(entree)
    entrees.extend(_jetable(env, seuil_s, maintenant))
    for entree in _hors_racine(adresses, seuil_s, maintenant):
        if entree.sort in (A_RETIRER, A_SON_PROPRIETAIRE):
            entree = _juger_candidat(entree, gardes, seuil_s=seuil_s, maintenant=maintenant)
        entrees.append(entree)
    entrees.extend(_declarations_orphelines(gardes))
    if tailles:
        entrees = [replace(entree, taille=taille(entree.chemin)) for entree in entrees]
    return tuple(entrees)


# ── Le compte rendu ───────────────────────────────────────────────────────────


def _affiche(chemin: Path, environnement: Mapping[str, str]) -> str:
    """Un chemin tel qu'on le lit : sous `~` ou `<temp>` quand il y est, entier sinon."""
    for base, etiquette in ((repertoire_temporaire(environnement), "<temp>"), (maison(), "~")):
        try:
            relatif = chemin.relative_to(base)
        except ValueError:
            continue
        return f"{etiquette}/{relatif.as_posix()}"
    return str(chemin)


def rendu(entrees: Sequence[Entree], environnement: Mapping[str, str] | None = None) -> list[str]:
    """Les lignes de l'inventaire : un titre par racine, une ligne par élément, puis le bilan."""
    env = os.environ if environnement is None else environnement
    titres = (
        (RACINE_PROJETS, f"{RACINE_PROJETS} — les projets de l'utilisateur, jamais retirés"),
        (RACINE_ETAT, f"{RACINE_ETAT} — l'état du poste"),
        (RACINE_JETABLE, f"{RACINE_JETABLE} — le jetable, ramassé seul"),
        (HORS_RACINE, "hors racine — ce qui n'aurait pas dû naître là"),
        (DECLARATIONS, f"{DECLARATIONS} — dont la racine a disparu : elles ne protègent rien"),
    )
    lignes: list[str] = []
    for racine, titre in titres:
        groupe = [entree for entree in entrees if entree.racine == racine]
        lignes.append("")
        lignes.append(titre)
        if not groupe:
            lignes.append("  (rien)")
            continue
        largeur = max(len(_affiche(entree.chemin, env)) for entree in groupe)
        for entree in groupe:
            sort = f"{entree.sort} : {entree.raison}" if entree.raison else entree.sort
            lignes.append(
                f"  {_affiche(entree.chemin, env).ljust(largeur)}  {lisible(entree.taille):>9}  "
                f"{entree.proprietaire}  →  {sort}"
            )
    a_retirer = [entree for entree in entrees if entree.a_retirer]
    ramassables = [entree for entree in entrees if entree.sort == RAMASSABLE]
    lignes.append("")
    if a_retirer:
        total = sum(entree.taille or 0 for entree in a_retirer)
        lignes.append(
            f"À retirer sur un « oui » : {len(a_retirer)} élément(s), {lisible(total)} — "
            f"python -m {MODULE} --purger [<nom>…]"
        )
    else:
        lignes.append("Rien à retirer : ce que Maestro a posé est à sa place ou à un propriétaire.")
    if ramassables:
        lignes.append(
            f"{len(ramassables)} jetable(s) ramassable(s) : le ramassage les retire au prochain "
            "démarrage d'un hôte, sans geste."
        )
    return lignes


# ── La purge ──────────────────────────────────────────────────────────────────


def _designes(entrees: Sequence[Entree], noms: Sequence[str]) -> tuple[list[Entree], list[str]]:
    """Les éléments à retirer que `noms` désignent (nom ou chemin), et les noms sans écho."""
    a_retirer = [entree for entree in entrees if entree.a_retirer]
    if not noms:
        return a_retirer, []
    choisis: dict[str, Entree] = {}
    inconnus: list[str] = []
    for nom in noms:
        trouves = [
            entree
            for entree in a_retirer
            if entree.chemin.name == nom
            or _cle(entree.chemin) == _cle(Path(nom).expanduser())
            or (entree.declaration is not None and entree.declaration.id == nom)
        ]
        if not trouves:
            inconnus.append(nom)
        for entree in trouves:
            choisis.setdefault(_cle(entree.chemin), entree)
    return list(choisis.values()), inconnus


def _retirer(entree: Entree) -> bool:
    """Retire un élément — `True` si plus rien ne subsiste à cette adresse.

    Une déclaration s'oublie par son dépôt (`ProjetStore.supprimer`, le verbe de
    `DELETE /api/projets`), jamais en effaçant son fichier : son format est au produit.
    """
    if entree.declaration is not None:
        store = ProjetStore(entree.declaration.depot)
        try:
            store.supprimer(entree.declaration.id)
        except OSError:
            return False
        return entree.declaration.id not in store.ids()
    chemin = entree.chemin
    if chemin.is_dir() and not _est_un_lien(chemin):
        return retirer_arbre(chemin)
    try:
        chemin.unlink(missing_ok=True)
    except OSError:
        return False
    return not chemin.exists()


def main(
    argv: Sequence[str] | None = None,
    *,
    gardes: Protections | None = None,
    copies: Sequence[Path] | None = None,
    anciennes_adresses: Iterable[Path] | None = None,
    environnement: Mapping[str, str] | None = None,
    entree: TextIO | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée — voir l'en-tête du module.

    `gardes` (des protections figées), `copies` (des copies du dépôt dont les
    déclarations sont relues à chaque jugement), `anciennes_adresses`, `environnement`
    et les flux sont injectables **pour les tests** : des déclarations choisies, des
    adresses jetables, une réponse écrite.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    purger = "--purger" in args
    check = "--check" in args
    noms = [arg for arg in args if not arg.startswith("--")]
    inconnues = [arg for arg in args if arg.startswith("--") and arg not in ("--check", "--purger")]
    if inconnues or (purger and check) or (noms and not purger):
        detail = " ".join(inconnues) or "--check et --purger s'excluent ; un nom suit --purger"
        print(f"{_USAGE}\n  {detail}", file=erreur)
        return CODE_USAGE

    env = os.environ if environnement is None else environnement
    adresses = None if anciennes_adresses is None else tuple(anciennes_adresses)
    # Relues à chaque jugement : un projet déclaré entre la liste et la réponse protège.
    def lire_gardes() -> Protections:
        return gardes if gardes is not None else protections(copies=copies)

    lues = lire_gardes()
    entrees = inventorier(lues, environnement=env, anciennes_adresses=adresses)
    mode = "purge" if purger else "vérification, rien n'est écrit"
    print(f"Ce que Maestro a posé sur le poste — {mode} :", file=sortie)
    print("\n".join(rendu(entrees, env)), file=sortie)
    if lues.illisible:
        print(
            "Purge impossible : " + " ; ".join(lues.illisible) + " — un projet qu'on ne voit "
            "pas pourrait vivre dans ce qu'on retirerait.",
            file=erreur,
        )
        if purger:
            return CODE_NON_CONFIRME
    if not purger:
        return CODE_FAIT

    cibles, inconnus = _designes(entrees, noms)
    if inconnus:
        print(
            f"{_USAGE}\n  rien d'« à retirer » sous ce(s) nom(s) : {' '.join(inconnus)}",
            file=erreur,
        )
        return CODE_USAGE
    if not cibles:
        return CODE_FAIT
    total = sum(cible.taille or 0 for cible in cibles)
    print("", file=sortie)
    for cible in cibles:
        print(f"  - {_affiche(cible.chemin, env)}", file=sortie)
    print(
        f"Retirer ces {len(cibles)} élément(s) ({lisible(total)}) ? "
        f"Tapez « {REPONSE_ACCORD} » pour confirmer : ",
        end="",
        file=sortie,
        flush=True,
    )
    reponse = (entree or sys.stdin).readline()
    if reponse.strip().casefold() != REPONSE_ACCORD:
        print(f"\nRien n'est retiré (réponse : « {reponse.strip()} »).", file=sortie)
        return CODE_NON_CONFIRME

    # Le sort de chacun est rejugé maintenant : la réponse a pu arriver bien après la liste.
    rejuges = inventorier(
        lire_gardes(), environnement=env, anciennes_adresses=adresses, tailles=False
    )
    encore = {_cle(rejuge.chemin) for rejuge in rejuges if rejuge.a_retirer}
    retires: list[Entree] = []
    epargnes: list[Entree] = []
    resistants: list[Entree] = []
    for cible in cibles:
        if _cle(cible.chemin) not in encore:
            epargnes.append(cible)
        elif _retirer(cible):
            retires.append(cible)
        else:
            resistants.append(cible)
    poids = lisible(sum(cible.taille or 0 for cible in retires))
    print(f"\nRetiré(s) : {len(retires)} élément(s), {poids}.", file=sortie)
    for cible in epargnes:
        print(f"  gardé, vivant ou protégé depuis : {_affiche(cible.chemin, env)}", file=sortie)
    for cible in resistants:
        print(f"  a résisté au retrait : {_affiche(cible.chemin, env)}", file=erreur)
    return CODE_RESISTANCE if resistants else CODE_FAIT


if __name__ == "__main__":
    raise SystemExit(main())
