"""La décision au plafond de dépense, côté Control Tower — l'arbitre, la projection, l'API (#1182).

Le moteur sait désormais se suspendre au plafond (`tests/test_plafond_suspendu.py`) ;
cette suite éprouve le chemin par lequel la question atteint une personne et sa
réponse revient, dans l'ordre où il se parcourt :

① **l'arbitre** publie la demande sur le bus et rend la décision qui y revient —
   sans borne, en ignorant ce qui n'en est pas une ;
② **la projection** passe le run « en attente du plafond », en garde les faits
   (dépense, plafonds, reste à faire) dans le résumé que le fil lit, et les retire
   à la décision ;
③ **l'API** tranche : `POST /api/executions/{run_id}/plafond` refuse ce qui ne se
   tient pas (404, 409, 422) et publie le reste ;
④ **de bout en bout**, sur l'app réelle et un vrai moteur : un run lancé par
   `POST /api/executions` avec un plafond atteint s'y arrête, attend, puis
   reprend sur « relever » ou se solde sur « arrêter ».

Ni Redis, ni réseau, ni modèle : bus mémoire, TestClient de Starlette, fournisseurs
factices qui signalent leur dépense comme un vrai.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, Event, InMemoryEventBus, create_app
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_PLAFOND_DECISION,
    EVENEMENT_PLAFOND_DEMANDE,
)
from maestro.controltower.plafond import (
    ArbitrePlafondControlTower,
    evenement_decision,
    evenement_demande,
)
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_PLAFOND,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_EN_ATTENTE,
    libelle_statut_execution,
)
from maestro.engine import OrchestrationEngine
from maestro.engine.plafond import (
    GESTE_ARRETER,
    GESTE_REDUIRE,
    GESTE_RELEVER,
    DecisionPlafond,
    DemandePlafond,
    TacheRestante,
)
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider
from maestro.telemetry import StepUsage, report_usage

RUN = "run-au-plafond"
PROJET = "prj-plafond"

#: Plafond d'attente d'un run en fond ou d'un rendez-vous entre coroutines — jamais
#: atteint quand tout va bien.
DELAI_S = 10.0

DEMANDE = DemandePlafond(
    run_id=RUN,
    projet_id=PROJET,
    objectif="Livrer l'agenda",
    depense_usd=5.02,
    depense_tokens=41_000,
    plafond_cout_usd=5.0,
    plafond_tokens=None,
    raison="plafond de dépense dépassé : 5.0200 $ consommés sur l'exécution",
    restantes=(
        TacheRestante("api", "Écrire l'API", interrompue=True),
        TacheRestante("doc", "Documenter l'API"),
        TacheRestante("recette", "Recette"),
    ),
)


# --------------------------------------------------------------------------- #
# ① L'arbitre
# --------------------------------------------------------------------------- #


async def _laisse_s_abonner() -> None:
    """Assez de tours de boucle pour que l'abonnement au bus mémoire soit posé."""
    for _ in range(5):
        await asyncio.sleep(0)


def test_l_arbitre_publie_la_demande_puis_rend_la_decision_du_bus():
    async def scenario() -> tuple[list[Event], DecisionPlafond]:
        bus = InMemoryEventBus()
        vus: list[Event] = []

        async def ecoute() -> None:
            async for event in bus.subscribe():
                vus.append(event)
                if event.type == EVENEMENT_PLAFOND_DEMANDE:
                    return

        temoin = asyncio.create_task(ecoute())
        await _laisse_s_abonner()
        attente = asyncio.create_task(ArbitrePlafondControlTower(bus)(DEMANDE))
        await asyncio.wait_for(temoin, timeout=DELAI_S)
        await _laisse_s_abonner()
        # Du bruit d'abord : une décision pour un autre run, et une décision
        # illisible pour celui-ci — ni l'une ni l'autre n'est la réponse.
        await bus.publish(evenement_decision("un-autre-run", DecisionPlafond(GESTE_ARRETER)))
        await bus.publish(
            Event(type=EVENEMENT_PLAFOND_DECISION, run_id=RUN, plafond={"geste": "?"})
        )
        await bus.publish(
            evenement_decision(RUN, DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=8.0))
        )
        return vus, await asyncio.wait_for(attente, timeout=DELAI_S)

    vus, decision = asyncio.run(scenario())

    (demande,) = [e for e in vus if e.type == EVENEMENT_PLAFOND_DEMANDE]
    assert demande.run_id == RUN
    assert demande.projet_id == PROJET
    assert DemandePlafond.from_dict(demande.plafond or {}) == DEMANDE
    assert decision == DecisionPlafond(GESTE_RELEVER, plafond_cout_usd=8.0)


def test_la_demande_expurge_l_objectif():
    secret = "sk-ant-api03-secretsecretsecretsecretsecret"
    demande = DemandePlafond(
        run_id=RUN,
        projet_id=None,
        objectif=f"Déployer avec {secret}",
        depense_usd=None,
        depense_tokens=10,
        plafond_cout_usd=None,
        plafond_tokens=5,
        raison="plafond de tokens dépassé",
    )
    assert secret not in json.dumps(evenement_demande(demande).to_dict())


# --------------------------------------------------------------------------- #
# ② La projection
# --------------------------------------------------------------------------- #


def _run_en_vol(state: ControlTowerState) -> None:
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            titre="Livrer l'agenda",
            projet_id=PROJET,
        )
    )


def test_la_demande_suspend_le_run_et_ses_faits_sont_dans_le_resume():
    state = ControlTowerState()
    _run_en_vol(state)

    state.appliquer(evenement_demande(DEMANDE))

    resume = state.execution(RUN).resume()
    assert resume["statut"] == EXECUTION_EN_ATTENTE_PLAFOND
    assert resume["statut"] in STATUTS_EXECUTION_EN_ATTENTE
    assert resume["attente_depuis"]
    assert DemandePlafond.from_dict(resume["plafond"]) == DEMANDE


def test_la_decision_leve_l_attente_et_retire_les_faits():
    state = ControlTowerState()
    _run_en_vol(state)
    state.appliquer(evenement_demande(DEMANDE))

    decision = evenement_decision(RUN, DecisionPlafond(GESTE_ARRETER))
    state.appliquer(decision)
    state.appliquer(decision)  # la pompe réapplique ce que l'endpoint a posé

    resume = state.execution(RUN).resume()
    assert resume["statut"] == EXECUTION_EN_COURS
    assert resume["attente_depuis"] is None
    assert resume["plafond"] is None


def test_une_issue_du_run_retire_la_question():
    state = ControlTowerState()
    _run_en_vol(state)
    state.appliquer(evenement_demande(DEMANDE))

    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut="annulee")
    )

    assert state.execution(RUN).resume()["plafond"] is None


def test_le_fil_dit_le_statut_avec_les_mots_de_l_ecran():
    """Le libellé est celui de `libelleStatutExecution` (apps/web/lib/format.ts), au mot près."""
    assert libelle_statut_execution(EXECUTION_EN_ATTENTE_PLAFOND) == "Budget atteint"


# --------------------------------------------------------------------------- #
# ③ L'API — ce qu'elle refuse, et ce qu'elle publie
# --------------------------------------------------------------------------- #


def _client(state: ControlTowerState, bus: InMemoryEventBus | None = None) -> TestClient:
    return TestClient(create_app(bus=bus or InMemoryEventBus(), state=state))


def _en_attente() -> ControlTowerState:
    state = ControlTowerState()
    _run_en_vol(state)
    state.appliquer(evenement_demande(DEMANDE))
    return state


def test_un_run_inconnu_est_un_404():
    with _client(ControlTowerState()) as client:
        reponse = client.post("/api/executions/fantome/plafond", json={"geste": "arreter"})
    assert reponse.status_code == 404


def test_un_run_qui_n_attend_pas_de_decision_est_un_409():
    state = ControlTowerState()
    _run_en_vol(state)
    with _client(state) as client:
        reponse = client.post(f"/api/executions/{RUN}/plafond", json={"geste": "arreter"})
    assert reponse.status_code == 409
    assert "n'attend pas de décision au plafond" in reponse.json()["detail"]


@pytest.mark.parametrize(
    ("corps", "motif"),
    [
        ({"geste": "continuer"}, "inconnu"),
        ({"geste": GESTE_RELEVER}, "nouveau plafond"),
        ({"geste": GESTE_RELEVER, "plafond_cout_usd": 5.0}, "ne couvre pas"),
        ({"geste": GESTE_RELEVER, "plafond_cout_usd": 4.0}, "ne couvre pas"),
        ({"geste": GESTE_REDUIRE, "plafond_cout_usd": 8.0}, "écarter"),
        (
            {"geste": GESTE_REDUIRE, "plafond_cout_usd": 8.0, "ecartees": ["inventee"]},
            "inconnue",
        ),
        (
            {
                "geste": GESTE_REDUIRE,
                "plafond_cout_usd": 8.0,
                "ecartees": ["api", "doc", "recette"],
            },
            "arrêter",
        ),
    ],
)
def test_une_decision_qui_ne_se_tient_pas_est_un_422(corps: dict[str, Any], motif: str):
    state = _en_attente()
    with _client(state) as client:
        reponse = client.post(f"/api/executions/{RUN}/plafond", json=corps)
    assert reponse.status_code == 422, reponse.json()
    assert motif in reponse.json()["detail"]
    # Rien n'a bougé : le run attend toujours.
    assert state.execution(RUN).statut == EXECUTION_EN_ATTENTE_PLAFOND


def test_relever_publie_la_decision_et_rend_le_run_en_cours():
    state = _en_attente()
    bus = InMemoryEventBus()
    with _client(state, bus) as client:
        reponse = client.post(
            f"/api/executions/{RUN}/plafond",
            json={"geste": GESTE_RELEVER, "plafond_cout_usd": 8.0},
        )
        decisions = [
            e for e in state.execution(RUN).evenements if e.type == EVENEMENT_PLAFOND_DECISION
        ]

    assert reponse.status_code == 200
    resume = reponse.json()
    assert resume["statut"] == EXECUTION_EN_COURS
    assert resume["plafond"] is None
    assert decisions
    assert DecisionPlafond.from_dict(decisions[0].plafond or {}) == DecisionPlafond(
        GESTE_RELEVER,
        plafond_cout_usd=8.0,
        detail="plafond relevé depuis la Control Tower",
    )


def test_reduire_nomme_des_taches_de_la_question():
    state = _en_attente()
    with _client(state) as client:
        reponse = client.post(
            f"/api/executions/{RUN}/plafond",
            json={"geste": GESTE_REDUIRE, "plafond_cout_usd": 6.5, "ecartees": ["doc"]},
        )
    assert reponse.status_code == 200
    # L'endpoint l'applique, la pompe la réapplique : la même décision, vue deux fois.
    decision, *_ = [
        e for e in state.execution(RUN).evenements if e.type == EVENEMENT_PLAFOND_DECISION
    ]
    assert decision.statut == GESTE_REDUIRE
    assert (decision.plafond or {})["ecartees"] == ["doc"]


def test_une_decision_ne_se_rend_pas_deux_fois():
    state = _en_attente()
    with _client(state) as client:
        premiere = client.post(f"/api/executions/{RUN}/plafond", json={"geste": "arreter"})
        seconde = client.post(f"/api/executions/{RUN}/plafond", json={"geste": "arreter"})
    assert premiere.status_code == 200
    assert seconde.status_code == 409


# --------------------------------------------------------------------------- #
# ④ De bout en bout : l'app réelle, un vrai moteur
# --------------------------------------------------------------------------- #


class _Planificateur(ModelProvider):
    name = "planificateur"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class _Depensier(ModelProvider):
    """Chaque appel signale deux mesures de 0,006 $ — le cumul franchit 0,01 $."""

    name = "depensier"

    def __init__(self) -> None:
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        return "LIVRABLE"


PLAN = json.dumps(
    [
        {
            "id": "tests-api",
            "titre": "Tests de l'API",
            "description": "Tests d'intégration.",
            "competences_requises": ["tests"],
            "format_sortie": "Note",
            "dependances": [],
        }
    ]
)


def _app_reelle(state: ControlTowerState, depensier: _Depensier) -> TestClient:
    """L'app réelle, avec un moteur réel que la fabrique arme comme la production."""

    def fabrique(**reglages: Any) -> OrchestrationEngine:
        return OrchestrationEngine(
            depensier,
            Orchestrator(_Planificateur(PLAN), model="claude-opus-4-8"),
            guardrails=reglages["guardrails"],
            max_parallele=reglages["max_parallele"],
            arbitre_plafond=reglages["arbitre_plafond"],
        )

    return TestClient(create_app(bus=InMemoryEventBus(), state=state, fabrique_moteur=fabrique))


#: Le lancement des runs de bout en bout : un plafond que la première tâche
#: franchit, et sans brief — le cadrage n'est pas le sujet ici.
LANCEMENT = {"objectif": "Tester l'API", "plafond_cout_usd": 0.01, "brief": "sans"}


def _attendre_statut(client: TestClient, run_id: str, statut: str) -> dict:
    """Attend que le run en fond atteigne `statut` — interrogé par l'API, comme l'UI."""
    limite = time.monotonic() + DELAI_S
    while True:
        resume = client.get(f"/api/executions/{run_id}").json()
        if resume["statut"] == statut:
            return resume
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"run {run_id} resté en « {resume['statut']} », attendu « {statut} »")
        time.sleep(0.02)


def test_de_bout_en_bout_le_run_attend_au_plafond_puis_reprend_sur_relever():
    state = ControlTowerState()
    depensier = _Depensier()
    with _app_reelle(state, depensier) as client:
        run_id = client.post("/api/executions", json=LANCEMENT).json()["run_id"]

        attente = _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_PLAFOND)
        # La question porte ses chiffres, et le run n'a rien dépensé de plus.
        faits = DemandePlafond.from_dict(attente["plafond"])
        assert faits.depense_usd == pytest.approx(0.012)
        assert faits.plafond_cout_usd == pytest.approx(0.01)
        assert [(t.tache_id, t.interrompue) for t in faits.restantes] == [
            ("tests-api", True)
        ]
        assert depensier.appels == 1

        reponse = client.post(
            f"/api/executions/{run_id}/plafond",
            json={"geste": GESTE_RELEVER, "plafond_cout_usd": 1.0},
        )
        assert reponse.status_code == 200
        fin = _attendre_statut(client, run_id, EXECUTION_TERMINEE)

    assert fin["plafond"] is None
    assert depensier.appels == 2


def test_de_bout_en_bout_arreter_solde_le_run_sur_ce_qui_est_fait():
    state = ControlTowerState()
    depensier = _Depensier()
    with _app_reelle(state, depensier) as client:
        run_id = client.post("/api/executions", json=LANCEMENT).json()["run_id"]
        _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_PLAFOND)

        client.post(f"/api/executions/{run_id}/plafond", json={"geste": GESTE_ARRETER})
        _attendre_statut(client, run_id, EXECUTION_ECHEC)

    assert depensier.appels == 1
