"""Integration tests of the task queue: Celery + Redis (ticket #41).

No Redis and no network required: the queue runs on Kombu's **in-memory**
transport (broker `memory://`, backend `cache+memory://`) with **real Celery
workers** embedded as threads. Queuing, consumption and reporting are those of
production; only the transport and the provider (a fake, no model call) differ.
Covers the acceptance criteria of ticket #41:

① the queue **receives the orchestrator's tasks**, with queuing and consumption
   decoupled: a task sent with no worker stays pending, and a worker started
   afterwards consumes it (`send_task` by name, no import of the worker code);
② at least **2 agents in parallel on distinct workers**: a forced inter-worker
   rendezvous (sequential ⇒ clear failure), distinct worker names on the
   results;
③ the **status and the result come back** to the orchestrator: success, failure
   (business failure as well as payload failure), produced files (artifacts),
   usage measured on the worker side;
④ queuing → execution → reporting is covered end to end, including through the
   full loop (`OrchestrationEngine` + `CeleryExecutor`): dependencies (the
   blackboard) passed to the workers, aggregated report, recorded journal.

Plus the DAG (#43): a task one of whose dependencies failed is **never queued**:
the orchestrator blocks it explicitly, and no message is left orphaned. And the
versioned playbooks (#78, #75): a version published between two messages applies
to the next one without restarting the worker, and the version used comes back
with the result.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker, test_worker_started

from maestro.engine import MODE_BRIEF_AUTO, OrchestrationEngine, PolitiqueRelance, TaskResult
from maestro.orchestrator import BRIEF_SYSTEM_PROMPT, Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.queue import EXECUTE_TASK_NAME, CeleryExecutor, create_app
from maestro.queue.worker import configure_worker, name_worker, reset_worker
from maestro.sandbox import ProducedFile
from maestro.telemetry import RunJournal, StepUsage


@test_worker_started.connect
def _name_the_embedded_worker(sender=None, worker=None, **kwargs):
    """Set the nodename of the embedded workers (one test worker is one thread).

    The signal is emitted in the worker's thread: `name_worker` sets there the name
    the results report. `celeryd_after_setup`, which does it for a real CLI worker,
    is not emitted by embedded workers.
    """
    name_worker(worker.hostname)


@pytest.fixture()
def app():
    """Celery app on the in-memory transport: a real queue, without Redis or network."""
    return create_app(broker_url="memory://", result_backend="cache+memory://")


@pytest.fixture(autouse=True)
def _worker_reset():
    """Restore the worker's default configuration after each test.

    Also turns off the automatic retry (#91), on by default on the workers: these
    tests count model calls and simulate failures. The retry itself is covered by
    test_retry.py (and its worker wiring by
    `test_the_worker_retries_a_transient_failure`).

    Turns off delivery verification (#1177) likewise: these doubles do not play the
    verifier, and it is covered by `test_verification_taches.py`, worker wiring
    included (`test_le_worker_verifie_ses_livraisons_par_defaut`).
    """
    configure_worker(retry=PolitiqueRelance(max_tentatives=1), verification=False)
    yield
    reset_worker()


@pytest.fixture(autouse=True)
def _isolated_mcp_store(tmp_path, monkeypatch):
    """Isolate the test workers from the repository's versioned MCP declarations.

    The worker reads `MAESTRO_MCP_DIR` (default: `core/mcp/`) again at each task:
    without this isolation, a real declaration (e.g. `core/mcp/qa.json`, #106)
    would apply to the fake executors, and would fail their tasks wherever its
    secrets (`${GITLAB_TOKEN}`) do not exist, as in CI.
    """
    monkeypatch.setenv("MAESTRO_MCP_DIR", str(tmp_path / "mcp"))


class EchoProvider(ModelProvider):
    """Fake executor: always returns the same deliverable."""

    name = "echo"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class RecordingProvider(ModelProvider):
    """Fake executor: records each call and returns a numbered deliverable."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return f"LIVRABLE pour l'appel #{len(self.calls)}"


class ToolingProvider(ModelProvider):
    """Fake tooled executor: `run_agent` writes files into the workspace."""

    name = "tooling"

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None,
        on_arbitrage=None, on_blocage=None, on_decision=None, credit_arbitrage=None,
        on_courrier=None, on_question=None,
        on_processus=None,
        plafond_tours=None, projet=None,
    ):
        for path, content in self._files.items():
            (Path(workspace) / path).write_text(content, encoding="utf-8")
        return "OUTILLE"


class FailingProvider(ModelProvider):
    """Failing fake executor: every call raises."""

    name = "failing"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("panne du modèle")


class FailureCountingProvider(ModelProvider):
    """Failing fake executor that **counts** its calls.

    Proves that no downstream task was queued (#43): one model call is one task
    consumed by a worker. The embedded workers share the test's process, so the
    counter is directly observable.
    """

    name = "compteur-pannes"

    def __init__(self) -> None:
        self.call_count = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.call_count += 1
        raise RuntimeError("panne du modèle")


class BarrierProvider(ModelProvider):
    """Forces simultaneity between workers: each call waits for the other at a rendezvous.

    The embedded workers are threads: a `threading.Barrier(2)` is only crossed if
    two tasks run at the same time on two workers. In a sequential run, the first
    call breaks the barrier once the timeout elapses: a clear task failure rather
    than a hung test suite.
    """

    name = "barrier"

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._barrier.wait(timeout=15)
        return "LIVRABLE simultané"


def _task(
    id: str = "endpoints-api",
    *,
    competencies: tuple[str, ...] = ("backend",),
    dependencies: tuple[str, ...] = (),
) -> dict[str, object]:
    """A task dict that conforms to the shared schema (sensitive words avoided: #9)."""
    return {
        "id": id,
        "titre": f"Tâche {id}",
        "description": f"Réaliser la tâche {id}.",
        "competences_requises": list(competencies),
        "format_sortie": "Texte",
        "dependances": list(dependencies),
    }


def _wait_for_state(async_result, state: str, *, timeout: float = 10.0) -> None:
    """Wait until the Celery task reaches `state` (status reported by the backend)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if async_result.state == state:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"état {state!r} non atteint en {timeout:g} s (actuel : {async_result.state!r})"
    )


# --- Criteria ①/③: decoupled queuing, execution, result reporting ---------------------


def test_a_queued_task_is_executed_and_its_result_comes_back(app):
    configure_worker(provider_factory=lambda: EchoProvider("LIVRABLE distribué"))

    # Queued BEFORE any worker: the message waits in the queue (decoupling).
    async_result = app.send_task(EXECUTE_TASK_NAME, args=[_task(), [], "run-q1"])
    assert async_result.state == "PENDING"

    # A worker started afterwards consumes the task and the result comes back.
    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        raw = async_result.get(timeout=30)

    assert async_result.state == "SUCCESS"  # the status reaches the backend
    result = TaskResult.from_dict(raw)
    assert result.ok
    assert result.statut == "terminee"
    assert result.sortie == "LIVRABLE distribué"
    assert result.agent == "developpeur"  # routed on the worker side (backend competency)
    assert result.worker == "agent1@maestro"
    assert result.usage.duration_ms is not None  # usage measured on the worker side


def test_the_blackboard_travels_with_the_task(app):
    provider = RecordingProvider()
    configure_worker(provider_factory=lambda: provider)
    dependency = TaskResult(
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
            EXECUTE_TASK_NAME,
            args=[
                _task("endpoints-api", dependencies=("schema-bdd",)),
                [dependency.to_dict()],
                "run-q2",
            ],
        )
        raw = async_result.get(timeout=30)

    # The worker saw the dependency's deliverable in its prompt (the blackboard).
    assert TaskResult.from_dict(raw).ok
    assert "SCHEMA LIVRÉ" in str(provider.calls[0]["prompt"])


def test_produced_files_come_back_as_artifacts(app):
    configure_worker(
        provider_factory=lambda: ToolingProvider({"livrable.txt": "contenu produit"})
    )

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(EXECUTE_TASK_NAME, args=[_task(), [], "run-q3"])
        raw = async_result.get(timeout=30)

    result = TaskResult.from_dict(raw)
    assert result.ok
    assert result.fichiers == (ProducedFile(chemin="livrable.txt", contenu="contenu produit"),)


def test_an_edited_playbook_applies_to_the_worker_without_restart(app, tmp_path, monkeypatch):
    # Hot application on the worker side (#78, #75): the worker's executor, built
    # once on the first message, reads the store (`MAESTRO_PLAYBOOKS_DIR`) again at
    # each consumed task. A version published between two messages holds for the
    # next one, and the version used comes back stamped on the result.
    from maestro.agents.playbooks import PlaybookStore

    monkeypatch.setenv("MAESTRO_PLAYBOOKS_DIR", str(tmp_path))
    provider = RecordingProvider()
    configure_worker(provider_factory=lambda: provider)

    with start_worker(app, pool="solo", perform_ping_check=False):
        before = app.send_task(EXECUTE_TASK_NAME, args=[_task("avant-edition"), [], "run-pb"])
        first = TaskResult.from_dict(before.get(timeout=30))
        PlaybookStore(tmp_path).ecrire("developpeur", "Playbook édité pour le worker.")
        after = app.send_task(EXECUTE_TASK_NAME, args=[_task("apres-edition"), [], "run-pb"])
        second = TaskResult.from_dict(after.get(timeout=30))

    assert first.playbook_version is None  # empty store: the code's prompt
    assert second.playbook_version == 1  # the edit took effect without a restart
    assert provider.calls[-1]["system_prompt"] == "Playbook édité pour le worker."


def test_an_agent_failure_comes_back_as_a_task_failure(app):
    # *Business* failure: the Celery task succeeds, the TaskResult carries the failed status.
    configure_worker(provider_factory=lambda: FailingProvider())

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(EXECUTE_TASK_NAME, args=[_task(), [], "run-q4"])
        raw = async_result.get(timeout=30)

    assert async_result.state == "SUCCESS"
    result = TaskResult.from_dict(raw)
    assert result.statut == "echec"
    assert "panne du modèle" in (result.erreur or "")


def test_the_worker_retries_a_transient_failure(app):
    # Worker wiring of the automatic retry (#91, ENF-06): a transient provider
    # failure is retried on the worker side, the task succeeds and the result comes
    # back as a success (the full mechanism is in test_retry.py).
    class FlakyProvider(ModelProvider):
        name = "flaky-worker"

        def __init__(self) -> None:
            self.call_count = 0

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("aléa SDK simulé")
            return "LIVRABLE après relance"

    provider = FlakyProvider()
    configure_worker(
        provider_factory=lambda: provider,
        retry=PolitiqueRelance(max_tentatives=2, backoff_s=0),
    )

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(EXECUTE_TASK_NAME, args=[_task(), [], "run-q4b"])
        raw = async_result.get(timeout=30)

    result = TaskResult.from_dict(raw)
    assert result.statut == "terminee"
    assert result.sortie == "LIVRABLE après relance"
    assert provider.call_count == 2


def test_a_malformed_task_is_refused_without_running(app):
    # *Payload* failure: refused before running, FAILURE on the Celery side.
    provider = RecordingProvider()
    configure_worker(provider_factory=lambda: provider)
    malformed = {"id": "sans-description", "titre": "Tâche invalide"}

    with start_worker(app, pool="solo", perform_ping_check=False):
        async_result = app.send_task(EXECUTE_TASK_NAME, args=[malformed, [], "run-q5"])
        with pytest.raises(Exception, match="invalide"):
            async_result.get(timeout=30)

    assert async_result.state == "FAILURE"
    assert provider.calls == []  # never run


# --- Criterion ②: 2 agents in parallel on distinct workers ----------------------------


def test_two_agents_in_parallel_on_distinct_workers(app):
    barrier = threading.Barrier(2)
    configure_worker(provider_factory=lambda: BarrierProvider(barrier))

    with (
        start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"),
        start_worker(app, pool="solo", perform_ping_check=False, hostname="agent2@maestro"),
    ):
        # The 1st task holds one worker (blocked at the rendezvous)…
        r1 = app.send_task(EXECUTE_TASK_NAME, args=[_task("endpoints-api"), [], "run-p"])
        _wait_for_state(r1, "STARTED")
        # …so the 2nd one can only succeed on the OTHER worker, at the same time.
        r2 = app.send_task(
            EXECUTE_TASK_NAME,
            args=[_task("suite-de-tests", competencies=("tests",)), [], "run-p"],
        )
        results = [TaskResult.from_dict(r.get(timeout=30)) for r in (r1, r2)]

    assert all(r.ok for r in results)  # the rendezvous was crossed: simultaneity
    assert not barrier.broken
    # Two distinct agents, on two distinct workers (MVP criterion no. 2).
    assert {r.agent for r in results} == {"developpeur", "qa"}
    assert {r.worker for r in results} == {"agent1@maestro", "agent2@maestro"}


# --- Criterion ④: the full loop through the queue (orchestrator → workers → report) ---


def _plan_json() -> str:
    # 3 chained tasks: bdd → developpeur → qa (same competencies as test_engine).
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


def test_full_loop_through_the_queue(app):
    # The orchestrator plans locally (fake provider); the execution goes into the
    # queue and comes back from the workers: the loop does not change.
    exec_provider = RecordingProvider()
    configure_worker(provider_factory=lambda: exec_provider)
    planner = EchoProvider(_plan_json())
    engine = OrchestrationEngine(
        planner,
        Orchestrator(planner, model="claude-opus-4-8"),
        executor=CeleryExecutor(app, timeout_s=30),
    )
    journal = RunJournal(run_id="run-file")

    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        report = asyncio.run(engine.run("Créer une API de gestion de tâches", journal=journal))

    # The report aggregates the results that came back from the queue, in plan order.
    assert [r.agent for r in report.resultats] == ["bdd", "developpeur", "qa"]
    assert all(r.ok for r in report.resultats)
    assert all(r.worker == "agent1@maestro" for r in report.resultats)
    assert report.run_id == "run-file"

    # The dependencies (the blackboard) travelled with the tasks to the workers.
    assert "appel #1" in str(exec_provider.calls[1]["prompt"])
    assert "appel #2" in str(exec_provider.calls[2]["prompt"])

    # Each step is recorded in the orchestrator's journal, and the cadence is stated
    # before the first task (#1298): the chain's tasks run one at a time.
    assert [rec.etape for rec in journal.records] == [
        "planification",
        "cadence",
        "schema-bdd",
        "api-taches",
        "tests-api",
    ]
    assert all(
        rec.statut == "terminee" for rec in journal.records if rec.etape != "cadence"
    )


class FramingProvider(ModelProvider):
    """Fake planner that also drafts the brief: the brief at its step, the plan otherwise."""

    name = "cadrage"

    def __init__(self, brief: dict[str, object]) -> None:
        self._brief = brief

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == BRIEF_SYSTEM_PROMPT:
            return json.dumps(self._brief, ensure_ascii=False)
        return _plan_json()


def test_the_approved_brief_travels_to_the_workers(app):
    """#1402: each task receives the run's brief, even when run on a remote worker.

    The worker shares nothing with the orchestrator: the brief travels with each
    message, like the blackboard. The prompt of each consumed task carries it, and
    so does the entry the orchestrator records in the journal.
    """
    exec_provider = RecordingProvider()
    configure_worker(provider_factory=lambda: exec_provider)
    planner = FramingProvider(
        {
            "objectif": "Une API de gestion de tâches",
            "perimetre": ["CRUD des tâches"],
            "hors_perimetre": ["Authentification"],
            "criteres_acceptation": ["Une tâche se crée et se relit"],
        }
    )
    engine = OrchestrationEngine(
        planner,
        Orchestrator(planner, model="claude-opus-4-8"),
        executor=CeleryExecutor(app, timeout_s=30),
    )
    journal = RunJournal(run_id="run-brief")

    with start_worker(app, pool="solo", perform_ping_check=False):
        report = asyncio.run(
            engine.run("Créer une API", journal=journal, mode_brief=MODE_BRIEF_AUTO)
        )

    assert all(r.ok for r in report.resultats)
    assert len(exec_provider.calls) == 3
    for call in exec_provider.calls:
        prompt = str(call["prompt"])
        assert "Brief approuvé du run" in prompt
        assert "Hors périmètre :\n- Authentification" in prompt
        assert "- Une tâche se crée et se relit" in prompt
    for step in ("schema-bdd", "api-taches", "tests-api"):
        entry = next(r.entree for r in journal.records if r.etape == step)
        assert "Brief approuvé du run" in entry


def test_a_failed_dependency_never_queues_the_downstream_tasks(app):
    # DAG (#43): the plan is a chain and its root fails on the worker side. The
    # downstream tasks must never become messages: a single model call reaches the
    # workers, and the downstream tasks are blocked by the orchestrator (explicit
    # status).
    exec_provider = FailureCountingProvider()
    configure_worker(provider_factory=lambda: exec_provider)
    planner = EchoProvider(_plan_json())
    engine = OrchestrationEngine(
        planner,
        Orchestrator(planner, model="claude-opus-4-8"),
        executor=CeleryExecutor(app, timeout_s=30),
    )
    journal = RunJournal(run_id="run-bloque")

    with start_worker(app, pool="solo", perform_ping_check=False, hostname="agent1@maestro"):
        report = asyncio.run(engine.run("Créer une API de gestion de tâches", journal=journal))

    # Only the root was queued and consumed (a single model call).
    assert exec_provider.call_count == 1
    root, *downstream = report.resultats
    assert root.statut == "echec"
    assert root.worker == "agent1@maestro"
    # The downstream tasks are blocked without running: explicit status, no worker hit.
    assert [r.statut for r in downstream] == ["bloquee", "bloquee"]
    assert all(r.worker == "" for r in downstream)
    assert [rec.statut for rec in journal.records] == [
        "terminee",  # planning
        "une_a_une",  # cadence (#1298): the plan is a chain
        "echec",  # schema-bdd (root)
        "bloquee",  # api-taches
        "bloquee",  # tests-api
    ]


def test_without_a_worker_the_timeout_becomes_a_recorded_failure(app):
    # A queue dedicated to the test: the orphaned message does not pollute other tests.
    app.conf.task_default_queue = "maestro.taches.orpheline"
    executor = CeleryExecutor(app, timeout_s=0.5)
    task = Task.from_dict(_task())
    journal = RunJournal(run_id="run-vide")

    result = asyncio.run(executor.execute(task, [], journal))

    assert result.statut == "echec"
    assert "résultat non remonté" in (result.erreur or "")
    assert journal.records[-1].etape == "endpoints-api"
    assert journal.records[-1].statut == "echec"


def test_an_invalid_timeout_is_refused(app):
    with pytest.raises(ValueError):
        CeleryExecutor(app, timeout_s=0)


# --- Serialization: the JSON round trip of reporting is faithful ----------------------


def test_task_result_round_trips_through_json():
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
        usage=StepUsage(calls=1, input_tokens=100, output_tokens=20, cost_usd=0.01),
        worker="agent1@maestro",
        playbook_version=3,
    )

    replayed = TaskResult.from_dict(json.loads(json.dumps(result.to_dict())))

    assert replayed == result


def test_task_result_tolerates_a_missing_playbook_version():
    # A result without a playbook version (#78, a worker without a store) stays readable.
    result = TaskResult.from_dict(
        {"task_id": "t1", "titre": "T", "agent": "developpeur", "role": "Développeur",
         "competences_requises": [], "score": 1, "statut": "terminee", "sortie": "ok"}
    )

    assert result.playbook_version is None


def test_step_usage_from_dict_tolerates_derived_and_missing_fields():
    usage = StepUsage.from_dict({"appels": 2, "tokens_total": 999, "outils": ["Bash"]})

    assert usage.calls == 2
    assert usage.tools == ("Bash",)
    assert usage.total_tokens == 0  # derived field recomputed, not read back
    assert usage.cost_usd is None
