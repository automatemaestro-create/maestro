"""Tests d'API du backend Control Tower — REST + WebSocket (ticket #46).

Aucun Redis ni réseau requis : l'app FastAPI tourne sur le **bus mémoire**
(`InMemoryEventBus`) via le TestClient de Starlette — REST, WebSocket et pompe
de diffusion sont ceux de la production, seul le transport change. Couvre les
critères d'acceptation du ticket #46 :

① endpoints REST : liste des tâches (statut, agent, coût), état des agents,
   détail d'une exécution (404 sur un run inconnu) — et le **grand livre** d'un
   run (#57, tests différés du parent #49 → #59) : coût par tâche (tokens,
   coût estimé, durée), part de planification et agrégat, même convention
   d'attribution que la comptabilité du journal (#55) ;
② flux WebSocket : les événements publiés sur le bus (statut de tâche,
   activité agent, message inter-agents) arrivent en JSON aux clients, et
   l'état REST est déjà à jour quand l'événement est reçu (pompe : état
   d'abord, diffusion ensuite) ;
③ réassignation manuelle d'une tâche (support du Kanban) : mise à jour de la
   projection, diffusion aux clients WebSocket, 404/422 sur tâche ou agent
   inconnus ;
④ pont télémétrie → événements : une ligne de journal (#8) devient
   l'événement attendu, y compris via un vrai `RunJournal` ;
⑤ validations humaines (#48) : la demande publiée par le moteur apparaît avec
   son contexte (agent, tâche, action, justification), la décision prise via
   l'API fait reprendre (ou annule) le validateur en attente — sans time-out —
   et une demande ne se tranche qu'une fois (409 ensuite) ;
⑥ playbooks versionnés (#76, tests différés du parent #74 → #75) : lecture des
   playbooks du catalogue (défaut du code tant que jamais édité), publication
   d'une nouvelle version, historique consultable, retour arrière par
   republication — et les refus : agent hors catalogue (404, sans création de
   dossier orphelin), contenu vide (422), version inconnue (404).
"""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    ValidateurControlTower,
    activer_publication,
    create_app,
    evenements_depuis_step,
)
from maestro.controltower.validation import evenement_demande
from maestro.engine.guardrails import DemandeValidation
from maestro.telemetry import LOGGER_NAME, RunCost, RunJournal, StepUsage


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
        "usage": None,  # aucune mesure détaillée rapportée (#57)
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


# ------------------------------------------- ①bis Grand livre d'un run (#57)


def test_la_tache_expose_sa_mesure_detaillee(client, state):
    """La carte Kanban porte le coût détaillé (#57) : tokens, coût, durée."""
    state.appliquer(Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t1",
        agent="developpeur", role="Développeur", statut="terminee", cout_usd=0.3,
        usage=StepUsage(appels=1, tokens_entree=500, tokens_sortie=40,
                        cout_usd=0.3, duree_ms=2000),
    ))

    (tache,) = client.get("/api/taches").json()

    assert tache["cout_usd"] == pytest.approx(0.3)
    assert tache["usage"]["tokens_total"] == 540
    assert tache["usage"]["duree_ms"] == 2000


def test_grand_livre_d_une_execution(client, state):
    """`/executions/{run_id}/cout` : planification, coût par tâche, agrégat (MVP n°6)."""
    state.appliquer(Event(
        type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1",
        agent="orchestrateur", role="Orchestrateur", statut="terminee",
        usage=StepUsage(appels=1, tokens_entree=200, tokens_sortie=20, cout_usd=0.1),
    ))
    state.appliquer(Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t1",
        titre="Implémenter", agent="developpeur", role="Développeur",
        statut="terminee", cout_usd=0.4,
        usage=StepUsage(appels=2, tokens_entree=1000, tokens_sortie=150,
                        cout_usd=0.4, duree_ms=1200),
    ))

    cout = client.get("/api/executions/run-1/cout").json()

    assert cout["run_id"] == "run-1"
    assert cout["planification"]["cout_usd"] == pytest.approx(0.1)
    (t1,) = cout["taches"]
    assert t1["tache_id"] == "t1" and t1["agent"] == "developpeur"
    assert t1["usage"]["tokens_entree"] == 1000 and t1["usage"]["duree_ms"] == 1200
    assert cout["total"]["cout_usd"] == pytest.approx(0.5)
    assert cout["total"]["tokens_total"] == 1370


def test_grand_livre_execution_inconnue_404(client):
    assert client.get("/api/executions/nulle-part/cout").status_code == 404


def test_grand_livre_rattache_les_annexes_a_leur_tache(client, state):
    """Validation (#48) et message (#44) fusionnent dans leur tâche ; un événement
    qui ne rapporte qu'un `cout_usd` (producteur minimaliste) compte pour ce coût."""
    state.appliquer(_statut_tache("t1", "terminee", cout_usd=0.2))  # sans mesure détaillée
    state.appliquer(Event(
        type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1", tache_id="t1",
        agent="devops", role="DevOps / SRE", statut="approuve",
        usage=StepUsage(appels=1, cout_usd=0.05),
    ))
    state.appliquer(Event(
        type=EVENEMENT_MESSAGE_INTER_AGENTS, run_id="run-1", tache_id="t1",
        agent="qa", role="QA / Testeur", detail="handoff",
        usage=StepUsage(cout_usd=0.01),
    ))

    cout = client.get("/api/executions/run-1/cout").json()

    (t1,) = cout["taches"]
    assert t1["usage"]["cout_usd"] == pytest.approx(0.26)
    # L'identité de la tâche vient de son événement `tache.statut`, pas des annexes.
    assert t1["agent"] == "developpeur" and t1["statut"] == "terminee"
    assert cout["total"]["cout_usd"] == pytest.approx(0.26)


def test_grand_livre_sans_mesure_rapportee(client, state):
    """Aucune télémétrie : la tâche n'a pas d'entrée comptable, le total reste inconnu."""
    state.appliquer(_statut_tache("t1", "en_cours"))

    cout = client.get("/api/executions/run-1/cout").json()

    assert cout["taches"] == []
    assert cout["total"]["cout_usd"] is None  # inconnu, pas nul


def test_le_grand_livre_api_retombe_sur_la_comptabilite_du_journal(client, state):
    """Même convention des deux côtés (#55/#57) : le journal projeté en événements
    donne, via l'API, exactement le grand livre de `RunCost.depuis_journal`."""
    journal = RunJournal(run_id="run-9")
    journal.consigne(etape="planification", nom="Planification de l'objectif",
                     agent="orchestrateur", role="Orchestrateur", statut="terminee",
                     entree="objectif", sortie="2 tâche(s) planifiée(s)",
                     usage=StepUsage(appels=1, tokens_entree=300, tokens_sortie=30,
                                     cout_usd=0.05))
    journal.consigne(etape="t1", nom="Implémenter", agent="developpeur",
                     role="Développeur", statut="terminee", entree="e", sortie="s",
                     usage=StepUsage(appels=2, tokens_entree=1000, tokens_sortie=100,
                                     cout_usd=0.3, duree_ms=1000))
    journal.consigne(etape="t1:validation", nom="Validation — Implémenter",
                     agent="devops", role="DevOps / SRE", statut="approuve",
                     entree="e", sortie="s", usage=StepUsage())
    for record in journal.records:
        for event in evenements_depuis_step(record.to_dict()):
            state.appliquer(event)

    cout = client.get("/api/executions/run-9/cout").json()

    assert cout == RunCost.depuis_journal(journal).to_dict()


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
    """Le POST du Kanban réassigne la tâche, les fiches agents suivent, les WebSocket le voient."""
    state.appliquer(_statut_tache("t1", "en_cours", agent="qa", role="QA / Testeur"))

    with client.websocket_connect("/ws/evenements") as ws:
        reponse = client.post("/api/taches/t1/reassigner", json={"agent": "bdd"})
        recu = ws.receive_json()

    assert reponse.status_code == 200
    assert reponse.json()["agent"] == "bdd"
    assert reponse.json()["statut"] == "assignee"
    assert recu["type"] == EVENEMENT_TACHE_REASSIGNATION
    assert recu["tache_id"] == "t1" and recu["agent"] == "bdd"

    tache = client.get("/api/taches").json()[0]
    assert tache["agent"] == "bdd" and tache["role"] == "Base de données"

    # Les fiches agents suivent (#52). À ce stade l'événement a été appliqué
    # deux fois (endpoint puis pompe) : les assertions valent aussi idempotence.
    par_nom = {a["nom"]: a for a in client.get("/api/agents").json()}
    assert par_nom["qa"]["statut"] == "libre" and par_nom["qa"]["tache_courante"] == ""
    assert par_nom["bdd"]["statut"] == "occupe"
    assert par_nom["bdd"]["tache_courante"] == "t1"


def test_reassignation_tache_inconnue_404(client):
    reponse = client.post("/api/taches/fantome/reassigner", json={"agent": "qa"})
    assert reponse.status_code == 404


def test_reassignation_agent_inconnu_422(client, state):
    state.appliquer(_statut_tache("t1", "echec"))
    reponse = client.post("/api/taches/t1/reassigner", json={"agent": "stagiaire"})
    assert reponse.status_code == 422


def test_reassignation_ne_libere_pas_un_agent_passe_a_autre_chose(state):
    """L'agent d'origine occupé sur une **autre** tâche n'est pas libéré à tort."""
    state.appliquer(_statut_tache("t1", "echec", agent="qa", role="QA / Testeur"))
    state.appliquer(_statut_tache("t2", "en_cours", agent="qa", role="QA / Testeur"))

    state.appliquer(Event(
        type=EVENEMENT_TACHE_REASSIGNATION, tache_id="t1",
        agent="bdd", role="Base de données", statut="assignee",
    ))

    assert state.agent("qa").statut == "occupe"
    assert state.agent("qa").tache_courante == "t2"
    assert state.agent("bdd").statut == "occupe"
    assert state.agent("bdd").tache_courante == "t1"


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


def test_la_ligne_de_journal_embarque_la_mesure_complete():
    """L'événement porte la mesure d'usage entière (#57), pas le seul raccourci `cout_usd`."""
    record = {
        "run_id": "run-7", "etape": "t1", "nom": "Implémenter",
        "agent": "developpeur", "role": "Développeur", "statut": "terminee",
        "usage": {"appels": 2, "tokens_entree": 100, "tokens_sortie": 10,
                  "cout_usd": 0.12, "duree_ms": 1500},
    }

    (event,) = evenements_depuis_step(record)

    assert event.usage == StepUsage(appels=2, tokens_entree=100, tokens_sortie=10,
                                    cout_usd=0.12, duree_ms=1500)
    assert event.cout_usd == pytest.approx(0.12)  # le raccourci scalaire reste posé


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


# ------------------------------------------------- ⑤ Validations humaines


def _demande_evenement(tache_id="t-sensible", **surcharge):
    """Événement `validation.demande` prêt à l'emploi pour les scénarios."""
    defauts = dict(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=tache_id,
        titre="Déployer en production",
        agent="devops",
        role="DevOps / SRE",
        statut=VALIDATION_EN_ATTENTE,
        detail="mot sensible « deploi » détecté dans la tâche",
        description="Déployer l'API du mini-CRM sur l'environnement de production.",
    )
    defauts.update(surcharge)
    return Event(**defauts)


def _demande_moteur(task_id="t-sensible"):
    """La `DemandeValidation` telle que le moteur la construit (#9)."""
    return DemandeValidation(
        task_id=task_id,
        titre="Déployer en production",
        description="Déployer l'API du mini-CRM sur l'environnement de production.",
        agent="devops",
        role="DevOps / SRE",
        raison="mot sensible « deploi » détecté dans la tâche",
    )


def _attend_que(condition, timeout_s=5.0):
    """Attend (côté thread de test) que `condition()` rende une valeur vraie."""
    import time

    fin = time.monotonic() + timeout_s
    while time.monotonic() < fin:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(0.01)
    raise AssertionError("condition non remplie avant le time-out du test")


def test_demande_de_validation_apparait_avec_son_contexte(client, state):
    """La demande expose tout ce qu'il faut pour trancher (critère #48 n°2)."""
    state.appliquer(_demande_evenement())

    (validation,) = client.get("/api/validations").json()

    assert validation["statut"] == VALIDATION_EN_ATTENTE
    assert validation["tache_id"] == "t-sensible"
    assert validation["titre"] == "Déployer en production"
    assert validation["agent"] == "devops" and validation["role"] == "DevOps / SRE"
    assert "deploi" in validation["raison"]  # la justification de la classification
    assert "production" in validation["description"]  # l'action demandée


def test_decision_met_a_jour_l_etat_et_diffuse(client, bus, state):
    """La décision part vers les clients WebSocket (donc le moteur) et le REST."""
    state.appliquer(_demande_evenement())

    with client.websocket_connect("/ws/evenements") as ws:
        reponse = client.post(
            "/api/validations/t-sensible/decision", json={"approuve": True}
        )
        recu = ws.receive_json()

    assert reponse.status_code == 200
    assert recu["type"] == EVENEMENT_VALIDATION_DECISION
    assert recu["tache_id"] == "t-sensible"
    assert recu["statut"] == VALIDATION_APPROUVEE

    (validation,) = client.get("/api/validations").json()
    assert validation["statut"] == VALIDATION_APPROUVEE
    assert validation["decision"] == "approuvée depuis la Control Tower"


def test_decision_refus(client, state):
    state.appliquer(_demande_evenement())
    client.post("/api/validations/t-sensible/decision", json={"approuve": False})
    (validation,) = client.get("/api/validations").json()
    assert validation["statut"] == VALIDATION_REFUSEE
    assert validation["decision"] == "refusée depuis la Control Tower"


def test_decision_sans_demande_404(client):
    reponse = client.post("/api/validations/fantome/decision", json={"approuve": True})
    assert reponse.status_code == 404


def test_demande_deja_tranchee_409(client, state):
    """Jamais deux décisions : la première fait foi, la seconde est refusée."""
    state.appliquer(_demande_evenement())
    premier = client.post("/api/validations/t-sensible/decision", json={"approuve": False})
    second = client.post("/api/validations/t-sensible/decision", json={"approuve": True})

    assert premier.status_code == 200 and second.status_code == 409
    (validation,) = client.get("/api/validations").json()
    assert validation["statut"] == VALIDATION_REFUSEE  # la décision n'a pas bougé


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [(VALIDATION_APPROUVEE, True), (VALIDATION_REFUSEE, False)],
)
def test_validateur_rend_la_decision_humaine(statut, attendu):
    """Le validateur publie la demande puis attend la décision qui le vise."""

    async def scenario():
        bus = InMemoryEventBus()
        validateur = ValidateurControlTower(bus)
        demandes = []

        async def decideur():
            async for event in bus.subscribe():
                if event.type != EVENEMENT_VALIDATION_DEMANDE:
                    continue
                demandes.append(event)
                # Une décision visant une autre tâche doit être ignorée.
                await bus.publish(Event(
                    type=EVENEMENT_VALIDATION_DECISION, tache_id="autre",
                    statut=VALIDATION_APPROUVEE,
                ))
                await bus.publish(Event(
                    type=EVENEMENT_VALIDATION_DECISION, tache_id="t-sensible",
                    statut=statut,
                ))
                return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(decideur())
            await asyncio.sleep(0)  # laisse l'abonnement du décideur se poser
            resultat = await validateur(_demande_moteur())

        assert resultat is attendu
        (demande,) = demandes
        assert demande.tache_id == "t-sensible"
        assert demande.statut == VALIDATION_EN_ATTENTE
        assert demande.description  # le contexte voyage avec la demande

    asyncio.run(scenario())


def test_validateur_expurge_les_secrets_de_la_demande():
    """Ce qui part sur le bus est montrable — même filet que le journal (#8)."""
    demande = _demande_moteur()
    demande = DemandeValidation(
        task_id=demande.task_id, titre=demande.titre,
        description="Déployer avec la clé sk-ant-abcdefghijklmnop.",
        agent=demande.agent, role=demande.role, raison=demande.raison,
    )
    event = evenement_demande(demande)
    assert "sk-ant-" not in event.description
    assert "[secret masqué]" in event.description


def test_bout_en_bout_pause_ui_decision_reprise(client, bus):
    """Critères #48 : la tâche attend, la demande est dans l'UI, la décision la libère."""
    validateur = ValidateurControlTower(bus)
    attente = client.portal.start_task_soon(validateur, _demande_moteur())

    # La demande publiée par le moteur est projetée puis servie par le REST.
    validations = _attend_que(lambda: client.get("/api/validations").json())
    assert validations[0]["statut"] == VALIDATION_EN_ATTENTE
    assert not attente.done()  # la tâche est bien en pause, sans time-out

    reponse = client.post(
        "/api/validations/t-sensible/decision", json={"approuve": True}
    )

    assert reponse.status_code == 200
    assert attente.result(timeout=5) is True  # le moteur reprend la tâche


# ------------------------------------------------- ⑥ Playbooks versionnés (#76)


@pytest.fixture()
def depot(tmp_path):
    """Dépôt versionné vierge injecté dans l'app — jamais le `core/playbooks/` réel."""
    return PlaybookStore(tmp_path / "playbooks")


@pytest.fixture()
def client_pb(bus, state, depot):
    """TestClient de l'app branchée sur le dépôt temporaire."""
    with TestClient(create_app(bus=bus, state=state, playbooks=depot)) as client:
        yield client


def test_la_liste_expose_les_playbooks_du_catalogue(client_pb):
    """Tous les rôles éditables apparaissent, au défaut du code tant que rien n'est publié."""
    fiches = client_pb.get("/api/playbooks").json()

    assert {f["agent"] for f in fiches} == set(PLAYBOOK_DEFAUTS)
    for fiche in fiches:
        assert fiche["source"] == "defaut"
        assert fiche["version"] == 0 and fiche["nb_versions"] == 0
        assert fiche["cree_le"] is None
        assert "contenu" not in fiche  # métadonnées seules sur la liste


def test_le_playbook_jamais_edite_rend_le_prompt_du_code(client_pb):
    fiche = client_pb.get("/api/playbooks/developpeur").json()

    assert fiche["source"] == "defaut" and fiche["version"] == 0
    assert fiche["role"] == PLAYBOOK_DEFAUTS["developpeur"].role
    assert fiche["contenu"] == PLAYBOOK_DEFAUTS["developpeur"].contenu


def test_publier_puis_relire_une_nouvelle_version(client_pb):
    reponse = client_pb.put(
        "/api/playbooks/developpeur", json={"contenu": "Consignes éditées."}
    )

    assert reponse.status_code == 200
    assert reponse.json()["version"] == 1

    fiche = client_pb.get("/api/playbooks/developpeur").json()
    assert fiche["source"] == "stockage" and fiche["version"] == 1
    assert fiche["contenu"] == "Consignes éditées."
    assert fiche["cree_le"]


def test_l_historique_et_les_versions_passees_sont_consultables(client_pb):
    client_pb.put("/api/playbooks/qa", json={"contenu": "Consignes v1."})
    client_pb.put("/api/playbooks/qa", json={"contenu": "Consignes v2."})

    versions = client_pb.get("/api/playbooks/qa/versions").json()

    # L'historique, de la première à la courante — métadonnées seules.
    assert [v["version"] for v in versions] == [1, 2]
    assert all("contenu" not in v for v in versions)
    # Une version passée se lit avec son contenu, sans devenir la courante.
    passee = client_pb.get("/api/playbooks/qa/versions/1").json()
    assert passee["contenu"] == "Consignes v1."
    assert client_pb.get("/api/playbooks/qa").json()["version"] == 2


def test_restaurer_republie_une_version_passee(client_pb):
    client_pb.put("/api/playbooks/bdd", json={"contenu": "Consignes v1."})
    client_pb.put("/api/playbooks/bdd", json={"contenu": "Consignes v2."})

    reponse = client_pb.post("/api/playbooks/bdd/restaurer", json={"version": 1})

    # Le retour arrière (EF-25) crée une version de plus, rien n'est réécrit.
    assert reponse.status_code == 200
    assert reponse.json()["version"] == 3
    fiche = client_pb.get("/api/playbooks/bdd").json()
    assert fiche["version"] == 3 and fiche["contenu"] == "Consignes v1."
    versions = client_pb.get("/api/playbooks/bdd/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]


def test_un_agent_hors_catalogue_est_refuse_sans_dossier_orphelin(client_pb, depot):
    """Une faute de frappe dans l'URL ne crée jamais de playbook orphelin."""
    assert client_pb.get("/api/playbooks/stagiaire").status_code == 404
    assert client_pb.put(
        "/api/playbooks/stagiaire", json={"contenu": "x"}
    ).status_code == 404
    assert client_pb.get("/api/playbooks/stagiaire/versions").status_code == 404
    assert client_pb.post(
        "/api/playbooks/stagiaire/restaurer", json={"version": 1}
    ).status_code == 404
    assert not (depot.racine / "stagiaire").exists()


def test_un_contenu_vide_est_refuse_en_422(client_pb):
    reponse = client_pb.put("/api/playbooks/developpeur", json={"contenu": "  \n"})

    assert reponse.status_code == 422
    assert client_pb.get("/api/playbooks/developpeur").json()["version"] == 0


def test_une_version_inconnue_rend_404(client_pb):
    client_pb.put("/api/playbooks/qa", json={"contenu": "Consignes v1."})

    assert client_pb.get("/api/playbooks/qa/versions/9").status_code == 404
    assert client_pb.post(
        "/api/playbooks/qa/restaurer", json={"version": 9}
    ).status_code == 404
    # Le refus n'a rien publié : l'historique n'a pas bougé.
    assert client_pb.get("/api/playbooks/qa").json()["version"] == 1


# --------------------------------------------------------- Bus & modèle


def test_evenement_aller_retour_json():
    event = _statut_tache("t1", "terminee", cout_usd=1.5)
    assert Event.from_json(event.to_json()) == event
    assert Event.from_json(event.to_json().encode("utf-8")) == event


def test_evenement_avec_usage_aller_retour_json():
    """La mesure d'usage (#57) voyage en JSON sans perte (`StepUsage.from_dict`)."""
    event = Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t1",
        statut="terminee", cout_usd=0.3,
        usage=StepUsage(appels=1, tokens_entree=10, tokens_sortie=2,
                        cout_usd=0.3, duree_ms=800, outils=("Read", "Bash")),
    )
    assert Event.from_json(event.to_json()) == event


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
