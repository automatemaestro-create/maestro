"""Contrats d'API v2 (ticket #183) : formes JSON figées et fixtures de la démo.

Couvre les trois critères d'acceptation, sans réseau ni backend réel :

① les **routes de contrat répondent 501** sans fixtures (le contrat est stable,
  le lot d'implémentation n'est pas livré) et **servent des données factices**
  une fois `create_app(fixtures=…)` fourni — ce que fait `maestro.controltower.demo`.
  Les **exécutions** en sont sorties : leur lot (#185) est livré, `/api/executions`
  est servi pour de vrai par `maestro.controltower.executions` ;
② les **formes** servies sont celles documentées (docs/05 §6) et typées
  (`apps/web/lib/types.ts`), filtres/tri/pagination du journal compris ;
③ la **référence de ticket externe** (#187) voyage par `Event`/`EtatTache`, survit
  au rejeu, et le schéma de tâche partagé l'accepte — absente par défaut.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_PLAYBOOK_PROPOSITION,
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    FixturesControlTower,
    ReferenceTicket,
    create_app,
    demo,
)
from maestro.controltower.fixtures import (
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_FIN,
    TAILLE_PAGE_MAX,
)
from maestro.orchestrator.errors import TaskValidationError
from maestro.orchestrator.schema import validate_plan, validate_task

# Les routes de contrat v2, la méthode HTTP et un corps **valide** éventuel : le
# corps doit passer la validation Pydantic (sinon 422 avant d'atteindre la gate
# 501), pour prouver que c'est bien la gate qui répond 501.
ROUTES_V2 = [
    ("get", "/api/journal", {}),
    ("get", "/api/configuration", {}),
    ("get", "/api/playbooks/propositions", {}),
    ("get", "/api/chat/qa/flux", {}),
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


# --- ① 501 sans fixtures, servi avec ------------------------------------------------


@pytest.mark.parametrize(("methode", "chemin", "kwargs"), ROUTES_V2)
def test_sans_fixtures_les_routes_de_contrat_repondent_501(client_nu, methode, chemin, kwargs):
    """Le contrat est déclaré, l'implémentation pas encore livrée : 501 franc."""
    reponse = getattr(client_nu, methode)(chemin, **kwargs)
    assert reponse.status_code == 501


def test_les_routes_existantes_restent_servies_sans_fixtures(client_nu):
    """La gate 501 ne touche que les routes v2 : le reste de l'API répond normalement."""
    assert client_nu.get("/api/sante").status_code == 200
    assert client_nu.get("/api/taches").status_code == 200
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


def test_journal_filtre_par_agent_et_trie(client):
    page = client.get("/api/journal", params={"agent": "bdd", "ordre": "asc"}).json()
    assert {e["agent"] for e in page["entrees"]} == {"bdd"}
    horodatages = [e["horodatage"] for e in page["entrees"]]
    assert horodatages == sorted(horodatages)  # ordre ascendant demandé
    assert page["total"] == len(page["entrees"])


def test_journal_pagine(client):
    p1 = client.get("/api/journal", params={"taille": 3, "page": 1}).json()
    assert len(p1["entrees"]) == 3
    assert p1["pages"] == (p1["total"] + 2) // 3
    p2 = client.get("/api/journal", params={"taille": 3, "page": 2}).json()
    # Pages disjointes : aucun id commun entre deux pages.
    assert {e["id"] for e in p1["entrees"]}.isdisjoint(e["id"] for e in p2["entrees"])


def test_journal_filtre_periode(client):
    page = client.get(
        "/api/journal",
        params={"depuis": "2026-07-30T09:02:00+00:00", "jusqua": "2026-07-30T09:02:30+00:00"},
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
def test_journal_parametres_invalides_422(client, params):
    assert client.get("/api/journal", params=params).status_code == 422


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
