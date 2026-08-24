"""Contrats d'API v2 (ticket #183) : formes JSON figées et fixtures de la démo.

Couvre les trois critères d'acceptation, sans réseau ni backend réel :

① les **routes de contrat répondent 501** sans fixtures (le contrat est stable,
  le lot d'implémentation n'est pas livré) et **servent des données factices**
  une fois `create_app(fixtures=…)` fourni — ce que fait `maestro.controltower.demo`.
  Deux contrats en sont sortis à mesure que leur lot était livré : les
  **exécutions** (#185, `maestro.controltower.executions`) puis le **journal
  requêtable** (#478, `maestro.controltower.journal`), tous deux servis pour de
  vrai, fixtures ou pas ;
② les **formes** servies sont celles documentées (docs/05 §6) et typées
  (`apps/web/lib/types.ts`), filtres/tri/pagination du journal compris — ce
  dernier éprouvé sur son **implémentation réelle** depuis #478, l'historique
  étant posé par le journal durable que le lifespan rejoue ;
③ la **référence de ticket externe** (#187) voyage par `Event`/`EtatTache`, survit
  au rejeu, et le schéma de tâche partagé l'accepte — absente par défaut.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_PLAYBOOK_PROPOSITION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    ControlTowerState,
    Event,
    FixturesControlTower,
    InMemoryEventLog,
    ReferenceTicket,
    create_app,
    demo,
)
from maestro.controltower.fixtures import FRAGMENT_CHAT_DEBUT, FRAGMENT_CHAT_FIN
from maestro.controltower.journal import TAILLE_PAGE_MAX
from maestro.orchestrator.errors import TaskValidationError
from maestro.orchestrator.schema import validate_plan, validate_task

# Les routes de contrat v2, la méthode HTTP et un corps **valide** éventuel : le
# corps doit passer la validation Pydantic (sinon 422 avant d'atteindre la gate
# 501), pour prouver que c'est bien la gate qui répond 501.
ROUTES_V2 = [
    ("get", "/api/configuration", {}),
    ("get", "/api/playbooks/propositions", {}),
    ("get", "/api/chat/qa/flux", {}),
]

#: Le run et le projet du scénario de la démo — ceux que portaient les fixtures
#: du journal avant que #478 ne serve la route pour de vrai.
RUN_DEMO = "demo-live"
PROJET_DEMO = "prj-demo"


def _evt(**champs) -> Event:
    """Un événement du scénario, rattaché au run et au projet de la démo."""
    return Event(run_id=RUN_DEMO, projet_id=PROJET_DEMO, **champs)


#: L'historique consigné avant l'ouverture de l'API : c'est lui que le rejeu du
#: journal durable (#97) remet dans le journal requêtable au démarrage — donc
#: exactement le chemin qu'emprunte un rechargement de page en production.
EVENEMENTS_JOURNAL = [
    _evt(
        type=EVENEMENT_AGENT_ACTIVITE,
        horodatage="2026-07-30T09:00:04+00:00",
        agent="orchestrateur",
        role="Orchestrateur",
        titre="Planification",
        detail="Objectif décomposé en 4 tâches",
    ),
    _evt(
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:00:12+00:00",
        agent="bdd",
        role="Base de données",
        tache_id="demo-t1",
        statut="en_cours",
        detail="Concevoir le schéma SQL de la table contacts",
    ),
    _evt(
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:00:50+00:00",
        agent="bdd",
        role="Base de données",
        tache_id="demo-t1",
        statut="terminee",
        detail="Schéma prêt",
    ),
    _evt(
        type=EVENEMENT_MESSAGE_INTER_AGENTS,
        horodatage="2026-07-30T09:00:52+00:00",
        agent="bdd",
        role="Base de données",
        detail="→ developpeur : la table `contacts` est disponible",
    ),
    _evt(
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:01:05+00:00",
        agent="developpeur",
        role="Développeur",
        tache_id="demo-t2",
        statut="en_cours",
        detail="Implémenter l'API REST des contacts",
    ),
    _evt(
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:02:06+00:00",
        agent="developpeur",
        role="Développeur",
        tache_id="demo-t2",
        statut="terminee",
        detail="API créer/lister livrée",
    ),
    _evt(
        type=EVENEMENT_VALIDATION_DEMANDE,
        horodatage="2026-07-30T09:02:25+00:00",
        agent="devops",
        role="DevOps",
        tache_id="demo-t3",
        statut="en_attente",
        detail="validation requise avant déploiement",
    ),
    _evt(
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:03:00+00:00",
        agent="qa",
        role="QA / Testeur",
        tache_id="demo-qa",
        statut="terminee",
        detail="Vérification de santé de l'API",
    ),
]


@pytest.fixture()
def client_nu():
    """App sans fixtures : les routes de contrat doivent répondre 501 (production)."""
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture()
def client():
    """App branchée sur les fixtures : les routes servent des données factices (la démo)."""
    with TestClient(create_app(fixtures=FixturesControlTower())) as client:
        yield client


@pytest.fixture()
def client_journal():
    """App **sans fixtures** dont le journal durable porte déjà un historique.

    C'est le chemin réel : la route ne dépend plus d'aucune fixture, et ce
    qu'elle sert vient du rejeu de l'`EventLog` à l'ouverture (`lifespan`).
    """
    log = InMemoryEventLog()
    for event in EVENEMENTS_JOURNAL:
        asyncio.run(log.consigner(event))
    with TestClient(create_app(event_log=log)) as client:
        yield client


# --- ① 501 sans fixtures, servi avec ------------------------------------------------


@pytest.mark.parametrize(("methode", "chemin", "kwargs"), ROUTES_V2)
def test_sans_fixtures_les_routes_de_contrat_repondent_501(client_nu, methode, chemin, kwargs):
    """Le contrat est déclaré, l'implémentation pas encore livrée : 501 franc."""
    reponse = getattr(client_nu, methode)(chemin, **kwargs)
    assert reponse.status_code == 501


def test_les_routes_existantes_restent_servies_sans_fixtures(client_nu):
    """La gate 501 ne touche que les routes v2 : le reste de l'API répond normalement."""
    assert client_nu.get("/api/sante").status_code == 200
    assert client_nu.get("/api/taches?projet=tous").status_code == 200
    # Le journal a quitté la gate avec #478 : sans fixtures il est **servi**, et
    # rend un journal vide plutôt qu'un 501 — c'est la fin du mur, pas un
    # contournement.
    assert client_nu.get("/api/journal?projet=tous").status_code == 200
    # /api/playbooks/propositions ne « mange » pas la capture {agent} : le playbook
    # d'un agent reste servi, un agent inconnu reste un 404 (pas un 501).
    assert client_nu.get("/api/playbooks/qa").status_code == 200
    assert client_nu.get("/api/playbooks/inconnu").status_code == 404


def test_la_demo_branche_les_fixtures_sur_son_app(monkeypatch):
    """③ La démo est bien le backend de fixtures : elle construit son app **avec**.

    Sans ce câblage, les routes de contrat répondraient 501 à la voie front — le
    ticket ne serait pas rendu. On coupe juste après la construction de l'app :
    au-delà, `_servir` lance uvicorn, qui est bloquant.
    """
    capture: dict = {}

    class _Coupe(Exception):
        """Interrompt `_servir` dès l'app construite."""

    def _create_app_factice(**kwargs):
        capture.update(kwargs)
        raise _Coupe

    monkeypatch.setattr(demo, "create_app", _create_app_factice)
    with pytest.raises(_Coupe):
        asyncio.run(demo._servir("127.0.0.1", 0))
    assert isinstance(capture["fixtures"], FixturesControlTower)


# --- ② Journal requêtable -----------------------------------------------------------


def test_journal_relu_au_demarrage(client_journal):
    """L'historique persisté est là **avant** le premier événement temps réel (#478).

    C'est le critère du ticket vu depuis l'API : un client qui ouvre la page —
    donc qui n'a encore rien reçu sur le WebSocket — obtient déjà tout ce que le
    run a dit. Les identifiants d'entrée sortent du rang dans le journal, donc
    du rejeu, donc sont les mêmes d'un redémarrage à l'autre.
    """
    page = client_journal.get("/api/journal", params={"projet": "tous"}).json()
    assert page["total"] == len(EVENEMENTS_JOURNAL)
    assert [e["id"] for e in page["entrees"]][-1] == "j-0001"
    # Le titre voyage avec l'entrée : sans lui la ligne relue dirait « une étape »
    # là où le direct nommait la planification.
    assert page["entrees"][-1]["titre"] == "Planification"


def test_journal_filtre_par_agent_et_trie(client_journal):
    params = {"projet": "tous", "agent": "bdd", "ordre": "asc"}
    page = client_journal.get("/api/journal", params=params).json()
    assert {e["agent"] for e in page["entrees"]} == {"bdd"}
    horodatages = [e["horodatage"] for e in page["entrees"]]
    assert horodatages == sorted(horodatages)  # ordre ascendant demandé
    assert page["total"] == len(page["entrees"])


def test_journal_filtre_par_run(client_journal):
    """`run_id` est un filtre du contrat, et c'est lui que la vue d'un run utilise."""
    a_lui = client_journal.get(
        "/api/journal", params={"projet": "tous", "run_id": RUN_DEMO}
    ).json()
    d_un_autre = client_journal.get(
        "/api/journal", params={"projet": "tous", "run_id": "run-inconnu"}
    ).json()
    assert a_lui["total"] == len(EVENEMENTS_JOURNAL)
    assert d_un_autre["total"] == 0
    assert d_un_autre["pages"] == 0


def test_journal_pagine(client_journal):
    p1 = client_journal.get(
        "/api/journal", params={"projet": "tous", "taille": 3, "page": 1}
    ).json()
    assert len(p1["entrees"]) == 3
    assert p1["pages"] == (p1["total"] + 2) // 3
    p2 = client_journal.get(
        "/api/journal", params={"projet": "tous", "taille": 3, "page": 2}
    ).json()
    # Pages disjointes : aucun id commun entre deux pages.
    assert {e["id"] for e in p1["entrees"]}.isdisjoint(e["id"] for e in p2["entrees"])


def test_journal_filtre_periode(client_journal):
    page = client_journal.get(
        "/api/journal",
        params={
            "projet": "tous",
            "depuis": "2026-07-30T09:02:00+00:00",
            "jusqua": "2026-07-30T09:02:30+00:00",
        },
    ).json()
    assert page["total"] >= 1
    assert all(
        "2026-07-30T09:02:00+00:00" <= e["horodatage"] <= "2026-07-30T09:02:30+00:00"
        for e in page["entrees"]
    )


@pytest.mark.parametrize(
    "params",
    [
        {"tri": "inconnu"},
        {"ordre": "zigzag"},
        {"page": 0},
        {"taille": 0},
        {"taille": TAILLE_PAGE_MAX + 1},
    ],
)
def test_journal_parametres_invalides_422(client_journal, params):
    assert (
        client_journal.get("/api/journal", params={**params, "projet": "tous"}).status_code
        == 422
    )


# --- ② Configuration & propositions -------------------------------------------------


def test_registre_de_configuration(client):
    registre = client.get("/api/configuration").json()
    assert set(registre) == {"reglages", "version", "erreur"}
    assert registre["erreur"] is None
    par_cle = {r["cle"]: r for r in registre["reglages"]}
    # Un secret ne renvoie jamais sa valeur en clair (#132) : masqué, write-only.
    secret = par_cle["cle_api_fournisseur"]
    assert secret["secret"] is True
    assert set("•") >= set(secret["valeur"])  # que des points


def test_propositions_de_playbook_globales(client):
    propositions = client.get("/api/playbooks/propositions").json()
    assert {p["agent"] for p in propositions} == {"qa", "developpeur"}
    # Chaque proposition porte le role de son agent (affichable sans le catalogue).
    assert all(p["role"] and p["provenance"] == "proposition" for p in propositions)


# --- ② Flux SSE d'un fil de chat ----------------------------------------------------


def test_flux_chat_sse(client):
    reponse = client.get("/api/chat/qa/flux", params={"contenu": "salut"})
    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/event-stream")
    trames = [
        json.loads(ligne.removeprefix("data: "))
        for ligne in reponse.text.splitlines()
        if ligne.startswith("data: ")
    ]
    assert trames[0]["type"] == FRAGMENT_CHAT_DEBUT
    assert trames[-1]["type"] == FRAGMENT_CHAT_FIN
    # La dernière trame porte le message complet ; les fragments reconstituent son texte.
    complet = trames[-1]["message"]["contenu"]
    assert "".join(t["delta"] for t in trames) == complet


def test_flux_chat_agent_inconnu_404(client):
    assert client.get("/api/chat/inconnu/flux").status_code == 404


# --- ③ Référence de ticket externe (#187) -------------------------------------------


def test_reference_ticket_aller_retour():
    ref = ReferenceTicket(id="#183", url="https://x/183")
    assert ReferenceTicket.from_dict(ref.to_dict()) == ref
    # Un événement complet fait l'aller-retour JSON en conservant son ticket.
    event = Event(type=EVENEMENT_TACHE_STATUT, tache_id="t1", ticket=ref)
    assert Event.from_dict(event.to_dict()).ticket == ref


def test_le_ticket_est_porte_par_la_projection_et_survit():
    """Le ticket posé par un événement de tâche reste sur la carte, événements suivants compris."""
    state = ControlTowerState()
    ref = ReferenceTicket(id="#42", url="https://x/42")
    state.appliquer(
        Event(type=EVENEMENT_TACHE_STATUT, run_id="r1", tache_id="t1",
              titre="T", agent="bdd", role="BDD", statut="en_cours", ticket=ref)
    )
    assert state.tache("t1").to_dict()["ticket"] == {"id": "#42", "url": "https://x/42"}
    # Un événement ultérieur sans ticket ne l'efface pas (inconnu ≠ absent).
    state.appliquer(
        Event(type=EVENEMENT_TACHE_STATUT, run_id="r1", tache_id="t1", statut="terminee")
    )
    assert state.tache("t1").ticket == ref


def test_taches_sans_ticket_exposent_null():
    """Une tâche sans référence expose `ticket: null` (pas d'absence de clé)."""
    state = ControlTowerState()
    state.appliquer(Event(type=EVENEMENT_TACHE_STATUT, tache_id="t1", statut="en_cours"))
    assert state.tache("t1").to_dict()["ticket"] is None


def test_evenement_playbook_proposition_existe():
    """Le type d'événement du signal global de proposition est figé (#183)."""
    assert EVENEMENT_PLAYBOOK_PROPOSITION == "playbook.proposition"


# --- ③ Schéma de tâche partagé ------------------------------------------------------

_TACHE_BASE = {
    "id": "t1",
    "titre": "Une tâche",
    "description": "Ce qu'il faut faire.",
    "competences_requises": ["sql"],
    "format_sortie": "Fichier SQL",
}


def test_schema_accepte_une_tache_avec_ticket():
    validate_task({**_TACHE_BASE, "ticket": {"id": "#183", "url": "https://x/183"}})
    validate_task({**_TACHE_BASE, "ticket": {"id": "#183"}})  # url optionnelle
    # `url` vide quand seul l'identifiant est connu (docs/05 §6.6) : le `format:
    # uri` du schéma ne doit pas le refuser — il reste annotatif (aucun
    # `format_checker` monté par `maestro.orchestrator.schema`).
    validate_task({**_TACHE_BASE, "ticket": {"id": "#183", "url": ""}})


def test_schema_reste_valide_sans_ticket():
    """Un plan sans référence reste valide (absente par défaut)."""
    (tache,) = validate_plan([dict(_TACHE_BASE)])
    assert tache.id == "t1"


@pytest.mark.parametrize(
    "ticket",
    [
        {"url": "https://x"},          # id requis manquant
        {"id": "#1", "extra": "non"},  # additionalProperties: false
    ],
)
def test_schema_refuse_un_ticket_malforme(ticket):
    with pytest.raises(TaskValidationError):
        validate_task({**_TACHE_BASE, "ticket": ticket})
