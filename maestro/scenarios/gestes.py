"""Les gestes d'une personne sur la vraie stack, et ce qu'ils demandent au poste (#1569).

Ce que le banc comparatif et l'exercice construit partagent : dire un message au fil,
faire naître un projet dans la conversation et le ranger, conduire son outillage,
valider l'équipe qu'on propose, accorder un run et le suivre jusqu'à son issue,
reprendre un run interrompu — puis, côté poste, jouer une commande écrite comme un
agent la jouerait et servir un livrable pour sonder ses pages.

Tout passe par **la porte d'entrée réelle** : l'API de la Control Tower, en HTTP, par le
fil de l'orchestrateur (`maestro.scenarios.api`). Rien ici ne choisit un parcours : ce
module rend des gestes, et qui les compose décide de l'ordre — une mesure côte à côte
(`maestro.scenarios.comparatif`), ou l'exercice qu'une session construit pour ce
qu'elle vient d'implémenter (`maestro.scenarios.exercice`). Aucun parcours figé ne les
compose (#1568) : leurs docstrings gardent la raison de chacun, et les défauts qu'il a
appris à éviter sur la vraie stack.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from maestro.controltower.battement import VITALITE_ORPHELIN
from maestro.controltower.chat import DECISION_ECRIRE, DECISION_PASSER
from maestro.controltower.state import STATUTS_EXECUTION_TERMINAUX
from maestro.lanceur.lanceur import HOTE_DEFAUT
from maestro.lanceur.systeme import Systeme
from maestro.outillage.questionnaire import PROPOSITION_RETENUE
from maestro.outillage.verification import Delais
from maestro.sandbox import verification as execution
from maestro.scenarios.api import (
    DELAI_RUN_S,
    ClientAPI,
    ErreurAPI,
    attendre_le_run,
    equipe_validee,
)
from maestro.scenarios.juge import Juge
from maestro.scenarios.modele import Issue, Journal, empeche, rouge
from maestro.scenarios.projets import Atelier
from maestro.scenarios.redemarrage import redemarrer_l_api

#: Ce qu'on laisse au récit de fin pour paraître dans le fil, et l'intervalle
#: entre deux lectures (#1224). Le récit part **après** que le run est soldé — sa
#: rédaction est un appel modèle —, donc le lire une seule fois le manquerait sur un
#: produit qui marche. Deux minutes : c'est la marge d'un appel modèle
#: unique sur un contexte borné, pas celle d'un run.
ATTENTE_RECIT_S = 120.0
INTERVALLE_RECIT_S = 2.0


#: Ce qu'on laisse à la fin d'un run outillé pour paraître (#1343) : le récit, et
#: l'outillage revu sur le projet construit — les commandes s'y rejouent pendant que le
#: modèle rédige, et le message ne part qu'avec les deux. Le récit plus une
#: vérification entière (`Delais.total_s`, lu et non recopié).
ATTENTE_REVUE_S = ATTENTE_RECIT_S + Delais().total_s


#: L'encodage sous lequel la stack de Maestro joue les commandes d'un projet — celui
#: que `start.sh` et `maestro.lanceur` exportent (#141). Voir `jouer_commande`.
ENCODAGE_DES_COMMANDES = ("PYTHONIOENCODING", "utf-8")


#: Ce qu'un test double pour jouer une commande écrite : `(commande, dossier, délai)`
#: → ce qu'elle a rendu, dans la forme de `maestro.sandbox.verification.jouer`.
Joueur = Callable[[str, Path, float], execution.Execution]


#: Ce que la personne répond à une question d'outillage : la question telle que le
#: fil la sert → `(valeur, libre)`, `libre` quand elle répond avec ses mots plutôt
#: que par une option de la carte (`outiller_dans_le_fil`).
Repondeur = Callable[[Mapping[str, Any]], tuple[str, bool]]


DOSSIER_API = "_api"


#: La réponse de la personne à une question du fil : elle ne sait rien de plus, et
#: laisse Maestro choisir. Un geste ne peut pas deviner ce qu'on lui demandera ; il
#: peut dire ce que dirait quelqu'un qui n'a pas d'avis.
REPONSE_SANS_PRECISION = (
    "Je n'ai pas d'autre précision : choisissez ce qui vous paraît le mieux et "
    "proposez-moi le projet."
)


#: Combien de tours le fil a pour arriver à une proposition — la première, puis la
#: corrigée. « Au plus les questions qui manquent » (#1294) : trois tours, c'est
#: déjà deux questions pour un site vitrine ; au-delà, le fil ne propose pas, il
#: interroge.
TOURS_PROPOSITION = 3


#: Combien de gestes l'outillage a pour se construire dans la conversation — questions
#: et pièces confondues. Une recommandation d'aujourd'hui rédige une poignée de
#: fichiers (`AGENTS.md`, un skill par usage constaté) : vingt gestes, c'est de quoi
#: poser les questions qui manquent puis écrire chaque pièce, pas une conversation
#: qui ne finit pas.
GESTES_OUTILLAGE = 20


#: Ce que la personne répond à une question d'outillage qui ne recommande rien : elle
#: n'a pas d'avis. Une question qui en recommande un reçoit **sa** recommandation,
#: comme l'équipe proposée est reprise telle quelle (`equipe_validee`) : un banc qui
#: choisirait à la place du produit jugerait autre chose que ce qu'il propose.
REPONSE_OUTILLAGE = (
    "Je n'ai pas d'avis là-dessus : choisissez ce qui convient le mieux à ce projet."
)


@dataclass
class Contexte:
    """Tout ce que les gestes ont besoin de connaître du monde — et rien d'autre.

    Chaque dépendance est **injectable**, et c'est ce qui permet aux tests de
    jouer les gestes contre une fausse API et un faux fournisseur, sans réseau,
    sans horloge réelle et sans lancer de sous-process.

    `projet_id` et `racine` sont **écrits par les gestes** qui déclarent un projet,
    et relus par qui les compose : ils nomment le projet jetable, et le relevé en a
    besoin même quand le parcours s'arrête avant son issue — c'est là que les pièces
    sont restées.

    `arbitrages` (#1226) est ce que le passage a **tranché à la place de la
    personne** : un outil par demande approuvée. Il se remplit pendant le suivi du
    run, et le relevé le compte.

    `redemarrer` et `sonder_page` (#1408) sont les deux gestes qui touchent au monde
    hors de l'API : couper puis rallumer l'API qui sert le banc
    (`maestro.scenarios.redemarrage`), et demander une page au livrable servi.
    """

    client: ClientAPI
    atelier: Atelier
    juge: Juge
    journal: Journal = field(default_factory=Journal)
    delai_run_s: float = DELAI_RUN_S
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    redemarrer: Callable[[], str] | None = None
    sonder_page: Callable[[str], int | None] | None = None
    projet_id: str = ""
    racine: Path | None = None
    arbitrages: list[str] = field(default_factory=list)

    def note(self, libelle: str, detail: str = "") -> None:
        """Consigne une étape du déroulé."""
        self.journal.note(libelle, detail)

    def rallumer(self) -> str:
        """Coupe puis rallume l'API qui sert le banc — le vrai process, sauf injection.

        Le journal de l'API rallumée va dans l'atelier du passage : c'est une pièce
        du verdict, et le lanceur qui avait démarré la première n'en sait rien.
        """
        if self.redemarrer is not None:
            return self.redemarrer()
        journal = self.atelier.racine / DOSSIER_API / f"api-{datetime.now():%H%M%S}.log"
        return redemarrer_l_api(self.client, journal=journal)

    def sonder_la_page(self, url: str) -> int | None:
        """Le statut HTTP d'une page du livrable servi — la vraie requête, sauf injection."""
        sonde = self.sonder_page or sonder_page
        return sonde(url)


def jouer_commande(commande: str, dossier: Path, delai_s: float) -> execution.Execution:
    """Joue une commande écrite par l'outillage, **comme un agent la jouerait** (#1162).

    Par le bash des agents — Git Bash sous Windows, jamais le `bash.exe` de WSL —,
    retrouvé et lancé par la mécanique de la vérification du produit
    (`maestro.sandbox.verification`) : un démarrage arrêté au délai l'est avec sa
    descendance, et c'est une mécanique du système, pas un verdict. Le **verdict**, lui,
    est celui de qui rejoue (`maestro.scenarios.grille`). Sans bash sur le poste,
    `OSError` : le rejeu n'a pas pu se jouer, ce n'est pas le produit qui s'est trompé.

    ⚠ **Dans l'environnement de la stack, pas dans celui du terminal qui lance le
    rejeu.** Maestro joue les commandes d'un projet — sa vérification (#1160) comme ses
    agents — sous `PYTHONIOENCODING=utf-8`, que `start.sh` et le lanceur exportent
    (#141) ; sans lui, le rejeu mesurerait son terminal, et des tests verts pour
    l'agent qui les a écrits rougiraient sur une sortie cp1252 (#1162). Le réglage ne
    remplace jamais un choix explicite de l'environnement.
    """
    interprete = execution.interprete()
    if interprete is None:
        raise OSError(
            "aucun bash n'a été trouvé sur ce poste pour rejouer les commandes — celui des "
            "agents (Git Bash sous Windows)"
        )
    os.environ.setdefault(*ENCODAGE_DES_COMMANDES)
    return execution.jouer(commande, dossier, interprete=interprete, delai_s=delai_s)


def declarer(ctx: Contexte, nom: str, racine: Path, *, origine: str) -> str:
    """Déclare le projet jetable du passage et le retient dans le contexte."""
    projet_id = ctx.client.declarer_projet(nom, str(racine), origine=origine)
    ctx.projet_id, ctx.racine = projet_id, racine
    ctx.note("projet déclaré", f"{projet_id} — {racine}")
    return projet_id


def doter_d_une_equipe(ctx: Contexte, projet_id: str) -> int:
    """Crée l'équipe que l'analyse du projet recommande — du **montage**, pas l'oracle.

    Par la route d'équipe du projet (#1039 puis #1040), et non par le fil : ce qu'on
    mesure commence après. L'équipe proposée est reprise **telle
    quelle** (`equipe_validee`), parce qu'un utilisateur qui découvre le produit
    accepte ce qu'on lui montre.
    """
    proposition = ctx.client.proposition_equipe(projet_id)
    validee = equipe_validee(proposition)
    ctx.client.creer_equipe(projet_id, validee)
    noms = ", ".join(str(role["nom"]) for role in validee["roles"])
    ctx.note("équipe créée", f"{len(validee['roles'])} rôle(s) : {noms}")
    return len(validee["roles"])


def demander(ctx: Contexte, conversation: str, projet_id: str, texte: str) -> dict[str, Any]:
    """Envoie la demande de travail au fil et rend la réponse de l'orchestrateur."""
    reponse = ctx.client.envoyer(texte, projet_id=projet_id, conversation=conversation)
    ctx.note("demande envoyée", texte)
    ctx.note("réponse du fil", extrait(reponse))
    return reponse


def accorder(
    ctx: Contexte,
    conversation: str,
    projet_id: str,
    *,
    bornes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Donne l'accord au cadrage proposé et rend la réponse qui ouvre le run."""
    reponse = ctx.client.trancher_cadrage(
        conversation=conversation, projet_id=projet_id, bornes=bornes
    )
    ctx.note("accord donné", extrait(reponse))
    return reponse


def suivre(
    ctx: Contexte,
    run_id: str,
    projet_id: str,
    *,
    approuve: bool = True,
    demandes: list[dict[str, Any]] | None = None,
    delai_s: float | None = None,
) -> dict[str, Any]:
    """Suit le run jusqu'à son issue et note ce qu'elle a été.

    `approuve` et `demandes` sont ceux d'`attendre_le_run` : la personne qui refuse,
    et ce qu'elle a refusé. `delai_s` remplace le délai par run quand une partie en a
    déjà été attendue.
    """
    detail = attendre_le_run(
        ctx.client,
        run_id,
        projet_id=projet_id,
        delai_s=ctx.delai_run_s if delai_s is None else delai_s,
        note=ctx.note,
        horloge=ctx.horloge,
        dormir=ctx.dormir,
        arbitrages=ctx.arbitrages,
        approuve=approuve,
        demandes=demandes,
    )
    ctx.note(
        "run soldé",
        f"statut « {detail.get('statut')} », cause « {detail.get('cause') or '—'} », "
        f"{detail.get('nb_taches')} tâche(s), {montant(detail)}",
    )
    return detail


def extrait(message: Mapping[str, Any], longueur: int = 300) -> str:
    """Le contenu d'un message du fil, borné — le rapport se lit à l'œil nu."""
    contenu = str(message.get("contenu") or "").strip().replace("\n", " ")
    return contenu if len(contenu) <= longueur else f"{contenu[: longueur - 1]}…"


def montant(detail: Mapping[str, Any]) -> str:
    """Le coût du run en mots, ou le fait qu'aucun n'a été rapporté."""
    cout = detail.get("cout_usd")
    return "coût non rapporté" if cout is None else f"{float(cout):.4f} $"


def cout(detail: Mapping[str, Any] | None) -> float | None:
    """Le coût du run, tel que l'API l'agrège — `None` quand rien n'est connu."""
    if not detail:
        return None
    cout = detail.get("cout_usd")
    return None if cout is None else float(cout)


def run_de(reponse: Mapping[str, Any]) -> str:
    """Le run que la réponse du fil a ouvert — chaîne vide si elle n'en a ouvert aucun."""
    return str(reponse.get("run_id") or "")


def proposition_du_fil(
    ctx: Contexte,
    conversation: str,
    reponse: dict[str, Any],
    *,
    precision: str = REPONSE_SANS_PRECISION,
) -> dict[str, Any] | None:
    """La carte de projet que le fil pose, en répondant à ses questions — `None` sinon.

    `precision` est ce que la personne répond quand le fil lui demande quelque chose
    avant de proposer : sans avis par défaut, sa feuille pour l'exercice (#1569).
    """
    for tour in range(TOURS_PROPOSITION):
        proposee = reponse.get("projet_propose")
        if isinstance(proposee, Mapping) and proposee.get("racine"):
            return dict(proposee)
        if tour + 1 >= TOURS_PROPOSITION:
            break
        reponse = demander(ctx, conversation, "", precision)
    return None


def meme_dossier(a: str, b: Path) -> bool:
    """Deux écritures du même dossier se reconnaissent — l'API rend du POSIX canonique."""
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:  # pragma: no cover - chemin illisible pour l'OS
        return False


def projet_en_mots(proposee: Mapping[str, Any]) -> str:
    """Une proposition de projet en une ligne — la pièce du déroulé."""
    return (
        f"« {proposee.get('nom')} » — {proposee.get('racine')} — origine "
        f"{proposee.get('origine') or '—'}, versionner : {proposee.get('versionner')}"
    )


def naitre_dans_l_atelier(
    ctx: Contexte,
    conversation: str,
    demande: str,
    racine: Path,
    scenario: str,
    *,
    montage: Callable[[], AbstractContextManager[object]] = nullcontext,
    precision: str = REPONSE_SANS_PRECISION,
) -> Issue | None:
    """Fait proposer un projet neuf par le fil **sans projet**, rangé dans `racine`.

    Rend `None` quand le projet proposé est rangé là.

    La première moitié de la naissance (#1294) : la phrase de la personne, la
    proposition, et la correction en mots qui range le dossier dans l'atelier du
    passage. Rien n'est encore déclaré : c'est l'accord qui le fera
    (`lancer_dans_le_fil`). Une correction qui ne prend pas est un **empêchement** —
    le passage ne déclare rien ailleurs que dans l'atelier.

    `montage` (#1512) encadre cette correction, et elle seule : c'est un geste du
    banc, qui range le projet là où il le relira, et non un geste de la personne,
    qui aurait accepté le dossier proposé. Le banc comparatif y suspend son compte
    des gestes (`maestro.scenarios.comparatif.ClientCompteur.montage`).

    `precision` est ce que la personne répond si le fil l'interroge avant de proposer
    (`proposition_du_fil`).
    """
    proposee = proposition_du_fil(
        ctx, conversation, demander(ctx, conversation, "", demande), precision=precision
    )
    if proposee is None:
        return rouge(
            f"le fil n'a proposé aucun projet en {TOURS_PROPOSITION} tours pour « {demande} »",
            cout_usd=None,
        )
    ctx.note("projet proposé", projet_en_mots(proposee))
    if meme_dossier(str(proposee.get("racine") or ""), racine):
        return None
    with montage():
        corrigee = proposition_du_fil(
            ctx,
            conversation,
            demander(
                ctx, conversation, "", f"Mets-le plutôt dans le dossier {racine.as_posix()}."
            ),
        )
    if corrigee is None or not meme_dossier(str(corrigee.get("racine") or ""), racine):
        return empeche(
            "le projet n'a pas pu être rangé dans l'atelier du banc (proposé : "
            f"{(corrigee or proposee).get('racine')}) : {scenario} ne déclare rien ailleurs",
            cout_usd=None,
        )
    ctx.note("dossier rangé dans l'atelier", projet_en_mots(corrigee))
    return None


@dataclass(frozen=True)
class Lancement:
    """Un projet né dans le fil, outillé et doté, et le run que son travail a ouvert.

    Le parcours d'une personne du premier mot au run (`lancer_dans_le_fil`), et ce
    qu'on relit ensuite : les pièces écrites, l'équipe que l'analyse a proposée et
    celle qui a été validée.
    """

    projet_id: str
    run_id: str
    ecrites: tuple[str, ...]
    proposition: Mapping[str, Any]
    validee: Mapping[str, Any]

    @property
    def equipe(self) -> str:
        """Les rôles recrutés, par leur nom."""
        return ", ".join(str(role["nom"]) for role in self.validee["roles"])


def lancer_dans_le_fil(
    ctx: Contexte,
    conversation: str,
    racine: Path,
    travail: str,
    *,
    versionner: bool = False,
    repondre: Repondeur | None = None,
) -> Lancement | Issue:
    """L'accord, l'outillage, l'équipe, puis le run — le lancement, ou pourquoi il n'a pas eu lieu.

    Tout passe par le fil, dans l'ordre où une personne le vit (#1162) : elle accepte
    le projet proposé, tranche chaque pièce d'outillage que la conversation lui montre,
    demande un premier travail, valide l'équipe qu'on lui propose, puis accorde le run.

    `versionner` (#1408) demande un projet **sous Git** avant le travail : versionné à
    sa naissance quand le fil l'a proposé, sinon par le geste de l'écran Projets
    (`POST …/versionner`) — un poste qui ne sait pas versionner est
    un empêchement, jamais un rouge du produit.

    `repondre` est la personne qui répond aux questions d'outillage
    (`outiller_dans_le_fil`) : sans avis par défaut, ses propres choix pour le banc
    comparatif (#1512).
    """
    naissance = accepter_le_projet(
        ctx, conversation, racine, versionner=versionner, repondre=repondre
    )
    if isinstance(naissance, Issue):
        return naissance
    projet_id, ecrites = naissance

    reponse = demander(ctx, conversation, projet_id, travail)
    if not reponse.get("recrutement"):
        return rouge(
            "le fil n'a proposé aucune équipe sur un projet qui n'en a pas "
            f"(proposition de run : {str(reponse.get('proposition') or '—')!r})",
            run_id=run_de(reponse),
            cout_usd=None,
        )
    recrutement = valider_l_equipe(ctx, conversation, projet_id)
    if isinstance(recrutement, Issue):
        return recrutement
    proposition, validee, suite = recrutement
    if not suite.get("proposition"):
        return rouge(
            "l'équipe validée, le fil n'a pas reproposé le travail demandé", cout_usd=None
        )
    run_id = run_de(accorder(ctx, conversation, projet_id))
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)
    return Lancement(projet_id, run_id, ecrites, proposition, validee)


def accepter_le_projet(
    ctx: Contexte,
    conversation: str,
    racine: Path,
    *,
    versionner: bool = False,
    repondre: Repondeur | None = None,
) -> tuple[str, tuple[str, ...]] | Issue:
    """Accepte le projet que le fil propose, puis conduit son outillage — `(projet, écrites)`.

    La seconde moitié de la naissance (#1294), après `naitre_dans_l_atelier` : l'accord
    déclare le projet, la conversation l'outille pièce après pièce (`outiller_dans_le_fil`),
    et `versionner` le met sous Git — voir `lancer_dans_le_fil`, qui enchaîne ensuite le
    travail, et l'exercice (#1569), qui s'arrête là.
    """
    accord = ctx.client.declarer_par_le_fil(conversation=conversation)
    ctx.note("accord donné", extrait(accord))
    cree = accord.get("projet_cree")
    if not isinstance(cree, Mapping) or not cree.get("id"):
        return rouge(f"l'accord n'a déclaré aucun projet : {extrait(accord)}", cout_usd=None)
    projet_id = str(cree["id"])
    ctx.projet_id, ctx.racine = projet_id, racine

    ecrites, inacheve = outiller_dans_le_fil(ctx, conversation, accord, repondre=repondre)
    if inacheve:
        return rouge(inacheve, cout_usd=None)
    if not ecrites:
        return rouge(
            "aucune pièce d'outillage ne s'est écrite dans la conversation : le projet né "
            "n'a reçu ni instructions ni commandes",
            cout_usd=None,
        )
    ctx.note("outillage écrit sur accord", ", ".join(ecrites))

    if versionner:
        if cree.get("versionne"):
            ctx.note("projet versionné", "dès sa naissance, comme le fil l'a proposé")
        else:
            fiche = ctx.client.versionner_projet(projet_id)
            if not fiche.get("vcs"):
                return empeche(
                    "le projet né dans le fil n'a pas pu être versionné par le geste de "
                    f"l'écran Projets ({cree.get('versionnement_refuse') or 'sans cause dite'})",
                    cout_usd=None,
                )
            ctx.note("projet versionné", "par `POST /api/projets/{id}/versionner`")
    return projet_id, tuple(ecrites)


def valider_l_equipe(
    ctx: Contexte, conversation: str, projet_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]] | Issue:
    """Valide dans le fil l'équipe que l'analyse propose — `(proposée, validée, suite du fil)`.

    Le geste qui répond à une demande de recrutement (#1146) : l'équipe est reprise
    **telle quelle** (`equipe_validee`), comme une personne accepte ce qu'on lui montre.
    """
    proposition = ctx.client.proposition_equipe(projet_id)
    validee = equipe_validee(proposition)
    if not validee["roles"]:
        return rouge("l'analyse du projet n'a proposé aucun rôle", cout_usd=None)
    suite = ctx.client.recruter(conversation=conversation, validee=validee)
    noms = ", ".join(str(role["nom"]) for role in validee["roles"])
    ctx.note("équipe validée dans le fil", f"{noms} — {extrait(suite)}")
    return proposition, validee, suite


def message_de_fin(
    ctx: Contexte, conversation: str, run_id: str, attente_s: float
) -> Mapping[str, Any] | None:
    """Le message que la **fin** du run a écrit dans le fil — `None` s'il n'y vient pas.

    Le récit se reconnaît à sa place et à son rattachement, jamais à ses mots :
    un message de l'orchestrateur, portant ce `run_id`, et qui n'est pas le
    premier — le premier étant la réponse qui a ouvert le run (#268). Juger sur
    le contenu reviendrait à chercher un lexique (#746) dans ce que le modèle a
    écrit, ce que ce banc existe précisément pour ne pas faire.

    ⚠ **Il faut l'attendre**, et c'est une propriété du produit, pas une
    commodité du banc : le récit s'écrit quand la fin **passe**, et sa rédaction
    est un appel modèle qui part *après* que le run est soldé. Mesuré le
    2026-09-23 sur le passage `20260923-185330` — le run était terminé, le fil
    relu dans la foulée ne portait encore que le lancement, et le récit y est
    arrivé quelques secondes plus tard. Lire une seule fois rendait donc un rouge
    sur un produit qui marche, ce qui est le pire des verdicts.

    L'attente est **bornée et dite** : passé `attente_s`, on rend `None` et l'oracle
    tranche. Elle passe par l'horloge et le sommeil du contexte, comme le suivi d'un
    run — les tests jouent donc ce chemin sans attendre.
    """
    limite = ctx.horloge() + attente_s
    while True:
        porteurs = [
            message
            for message in ctx.client.fil(conversation)
            if str(message.get("run_id") or "") == run_id
            and str(message.get("auteur") or "") != "utilisateur"
        ]
        if len(porteurs) > 1:
            return porteurs[-1]
        if ctx.horloge() >= limite:
            return None
        ctx.dormir(INTERVALLE_RECIT_S)


def revue_d_apres_le_run(
    ctx: Contexte, conversation: str, run_id: str, *, repondre: Repondeur | None = None
) -> str:
    """Tranche l'outillage que la fin du run a revu — `""`, ou pourquoi il ne s'est pas soldé.

    #1343 : ce qu'un projet neuf ne pouvait pas jouer se joue **dès que le projet le
    permet**, et c'est la fin du run qui le propose — la pièce revue voyage sur le
    message du récit (`maestro.controltower.recit`). Le banc la tranche comme toute
    pièce, en personne sans avis (`outiller_dans_le_fil`), **avant** de rejouer : ce
    qu'il rejoue ensuite est ce que le manifeste dit une fois l'outillage revu.

    L'attente est celle du récit **plus** celle d'une vérification entière : la revue
    rejoue les commandes du projet pendant que le modèle rédige, et le message ne part
    qu'avec les deux. Une fin qui ne vient pas n'est pas un rouge ici : le rejeu
    tranchera sur le manifeste tel qu'il est.
    """
    fin = message_de_fin(ctx, conversation, run_id, ATTENTE_REVUE_S)
    if fin is None:
        ctx.note("fin du run", f"aucun message de fin dans le fil en {ATTENTE_REVUE_S:g} s")
        return ""
    if not isinstance(fin.get("piece"), Mapping):
        return ""
    revues, inacheve = outiller_dans_le_fil(ctx, conversation, fin, repondre=repondre)
    if revues:
        ctx.note("outillage revu après le run", ", ".join(revues))
    return inacheve


def outiller_dans_le_fil(
    ctx: Contexte,
    conversation: str,
    message: Mapping[str, Any],
    *,
    repondre: Repondeur | None = None,
) -> tuple[list[str], str]:
    """Conduit l'outillage que le fil ouvre, geste après geste — rend les pièces écrites (#1161).

    Le banc répond à ce que **le dernier message** demande, et à rien d'autre — la
    règle du canal (`question_en_attente`, `piece_en_attente`) : une question reçoit
    sa réponse (`repondre`, sans avis par défaut : `reponse_a`), une pièce est écrite
    telle que la carte la montre, par son empreinte, ou passée si cette version ne peut
    pas s'écrire. Le dernier message qui ne demande plus rien clôt l'outillage.

    Rend les chemins **écrits** — lus sur le fait que la réponse porte
    (`piece_ecrite`), jamais sur la pièce proposée —, et un motif quand l'outillage ne
    s'est pas soldé en `GESTES_OUTILLAGE` gestes.
    """
    repondre = repondre or reponse_a
    ecrites: list[str] = []
    for _geste in range(GESTES_OUTILLAGE):
        question = message.get("question")
        piece = message.get("piece")
        if isinstance(question, Mapping) and question.get("cle"):
            valeur, libre = repondre(question)
            ctx.note(
                "question d'outillage",
                f"« {question.get('intitule')} » → {valeur}{' (avec ses mots)' if libre else ''}",
            )
            message = ctx.client.repondre_question(
                conversation=conversation, valeur=valeur, libre=libre
            )
            continue
        if isinstance(piece, Mapping) and piece.get("chemin"):
            decision = DECISION_ECRIRE if piece.get("ecrivable", True) else DECISION_PASSER
            message = ctx.client.trancher_piece(
                conversation=conversation,
                decision=decision,
                piece=str(piece.get("empreinte") or ""),
            )
            fait = message.get("piece_ecrite")
            etat = str(fait.get("etat") or "—") if isinstance(fait, Mapping) else "—"
            ctx.note(
                "pièce d'outillage",
                f"{piece.get('chemin')} ({piece.get('nature') or '—'}, pièce "
                f"{piece.get('rang')}/{piece.get('total')}) — {decision} → {etat} ; "
                f"commandes : {verdicts_en_mots(piece.get('verifications'))}"
                f"{proposees_en_mots(piece)}",
            )
            if isinstance(fait, Mapping) and fait.get("ecrite"):
                chemin = str(fait.get("chemin") or piece.get("chemin"))
                if chemin not in ecrites:
                    ecrites.append(chemin)
            continue
        ctx.note("outillage soldé", extrait(message))
        return ecrites, ""
    return ecrites, (
        f"l'outillage ne s'est pas soldé en {GESTES_OUTILLAGE} gestes : la conversation "
        "proposait encore une question ou une pièce"
    )


def reponse_a(question: Mapping[str, Any]) -> tuple[str, bool]:
    """La réponse du banc à une question d'outillage — `(valeur, libre)`.

    La recommandation de la question quand elle en porte une qui est l'une de ses
    options ; sinon, avec ses mots, qu'elle n'a pas d'avis (`REPONSE_OUTILLAGE`). La
    proposition de l'outillage entier (#1533), une personne sans avis la **retient**,
    d'un geste : c'est la recommandation de Maestro, faite d'un bloc.
    """
    if question.get("proposition"):
        return PROPOSITION_RETENUE, False
    options = {
        str(option.get("valeur") or "")
        for option in question.get("options") or []
        if isinstance(option, Mapping)
    }
    recommande = str(question.get("recommande") or "")
    if recommande and recommande in options:
        return recommande, False
    return REPONSE_OUTILLAGE, True


def proposees_en_mots(piece: Mapping[str, Any]) -> str:
    """Les commandes que Maestro propose dans cette pièce (#1381), et où il les a lues.

    La pièce du déroulé qui dit qu'une commande ne vient ni du projet né, ni de la
    personne : la revue d'après le run l'a lue dans le projet construit et l'a jouée.
    """
    proposees = piece.get("proposees")
    if not isinstance(proposees, list) or not proposees:
        return ""
    lues = piece.get("lues_dans")
    ou = ", ".join(str(c) for c in lues) if isinstance(lues, list) and lues else "—"
    commandes = ", ".join(f"`{c}`" for c in proposees)
    return f" ; proposée(s) par Maestro : {commandes}, lue(s) dans {ou}"


def verdicts_en_mots(verifications: Any) -> str:
    """Les verdicts qu'une pièce porte, en une ligne — « aucune » sans commande."""
    if not isinstance(verifications, list) or not verifications:
        return "aucune"
    return " ; ".join(
        f"`{v.get('commande')}` {v.get('etat')}"
        for v in verifications[:6]
        if isinstance(v, Mapping)
    )


def runs_en_vol(ctx: Contexte) -> tuple[str, ...]:
    """Les runs de la stack que l'extinction solderait — non soldés, et dont l'hôte n'est pas mort.

    Un run **orphelin** (#348) n'a plus d'hôte : l'extinction ne le touche pas.
    """
    return tuple(
        str(resume.get("run_id") or "")
        for resume in ctx.client.executions()
        if str(resume.get("statut") or "") not in STATUTS_EXECUTION_TERMINAUX
        and str(resume.get("vitalite") or "") != VITALITE_ORPHELIN
    )


def reprendre(ctx: Contexte, run_id: str) -> tuple[str, str]:
    """Le geste « Reprendre » sur le run interrompu — rend le run qui continue, et le geste joué.

    Depuis #1391, c'est **un** geste, celui de l'écran : `…/reprendre` continue sur son
    plan un run que l'extinction a soldé, pause comprise — le bouton du panneau *Runs
    qui n'avancent plus* et celui de la vue du run l'appellent tous deux. Avant lui,
    `…/reprendre` refusait ce run (`409`) et l'écran offrait la relance, un **nouveau**
    run : le banc jouait les deux dans cet ordre. Un refus remonte désormais tel quel —
    c'est le run qui ne se reprend pas, et l'oracle le dit.
    """
    resume = ctx.client.reprendre(run_id)
    return str(resume.get("run_id") or run_id), "reprise (`…/reprendre`)"


#: Les postes du grand livre d'un projet que le rapport détaille : ce que Maestro
#: dépense hors des agents — la conduite des runs (#1028) et le fil (#1534). Ce sont
#: les noms des champs de la vue Coûts & analytics, et le rapport les reprend tels quels.
POSTES_DU_GRAND_LIVRE: tuple[str, ...] = ("orchestration", "fil")


def cout_du_grand_livre(
    client: ClientAPI, projet_id: str, detail: Mapping[str, Any]
) -> tuple[float | None, str, dict[str, float]]:
    """Ce que le projet a coûté, et ses postes — lu au grand livre du projet, du run à défaut.

    Le total de la vue Coûts & analytics compte, depuis #1534, ce que le fil dépense
    pour le projet et ce que coûtent les tentatives coupées en vol. C'est le compte du
    produit, ce qu'il voit et rien de plus : ce que le grand livre ne voit pas, on ne
    l'invente pas (le banc comparatif, #1512, puis l'exercice, #1569).
    """
    if projet_id:
        try:
            vue = client.couts(projet_id)
        except ErreurAPI:
            vue = {}
        total = vue.get("total")
        if isinstance(total, Mapping) and total.get("cout_usd") is not None:
            return (
                float(total["cout_usd"]),
                "grand livre du projet (Coûts & analytics)",
                _postes(vue),
            )
    if detail.get("cout_usd") is not None:
        return float(detail["cout_usd"]), "grand livre du run", {}
    return None, "non rapporté", {}


def _postes(vue: Mapping[str, Any]) -> dict[str, float]:
    """Le coût de chaque poste que la vue rapporte — un poste sans coût mesuré n'y est pas."""
    postes: dict[str, float] = {}
    for nom in POSTES_DU_GRAND_LIVRE:
        poste = vue.get(nom)
        usage = poste.get("usage") if isinstance(poste, Mapping) else None
        if isinstance(usage, Mapping) and usage.get("cout_usd") is not None:
            postes[nom] = float(usage["cout_usd"])
    return postes


#: Ce que les commandes du livrable reçoivent : elles jouent comme dans une
#: intégration continue, sans terminal — Vitest ne s'y met pas en veille, et rien ne
#: s'arrête sur une question.
SANS_TERMINAL = "CI=1"


#: Ce qu'on laisse à `npm start` pour démarrer et servir chaque page. La fenêtre est
#: attendue en entier : le serveur, qui ne rend jamais la main, est arrêté avec sa
#: descendance à son terme (`maestro.sandbox.verification.jouer`). Ce n'est pas la
#: fenêtre de démarrage de la vérification du produit (`Delais.demarrage_s`), qui
#: constate qu'un service **tourne** : ici on attend qu'il **réponde**.
DELAI_SERVICE_S = 60.0
INTERVALLE_SERVICE_S = 1.0
DELAI_SONDE_PAGE_S = 5.0


#: Le port à partir duquel le banc cherche où servir le livrable — loin de ceux des
#: stacks du poste (8000 et 3000, puis ceux que les worktrees dérivent de leur ticket).
PORT_SERVICE = 4600


#: Le statut d'une page servie.
PAGE_SERVIE = 200


def sonder_page(url: str) -> int | None:
    """Le statut HTTP que rend `url` — `None` si personne ne répond.

    Un `404` ou un `500` **répondent**, et c'est leur statut qui est rendu : l'oracle
    distingue une page absente d'un serveur muet.
    """
    try:
        with urlopen(url, timeout=DELAI_SONDE_PAGE_S) as reponse:  # noqa: S310 - boucle locale
            return int(reponse.status)
    except HTTPError as erreur:
        return int(erreur.code)
    except (URLError, OSError, ValueError):
        return None


class Poste(Protocol):
    """Ce que servir un livrable demande au poste : jouer, sonder, attendre (#1512).

    `Contexte` le satisfait ; le banc comparatif en a un à lui, qui ne parle à
    aucune API (`maestro.scenarios.grille`).
    """

    @property
    def horloge(self) -> Callable[[], float]: ...

    @property
    def dormir(self) -> Callable[[float], None]: ...

    def jouer(self, commande: str, dossier: Path, delai_s: float) -> execution.Execution: ...

    def sonder_la_page(self, url: str) -> int | None: ...


def _a_redemander(statut: int | None, attendu: int) -> bool:
    """La page doit-elle être redemandée ? Tant qu'elle n'a pas son statut — sauf une absente.

    Une page **servie** se redemande jusqu'à rendre `200` : un serveur qui démarre peut
    répondre autre chose le temps d'être prêt. Une page qui ne doit **pas** exister se
    juge à sa **première réponse**, et ne se redemande que tant que personne ne répond
    (#1512). Mesuré au rejeu de C3 le 2026-10-15 : une fiche inconnue rend `200` à la
    première visite — le faux 404 du bilan —, puis `404` une fois en cache ; redemander
    jusqu'au `404` effaçait le défaut que la personne voit.
    """
    if statut == attendu:
        return False
    return attendu == PAGE_SERVIE or statut is None


#: Combien de ports le banc essaie, à partir du premier, pour servir un livrable.
ESSAIS_PORT_SERVICE = 50


def port_de_service(depuis: int = PORT_SERVICE) -> int | None:
    """Le port où servir un livrable, à partir de `depuis` — `None` s'il n'y en a aucun.

    ⚠ **Libre en IPv4 comme en IPv6** (#1512). S'attacher à `127.0.0.1` ne suffit pas :
    sous Windows, l'attachement réussit alors qu'un serveur Node écoute en double pile
    sur `::` — ce que fait `next start`. Mesuré au rejeu de C3 le 2026-10-15 : le port
    4600, jugé libre, était tenu ; le livrable est tombé sur `EADDRINUSE :::4600`, et la
    sonde a interrogé le serveur d'un autre, dont elle a rendu les statuts comme ceux du
    livrable. Le port retenu s'attache donc dans les deux familles. Par attachement
    seulement, sans connexion d'essai : sous Windows, une connexion refusée attend son
    délai entier, et un balayage en ouvrirait une par port (`Systeme.port_libre_depuis`).
    Sur un poste sans IPv6, seule la famille IPv4 compte.
    """
    systeme = Systeme()
    for port in range(depuis, min(depuis + ESSAIS_PORT_SERVICE, 65536)):
        if systeme.attachable(HOTE_DEFAUT, port) and _libre_en_ipv6(port):
            return port
    return None


def _libre_en_ipv6(port: int) -> bool:
    """Peut-on écouter sur `[::]:port` **en double pile**, comme Node ? Vrai sans IPv6.

    En double pile (`IPV6_V6ONLY` à 0) et non en IPv6 seul : c'est ainsi que Node
    s'attache, et sous Windows un attachement IPv6 seul ne heurte pas un serveur qui
    tient le port en double pile — mesuré en écrivant ce test, le port tenu passait.
    """
    if not socket.has_ipv6:
        return True
    try:
        with _prise_double_pile() as prise:
            prise.bind(("::", 0))
    except OSError:
        return True  # IPv6 indisponible : personne n'y écoute
    try:
        with _prise_double_pile() as prise:
            prise.bind(("::", port))
    except OSError:
        return False
    return True


def _prise_double_pile() -> socket.socket:
    """Une prise IPv6 qui accepte aussi l'IPv4 — l'option ignorée là où elle n'existe pas."""
    prise = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        prise.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    except (AttributeError, OSError):
        pass
    return prise


def servir(
    poste: Poste, copie: Path, *, commande: str, attendus: Mapping[str, int]
) -> tuple[dict[str, int | None], str]:
    """Sert `copie` par `commande` et sonde ses pages — leurs statuts, et la fin de sa sortie.

    `attendus` donne, page par page, le statut qu'elle doit rendre : `200` pour une
    page servie, `404` pour une page qui ne doit pas exister (le banc comparatif,
    #1512). Le port est donné à `commande` par la variable `PORT`.

    Le serveur tourne dans un fil à part, borné par `DELAI_SERVICE_S` et arrêté avec
    sa descendance à son terme ; les pages sont sondées pendant ce temps, jusqu'à ce
    qu'elles aient toutes rendu leur statut attendu, que le serveur se soit arrêté, ou
    que la fenêtre soit passée. Une sonde a toujours lieu, même sur un serveur déjà
    tombé : c'est ce qui dit « sans réponse » plutôt que rien.
    """
    port = port_de_service()
    vues: dict[str, int | None] = dict.fromkeys(attendus)
    if port is None:
        return vues, f"aucun port libre à partir de {PORT_SERVICE} pour le servir"
    rendu: list[execution.Execution] = []
    serveur = threading.Thread(
        target=lambda: rendu.append(poste.jouer(f"PORT={port} {commande}", copie, DELAI_SERVICE_S)),
        daemon=True,
    )
    serveur.start()
    limite = poste.horloge() + DELAI_SERVICE_S
    while True:
        for page, attendu in attendus.items():
            if not _a_redemander(vues[page], attendu):
                continue
            vues[page] = poste.sonder_la_page(f"http://{HOTE_DEFAUT}:{port}{page}")
        rendues = all(not _a_redemander(vues[page], attendu) for page, attendu in attendus.items())
        if rendues or not serveur.is_alive() or poste.horloge() >= limite:
            break
        poste.dormir(INTERVALLE_SERVICE_S)
    serveur.join(timeout=DELAI_SERVICE_S + DELAI_SONDE_PAGE_S)
    sortie = " ".join(rendu[0].sortie.split())[-300:] if rendu else ""
    return vues, sortie
