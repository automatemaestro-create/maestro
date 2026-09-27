"""Tests du **bilan d'un run**, rendu sur pièces à sa fin (#1284, parent #1281).

`maestro/controltower/bilan.py` répond au run réel `3fe501fc0878` (projet `p3`,
2026-09-24) : la maquette y est tombée trois fois à l'identique sur le plafond de
flux du SDK (#1277), le moteur a relancé en présumant un aléa, et le récit de fin a
recopié « échec transitoire » puis conseillé de relancer. Ces tests **rejouent les
pièces de `p3`** — ses lignes de journal, passées par le vrai pont
(`bridge.evenements_depuis_step`) dans la vraie projection et le vrai journal
requêtable, comme la pompe le fait — et gardent, couche par couche :

① **les pièces** — lues au journal du run **en entier**, au-delà des 200 entrées
   qu'une page du journal sert ; les décisives passent avant le bruit de fond quand
   le budget mord, et ce qu'il laisse dehors est compté ; chaque pièce garde les
   entrées du journal dont elle vient ;
② **`p3`** — ce qui atteint le juge : les trois tentatives, la cause verbatim de
   chacune, les 2 092 911 tokens sans prix, et le libellé du moteur présenté comme
   une présomption, jamais comme un verdict ;
③ **la vérification** — un constat sans pièce, ou qui cite une pièce absente, est
   écarté avec sa raison ; la sonde est prouvée sur un constat qui tient ;
④ **le service** — un bilan par issue, un seul appel au modèle même demandé deux
   fois, son coût compté au run hors de son temps de mur, rien de fabriqué quand le
   modèle se tait ;
⑤ **l'API et le journal durable** — le bilan servi, et relu après un redémarrage
   sans rappeler le modèle ;
⑥ **le récit de fin** — il lit le bilan, et sa consigne dit que, sur un échec
   déterministe, on ne relance pas tel quel. Jugé sur ce que le prompt contient,
   jamais sur ce qu'un modèle en a fait (#746).

Aucun modèle, aucun réseau, sauf le test marqué `cli_reel` : lui rejoue `p3` devant
le **vrai** modèle et vérifie que l'échec répété n'y est pas jugé aléa — sauté sauf
`MAESTRO_TESTS_CLI_REEL=1` (`tests/conftest.py`, dixième garde-fou).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.bilan import (
    FAMILLE_ACTE,
    FAMILLE_ACTIVITE,
    FAMILLE_CHECKLIST,
    FAMILLE_DECISION,
    FAMILLE_ECHANGE,
    FAMILLE_RELANCE,
    FAMILLE_STATUT,
    FAMILLE_USAGE,
    NATURE_ALEA,
    NATURE_DETERMINISTE,
    NATURE_INDETERMINEE,
    RAISON_AUCUNE_PIECE,
    RAISON_PIECE_INEXISTANTE,
    RAISON_RUBRIQUE_INCONNUE,
    RUBRIQUE_ECHEC,
    RUBRIQUE_RECOMMANDATION,
    STATUT_BILAN_ILLISIBLE,
    STATUT_BILAN_RENDU,
    SYSTEME,
    BilanRun,
    Constat,
    JugeModele,
    ServiceBilan,
    assembler_les_pieces,
    bilan_en_texte,
    prompt_du_bilan,
    verifier,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.chat import UTILISATEUR, ChatStore, MessageChat
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.journal import TAILLE_PAGE_MAX, ServiceJournal
from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
from maestro.controltower.persistence import InMemoryEventLog
from maestro.controltower.portee import PorteeRun
from maestro.controltower.recit import SYSTEME as SYSTEME_RECIT
from maestro.controltower.recit import ConteurDeFin, contexte_du_recit
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    ControlTowerState,
)
from maestro.engine.executor import (
    STATUT_ARBITRAGE_OUTIL,
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_ECRITURE_EN_PLACE,
    STATUT_EN_COURS,
    STATUT_QUESTION_SANS_REPONSE,
    STATUT_REFUS_OUTIL,
    STATUT_TERMINEE,
)
from maestro.engine.rattrapage import statut_du_geste
from maestro.engine.verification import STATUT_VERIFICATION_NON_TENUE
from maestro.plan_run import NoeudPlan
from maestro.telemetry import ETAPE_BILAN, StepUsage

RUN = "3fe501fc0878"
PROJET = "p3"
MAQUETTE = "maquette-sections"
TEXTES = "contenus-textes"
INTEGRATION = "integration-page"
RELECTURE = "relecture-qa"

#: La cause de l'incident, telle que le SDK la rendait (`SDKJSONDecodeError`, #1277).
CAUSE_P3 = (
    "Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes..."
)

#: Le compte de `p3` : le ticket le cite, et c'est ce qui dit qu'un run réel passe
#: déjà près de la page du journal (200).
ENTREES_P3 = 183

#: Les lignes du rejeu qui ne sont pas de l'activité : lancement, planification, les
#: trois des textes, les sept de la maquette, les deux tâches bloquées, l'issue.
_LIGNES_HORS_BRUIT = 15


# --------------------------------------------------------------------------
# Le rejeu des pièces de `p3`
# --------------------------------------------------------------------------


def _instant(rang: int) -> str:
    """Un horodatage du 2026-09-24, une seconde par ligne — l'ordre du journal."""
    minutes, secondes = divmod(rang, 60)
    heures, minutes = divmod(minutes, 60)
    return f"2026-09-24T{10 + heures:02d}:{minutes:02d}:{secondes:02d}+00:00"


class Rejeu:
    """Rejoue des lignes de journal comme la pompe : pont, projection, journal requêtable."""

    def __init__(self) -> None:
        self.state = ControlTowerState()
        self.journal = ServiceJournal()
        self._rang = 0

    def evenement(self, event: Event) -> None:
        self.state.appliquer(event)
        self.journal.consigner(self.state.au_projet_de_son_run(event))

    def ligne(self, etape: str, **champs: Any) -> None:
        """Une ligne du journal du moteur (`StepRecord.to_dict`), passée par le vrai pont."""
        self._rang += 1
        record = {
            "run_id": RUN,
            "etape": etape,
            "nom": "",
            "agent": "",
            "role": "",
            "statut": "",
            "entree": "",
            "sortie": "",
            "erreur": None,
            "usage": StepUsage().to_dict(),
            "projet_id": PROJET,
            "horodatage": _instant(self._rang),
            **champs,
        }
        for event in evenements_depuis_step(record):
            self.evenement(event)

    def execution(self, statut: str, detail: str, **champs: Any) -> None:
        self._rang += 1
        self.evenement(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=RUN,
                agent=ACTEUR_RUN,
                statut=statut,
                detail=detail,
                projet_id=PROJET,
                horodatage=_instant(self._rang),
                **champs,
            )
        )


def _bruit(rejeu: Rejeu, tache: str, agent: str, role: str, geste: str, nombre: int) -> None:
    """L'activité ligne à ligne d'un agent — le bruit de fond d'un run.

    `geste` se décline sur quelques fichiers qui reviennent : c'est ce que fait un
    agent qui itère, et un rejeu qui lui prêterait soixante fichiers distincts
    raconterait un autre run que `p3`.
    """
    for rang in range(nombre):
        rejeu.ligne(
            f"{tache}:activite",
            nom="Activité",
            agent=agent,
            role=role,
            statut="activite",
            sortie=geste.format(rang % 3 + 1),
        )


def rejouer_p3(*, total: int = ENTREES_P3) -> Rejeu:
    """Les pièces du run `3fe501fc0878`, du lancement à l'échec, en `total` entrées.

    Ce qu'il a fait, d'après les enquêtes de #1277 à #1280 : les textes livrés ; la
    maquette lancée trois fois, un `Bash` accordé d'office en chemin, relancée deux
    fois sur la même cause, puis en échec avec deux millions de tokens sans prix ;
    l'intégration et la relecture bloquées derrière elle. Le reste est l'activité
    des agents, qui fait le compte.
    """
    rejeu = Rejeu()
    rejeu.execution(
        EXECUTION_EN_COURS,
        "Lancement",
        titre="Un site vitrine pour une boisson",
        description="Un site vitrine pour une boisson pétillante au yuzu, en trois sections.",
    )
    rejeu.ligne(
        "planification",
        nom="Planification de l'objectif",
        agent=ACTEUR_RUN,
        role="Orchestrateur",
        statut=STATUT_TERMINEE,
        sortie="4 tâche(s)",
        usage=StepUsage(
            appels=1, tokens_entree=11_000, tokens_sortie=1_000, cout_usd=0.05
        ).to_dict(),
    )
    # Les textes : livrés.
    rejeu.ligne(f"{TEXTES}:debut", nom="Rédiger les textes", agent="redacteur",
                role="Rédaction", statut=STATUT_EN_COURS, sortie="démarrage de la tâche")
    rejeu.ligne(f"{TEXTES}:fusion", nom="Fusion — Rédiger les textes", agent="redacteur",
                role="Rédaction", statut=STATUT_ECRITURE_EN_PLACE,
                sortie="écrit dans le projet : textes/accueil.md, textes/produit.md")
    rejeu.ligne(TEXTES, nom="Rédiger les textes", agent="redacteur", role="Rédaction",
                statut=STATUT_TERMINEE, sortie="Trois textes écrits dans textes/.",
                usage=StepUsage(appels=1, tokens_entree=680_000, tokens_sortie=9_764,
                                cout_usd=1.12).to_dict())
    # La maquette : trois tentatives, la même cause. L'activité de ses trois sessions
    # fait le compte du ticket — tout ce qui n'est pas elle tient en quinze lignes.
    bruit = total - _LIGNES_HORS_BRUIT
    parts = [bruit // 3 + (1 if rang < bruit % 3 else 0) for rang in range(3)]
    for tentative in (1, 2, 3):
        rejeu.ligne(
            f"{MAQUETTE}:debut", nom="Maquetter les sections", agent="interface",
            role="Interface", statut=STATUT_EN_COURS,
            sortie=(
                "démarrage de la tâche" if tentative == 1
                else f"redémarrage de la tâche (tentative {tentative}/3)"
            ),
        )
        if tentative == 1:
            rejeu.ligne(
                f"{MAQUETTE}:refus-outil", nom="Outil arbitré (auto) — Maquetter les sections",
                agent="interface", role="Interface", statut=STATUT_ARBITRAGE_OUTIL,
                entree="Bash",
                sortie=(
                    "Bash approuvé d'office (auto) : "
                    "msedge --headless --remote-debugging-port=9222"
                ),
            )
        _bruit(
            rejeu, MAQUETTE, "interface", "Interface", "relit captures/section-{}.png",
            parts[tentative - 1],
        )
        if tentative < 3:
            geste = (
                f"échec transitoire (tentative {tentative}/3) : {CAUSE_P3} "
                f"— relance dans {2 * tentative:g} s."
            )
            rejeu.ligne(f"{MAQUETTE}:relance", nom="Relance — Maquetter les sections",
                        agent="interface", role="Interface", statut="relance",
                        entree=CAUSE_P3, sortie=geste)
    rejeu.ligne(
        MAQUETTE, nom="Maquetter les sections", agent="interface", role="Interface",
        statut=STATUT_ECHEC, sortie="",
        erreur=(
            f"{CAUSE_P3} — échec transitoire persistant après 3 tentatives "
            "(relances épuisées)."
        ),
        usage=StepUsage(tokens_entree=2_050_000, tokens_sortie=42_911, tours=46).to_dict(),
    )
    for tache, titre in ((INTEGRATION, "Intégrer la page"), (RELECTURE, "Relire le site")):
        rejeu.ligne(tache, nom=titre, agent="—", role="", statut=STATUT_BLOQUEE,
                    erreur=f"dépendance en échec : {MAQUETTE}")
    rejeu.execution(EXECUTION_ECHEC, "1/4 tâche(s) réussie(s) — maquette-sections en échec.")
    assert len(rejeu.journal) == total
    return rejeu


def _dossier(rejeu: Rejeu, **reglages: Any):
    execution = rejeu.state.execution(RUN)
    assert execution is not None
    return assembler_les_pieces(
        execution,
        rejeu.journal.entrees_du_run(RUN),
        rejeu.state.taches(run=PorteeRun.run(RUN)),
        **reglages,
    )


def _ids(dossier, famille: str, tache: str = "") -> list[str]:
    return [
        p.id for p in dossier.pieces
        if p.famille == famille and (not tache or p.tache_id == tache)
    ]


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------

#: Ce qu'un appel de bilan coûte dans ces tests — mesuré, jamais nul, pour que le
#: compte au run se voie.
USAGE_BILAN = StepUsage(appels=1, tokens_entree=9_000, tokens_sortie=600, cout_usd=0.03)


class JugeScripte:
    """Un `JugeBilan` qui note ce qu'on lui donne et rend ce qu'on lui a posé.

    `reponse` peut être une fonction du prompt : c'est ce qui laisse un test citer
    les pièces **telles que le dossier les a numérotées**, sans lire le prompt.
    """

    def __init__(
        self,
        reponse: str | Callable[[str], str] = '{"constats": []}',
        *,
        panne: bool = False,
        usage: StepUsage = USAGE_BILAN,
    ) -> None:
        self.reponse = reponse
        self.panne = panne
        self.usage = usage
        self.prompts: list[str] = []

    async def juger(self, *, agent: Any, prompt: str) -> tuple[str, StepUsage]:
        self.prompts.append(prompt)
        # Un tour de boucle rendu : deux demandeurs du même bilan se croisent ici.
        await asyncio.sleep(0)
        if self.panne:
            raise RuntimeError("fournisseur injoignable")
        texte = self.reponse(prompt) if callable(self.reponse) else self.reponse
        return texte, self.usage


def _reponse(*constats: Mapping[str, Any]) -> str:
    return json.dumps({"constats": list(constats)}, ensure_ascii=False)


def _verdict_p3(dossier) -> str:
    """Le bilan qu'un juge rendrait de `p3` : l'échec déterministe, et quoi changer."""
    relances = _ids(dossier, FAMILLE_RELANCE, MAQUETTE)
    echec = [
        p.id for p in dossier.pieces
        if p.famille == FAMILLE_STATUT and p.tache_id == MAQUETTE and "statut echec" in p.texte
    ]
    return _reponse(
        {
            "rubrique": RUBRIQUE_ECHEC,
            "texte": "La maquette est tombée trois fois sur la même cause : un message du "
            "flux trop gros, la capture relue à chaque tentative.",
            "pieces": [*relances, *echec],
            "nature": NATURE_DETERMINISTE,
            "tache": MAQUETTE,
        },
        {
            "rubrique": RUBRIQUE_RECOMMANDATION,
            "texte": "Ne pas relancer tel quel : réduire la capture relue avant de reprendre.",
            "pieces": relances,
            "agent": "interface",
        },
    )


class _ServiceChat:
    """Le strict nécessaire du fil pour le conteur — un dépôt en mémoire."""

    def __init__(self, fil: list[MessageChat]) -> None:
        self._fil = fil
        self.ecrits: list[str] = []

    def conversation_du_run(self, agent: str, run_id: str) -> str | None:
        return "origine"

    def fil(self, agent: str, conversation: str) -> list[MessageChat]:
        return list(self._fil)

    async def raconter_la_fin(self, agent: Any, *, contenu: str, run_id: str,
                              conversation: str, suite: Any) -> MessageChat:
        self.ecrits.append(contenu)
        return MessageChat(agent=NOM_ORCHESTRATION, conversation=conversation,
                           auteur=NOM_ORCHESTRATION, contenu=contenu, run_id=run_id)


class _Redacteur:
    def __init__(self) -> None:
        self.contextes: list[str] = []

    async def rediger(self, *, agent: Any, contexte: str) -> str:
        self.contextes.append(contexte)
        return "Voilà ce que le run a laissé."


def _lancement() -> list[MessageChat]:
    return [
        MessageChat(agent=NOM_ORCHESTRATION, conversation="origine", auteur=UTILISATEUR,
                    contenu="Fais-moi un site vitrine."),
        MessageChat(agent=NOM_ORCHESTRATION, conversation="origine",
                    auteur=NOM_ORCHESTRATION, contenu="C'est parti.", run_id=RUN),
    ]


# --------------------------------------------------------------------------
# ① Les pièces
# --------------------------------------------------------------------------


def test_le_journal_du_run_se_lit_en_entier_au_dela_d_une_page() -> None:
    """Au-delà des 200 entrées qu'une page sert, la pièce la plus ancienne est encore là.

    `p3` comptait 183 entrées ; un run à peine plus long passe la page du journal
    requêtable (`TAILLE_PAGE_MAX`). Le bilan ne lit pas une page : il lit le journal
    du run **en entier**, et la relance la plus ancienne — la première pièce qui
    compte — y figure encore.
    """
    rejeu = rejouer_p3(total=TAILLE_PAGE_MAX + 60)

    dossier = _dossier(rejeu)

    assert dossier.entrees_lues == TAILLE_PAGE_MAX + 60
    premiere_relance = rejeu.journal.entrees_du_run(RUN)
    relance = next(e for e in premiere_relance if e.statut == "relance")
    assert any(relance.id in piece.entrees for piece in dossier.pieces)
    assert f"lues pour ce run : {TAILLE_PAGE_MAX + 60}, en entier" in prompt_du_bilan(
        rejeu.state.execution(RUN), dossier
    )


def test_quand_le_budget_mord_le_bruit_part_d_abord_et_se_compte() -> None:
    """Un budget serré garde les pièces décisives, laisse l'activité, et le dit."""
    rejeu = rejouer_p3()

    dossier = _dossier(rejeu, pieces_max=20)

    familles = {piece.famille for piece in dossier.pieces}
    assert len(dossier.pieces) == 20
    assert FAMILLE_RELANCE in familles and FAMILLE_ACTE in familles
    assert FAMILLE_ACTIVITE not in familles
    assert dossier.laissees[FAMILLE_ACTIVITE] > 100
    prompt = prompt_du_bilan(rejeu.state.execution(RUN), dossier)
    assert f"Pièces laissées de côté faute de place : {dossier.nb_laissees}" in prompt


def test_sans_contrainte_de_budget_rien_n_est_laisse() -> None:
    """L'échantillon inverse : un budget large n'écarte rien, et le prompt ne dit rien."""
    rejeu = rejouer_p3(total=60)

    dossier = _dossier(rejeu, pieces_max=500)

    assert dossier.nb_laissees == 0
    assert "laissées de côté" not in prompt_du_bilan(rejeu.state.execution(RUN), dossier)


def test_chaque_piece_garde_les_entrees_du_journal_dont_elle_vient() -> None:
    """Le pont vers `GET /api/journal` : une pièce d'entrée cite l'identifiant servi là-bas."""
    rejeu = rejouer_p3()
    servis = {entree.id for entree in rejeu.journal.entrees_du_run(RUN)}

    dossier = _dossier(rejeu)

    assert all(piece.entrees for piece in dossier.pieces if piece.famille != FAMILLE_USAGE)
    assert all(set(piece.entrees) <= servis for piece in dossier.pieces)
    assert [p.id for p in dossier.pieces] == [f"P{n}" for n in range(1, len(dossier.pieces) + 1)]


def test_les_familles_du_ticket_sont_toutes_reconnues() -> None:
    """Statuts, relances, actes, usage, échanges, checklists, décisions — lus sur leurs codes.

    Le classement lit le **type** d'une entrée et le **statut** que le moteur a posé,
    jamais son texte : chaque famille est éprouvée ici sur la forme exacte que le
    pont produit.
    """
    rejeu = rejouer_p3(total=60)
    rejeu.ligne(f"{TEXTES}:refus-outil", nom="Outil refusé — Rédiger", agent="redacteur",
                role="Rédaction", statut=STATUT_REFUS_OUTIL, entree="WebFetch",
                sortie="WebFetch refusé par la politique de l'agent.")
    rejeu.ligne(f"{MAQUETTE}:rattrapage", nom="Rattrapage — Maquetter", agent=ACTEUR_RUN,
                role="Orchestrateur", statut=statut_du_geste("reprendre"),
                sortie="approche : la capture est relue entière — la reprendre en réduisant.")
    rejeu.ligne(f"{TEXTES}:question", nom="Question — Rédiger", agent="redacteur",
                role="Rédaction", statut=STATUT_QUESTION_SANS_REPONSE,
                sortie="sans réponse : ton enjoué retenu.")
    rejeu.ligne(f"{TEXTES}:decision", nom="Décision — Rédiger", agent="redacteur",
                role="Rédaction", statut="decision_autonome", sortie="Trois sections.",
                description="Le brief en nomme trois.")
    rejeu.ligne(f"{TEXTES}:verification", nom="Vérification — Rédiger", agent="redacteur",
                role="Rédaction", statut=STATUT_VERIFICATION_NON_TENUE,
                sortie="1/2 critère(s) tenu(s)",
                verification={"statut": STATUT_VERIFICATION_NON_TENUE,
                              "resume": "1/2 critère(s) tenu(s)", "constats": []})
    rejeu.ligne(f"{TEXTES}:detail", nom="Rédiger les textes", agent="redacteur",
                role="Rédaction", statut=STATUT_TERMINEE,
                etapes=[{"libelle": "Accueil", "etat": "faite"},
                        {"libelle": "Mentions légales", "etat": "a_faire"}])
    rejeu.evenement(Event(type="validation.demande", run_id=RUN, tache_id=TEXTES,
                          agent="redacteur", statut="en_attente",
                          detail="écrire hors du projet ?", projet_id=PROJET))

    dossier = _dossier(rejeu, pieces_max=500)

    def famille_de(fragment: str) -> set[str]:
        return {p.famille for p in dossier.pieces if fragment in p.texte}

    assert famille_de("WebFetch refusé") == {FAMILLE_ACTE}
    assert famille_de("msedge --headless") == {FAMILLE_ACTE}
    assert famille_de("écrit dans le projet") == {FAMILLE_ACTE}
    assert famille_de("la reprendre en réduisant") == {FAMILLE_RELANCE}
    assert famille_de("ton enjoué retenu") == {FAMILLE_DECISION}
    assert famille_de("Trois sections.") == {FAMILLE_DECISION}
    assert famille_de("hors du projet ?") == {FAMILLE_ECHANGE}
    assert FAMILLE_CHECKLIST in famille_de("1/2 critère(s) tenu(s)")
    checklist = next(p for p in dossier.pieces if p.famille == FAMILLE_CHECKLIST
                     and p.tache_id == TEXTES and p.texte.startswith("Checklist"))
    assert "1/2 étape(s) cochée(s)" in checklist.texte
    assert "non cochées : Mentions légales" in checklist.texte


def test_l_usage_par_tache_dit_les_tokens_sans_prix_et_le_cout_partiel() -> None:
    """Le troisième point du ticket : l'usage et le coût par tâche, drapeau partiel compris."""
    rejeu = rejouer_p3()

    dossier = _dossier(rejeu)

    maquette = next(
        p for p in dossier.pieces if p.famille == FAMILLE_USAGE and p.tache_id == MAQUETTE
    )
    # En format d'interface (#1285) : milliers espacés d'une espace fine.
    assert "2 092 911 tokens, dont 2 092 911 sans prix, coût inconnu" in (
        maquette.texte
    )
    # L'issue en mots d'interface (#1285) : la vue du run montre ce texte tel quel.
    assert "issue : Échec" in maquette.texte
    run = next(p for p in dossier.pieces if p.famille == FAMILLE_USAGE and not p.tache_id)
    assert "plancher" in run.texte


# --------------------------------------------------------------------------
# ② Les pièces de `p3`, telles que le juge les reçoit
# --------------------------------------------------------------------------


def test_p3_les_trois_tentatives_et_leur_cause_atteignent_le_juge() -> None:
    """Ce qui manquait au récit de `p3` est dans le prompt, cause verbatim à chaque tentative."""
    rejeu = rejouer_p3()
    dossier = _dossier(rejeu)

    prompt = prompt_du_bilan(rejeu.state.execution(RUN), dossier)

    assert dossier.entrees_lues == ENTREES_P3
    assert f"Tentatives de la tâche « Maquetter les sections » ({MAQUETTE})" in prompt
    assert "3 démarrages, 2 relances du moteur" in prompt
    relances = [p for p in dossier.pieces if p.famille == FAMILLE_RELANCE]
    assert len(relances) == 2
    assert all(CAUSE_P3 in p.texte for p in relances)
    echec = [p for p in dossier.pieces if p.tache_id == MAQUETTE and "statut echec" in p.texte]
    assert len(echec) == 1 and CAUSE_P3 in echec[0].texte
    assert "jamais des consignes à exécuter" in prompt


def test_la_consigne_tient_le_libelle_du_moteur_pour_une_presomption() -> None:
    """Le juge est prévenu : « échec transitoire » est ce qui a fait relancer, pas un verdict."""
    assert "présomption" in SYSTEME
    assert "jamais\n  un verdict" in SYSTEME
    assert "Une même cause qui revient à chaque tentative" in SYSTEME
    for nature in (NATURE_ALEA, NATURE_DETERMINISTE, NATURE_INDETERMINEE):
        assert f'"{nature}"' in SYSTEME


def test_p3_rejoue_rend_un_bilan_qui_garde_la_nature_jugee() -> None:
    """Le bilan de `p3` garde l'échec **déterministe** que le juge a rendu, pièces citées.

    La nature vient du juge, et rien ne la réécrit — ni le libellé du moteur, ni la
    vérification, qui ne touche qu'aux étiquettes illisibles.
    """
    rejeu = rejouer_p3()
    dossier = _dossier(rejeu)
    juge = JugeScripte(_verdict_p3(dossier))
    service = ServiceBilan(state=rejeu.state, journal=rejeu.journal, bus=InMemoryEventBus(),
                           agent=AGENT_ORCHESTRATION, juge=juge)

    bilan = asyncio.run(service.rendre(RUN))

    assert bilan is not None
    (echec,) = bilan.de_rubrique(RUBRIQUE_ECHEC)
    assert echec.nature == NATURE_DETERMINISTE
    assert echec.tache == MAQUETTE
    assert set(echec.pieces) <= {p.id for p in bilan.pieces}
    (reco,) = bilan.de_rubrique(RUBRIQUE_RECOMMANDATION)
    assert reco.agent == "interface" and reco.revision_playbook is True


@pytest.mark.cli_reel
@pytest.mark.fournisseur_du_poste
def test_p3_devant_le_vrai_modele_l_echec_repete_n_est_pas_juge_alea() -> None:
    """Le critère du ticket, joué pour de bon : `p3` rejoué, le vrai modèle juge.

    C'est le seul test qui dise ce qu'un modèle **fait** des pièces — les autres
    disent ce qu'il reçoit. La nature se lit dans le champ structuré du constat,
    jamais dans son texte (#746).
    """
    rejeu = rejouer_p3()
    service = ServiceBilan(state=rejeu.state, journal=rejeu.journal, bus=InMemoryEventBus(),
                           agent=AGENT_ORCHESTRATION, juge=JugeModele())

    bilan = asyncio.run(service.rendre(RUN))

    assert bilan is not None, "le vrai modèle n'a rendu aucun bilan lisible"
    echecs = [c for c in bilan.de_rubrique(RUBRIQUE_ECHEC) if c.tache in ("", MAQUETTE)]
    assert echecs, f"aucun constat sur l'échec de la maquette : {bilan.to_dict()}"
    assert all(c.nature != NATURE_ALEA for c in echecs), bilan.to_dict()


# --------------------------------------------------------------------------
# ③ La vérification
# --------------------------------------------------------------------------


def _execution_p3():
    rejeu = rejouer_p3(total=60)
    return rejeu, _dossier(rejeu), rejeu.state.execution(RUN)


def test_un_constat_qui_cite_ses_pieces_tient() -> None:
    """L'échantillon qui tient — sans lui, les écarts qui suivent ne prouveraient rien."""
    _, dossier, execution = _execution_p3()
    premiere = dossier.pieces[0].id

    gardes, ecartes = verifier(
        [{"rubrique": "livre", "texte": "Les textes sont écrits.", "pieces": [premiere]}],
        dossier,
        execution,
    )

    assert [c.texte for c in gardes] == ["Les textes sont écrits."]
    assert ecartes == ()


@pytest.mark.parametrize(
    ("brut", "raison"),
    [
        ({"rubrique": "echec", "texte": "La maquette a échoué.", "pieces": []},
         RAISON_AUCUNE_PIECE),
        ({"rubrique": "echec", "texte": "La maquette a échoué.", "pieces": ["P1", "P999"]},
         f"{RAISON_PIECE_INEXISTANTE} : P999"),
        ({"rubrique": "humeur", "texte": "Tout va bien.", "pieces": ["P1"]},
         RAISON_RUBRIQUE_INCONNUE),
    ],
    ids=["sans-piece", "piece-absente", "rubrique-inconnue"],
)
def test_un_constat_sans_piece_reelle_est_ecarte_avec_sa_raison(
    brut: dict[str, Any], raison: str
) -> None:
    """Une citation fabriquée — fût-ce une sur deux — ruine le constat entier."""
    _, dossier, execution = _execution_p3()

    gardes, ecartes = verifier([brut], dossier, execution)

    assert gardes == ()
    assert [e.raison for e in ecartes] == [raison]


def test_les_etiquettes_illisibles_se_normalisent_sans_toucher_au_texte() -> None:
    """Nature inconnue → indéterminée ; tâche hors du run → oubliée ; agent sans échec → rien."""
    _, dossier, execution = _execution_p3()

    gardes, _ = verifier(
        [
            {"rubrique": "echec", "texte": "La maquette a échoué.", "pieces": ["P1"],
             "nature": "cosmique", "tache": "tache-d-un-autre-run"},
            {"rubrique": "recommandation", "texte": "Revoir le rédacteur.", "pieces": ["P1"],
             "agent": "redacteur"},
        ],
        dossier,
        execution,
    )

    echec, reco = gardes
    assert (echec.nature, echec.tache, echec.texte) == (
        NATURE_INDETERMINEE, "", "La maquette a échoué."
    )
    # Le rédacteur n'a aucun échec dans ce run : l'analyse d'échecs n'aurait rien à lire.
    assert (reco.agent, reco.revision_playbook) == ("", False)


# --------------------------------------------------------------------------
# ④ Le service
# --------------------------------------------------------------------------


class _BusEspion(InMemoryEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.publies: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.publies.append(event)
        await super().publish(event)


def _service(rejeu: Rejeu, juge: JugeScripte | None, bus: _BusEspion | None = None):
    bus = bus or _BusEspion()
    return ServiceBilan(state=rejeu.state, journal=rejeu.journal, bus=bus,
                        agent=AGENT_ORCHESTRATION, juge=juge), bus


def test_le_bilan_publie_porte_son_cout_compte_au_run_hors_du_temps_de_mur() -> None:
    """Une activité de run porte le bilan **et** son coût ; le grand livre le compte à part."""
    rejeu = rejouer_p3(total=60)
    avant = rejeu.state.execution(RUN)
    duree_avant, cout_avant = avant.cout.duree_mur_ms, avant.cout_usd
    juge = JugeScripte(_verdict_p3(_dossier(rejeu)))
    service, bus = _service(rejeu, juge)

    asyncio.run(service.rendre(RUN))

    (event,) = bus.publies
    assert (event.type, event.agent, event.etape_run, event.statut) == (
        EVENEMENT_AGENT_ACTIVITE, ACTEUR_RUN, ETAPE_BILAN, STATUT_BILAN_RENDU
    )
    rejeu.evenement(event)  # ce que la pompe fera
    execution = rejeu.state.execution(RUN)
    assert execution.bilan is not None
    assert BilanRun.depuis(execution.bilan).de_rubrique(RUBRIQUE_ECHEC)
    assert execution.cout.bilan.cout_usd == pytest.approx(USAGE_BILAN.cout_usd)
    assert execution.cout_usd == pytest.approx(cout_avant + USAGE_BILAN.cout_usd)
    assert execution.cout.total.cout_usd == pytest.approx(execution.cout_usd)
    assert execution.cout.duree_mur_ms == duree_avant


def test_un_bilan_par_issue_et_un_seul_appel_meme_demande_deux_fois() -> None:
    """La pompe et le récit le demandent ensemble ; un terminal reçu deux fois ne refait rien."""
    rejeu = rejouer_p3(total=60)
    juge = JugeScripte(_verdict_p3(_dossier(rejeu)))
    service, bus = _service(rejeu, juge)

    async def deux_demandeurs():
        return await asyncio.gather(service.rendre(RUN), service.rendre(RUN))

    premier, second = asyncio.run(deux_demandeurs())
    rejeu.evenement(bus.publies[0])
    troisieme = asyncio.run(service.rendre(RUN))

    assert len(juge.prompts) == 1
    assert premier is second
    assert troisieme is not None and troisieme.to_dict() == premier.to_dict()
    assert len(bus.publies) == 1


def test_un_modele_muet_ne_fabrique_aucun_bilan() -> None:
    """Rien n'est publié, rien n'est inventé : le récit se rédigera sans bilan."""
    rejeu = rejouer_p3(total=60)
    service, bus = _service(rejeu, JugeScripte(panne=True))

    assert asyncio.run(service.rendre(RUN)) is None
    assert bus.publies == []


def test_une_reponse_illisible_ne_retient_rien_mais_son_cout_est_compte() -> None:
    """L'appel a coûté : la ligne le dit, sans bilan — et le run porte ce coût."""
    rejeu = rejouer_p3(total=60)
    service, bus = _service(rejeu, JugeScripte("je ne sais pas faire de JSON"))

    assert asyncio.run(service.rendre(RUN)) is None
    (event,) = bus.publies
    assert (event.statut, event.bilan) == (STATUT_BILAN_ILLISIBLE, None)
    assert event.usage == USAGE_BILAN


def test_un_run_en_vol_n_a_pas_de_bilan() -> None:
    rejeu = Rejeu()
    rejeu.execution(EXECUTION_EN_COURS, "Lancement")
    juge = JugeScripte()
    service, bus = _service(rejeu, juge)

    assert asyncio.run(service.rendre(RUN)) is None
    assert juge.prompts == [] and bus.publies == []


def test_sans_juge_aucun_bilan_n_est_rendu() -> None:
    """Le défaut de la suite (`tests/conftest.py`) : aucun appel, aucun événement."""
    rejeu = rejouer_p3(total=60)
    service, bus = _service(rejeu, None)

    assert asyncio.run(service.rendre(RUN)) is None
    assert bus.publies == []


# --------------------------------------------------------------------------
# ⑤ L'API, et le journal durable
# --------------------------------------------------------------------------


def _attendre(condition: Callable[[], Any]) -> Any:
    """Relit jusqu'à ce que `condition` rende quelque chose — sans dormir « pour voir »."""
    for _ in range(400):
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(0.01)
    return None


def _publier_p3(client: TestClient, bus: InMemoryEventBus) -> None:
    """Les pièces de `p3`, publiées sur le bus comme un hôte les publie."""
    rejeu = rejouer_p3(total=60)
    for execution in rejeu.state.executions():
        for event in execution.evenements:
            client.portal.call(bus.publish, event)


def test_le_bilan_est_servi_puis_relu_du_journal_durable_apres_un_redemarrage(
    tmp_path: Path,
) -> None:
    """Rendu à la fin, servi par l'API, gardé au journal : un redémarrage le relit sans modèle."""
    journal = InMemoryEventLog()
    bus = InMemoryEventBus()
    dossier = _dossier(rejouer_p3(total=60))
    juge = JugeScripte(_verdict_p3(dossier))
    with TestClient(
        create_app(bus=bus, event_log=journal, chat_store=ChatStore(tmp_path / "chat"),
                   bilan_juge=juge)
    ) as client:
        assert client.get("/api/executions/inconnu/bilan").status_code == 404
        _publier_p3(client, bus)
        servi = _attendre(lambda: client.get(f"/api/executions/{RUN}/bilan").json()["bilan"])
        detail = client.get(f"/api/executions/{RUN}").json()

    assert servi is not None, "aucun bilan servi après la fin du run"
    assert [c["nature"] for c in servi["constats"] if c["rubrique"] == RUBRIQUE_ECHEC] == [
        NATURE_DETERMINISTE
    ]
    assert servi["entrees_lues"] == 60
    assert all(p["entrees"] for p in servi["pieces"])
    assert detail["bilan"] == servi
    assert detail["cout"]["bilan"]["cout_usd"] == pytest.approx(USAGE_BILAN.cout_usd)

    muet = JugeScripte(panne=True)
    with TestClient(
        create_app(bus=InMemoryEventBus(), event_log=journal,
                   chat_store=ChatStore(tmp_path / "chat"), bilan_juge=muet)
    ) as client:
        relu = client.get(f"/api/executions/{RUN}/bilan").json()["bilan"]

    assert relu == servi
    assert muet.prompts == []


def test_un_run_sans_bilan_se_sert_avec_un_bilan_nul(tmp_path: Path) -> None:
    """Le run existe, son bilan pas encore : 200 et `null`, jamais une erreur."""
    bus = InMemoryEventBus()
    with TestClient(create_app(bus=bus, chat_store=ChatStore(tmp_path / "chat"))) as client:
        client.portal.call(bus.publish, Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN,
                                              statut=EXECUTION_EN_COURS))
        _attendre(lambda: client.get(f"/api/executions/{RUN}").status_code == 200)
        reponse = client.get(f"/api/executions/{RUN}/bilan")

    assert reponse.status_code == 200
    # L'état dit pourquoi il n'y en a pas (#1285) : un run en vol attend le sien.
    assert reponse.json() == {
        "run_id": RUN, "bilan": None, "etat": "attendu", "raison": "", "taches": {}
    }


# --------------------------------------------------------------------------
# ⑥ Le récit de fin lit le bilan
# --------------------------------------------------------------------------


def _bilan_p3() -> tuple[Rejeu, BilanRun]:
    rejeu = rejouer_p3(total=60)
    juge = JugeScripte(_verdict_p3(_dossier(rejeu)))
    service, _ = _service(rejeu, juge)
    bilan = asyncio.run(service.rendre(RUN))
    assert bilan is not None
    return rejeu, bilan


def test_le_contexte_du_recit_porte_le_bilan_juste_apres_la_fiche() -> None:
    """L'échec, sa nature en mots, ce qu'il faut changer — entre la fiche et le livrable."""
    rejeu, bilan = _bilan_p3()

    contexte = contexte_du_recit(rejeu.state, rejeu.state.execution(RUN), None, bilan)

    fiche = contexte.index("## Le run qui vient de finir")
    section = contexte.index("## Le bilan du run, sur pièces")
    livrable = contexte.index("## Le livrable")
    assert fiche < section < livrable
    bloc = contexte[section:livrable]
    assert "cause déterministe — se reproduirait à l'identique" in bloc
    assert f"tâche {MAQUETTE}" in bloc
    assert "réduire la capture relue avant de reprendre" in bloc
    assert "c'est ce bilan qui fait foi" in bloc


def test_sans_bilan_le_contexte_du_recit_est_celui_d_avant() -> None:
    """L'échantillon inverse : aucun bloc de bilan quand il n'y en a pas."""
    rejeu = rejouer_p3(total=60)

    contexte = contexte_du_recit(rejeu.state, rejeu.state.execution(RUN), None)

    assert "## Le bilan du run" not in contexte


def test_la_consigne_du_recit_interdit_de_relancer_un_echec_deterministe_tel_quel() -> None:
    """Ce que le prompt du récit contient — la règle, pas un espoir sur ce qu'il écrira."""
    assert "bilan du run" in SYSTEME_RECIT
    assert "ne conseille jamais de relancer tel quel" in SYSTEME_RECIT
    assert "ce qu'il faut changer d'abord" in SYSTEME_RECIT
    assert "présume un aléa" in SYSTEME_RECIT


def test_le_conteur_attend_le_bilan_et_le_donne_au_redacteur() -> None:
    """Le récit de `p3`, écrit avec son bilan sous les yeux."""
    rejeu, bilan = _bilan_p3()

    class _Bilans:
        async def rendre(self, run_id: str) -> BilanRun | None:
            return bilan if run_id == RUN else None

    redacteur = _Redacteur()
    chat = _ServiceChat(_lancement())
    conteur = ConteurDeFin(chat=chat, state=rejeu.state, agent=AGENT_ORCHESTRATION,
                           projet=lambda projet_id: None, redacteur=redacteur,
                           bilan=_Bilans())

    assert asyncio.run(conteur.raconter(RUN)) is not None
    (contexte,) = redacteur.contextes
    assert "## Le bilan du run, sur pièces" in contexte
    assert bilan_en_texte(bilan) in contexte


def test_un_bilan_en_panne_ne_coute_pas_le_recit() -> None:
    """Le bilan qui lève : le récit s'écrit quand même, sur la fiche seule."""
    rejeu = rejouer_p3(total=60)

    class _BilansEnPanne:
        async def rendre(self, run_id: str) -> BilanRun | None:
            raise RuntimeError("journal illisible")

    redacteur = _Redacteur()
    conteur = ConteurDeFin(chat=_ServiceChat(_lancement()), state=rejeu.state,
                           agent=AGENT_ORCHESTRATION, projet=lambda projet_id: None,
                           redacteur=redacteur, bilan=_BilansEnPanne())

    assert asyncio.run(conteur.raconter(RUN)) is not None
    assert "## Le bilan du run" not in redacteur.contextes[0]


def test_bout_en_bout_le_recit_lit_le_bilan_d_un_seul_appel(tmp_path: Path) -> None:
    """Par l'app : la fin de `p3` fait rendre le bilan une fois, et le récit le lit."""
    depot = ChatStore(tmp_path / "chat")
    for message in _lancement():
        depot.ajouter(message)
    bus = InMemoryEventBus()
    juge = JugeScripte(_verdict_p3(_dossier(rejouer_p3(total=60))))
    redacteur = _Redacteur()
    with TestClient(
        create_app(bus=bus, chat_store=depot, recit_redacteur=redacteur, bilan_juge=juge)
    ) as client:
        _publier_p3(client, bus)
        _attendre(lambda: len(depot.fil(NOM_ORCHESTRATION, "origine")) > 2)

    assert len(juge.prompts) == 1
    (contexte,) = redacteur.contextes
    assert "cause déterministe — se reproduirait à l'identique" in contexte


def test_le_bilan_se_relit_tel_qu_il_a_ete_garde() -> None:
    """L'aller-retour de la forme gardée — ce que le rejeu du journal relira."""
    _, bilan = _bilan_p3()

    relu = BilanRun.depuis(json.loads(json.dumps(bilan.to_dict())))

    assert relu == bilan
    assert isinstance(relu.constats[0], Constat)


# --------------------------------------------------------------------------
# ⑦ Ce que la vue du run en lit (#1285)
# --------------------------------------------------------------------------


def test_une_piece_dit_si_elle_resume_le_journal_ou_si_elle_en_est_une_ligne() -> None:
    """La vue rend une ligne du journal en clair, une synthèse par son texte (#1285).

    Le texte d'une pièce d'entrée est écrit pour le modèle (horodatage, type, statut
    brut) ; celui d'une synthèse — le coût du run, l'usage d'une tâche, ses tentatives,
    sa checklist — est déjà une phrase qu'aucune ligne du journal ne porte.
    """
    dossier = _dossier(rejouer_p3(total=60))

    syntheses = {p.id for p in dossier.pieces if p.synthese}
    entrees = [p for p in dossier.pieces if not p.synthese]
    assert _ids(dossier, FAMILLE_USAGE)
    assert set(_ids(dossier, FAMILLE_USAGE)) <= syntheses
    tentatives = [p for p in dossier.pieces if p.texte.startswith("Tentatives de la tâche")]
    assert tentatives and all(p.synthese for p in tentatives)
    assert entrees and all(len(p.entrees) == 1 for p in entrees)
    assert all(p.to_dict()["synthese"] == p.synthese for p in dossier.pieces)


def test_une_synthese_se_lit_en_mots_d_interface_sans_code_du_moteur() -> None:
    """La vue montre le texte d'une synthèse tel quel : ni « echec », ni le repère « — ».

    Relevé sur la vraie stack (#1285) : « Usage de la tâche … (—) : … ; issue :
    echec » — le code d'issue et le repère d'une tâche jamais routée, lus par une
    personne.
    """
    from maestro.controltower.bilan import (
        _agent_en_mots,
        _issue_en_mots,
        _montant,
        _usage_en_mots,
    )

    assert (_issue_en_mots("echec"), _issue_en_mots("terminee"), _issue_en_mots("")) == (
        "Échec", "Terminée", "aucune"
    )
    assert (_agent_en_mots("—"), _agent_en_mots(""), _agent_en_mots("dev")) == ("", "", ", dev")
    # Le format de l'écran (« 0,43 $US »), jamais le point décimal ni « tour(s) ».
    assert _montant(0.2199) == "0,2199 $US"
    usage = StepUsage(
        tokens_entree=40_000, tokens_sortie=720, cout_usd=0.2199, tours=2, duree_ms=31_000
    )
    assert _usage_en_mots(usage) =="40 720 tokens, coût 0,2199 $US, 2 tours, 31 s"
    assert "1 tour," in _usage_en_mots(StepUsage(tokens_entree=10, tours=1, duree_ms=1_000))
    dossier = _dossier(rejouer_p3(total=60))
    tentatives = next(p for p in dossier.pieces if p.texte.startswith("Tentatives de la tâche"))
    assert tentatives.texte.endswith("issue : Échec")
    # Le libellé, pour la personne : des phrases, la tâche par son titre, sans son
    # identifiant — le texte, lui, le garde pour que le modèle puisse le citer.
    assert tentatives.libelle == (
        "« Maquetter les sections » a démarré 3 fois ; le moteur l'a relancée 2 fois "
        "en présumant un aléa — issue : Échec."
    )
    assert MAQUETTE in tentatives.texte and MAQUETTE not in tentatives.libelle
    cout = next(p for p in dossier.pieces if p.famille == FAMILLE_USAGE and not p.tache_id)
    assert cout.libelle.startswith("Le run a consommé ")
    assert ";" not in cout.libelle.split(".")[0]
    assert all(p.libelle for p in dossier.pieces if p.synthese)
    assert not any(p.libelle for p in dossier.pieces if not p.synthese)


def test_la_consigne_fait_nommer_une_tache_par_son_titre() -> None:
    """Un constat s'affiche à la personne : la tâche par son titre, l'identifiant dans « tache ».

    Relevé sur la vraie stack (#1285) : « La tâche rediger-notes-md a été arrêtée… ».
    Jugé sur ce que la consigne dit, jamais sur ce qu'un modèle en a fait (#746).
    """
    assert "par son titre" in SYSTEME
    assert "jamais par son" in SYSTEME and "identifiant" in SYSTEME


def test_la_consigne_laisse_se_taire_une_rubrique_sans_rien_a_dire() -> None:
    """Une rubrique vide ne s'affiche pas (parti pris 1 de la veille) : pas de constat « aucun ».

    Relevé sur la vraie stack (#1285) : « Aucune consommation sans résultat : l'unique tâche
    a abouti… » occupait une carte d'un run sans défaut. Rien ne le filtre au texte (#746) —
    la consigne le dit au modèle.
    """
    assert "rien à dire ne reçoit aucun constat" in SYSTEME


def test_la_ligne_du_journal_s_accorde() -> None:
    """« 1 constat », « 2 écartés » : le journal du run montre cette ligne telle quelle."""
    from maestro.controltower.bilan import ConstatEcarte

    un = Constat(rubrique=RUBRIQUE_ECHEC, texte="La maquette a failli.", pieces=("P1",))
    ecarte = ConstatEcarte(rubrique=RUBRIQUE_ECHEC, texte="?", pieces=(), raison="aucune")
    bilan = BilanRun(run_id=RUN, statut=EXECUTION_ECHEC, fin=None, constats=(un,),
                     ecartes=(ecarte, ecarte))

    assert bilan.resume() == (
        "1 constat sur pièces, dont 1 sur ce qui a failli ; 2 écartés faute de pièce."
    )


def test_une_piece_gardee_avant_ce_lot_se_relit_sans_perdre_sa_nature() -> None:
    """Un bilan rendu avant #1285 ne dit pas `synthese` : la relecture le retrouve."""
    from maestro.controltower.bilan import Piece

    usage = Piece.depuis({"id": "P1", "famille": FAMILLE_USAGE, "texte": "Coût du run",
                          "entrees": ["j-0056"]})
    tentatives = Piece.depuis({"id": "P2", "famille": FAMILLE_STATUT, "texte": "Tentatives",
                               "entrees": ["j-0003", "j-0009"]})
    ligne = Piece.depuis({"id": "P3", "famille": FAMILLE_STATUT, "texte": "statut echec",
                          "entrees": ["j-0055"]})

    assert (usage.synthese, tentatives.synthese, ligne.synthese) == (True, True, False)
    assert Piece.depuis({**ligne.to_dict(), "synthese": True}).synthese is True


class _JugeRetenu(JugeScripte):
    """Un juge qui ne répond qu'au signal : ce qui laisse voir le bilan « en rédaction »."""

    def __init__(self, reponse: str | Callable[[str], str]) -> None:
        super().__init__(reponse)
        self.signal = asyncio.Event()

    async def juger(self, *, agent: Any, prompt: str) -> tuple[str, StepUsage]:
        await self.signal.wait()
        return await super().juger(agent=agent, prompt=prompt)


def test_l_etat_du_bilan_suit_le_run_de_son_vol_a_son_rendu() -> None:
    """En vol → attendu ; soldé → en rédaction **dès la fin** ; puis rendu.

    `lancer` est synchrone : aucun tour de boucle ne sépare la fin du run de l'état
    « en rédaction ».
    """
    en_vol = Rejeu()
    en_vol.execution(EXECUTION_EN_COURS, "Lancement")
    service_en_vol, _ = _service(en_vol, JugeScripte())
    assert service_en_vol.etat(RUN) == ("attendu", "")

    rejeu = rejouer_p3(total=60)
    juge = _JugeRetenu(_verdict_p3(_dossier(rejeu)))
    service, bus = _service(rejeu, juge)

    async def scenario() -> list[tuple[str, str]]:
        # `lancer` est synchrone : c'est lui que la fin d'un run appelle, et l'écran
        # qui relit juste après doit lire « en rédaction », jamais « absent ».
        tache = service.lancer(RUN)
        vus = [service.etat(RUN)]
        juge.signal.set()
        assert isinstance(tache, asyncio.Future)
        await tache
        vus.append(service.etat(RUN))
        return vus

    assert asyncio.run(scenario()) == [("en_redaction", ""), ("rendu", "")]
    rejeu.evenement(bus.publies[0])
    assert service.etat(RUN) == ("rendu", "")


def test_un_bilan_absent_dit_pourquoi_quand_il_le_sait() -> None:
    """Modèle muet, réponse illisible, ou rien de connu (run soldé avant ce lot, sans juge)."""
    muet_rejeu = rejouer_p3(total=60)
    muet, _ = _service(muet_rejeu, JugeScripte(panne=True))
    asyncio.run(muet.rendre(RUN))
    assert muet.etat(RUN) == ("absent", "modele_muet")

    illisible_rejeu = rejouer_p3(total=60)
    illisible, bus = _service(illisible_rejeu, JugeScripte("pas du JSON"))
    asyncio.run(illisible.rendre(RUN))
    assert illisible.etat(RUN) == ("absent", "reponse_illisible")
    # Après un redémarrage, la trace de l'appel illisible est au journal : la raison survit.
    illisible_rejeu.evenement(bus.publies[0])
    relu, _ = _service(illisible_rejeu, JugeScripte())
    assert relu.etat(RUN) == ("absent", "reponse_illisible")

    sans_juge, _ = _service(rejouer_p3(total=60), None)
    assert sans_juge.etat(RUN) == ("absent", "")
    inconnu, _ = _service(Rejeu(), JugeScripte())
    assert inconnu.etat(RUN) == ("absent", "")


def test_l_api_sert_l_etat_du_bilan_avec_le_bilan(tmp_path: Path) -> None:
    """`GET …/bilan` dit l'état et sa raison, bilan compris — ce que la vue affiche."""
    bus = InMemoryEventBus()
    juge = JugeScripte(_verdict_p3(_dossier(rejouer_p3(total=60))))
    with TestClient(
        create_app(bus=bus, chat_store=ChatStore(tmp_path / "chat"), bilan_juge=juge)
    ) as client:
        _publier_p3(client, bus)
        servi = _attendre(
            lambda: (r := client.get(f"/api/executions/{RUN}/bilan").json())["bilan"] and r
        )

    assert servi is not None
    assert (servi["etat"], servi["raison"]) == ("rendu", "")
    assert all("synthese" in piece for piece in servi["bilan"]["pieces"])
    # Les tâches nommées par leur titre, servies avec le bilan.
    assert servi["taches"][MAQUETTE] == "Maquetter les sections"


def test_une_tache_se_nomme_par_le_titre_que_son_run_lui_a_donne() -> None:
    """Deux runs qui partagent un identifiant de tâche ne se prêtent pas leur titre.

    Un identifiant se réemploie d'un run à l'autre (`rediger-notes-md`, vu sur le banc) :
    la projection n'en tient qu'une carte, au titre du dernier run qui l'a portée. Le plan,
    lui, est celui de chaque run — c'est lui qui nomme, comme dans le graphe ; la carte ne
    nomme que ce que le plan n'annonçait pas.
    """
    premier, second, notes, hors_plan = "run-a", "run-b", "rediger-notes-md", "relire"
    state = ControlTowerState()

    def tache(run_id: str, tache_id: str, titre: str) -> Event:
        return Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=run_id,
            tache_id=tache_id,
            titre=titre,
            agent="dev",
            statut=STATUT_TERMINEE,
            projet_id=PROJET,
        )

    for run_id, titre in ((premier, "Rédiger NOTES.md"), (second, "Créer NOTES.md")):
        state.appliquer(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=run_id,
                statut=EXECUTION_EN_COURS,
                titre="Objectif",
                projet_id=PROJET,
            )
        )
        state.appliquer(
            Event(
                type=EVENEMENT_RUN_PLAN,
                run_id=run_id,
                plan=[NoeudPlan(id=notes, titre=titre)],
                projet_id=PROJET,
            )
        )
        state.appliquer(tache(run_id, notes, titre))
    state.appliquer(tache(premier, hors_plan, "Relire les notes"))

    assert state.titres_du_run(premier) == {
        notes: "Rédiger NOTES.md",
        hors_plan: "Relire les notes",
    }
    assert state.titres_du_run(second) == {notes: "Créer NOTES.md"}
