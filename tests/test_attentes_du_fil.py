"""Tests du **règlement des attentes depuis le fil** — répondre, trancher (#1183).

Un run qui attend une personne l'attendait sur un autre écran : la question d'un agent
(#1023), la validation d'une action sensible (#48). « Réponds-lui : prends Postgres »,
« oui, valide », « refuse et archive plutôt » n'avaient aucun effet dans le fil. Il les
règle désormais, par le **même** service que les deux écrans (`ServiceAttentes`), et
chaque règlement est **proposé** sur une carte puis **confirmé** par la personne.

Couvre les deux critères du ticket :

① **la question d'un agent** — le fil la nomme (le bloc des attentes porte de quoi la
   désigner), la réponse dite devient une carte qui ne part pas seule, et la réponse
   confirmée **parvient à l'agent qui l'attend** : joué contre le vrai moteur, suspendu
   sur le vrai canal des questions (`ArbitreQuestionControlTower`), dont la tâche
   reprend et aboutit avec la réponse lue telle quelle ;
② **la validation d'une action sensible** — tranchée par une carte confirmée, jusqu'au
   validateur que le moteur attend (`ValidateurControlTower`) ; un refus y porte sa
   raison, **la même** que celle d'un refus motivé depuis l'écran des validations, dans
   l'événement même que le moteur reçoit (celui que #1185 portera jusqu'à l'agent).

Et ce que les routes y gagnent, parce que c'est la condition du « sans doublon » : les
règles vivent **une fois** (`ServiceAttentes`), pour l'écran et pour le fil — leurs
codes (`404`, `409`, `422`) n'ont pas bougé.

Aucun réseau, aucun modèle : le fournisseur est un double qui sépare le juge, la
rédaction et le tour de lecture par leur prompt système. La qualité du jugement — « lui »
désigne-t-il le bon agent ? — n'est pas posée ici : on tient que ce que le modèle
désigne atteint le service par la carte, et rien d'autre.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.attentes import ServiceAttentes, suite_de_la_decision
from maestro.controltower.chat import (
    ChatStore,
    MessageChat,
    ReponseChat,
    reglement_en_attente,
    transcription,
)
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
    InMemoryEventBus,
)
from maestro.controltower.orchestration import (
    _MARQUEUR_VERDICT,
    _PROMPT_CONSULTATION,
    _PROMPT_ORCHESTRATION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ATTENTE,
    RepondeurOrchestration,
    attentes_de,
)
from maestro.controltower.question import ArbitreQuestionControlTower, evenement_question
from maestro.controltower.reglements import (
    GENRE_QUESTION,
    GENRE_VALIDATION,
    REGLEMENT_APPROBATION,
    REGLEMENT_REFUS,
    REGLEMENT_REPONSE,
    AttenteVisee,
    ReglementFait,
    ReglementPropose,
)
from maestro.controltower.state import (
    EXECUTION_TERMINEE,
    QUESTION_EN_ATTENTE,
    QUESTION_REPONDUE,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
    EtatValidation,
)
from maestro.controltower.validation import ValidateurControlTower, evenement_demande
from maestro.engine import OrchestrationEngine
from maestro.engine.guardrails import DemandeValidation
from maestro.engine.questions import DemandeQuestion
from maestro.orchestrator import Orchestrator
from maestro.projets.application import DiffProjet
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

UTILISATEUR = "utilisateur"

QUESTION_ID = "schema:1183a"
QUESTION = "Postgres ou SQLite pour la démo ?"
HYPOTHESE = "je pars sur SQLite, plus simple à embarquer"
CHOIX = ("Postgres", "SQLite")
REPONSE = "Prends Postgres : la démo tourne déjà sur le compose."
RAISON = "archive plutôt les fichiers au lieu de les supprimer"

#: Ce que la rédaction écrit : une phrase qu'aucun gabarit du code ne contient, si bien
#: que `contenu == REDIGE` prouve que le modèle a parlé et que le code n'a rien ajouté.
REDIGE = "C'est transmis : il reprend avec votre réponse."


def _attente(
    action: str, *cibles: str, texte: str = "", reponse: str = "Je vous propose ceci."
) -> str:
    """La réponse du juge au contrat de #1222 : la prose, puis la ligne du verdict."""
    attente = {"action": action, "cibles": list(cibles), "texte": texte}
    charge = {"verdict": VERDICT_ATTENTE, "objectif": "", "attente": attente}
    return f"{reponse}\n{_MARQUEUR_VERDICT} {json.dumps(charge, ensure_ascii=False)}"


def _dit(nom: str, reponse: str) -> str:
    charge = json.dumps({"verdict": nom, "objectif": ""}, ensure_ascii=False)
    return f"{reponse}\n{_MARQUEUR_VERDICT} {charge}"


class Juge(ModelProvider):
    """Juge, rédacteur et lecteur à la fois — séparés par leur prompt système.

    Le juge rend le verdict qu'on lui pose et **note son prompt** — c'est là qu'on
    vérifie que le fil voit ce qui attend ; la rédaction rend `REDIGE` et note ses
    faits — c'est là qu'on vérifie ce que le modèle apprend de ce qui a repris.
    """

    name = "juge-des-attentes"

    def __init__(self, verdict: str = "") -> None:
        self.verdict = verdict or _dit("echange", "Bien noté.")
        self.jugements: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        if system_prompt == _PROMPT_ORCHESTRATION:
            self.jugements.append(prompt)
            return self.verdict
        if system_prompt == _PROMPT_CONSULTATION:
            return "RIEN"
        self.redactions.append(prompt)
        return REDIGE


def _fil(*contenus: str) -> list[MessageChat]:
    return [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR if rang % 2 == 0 else NOM_ORCHESTRATION,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


def _demande_question(**champs: Any) -> DemandeQuestion:
    defauts: dict[str, Any] = {
        "question_id": QUESTION_ID,
        "question": QUESTION,
        "hypothese": HYPOTHESE,
        "choix": CHOIX,
        "tache_id": "schema",
        "titre": "Rédiger le schéma de données",
        "agent": "dev",
        "role": "Développeur",
        "run_id": "run-1183",
        "projet_id": None,
        "attente_s": 600.0,
    }
    return DemandeQuestion(**{**defauts, **champs})


def _pose(state: ControlTowerState, **champs: Any) -> str:
    """Projette une question posée, comme le ferait le moteur, et rend son identifiant."""
    event = evenement_question(_demande_question(**champs))
    state.appliquer(event)
    return str(event.question_id)


def _demande_validation(**champs: Any) -> DemandeValidation:
    defauts: dict[str, Any] = {
        "task_id": "nettoyer",
        "titre": "Nettoyer le dossier de build",
        "description": "supprimer les artefacts du dossier build/",
        "agent": "dev",
        "role": "Développeur",
        "raison": "acte irréversible",
        "run_id": "run-1183",
        "outil": "Bash",
        "arguments": {"command": "rm -rf build"},
    }
    return DemandeValidation(**{**defauts, **champs})


def _soumet(state: ControlTowerState, **champs: Any) -> str:
    """Projette une demande de validation, comme le ferait le validateur, et rend sa tâche."""
    demande = _demande_validation(**champs)
    state.appliquer(evenement_demande(demande))
    return demande.task_id


def _repondeur(
    state: ControlTowerState, juge: Juge, bus: EventBus | None = None, **kw: Any
) -> RepondeurOrchestration:
    return RepondeurOrchestration(
        provider=juge,
        attentes=attentes_de(state),
        reglements=ServiceAttentes(state, bus or InMemoryEventBus(), **kw),
    )


def _carte(reponse: ReponseChat, message: str) -> list[MessageChat]:
    """Le fil tel qu'il est après la carte : la demande, puis la réponse qui porte la carte."""
    return [
        *_fil(message),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu=reponse.contenu,
            reglement=reponse.reglement,
        ),
    ]


async def _pomper(bus: EventBus, state: ControlTowerState) -> None:
    """La pompe de l'API : tout ce qui passe sur le bus rejoint la projection."""
    async for event in bus.subscribe():
        state.appliquer(event)


async def _quand(condition: Callable[[], bool], *, delai_s: float = 20.0) -> None:
    """Attend qu'une condition tienne — une barrière bornée, jamais une durée supposée."""
    boucle = asyncio.get_running_loop()
    limite = boucle.time() + delai_s
    while not condition():
        if boucle.time() > limite:
            raise AssertionError("la condition attendue n'est jamais venue")
        await asyncio.sleep(0.01)


# ── ① la question d'un agent ────────────────────────────────────────────────────


def test_le_fil_nomme_la_question_qui_attend_et_de_quoi_la_designer() -> None:
    """« Le développeur demande quelle base utiliser » : le juge a ce qu'il faut pour le dire.

    La question, qui la pose et sur quelle tâche — et, depuis #1183, son identifiant,
    sans lequel « réponds-lui » ne peut rien désigner.
    """
    state = ControlTowerState()
    _pose(state)
    juge = Juge()

    asyncio.run(
        _repondeur(state, juge).produire(AGENT_ORCHESTRATION, _fil("qu'est-ce qui m'attend ?"))
    )

    prompt = juge.jugements[-1]
    assert QUESTION in prompt
    assert f"identifiant : {QUESTION_ID}" in prompt
    assert "agent : dev" in prompt
    assert "Rédiger le schéma de données" in prompt
    assert HYPOTHESE in prompt
    # Et le cadre lui dit qu'une question se règle ici, pas sur un autre écran.
    assert "règle-le avec elle ICI" in _PROMPT_ORCHESTRATION


def test_une_reponse_dite_au_fil_devient_une_carte_et_ne_part_pas() -> None:
    """Le verdict `attente` pose la carte — la question telle qu'on la montre — et c'est tout."""
    state = ControlTowerState()
    _pose(state)
    juge = Juge(
        _attente(
            REGLEMENT_REPONSE,
            QUESTION_ID,
            texte=REPONSE,
            reponse="Le développeur demande quelle base utiliser : je lui réponds Postgres ?",
        )
    )

    reponse = asyncio.run(
        _repondeur(state, juge).produire(AGENT_ORCHESTRATION, _fil("réponds-lui : Postgres"))
    )

    assert reponse.contenu.startswith("Le développeur demande quelle base utiliser")
    carte = reponse.reglement
    assert carte is not None
    assert carte.action == REGLEMENT_REPONSE
    assert carte.texte == REPONSE
    assert carte.attente.genre == GENRE_QUESTION
    assert carte.attente.identifiant == QUESTION_ID
    assert carte.attente.objet == QUESTION
    assert carte.attente.agent == "dev"
    # Ce qui se passera, dit par le service qui le fera — les mots du fait d'après.
    assert carte.suite == "l'agent attendait cette réponse : il la lit et reprend sa tâche"
    assert reponse.porte_une_demande
    # Rien n'est parti : la question attend toujours.
    question = state.question(QUESTION_ID)
    assert question is not None and question.statut == QUESTION_EN_ATTENTE


def test_la_reponse_confirmee_parvient_a_l_agent_et_sa_tache_reprend() -> None:
    """Le critère entier, contre le vrai moteur : suspendu, il lit la réponse et aboutit.

    L'agent pose sa question par le vrai canal (`ArbitreQuestionControlTower`, sur le
    bus), la pompe la projette, le fil la règle par sa carte, et **c'est cette
    réponse-là** que l'agent lit — puis sa tâche et la suivante aboutissent. La borne
    de la question est large : si l'agent reprenait, ce serait par la réponse, jamais
    par l'hypothèse.
    """

    class Demandeur(ModelProvider):
        """Exécutant outillé qui pose **une** question sur sa première tâche et la lit."""

        name = "demandeur"

        def __init__(self) -> None:
            self.lu: list[str | None] = []

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
            return "TEXTE"

        async def run_agent(self, prompt, **kw):
            if kw.get("on_question") is not None and not self.lu:
                self.lu.append(await kw["on_question"](QUESTION, CHOIX, HYPOTHESE))
            (Path(kw["workspace"]) / "livrable.txt").write_text("contenu", encoding="utf-8")
            return "OUTILLE"

    class Plan(ModelProvider):
        name = "plan"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            return json.dumps(
                [
                    {
                        "id": "schema",
                        "titre": "Rédiger le schéma de données",
                        "description": "Choisir la base et écrire le schéma.",
                        "competences_requises": ["backend"],
                        "format_sortie": "Texte",
                        "dependances": [],
                    },
                    {
                        "id": "api",
                        "titre": "Exposer l'API",
                        "description": "Exposer l'API sur le schéma.",
                        "competences_requises": ["backend"],
                        "format_sortie": "Texte",
                        "dependances": ["schema"],
                    },
                ],
                ensure_ascii=False,
            )

    async def scenario():
        bus = InMemoryEventBus()
        state = ControlTowerState()
        pompe = asyncio.create_task(_pomper(bus, state))
        await asyncio.sleep(0)
        demandeur = Demandeur()
        moteur = OrchestrationEngine(
            demandeur,
            Orchestrator(Plan(), model="claude-opus-4-8"),
            questionneur=ArbitreQuestionControlTower(bus),
            bornes_question=BornesArbitrage(attente_s=600.0),
        )
        run = asyncio.create_task(
            moteur.run("Livrer la démo", journal=RunJournal(run_id="run-1183"))
        )
        try:
            await _quand(lambda: bool(state.questions()))
            (question,) = state.questions()
            juge = Juge(_attente(REGLEMENT_REPONSE, question.question_id, texte=REPONSE))
            repondeur = _repondeur(state, juge, bus)
            carte = await repondeur.produire(
                AGENT_ORCHESTRATION, _fil("réponds-lui : prends Postgres")
            )
            suspendu = not run.done() and demandeur.lu == []
            assert carte.reglement is not None
            suite = await repondeur.trancher_reglement(
                AGENT_ORCHESTRATION,
                _carte(carte, "réponds-lui : prends Postgres"),
                demande=carte.reglement,
                approuve=True,
            )
            rapport = await asyncio.wait_for(run, timeout=30)
        finally:
            pompe.cancel()
            if not run.done():
                run.cancel()
        return suspendu, suite, rapport, demandeur.lu, state, juge

    suspendu, suite, rapport, lu, state, juge = asyncio.run(scenario())

    # L'agent attendait bien sa réponse quand la carte a été posée.
    assert suspendu is True
    # C'est la réponse de la carte qu'il a lue, telle qu'elle a été montrée.
    assert lu == [REPONSE]
    # Et sa tâche a repris : elle aboutit, et la suivante avec elle.
    assert [resultat.ok for resultat in rapport.resultats] == [True, True]
    (question,) = state.questions()
    assert question.statut == QUESTION_REPONDUE
    assert question.reponse == REPONSE
    # Le fil dit ce qui a repris : un fait, puis la parole du modèle sur ce fait.
    fait = suite.reglement_fait
    assert fait is not None and fait.refus == ""
    assert fait.suite == "l'agent attendait cette réponse : il la lit et reprend sa tâche"
    assert suite.contenu == REDIGE
    assert "il la lit et reprend sa tâche" in juge.redactions[-1]


def test_un_oui_tape_sur_la_carte_vaut_le_clic() -> None:
    """« Oui, envoie-la » : le juge rend un accord, et c'est **la carte** qui part."""
    state = ControlTowerState()
    _pose(state)
    juge = Juge(_attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE))
    repondeur = _repondeur(state, juge)
    carte = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("réponds-lui Postgres")))
    fil = [*_carte(carte, "réponds-lui Postgres"), *_fil("oui, envoie-la")]
    juge.verdict = _dit("accord", "Je lui transmets votre réponse.")

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil))

    assert reponse.contenu == "Je lui transmets votre réponse."
    assert reponse.reglement_fait is not None and reponse.reglement_fait.refus == ""
    question = state.question(QUESTION_ID)
    assert question is not None and question.reponse == REPONSE


def test_une_reponse_ecartee_ne_part_pas() -> None:
    """« Pas maintenant » : rien ne part, et le modèle le dit depuis ce fait."""
    state = ControlTowerState()
    _pose(state)
    juge = Juge(_attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE))
    repondeur = _repondeur(state, juge)
    carte = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("réponds-lui")))
    assert carte.reglement is not None

    reponse = asyncio.run(
        repondeur.trancher_reglement(
            AGENT_ORCHESTRATION,
            _carte(carte, "réponds-lui"),
            demande=carte.reglement,
            approuve=False,
        )
    )

    assert reponse.reglement_fait is None
    assert "Rien n'est parti" in juge.redactions[-1]
    question = state.question(QUESTION_ID)
    assert question is not None and question.statut == QUESTION_EN_ATTENTE


def test_passee_l_echeance_le_fil_dit_que_l_agent_etait_reparti() -> None:
    """La réponse sert encore, mais elle ne le fait plus « reprendre » : elle le rattrapera."""
    state = ControlTowerState()
    _pose(state, attente_s=1.0)
    plus_tard = datetime.now(UTC) + timedelta(hours=1)
    juge = Juge(_attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE))
    repondeur = _repondeur(state, juge, horloge=lambda: plus_tard)
    carte = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("réponds-lui")))
    assert carte.reglement is not None

    reponse = asyncio.run(
        repondeur.trancher_reglement(
            AGENT_ORCHESTRATION,
            _carte(carte, "réponds-lui"),
            demande=carte.reglement,
            approuve=True,
        )
    )

    fait = reponse.reglement_fait
    assert fait is not None
    assert fait.suite.startswith("l'agent était déjà reparti sur son hypothèse")
    assert "au prochain appel identique" in fait.suite


def test_une_question_deja_repondue_ne_pose_pas_de_carte() -> None:
    """Le refus du service, dit **avant** la carte : on ne propose pas ce qui ne partira pas."""
    state = ControlTowerState()
    _pose(state)
    asyncio.run(ServiceAttentes(state, InMemoryEventBus()).repondre(QUESTION_ID, "SQLite"))
    juge = Juge(_attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE))

    reponse = asyncio.run(
        _repondeur(state, juge).produire(AGENT_ORCHESTRATION, _fil("réponds-lui Postgres"))
    )

    assert reponse.reglement is None
    assert "aucune carte ne suivra" in reponse.contenu
    fait = reponse.reglement_fait
    assert fait is not None and "déjà reçu sa réponse" in fait.refus
    assert "SQLite" in fait.refus


def test_deux_questions_possibles_sont_nommees_et_rien_n_attend() -> None:
    """« Réponds-lui » quand deux agents demandent : les deux sont nommées, rien n'est proposé."""
    state = ControlTowerState()
    _pose(state)
    autre = _pose(state, question_id="api:1183b", question="REST ou GraphQL ?", agent="api")
    juge = Juge(
        _attente(REGLEMENT_REPONSE, QUESTION_ID, autre, texte="Postgres", reponse="Laquelle ?")
    )

    reponse = asyncio.run(
        _repondeur(state, juge).produire(AGENT_ORCHESTRATION, _fil("réponds-lui"))
    )

    assert reponse.reglement is None
    assert {a.identifiant for a in reponse.attentes_candidates} == {QUESTION_ID, autre}
    assert reponse.contenu == "Laquelle ?"


def test_une_question_ne_s_approuve_pas() -> None:
    """Le genre se déduit de l'action : approuver un identifiant de question ne vise rien.

    Répondre à un agent n'autorise aucun acte (EF-08) ; l'inverse tient aussi — une
    approbation ne peut pas atterrir sur une question.
    """
    state = ControlTowerState()
    _pose(state)
    juge = Juge(_attente(REGLEMENT_APPROBATION, QUESTION_ID))

    reponse = asyncio.run(
        _repondeur(state, juge).produire(AGENT_ORCHESTRATION, _fil("oui, valide"))
    )

    assert reponse.reglement is None
    assert "Je ne trouve rien qui attende" in reponse.contenu


def test_sans_service_le_fil_dit_qu_il_ne_peut_pas_regler() -> None:
    juge = Juge(_attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE))

    reponse = asyncio.run(
        RepondeurOrchestration(provider=juge).produire(AGENT_ORCHESTRATION, _fil("réponds-lui"))
    )

    assert reponse.reglement is None
    assert "Aucun règlement des attentes" in reponse.contenu


def test_la_transcription_rend_la_carte_et_le_fait_au_tour_suivant() -> None:
    """Le tour d'après sait ce que la carte proposait et ce qu'il en est sorti."""
    attente = AttenteVisee(
        genre=GENRE_VALIDATION, identifiant="nettoyer", agent="dev", objet="Bash command=rm"
    )
    fil = [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Je refuse ?",
            reglement=ReglementPropose(REGLEMENT_REFUS, attente, texte=RAISON),
        ),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Refusé.",
            reglement_fait=ReglementFait(
                REGLEMENT_REFUS, attente, texte=RAISON, suite="l'appel est écarté"
            ),
        ),
    ]

    texte = transcription(fil)

    assert "[Règlement proposé sur la carte : refuser la validation de l'agent dev" in texte
    assert f"raison : « {RAISON} »" in texte
    assert "[Règlement d'une attente : refuser" in texte
    assert "fait — l'appel est écarté" in texte


def test_la_carte_se_relit_du_disque_telle_qu_elle_a_ete_montree() -> None:
    """Le message persisté rend sa carte et son fait, sans rien rejuger."""
    attente = AttenteVisee(genre=GENRE_QUESTION, identifiant=QUESTION_ID, objet=QUESTION)
    message = MessageChat(
        agent=NOM_ORCHESTRATION,
        auteur=NOM_ORCHESTRATION,
        contenu="Je lui réponds ?",
        reglement=ReglementPropose(REGLEMENT_REPONSE, attente, texte=REPONSE),
        attentes_candidates=(attente,),
    )

    relu = MessageChat.from_dict(message.to_ligne())

    assert relu.reglement == message.reglement
    assert relu.attentes_candidates == (attente,)
    assert reglement_en_attente([relu]) is relu
    # Une ligne écrite avant #1183 se relit sans carte.
    ancienne = {k: v for k, v in message.to_ligne().items() if k != "reglement"}
    assert MessageChat.from_dict(ancienne).reglement is None


# ── ② la validation d'une action sensible ───────────────────────────────────────


def _trancher_depuis_le_fil(
    state: ControlTowerState, bus: EventBus, verdict: str, message: str
) -> tuple[ReponseChat, ReponseChat, Juge]:
    """La carte, puis sa confirmation — dans une boucle où le validateur peut attendre."""

    async def tour() -> tuple[ReponseChat, ReponseChat, Juge]:
        juge = Juge(verdict)
        repondeur = _repondeur(state, juge, bus)
        carte = await repondeur.produire(AGENT_ORCHESTRATION, _fil(message))
        assert carte.reglement is not None
        suite = await repondeur.trancher_reglement(
            AGENT_ORCHESTRATION, _carte(carte, message), demande=carte.reglement, approuve=True
        )
        return carte, suite, juge

    return asyncio.run(tour())


@pytest.mark.parametrize(
    ("action", "texte", "approuve"),
    [(REGLEMENT_APPROBATION, "", True), (REGLEMENT_REFUS, RAISON, False)],
)
def test_une_validation_se_tranche_par_une_carte_confirmee_jusqu_au_moteur(
    action: str, texte: str, approuve: bool
) -> None:
    """Le validateur que le moteur attend rend la décision de la carte, et rien avant elle.

    Et un refus porte sa raison **dans l'événement même que le moteur reçoit** : c'est
    ce champ-là que #1185 portera jusqu'à l'agent.
    """

    async def scenario():
        bus = InMemoryEventBus()
        state = ControlTowerState()
        pompe = asyncio.create_task(_pomper(bus, state))
        recus: list[Event] = []

        async def ecouter() -> None:
            async for event in bus.subscribe():
                if event.type == EVENEMENT_VALIDATION_DECISION:
                    recus.append(event)

        ecoute = asyncio.create_task(ecouter())
        await asyncio.sleep(0)
        attente = asyncio.create_task(ValidateurControlTower(bus)(_demande_validation()))
        try:
            await _quand(lambda: state.validation("nettoyer") is not None)
            juge = Juge(_attente(action, "nettoyer", texte=texte))
            repondeur = _repondeur(state, juge, bus)
            carte = await repondeur.produire(AGENT_ORCHESTRATION, _fil("tranche-la"))
            suspendu = not attente.done()
            assert carte.reglement is not None
            suite = await repondeur.trancher_reglement(
                AGENT_ORCHESTRATION,
                _carte(carte, "tranche-la"),
                demande=carte.reglement,
                approuve=True,
            )
            decision = await asyncio.wait_for(attente, timeout=10)
            await _quand(lambda: bool(recus))
        finally:
            pompe.cancel()
            ecoute.cancel()
        return suspendu, carte, suite, decision, recus, state

    suspendu, carte, suite, decision, recus, state = asyncio.run(scenario())

    assert suspendu is True
    assert carte.reglement is not None
    assert carte.reglement.attente.objet == "Bash command=rm -rf build"
    assert carte.reglement.suite == (
        "l'appel s'exécute et la tâche reprend"
        if approuve
        else "l'appel est écarté : l'agent poursuit sa tâche sans lui"
    )
    assert decision is approuve
    validation = state.validation("nettoyer")
    assert validation is not None
    assert validation.statut == (VALIDATION_APPROUVEE if approuve else VALIDATION_REFUSEE)
    if not approuve:
        assert recus[-1].detail == f"refusée depuis la Control Tower — {RAISON}"
        assert validation.decision == f"refusée depuis la Control Tower — {RAISON}"
    fait = suite.reglement_fait
    assert fait is not None and fait.refus == ""
    assert fait.texte == texte


def test_ce_qui_reprend_se_lit_sur_la_structure_de_la_demande() -> None:
    """Un acte, une écriture dans le projet, le reste — jamais deviné d'un texte."""
    acte = EtatValidation(tache_id="t", outil="Bash")
    ecriture = EtatValidation(tache_id="t", diff=DiffProjet())
    tache = EtatValidation(tache_id="t")

    assert suite_de_la_decision(acte, approuve=True) == "l'appel s'exécute et la tâche reprend"
    assert "poursuit sa tâche sans lui" in suite_de_la_decision(acte, approuve=False)
    assert suite_de_la_decision(ecriture, approuve=True) == "le travail s'écrit dans le projet"
    assert "rien n'est écrit" in suite_de_la_decision(ecriture, approuve=False)
    assert suite_de_la_decision(tache, approuve=True) == "la tâche reprend"
    assert suite_de_la_decision(tache, approuve=False) == "l'action demandée n'aura pas lieu"


@pytest.mark.parametrize("genre", [GENRE_QUESTION, GENRE_VALIDATION])
def test_sur_un_run_deja_solde_rien_ne_reprend_et_le_fil_le_dit(genre: str) -> None:
    """Vu sur la vraie stack : le run avait fini, la carte disait « l'agent poursuit ».

    Une demande reste réglable une fois son run soldé — elle est restée servie —, mais
    ce qui en sort n'est plus une reprise : le service le dit, sur la carte comme dans
    le fait, depuis l'état du run et jamais depuis un texte.
    """
    state = ControlTowerState()
    identifiant = _pose(state) if genre == GENRE_QUESTION else _soumet(state)
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id="run-1183", statut=EXECUTION_TERMINEE)
    )
    service = ServiceAttentes(state, InMemoryEventBus())
    action = REGLEMENT_REPONSE if genre == GENRE_QUESTION else REGLEMENT_REFUS

    annonce = service.suite(action, identifiant)
    fait = asyncio.run(service.regler(action, identifiant, "Postgres"))

    assert annonce == fait.suite
    assert annonce.startswith("son run est déjà soldé")
    # Ni reprise ni poursuite annoncées : ce sont elles que le run soldé dément.
    for promesse in ("reprend sa tâche", "la tâche reprend", "poursuit sa tâche"):
        assert promesse not in annonce


def test_un_refus_confirme_se_raconte_avec_sa_raison_et_ce_qui_en_sort() -> None:
    """Le modèle parle depuis le fait : la raison partie, et l'agent qui poursuit sans l'acte."""
    state = ControlTowerState()
    _soumet(state)

    _, suite, juge = _trancher_depuis_le_fil(
        state,
        InMemoryEventBus(),
        _attente(REGLEMENT_REFUS, "nettoyer", texte=RAISON),
        "refuse et archive plutôt",
    )

    assert suite.contenu == REDIGE
    faits = juge.redactions[-1]
    assert f"Sa raison, « {RAISON} », est consignée avec la décision" in faits
    assert "l'agent poursuit sa tâche sans lui" in faits
    # Vu sur la vraie stack : « avec sa raison » faisait dire au modèle que la consigne
    # était transmise à l'agent. Elle ne l'est pas tant que #1185 ne la lui porte pas.
    assert "l'agent, lui, ne la reçoit pas" in faits
    assert "que tu lui transmets la raison ni qu'il la suivra" in _PROMPT_ORCHESTRATION


# ── Bout en bout : l'API entière, le même service que les écrans ───────────────


@pytest.fixture()
def juge(monkeypatch: pytest.MonkeyPatch) -> Juge:
    juge = Juge()
    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", lambda *a, **k: juge)
    return juge


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice : aucun test n'écrit dans le vrai."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _app(state: ControlTowerState, tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            bus=InMemoryEventBus(), state=state, chat_store=ChatStore(tmp_path / "chat")
        )
    )


def _dire(client: TestClient, contenu: str) -> dict[str, Any]:
    reponse = client.post(f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": contenu})
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["messages"][1]


def _confirmer(client: TestClient, approuve: bool = True) -> Any:
    return client.post(f"/api/chat/{NOM_ORCHESTRATION}/reglement", json={"approuve": approuve})


def test_de_bout_en_bout_repondre_depuis_le_fil_passe_par_le_service_de_l_ecran(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    state = ControlTowerState()
    _pose(state)
    juge.verdict = _attente(REGLEMENT_REPONSE, QUESTION_ID, texte=REPONSE)

    with _app(state, tmp_path) as client:
        carte = _dire(client, "réponds-lui : prends Postgres")
        assert carte["reglement"]["action"] == REGLEMENT_REPONSE
        assert carte["reglement"]["attente"]["identifiant"] == QUESTION_ID
        assert carte["reglement"]["texte"] == REPONSE
        # La carte est posée, et rien n'est parti.
        (avant,) = client.get("/api/questions?projet=tous").json()
        assert avant["statut"] == QUESTION_EN_ATTENTE

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        geste, suite = reponse.json()["messages"]
        assert geste["contenu"] == "Oui, envoie-lui cette réponse."
        assert suite["contenu"] == REDIGE
        assert suite["reglement_fait"]["refus"] == ""
        assert suite["reglement_fait"]["suite"].endswith("il la lit et reprend sa tâche")
        # La question est répondue — par la même route que l'écran la voit.
        (apres,) = client.get("/api/questions?projet=tous").json()
        assert apres["statut"] == QUESTION_REPONDUE
        assert apres["reponse"] == REPONSE
        # Un second clic ne répond pas deux fois.
        assert _confirmer(client).status_code == 409


def test_de_bout_en_bout_un_refus_du_fil_porte_la_meme_raison_que_celui_de_l_ecran(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """Deux demandes, deux chemins — le fil et l'écran des validations —, une seule raison."""
    state = ControlTowerState()
    _soumet(state)
    _soumet(state, task_id="purger", titre="Purger les journaux")
    juge.verdict = _attente(REGLEMENT_REFUS, "nettoyer", texte=RAISON)

    with _app(state, tmp_path) as client:
        carte = _dire(client, "refuse et archive plutôt")
        assert carte["reglement"]["action"] == REGLEMENT_REFUS
        assert carte["reglement"]["texte"] == RAISON

        reponse = _confirmer(client)
        ecran = client.post(
            "/api/validations/purger/decision", json={"approuve": False, "motif": RAISON}
        )

        assert reponse.status_code == 201, reponse.text
        assert ecran.status_code == 200, ecran.text
        geste, suite = reponse.json()["messages"]
        assert geste["contenu"] == f"Oui, refuse cet acte — raison : {RAISON}"
        assert suite["reglement_fait"]["texte"] == RAISON
        decisions = {
            v["tache_id"]: v for v in client.get("/api/validations?projet=tous").json()
        }
        assert decisions["nettoyer"]["statut"] == VALIDATION_REFUSEE
        assert decisions["nettoyer"]["decision"] == decisions["purger"]["decision"]
        assert decisions["nettoyer"]["decision"] == f"refusée depuis la Control Tower — {RAISON}"


def test_de_bout_en_bout_une_validation_tranchee_entre_la_carte_et_le_clic_est_dite(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """L'écran des validations a tranché pendant que la carte attendait : le refus se dit."""
    state = ControlTowerState()
    _soumet(state)
    juge.verdict = _attente(REGLEMENT_APPROBATION, "nettoyer", reponse="Je l'approuve ?")

    with _app(state, tmp_path) as client:
        assert _dire(client, "oui, valide")["reglement"]["action"] == REGLEMENT_APPROBATION
        assert (
            client.post("/api/validations/nettoyer/decision", json={"approuve": False}).status_code
            == 200
        )

        reponse = _confirmer(client)

        assert reponse.status_code == 201, reponse.text
        fait = reponse.json()["messages"][1]["reglement_fait"]
        assert fait["refus"] == "cette demande a déjà été tranchée — refusée."
        assert "REFUSÉ" in juge.redactions[-1]
        validation = state.validation("nettoyer")
        assert validation is not None and validation.statut == VALIDATION_REFUSEE


def test_les_routes_des_ecrans_gardent_leurs_codes_de_refus(
    tmp_path: Path, maison: Path, juge: Juge
) -> None:
    """Les règles ont déménagé dans le service ; les statuts HTTP n'ont pas bougé."""
    state = ControlTowerState()
    question_id = _pose(state)
    _soumet(state)

    with _app(state, tmp_path) as client:
        assert (
            client.post("/api/questions/inconnue/reponse", json={"reponse": "x"}).status_code
            == 404
        )
        assert (
            client.post(f"/api/questions/{question_id}/reponse", json={"reponse": "  "}).status_code
            == 422
        )
        assert (
            client.post(f"/api/questions/{question_id}/reponse", json={"reponse": "ok"}).status_code
            == 200
        )
        assert (
            client.post(f"/api/questions/{question_id}/reponse", json={"reponse": "ok"}).status_code
            == 409
        )
        assert (
            client.post("/api/validations/inconnue/decision", json={"approuve": True}).status_code
            == 404
        )
        assert (
            client.post("/api/validations/nettoyer/decision", json={"approuve": True}).status_code
            == 200
        )
        assert (
            client.post("/api/validations/nettoyer/decision", json={"approuve": True}).status_code
            == 409
        )
        validation = state.validation("nettoyer")
        assert validation is not None and validation.statut == VALIDATION_APPROUVEE
        assert validation.statut != VALIDATION_EN_ATTENTE
