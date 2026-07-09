"""Tests d'API du backend Control Tower — REST + WebSocket (ticket #46).

Aucun Redis ni réseau requis : l'app FastAPI tourne sur le **bus mémoire**
(`InMemoryEventBus`) via le TestClient de Starlette — REST, WebSocket et pompe
de diffusion sont ceux de la production, seul le transport change. Couvre les
critères d'acceptation du ticket #46 :

① endpoints REST : liste des tâches (statut, agent, coût), état des agents,
   détail d'une exécution (404 sur un run inconnu) ;
② flux WebSocket : les événements publiés sur le bus (statut de tâche,
   activité agent, message inter-agents) arrivent en JSON aux clients, et
   l'état REST est déjà à jour quand l'événement est reçu (pompe : état
   d'abord, diffusion ensuite) ;
③ réassignation manuelle d'une tâche (support du Kanban) : mise à jour de la
   projection, diffusion aux clients WebSocket, 404/422 sur tâche ou agent
   inconnus ;
④ pont télémétrie → événements : une ligne de journal (#8) devient
   l'événement attendu, y compris via un vrai `RunJournal`.
"""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    activer_publication,
    create_app,
    evenements_depuis_step,
)
from maestro.telemetry import LOGGER_NAME, RunJournal, StepUsage


def _statut_tache(tache_id, statut, *, agent="developpeur", role="Développeur",
                  run_id="run-1", titre="Une tâche", cout_usd=None):
    """Événement `tache.statut` prêt à l'emploi pour les scénarios."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=titre,
        agent=agent,
        role=role,
        statut=statut,
        cout_usd=cout_usd,
    )


@pytest.fixture()
def bus():
    """Bus mémoire partagé entre le test (producteur) et l'app (consommatrice)."""
    return InMemoryEventBus()


@pytest.fixture()
def state():
    """Projection d'état injectée : les tests REST la peuplent directement."""
    return ControlTowerState()


@pytest.fixture()
def client(bus, state):
    """TestClient de l'app sur bus mémoire, pompe démarrée (contexte = lifespan)."""
    with TestClient(create_app(bus=bus, state=state)) as client:
        yield client


def publie(client, bus, *events):
    """Publie sur le bus depuis le thread de test, dans la boucle de l'app."""
    for event in events:
        client.portal.call(bus.publish, event)


# ---------------------------------------------------------------- ① REST


def test_sante(client):
    reponse = client.get("/api/sante")
    assert reponse.status_code == 200
    assert reponse.json() == {"statut": "ok"}


def test_cors_ouvert_pour_l_ui(client):
    """L'UI (apps/web, #47) est servie sur une autre origine : le CORS doit répondre."""
    reponse = client.get("/api/sante", headers={"Origin": "http://localhost:3000"})
    assert reponse.headers["access-control-allow-origin"] == "*"


def test_liste_des_taches_statut_agent_cout(client, state):
    """La liste des tâches expose statut, agent assigné et coût (source du Kanban)."""
    state.appliquer(_statut_tache("t1", "en_cours"))
    state.appliquer(_statut_tache("t2", "terminee", agent="qa", role="QA / Testeur",
                                  titre="Relire", cout_usd=0.25))

    taches = client.get("/api/taches").json()

    assert [t["id"] for t in taches] == ["t1", "t2"]
    t1, t2 = taches
    assert t1["statut"] == "en_cours" and t1["agent"] == "developpeur"
    assert t1["cout_usd"] is None  # aucun coût rapporté : inconnu, pas nul
    assert t2 == {
        "id": "t2", "titre": "Relire", "statut": "terminee", "agent": "qa",
        "role": "QA / Testeur", "run_id": "run-1", "cout_usd": 0.25,
        "horodatage": t2["horodatage"],
    }


def test_etat_des_agents_catalogue_et_compteurs(client, state):
    """Les agents du catalogue sont exposés dès le départ ; les événements les animent."""
    noms = {a["nom"] for a in client.get("/api/agents").json()}
    assert {"developpeur", "bdd", "devops", "designer", "qa"} <= noms

    state.appliquer(_statut_tache("t1", "en_cours"))
    par_nom = {a["nom"]: a for a in client.get("/api/agents").json()}
    assert par_nom["developpeur"]["statut"] == "occupe"
    assert par_nom["developpeur"]["tache_courante"] == "t1"

    state.appliquer(_statut_tache("t1", "terminee", cout_usd=0.5))
    state.appliquer(_statut_tache("t2", "echec", cout_usd=0.2))
    dev = {a["nom"]: a for a in client.get("/api/agents").json()}["developpeur"]
    assert dev["statut"] == "libre" and dev["tache_courante"] == ""
    assert dev["taches_terminees"] == 1 and dev["taches_echouees"] == 1
    assert dev["cout_usd"] == pytest.approx(0.7)


def test_tache_bloquee_sans_executant_ne_touche_pas_les_agents(client, state):
    """Une tâche bloquée (#43, agent « — ») apparaît sans créer d'agent fantôme."""
    state.appliquer(_statut_tache("t9", "bloquee", agent="—", role="non exécutée"))
    assert client.get("/api/taches").json()[0]["statut"] == "bloquee"
    assert "—" not in {a["nom"] for a in client.get("/api/agents").json()}


def test_detail_d_une_execution(client, state):
    """Le détail d'un run rejoue ses événements dans l'ordre et agrège le coût."""
    state.appliquer(Event(type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1",
                          agent="orchestrateur", role="Orchestrateur",
                          statut="terminee", detail="2 tâche(s) planifiée(s)",
                          cout_usd=0.1))
    state.appliquer(_statut_tache("t1", "terminee", cout_usd=0.4))

    detail = client.get("/api/executions/run-1").json()

    assert detail["run_id"] == "run-1"
    assert [e["type"] for e in detail["evenements"]] == [
        EVENEMENT_AGENT_ACTIVITE, EVENEMENT_TACHE_STATUT,
    ]
    assert detail["cout_usd"] == pytest.approx(0.5)


def test_execution_inconnue_404(client):
    assert client.get("/api/executions/nulle-part").status_code == 404


# ----------------------------------------------------------- ② WebSocket


def test_websocket_diffuse_les_trois_familles_d_evenements(client, bus):
    """Statut de tâche, activité agent et message inter-agents arrivent en JSON."""
    evenements = (
        _statut_tache("t1", "terminee", cout_usd=0.3),
        Event(type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1", agent="orchestrateur",
              role="Orchestrateur", statut="terminee"),
        Event(type=EVENEMENT_MESSAGE_INTER_AGENTS, run_id="run-1", agent="developpeur",
              role="Développeur", detail="handoff vers qa"),
    )
    with client.websocket_connect("/ws/evenements") as ws:
        publie(client, bus, *evenements)
        recus = [ws.receive_json() for _ in evenements]

    assert recus == [e.to_dict() for e in evenements]


def test_websocket_puis_rest_l_etat_est_deja_a_jour(client, bus):
    """La pompe projette avant de diffuser : un événement reçu ⇒ un REST à jour."""
    with client.websocket_connect("/ws/evenements") as ws:
        publie(client, bus, _statut_tache("t1", "en_cours"))
        ws.receive_json()
        taches = client.get("/api/taches").json()
    assert taches[0]["id"] == "t1" and taches[0]["statut"] == "en_cours"


def test_websocket_plusieurs_clients_recoivent_chacun_tout(client, bus):
    with client.websocket_connect("/ws/evenements") as ws1, \
         client.websocket_connect("/ws/evenements") as ws2:
        publie(client, bus, _statut_tache("t1", "terminee"))
        assert ws1.receive_json()["tache_id"] == "t1"
        assert ws2.receive_json()["tache_id"] == "t1"


# ------------------------------------------------------ ③ Réassignation


def test_reassignation_manuelle_met_a_jour_et_diffuse(client, bus, state):
    """Le POST du Kanban réassigne la tâche et les clients WebSocket le voient."""
    state.appliquer(_statut_tache("t1", "echec"))

    with client.websocket_connect("/ws/evenements") as ws:
        reponse = client.post("/api/taches/t1/reassigner", json={"agent": "qa"})
        recu = ws.receive_json()

    assert reponse.status_code == 200
    assert reponse.json()["agent"] == "qa"
    assert reponse.json()["statut"] == "assignee"
    assert recu["type"] == EVENEMENT_TACHE_REASSIGNATION
    assert recu["tache_id"] == "t1" and recu["agent"] == "qa"

    tache = client.get("/api/taches").json()[0]
    assert tache["agent"] == "qa" and tache["role"] == "QA / Testeur"


def test_reassignation_tache_inconnue_404(client):
    reponse = client.post("/api/taches/fantome/reassigner", json={"agent": "qa"})
    assert reponse.status_code == 404


def test_reassignation_agent_inconnu_422(client, state):
    state.appliquer(_statut_tache("t1", "echec"))
    reponse = client.post("/api/taches/t1/reassigner", json={"agent": "stagiaire"})
    assert reponse.status_code == 422


# ------------------------------------------- ④ Pont télémétrie → événements


def test_ligne_de_tache_devient_evenement_statut():
    record = {
        "run_id": "run-7", "etape": "t1", "nom": "Créer le schéma",
        "agent": "bdd", "role": "Base de données", "statut": "terminee",
        "horodatage": "2026-07-09T10:00:00+00:00",
        "entree": "...", "sortie": "...", "erreur": None,
        "usage": {"cout_usd": 0.12},
    }
    (event,) = evenements_depuis_step(record)
    assert event.type == EVENEMENT_TACHE_STATUT
    assert event.tache_id == "t1" and event.run_id == "run-7"
    assert event.agent == "bdd" and event.statut == "terminee"
    assert event.cout_usd == pytest.approx(0.12)
    assert event.titre == "Créer le schéma"


def test_lignes_planification_et_validation_deviennent_activites():
    planification = {"run_id": "r", "etape": "planification", "nom": "Planification",
                     "agent": "orchestrateur", "role": "Orchestrateur",
                     "statut": "terminee", "sortie": "3 tâche(s) planifiée(s)"}
    validation = {"run_id": "r", "etape": "t2:validation", "nom": "Validation — Déployer",
                  "agent": "devops", "role": "DevOps", "statut": "refuse",
                  "entree": "déploiement production"}

    (activite_plan,) = evenements_depuis_step(planification)
    (activite_valid,) = evenements_depuis_step(validation)

    assert activite_plan.type == EVENEMENT_AGENT_ACTIVITE
    assert activite_plan.tache_id == "" and "planifiée" in activite_plan.detail
    assert activite_valid.type == EVENEMENT_AGENT_ACTIVITE
    assert activite_valid.tache_id == "t2" and activite_valid.statut == "refuse"


def test_ligne_illisible_ne_produit_aucun_evenement():
    assert evenements_depuis_step({}) == ()
    assert evenements_depuis_step({"etape": ""}) == ()


def test_le_journal_publie_ses_etapes_en_evenements():
    """Un vrai `RunJournal` branché sur le pont : consigner ⇒ publier (sans Redis)."""
    captes = []
    handler = activer_publication(captes.append)
    try:
        journal = RunJournal(run_id="run-42")
        journal.consigne(etape="t1", nom="Implémenter", agent="developpeur",
                         role="Développeur", statut="terminee", entree="e",
                         sortie="s", usage=StepUsage(cout_usd=0.05))
    finally:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)

    (event,) = captes
    assert event.type == EVENEMENT_TACHE_STATUT
    assert event.run_id == "run-42" and event.tache_id == "t1"
    assert event.cout_usd == pytest.approx(0.05)


# --------------------------------------------------------- Bus & modèle


def test_evenement_aller_retour_json():
    event = _statut_tache("t1", "terminee", cout_usd=1.5)
    assert Event.from_json(event.to_json()) == event
    assert Event.from_json(event.to_json().encode("utf-8")) == event


def test_bus_memoire_plusieurs_abonnes():
    """Chaque abonné du bus voit tous les événements, dans l'ordre de publication."""

    async def scenario():
        bus = InMemoryEventBus()
        premiers, seconds = [], []

        async def ecoute(destination, nombre):
            async for event in bus.subscribe():
                destination.append(event)
                if len(destination) == nombre:
                    return

        async with asyncio.TaskGroup() as tg:
            t1 = tg.create_task(ecoute(premiers, 2))
            t2 = tg.create_task(ecoute(seconds, 2))
            await asyncio.sleep(0)  # laisse les abonnements se poser
            await bus.publish(_statut_tache("a", "en_cours"))
            await bus.publish(_statut_tache("a", "terminee"))
            await t1
            await t2

        assert [e.statut for e in premiers] == ["en_cours", "terminee"]
        assert premiers == seconds

    asyncio.run(scenario())
