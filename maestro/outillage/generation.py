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

## Une pièce à la fois (#1161)

L'outillage se construit désormais **dans la conversation, pièce par pièce** : chaque
fichier est montré avec son diff, puis écrit sur accord. Deux verbes servent ce
régime, et aucun ne décide autre chose que ce que `generer` décide déjà :

- `prevoir` dit ce qu'écrire **ferait** d'un fichier — les quatre cas du tableau, le
  texte d'aujourd'hui et celui d'après — **sans rien écrire**. C'est la même décision
  que celle de l'écriture (`_prevision`, appelée par les deux), pas une seconde
  lecture des règles : le diff montré est celui de ce qui sera posé ;
- `poser_piece` écrit **un** fichier et **fusionne** le manifeste. `generer` retire du
  manifeste toute entrée qu'on ne lui repasse pas (une pièce que l'analyse ne
  recommande plus) : lui confier une pièce seule effacerait de la comptabilité celles
  écrites avant elle. Ici, les autres entrées restent telles quelles, et les verdicts
  des commandes s'ajoutent à ceux déjà déclarés.

## Ce que la personne a dit de l'outillage (#1334)

Une correction dite dans la conversation (« Nos tests tournent avec `node --test` »,
`maestro.outillage.correction`) s'écrit dans le fichier avec sa phrase ; le manifeste
en garde la **correction elle-même** (`corrections` : le sujet, la valeur, la phrase,
quand elle a été prise), pour que l'outillage rouvert dans une autre conversation la
reprenne au lieu de la remplacer par ce que le projet déclare. `poser_piece` les
**fusionne** — la plus récente de chaque sujet l'emporte (`retenir`) —, et `generer`,
qui n'en reçoit aucune, garde celles déjà déclarées : régénérer l'outillage ne fait
pas oublier ce que la personne a dit.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from maestro.outillage.contexte import BALISE_DEBUT, BALISE_FIN, VERSION_MANIFESTE
from maestro.outillage.correction import (
    CLES_CORRIGEABLES,
    CorrectionPrise,
    corrections_lues,
    retenir,
)
from maestro.outillage.detection import CHEMIN_MANIFESTE, lire_texte
from maestro.outillage.questionnaire import Choix, choix_du_manifeste
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
    verifications: tuple[Verification, ...] = ()
    corrections: tuple[CorrectionPrise, ...] = ()

    def empreinte_de(self, chemin: str) -> str:
        """L'empreinte que Maestro a **écrite** pour `chemin`, "" s'il ne le possède pas."""
        entree = self.entrees.get(chemin)
        return str(entree.get("empreinte") or "") if entree is not None else ""


#: Les cas de `_prevision` — la décision que prend l'écriture, nommée une fois.
#: `frontiere` : le chemin est refusé par la frontière d'écriture. `ignore` : le
#: fichier (ou le bloc) est au projet, pas à Maestro. `modifie` : Maestro l'a écrit et
#: quelqu'un l'a changé depuis — jamais écrasé. `inchange` : déjà exactement celui-là.
#: `ecrit` : posé (créé, ou réécrit parce que Maestro le possède et que rien n'a bougé).
_CAS_FRONTIERE = "frontiere"
_CAS_IGNORE = "ignore"
_CAS_MODIFIE = "modifie"
_CAS_INCHANGE = "inchange"
_CAS_ECRIT = "ecrit"


@dataclass(frozen=True)
class Prevision:
    """Ce qu'écrire un fichier **ferait**, sans rien écrire (#1161).

    `etat` est celui que le rapport porterait (`ecrit`, `inchange`, `refuse`,
    `ignore`) et `raison` la phrase qui l'accompagnerait. `avant` est le texte que le
    disque porte aujourd'hui (`None` : le fichier n'existe pas) et `apres` celui qu'il
    porterait une fois écrit — le fichier **entier**, bloc fusionné compris : c'est ce
    que le diff d'une carte compare. `apres` vaut `None` quand rien ne s'écrirait à ce
    chemin.

    `cas` est la décision elle-même (`_CAS_*`), celle que `_poser` exécute : la
    prévision et l'écriture la tiennent **du même calcul**.

    `avant` et `apres` sont en fins de ligne `\\n`, quel que soit le disque (#1161,
    `_texte_en_place`) ; `crlf` dit que le fichier en place est en `\\r\\n`, et que sa
    réécriture les gardera.
    """

    chemin: str
    etat: str
    raison: str
    avant: str | None = None
    apres: str | None = None
    cas: str = _CAS_ECRIT
    bloc: bool = False
    crlf: bool = False

    @property
    def ecrirait(self) -> bool:
        """Écrire ce fichier changerait-il le disque ?"""
        return self.etat == "ecrit"


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
    refus = _refus_de_version(racine, etat, quand, verdicts)
    if refus is not None:
        return refus

    ecritures: list[Ecriture] = []
    gardees: dict[str, dict[str, Any]] = {}
    for fichier in fichiers:
        ecriture, entree = _poser(racine, fichier, etat, garde, quand)
        ecritures.append(ecriture)
        if entree is not None:
            gardees[fichier.chemin] = entree
    ecritures.extend(_retirees(etat, gardees))
    manifeste = _ecrire_manifeste(
        racine, gardees, dict(source), quand, garde, verdicts, etat.corrections
    )
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


def poser_piece(
    cible: Path | str,
    fichier: Fichier,
    *,
    source: Mapping[str, Any],
    frontiere: FrontiereEcriture | None = None,
    horodatage: str = "",
    verifications: Sequence[Verification] = (),
    corrections: Sequence[CorrectionPrise] = (),
) -> Rapport:
    """Écrit **une** pièce de l'outillage dans `cible`, et **fusionne** le manifeste (#1161).

    Les quatre cas de docs/38 §4.2 sont ceux de `generer`, décidés par la même
    fonction : un fichier du projet n'est pas touché, un fichier modifié n'est jamais
    écrasé. Ce qui change est le manifeste : les **autres** entrées y restent telles
    quelles. `generer` les en retirerait — il tient la recommandation entière et lit
    une absence comme « ne régénère plus ça » —, et une pièce écrite seule effacerait
    ainsi de la comptabilité celles écrites avant elle, que la génération suivante
    prendrait pour des fichiers du projet.

    L'entrée de cette pièce suit `_poser` : la neuve quand elle est posée, l'ancienne
    quand on refuse d'écraser, aucune pour un fichier qui n'est pas à Maestro.

    `verifications` sont les verdicts des commandes que cette pièce écrit : ils
    **s'ajoutent** à ceux déjà déclarés, et remplacent ceux d'une même commande — le
    dernier verdict d'une commande est celui qui vaut. Un verdict déclaré dont la
    commande n'est plus écrite dans **aucun** fichier de Maestro quitte le manifeste
    (`_verdicts_encore_ecrits`, #1381). `source` remplace celle du manifeste : c'est la
    provenance de ce qui vient d'être écrit.

    `corrections` (#1334) sont celles que la pièce porte — prises dans la conversation
    ou reprises du manifeste : elles s'ajoutent à celles déjà déclarées, la plus récente
    de chaque sujet l'emportant (`retenir`). Seuls les sujets que `corriger` change y
    entrent.

    Ne lève pas, comme `generer` : tout empêchement est une ligne du rapport.
    """
    racine = Path(cible)
    quand = horodatage or datetime.now(UTC).isoformat(timespec="seconds")
    garde = frontiere or FrontiereEcriture(racine=racine.resolve(), exclus=())
    etat = _lire_manifeste(racine)
    verdicts = _verdicts_fusionnes(etat.verifications, verifications)
    refus = _refus_de_version(racine, etat, quand, tuple(verifications))
    if refus is not None:
        return refus
    ecriture, entree = _poser(racine, fichier, etat, garde, quand)
    entrees = dict(etat.entrees)
    if entree is not None:
        entrees[fichier.chemin] = entree
    else:
        entrees.pop(fichier.chemin, None)
    ecritures = [ecriture]
    verdicts = _verdicts_encore_ecrits(racine, entrees, verdicts, verifications)
    prises = tuple(
        c for c in retenir(etat.corrections, corrections) if c.cle in CLES_CORRIGEABLES
    )
    manifeste = _ecrire_manifeste(
        racine, entrees, dict(source), quand, garde, verdicts, prises
    )
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
        cible=str(racine),
        genere_le=quand,
        ecritures=tuple(ecritures),
        verifications=tuple(verifications),
    )


def verifications_declarees(cible: Path | str) -> tuple[Verification, ...]:
    """Les verdicts de commandes que le manifeste de `cible` garde — `()` s'il n'y en a pas.

    C'est ce que Maestro sait déjà des commandes qu'il a écrites (#1160) : une pièce
    réécrite avec les mêmes verdicts rend le même texte, donc se reconnaît « déjà à
    jour » sans rejouer une installation. Lecture pure, comme `portees_declarees`.
    """
    return _lire_manifeste(Path(cible)).verifications


def corrections_declarees(cible: Path | str) -> tuple[CorrectionPrise, ...]:
    """Les corrections que le manifeste de `cible` garde — `()` s'il n'y en a pas (#1334).

    Ce que la personne a dit de l'outillage de ce projet, dans une conversation où une
    pièce s'est écrite : l'outillage rouvert ailleurs la rejoue par `corriger`. Lecture
    pure, comme `verifications_declarees` ; chaque entrée est lue sans rien croire
    (`corrections_lues`).
    """
    return _lire_manifeste(Path(cible)).corrections


def choix_declares(cible: Path | str) -> tuple[Choix, ...]:
    """Les réponses dont l'outillage de `cible` a été écrit — `()` s'il vient d'une analyse (#1350).

    Ce qu'un projet neuf **est**, tant qu'il n'a aucun fichier à lui : le questionnaire
    l'a compris, l'outillage s'en est écrit, et le manifeste le garde (`source.choix`).
    L'équipe d'un projet encore vide s'y compose, plutôt que sur un disque qui ne porte
    que l'outillage de Maestro. Lecture pure, comme `corrections_declarees` ; rien n'y est
    cru sans être relu (`choix_du_manifeste`).
    """
    return choix_du_manifeste(_lire_manifeste(Path(cible)).source)


def _refus_de_version(
    racine: Path, etat: _EtatManifeste, quand: str, verdicts: tuple[Verification, ...]
) -> Rapport | None:
    """Le refus global d'un manifeste d'une version inconnue — `None` s'il se lit.

    **Rien n'est écrit** — ni fichier, ni manifeste. Écraser un manifeste qu'on ne
    comprend pas reviendrait à effacer ce qu'une version future y a déclaré.
    """
    if etat.version_lue is None or etat.version_lue == VERSION_MANIFESTE:
        return None
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


def _verdicts_encore_ecrits(
    racine: Path,
    entrees: Mapping[str, Mapping[str, Any]],
    verdicts: Sequence[Verification],
    neufs: Sequence[Verification],
) -> tuple[Verification, ...]:
    """Les verdicts gardés : ceux des commandes que l'outillage **écrit encore** (#1381).

    `verifications` dit, commande par commande, ce que l'outillage prescrit — et c'est
    ce que le banc rejoue. Une commande remplacée (corrigée par la personne, proposée
    par Maestro à la revue d'après un run) cesse d'y avoir sa place quand plus aucun
    fichier de Maestro ne l'écrit : vu sur S9 (passage `20260929-130422`), le verdict
    « à vérifier » de `python assembler.py` y restait, et le banc la rejouait comme une
    commande prescrite. Tant qu'un fichier la porte encore — un skill pas encore
    réécrit, une pièce passée —, elle y reste.

    Les verdicts de la pièce qu'on écrit (`neufs`) restent sans condition : elle les
    écrit. Les autres se cherchent **tels que Maestro les a écrits**, sur le texte des
    fichiers que le manifeste déclare — la seule preuve qu'une commande est prescrite,
    comme la phrase d'une correction se lit sur la pièce qui la porte. Un fichier qu'on
    ne lit pas en entier — illisible, vide, plus grand que ce qu'on en lit — garde
    tout : dans le doute, on ne retire rien.
    """
    ecrites = {v.commande for v in neufs}
    textes: list[str] = []
    for chemin in entrees:
        texte = lire_texte(racine / PurePosixPath(chemin), OCTETS_MAX + 1)
        if not texte or len(texte.encode("utf-8", errors="replace")) > OCTETS_MAX:
            return tuple(verdicts)
        textes.append(texte)
    return tuple(
        v for v in verdicts if v.commande in ecrites or any(v.commande in t for t in textes)
    )


def _verdicts_fusionnes(
    declares: Sequence[Verification], neufs: Sequence[Verification]
) -> tuple[Verification, ...]:
    """Les verdicts déclarés, ceux d'une même commande remplacés par les neufs — dans l'ordre."""
    par_commande = {verdict.commande: verdict for verdict in declares}
    for verdict in neufs:
        par_commande[verdict.commande] = verdict
    return tuple(par_commande.values())


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


def prevoir(
    cible: Path | str,
    fichier: Fichier,
    *,
    frontiere: FrontiereEcriture | None = None,
) -> Prevision:
    """Ce qu'écrire `fichier` dans `cible` ferait — **sans rien écrire** (#1161).

    La décision est celle de `generer` et de `poser_piece`, prise par la même
    fonction (`_prevision`) sur le même manifeste : c'est ce qui fait que le diff
    qu'une carte montre est celui de ce qui sera posé, et non une seconde lecture
    des quatre cas de docs/38 §4.2. `frontiere` comme pour `generer` — `None` la
    dérive de la cible seule.

    Lecture pure : ni écriture, ni dossier créé, ni exception pour un fichier
    illisible (il compte comme présent, cf. `_texte_existant`).
    """
    racine = Path(cible)
    garde = frontiere or FrontiereEcriture(racine=racine.resolve(), exclus=())
    return _prevision(racine, fichier, _lire_manifeste(racine), garde)


def _prevision(
    racine: Path, fichier: Fichier, etat: _EtatManifeste, garde: FrontiereEcriture
) -> Prevision:
    """La décision d'écriture d'un fichier, prise une fois pour ses deux lecteurs.

    `_poser` l'exécute, `prevoir` la montre. Les raisons ici sont celles que le
    rapport porte — à une exception, le fichier modifié, dont la raison écrite dit
    aussi où la version neuve a été déposée, ce qu'on ne sait qu'après avoir essayé.
    """
    refus = garde.refus_chemin(fichier.chemin, ecriture=True)
    if refus is not None:
        return Prevision(fichier.chemin, "refuse", refus, cas=_CAS_FRONTIERE)
    present, crlf = _texte_en_place(racine / PurePosixPath(fichier.chemin))
    return replace(_decision(fichier, present, etat.empreinte_de(fichier.chemin)), crlf=crlf)


def _decision(fichier: Fichier, present: str | None, connue: str) -> Prevision:
    """Les quatre cas de docs/38 §4.2, sur le texte en place (fins de ligne `\\n`)."""
    if fichier.portee == PORTEE_BLOC:
        return _prevision_bloc(fichier, present, connue)
    if present is not None and not connue:
        return Prevision(
            fichier.chemin,
            "ignore",
            "le projet porte déjà ce fichier et Maestro ne l'a pas écrit : il n'y touche pas.",
            avant=present,
            cas=_CAS_IGNORE,
        )
    if present is not None and _empreinte(present) != connue:
        return Prevision(
            fichier.chemin,
            "refuse",
            "modifié depuis que Maestro l'a écrit : jamais écrasé.",
            avant=present,
            cas=_CAS_MODIFIE,
        )
    if present == fichier.contenu:
        return Prevision(
            fichier.chemin,
            "inchange",
            "déjà à jour — rien n'a été réécrit.",
            avant=present,
            apres=present,
            cas=_CAS_INCHANGE,
        )
    return Prevision(
        fichier.chemin,
        "ecrit",
        "écrit" if present is None else "réécrit : le fichier était celui que Maestro avait posé.",
        avant=present,
        apres=fichier.contenu,
    )


def _prevision_bloc(fichier: Fichier, present: str | None, connue: str) -> Prevision:
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
        return Prevision(
            fichier.chemin,
            "ignore",
            f"`{fichier.chemin}` porte déjà un bloc `maestro-outillage` que Maestro "
            "n'a pas écrit : il n'y touche pas.",
            avant=present,
            cas=_CAS_IGNORE,
            bloc=True,
        )
    if existant is not None and _empreinte(existant) != connue:
        return Prevision(
            fichier.chemin,
            "refuse",
            f"le bloc `maestro-outillage` de `{fichier.chemin}` a été modifié depuis : "
            "jamais écrasé.",
            avant=present,
            cas=_CAS_MODIFIE,
            bloc=True,
        )
    if existant == fichier.contenu.strip():
        return Prevision(
            fichier.chemin,
            "inchange",
            "le bloc était déjà à jour.",
            avant=present,
            apres=present,
            cas=_CAS_INCHANGE,
            bloc=True,
        )
    return Prevision(
        fichier.chemin,
        "ecrit",
        "bloc `maestro-outillage` ajouté à la fin du fichier existant — rien d'autre "
        "n'a été touché."
        if existant is None
        else "bloc `maestro-outillage` réécrit ; le reste du fichier est intact.",
        avant=present,
        apres=_fusionner_bloc(present, fichier.contenu),
        bloc=True,
    )


def _poser(
    racine: Path,
    fichier: Fichier,
    etat: _EtatManifeste,
    garde: FrontiereEcriture,
    quand: str,
) -> tuple[Ecriture, dict[str, Any] | None]:
    """Décide (`_prevision`), puis écrit — et rend l'entrée de manifeste à conserver.

    L'entrée rendue est celle qui décrit **ce que Maestro a écrit** : celle du
    contenu neuf quand il a été posé, celle d'avant quand on a refusé d'écraser.
    Garder l'ancienne dans ce second cas n'est pas un détail : la retirer ferait
    que la génération suivante lirait « présent, absent du manifeste » et
    n'oserait plus jamais toucher au fichier, y compris une fois la personne
    revenue à la version de Maestro.

    Deux nuances de la portée `bloc`, tenues depuis #1033 : un bloc modifié ou une
    écriture de bloc en échec ne gardent **aucune** entrée — un bloc que Maestro ne
    peut plus réécrire n'est plus à lui.
    """
    prevision = _prevision(racine, fichier, etat, garde)
    ancienne = etat.entrees.get(fichier.chemin)
    if prevision.cas == _CAS_FRONTIERE:
        return _ecriture(fichier, "refuse", prevision.raison), ancienne
    if prevision.cas == _CAS_IGNORE:
        return _ecriture(fichier, "ignore", prevision.raison), None
    if prevision.cas == _CAS_MODIFIE:
        if prevision.bloc:
            return _refuser_bloc(racine, fichier)
        return _refuser(racine, fichier, etat)
    if prevision.bloc:
        empreinte = _empreinte(fichier.contenu.strip())
        if prevision.cas == _CAS_INCHANGE:
            return _ecriture(fichier, "inchange", prevision.raison), _entree(
                fichier, empreinte, quand
            )
        erreur = _ecrire_fichier(
            racine / PurePosixPath(fichier.chemin),
            _aux_fins_de(prevision, prevision.apres or ""),
            executable=False,
        )
        if erreur:
            return _ecriture(fichier, "refuse", erreur), None
        return _ecriture(fichier, "ecrit", prevision.raison), _entree(fichier, empreinte, quand)
    empreinte = _empreinte(fichier.contenu)
    if prevision.cas == _CAS_INCHANGE:
        return _ecriture(fichier, "inchange", prevision.raison), _entree(
            fichier, empreinte, _quand_connu(etat, fichier, quand)
        )
    erreur = _ecrire_fichier(
        racine / PurePosixPath(fichier.chemin),
        _aux_fins_de(prevision, fichier.contenu),
        executable=fichier.executable,
    )
    if erreur:
        return _ecriture(fichier, "refuse", erreur), ancienne
    return _ecriture(fichier, "ecrit", prevision.raison), _entree(fichier, empreinte, quand)


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


def empreinte(texte: str) -> str:
    """L'empreinte d'un contenu, celle du manifeste — publique pour qui compare une pièce (#1161).

    Une carte d'outillage garde l'empreinte de ce que le disque portait quand elle a
    été montrée : à l'accord, un fichier qui a bougé entre-temps ne s'écrit pas sur la
    foi d'un diff périmé. Même calcul que le manifeste, jamais un second.
    """
    return _empreinte(texte)


def _empreinte(texte: str) -> str:
    """L'empreinte d'un contenu, telle qu'elle voyage dans le manifeste (`sha256:…`).

    Sur les **octets UTF-8** du texte et non sur le fichier lu tel quel : c'est le
    contenu que Maestro a écrit qui est déclaré. Le texte d'un fichier en place lui
    arrive en fins de ligne `\\n` (`_texte_en_place`) : une différence de fins de ligne
    n'est pas une modification du contenu (#1161).
    """
    return "sha256:" + hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _texte_en_place(cible: Path) -> tuple[str | None, bool]:
    """Le texte du fichier en fins de ligne `\\n`, et s'il était en `\\r\\n` sur le disque.

    ⚠ **Une différence de fins de ligne n'est pas une modification** (#1161) — elle
    l'était, et la vraie stack a montré ce que ça coûtait. Sous Windows, un dépôt en
    `core.autocrlf` remet en `\\r\\n`, à **chaque checkout**, ce que Maestro a écrit en
    `\\n` : dans le dossier de la personne après la fusion d'une écriture versionnée,
    et dans le worktree de l'écriture suivante. Compté comme une modification, le
    fichier cessait d'être à Maestro dès sa première fusion — une pièce déjà écrite ne
    se corrigeait plus, et la génération suivante l'aurait refusée. Aucun caractère du
    contenu n'a changé : on compare le texte, pas ses fins de ligne. Et le choix de
    quiconque les a mises (Git, un éditeur) n'est pas défait : la réécriture les garde
    (`_aux_fins_de`).
    """
    texte = _texte_existant(cible)
    if texte is None:
        return None, False
    return texte.replace("\r\n", "\n"), "\r\n" in texte


def _aux_fins_de(prevision: Prevision, texte: str) -> str:
    """`texte` (en `\\n`) aux fins de ligne du fichier qu'il remplace — cf. `_texte_en_place`."""
    return texte.replace("\n", "\r\n") if prevision.crlf else texte


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
    verifications = donnees.get("verifications")
    return _EtatManifeste(
        version_lue=donnees.get("manifeste"),
        entrees=entrees,
        source=source if isinstance(source, dict) else {},
        verifications=tuple(
            Verification.from_dict(v)
            for v in (verifications if isinstance(verifications, list) else ())
            if isinstance(v, dict) and v.get("commande")
        ),
        corrections=corrections_lues(donnees.get("corrections")),
    )


def _ecrire_manifeste(
    racine: Path,
    entrees: Mapping[str, dict[str, Any]],
    source: dict[str, Any],
    quand: str,
    garde: FrontiereEcriture,
    verifications: Sequence[Verification] = (),
    corrections: Sequence[CorrectionPrise] = (),
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
    `corrections` (#1334) l'est au même titre : une version qui ne la lit pas perd la
    mémoire de ce qui a été dit, jamais celle de ce que Maestro possède.
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
        "corrections": [c.to_dict() for c in corrections],
    }
    texte = json.dumps(donnees, ensure_ascii=False, indent=2) + "\n"
    return _ecrire_fichier(racine / PurePosixPath(CHEMIN_MANIFESTE), texte)
