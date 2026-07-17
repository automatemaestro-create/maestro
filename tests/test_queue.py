"""Tests d'intégration de la file de tâches — Celery + Redis (ticket #41).

Aucun Redis ni réseau requis : la file tourne sur le transport **mémoire** de
Kombu (broker `memory://`, backend `cache+memory://`) avec de **vrais workers
Celery** embarqués en threads — mise en file, consommation et remontée sont
celles de la production ; seuls le transport et le fournisseur (factice, aucun
appel modèle) changent. Couvre les critères d'acceptation du ticket #41 :

① la file **reçoit les tâches de l'orchestrateur**, mise en file et consommation
   découplées : une tâche envoyée sans worker reste en attente, un worker démarré
   ensuite la consomme (`send_task` par nom — aucun import du code worker) ;
② au moins **2 agents en parallèle sur des workers distincts** : rendez-vous
   inter-workers forcé (séquentiel ⇒ échec net), noms de workers distincts sur
   les résultats ;
③ le **statut et le résultat remontent** à l'orchestrateur : succès, échec
   (métier comme payload), fichiers produits (artefacts), usage mesuré côté
   worker ;
④ mise en file → exécution → remontée couverte de bout en bout, y compris via
   la boucle complète (`OrchestrationEngine` + `CeleryExecutor`) : dépendances
   (tableau noir) transmises aux workers, rapport agrégé, journal consigné.

Plus le DAG (#43) : une tâche dont une dépendance a échoué n'est **jamais mise en
file** — blocage explicite côté orchestrateur, aucun message orphelin. Et les
playbooks versionnés (#78, tests différés → #75) : une version publiée entre deux
messages s'applique au suivant sans redémarrer le worker, la version utilisée
remonte avec le résultat.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker, test_worker_started

from maestro.engine import OrchestrationEngine, PolitiqueRelance, TaskResult
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.queue import NOM_TACHE_EXECUTER, CeleryExecutor, create_app
from maestro.queue.worker import configurer_worker, nommer_worker, reinitialiser_worker
from maestro.sandbox import ProducedFile
from maestro.telemetry import RunJournal, StepUsage


@test_worker_started.connect
def _nomme_le_worker_embarque(sender=None, worker=None, **kwargs):
    """Pose le nodename des workers embarqués (un worker de test = un thread).

    Le signal est émis dans le thread du worker : `nommer_worker` y fixe le nom
    rapporté par les résultats — `celeryd_after_setup`, qui s'en charge pour un
    vrai worker CLI, n'est pas émis par les workers embarqués.
    """
    nommer_worker(worker.hostname)


@pytest.fixture()
def app():
    """App Celery sur transport mémoire : une vraie file, sans Redis ni réseau."""
    return create_app(broker_url="memory://", result_backend="cache+memory://")


@pytest.fixture(autouse=True)
def _worker_reinitialise():
    """Rend au worker sa configuration par défaut après chaque test.

    Neutralise aussi la relance automatique (#91), armée par défaut sur les
    workers : ces tests comptent les appels modèle et simulent des pannes —
    la relance elle-même est couverte par test_retry.py (et le câblage worker
    par `test_le_worker_relance_un_echec_transitoire`).
    """
    configurer_worker(relance=PolitiqueRelance(max_tentatives=1))
    yield
    reinitialiser_worker()


@pytest.fixture(autouse=True)
def _depot_mcp_isole(tmp_path, monkeypatch):
    """Isole les workers de test des déclarations MCP versionnées du dépôt.

    Le worker relit `MAESTRO_MCP_DIR` (défaut : `core/mcp/`) à chaque tâche :
    sans cette isolation, une déclaration réelle (ex. `core/mcp/qa.json`, #106)
    s'appliquerait aux exécutants factices — et ferait échouer leurs tâches là
    où ses secrets (`${GITLAB_TOKEN}`) n'existent pas, comme en CI.
    """
    monkeypatch.setenv("MAESTRO_MCP_DIR", str(tmp_path / "mcp"))


class EchoProvider(ModelProvider):
    """Exécutant factice : renvoie toujours le même livrable."""

    name = "echo"

    def __init__(self, reponse: str) -> None:
        self._reponse = reponse

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._reponse


class RecordingProvider(ModelProvider):
    """Exécutant factice : enregistre chaque appel et renvoie un livrable numéroté."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return f"LIVRABLE pour l'appel #{len(self.calls)}"


class ToolingProvider(ModelProvider):
    """Exécutant outillé factice : `run_agent` écrit des fichiers dans le workspace."""

    name = "tooling"

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools, mcp_serveurs=()
    ):
        for chemin, contenu in self._files.items():
            (Path(workspace) / chemin).write_text(contenu, encoding="utf-8")
        return "OUTILLE"


class FailingProvider(ModelProvider):
    """Exécutant factice en panne : chaque appel lève."""

    name = "failing"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("panne du modèle")


class CompteurDePannesProvider(ModelProvider):
    """Exécutant factice en panne qui **compte** ses appels.

    Sert à prouver qu'aucune tâche aval n'a été mise en file (#43) : un appel
    modèle = une tâche consommée par un worker. Les workers embarqués partagent
    le process du test, le compteur est donc directement observable.
    """

    name = "compteur-pannes"

    def __init__(self) -> None:
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        raise RuntimeError("panne du modèle")


class BarrierProvider(ModelProvider):
    """Force la simultanéité entre workers : chaque appel attend l'autre au rendez-vous.

    Les workers embarqués sont des threads : un `threading.Barrier(2)` ne se
    franchit que si deux tâches s'exécutent en même temps sur deux workers.
    En exécution séquentielle, le premier appel casse la barrière au bout du
    timeout — échec de tâche net plutôt que suite de tests suspendue.
    """

    name = "barrier"

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._barrier.wait(timeout=15)
        return "LIVRABLE simultané"


def _tache(
    id: str = "endpoints-api",
    *,
    competences: tuple[str, ...] = ("backend",),
    dependances: tuple[str, ...] = (),
) -> dict[str, object]:
    """Dict de tâche conforme au schéma partagé (mots sensibles évités : #9)."""
    return {
        "id": id,
        "titre": f"Tâche {id}",
        "description": f"Réaliser la tâche {id}.",
        "competences_requises": list(competences),
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _attendre_etat(async_result, etat: str, *, timeout: float = 10.0) -> None:
    """Attend que la tâche Celery atteigne `etat` (remontée de statut, backend)."""
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        if async_result.state == etat:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"état {etat!r} non atteint en {timeout:g} s (actuel : {async_result.state!r})"
    )


# --- Critères ①/③ : mise en file découplée, exécution, remontée du résultat -----------


def test_mise_en_file_execution_et_remontee_du_resultat(app):
    configurer_worker(provider_factory=lambda: EchoProvider("LIVRABLE distribué"))

    # Mise en file AVANT tout worker : le message attend dans la file (découplage).
    async_result = app.send_task(NOM_TACHE_EXECUTER, args=[_tache(), [], "run-q1"])
    assert async_result.state == "PENDING"

    # Un worker démarré ensuite consomme la tâche et le résultat remonte.
    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        brut = async_result.get(timeout=30)

    assert async_result.state == "SUCCESS"  # le statut remonte au backend
    result = TaskResult.from_dict(brut)
    assert result.ok
    assert result.statut == "terminee"
    assert result.sortie == "LIVRABLE distribué"
    assert result.agent == "developpeur"  # routé côté worker (compétence backend)
    assert result.worker == "agent1@maestro"
    assert result.usage.duree_ms is not None  # usage mesuré côté worker


def test_le_tableau_noir_voyage_avec_la_tache(app):
    provider = RecordingProvider()
    configurer_worker(provider_factory=lambda: provider)
    dependance = TaskResult(
        task_id="schema-bdd",
        titre="Schéma BDD",
        agent="bdd",
        role="Base de données",
        competences_requises=("sql",),
        score=1,
        statut="terminee",
        sortie="SCHEMA LIVRÉ",
    )

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(
            NOM_TACHE_EXECUTER,
            args=[
                _tache("endpoints-api", dependances=("schema-bdd",)),
                [dependance.to_dict()],
                "run-q2",
            ],
        )
        brut = async_result.get(timeout=30)

    # Le worker a vu le livrable de la dépendance dans son prompt (tableau noir).
    assert TaskResult.from_dict(brut).ok
    assert "SCHEMA LIVRÉ" in str(provider.calls[0]["prompt"])


def test_les_fichiers_produits_remontent_en_artefacts(app):
    configurer_worker(
        provider_factory=lambda: ToolingProvider({"livrable.txt": "contenu produit"})
    )

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(NOM_TACHE_EXECUTER, args=[_tache(), [], "run-q3"])
        brut = async_result.get(timeout=30)

    result = TaskResult.from_dict(brut)
    assert result.ok
    assert result.fichiers == (ProducedFile(chemin="livrable.txt", contenu="contenu produit"),)


def test_le_playbook_edite_s_applique_au_worker_sans_redemarrage(app, tmp_path, monkeypatch):
    # Application à chaud côté worker (#78, tests différés du parent #74 → #75) :
    # l'exécuteur du worker — construit une fois au premier message — relit le
    # dépôt (`MAESTRO_PLAYBOOKS_DIR`) à chaque tâche consommée. Une version
    # publiée entre deux messages vaut pour le suivant, et la version utilisée
    # remonte estampillée sur le résultat.
    from maestro.agents.playbooks import PlaybookStore

    monkeypatch.setenv("MAESTRO_PLAYBOOKS_DIR", str(tmp_path))
    provider = RecordingProvider()
    configurer_worker(provider_factory=lambda: provider)

    with start_worker(app, pool="solo", perform_ping_check=False):
        avant = app.send_task(NOM_TACHE_EXECUTER, args=[_tache("avant-edition"), [], "run-pb"])
        premier = TaskResult.from_dict(avant.get(timeout=30))
        PlaybookStore(tmp_path).ecrire("developpeur", "Playbook édité pour le worker.")
        apres = app.send_task(NOM_TACHE_EXECUTER, args=[_tache("apres-edition"), [], "run-pb"])
        second = TaskResult.from_dict(apres.get(timeout=30))

    assert premier.playbook_version is None  # dépôt vide : prompt du code
    assert second.playbook_version == 1  # l'édition a pris effet sans redémarrage
    assert provider.calls[-1]["system_prompt"] == "Playbook édité pour le worker."


def test_un_echec_d_agent_remonte_en_echec_de_tache(app):
    # Échec *métier* : la tâche Celery réussit, le TaskResult porte le statut echec.
    configurer_worker(provider_factory=lambda: FailingProvider())

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(NOM_TACHE_EXECUTER, args=[_tache(), [], "run-q4"])
        brut = async_result.get(timeout=30)

    assert async_result.state == "SUCCESS"
    result = TaskResult.from_dict(brut)
    assert result.statut == "echec"
    assert "panne du modèle" in (result.erreur or "")


def test_le_worker_relance_un_echec_transitoire(app):
    # Câblage worker de la relance automatique (#91, ENF-06) : un aléa
    # fournisseur transitoire est relancé côté worker — la tâche aboutit et le
    # résultat remonte en succès (la mécanique complète est dans test_retry.py).
    class FlakyProvider(ModelProvider):
        name = "flaky-worker"

        def __init__(self) -> None:
            self.appels = 0

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            self.appels += 1
            if self.appels == 1:
                raise RuntimeError("aléa SDK simulé")
            return "LIVRABLE après relance"

    provider = FlakyProvider()
    configurer_worker(
        provider_factory=lambda: provider,
        relance=PolitiqueRelance(max_tentatives=2, backoff_s=0),
    )

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(NOM_TACHE_EXECUTER, args=[_tache(), [], "run-q4b"])
        brut = async_result.get(timeout=30)

    result = TaskResult.from_dict(brut)
    assert result.statut == "terminee"
    assert result.sortie == "LIVRABLE après relance"
    assert provider.appels == 2


def test_une_tache_malformee_est_refusee_sans_execution(app):
    # Échec de *payload* : refus avant exécution, FAILURE côté Celery.
    provider = RecordingProvider()
    configurer_worker(provider_factory=lambda: provider)
    malformee = {"id": "sans-description", "titre": "Tâche invalide"}

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(NOM_TACHE_EXECUTER, args=[malformee, [], "run-q5"])
        with pytest.raises(Exception, match="invalide"):
            async_result.get(timeout=30)

    assert async_result.state == "FAILURE"
    assert provider.calls == []  # jamais exécutée


# --- Critère ② : 2 agents en parallèle sur des workers distincts ----------------------


def test_deux_agents_en_parallele_sur_des_workers_distincts(app):
    barrier = threading.Barrier(2)
    configurer_worker(provider_factory=lambda: BarrierProvider(barrier))

    with (
        start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"),
        start_worker(app, pool="solo", perform_ping_check=False, hostname="agent2@maestro"),
    ):
        # La 1re tâche occupe un worker (bloquée au rendez-vous)…
        r1 = app.send_task(NOM_TACHE_EXECUTER, args=[_tache("endpoints-api"), [], "run-p"])
        _attendre_etat(r1, "STARTED")
        # …la 2e ne peut donc aboutir que sur l'AUTRE worker, en même temps.
        r2 = app.send_task(
            NOM_TACHE_EXECUTER,
            args=[_tache("suite-de-tests", competences=("tests",)), [], "run-p"],
        )
        resultats = [TaskResult.from_dict(r.get(timeout=30)) for r in (r1, r2)]

    assert all(r.ok for r in resultats)  # le rendez-vous a été franchi : simultanéité
    assert not barrier.broken
    # Deux agents distincts, sur deux workers distincts (critère MVP n°2).
    assert {r.agent for r in resultats} == {"developpeur", "qa"}
    assert {r.worker for r in resultats} == {"agent1@maestro", "agent2@maestro"}


# --- Critère ④ : la boucle complète via la file (orchestrateur → workers → rapport) ---


def _plan_json() -> str:
    # 3 tâches en chaîne : bdd → developpeur → qa (mêmes compétences que test_engine).
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma des tâches.",
                "competences_requises": ["sql", "schema"],
                "format_sortie": "Fichier SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API",
                "dependances": ["schema-bdd"],
            },
            {
                "id": "tests-api",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


def test_boucle_complete_via_la_file(app):
    # L'orchestrateur planifie en local (fournisseur factice) ; l'exécution part
    # dans la file et revient des workers — la boucle ne change pas.
    exec_provider = RecordingProvider()
    configurer_worker(provider_factory=lambda: exec_provider)
    planner = EchoProvider(_plan_json())
    engine = OrchestrationEngine(
        planner,
        Orchestrator(planner, model="claude-opus-4-8"),
        executor=CeleryExecutor(app, timeout_s=30),
    )
    journal = RunJournal(run_id="run-file")

    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        report = asyncio.run(engine.run("Créer une API de gestion de tâches", journal=journal))

    # Le rapport agrège les résultats remontés de la file, dans l'ordre du plan.
    assert [r.agent for r in report.resultats] == ["bdd", "developpeur", "qa"]
    assert all(r.ok for r in report.resultats)
    assert all(r.worker == "agent1@maestro" for r in report.resultats)
    assert report.run_id == "run-file"

    # Les dépendances (tableau noir) ont voyagé avec les tâches jusqu'aux workers.
    assert "appel #1" in str(exec_provider.calls[1]["prompt"])
    assert "appel #2" in str(exec_provider.calls[2]["prompt"])

    # Chaque étape est consignée au journal de l'orchestrateur.
    assert [rec.etape for rec in journal.records] == [
        "planification",
        "schema-bdd",
        "api-taches",
        "tests-api",
    ]
    assert all(rec.statut == "terminee" for rec in journal.records)


def test_une_dependance_en_echec_ne_met_jamais_l_aval_en_file(app):
    # DAG (#43) : le plan est une chaîne et sa racine échoue côté worker. Les
    # tâches aval ne doivent jamais devenir des messages : un seul appel modèle
    # atteint les workers, l'aval est bloqué côté orchestrateur (statut explicite).
    exec_provider = CompteurDePannesProvider()
    configurer_worker(provider_factory=lambda: exec_provider)
    planner = EchoProvider(_plan_json())
    engine = OrchestrationEngine(
        planner,
        Orchestrator(planner, model="claude-opus-4-8"),
        executor=CeleryExecutor(app, timeout_s=30),
    )
    journal = RunJournal(run_id="run-bloque")

    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        report = asyncio.run(engine.run("Créer une API de gestion de tâches", journal=journal))

    # Seule la racine a été mise en file et consommée (un unique appel modèle).
    assert exec_provider.appels == 1
    racine, *aval = report.resultats
    assert racine.statut == "echec"
    assert racine.worker == "agent1@maestro"
    # L'aval est bloqué sans exécution : statut explicite, aucun worker touché.
    assert [r.statut for r in aval] == ["bloquee", "bloquee"]
    assert all(r.worker == "" for r in aval)
    assert [rec.statut for rec in journal.records] == [
        "terminee",  # planification
        "echec",  # schema-bdd (racine)
        "bloquee",  # api-taches
        "bloquee",  # tests-api
    ]


def test_sans_worker_le_delai_devient_un_echec_consigne(app):
    # File dédiée au test : le message orphelin ne pollue pas les autres tests.
    app.conf.task_default_queue = "maestro.taches.orpheline"
    executor = CeleryExecutor(app, timeout_s=0.5)
    task = Task.from_dict(_tache())
    journal = RunJournal(run_id="run-vide")

    result = asyncio.run(executor.execute(task, [], journal))

    assert result.statut == "echec"
    assert "résultat non remonté" in (result.erreur or "")
    assert journal.records[-1].etape == "endpoints-api"
    assert journal.records[-1].statut == "echec"


def test_timeout_invalide_est_refuse(app):
    with pytest.raises(ValueError):
        CeleryExecutor(app, timeout_s=0)


# --- Sérialisation : l'aller-retour JSON de la remontée est fidèle --------------------


def test_task_result_fait_l_aller_retour_json():
    result = TaskResult(
        task_id="endpoints-api",
        titre="API",
        agent="developpeur",
        role="Développeur",
        competences_requises=("backend", "api"),
        score=2,
        statut="terminee",
        sortie="LIVRABLE",
        fichiers=(ProducedFile(chemin="livrable.txt", contenu="contenu"),),
        usage=StepUsage(appels=1, tokens_entree=100, tokens_sortie=20, cout_usd=0.01),
        worker="agent1@maestro",
        playbook_version=3,
    )

    rejoue = TaskResult.from_dict(json.loads(json.dumps(result.to_dict())))

    assert rejoue == result


def test_task_result_tolere_l_absence_de_version_de_playbook():
    # Un résultat émis avant #78 (ou par un worker sans dépôt) reste lisible.
    result = TaskResult.from_dict(
        {"task_id": "t1", "titre": "T", "agent": "developpeur", "role": "Développeur",
         "competences_requises": [], "score": 1, "statut": "terminee", "sortie": "ok"}
    )

    assert result.playbook_version is None


def test_step_usage_from_dict_tolere_les_champs_derives_et_absents():
    usage = StepUsage.from_dict({"appels": 2, "tokens_total": 999, "outils": ["Bash"]})

    assert usage.appels == 2
    assert usage.outils == ("Bash",)
    assert usage.tokens_total == 0  # dérivé recalculé, pas relu
    assert usage.cout_usd is None
