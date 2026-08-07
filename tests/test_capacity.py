"""Tests du contrôle de capacité des agents (ticket #86, EF-21).

Couvre les trois critères d'acceptation, couche par couche :

① le **dépôt** (`CapacityStore`) : capacité par défaut (actif, une instance),
   écriture/relecture persistée, refus des réglages invalides (instances < 1,
   nom hors slug), liste des réglages posés et des agents désactivés,
   résolution du dépôt configuré (`MAESTRO_CAPACITE_DIR`) ;
② le **routage et l'exécution** : un agent désactivé est écarté des candidats
   (`Router.route(exclus=...)`) — la tâche va au meilleur agent restant ou en
   repli « à assigner », jamais vers lui — et le réglage est relu **à chaud**
   (réactiver vaut pour la tâche suivante, sans reconstruire l'exécuteur) ;
   le plafond d'**instances** borne les exécutions simultanées d'un agent
   (`JaugeInstances`) : une instance sérialise, deux s'exécutent de front —
   sans dépôt câblé, comportement historique (capacité illimitée) ;
③ l'**API Control Tower** : `GET /api/agents` expose `actif`/`instances`,
   `POST /api/agents/{nom}/capacite` persiste le réglage, met la fiche à jour
   et diffuse `agent.capacite` sur le WebSocket ; les réglages persistés sont
   visibles dès le démarrage ; refus : agent inconnu (404), requête vide ou
   plafond invalide (422), réassignation manuelle vers un agent désactivé
   (422) ; la suppression d'un agent personnalisé purge son réglage.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from maestro.agents import DEFAULT_AGENTS
from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.agents.catalog import Agent
from maestro.agents.store import AgentStore
from maestro.config import Settings
from maestro.controltower import (
    CAPACITE_DESACTIVE,
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_TACHE_STATUT,
    Event,
    InMemoryEventBus,
    create_app,
)
from maestro.engine.executor import LocalExecutor
from maestro.orchestrator import Task
from maestro.providers.base import ModelProvider
from maestro.router import Router
from maestro.telemetry import RunJournal


def _task(**overrides) -> Task:
    data = {
        "id": "t",
        "titre": "Une tâche",
        "description": "Description.",
        "competences_requises": ("sql",),
        "format_sortie": "Un livrable",
        "dependances": (),
    }
    data.update(overrides)
    return Task(**data)


class ConstantProvider(ModelProvider):
    """Fournisseur factice : toujours le même livrable texte (jamais d'outillé)."""

    name = "constant"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "LIVRABLE"


# --- ① Le dépôt des capacités (`CapacityStore`) ------------------------------------------


def test_capacite_par_defaut_actif_une_instance(tmp_path):
    capacite = CapacityStore(tmp_path).lire("developpeur")
    assert capacite.actif is True
    assert capacite.instances == 1
    assert capacite.modifie_le == ""


def test_ecrire_persiste_et_relit_le_reglage(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="developpeur", actif=False, instances=3))

    relu = store.lire("developpeur")
    assert relu.actif is False
    assert relu.instances == 3
    assert relu.modifie_le  # daté à l'écriture
    # Persisté sur fichier : un autre dépôt (autre process) voit le même réglage.
    assert CapacityStore(tmp_path).lire("developpeur").actif is False


def test_instances_invalides_refusees(tmp_path):
    store = CapacityStore(tmp_path)
    with pytest.raises(ValueError, match="instances"):
        store.ecrire(CapaciteAgent(nom="developpeur", instances=0))
    assert not list(tmp_path.iterdir())  # rien n'a été stocké


def test_nom_hors_slug_refuse_sans_traversee_de_chemin(tmp_path):
    store = CapacityStore(tmp_path)
    with pytest.raises(ValueError, match="slug"):
        store.lire("../evasion")
    with pytest.raises(ValueError, match="slug"):
        store.ecrire(CapaciteAgent(nom="Pas Un Slug"))


def test_lister_et_inactifs_ne_rendent_que_les_regles(tmp_path):
    store = CapacityStore(tmp_path)
    assert store.lister() == ()
    assert store.inactifs() == frozenset()

    store.ecrire(CapaciteAgent(nom="qa", actif=False))
    store.ecrire(CapaciteAgent(nom="bdd", instances=2))

    assert [c.nom for c in store.lister()] == ["bdd", "qa"]
    assert store.inactifs() == frozenset({"qa"})


def test_supprimer_revient_aux_defauts(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="qa", actif=False))
    assert store.supprimer("qa") is True
    assert store.lire("qa").actif is True
    assert store.supprimer("qa") is False  # déjà aux défauts : rien à faire


def test_le_depot_configure_vient_de_l_environnement(monkeypatch, tmp_path):
    monkeypatch.setenv("MAESTRO_CAPACITE_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.capacite_dir == str(tmp_path)
    assert CapacityStore.default(settings).racine == tmp_path


# --- ② Routage et exécution : désactivation ----------------------------------------------


def _agents_partageant_sql() -> tuple[Agent, ...]:
    # Le titulaire couvre mieux la tâche (sql + migration) : sans exclusion il
    # gagne net ; écarté, le remplaçant (sql seul) reste un vainqueur net.
    return (
        Agent(nom="titulaire", role="T", competences=frozenset({"sql", "migration"}),
              modele="m", prompt_systeme="p"),
        Agent(nom="remplacant", role="R", competences=frozenset({"sql"}),
              modele="m", prompt_systeme="p"),
    )


def _task_sql_migration() -> Task:
    return _task(competences_requises=("sql", "migration"))


def test_route_ecarte_l_agent_exclu_au_profit_du_suivant():
    router = Router(_agents_partageant_sql())
    decision = asyncio.run(router.route(_task_sql_migration(), exclus={"titulaire"}))
    assert decision.agent is not None and decision.agent.nom == "remplacant"


def test_route_sans_exclusion_garde_le_titulaire():
    router = Router(_agents_partageant_sql())
    decision = asyncio.run(router.route(_task_sql_migration()))
    assert decision.agent is not None and decision.agent.nom == "titulaire"


def test_route_tous_exclus_replie_a_assigner_sans_lever():
    router = Router(_agents_partageant_sql())
    decision = asyncio.run(
        router.route(_task_sql_migration(), exclus={"titulaire", "remplacant"})
    )
    assert decision.a_assigner
    assert "désactivés" in decision.raison


def test_agent_desactive_ne_recoit_plus_de_taches(tmp_path):
    # bdd est le seul agent compétent en sql : désactivé, la tâche part en repli
    # « à assigner » (échec explicite) plutôt que d'être exécutée par lui.
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", actif=False))
    executor = LocalExecutor(ConstantProvider(), runtimes={}, capacites=store)

    result = asyncio.run(executor.execute(_task(), [], RunJournal()))
    assert result.statut == "echec"
    assert result.agent == "—"
    assert "à assigner" in (result.erreur or "")


def test_reactivation_appliquee_a_chaud_sans_reconstruire_l_executeur(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", actif=False))
    executor = LocalExecutor(ConstantProvider(), runtimes={}, capacites=store)

    avant = asyncio.run(executor.execute(_task(id="t1"), [], RunJournal()))
    assert avant.statut == "echec"

    store.ecrire(CapaciteAgent(nom="bdd", actif=True))
    apres = asyncio.run(executor.execute(_task(id="t2"), [], RunJournal()))
    assert apres.ok
    assert apres.agent == "bdd"


# --- ② Routage et exécution : plafond d'instances ----------------------------------------


class PicProvider(ModelProvider):
    """Mesure le pic d'appels simultanés, chaque appel cédant brièvement la main."""

    name = "pic"

    def __init__(self) -> None:
        self._en_vol = 0
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        await asyncio.sleep(0.01)
        self._en_vol -= 1
        return "LIVRABLE"


class RendezvousProvider(ModelProvider):
    """Force la simultanéité : chaque appel attend `attendus` appels en vol.

    En exécution sérialisée, le premier appel attendrait seul : le timeout borne
    l'attente et mue une régression en échec net plutôt qu'en suite suspendue.
    """

    name = "rendezvous"

    def __init__(self, attendus: int) -> None:
        self._attendus = attendus
        self._en_vol = 0
        self._complet = asyncio.Event()
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        if self._en_vol >= self._attendus:
            self._complet.set()
        await asyncio.wait_for(self._complet.wait(), timeout=5)
        self._en_vol -= 1
        return "LIVRABLE"


async def _deux_taches_bdd(executor: LocalExecutor):
    """Deux tâches indépendantes routées vers le même agent (bdd), lancées de front."""
    return await asyncio.gather(
        executor.execute(_task(id="t1"), [], RunJournal()),
        executor.execute(_task(id="t2"), [], RunJournal()),
    )


def test_une_instance_serialise_les_taches_du_meme_agent(tmp_path):
    # Sans réglage stocké, le plafond par défaut est 1 : jamais 2 en vol.
    provider = PicProvider()
    executor = LocalExecutor(provider, runtimes={}, capacites=CapacityStore(tmp_path))

    resultats = asyncio.run(_deux_taches_bdd(executor))
    assert all(r.ok for r in resultats)
    assert provider.pic == 1


def test_deux_instances_s_executent_de_front(tmp_path):
    store = CapacityStore(tmp_path)
    store.ecrire(CapaciteAgent(nom="bdd", instances=2))
    provider = RendezvousProvider(attendus=2)
    executor = LocalExecutor(provider, runtimes={}, capacites=store)

    resultats = asyncio.run(_deux_taches_bdd(executor))
    assert all(r.ok for r in resultats)
    assert provider.pic >= 2


def test_sans_depot_cable_la_capacite_reste_illimitee(tmp_path):
    # Comportement historique préservé : sans `capacites`, aucune jauge.
    provider = RendezvousProvider(attendus=2)
    executor = LocalExecutor(provider, runtimes={})

    resultats = asyncio.run(_deux_taches_bdd(executor))
    assert all(r.ok for r in resultats)
    assert provider.pic >= 2


# --- L'événement `agent.capacite` voyage en JSON ------------------------------------------


def test_l_evenement_capacite_fait_l_aller_retour_json():
    event = Event(
        type=EVENEMENT_AGENT_CAPACITE, agent="bdd", statut=CAPACITE_DESACTIVE, instances=3
    )
    relu = Event.from_json(event.to_json())
    assert relu.instances == 3
    assert relu.statut == CAPACITE_DESACTIVE


def test_un_evenement_sans_instances_reste_lisible():
    # Producteur minimaliste ou flux antérieur au #86 : la clé peut manquer.
    relu = Event.from_dict({"type": EVENEMENT_TACHE_STATUT})
    assert relu.instances is None


# --- ③ L'API Control Tower ----------------------------------------------------------------


@pytest.fixture()
def bus():
    return InMemoryEventBus()


@pytest.fixture()
def capacites(tmp_path):
    return CapacityStore(tmp_path / "capacite")


@pytest.fixture()
def client(bus, capacites, tmp_path):
    """TestClient de l'app sur bus mémoire, dépôts (capacités, agents) temporaires."""
    app = create_app(
        bus=bus,
        capacites=capacites,
        agents_store=AgentStore(tmp_path / "agents"),
    )
    with TestClient(app) as client:
        yield client


def publie(client, bus, *events):
    """Publie sur le bus depuis le thread de test, dans la boucle de l'app."""
    for event in events:
        client.portal.call(bus.publish, event)


def _fiche(client, nom):
    return next(a for a in client.get("/api/agents").json() if a["nom"] == nom)


def test_les_fiches_agents_exposent_la_capacite_par_defaut(client):
    for agent in DEFAULT_AGENTS:
        fiche = _fiche(client, agent.nom)
        assert fiche["actif"] is True
        assert fiche["instances"] == 1


def test_desactiver_un_agent_met_a_jour_fiche_et_depot(client, capacites):
    reponse = client.post("/api/agents/qa/capacite", json={"actif": False})
    assert reponse.status_code == 200
    assert reponse.json()["actif"] is False

    assert _fiche(client, "qa")["actif"] is False
    # Persisté dans le dépôt partagé : moteur et workers le reliront à la
    # prochaine tâche — c'est l'effet réel du réglage.
    assert capacites.lire("qa").actif is False
    assert capacites.inactifs() == frozenset({"qa"})


def test_ajuster_les_instances_sans_toucher_l_actif(client, capacites):
    client.post("/api/agents/developpeur/capacite", json={"actif": False})
    reponse = client.post("/api/agents/developpeur/capacite", json={"instances": 4})
    assert reponse.status_code == 200

    fiche = _fiche(client, "developpeur")
    assert fiche["instances"] == 4
    assert fiche["actif"] is False  # le champ omis reste en place
    assert capacites.lire("developpeur").instances == 4


def test_le_reglage_est_diffuse_en_temps_reel_sur_le_websocket(client):
    with client.websocket_connect("/ws/evenements?projet=tous") as ws:
        client.post("/api/agents/qa/capacite", json={"actif": False, "instances": 2})
        event = ws.receive_json()

    assert event["type"] == EVENEMENT_AGENT_CAPACITE
    assert event["agent"] == "qa"
    assert event["statut"] == CAPACITE_DESACTIVE
    assert event["instances"] == 2


def test_les_reglages_persistes_sont_visibles_des_le_demarrage(bus, capacites, tmp_path):
    capacites.ecrire(CapaciteAgent(nom="devops", actif=False, instances=3))
    app = create_app(
        bus=bus, capacites=capacites, agents_store=AgentStore(tmp_path / "agents")
    )
    with TestClient(app) as client:
        fiche = _fiche(client, "devops")
    assert fiche["actif"] is False
    assert fiche["instances"] == 3


def test_agent_inconnu_404(client):
    assert client.post("/api/agents/fantome/capacite", json={"actif": False}).status_code == 404


def test_requete_vide_422(client):
    reponse = client.post("/api/agents/qa/capacite", json={})
    assert reponse.status_code == 422
    assert "rien à régler" in reponse.json()["detail"]


def test_instances_invalides_422_sans_rien_persister(client, capacites):
    reponse = client.post("/api/agents/qa/capacite", json={"instances": 0})
    assert reponse.status_code == 422
    assert capacites.lire("qa").instances == 1


def test_reassignation_vers_un_agent_desactive_422(client, bus):
    publie(client, bus, Event(
        type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t1",
        titre="Une tâche", agent="qa", role="QA / Testeur", statut="en_cours",
    ))
    client.post("/api/agents/developpeur/capacite", json={"actif": False})

    reponse = client.post("/api/taches/t1/reassigner", json={"agent": "developpeur"})
    assert reponse.status_code == 422
    assert "désactivé" in reponse.json()["detail"]

    # Réactivé, la réassignation reprend — un agent désactivé n'est pas perdu.
    client.post("/api/agents/developpeur/capacite", json={"actif": True})
    relance = client.post("/api/taches/t1/reassigner", json={"agent": "developpeur"})
    assert relance.status_code == 200


def test_supprimer_un_agent_personnalise_purge_son_reglage(client, capacites):
    client.post("/api/catalogue", json={
        "nom": "traducteur", "role": "Traducteur",
        "competences": ["traduction"], "playbook": "Traduis fidèlement.",
    })
    client.post("/api/agents/traducteur/capacite", json={"actif": False, "instances": 2})
    assert capacites.lire("traducteur").actif is False

    assert client.delete("/api/catalogue/traducteur").status_code == 200
    # Le réglage part avec l'agent : un homonyme recréé repart des défauts.
    assert capacites.lire("traducteur") == CapaciteAgent(nom="traducteur")


def test_le_reglage_capacite_survit_a_un_redemarrage_de_l_app(bus, capacites, tmp_path):
    agents_store = AgentStore(tmp_path / "agents")
    app = create_app(bus=bus, capacites=capacites, agents_store=agents_store)
    with TestClient(app) as client:
        client.post("/api/agents/bdd/capacite", json={"instances": 5})

    relance = create_app(
        bus=InMemoryEventBus(), capacites=capacites, agents_store=agents_store
    )
    with TestClient(relance) as client:
        assert _fiche(client, "bdd")["instances"] == 5
