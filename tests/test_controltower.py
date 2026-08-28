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
   dossier orphelin), contenu vide (422), version inconnue (404) ;
⑦ catalogue d'agents (#72, tests différés du parent #70 → #71) : liste du
   catalogue effectif (agents du code puis personnalisés, avec provenance),
   création/modification/suppression d'un agent personnalisé — répercutées
   immédiatement sur la vue `GET /api/agents` — et les refus : nom déjà pris
   (409), agent par défaut intouchable (403), définition invalide (422), nom
   inconnu ou hors slug (404, sans fichier orphelin). Le dépôt et le catalogue
   effectif eux-mêmes (routage, exécution) sont couverts dans
   `tests/test_agent_store.py` ;
⑦bis volet MCP des fiches catalogue (#104, tests différés du parent #101 →
   #103) : serveurs déclarés exposés en lecture seule (valeurs de secrets
   masquées, références `${VAR}` visibles), volet vide sans déclaration,
   déclaration invalide rendue avec sa cause (`mcp_erreur`) sans casser la
   fiche ni le listing. Le socle lui-même (dépôt, résolution, montage par le
   moteur, couture SDK) est couvert dans `tests/test_mcp.py` ;
⑦ter la forge du produit (#412) — seuls tests de ce fichier à lire le
   `core/mcp/` **réel** : ce ne sont pas des tests d'API mais l'épinglage
   d'une décision versionnée. `qa.json` est un **défaut** et suit la forge du
   projet (GitHub, optionnel pour ne casser aucun poste) ; le registre curé
   est une **bibliothèque** et garde GitLab *à côté de* GitHub ; les deux
   disent la même chose de GitHub — c'est cette divergence-là que le ticket
   visait ;
⑧ chat utilisateur ↔ agent (#84, tests différés du parent #82 → #83) : fil
   vide tant que l'agent n'a jamais été contacté, envoi d'un message rendant
   la paire message/réponse, fil persisté (relu par le GET, survivant à un
   redémarrage de l'app), exposition temps réel (chaque message part en
   `chat.message` sur le WebSocket, réponse comprise), chat avec un agent
   personnalisé du catalogue — et les refus : agent hors catalogue (404, sans
   fil orphelin), message vide (422), réponse indisponible (502, le message
   utilisateur restant acquis et diffusé). Depuis #482 (couverture due au lot
   final #485) un message peut porter des **sources** : le 201 rend leur rapport
   de lecture, un refus est un **422 motivé** (`{motif, message, index}`) qui ne
   persiste rien, et le REST ne rapatrie jamais le contenu extrait. Le canal
   lui-même (persistance, répondeurs, messagerie, chaîne d'ingestion) est couvert
   dans `tests/test_chat.py` ;
⑨ vue coûts & analytics (#87) : agrégats transverses par tâche (cumul des
   re-tentatives), par agent (planification comprise) et par exécution, série
   temporelle en seaux (minute/heure/jour), fenêtre `depuis` (période
   sélectionnable), cohérence avec les grands livres (#57) — et les refus :
   `pas` ou `depuis` invalides (422).
"""

import asyncio
import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import DEFAULT_AGENTS
from maestro.agents.mcp import IntegrationMcp, McpStore, ServeurMcp
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_CHAT_MESSAGE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    UTILISATEUR,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ChatStore,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RepondeurChat,
    RepondeurScripte,
    ValidateurControlTower,
    activer_publication,
    create_app,
    evenements_depuis_step,
)
from maestro.controltower.chat import AUTEUR_AGENT, AUTEUR_UTILISATEUR
from maestro.controltower.validation import evenement_demande
from maestro.engine.guardrails import DemandeValidation
from maestro.sources import DepotTeleversements
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

    taches = client.get("/api/taches?projet=tous").json()

    assert [t["id"] for t in taches] == ["t1", "t2"]
    t1, t2 = taches
    assert t1["statut"] == "en_cours" and t1["agent"] == "developpeur"
    assert t1["cout_usd"] is None  # aucun coût rapporté : inconnu, pas nul
    assert t2 == {
        "id": "t2", "titre": "Relire", "statut": "terminee", "agent": "qa",
        "role": "QA / Testeur", "run_id": "run-1", "cout_usd": 0.25,
        "usage": None,  # aucune mesure détaillée rapportée (#57)
        "ticket": None,  # aucune référence de ticket externe transportée (#183)
        "projet_id": None,  # la tâche ne relève d'aucun projet (#222)
        # Le détail de la tâche (#246) : absent ici, donc `null` et listes vides
        # — le panneau de détail (#251) ne s'ouvre pas et la carte est celle
        # d'avant le lot. Ces trois clés sont *toujours* servies, un client qui
        # les ignore n'étant pas cassé pour autant.
        "description": None,
        "etapes": [],
        "liens": [],
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


def test_un_agent_multi_instances_ne_se_libere_qu_a_la_derniere_tache(client, state):
    """Deux instances au travail (#100) : la fiche porte les deux tâches, sans mélange.

    L'issue d'une instance ne libère que son créneau — l'agent reste occupé tant
    que l'autre travaille, et `tache_courante` reste la plus récemment démarrée
    encore en vol (compatibilité mono-instance).
    """
    state.appliquer(_statut_tache("t1", "en_cours"))
    state.appliquer(_statut_tache("t2", "en_cours"))
    dev = {a["nom"]: a for a in client.get("/api/agents").json()}["developpeur"]
    assert dev["statut"] == "occupe"
    assert dev["taches_en_cours"] == ["t1", "t2"]
    assert dev["tache_courante"] == "t2"

    state.appliquer(_statut_tache("t1", "terminee", cout_usd=0.3))
    dev = {a["nom"]: a for a in client.get("/api/agents").json()}["developpeur"]
    assert dev["statut"] == "occupe"  # l'autre instance travaille encore
    assert dev["taches_en_cours"] == ["t2"]
    assert dev["taches_terminees"] == 1

    state.appliquer(_statut_tache("t2", "echec", cout_usd=0.2))
    dev = {a["nom"]: a for a in client.get("/api/agents").json()}["developpeur"]
    assert dev["statut"] == "libre"
    assert dev["taches_en_cours"] == [] and dev["tache_courante"] == ""
    assert dev["taches_terminees"] == 1 and dev["taches_echouees"] == 1
    assert dev["cout_usd"] == pytest.approx(0.5)


def test_le_redemarrage_d_une_tache_n_occupe_qu_un_creneau(state):
    """Une relance (#91) rejoue `en_cours` pour la même tâche : un seul créneau occupé."""
    state.appliquer(_statut_tache("t1", "en_cours"))
    state.appliquer(_statut_tache("t1", "en_cours"))  # redémarrage après relance

    assert state.agent("developpeur").taches_en_cours == ["t1"]

    state.appliquer(_statut_tache("t1", "terminee"))
    assert state.agent("developpeur").statut == "libre"


def test_tache_bloquee_sans_executant_ne_touche_pas_les_agents(client, state):
    """Une tâche bloquée (#43, agent « — ») apparaît sans créer d'agent fantôme."""
    state.appliquer(_statut_tache("t9", "bloquee", agent="—", role="non exécutée"))
    assert client.get("/api/taches?projet=tous").json()[0]["statut"] == "bloquee"
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

    (tache,) = client.get("/api/taches?projet=tous").json()

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


def test_le_grand_livre_survit_au_redemarrage_de_l_api():
    """Persistance #97 (critère #93) : les grands livres sont reconstruits au redémarrage.

    Un run passé a laissé sa trace dans le **journal durable** (`EventLog`). Une API
    qui redémarre repart sur une projection **vide**, puis **rejoue** le journal au
    démarrage (lifespan) : exécutions, tâches et grand livre réapparaissent à
    l'identique — sans quoi seuls les artefacts JSON du moteur subsisteraient (docs/13
    §5, réserve 2). Un journal mémoire pré-rempli tient lieu de « session précédente ».
    """
    journal = InMemoryEventLog()
    evenements = [
        Event(type=EVENEMENT_AGENT_ACTIVITE, run_id="run-persistant",
              agent="orchestrateur", role="Orchestrateur", statut="terminee",
              usage=StepUsage(appels=1, tokens_entree=200, tokens_sortie=20, cout_usd=0.1)),
        Event(type=EVENEMENT_TACHE_STATUT, run_id="run-persistant", tache_id="t1",
              titre="Schéma", agent="bdd", role="Base de données", statut="terminee",
              cout_usd=0.4,
              usage=StepUsage(appels=2, tokens_entree=1000, tokens_sortie=150,
                              cout_usd=0.4, duree_ms=900)),
        Event(type=EVENEMENT_TACHE_STATUT, run_id="run-persistant", tache_id="t2",
              titre="API", agent="developpeur", role="Développeur", statut="terminee",
              cout_usd=0.2,
              usage=StepUsage(appels=1, tokens_entree=500, tokens_sortie=60,
                              cout_usd=0.2, duree_ms=400)),
    ]

    async def _consigner():
        for event in evenements:
            await journal.consigner(event)

    asyncio.run(_consigner())  # la « session précédente » a persisté ses événements

    # Redémarrage : app neuve, projection vide, MÊME journal → rejeu au démarrage.
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=journal)
    with TestClient(app) as redemarree:
        taches = {t["id"] for t in redemarree.get("/api/taches?projet=tous").json()}
        cout = redemarree.get("/api/executions/run-persistant/cout").json()

    assert taches == {"t1", "t2"}  # les tâches du run passé ont resurgi
    assert cout["planification"]["cout_usd"] == pytest.approx(0.1)
    assert {t["tache_id"] for t in cout["taches"]} == {"t1", "t2"}
    assert cout["total"]["cout_usd"] == pytest.approx(0.7)  # 0.1 + 0.4 + 0.2 rejoués


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


# ----------------------------------------- ①ter Vue coûts & analytics (#87)


def _usage(cout_usd, *, entree=100, sortie=10, appels=1):
    """Mesure d'usage compacte pour les scénarios analytics."""
    return StepUsage(appels=appels, tokens_entree=entree, tokens_sortie=sortie,
                     cout_usd=cout_usd)


@pytest.fixture()
def state_analytics(state):
    """Deux exécutions datées : planification, tâche re-tentée, agents distincts."""
    state.appliquer(Event(
        type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1",
        agent="orchestrateur", role="Orchestrateur",
        usage=_usage(0.10, entree=300, sortie=30),
        horodatage="2026-07-14T10:00:05+00:00",
    ))
    state.appliquer(Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t1",
        titre="Implémenter", agent="developpeur", role="Développeur",
        statut="terminee", usage=_usage(0.40, entree=1000, sortie=150, appels=2),
        horodatage="2026-07-14T10:20:00+00:00",
    ))
    state.appliquer(Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t2",
        titre="Vérifier", agent="qa", role="QA / Testeur",
        statut="terminee", usage=_usage(0.05, entree=200, sortie=20),
        horodatage="2026-07-14T10:45:00+00:00",
    ))
    # Re-tentative de t1 dans un second run, une heure plus tard.
    state.appliquer(Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-2", tache_id="t1",
        titre="Implémenter", agent="developpeur", role="Développeur",
        statut="echec", usage=_usage(0.20, entree=500, sortie=50),
        horodatage="2026-07-14T11:30:00+00:00",
    ))
    return state


def test_analytics_agrege_par_tache_agent_et_execution(client, state_analytics):
    """`/api/analytics/couts` : agrégats transverses par tâche, agent, exécution."""
    vue = client.get("/api/analytics/couts?projet=tous").json()

    assert vue["total"]["cout_usd"] == pytest.approx(0.75)
    assert vue["total"]["tokens_total"] == 2250

    # Par exécution : ordre chronologique, bornes et tâches distinctes du run.
    r1, r2 = vue["executions"]
    assert r1["run_id"] == "run-1" and r1["nb_taches"] == 2
    assert r1["debut"] == "2026-07-14T10:00:05+00:00"
    assert r1["fin"] == "2026-07-14T10:45:00+00:00"
    assert r1["usage"]["cout_usd"] == pytest.approx(0.55)
    assert r2["run_id"] == "run-2" and r2["usage"]["cout_usd"] == pytest.approx(0.20)

    # Par agent : tri par coût décroissant, planification comprise (orchestrateur).
    assert [a["agent"] for a in vue["agents"]] == ["developpeur", "orchestrateur", "qa"]
    dev = vue["agents"][0]
    assert dev["usage"]["cout_usd"] == pytest.approx(0.60) and dev["taches"] == 1
    assert vue["agents"][1]["taches"] == 0  # planification : hors tâche

    # Par tâche : t1 cumule ses deux runs, l'identité suit le dernier statut vu.
    t1 = next(t for t in vue["taches"] if t["tache_id"] == "t1")
    assert t1["executions"] == 2 and t1["statut"] == "echec"
    assert t1["usage"]["cout_usd"] == pytest.approx(0.60)


def test_analytics_serie_temporelle_selon_le_pas(client, state_analytics):
    """La série regroupe l'usage en seaux du `pas` demandé (minute/heure/jour)."""
    vue = client.get("/api/analytics/couts?projet=tous").json()  # pas par défaut : heure
    assert [p["periode"] for p in vue["serie"]] == [
        "2026-07-14T10:00:00+00:00", "2026-07-14T11:00:00+00:00",
    ]
    assert vue["serie"][0]["usage"]["cout_usd"] == pytest.approx(0.55)

    par_jour = client.get("/api/analytics/couts", params={"projet": "tous", "pas": "jour"}).json()
    (seau,) = par_jour["serie"]
    assert seau["periode"] == "2026-07-14T00:00:00+00:00"
    assert seau["usage"]["cout_usd"] == pytest.approx(0.75)

    par_minute = client.get(
        "/api/analytics/couts", params={"projet": "tous", "pas": "minute"}
    ).json()
    assert len(par_minute["serie"]) == 4  # chaque événement daté dans son seau


def test_analytics_fenetre_depuis(client, state_analytics):
    """`depuis` restreint toutes les vues à la fenêtre demandée (période sélectionnable)."""
    vue = client.get(
        "/api/analytics/couts", params={"projet": "tous", "depuis": "2026-07-14T11:00:00+00:00"}
    ).json()

    assert vue["depuis"] == "2026-07-14T11:00:00+00:00"
    assert vue["total"]["cout_usd"] == pytest.approx(0.20)
    assert [r["run_id"] for r in vue["executions"]] == ["run-2"]
    assert [a["agent"] for a in vue["agents"]] == ["developpeur"]
    (t1,) = vue["taches"]
    assert t1["executions"] == 1 and len(vue["serie"]) == 1


def test_analytics_depuis_sans_fuseau_repute_utc(client, state_analytics):
    """Un `depuis` naïf est réputé UTC — la convention des horodatages du bus."""
    vue = client.get(
        "/api/analytics/couts", params={"projet": "tous", "depuis": "2026-07-14T11:00:00"}
    ).json()
    assert vue["total"]["cout_usd"] == pytest.approx(0.20)


def test_analytics_sans_execution(client):
    """Aucune trace : vues vides et total inconnu (None, pas 0)."""
    vue = client.get("/api/analytics/couts?projet=tous").json()
    assert vue["executions"] == [] and vue["agents"] == []
    assert vue["taches"] == [] and vue["serie"] == []
    assert vue["total"]["cout_usd"] is None


def test_analytics_retombe_sur_les_grands_livres(client, state_analytics):
    """Le total analytics égale la somme des grands livres des runs (#57) :
    même convention d'attribution, aucun double comptage."""
    vue = client.get("/api/analytics/couts?projet=tous").json()
    totaux = [
        client.get(f"/api/executions/{r}/cout").json()["total"]["cout_usd"]
        for r in ("run-1", "run-2")
    ]
    assert vue["total"]["cout_usd"] == pytest.approx(sum(totaux))


def test_analytics_pas_ou_depuis_invalides_422(client):
    assert client.get(
        "/api/analytics/couts", params={"projet": "tous", "pas": "semaine"}
    ).status_code == 422
    assert client.get(
        "/api/analytics/couts", params={"projet": "tous", "depuis": "pas-une-date"}
    ).status_code == 422


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
    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        publie(client, bus, *evenements)
        recus = [ws.receive_json() for _ in evenements]

    assert recus == [e.to_dict() for e in evenements]


def test_websocket_puis_rest_l_etat_est_deja_a_jour(client, bus):
    """La pompe projette avant de diffuser : un événement reçu ⇒ un REST à jour."""
    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        publie(client, bus, _statut_tache("t1", "en_cours"))
        ws.receive_json()
        taches = client.get("/api/taches?projet=tous").json()
    assert taches[0]["id"] == "t1" and taches[0]["statut"] == "en_cours"


def test_websocket_plusieurs_clients_recoivent_chacun_tout(client, bus):
    with client.websocket_connect("/ws/evenements?projet=tous") as ws1, \
         client.websocket_connect("/ws/evenements?projet=tous") as ws2:
        publie(client, bus, _statut_tache("t1", "terminee"))
        assert ws1.receive_json()["tache_id"] == "t1"
        assert ws2.receive_json()["tache_id"] == "t1"


# ------------------------------------------------------ ③ Réassignation


def test_reassignation_manuelle_met_a_jour_et_diffuse(client, bus, state):
    """Le POST du Kanban réassigne la tâche, les fiches agents suivent, les WebSocket le voient."""
    state.appliquer(_statut_tache("t1", "en_cours", agent="qa", role="QA / Testeur"))

    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        reponse = client.post("/api/taches/t1/reassigner", json={"agent": "bdd"})
        recu = ws.receive_json()

    assert reponse.status_code == 200
    assert reponse.json()["agent"] == "bdd"
    assert reponse.json()["statut"] == "assignee"
    assert recu["type"] == EVENEMENT_TACHE_REASSIGNATION
    assert recu["tache_id"] == "t1" and recu["agent"] == "bdd"

    tache = client.get("/api/taches?projet=tous").json()[0]
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


def test_reassignation_ne_rend_que_le_creneau_de_la_tache_deplacee(state):
    """Multi-instances (#100) : réassigner une des tâches d'un agent laisse l'autre en vol."""
    state.appliquer(_statut_tache("t1", "en_cours", agent="qa", role="QA / Testeur"))
    state.appliquer(_statut_tache("t2", "en_cours", agent="qa", role="QA / Testeur"))

    state.appliquer(Event(
        type=EVENEMENT_TACHE_REASSIGNATION, tache_id="t1",
        agent="bdd", role="Base de données", statut="assignee",
    ))

    assert state.agent("qa").statut == "occupe"
    assert state.agent("qa").taches_en_cours == ["t2"]
    assert state.agent("bdd").taches_en_cours == ["t1"]


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


def test_ligne_de_relance_devient_activite_avec_sa_raison():
    """Une relance (#91) part au fil temps réel en activité d'agent, raison comprise."""
    relance = {"run_id": "r", "etape": "t3:relance", "nom": "Relance — Implémenter",
               "agent": "developpeur", "role": "Développeur", "statut": "relance",
               "entree": "aléa SDK (crash du sous-processus).",
               "sortie": "échec transitoire (tentative 1/3) : aléa SDK (crash du "
                         "sous-processus). — relance dans 2 s."}

    (activite,) = evenements_depuis_step(relance)

    assert activite.type == EVENEMENT_AGENT_ACTIVITE
    assert activite.tache_id == "t3" and activite.statut == "relance"
    assert "aléa SDK" in activite.detail and "relance" in activite.detail


def test_ligne_de_debut_devient_statut_en_cours_sans_depense():
    """Un début de tâche (#98) part au fil temps réel en `tache.statut` `en_cours`
    — agent et heure de début posés, sans usage ni coût (rien au grand livre
    avant l'issue)."""
    debut = {"run_id": "r", "etape": "t3:debut", "nom": "Implémenter",
             "agent": "developpeur", "role": "Développeur", "statut": "en_cours",
             "horodatage": "2026-07-15T10:00:00+00:00",
             "entree": "", "sortie": "démarrage de la tâche",
             "usage": StepUsage().to_dict()}

    (event,) = evenements_depuis_step(debut)

    assert event.type == EVENEMENT_TACHE_STATUT
    assert event.tache_id == "t3" and event.statut == "en_cours"
    assert event.agent == "developpeur" and event.titre == "Implémenter"
    assert event.horodatage == "2026-07-15T10:00:00+00:00"
    assert "démarrage" in event.detail
    assert event.usage is None and event.cout_usd is None


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


def test_du_debut_a_l_issue_le_kanban_suit_la_tache(client, state):
    """Bout en bout #98 : le journal du moteur, projeté en événements, fait vivre
    la colonne « En cours » — la tâche y entre à son début (agent, heure de
    début), l'agent est occupé, puis l'issue la bascule et libère l'agent."""
    journal = RunJournal(run_id="run-98")
    journal.consigne(etape="t1:debut", nom="Implémenter", agent="developpeur",
                     role="Développeur", statut="en_cours", entree="",
                     sortie="démarrage de la tâche", usage=StepUsage())
    for event in evenements_depuis_step(journal.records[-1].to_dict()):
        state.appliquer(event)

    (tache,) = client.get("/api/taches?projet=tous").json()
    assert tache["id"] == "t1" and tache["statut"] == "en_cours"
    assert tache["agent"] == "developpeur"
    assert tache["horodatage"]  # l'heure de début affichée par la carte Kanban
    assert tache["cout_usd"] is None and tache["usage"] is None  # rien avant l'issue
    agent = next(a for a in client.get("/api/agents").json() if a["nom"] == "developpeur")
    assert agent["statut"] == "occupe" and agent["tache_courante"] == "t1"

    journal.consigne(etape="t1", nom="Implémenter", agent="developpeur",
                     role="Développeur", statut="terminee", entree="e", sortie="s",
                     usage=StepUsage(appels=1, cout_usd=0.1, duree_ms=1200))
    for event in evenements_depuis_step(journal.records[-1].to_dict()):
        state.appliquer(event)

    (tache,) = client.get("/api/taches?projet=tous").json()
    assert tache["statut"] == "terminee" and tache["cout_usd"] == pytest.approx(0.1)
    agent = next(a for a in client.get("/api/agents").json() if a["nom"] == "developpeur")
    assert agent["statut"] == "libre" and agent["tache_courante"] == ""


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

    (validation,) = client.get("/api/validations?projet=tous").json()

    assert validation["statut"] == VALIDATION_EN_ATTENTE
    assert validation["tache_id"] == "t-sensible"
    assert validation["titre"] == "Déployer en production"
    assert validation["agent"] == "devops" and validation["role"] == "DevOps / SRE"
    assert "deploi" in validation["raison"]  # la justification de la classification
    assert "production" in validation["description"]  # l'action demandée


def test_decision_met_a_jour_l_etat_et_diffuse(client, bus, state):
    """La décision part vers les clients WebSocket (donc le moteur) et le REST."""
    state.appliquer(_demande_evenement())

    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        reponse = client.post(
            "/api/validations/t-sensible/decision", json={"approuve": True}
        )
        recu = ws.receive_json()

    assert reponse.status_code == 200
    assert recu["type"] == EVENEMENT_VALIDATION_DECISION
    assert recu["tache_id"] == "t-sensible"
    assert recu["statut"] == VALIDATION_APPROUVEE

    (validation,) = client.get("/api/validations?projet=tous").json()
    assert validation["statut"] == VALIDATION_APPROUVEE
    assert validation["decision"] == "approuvée depuis la Control Tower"


def test_decision_refus(client, state):
    state.appliquer(_demande_evenement())
    client.post("/api/validations/t-sensible/decision", json={"approuve": False})
    (validation,) = client.get("/api/validations?projet=tous").json()
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
    (validation,) = client.get("/api/validations?projet=tous").json()
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
    validations = _attend_que(lambda: client.get("/api/validations?projet=tous").json())
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
    assert fiche["provenance"] == "humain"  # version éditée = provenance « humain » (#111)
    assert fiche["contenu"] == "Consignes éditées."
    assert fiche["cree_le"]


def test_les_propositions_se_listent_a_part_de_la_version_courante(client_pb, depot):
    """#111 : une proposition en brouillon est listée à part et ne devient jamais courante."""
    client_pb.put("/api/playbooks/qa", json={"contenu": "Consignes courantes."})
    depot.proposer("qa", "Consignes proposées.", justification="échecs analysés : timeout.")

    propositions = client_pb.get("/api/playbooks/qa/propositions").json()
    assert len(propositions) == 1
    assert propositions[0]["provenance"] == "proposition"
    assert propositions[0]["justification"] == "échecs analysés : timeout."
    assert "contenu" not in propositions[0]  # métadonnées seules
    # La version courante et son historique ignorent la proposition.
    assert client_pb.get("/api/playbooks/qa").json()["version"] == 1
    assert client_pb.get("/api/playbooks/qa").json()["contenu"] == "Consignes courantes."
    assert len(client_pb.get("/api/playbooks/qa/versions").json()) == 1
    # Endpoint borné aux agents à playbook connus.
    assert client_pb.get("/api/playbooks/stagiaire/propositions").status_code == 404


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


# ------------------------------------------------- ⑦ Catalogue d'agents (#72)


@pytest.fixture()
def depot_agents(tmp_path):
    """Dépôt d'agents personnalisés vierge injecté dans l'app — jamais le `core/agents/` réel."""
    return AgentStore(tmp_path / "agents")


@pytest.fixture()
def client_cat(bus, depot_agents):
    """TestClient de l'app sur dépôt temporaire (état par défaut : catalogue effectif)."""
    with TestClient(create_app(bus=bus, agents_store=depot_agents)) as client:
        yield client


def _corps_agent(nom="redacteur", **overrides):
    """Corps de création valide prêt à l'emploi (un rédacteur technique)."""
    corps = {
        "nom": nom,
        "role": "Rédacteur technique",
        "competences": ["redaction", "documentation"],
        "playbook": "Tu rédiges la documentation technique demandée.",
    }
    corps.update(overrides)
    return corps


def test_le_catalogue_expose_les_agents_du_code_puis_les_personnalises(client_cat):
    client_cat.post("/api/catalogue", json=_corps_agent())

    fiches = client_cat.get("/api/catalogue").json()

    # L'ordre est celui du catalogue effectif — celui que chargent les moteurs.
    assert [f["nom"] for f in fiches[: len(DEFAULT_AGENTS)]] == [
        a.nom for a in DEFAULT_AGENTS
    ]
    assert all(f["source"] == "defaut" for f in fiches[: len(DEFAULT_AGENTS)])
    assert fiches[-1]["nom"] == "redacteur"
    assert fiches[-1]["source"] == "personnalise"
    assert all("playbook" not in f for f in fiches)  # métadonnées seules sur la liste


def test_la_fiche_d_un_agent_par_defaut_rend_sa_definition_du_code(client_cat):
    fiche = client_cat.get("/api/catalogue/developpeur").json()

    developpeur = next(a for a in DEFAULT_AGENTS if a.nom == "developpeur")
    assert fiche["source"] == "defaut" and fiche["role"] == developpeur.role
    assert fiche["playbook"] == developpeur.prompt_systeme
    assert fiche["cree_le"] is None  # la définition vit dans le code, sans dates


def test_creer_un_agent_le_rend_visible_catalogue_et_vue_agents(client_cat, depot_agents):
    reponse = client_cat.post("/api/catalogue", json=_corps_agent())

    assert reponse.status_code == 201
    fiche = reponse.json()
    assert fiche["source"] == "personnalise" and fiche["cree_le"]
    assert fiche["playbook"] == "Tu rédiges la documentation technique demandée."
    # Persisté hors du code : le dépôt porte le fichier de la définition.
    assert (depot_agents.racine / "redacteur.json").is_file()
    # Et l'agent entre immédiatement dans la vue de supervision (cible de
    # réassignation manuelle), sans attendre un redémarrage.
    par_nom = {a["nom"]: a for a in client_cat.get("/api/agents").json()}
    assert par_nom["redacteur"]["role"] == "Rédacteur technique"


def test_un_agent_persiste_est_present_des_le_demarrage(bus, depot_agents):
    depot_agents.ecrire(
        AgentDefinition(
            nom="veilleur",
            role="Veille technologique",
            competences=("veille",),
            playbook="Tu fais la veille.",
        )
    )

    with TestClient(create_app(bus=bus, agents_store=depot_agents)) as client:
        noms = {a["nom"] for a in client.get("/api/agents").json()}

    # L'état par défaut se construit sur le catalogue effectif : les agents
    # personnalisés déjà persistés sont là dès le démarrage, comme ceux du code.
    assert "veilleur" in noms


@pytest.mark.parametrize("nom", ["developpeur", "orchestrateur"])
def test_creer_sous_un_nom_reserve_est_refuse_en_409(client_cat, nom):
    reponse = client_cat.post("/api/catalogue", json=_corps_agent(nom=nom))

    assert reponse.status_code == 409


def test_creer_deux_fois_le_meme_nom_est_refuse_en_409(client_cat):
    assert client_cat.post("/api/catalogue", json=_corps_agent()).status_code == 201
    reponse = client_cat.post("/api/catalogue", json=_corps_agent())

    assert reponse.status_code == 409
    # La modification passe par PUT, pas par une recréation.
    assert "PUT /api/catalogue" in reponse.json()["detail"]


def test_creer_une_definition_invalide_est_refuse_en_422(client_cat, depot_agents):
    sans_competence = client_cat.post(
        "/api/catalogue", json=_corps_agent(competences=[])
    )
    nom_hors_slug = client_cat.post(
        "/api/catalogue", json=_corps_agent(nom="../evasion")
    )

    assert sans_competence.status_code == 422
    assert nom_hors_slug.status_code == 422
    # Aucun refus n'a rien persisté — pas de fichier orphelin dans le dépôt.
    assert depot_agents.noms() == ()


def test_modifier_remplace_la_definition_et_garde_la_date_de_creation(client_cat):
    creee = client_cat.post("/api/catalogue", json=_corps_agent()).json()

    reponse = client_cat.put(
        "/api/catalogue/redacteur",
        json={
            "role": "Rédacteur senior",
            "competences": ["redaction"],
            "playbook": "Consignes éditées.",
        },
    )

    assert reponse.status_code == 200
    fiche = client_cat.get("/api/catalogue/redacteur").json()
    assert fiche["role"] == "Rédacteur senior"
    assert fiche["playbook"] == "Consignes éditées."
    assert fiche["cree_le"] == creee["cree_le"]  # remplacement, pas re-création
    # La vue de supervision suit le nouveau rôle.
    par_nom = {a["nom"]: a for a in client_cat.get("/api/agents").json()}
    assert par_nom["redacteur"]["role"] == "Rédacteur senior"


def test_modifier_une_definition_invalide_est_refuse_en_422(client_cat):
    client_cat.post("/api/catalogue", json=_corps_agent())

    reponse = client_cat.put(
        "/api/catalogue/redacteur",
        json={"role": "Rédacteur", "competences": [], "playbook": "x"},
    )

    assert reponse.status_code == 422
    # Le refus n'a pas touché la définition en place.
    assert client_cat.get("/api/catalogue/redacteur").json()["competences"] == [
        "redaction", "documentation",
    ]


def test_supprimer_retire_l_agent_du_catalogue_et_de_la_vue(client_cat, depot_agents):
    client_cat.post("/api/catalogue", json=_corps_agent())

    reponse = client_cat.delete("/api/catalogue/redacteur")

    assert reponse.status_code == 200
    assert reponse.json() == {"nom": "redacteur", "supprime": True}
    assert depot_agents.noms() == ()
    assert client_cat.get("/api/catalogue/redacteur").status_code == 404
    noms = {a["nom"] for a in client_cat.get("/api/agents").json()}
    assert "redacteur" not in noms


def test_un_agent_par_defaut_est_intouchable_en_403(client_cat):
    """Défini par le code : ni modifiable ni supprimable ici (playbook via /api/playbooks)."""
    modification = client_cat.put(
        "/api/catalogue/developpeur",
        json={"role": "Autre", "competences": ["x"], "playbook": "x"},
    )
    suppression = client_cat.delete("/api/catalogue/developpeur")

    assert modification.status_code == 403
    assert suppression.status_code == 403
    assert "/api/playbooks" in modification.json()["detail"]


def test_un_nom_inconnu_ou_hors_slug_rend_404(client_cat, depot_agents):
    # Inconnu du dépôt, ou hors slug (une faute d'URL n'est jamais un chemin disque).
    for nom in ("stagiaire", "Redacteur", "a.b"):
        assert client_cat.get(f"/api/catalogue/{nom}").status_code == 404
        assert client_cat.put(
            f"/api/catalogue/{nom}",
            json={"role": "x", "competences": ["x"], "playbook": "x"},
        ).status_code == 404
        assert client_cat.delete(f"/api/catalogue/{nom}").status_code == 404
    assert depot_agents.noms() == ()


# ----------------------------- ⑦bis Volet MCP des fiches catalogue (#104 → #103)


@pytest.fixture()
def depot_mcp(tmp_path):
    """Dépôt de déclarations MCP vierge injecté dans l'app — jamais le `core/mcp/` réel."""
    racine = tmp_path / "mcp"
    racine.mkdir()
    return McpStore(racine)


@pytest.fixture()
def client_mcp(bus, depot_agents, depot_mcp):
    """TestClient de l'app branchée sur les dépôts temporaires (agents + MCP)."""
    with TestClient(
        create_app(bus=bus, agents_store=depot_agents, mcp=depot_mcp)
    ) as client:
        yield client


def test_la_fiche_expose_les_serveurs_mcp_declares_valeurs_masquees(client_mcp, depot_mcp):
    (depot_mcp.racine / "qa.json").write_text(
        json.dumps(
            {
                "serveurs": [
                    {
                        "nom": "tickets",
                        "type": "stdio",
                        "commande": "npx",
                        "args": ["-y", "@zereight/mcp-gitlab"],
                        "env": {
                            "GITLAB_TOKEN": "${GITLAB_TOKEN}",
                            "EN_CLAIR": "secret-pose-par-erreur",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fiche = client_mcp.get("/api/catalogue/qa").json()

    assert fiche["mcp_erreur"] is None
    (serveur,) = fiche["mcp_serveurs"]
    assert (serveur["nom"], serveur["type"]) == ("tickets", "stdio")
    # Forme publique : la référence ${VAR} reste lisible, le littéral (un secret
    # potentiel) est masqué — jamais réémis vers l'UI.
    assert serveur["env"]["GITLAB_TOKEN"] == "${GITLAB_TOKEN}"
    assert "secret-pose-par-erreur" not in json.dumps(fiche)


def test_un_agent_sans_declaration_mcp_a_un_volet_vide(client_mcp):
    fiche = client_mcp.get("/api/catalogue/developpeur").json()
    assert fiche["mcp_serveurs"] == [] and fiche["mcp_erreur"] is None


def test_une_declaration_invalide_rend_la_cause_sans_casser_la_fiche(client_mcp, depot_mcp):
    (depot_mcp.racine / "qa.json").write_text("{pas du json", encoding="utf-8")

    fiche = client_mcp.get("/api/catalogue/qa").json()
    listing = client_mcp.get("/api/catalogue")

    # La misconfiguration est visible depuis l'UI, fiche et listing intacts.
    assert fiche["mcp_serveurs"] == []
    assert "déclaration MCP illisible" in fiche["mcp_erreur"]
    assert listing.status_code == 200
    assert any(f["nom"] == "qa" for f in listing.json())


def test_la_fiche_reflete_la_composition_pool_activation(client_mcp, depot_mcp):
    # Modèle pool ∩ activation (#130) vu par l'API : une intégration du pool
    # projet activée pour l'agent apparaît sur sa fiche — le volet lit désormais
    # la **composition** (`McpStore.lire`), pas un fichier hérité isolé. Valeurs
    # masquées comme pour une déclaration héritée.
    depot_mcp.ecrire_pool(
        [
            IntegrationMcp(
                id="gitlab",
                serveur=ServeurMcp(
                    nom="tickets",
                    type="stdio",
                    commande="npx",
                    args=("-y", "@zereight/mcp-gitlab"),
                    env={"GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}"},
                ),
            )
        ]
    )
    depot_mcp.ecrire_activations("qa", ["gitlab"])

    fiche = client_mcp.get("/api/catalogue/qa").json()

    assert fiche["mcp_erreur"] is None
    (serveur,) = fiche["mcp_serveurs"]
    assert (serveur["nom"], serveur["type"]) == ("tickets", "stdio")
    assert serveur["env"]["GITLAB_PERSONAL_ACCESS_TOKEN"] == "${GITLAB_TOKEN}"


def test_une_activation_vers_une_integration_absente_du_pool_rend_la_cause(client_mcp, depot_mcp):
    # Garde-fou de composition (#130) exposé sans casser la fiche : activer un id
    # que le pool ne porte pas est une misconfiguration, rendue comme `mcp_erreur`
    # (même canal qu'une déclaration illisible), fiche et listing intacts.
    depot_mcp.ecrire_activations("qa", ["fantome"])

    fiche = client_mcp.get("/api/catalogue/qa").json()

    assert fiche["mcp_serveurs"] == []
    assert "absente(s) du pool : fantome" in fiche["mcp_erreur"]
    assert client_mcp.get("/api/catalogue").status_code == 200


# --------------------- ⑦ter La forge du produit : un défaut, un catalogue (#412)
#
# Les tests ci-dessus jugent le **comportement** de l'API sur des déclarations
# jetables — d'où la fixture `depot_mcp`, qui n'ouvre jamais le `core/mcp/` réel.
# Ceux qui suivent jugent l'inverse : la **décision versionnée** elle-même. Ils
# lisent donc le vrai dépôt, comme
# `test_mcp.py::test_les_declarations_versionnees_du_depot_sont_valides`.
#
# Ce que #412 a tranché, et que ces tests empêchent de défaire par distraction :
# `qa.json` est un **défaut** (il suit la forge du projet → GitHub), le registre
# curé est une **bibliothèque** (il garde GitLab *à côté de* GitHub). Les deux
# affirmations sont fausses séparément — d'où un test chacune, plus un troisième
# qui vérifie qu'elles racontent la même histoire.


def _serveurs_qa_versionnes() -> tuple[ServeurMcp, ...]:
    """Les serveurs réellement déclarés pour l'agent QA dans le dépôt Git."""
    depot = McpStore(Path(__file__).resolve().parents[1] / "core" / "mcp")
    return tuple(depot.lire("qa"))


def test_le_defaut_de_forge_de_l_agent_qa_est_github():
    (forge,) = _serveurs_qa_versionnes()

    # Le défaut du produit suit la forge du projet (#343/#344 pour l'outillage,
    # #412 pour le produit) : plus aucune trace de GitLab dans ce fichier-ci.
    assert "github" in forge.url
    declaration = json.dumps(forge.to_stored_dict())
    assert "gitlab" not in declaration.lower()
    assert "${GITHUB_TOKEN}" in declaration

    # Le nom d'une liaison est le préfixe de ses outils (`mcp__forge__…`) : il
    # nomme le rôle et non la marque, sans quoi changer de forge rouvrirait les
    # playbooks.
    assert forge.nom == "forge"


def test_le_defaut_de_forge_est_optionnel_pour_ne_casser_aucun_poste():
    """`${GITHUB_TOKEN}` est une clé neuve : aucun poste ne la porte encore.

    Non optionnelle, la bascule ferait échouer toute exécution outillée de
    l'agent QA au premier run — sur une forge pourtant correcte. Canal #125 :
    sans jeton, la voie est omise du montage, sans échec.
    """
    (forge,) = _serveurs_qa_versionnes()
    assert forge.optionnel is True


def test_le_registre_cure_garde_les_deux_forges_et_dit_la_meme_chose_que_qa_json():
    registre = RegistreMcp.curee()
    ids = [entree.id for entree in registre.lister()]

    # La bibliothèque (#131) répond à « quelles intégrations existe-t-il ? » :
    # les deux forges y sont, et retirer `gitlab` INTERDIRAIT de le monter —
    # le seed **est** l'allowlist du dépôt (docs/19 §2.3, découverte ≠
    # installation), et une admission (#678) est un geste, pas un filet.
    assert "github" in ids and "gitlab" in ids
    assert registre.instancier("gitlab").url == ""  # stdio : toujours montable

    # … et elle dit de GitHub exactement ce que `qa.json` déclare : c'est la
    # dérive que le ticket visait — deux fichiers qui parlent de la même
    # intégration et divergent en silence.
    (forge,) = _serveurs_qa_versionnes()
    curee = registre.instancier("github", nom=forge.nom)
    assert curee == forge


# ------------------------------------------- ⑧ Chat utilisateur ↔ agent (#84)


@pytest.fixture()
def depot_chat(tmp_path):
    """Fil de chat sur répertoire temporaire — jamais le `core/chat/` réel."""
    return ChatStore(tmp_path / "chat")


@pytest.fixture()
def client_chat(bus, depot_chat):
    """TestClient de l'app avec répondeur scripté (zéro modèle) sur fil temporaire."""
    with TestClient(
        create_app(bus=bus, chat_store=depot_chat, chat_repondeur=RepondeurScripte())
    ) as client:
        yield client


def test_le_fil_d_un_agent_jamais_contacte_est_vide(client_chat):
    fil = client_chat.get("/api/chat/qa").json()

    assert fil["agent"] == "qa" and fil["role"] == "QA / Testeur"
    assert fil["messages"] == []


def test_envoyer_un_message_rend_la_paire_et_persiste_le_fil(client_chat, depot_chat):
    """Le POST rend message + réponse ; le GET relit le même fil, persisté sur disque."""
    reponse = client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})

    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["agent"] == "qa" and corps["role"] == "QA / Testeur"
    envoye, repondu = corps["messages"]
    assert envoye["auteur"] == UTILISATEUR and envoye["contenu"] == "Bonjour"
    assert repondu["auteur"] == "qa" and "Bonjour" in repondu["contenu"]

    # Le fil relu par le GET est exactement la paire rendue par le POST.
    assert client_chat.get("/api/chat/qa").json()["messages"] == [envoye, repondu]
    # Et il est bien persisté (un fichier JSONL par agent).
    assert (depot_chat.racine / "qa.jsonl").is_file()


def test_le_fil_survit_a_un_redemarrage_de_l_app(client_chat, depot_chat):
    """La persistance du fil : une nouvelle app sur le même dépôt relit tout."""
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Avant redémarrage"})

    with TestClient(
        create_app(chat_store=depot_chat, chat_repondeur=RepondeurScripte())
    ) as nouveau_client:
        messages = nouveau_client.get("/api/chat/qa").json()["messages"]

    assert [m["auteur"] for m in messages] == [UTILISATEUR, "qa"]
    assert messages[0]["contenu"] == "Avant redémarrage"


def test_websocket_diffuse_le_fil_en_temps_reel(client_chat):
    """Chaque message part en `chat.message` : l'envoi dès sa publication, puis la réponse."""
    with client_chat.websocket_connect("/ws/evenements?projet=tous") as ws:
        corps = client_chat.post(
            "/api/chat/qa/messages", json={"contenu": "Où en est la revue ?"}
        ).json()
        aller = ws.receive_json()
        retour = ws.receive_json()

    envoye, repondu = corps["messages"]
    assert aller["type"] == EVENEMENT_CHAT_MESSAGE
    assert aller["agent"] == "qa" and aller["statut"] == AUTEUR_UTILISATEUR
    assert aller["detail"] == "Où en est la revue ?"
    assert aller["horodatage"] == envoye["horodatage"]
    assert retour["type"] == EVENEMENT_CHAT_MESSAGE
    assert retour["statut"] == AUTEUR_AGENT
    assert retour["detail"] == repondu["contenu"]


def test_chat_avec_un_agent_personnalise_du_catalogue(bus, depot_chat, tmp_path):
    """Le chat s'adresse au catalogue effectif : un agent créé (#72) est joignable."""
    app = create_app(
        bus=bus,
        agents_store=AgentStore(tmp_path / "agents"),
        chat_store=depot_chat,
        chat_repondeur=RepondeurScripte(),
    )
    with TestClient(app) as client:
        client.post("/api/catalogue", json=_corps_agent())

        reponse = client.post("/api/chat/redacteur/messages", json={"contenu": "Bonjour"})

        assert reponse.status_code == 201
        assert reponse.json()["role"] == "Rédacteur technique"
        fil = client.get("/api/chat/redacteur").json()
        assert len(fil["messages"]) == 2


def test_chat_agent_hors_catalogue_404_sans_fil_orphelin(client_chat, depot_chat):
    """Un nom inconnu ou hors slug n'ouvre jamais de fil (ni fichier) orphelin."""
    for nom in ("stagiaire", "a.b", "Redacteur"):
        assert client_chat.get(f"/api/chat/{nom}").status_code == 404
        assert client_chat.post(
            f"/api/chat/{nom}/messages", json={"contenu": "Bonjour"}
        ).status_code == 404
    assert not depot_chat.racine.exists()  # aucun refus n'a rien écrit


def test_un_message_vide_est_refuse_en_422(client_chat, depot_chat):
    reponse = client_chat.post("/api/chat/qa/messages", json={"contenu": "  \n"})

    assert reponse.status_code == 422
    assert client_chat.get("/api/chat/qa").json()["messages"] == []
    assert not depot_chat.racine.exists()  # le refus n'a rien persisté


def test_reponse_indisponible_502_le_message_reste_acquis(bus, depot_chat):
    """Le 502 ne concerne que la réponse : le message est persisté et déjà diffusé."""

    class RepondeurEnPanne(RepondeurChat):
        async def repondre(self, agent, fil):
            raise RuntimeError("fournisseur indisponible")

    app = create_app(bus=bus, chat_store=depot_chat, chat_repondeur=RepondeurEnPanne())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/evenements?projet=tous") as ws:
            reponse = client.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})
            recu = ws.receive_json()

        assert reponse.status_code == 502
        assert "fournisseur indisponible" in reponse.json()["detail"]
        # Le message utilisateur est acquis : persisté, servi par le GET, diffusé.
        (message,) = client.get("/api/chat/qa").json()["messages"]
        assert message["auteur"] == UTILISATEUR

    assert recu["type"] == EVENEMENT_CHAT_MESSAGE
    assert recu["statut"] == AUTEUR_UTILISATEUR


@pytest.fixture()
def client_chat_sources(bus, depot_chat, tmp_path, monkeypatch):
    """Le même client, mais capable de recevoir des sources (#482).

    Deux réglages, et les deux sont nécessaires : le **dépôt de téléversement**
    est injecté (une seule instance pour la route qui reçoit les octets et pour
    le fil qui les rattache) et l'**emplacement d'ingestion** est déplacé sur un
    dossier jetable — sans quoi le rattachement écrirait dans le `core/ingestion/`
    du dépôt.
    """
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(tmp_path / "ingestion"))
    app = create_app(
        bus=bus,
        chat_store=depot_chat,
        chat_repondeur=RepondeurScripte(),
        televersements=DepotTeleversements(tmp_path / "depot"),
    )
    with TestClient(app) as client:
        yield client


def test_un_message_porte_ses_sources_et_rend_leur_rapport(client_chat_sources):
    """Le contrat complet d'un dépôt dans le fil : `POST /api/sources` puis le message.

    Un fichier voyage par l'**identifiant** que le téléversement lui a rendu —
    jamais par ses octets, jamais par un chemin du poste —, et le message rendu
    porte ce que Maestro en a **réellement lu**. Le contenu extrait, lui, ne
    revient pas : le rapatrier enverrait les documents entiers à chaque relecture
    du fil.
    """
    depose = client_chat_sources.post(
        "/api/sources",
        files=[("fichier", ("cdc.md", io.BytesIO(b"# Cahier\n\nDeux mots."), "text/markdown"))],
    ).json()

    reponse = client_chat_sources.post(
        "/api/chat/qa/messages",
        json={
            "contenu": "Voici le cahier.",
            "sources": [{"type": "fichier", "id": depose["sources"][0]["id"]}],
        },
    )

    assert reponse.status_code == 201
    envoye, _ = reponse.json()["messages"]
    assert [source["nom"] for source in envoye["sources"]] == ["cdc.md"]
    (lecture,) = envoye["rapport"]["lectures"]
    assert lecture["etat"] == "lu" and lecture["tokens"] > 0
    assert "contexte" not in envoye and "markdown" not in lecture

    # Le fil relu par le GET rend le même message, sources et rapport compris.
    (relu, _) = client_chat_sources.get("/api/chat/qa").json()["messages"]
    assert relu == envoye


def test_une_source_refusee_rend_un_422_motive_et_ne_persiste_rien(
    client_chat_sources, depot_chat
):
    """La forme du refus est celle que l'écran sait afficher **sur la source fautive**.

    `{motif, message, index}` — le même corps que `POST /api/sources` et
    `POST /api/executions`, et c'est ce qui permet à #482 de rendre le refus dans
    le fil plutôt que dans une console. Aplatir en chaîne perdrait l'`index`,
    donc l'endroit.
    """
    reponse = client_chat_sources.post(
        "/api/chat/qa/messages",
        json={
            "contenu": "Et cette adresse ?",
            "sources": [{"type": "url", "valeur": "ftp://exemple.test/spec"}],
        },
    )

    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert detail["motif"] == "url-non-suivable"
    assert detail["index"] == 0
    assert "ftp://exemple.test/spec" in detail["message"]
    # Le refus tombe avant toute écriture : ni fil, ni fichier de fil.
    assert client_chat_sources.get("/api/chat/qa").json()["messages"] == []
    assert not depot_chat.racine.exists()


def test_un_message_de_sources_seules_est_accepte_par_la_route(client_chat_sources):
    """Sans texte mais avec des sources, le POST rend 201 : le dépôt *est* le message.

    Le pendant HTTP de la règle du canal — et la distinction qui compte : sans
    texte **ni** source, c'est toujours un 422.
    """
    depose = client_chat_sources.post(
        "/api/sources",
        files=[("fichier", ("notes.md", io.BytesIO(b"trois mots ici"), "text/markdown"))],
    ).json()

    reponse = client_chat_sources.post(
        "/api/chat/qa/messages",
        json={"contenu": "", "sources": [{"type": "fichier", "id": depose["sources"][0]["id"]}]},
    )

    assert reponse.status_code == 201
    envoye, _ = reponse.json()["messages"]
    assert envoye["contenu"] == ""
    assert [source["nom"] for source in envoye["sources"]] == ["notes.md"]
    assert client_chat_sources.post(
        "/api/chat/qa/messages", json={"contenu": "", "sources": []}
    ).status_code == 422


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
