"""Un livrable rejoué sur un **clone propre**, selon la grille du bilan de p5 (#1512).

    .venv/Scripts/python.exe -m maestro.scenarios.comparatif --rejouer seul=<dépôt>

Le bilan de « Le run livre un vrai projet » (2026-10-14, § 3.1) a comparé le
livrable de Maestro à celui d'une session seule en rejouant chacun, script par
script, sur un clone neuf. C'est ce rejeu qui a vu ce qu'aucun statut ne disait :
cinquante fichiers hors du formatage choisi, un typage qui ne passe que sur
l'arbre de l'agent, une intégration continue retenue à l'outillage et jamais
écrite, des commits hors de la convention. Ce module en fait une commande, la
même pour les deux côtés du banc comparatif (`maestro.scenarios.comparatif`).

## La grille

Dix lignes, celles du bilan, dans son ordre :

- **installation, lint, formatage, types, tests, build** : la commande que **le
  livrable déclare** pour elle, jouée sur le clone, rend `0` ;
- **pages servies** : le livrable servi par sa commande, chaque page qu'il déclare
  répond `200`, et une page qui ne doit pas exister répond `404` ;
- **CI écrite, convention de commit, outillage choisi suivi** : un modèle juge sur
  pièces, jamais un lexique (#746).

**Le clone, et lui seul.** Ce qu'une personne récupère d'un projet est ce qu'il a
commité (`maestro.scenarios.projets.cloner`) : ni ce que le run a laissé traîner
dans la racine, ni les dépendances qu'un agent a installées. Un livrable qui ne
se clone pas manque toutes ses lignes, et le dit.

**Les commandes viennent du livrable.** Le banc ne porte aucune table de piles :
un modèle lit ce que le dépôt déclare — ses scripts, son README, ses
instructions, son intégration continue — et nomme, ligne par ligne, la commande
que **le projet** donne pour elle ; l'exécution tranche (docs/41). Un projet qui
ne déclare rien pour une ligne la manque : « le projet ne dit pas comment
vérifier son typage » est exactement le défaut que le bilan a relevé. C'est la
règle de S12, où l'oracle ne devine pas le script des tests (`TRAVAIL_S12`) : ici
le livrable le dit, et c'est lui qu'on lit. Les deux côtés passent par la même
lecture, donc par le même biais s'il y en a un.

**Ce qui se constate se constate.** L'exécution d'abord, dans l'ordre d'une
personne qui découvre le dépôt : installer, puis vérifier, puis construire, puis
servir. Une installation qui échoue laisse les lignes suivantes **non jouées**
(elles n'avaient pas de quoi passer), un build qui échoue laisse les pages non
jouées, et une non jouée compte comme manquée. Les trois lignes jugées le sont
**après**, sur ce que le dépôt porte : un juge injoignable s'abstient (« non
jugé »), et une abstention ne compte ni pour ni contre.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from maestro.outillage.verification import Delais
from maestro.sandbox import verification as execution
from maestro.scenarios.juge import FORME, Avis
from maestro.scenarios.projets import cloner, fichiers_suivis, sujets_des_commits
from maestro.scenarios.scenarios import (
    PAGE_SERVIE,
    SANS_TERMINAL,
    Joueur,
    jouer_commande,
    servir,
    sonder_page,
)

AXE_INSTALLATION = "installation"
AXE_LINT = "lint"
AXE_FORMATAGE = "formatage"
AXE_TYPES = "types"
AXE_TESTS = "tests"
AXE_BUILD = "build"
AXE_PAGES = "pages"
AXE_CI = "ci"
AXE_COMMITS = "commits"
AXE_OUTILLAGE = "outillage"

#: Les lignes qui se tiennent par une commande du livrable, dans l'ordre où elles se
#: jouent : installer d'abord, construire en dernier — les pages ont besoin du build.
AXES_JOUES: tuple[str, ...] = (
    AXE_INSTALLATION,
    AXE_LINT,
    AXE_FORMATAGE,
    AXE_TYPES,
    AXE_TESTS,
    AXE_BUILD,
)

#: Les lignes qu'un modèle juge sur pièces.
AXES_JUGES: tuple[str, ...] = (AXE_CI, AXE_COMMITS, AXE_OUTILLAGE)

#: La grille entière, dans l'ordre du bilan (§ 3.1).
AXES: tuple[str, ...] = (*AXES_JOUES, AXE_PAGES, *AXES_JUGES)

#: Le nom de chaque ligne, tel que le rapport l'écrit.
LIBELLES: Mapping[str, str] = {
    AXE_INSTALLATION: "Installation",
    AXE_LINT: "Lint",
    AXE_FORMATAGE: "Formatage",
    AXE_TYPES: "Types",
    AXE_TESTS: "Tests",
    AXE_BUILD: "Build",
    AXE_PAGES: "Pages servies",
    AXE_CI: "CI écrite",
    AXE_COMMITS: "Convention de commit",
    AXE_OUTILLAGE: "Outillage choisi suivi",
}

#: Les quatre états d'une ligne. `NON_JOUE` compte comme manqué — le livrable n'a pas
#: permis de la jouer —, `NON_JUGE` ne compte pas : c'est le juge qui s'est abstenu.
TENU = "tenu"
MANQUE = "manque"
NON_JOUE = "non joué"
NON_JUGE = "non jugé"
#: Le banc n'a pas pu rejouer la ligne pour une raison du poste (un clone refusé) :
#: une abstention, comme `NON_JUGE`, jamais un manque du livrable.
INJOUABLE = "injouable"

#: La page que le banc demande sans que le livrable l'ait déclarée : une application
#: web répond `404` à un chemin qu'elle ne connaît pas. Le bilan l'a vu manquer — une
#: fiche inconnue servie en `200` (« faux 404 »).
PAGE_ABSENTE_DU_BANC = "/page-absente-du-banc-maestro"
PAGE_ABSENTE = 404

#: Combien de pages déclarées le banc sonde au plus, et de pages absentes.
PAGES_MAX = 8
ABSENTES_MAX = 3

#: Ce que le juge et le lecteur voient du dépôt : la liste des fichiers suivis, les
#: fichiers de la racine et ceux des dossiers de configuration (`.github/…`), chacun
#: borné, et les sujets des derniers commits. De quoi reconnaître une pile et des
#: gestes, pas un dépôt recopié.
FICHIERS_LISTES = 200
CARACTERES_PAR_FICHIER = 3000
#: Deux quotas et non un : ceux de la racine, et ceux des dossiers de configuration.
#: Mesuré au premier passage réel (2026-10-15) : un quota commun, épuisé par vingt
#: fichiers de racine, laissait `.github/workflows/ci.yml` hors de la lecture, et le
#: juge a conclu à une CI absente faute de l'avoir lue.
FICHIERS_LUS = 12
FICHIERS_DE_CONFIGURATION_LUS = 8
OCTETS_LISIBLES = 64_000
COMMITS_LUS = 60

#: Les en-têtes de la réponse du lecteur de commandes, une ligne chacun. Lire un champ
#: que le modèle a été chargé de remplir est une **forme**, comme `VERDICT:` dans le
#: juge (`maestro.scenarios.juge`) — jamais une recherche dans le texte du livrable.
CHAMPS_COMMANDES: Mapping[str, str] = {
    AXE_INSTALLATION: "INSTALLATION",
    AXE_LINT: "LINT",
    AXE_FORMATAGE: "FORMATAGE",
    AXE_TYPES: "TYPES",
    AXE_TESTS: "TESTS",
    AXE_BUILD: "BUILD",
}
CHAMP_SERVIR = "SERVIR"
CHAMP_PAGES = "PAGES"
CHAMP_ABSENTES = "ABSENTES"
AUCUNE = "aucune"

#: Une ligne `CHAMP: valeur`. Des blancs **de ligne** seulement (`[ \t]`) : `\s`
#: traverse le saut de ligne, et un champ laissé vide avalerait alors la ligne suivante.
_LIGNE_CHAMP = re.compile(
    r"^[ \t]*(?P<champ>[A-Z]+)[ \t]*:[ \t]*(?P<valeur>.*?)[ \t]*$", re.MULTILINE
)

SYSTEME_COMMANDES = (
    "Tu lis un dépôt de logiciel et tu nommes, pour chaque vérification, la commande que "
    "le projet lui-même déclare pour la jouer : dans ses scripts, son README, ses "
    "instructions ou son intégration continue. Tu n'inventes rien : une vérification que "
    f"le projet ne déclare pas reçoit « {AUCUNE} ».\n"
    "Une commande vérifie sans modifier les fichiers : si le projet ne déclare que la forme "
    "qui réécrit (un formateur en écriture, un linter qui corrige), donne la forme qui "
    "vérifie du même outil, tel que le projet l'installe. N'ajoute aucune option qui "
    "assouplirait la vérification.\n"
    "Les commandes se jouent par bash, depuis la racine d'un clone neuf du dépôt, l'une "
    "après l'autre dans l'ordre ci-dessous. Le serveur reçoit son port dans la variable "
    "d'environnement PORT.\n"
    "Réponds par exactement ces lignes, et rien d'autre :\n"
    f"{CHAMPS_COMMANDES[AXE_INSTALLATION]}: la commande qui installe les dépendances, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_LINT]}: la commande du linter, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_FORMATAGE]}: la commande qui vérifie le formatage, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_TYPES]}: la commande qui vérifie les types, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_TESTS]}: la commande des tests, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_BUILD]}: la commande qui construit le livrable, ou {AUCUNE}\n"
    f"{CHAMP_SERVIR}: la commande qui sert l'application une fois construite, ou {AUCUNE}\n"
    f"{CHAMP_PAGES}: les chemins que l'application sert, séparés par des virgules "
    f"(au plus {PAGES_MAX}, un exemple réel par route paramétrée), ou {AUCUNE}\n"
    f"{CHAMP_ABSENTES}: des chemins qui doivent répondre 404, par exemple un contenu "
    f"inconnu sous une route paramétrée (au plus {ABSENTES_MAX}), ou {AUCUNE}"
)

#: Les trois jugements disent ce qui rend **non** en toutes lettres (« c'est non si… ») :
#: une formule comme « ne compte pas : des messages écrits par un outil » se lit dans
#: les deux sens. Mesuré au premier passage réel (2026-10-15), le juge des commits en a
#: conclu qu'il fallait **écarter** les commits « Maestro : … », et non les compter.
SYSTEME_CI = (
    "Tu juges une seule chose : ce dépôt porte-t-il une intégration continue écrite, qui "
    "joue les vérifications du projet (au moins ses tests ou sa construction) sur "
    "l'hébergement que la personne a choisi ?\n"
    "C'est oui si un fichier d'intégration continue est commité dans le dépôt et lance "
    "réellement des vérifications. C'est non si l'intégration n'est qu'une intention "
    "écrite dans un README ou des instructions, si elle est prévue mais absente, ou si son "
    "fichier est vide ou ne joue rien.\n" + FORME
)

SYSTEME_COMMITS = (
    "Tu juges une seule chose : les messages de commit de ce dépôt suivent-ils la "
    "convention que la personne a choisie ?\n"
    "**Chaque commit compte**, quel qu'en soit l'auteur : une personne, un agent ou un "
    "outil qui écrit ses propres messages. Aucun ne s'écarte du compte. C'est oui si la "
    "quasi-totalité des sujets suit la convention. C'est non si des commits dans une autre "
    "forme sont plus que de rares exceptions — y compris ceux qu'un outil a écrits —, si la "
    "convention n'est suivie que par une partie des commits, ou si le dépôt n'a aucun "
    "commit.\n" + FORME
)

SYSTEME_OUTILLAGE = (
    "Tu juges une seule chose : ce livrable suit-il chacun des choix d'outillage que la "
    "personne a faits (langage, cadre, gestionnaire de paquets, tests, vérification du "
    "code, formatage, hébergement, intégration continue) ?\n"
    "C'est oui si chaque outil choisi se retrouve dans le dépôt, configuré et utilisable. "
    "C'est non si un seul outil choisi est absent ou remplacé par un autre : dis lequel. "
    "Une **convention** choisie (messages de commit, nommage) n'est pas un outil : elle "
    "se juge à part, sur les commits eux-mêmes, et aucun outil qui la force n'est exigé "
    "ici. Tu ne vérifies pas que les commandes passent, c'est constaté à part en les "
    "exécutant.\n" + FORME
)


class Lecteur(Protocol):
    """Ce que la grille demande à un modèle : lire des commandes, juger une ligne."""

    def rediger(self, systeme: str, prompt: str) -> str: ...

    def juger(self, systeme: str, prompt: str) -> Avis: ...


@dataclass
class PosteDuBanc:
    """Le poste où le livrable se rejoue : jouer une commande, sonder une page, attendre.

    Les vrais gestes par défaut — le bash des agents (`jouer_commande`), une vraie
    requête (`sonder_page`) ; les tests les doublent. Il satisfait le `Poste` de
    `maestro.scenarios.scenarios.servir`, sans parler à aucune API.
    """

    joueur: Joueur = jouer_commande
    sonde: Callable[[str], int | None] = sonder_page
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep

    def jouer(self, commande: str, dossier: Path, delai_s: float) -> execution.Execution:
        """Joue `commande` dans `dossier`."""
        return self.joueur(commande, dossier, delai_s)

    def sonder_la_page(self, url: str) -> int | None:
        """Le statut que rend `url`, `None` si personne ne répond."""
        return self.sonde(url)


@dataclass(frozen=True)
class Ligne:
    """Une ligne de la grille : son état, ce qui le dit, la commande jouée s'il y en a une."""

    axe: str
    etat: str
    detail: str
    commande: str = ""
    duree_s: float | None = None

    @property
    def tenue(self) -> bool:
        """La ligne est-elle tenue ?"""
        return self.etat == TENU

    @property
    def jugee(self) -> bool:
        """A-t-elle un verdict ? Une abstention — du juge ou du poste — n'en est pas un."""
        return self.etat not in (NON_JUGE, INJOUABLE)

    def to_dict(self) -> dict[str, Any]:
        """La ligne en JSON — la forme du rapport."""
        return {
            "axe": self.axe,
            "libelle": LIBELLES.get(self.axe, self.axe),
            "etat": self.etat,
            "detail": self.detail,
            "commande": self.commande,
            "duree_s": None if self.duree_s is None else round(self.duree_s, 3),
        }


@dataclass(frozen=True)
class Commandes:
    """Ce que le lecteur a lu dans le livrable : une commande par ligne jouée, et ses pages."""

    par_axe: Mapping[str, str] = field(default_factory=dict)
    servir: str = ""
    pages: tuple[str, ...] = ()
    absentes: tuple[str, ...] = ()
    brut: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Les commandes lues en JSON — la réponse brute du lecteur comprise."""
        return {
            "par_axe": dict(self.par_axe),
            "servir": self.servir,
            "pages": list(self.pages),
            "absentes": list(self.absentes),
            "brut": self.brut,
        }


@dataclass(frozen=True)
class Grille:
    """Le rejeu d'un livrable : où il a été cloné, et l'état de chaque ligne."""

    depot: str
    copie: str
    lignes: tuple[Ligne, ...]
    commandes: Commandes | None = None

    @property
    def tenues(self) -> int:
        """Combien de lignes sont tenues."""
        return sum(1 for ligne in self.lignes if ligne.tenue)

    @property
    def jugees(self) -> int:
        """Combien de lignes ont un verdict — le dénominateur du score."""
        return sum(1 for ligne in self.lignes if ligne.jugee)

    def ligne(self, axe: str) -> Ligne | None:
        """La ligne d'un axe, si la grille la porte."""
        return next((ligne for ligne in self.lignes if ligne.axe == axe), None)

    def to_dict(self) -> dict[str, Any]:
        """La grille en JSON — la forme du rapport."""
        return {
            "depot": self.depot,
            "copie": self.copie,
            "tenues": self.tenues,
            "jugees": self.jugees,
            "lignes": [ligne.to_dict() for ligne in self.lignes],
            "commandes": None if self.commandes is None else self.commandes.to_dict(),
        }


def rejouer(
    depot: Path,
    copie: Path,
    *,
    choix: Sequence[str],
    lecteur: Lecteur,
    poste: PosteDuBanc | None = None,
    axes: Sequence[str] = AXES,
    trace: Callable[[str, str], None] = lambda _libelle, _detail: None,
) -> Grille:
    """Clone `depot` dans `copie` et y joue la grille — l'état de chaque ligne de `axes`.

    `choix` sont les choix d'outillage de la personne, en mots : ce que le juge
    confronte au livrable. Sans choix, les lignes qui en dépendent (convention de
    commit, outillage suivi) ne se jugent pas. `trace` reçoit chaque étape, pour le
    déroulé du rapport et l'écran du passage.
    """
    poste = poste or PosteDuBanc()
    if not (depot / ".git").exists():
        return _toutes(depot, copie, axes, MANQUE, "le livrable n'est pas un dépôt Git")
    try:
        cloner(depot, copie)
    except OSError as exc:
        # Un dépôt qui ne se clone pas dit une panne du poste, pas un défaut du livrable :
        # mesuré le 2026-10-15, le poste a refusé de créer `git` (0xC0000142).
        return _toutes(depot, copie, axes, INJOUABLE, f"le clone a échoué : {exc}")
    trace("livrable cloné", f"{depot} → {copie}")
    pieces = _pieces(copie)
    lignes: dict[str, Ligne] = {}

    joues = [axe for axe in AXES_JOUES if axe in axes]
    if joues or AXE_PAGES in axes:
        commandes, illisible = _lire_commandes(lecteur, choix, pieces)
        trace("commandes lues dans le livrable", illisible or _commandes_en_mots(commandes))
        lignes.update(_jouer_les_commandes(copie, commandes, illisible, joues, axes, poste, trace))
    else:
        commandes = None

    for axe in (a for a in AXES_JUGES if a in axes):
        lignes[axe] = _juger(axe, lecteur, choix, pieces)
        trace(f"ligne jugée : {LIBELLES[axe]}", f"{lignes[axe].etat} — {lignes[axe].detail}")

    return Grille(
        depot=str(depot),
        copie=str(copie),
        lignes=tuple(lignes[axe] for axe in AXES if axe in lignes),
        commandes=commandes,
    )


def _toutes(depot: Path, copie: Path, axes: Sequence[str], etat: str, detail: str) -> Grille:
    """Une grille dont chaque ligne a le même état, pour la même raison."""
    return Grille(
        depot=str(depot),
        copie=str(copie),
        lignes=tuple(Ligne(axe, etat, detail) for axe in AXES if axe in axes),
    )


# --- Ce que le dépôt porte --------------------------------------------------


@dataclass(frozen=True)
class Pieces:
    """Ce que le lecteur et le juge voient du clone, déjà borné."""

    fichiers: tuple[str, ...]
    contenus: tuple[tuple[str, str], ...]
    commits: tuple[str, ...]

    def en_texte(self) -> str:
        """Les pièces en un bloc : la liste des fichiers, puis chaque fichier lu."""
        liste = "\n".join(self.fichiers[:FICHIERS_LISTES]) or "(aucun fichier suivi)"
        reste = len(self.fichiers) - FICHIERS_LISTES
        if reste > 0:
            liste += f"\n… et {reste} autre(s)"
        lus = "\n".join(
            f'<fichier chemin="{chemin}">\n{contenu}\n</fichier>'
            for chemin, contenu in self.contenus
        )
        return f"Fichiers suivis :\n{liste}\n\nFichiers lus :\n{lus or '(aucun)'}"


def _pieces(copie: Path) -> Pieces:
    """Les fichiers suivis du clone, ceux qu'on lit, et les sujets des commits.

    Lus : les fichiers de la racine, puis ceux des dossiers de configuration qui
    commencent par un point (`.github/…`) — là où un dépôt dit comment il se
    construit et se vérifie —, les moins profonds d'abord, chacun borné et sans les
    gros fichiers (un verrou de dépendances ne dit rien qu'un manifeste ne dise).
    """
    try:
        suivis = fichiers_suivis(copie)
    except OSError:
        suivis = ()
    try:
        commits = sujets_des_commits(copie, nombre=COMMITS_LUS)
    except OSError:
        commits = ()
    fichiers = tuple(sorted(suivis, key=lambda chemin: (chemin.count("/"), chemin)))
    racine = [chemin for chemin in fichiers if "/" not in chemin]
    configuration = [
        chemin
        for chemin in fichiers
        if "/" in chemin and chemin.split("/", 1)[0].startswith(".")
    ]
    contenus = [
        *_lus(copie, racine, FICHIERS_LUS),
        *_lus(copie, configuration, FICHIERS_DE_CONFIGURATION_LUS),
    ]
    return Pieces(fichiers=fichiers, contenus=tuple(contenus), commits=commits)


def _lus(copie: Path, chemins: Sequence[str], maximum: int) -> list[tuple[str, str]]:
    """Les `maximum` premiers fichiers lisibles de `chemins`, et leur début."""
    lus: list[tuple[str, str]] = []
    for chemin in chemins:
        if len(lus) >= maximum:
            break
        texte = _lire(copie / chemin)
        if texte is not None:
            lus.append((chemin, texte))
    return lus


def _lire(chemin: Path) -> str | None:
    """Le début d'un fichier texte — `None` s'il est trop gros, binaire ou illisible."""
    try:
        if chemin.stat().st_size > OCTETS_LISIBLES:
            return None
        texte = chemin.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(texte) > CARACTERES_PAR_FICHIER:
        return texte[:CARACTERES_PAR_FICHIER] + "\n[…]"
    return texte


# --- Les commandes ----------------------------------------------------------


def _lire_commandes(
    lecteur: Lecteur, choix: Sequence[str], pieces: Pieces
) -> tuple[Commandes, str]:
    """Ce que le livrable déclare, ligne par ligne — et pourquoi rien, si la lecture a échoué."""
    prompt = (
        f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
        f"Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte()}</depot>\n"
        "Quelles commandes ce projet déclare-t-il ?"
    )
    try:
        brut = lecteur.rediger(SYSTEME_COMMANDES, prompt)
    except Exception as exc:  # fournisseur injoignable, quota : la lecture n'a pas eu lieu
        return Commandes(), f"les commandes du livrable n'ont pas pu être lues : {exc}"
    return commandes_depuis(brut), ""


def commandes_depuis(brut: str) -> Commandes:
    """Les commandes lues dans la réponse du lecteur — un champ absent vaut « aucune »."""
    champs: dict[str, str] = {}
    for trouve in _LIGNE_CHAMP.finditer(brut or ""):
        champs.setdefault(trouve.group("champ"), trouve.group("valeur").strip().strip("`"))

    def valeur(champ: str) -> str:
        texte = champs.get(champ, "").strip()
        return "" if texte.strip(" .").lower() == AUCUNE else texte

    def chemins(champ: str, maximum: int) -> tuple[str, ...]:
        vus: list[str] = []
        for morceau in valeur(champ).split(","):
            chemin = morceau.strip().strip("`")
            if chemin.startswith("/") and chemin not in vus:
                vus.append(chemin)
        return tuple(vus[:maximum])

    return Commandes(
        par_axe={axe: valeur(champ) for axe, champ in CHAMPS_COMMANDES.items()},
        servir=valeur(CHAMP_SERVIR),
        pages=chemins(CHAMP_PAGES, PAGES_MAX),
        absentes=chemins(CHAMP_ABSENTES, ABSENTES_MAX),
        brut=brut or "",
    )


def _jouer_les_commandes(
    copie: Path,
    commandes: Commandes,
    illisible: str,
    joues: Sequence[str],
    axes: Sequence[str],
    poste: PosteDuBanc,
    trace: Callable[[str, str], None],
) -> dict[str, Ligne]:
    """Les lignes jouées, dans l'ordre — une installation ratée laisse les suivantes non jouées."""
    lignes: dict[str, Ligne] = {}
    delai = Delais().commande_s
    bloquee = illisible
    for axe in joues:
        commande = commandes.par_axe.get(axe, "")
        if bloquee:
            lignes[axe] = Ligne(axe, NON_JOUE, bloquee, commande=commande)
            continue
        if not commande:
            lignes[axe] = Ligne(
                axe, MANQUE, f"le livrable ne déclare aucune commande pour : {LIBELLES[axe]}"
            )
            if axe == AXE_INSTALLATION:
                bloquee = "l'installation n'a pas de commande : rien n'a de quoi passer"
            continue
        fait = poste.jouer(f"{SANS_TERMINAL} {commande}", copie, delai)
        mots = _execution_en_mots(fait, delai)
        trace(f"ligne jouée : {LIBELLES[axe]}", f"`{commande}` — {mots}")
        tenue = not fait.expiree and fait.code == 0
        lignes[axe] = Ligne(
            axe, TENU if tenue else MANQUE, mots, commande=commande, duree_s=fait.duree_s
        )
        if not tenue and axe == AXE_INSTALLATION:
            bloquee = "l'installation a échoué : rien n'a de quoi passer"
    if AXE_PAGES in axes:
        lignes[AXE_PAGES] = _pages(copie, commandes, illisible, lignes, poste, trace)
    return lignes


def _pages(
    copie: Path,
    commandes: Commandes,
    illisible: str,
    lignes: Mapping[str, Ligne],
    poste: PosteDuBanc,
    trace: Callable[[str, str], None],
) -> Ligne:
    """Le livrable servi : ses pages en `200`, les pages absentes en `404`."""
    if illisible:
        return Ligne(AXE_PAGES, NON_JOUE, illisible, commande=commandes.servir)
    for prealable, echec in (
        (AXE_INSTALLATION, "l'installation a échoué : rien à servir"),
        (AXE_BUILD, "le build a échoué : rien à servir"),
    ):
        ligne = lignes.get(prealable)
        if ligne is not None and ligne.etat == NON_JOUE:
            return Ligne(AXE_PAGES, NON_JOUE, ligne.detail, commande=commandes.servir)
        # Un build que le livrable ne déclare pas n'empêche pas de servir : certaines
        # piles servent sans construire. Un build déclaré qui échoue, lui, l'empêche.
        if ligne is not None and ligne.etat == MANQUE and ligne.commande:
            return Ligne(AXE_PAGES, NON_JOUE, echec, commande=commandes.servir)
    if not commandes.servir:
        return Ligne(AXE_PAGES, MANQUE, "le livrable ne déclare aucune commande pour se servir")
    attendus: dict[str, int] = dict.fromkeys(commandes.pages or ("/",), PAGE_SERVIE)
    for absente in (*commandes.absentes, PAGE_ABSENTE_DU_BANC):
        attendus.setdefault(absente, PAGE_ABSENTE)
    vues, sortie = servir(poste, copie, commande=commandes.servir, attendus=attendus)
    rendu = ", ".join(f"{page} → {statut or 'sans réponse'}" for page, statut in vues.items())
    trace("pages servies", rendu)
    ecarts = [
        f"{page} ({statut or 'sans réponse'} au lieu de {attendus[page]})"
        for page, statut in vues.items()
        if statut != attendus[page]
    ]
    if ecarts:
        fin = f" — {sortie}" if sortie else ""
        return Ligne(
            AXE_PAGES, MANQUE, f"{', '.join(ecarts)}{fin}", commande=commandes.servir
        )
    return Ligne(AXE_PAGES, TENU, rendu, commande=commandes.servir)


def _execution_en_mots(fait: execution.Execution, delai_s: float) -> str:
    """Ce qu'une commande a rendu, en une ligne."""
    if fait.expiree:
        return f"aucun retour en {delai_s:g} s"
    fin = " ".join(fait.sortie.split())[-300:] or "aucune sortie"
    return f"code {fait.code} en {fait.duree_s:.1f} s — {fin}"


def _commandes_en_mots(commandes: Commandes) -> str:
    """Les commandes lues, en une ligne — la pièce du déroulé."""
    morceaux = [
        f"{LIBELLES[axe]} : `{commande}`" if commande else f"{LIBELLES[axe]} : aucune"
        for axe, commande in commandes.par_axe.items()
    ]
    morceaux.append(f"servir : `{commandes.servir}`" if commandes.servir else "servir : aucune")
    morceaux.append(f"pages : {', '.join(commandes.pages) or 'aucune'}")
    return " ; ".join(morceaux)


# --- Les lignes jugées ------------------------------------------------------


def _juger(axe: str, lecteur: Lecteur, choix: Sequence[str], pieces: Pieces) -> Ligne:
    """Une ligne jugée sur pièces : tenue, manquée, ou non jugée si le juge s'abstient."""
    if axe in (AXE_COMMITS, AXE_OUTILLAGE) and not choix:
        return Ligne(axe, NON_JUGE, "aucun choix d'outillage n'est déclaré pour ce livrable")
    choisi = f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
    if axe == AXE_COMMITS:
        sujets = "\n".join(pieces.commits) or "(aucun commit)"
        systeme, prompt = (
            SYSTEME_COMMITS,
            f"{choisi}Les sujets des derniers commits, du plus récent au plus ancien :\n"
            f"<commits>{sujets}</commits>\nCes commits suivent-ils la convention choisie ?",
        )
    elif axe == AXE_CI:
        systeme, prompt = (
            SYSTEME_CI,
            f"{choisi}Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte()}</depot>\n"
            "Ce dépôt porte-t-il une intégration continue écrite ?",
        )
    else:
        systeme, prompt = (
            SYSTEME_OUTILLAGE,
            f"{choisi}Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte()}</depot>\n"
            "Ce livrable suit-il chacun de ces choix ?",
        )
    avis = lecteur.juger(systeme, prompt)
    if not avis.lisible:
        return Ligne(axe, NON_JUGE, avis.pourquoi)
    return Ligne(axe, TENU if avis.nomme else MANQUE, avis.pourquoi)


def _choix_en_texte(choix: Sequence[str]) -> str:
    """Les choix d'outillage, un par ligne — « aucun » sans choix."""
    return "\n".join(f"- {un}" for un in choix) or "(aucun choix déclaré)"
