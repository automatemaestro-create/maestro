"""Écrire l'outillage dans un arbre — **sans jamais écraser en silence** (#1033).

La moitié qui touche au disque : `maestro.outillage.redaction` a rendu le texte,
ce module l'écrit dans un répertoire **cible** et tient le manifeste de
[docs/38 §4](../../docs/38-decision-outillage-universel-du-projet.md).

    from maestro.outillage import generer, rediger

    rapport = generer(cible, rediger(constats, recommandation), source=analyse.source_manifeste())
    rapport.ecrits          # ce qui vient d'être posé
    rapport.refuses         # ce qui n'a pas été écrasé, et où la version neuve attend

**Quelle cible ?** Ce module ne le décide pas : c'est le régime d'écriture du
projet (`maestro.outillage.ecriture`, docs/24 §2.4) qui lui passe la racine d'un
projet non versionné ou le worktree d'une branche à fusionner. Ici on ne répond
qu'à *quoi écrire, et sur quoi refuser d'écrire* — et les deux réponses sont les
mêmes dans les deux régimes, ce qui est exactement ce qu'on veut : le manifeste
lu est celui de l'arbre où l'on écrit.

## Les quatre cas, et un seul demande un geste (docs/38 §4.2)

| État du fichier | Ce qui se passe |
| --- | --- |
| absent du disque | écrit |
| présent, **absent du manifeste** | **rien** — Maestro ne possède que ce qu'il a déclaré |
| présent, empreinte **identique** | réécrit, ou laissé tel quel s'il est déjà à jour |
| présent, empreinte **différente** | **jamais écrasé** — la neuve va dans `refuses/`, nommée |
| déclaré, **absent du disque** | réécrit : une suppression n'est pas une modification à préserver |

Deux corollaires, et ils comptent autant que le tableau :

- **retirer une entrée du manifeste est la façon de dire « ne régénère plus
  ça »** — c'est le seul geste que le dernier cas laisse à la personne ;
- **un fichier que l'analyse ne recommande plus n'est pas supprimé.** Son entrée
  quitte le manifeste, le fichier reste sur le disque, et le rapport le nomme.
  Maestro n'efface rien dans le projet de quelqu'un.

## La portée `bloc`

Un `AGENTS.md` (ou un `CLAUDE.md`) que le projet possédait **avant** Maestro
n'est pas un fichier qu'on refuse d'écrire : c'est un fichier dont Maestro ne
possède qu'un **bloc délimité** (`portee: "bloc"`). Hors du bloc, rien n'est
touché, et `empreinte` est celle du bloc, pas du fichier. Les quatre cas
ci-dessus s'y appliquent mot pour mot, le « fichier » étant lu « bloc » — y
compris le refus : un bloc modifié à la main n'est jamais réécrit.

## Ce qui garde l'écriture

Chaque chemin est confronté à la **frontière d'écriture** de `maestro.sandbox.en_place`
avant d'être ouvert — hors de la cible, à travers un lien symbolique, ou exclu du
périmètre : refusé, avec son motif. Ce n'est pas une précaution ajoutée ici mais
le mécanisme que le régime en place arme déjà pour les agents (#839) : deux
orthographes de « où a-t-on le droit d'écrire » auraient fini par ne pas refuser
les mêmes chemins.

## Le verdict de chaque commande (#1160)

Les commandes que l'outillage écrit ont été **jouées avant** (`maestro.outillage.
verification`). Ce module ne les joue pas — il garde leurs verdicts là où ils se
relisent : dans le **manifeste** (`verifications`, à côté des `entrees`), où ils
disent six mois plus tard ce qui marchait le jour de l'écriture, et dans le
**rapport**, où la personne les lit commande par commande, sortie comprise.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from maestro.outillage.contexte import BALISE_DEBUT, BALISE_FIN, VERSION_MANIFESTE
from maestro.outillage.detection import CHEMIN_MANIFESTE, lire_texte
from maestro.outillage.redaction import GENERE_PAR, PORTEE_BLOC, Fichier, bloc
from maestro.outillage.verification import Verification
from maestro.sandbox.en_place import FrontiereEcriture

#: Où va une version neuve qu'on a refusé d'écraser (docs/38 §3.6). L'arborescence
#: du projet y est **conservée** (`refuses/.agents/skills/x/SKILL.md`) plutôt
#: qu'aplatie : deux propositions ne peuvent pas s'y écraser l'une l'autre, et un
#: `diff -r` entre ce dossier et la racine montre la proposition d'un seul geste.
DOSSIER_REFUSES = ".maestro/outillage/refuses"

#: Les états d'une écriture, tels qu'ils se lisent dans le rapport.
#: `ecrit` : posé sur le disque. `inchange` : le fichier était déjà exactement
#: celui-là — rien n'a été touché, et c'est ce qui fait que régénérer ne duplique
#: rien. `refuse` : modifié depuis, jamais écrasé. `ignore` : présent mais absent
#: du manifeste, donc pas à nous. `retire` : plus recommandé — l'entrée quitte le
#: manifeste, le fichier reste.
ETATS_ECRITURE: frozenset[str] = frozenset({"ecrit", "inchange", "refuse", "ignore", "retire"})

#: Plafond de lecture d'un fichier existant, pour décider s'il a bougé. Le même
#: que celui du contexte d'un agent (`maestro.outillage.contexte.Bornes`) : au
#: delà, ce n'est plus un fichier d'outillage.
OCTETS_MAX = 256 * 1024

#: Refus global : un manifeste d'une version que ce module ne sait pas lire.
#: **Rien n'est écrit** — ni fichier, ni manifeste. Écraser un manifeste qu'on ne
#: comprend pas reviendrait à effacer ce qu'une version future y a déclaré, donc
#: à faire perdre à Maestro la mémoire de ce qu'il possède : le cas « présent,
#: absent du manifeste » avalerait ensuite tout l'outillage du projet.
REFUS_VERSION = "version-de-manifeste-inconnue"


@dataclass(frozen=True)
class Ecriture:
    """Ce qu'il est advenu d'un fichier du plan — et pourquoi.

    `refuse_vers` est le chemin où la version neuve attend quand elle n'a pas
    écrasé l'existant : c'est la « proposition de fusion lisible » du ticket,
    et elle n'a de sens que nommée — un fichier déposé dans un dossier que
    personne ne regarde ne dit rien à personne.
    """

    chemin: str
    role: str
    portee: str
    etat: str
    raison: str
    refuse_vers: str = ""

    def to_dict(self) -> dict[str, Any]:
        """L'écriture en JSON."""
        return {
            "chemin": self.chemin,
            "role": self.role,
            "portee": self.portee,
            "etat": self.etat,
            "raison": self.raison,
            "refuse_vers": self.refuse_vers,
        }


@dataclass(frozen=True)
class Rapport:
    """Ce que la génération a fait, fichier par fichier — jamais un simple « ok ».

    `refus` porte le motif d'un refus **global** (manifeste illisible dans une
    version inconnue) : le rapport est alors vide de toute écriture, et c'est la
    seule forme sous laquelle la génération renonce entièrement.

    `verifications` (#1160) est le verdict de chaque commande que l'outillage
    écrit, dans l'ordre où elles ont été jouées — y compris sur un refus global :
    elles l'ont été, et le taire ferait croire qu'on n'a rien tenté.
    """

    cible: str = ""
    manifeste: str = CHEMIN_MANIFESTE
    genere_par: str = GENERE_PAR
    genere_le: str = ""
    ecritures: tuple[Ecriture, ...] = ()
    refus: str = ""
    verifications: tuple[Verification, ...] = ()

    @property
    def ecrits(self) -> tuple[Ecriture, ...]:
        """Ce qui vient d'être posé sur le disque."""
        return tuple(e for e in self.ecritures if e.etat == "ecrit")

    @property
    def refuses(self) -> tuple[Ecriture, ...]:
        """Ce qui n'a pas été écrasé — la liste qu'une personne doit relire."""
        return tuple(e for e in self.ecritures if e.etat == "refuse")

    @property
    def ignores(self) -> tuple[Ecriture, ...]:
        """Ce que le projet portait déjà et que Maestro ne possède pas."""
        return tuple(e for e in self.ecritures if e.etat == "ignore")

    @property
    def retires(self) -> tuple[Ecriture, ...]:
        """Ce qui quitte le manifeste sans quitter le disque."""
        return tuple(e for e in self.ecritures if e.etat == "retire")

    def to_dict(self) -> dict[str, Any]:
        """Le rapport en JSON — ce qu'un appelant peut montrer tel quel."""
        return {
            "cible": self.cible,
            "manifeste": self.manifeste,
            "genere_par": self.genere_par,
            "genere_le": self.genere_le,
            "refus": self.refus,
            "ecritures": [ecriture.to_dict() for ecriture in self.ecritures],
            "ecrits": [ecriture.chemin for ecriture in self.ecrits],
            "refuses": [ecriture.chemin for ecriture in self.refuses],
            "ignores": [ecriture.chemin for ecriture in self.ignores],
            "retires": [ecriture.chemin for ecriture in self.retires],
            "verifications": [v.to_dict() for v in self.verifications],
        }


@dataclass
class _EtatManifeste:
    """Le manifeste relu, et celui qu'on écrira — la mémoire de ce que Maestro possède."""

    version_lue: Any = None
    entrees: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    def empreinte_de(self, chemin: str) -> str:
        """L'empreinte que Maestro a **écrite** pour `chemin`, "" s'il ne le possède pas."""
        entree = self.entrees.get(chemin)
        return str(entree.get("empreinte") or "") if entree is not None else ""


def generer(
    cible: Path | str,
    fichiers: Sequence[Fichier],
    *,
    source: Mapping[str, Any],
    frontiere: FrontiereEcriture | None = None,
    horodatage: str = "",
    verifications: Sequence[Verification] = (),
) -> Rapport:
    """Écrit `fichiers` dans `cible` selon les quatre cas de docs/38 §4.2.

    `source` est le fragment `source` du manifeste — `Analyse.source_manifeste()`
    pour un projet analysé (#1030), les réponses données pour un projet neuf
    (#1031). Il est recopié tel quel : sa forme est écrite là où elle est
    produite, et une seconde formulation ferait qu'on lirait un outillage sans
    savoir ce qui l'a recommandé.

    `frontiere` garde chaque chemin (hors de la cible, lien symbolique, exclu du
    périmètre). `None` ne l'ouvre pas : la frontière est alors **dérivée de la
    cible seule**, donc rien ne peut sortir de l'arbre qu'on vient de nommer.

    `horodatage` fixe la date de génération (ISO 8601 UTC) ; vide, c'est
    maintenant. Il n'entre dans **aucun** contenu de fichier — seulement dans le
    manifeste —, parce qu'un contenu qui change à chaque appel rendrait le cas
    « empreinte identique » inatteignable.

    `verifications` sont les verdicts des commandes, jouées avant (#1160) : ils
    vont tels quels au manifeste et au rapport. Vides, le manifeste n'en porte pas
    moins la clé — une liste vide dit « rien n'était à jouer », une clé absente ne
    dirait rien.

    Ne lève pas : un chemin refusé, un fichier illisible, un disque en écriture
    seule sont des **lignes du rapport**, jamais une exception — il ne doit pas y
    avoir d'état où une partie de l'outillage est posée et où l'appelant ne sait
    pas laquelle.
    """
    racine = Path(cible)
    quand = horodatage or datetime.now(UTC).isoformat(timespec="seconds")
    garde = frontiere or FrontiereEcriture(racine=racine.resolve(), exclus=())
    verdicts = tuple(verifications)
    etat = _lire_manifeste(racine)
    if etat.version_lue is not None and etat.version_lue != VERSION_MANIFESTE:
        return Rapport(
            cible=str(racine),
            genere_le=quand,
            refus=(
                f"{REFUS_VERSION} : {CHEMIN_MANIFESTE} déclare la version "
                f"{etat.version_lue!r}, attendue {VERSION_MANIFESTE} — rien n'a été "
                "écrit, pour ne pas effacer ce qu'une autre version y a déclaré."
            ),
            verifications=verdicts,
        )

    ecritures: list[Ecriture] = []
    gardees: dict[str, dict[str, Any]] = {}
    for fichier in fichiers:
        ecriture, entree = _poser(racine, fichier, etat, garde, quand)
        ecritures.append(ecriture)
        if entree is not None:
            gardees[fichier.chemin] = entree
    ecritures.extend(_retirees(etat, gardees))
    manifeste = _ecrire_manifeste(racine, gardees, dict(source), quand, garde, verdicts)
    if manifeste:
        ecritures.append(
            Ecriture(
                chemin=CHEMIN_MANIFESTE,
                role="manifeste",
                portee="fichier",
                etat="refuse",
                raison=manifeste,
            )
        )
    return Rapport(
        cible=str(racine), genere_le=quand, ecritures=tuple(ecritures), verifications=verdicts
    )


def portees_declarees(cible: Path | str) -> dict[str, str]:
    """`chemin → portée` de ce que le manifeste de `cible` déclare — vide s'il n'y en a pas.

    C'est **la mémoire de ce que Maestro possède**, et c'est ce que la rédaction
    doit savoir avant de rendre le moindre texte (`maestro.outillage.redaction.rediger`,
    paramètre `portees`). Sans elle, l'analyse rejouée après une génération
    prend le travail de Maestro pour celui du projet : `AGENTS.md` repasse en
    `a-completer` et les skills en `deja-present`, si bien qu'une simple
    régénération dupliquerait le premier dans lui-même et sortirait les seconds
    du manifeste.

    Rendue en **lecture pure** : ni exception, ni écriture, ni création de
    dossier — un projet sans manifeste est le cas nominal d'une première
    génération, pas une anomalie.
    """
    return {
        chemin: str(entree.get("portee") or "fichier")
        for chemin, entree in _lire_manifeste(Path(cible)).entrees.items()
    }


def _poser(
    racine: Path,
    fichier: Fichier,
    etat: _EtatManifeste,
    garde: FrontiereEcriture,
    quand: str,
) -> tuple[Ecriture, dict[str, Any] | None]:
    """Décide, puis écrit — et rend l'entrée de manifeste à conserver, s'il y en a une.

    L'entrée rendue est celle qui décrit **ce que Maestro a écrit** : celle du
    contenu neuf quand il a été posé, celle d'avant quand on a refusé d'écraser.
    Garder l'ancienne dans ce second cas n'est pas un détail : la retirer ferait
    que la génération suivante lirait « présent, absent du manifeste » et
    n'oserait plus jamais toucher au fichier, y compris une fois la personne
    revenue à la version de Maestro.
    """
    refus = garde.refus_chemin(fichier.chemin, ecriture=True)
    if refus is not None:
        return _ecriture(fichier, "refuse", refus), etat.entrees.get(fichier.chemin)

    cible = racine / PurePosixPath(fichier.chemin)
    present = _texte_existant(cible)
    connue = etat.empreinte_de(fichier.chemin)

    if fichier.portee == PORTEE_BLOC:
        return _poser_bloc(racine, cible, fichier, present, connue, quand)

    if present is not None and not connue:
        return (
            _ecriture(
                fichier,
                "ignore",
                "le projet porte déjà ce fichier et Maestro ne l'a pas écrit : "
                "il n'y touche pas.",
            ),
            None,
        )
    if present is not None and _empreinte(present) != connue:
        return _refuser(racine, fichier, etat)
    if present == fichier.contenu:
        return (
            _ecriture(fichier, "inchange", "déjà à jour — rien n'a été réécrit."),
            _entree(fichier, _empreinte(fichier.contenu), _quand_connu(etat, fichier, quand)),
        )
    erreur = _ecrire_fichier(cible, fichier.contenu, executable=fichier.executable)
    if erreur:
        return _ecriture(fichier, "refuse", erreur), etat.entrees.get(fichier.chemin)
    raison = (
        "écrit" if present is None else "réécrit : le fichier était celui que Maestro avait posé."
    )
    return _ecriture(fichier, "ecrit", raison), _entree(fichier, _empreinte(fichier.contenu), quand)


def _poser_bloc(
    racine: Path,
    cible: Path,
    fichier: Fichier,
    present: str | None,
    connue: str,
    quand: str,
) -> tuple[Ecriture, dict[str, Any] | None]:
    """La portée `bloc` : Maestro ne possède qu'un bloc délimité d'un fichier d'autrui.

    Le fichier est **conservé tel quel** autour du bloc, dans tous les cas. Quatre
    situations, qui reprennent celles du fichier entier :

    - pas de bloc, fichier présent → le bloc est **ajouté à la fin**. C'est la
      proposition de fusion : elle est lisible parce qu'elle est délimitée, et
      réversible parce qu'elle ne remplace rien ;
    - bloc présent mais **absent du manifeste** → rien. Quelqu'un d'autre a écrit
      ces balises ; Maestro ne possède que ce qu'il a déclaré ;
    - bloc présent, empreinte **identique** → le bloc seul est réécrit ;
    - bloc présent, empreinte **différente** → jamais écrasé, la version neuve
      part dans `refuses/`.
    """
    existant = _bloc_existant(present or "")
    if present is not None and existant is not None and not connue:
        return (
            _ecriture(
                fichier,
                "ignore",
                f"`{fichier.chemin}` porte déjà un bloc `maestro-outillage` que Maestro "
                "n'a pas écrit : il n'y touche pas.",
            ),
            None,
        )
    if existant is not None and _empreinte(existant) != connue:
        return _refuser_bloc(racine, fichier)
    if existant == fichier.contenu.strip():
        return (
            _ecriture(fichier, "inchange", "le bloc était déjà à jour."),
            _entree(fichier, _empreinte(fichier.contenu.strip()), quand),
        )
    fusionne = _fusionner_bloc(present, fichier.contenu)
    erreur = _ecrire_fichier(cible, fusionne, executable=False)
    if erreur:
        return _ecriture(fichier, "refuse", erreur), None
    raison = (
        "bloc `maestro-outillage` ajouté à la fin du fichier existant — rien d'autre "
        "n'a été touché."
        if existant is None
        else "bloc `maestro-outillage` réécrit ; le reste du fichier est intact."
    )
    return _ecriture(fichier, "ecrit", raison), _entree(
        fichier, _empreinte(fichier.contenu.strip()), quand
    )


def _refuser(
    racine: Path, fichier: Fichier, etat: _EtatManifeste
) -> tuple[Ecriture, dict[str, Any] | None]:
    """Ne pas écraser, mais déposer la version neuve où elle se relit."""
    depot = f"{DOSSIER_REFUSES}/{fichier.chemin}"
    erreur = _ecrire_fichier(racine / PurePosixPath(depot), fichier.contenu, executable=False)
    raison = (
        f"modifié depuis que Maestro l'a écrit : jamais écrasé. La version neuve "
        f"attend dans `{depot}`."
        if not erreur
        else f"modifié depuis que Maestro l'a écrit : jamais écrasé — et la version "
        f"neuve n'a pas pu être déposée ({erreur})."
    )
    return (
        _ecriture(fichier, "refuse", raison, refuse_vers="" if erreur else depot),
        etat.entrees.get(fichier.chemin),
    )


def _refuser_bloc(racine: Path, fichier: Fichier) -> tuple[Ecriture, dict[str, Any] | None]:
    """Le refus d'un bloc modifié — la version neuve est déposée **balises comprises**.

    Avec ses balises, parce que ce qui se relit est un bloc à recoller dans un
    fichier, pas un fragment de texte dont on aurait à deviner où il allait.
    """
    depot = f"{DOSSIER_REFUSES}/{fichier.chemin}"
    erreur = _ecrire_fichier(racine / PurePosixPath(depot), bloc(fichier.contenu) + "\n", False)
    raison = (
        f"le bloc `maestro-outillage` de `{fichier.chemin}` a été modifié depuis : "
        f"jamais écrasé. La version neuve attend dans `{depot}`."
        if not erreur
        else f"le bloc de `{fichier.chemin}` a été modifié depuis : jamais écrasé — et "
        f"la version neuve n'a pas pu être déposée ({erreur})."
    )
    return _ecriture(fichier, "refuse", raison, refuse_vers="" if erreur else depot), None


def _retirees(etat: _EtatManifeste, gardees: Mapping[str, Any]) -> list[Ecriture]:
    """Les entrées du manifeste que la recommandation ne porte plus (docs/38 §4.2).

    Elles quittent le manifeste ; **le fichier reste sur le disque**. Maestro
    n'efface rien dans le projet de quelqu'un — et une entrée retirée est
    exactement ce qu'il faut pour que la personne puisse ensuite disposer du
    fichier comme elle l'entend, sans qu'une régénération vienne le reprendre.
    """
    return [
        Ecriture(
            chemin=chemin,
            role=str(entree.get("role") or ""),
            portee=str(entree.get("portee") or "fichier"),
            etat="retire",
            raison=(
                "l'analyse ne recommande plus cette pièce : son entrée quitte le "
                "manifeste, le fichier reste sur le disque."
            ),
        )
        for chemin, entree in etat.entrees.items()
        if chemin not in gardees
    ]


def _ecriture(fichier: Fichier, etat: str, raison: str, *, refuse_vers: str = "") -> Ecriture:
    """Une ligne de rapport pour `fichier`."""
    return Ecriture(
        chemin=fichier.chemin,
        role=fichier.role,
        portee=fichier.portee,
        etat=etat,
        raison=raison,
        refuse_vers=refuse_vers,
    )


def _entree(fichier: Fichier, empreinte: str, quand: str) -> dict[str, Any]:
    """Une entrée de manifeste — **une par fichier**, jamais par skill (docs/38 §4.1).

    « Régénérer sans écraser » se décide fichier par fichier : une personne
    corrige un `SKILL.md` et laisse ses scripts tranquilles, et l'inverse.
    """
    return {
        "chemin": fichier.chemin,
        "role": fichier.role,
        "portee": fichier.portee,
        "empreinte": empreinte,
        "genere_le": quand,
    }


def _quand_connu(etat: _EtatManifeste, fichier: Fichier, defaut: str) -> str:
    """La date déjà déclarée pour ce fichier — un contenu inchangé n'a pas été regénéré."""
    entree = etat.entrees.get(fichier.chemin)
    return str(entree.get("genere_le") or defaut) if entree is not None else defaut


def _empreinte(texte: str) -> str:
    """L'empreinte d'un contenu, telle qu'elle voyage dans le manifeste (`sha256:…`).

    Sur les **octets UTF-8** du texte et non sur le fichier lu tel quel : c'est le
    contenu que Maestro a écrit qui est déclaré, et une différence de fin de ligne
    introduite par un éditeur est une modification comme une autre — qu'on veut
    justement voir.
    """
    return "sha256:" + hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _texte_existant(cible: Path) -> str | None:
    """Le contenu du fichier, ou `None` s'il n'existe pas.

    Un fichier présent mais illisible (droits, verrou Windows) rend `""` et non
    `None` : il **existe**, donc les règles du fichier présent s'appliquent — le
    traiter comme absent reviendrait à l'écraser au premier problème de lecture.
    """
    if not cible.is_file():
        return None
    return lire_texte(cible, OCTETS_MAX)


def _bloc_existant(texte: str) -> str | None:
    """Le contenu du bloc `maestro-outillage`, ou `None` si le texte n'en porte pas.

    Même lecture que `maestro.outillage.contexte._portee`, et pour la même raison :
    ce qu'on écrit ici est ce qu'on relira là-bas. Des balises dépareillées (début
    sans fin) rendent `None` — on ne devine pas où un bloc s'arrête.
    """
    debut = texte.find(BALISE_DEBUT)
    if debut < 0:
        return None
    apres = debut + len(BALISE_DEBUT)
    fin = texte.find(BALISE_FIN, apres)
    return texte[apres:fin].strip() if fin >= 0 else None


def _fusionner_bloc(present: str | None, contenu: str) -> str:
    """Le fichier avec son bloc — remplacé s'il en portait un, ajouté à la fin sinon."""
    neuf = bloc(contenu)
    if present is None or not present.strip():
        return neuf + "\n"
    debut = present.find(BALISE_DEBUT)
    if debut < 0:
        return present.rstrip() + "\n\n" + neuf + "\n"
    fin = present.find(BALISE_FIN, debut + len(BALISE_DEBUT))
    if fin < 0:
        return present.rstrip() + "\n\n" + neuf + "\n"
    return present[:debut] + neuf + present[fin + len(BALISE_FIN) :]


def _ecrire_fichier(cible: Path, contenu: str, executable: bool = False) -> str:
    """Écrit `contenu` dans `cible`, dossiers créés — rend "" ou le motif de l'échec.

    Fins de ligne `\\n` explicites (`newline=""`) : sans cela Python écrirait des
    `\\r\\n` sous Windows, l'empreinte déclarée au manifeste ne serait plus celle
    du fichier relu, et **chaque** régénération croirait que quelqu'un a modifié
    l'outillage.
    """
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        with cible.open("w", encoding="utf-8", newline="") as flux:
            flux.write(contenu)
        if executable:
            mode = cible.stat().st_mode
            cible.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        return f"écriture impossible : {exc}"
    return ""


def _lire_manifeste(racine: Path) -> _EtatManifeste:
    """Le manifeste de `racine`, réduit à ce dont la génération a besoin.

    Un manifeste absent ou illisible rend un état **vide et sans version** : le
    projet est alors traité comme non outillé, ce qui est le cas nominal d'une
    première génération. Une version **lue mais inconnue**, elle, est gardée : le
    refus global s'y accroche.
    """
    brut = lire_texte(racine / CHEMIN_MANIFESTE, OCTETS_MAX)
    if not brut.strip():
        return _EtatManifeste()
    try:
        donnees = json.loads(brut)
    except (ValueError, RecursionError):
        return _EtatManifeste()
    if not isinstance(donnees, dict):
        return _EtatManifeste()
    entrees: dict[str, dict[str, Any]] = {}
    brutes = donnees.get("entrees")
    if isinstance(brutes, list):
        for entree in brutes:
            if isinstance(entree, dict) and isinstance(entree.get("chemin"), str):
                entrees[entree["chemin"]] = entree
    source = donnees.get("source")
    return _EtatManifeste(
        version_lue=donnees.get("manifeste"),
        entrees=entrees,
        source=source if isinstance(source, dict) else {},
    )


def _ecrire_manifeste(
    racine: Path,
    entrees: Mapping[str, dict[str, Any]],
    source: dict[str, Any],
    quand: str,
    garde: FrontiereEcriture,
    verifications: Sequence[Verification] = (),
) -> str:
    """Écrit `.maestro/outillage/manifeste.json` — rend "" ou le motif de l'échec.

    Écrit **en dernier**, et c'est un ordre, pas une commodité : s'il partait en
    premier, une écriture qui échoue ensuite laisserait un manifeste qui déclare
    des fichiers absents, et la génération suivante les réécrirait en croyant
    réparer une suppression volontaire.

    `verifications` (#1160) est une clé **ajoutée** à la version 1 et non une
    version 2 : un lecteur de la version 1 l'ignore sans rien perdre de ce qu'il
    lisait (`_lire_manifeste` ne lit que `manifeste`, `source` et `entrees`), et
    monter la version ferait refuser toute régénération par une version antérieure
    de Maestro (`REFUS_VERSION`) pour une information qu'elle n'a pas besoin de lire.
    """
    refus = garde.refus_chemin(CHEMIN_MANIFESTE, ecriture=True)
    if refus is not None:
        return refus
    donnees = {
        "manifeste": VERSION_MANIFESTE,
        "genere_par": GENERE_PAR,
        "genere_le": quand,
        "source": source,
        "entrees": list(entrees.values()),
        "verifications": [v.to_dict() for v in verifications],
    }
    texte = json.dumps(donnees, ensure_ascii=False, indent=2) + "\n"
    return _ecrire_fichier(racine / PurePosixPath(CHEMIN_MANIFESTE), texte)
