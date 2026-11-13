"""Tests of the durable mode: Temporal workflows (#93, final lot of #92).

Exercises the criteria of #94–#97 on the **Temporal test environment**
(`temporalio.testing.WorkflowEnvironment`, *time-skipping* test server) with
**fake providers**: no real Temporal server, no container, no model call. That is
what makes the durable mode testable without a live server or a real provider.

Coverage:

① **one run = one workflow, one task = one activity** (#95): a durable run
   executes through `MaestroRunWorkflow` and its activities, and `DurableEngine.run`
   rebuilds the report and the **ledger** from the serialized aggregate;
② **downstream blocking** (#43): a failed dependency blocks the downstream task,
   which never reaches the execution activity (no model call wasted);
③ **failure recovery** (#96): the checkpoint (`checkpoint`) exposes what is already
   paid for; a process that resumes midway (`resume`) does **not pay upstream
   again** (tasks that succeeded are not run again); a run that already finished is
   resumed by simply returning its report; an unknown run_id raises a readable
   orchestration error;
④ **invalid planning**: the workflow fails and the failure is translated back into
   `OrchestratorError` (without a plan, nothing to orchestrate).

The persistence of the Control Tower state across an API restart (#97) is covered on
the projection side in `tests/test_controltower.py` (event journal replayed).

**Shared loop and environment.** The repository does not use `pytest-asyncio`: each
scenario is a coroutine driven on **one** dedicated asyncio loop (the Temporal client
is bound to the loop that created it). The test server, a binary downloaded once then
cached, is started **once for the whole module**. Without network access for that
download, the module is skipped with an explicit message; the CI has that access (it
already installs its dependencies from PyPI).

⚠ **Locked clock and queries (#457).** The *time-skipping* server keeps its clock
**stopped** outside `WorkflowHandle.result()`: no server timer ever fires there. That
has no effect as long as a query is served by the worker that executed the workflow,
which is the case of every scenario but one. A **resume**, however, queries a
workflow whose worker is gone: its query then goes to a "sticky" queue that nobody
polls any more, and the fallback that should catch it is precisely one of those
timers. `_TestEnv.with_unlocked_clock` (see its docstring) gives the clock back to
that single call; without it, the test would fail on the query deadline whenever the
first dispatch loses the race, and not on what it checks.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from temporalio.testing import WorkflowEnvironment

from maestro.durable import (
    DurableEngine,
    MaestroRunWorkflow,
    build_worker,
    workflow_id,
)
from maestro.durable.activities import configure_worker, reset_worker
from maestro.durable.engine import create_durable_engine
from maestro.orchestrator.errors import OrchestratorError
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal
from maestro.telemetry.costs import ETAPE_REPRISE

#: Demonstration goal shared by the scenarios (sensitive words avoided, #9).
_GOAL = "Créer une API de gestion de tâches"


# --- Fake provider: plans then executes, without ever calling a model -------------------


class FakeDurableProvider(ModelProvider):
    """Fake executor covering **both** roles of the durable worker.

    In durable mode, planning (activity `plan`) and execution (activity
    `execute_task`) run on the **same** worker provider (the one set by
    `configure_worker`). The `system_prompt` tells the two roles apart reliably:
    the orchestrator (#3) calls with `ORCHESTRATOR_SYSTEM_PROMPT` and expects a JSON
    plan; any other generation is a task execution.

    - `block`: token which, present in a task's prompt, makes it **wait** for
      `unblock` (simulates a task still in flight at the time of a resume);
    - `fail`: token which makes the execution **fail** (provider outage); the
      `LocalExecutor` turns it into a `TaskResult` with status `echec`, without raising.

    `executions` keeps the prompt of every **attempted** execution, in order: the
    material of the assertions "this task was (was not) executed / executed again".
    """

    name = "fake-durable"

    def __init__(
        self, plan_json: str, *, block: str | None = None, fail: str | None = None
    ) -> None:
        self._plan = plan_json
        self._block = block
        self._fail = fail
        self.unblock = asyncio.Event()
        self.executions: list[str] = []
        self.plans = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == ORCHESTRATOR_SYSTEM_PROMPT:
            self.plans += 1
            return self._plan
        self.executions.append(prompt)
        if self._fail and self._fail in prompt:
            raise RuntimeError(f"simulated provider outage ({self._fail})")
        if self._block and self._block in prompt:
            await self.unblock.wait()
        return f"LIVRABLE ({len(self.executions)})"

    def executions_of(self, token: str) -> list[str]:
        """The execution prompts of a task identified by `token`.

        The token is looked for on the **first line** of the prompt ("Tâche : <titre>"),
        the identity of the executed task, and nowhere else: the title or deliverable of
        a dependency (blackboard) would bring it back and inflate the count.
        """
        return [p for p in self.executions if token in p.splitlines()[0]]


# --- Fake plans (sensitive words avoided, #9): one unique token per task ----------------


def _task(task_id, *, title, description, competencies, dependencies=()):
    """Task dict that follows the orchestrator's shared schema."""
    return {
        "id": task_id,
        "titre": title,
        "description": description,
        "competences_requises": list(competencies),
        "format_sortie": "Texte",
        "dependances": list(dependencies),
    }


def _plan(*tasks) -> str:
    """Serialize a plan (the output a fake orchestrator would return)."""
    return json.dumps(list(tasks), ensure_ascii=False)


def _chain_plan() -> str:
    """Chain plan bdd → developpeur.

    The upper-case token (AMONT/AVAL) identifies each task in its execution prompt,
    where the `LocalExecutor` injects title and description: it is the probe of the
    assertions "this task did (did not) run".
    """
    return _plan(
        _task(
            "schema-bdd",
            title="Schéma BDD [AMONT]",
            description="Définir le schéma des tâches (jeton AMONT).",
            competencies=("sql", "schema"),
        ),
        _task(
            "api-taches",
            title="API des tâches [AVAL]",
            description="Exposer les tâches en lecture/écriture (jeton AVAL).",
            competencies=("backend", "api"),
            dependencies=("schema-bdd",),
        ),
    )


# --- Shared test environment: one loop, one time-skipping server -------------------------


class _TestEnv:
    """Dedicated loop + `WorkflowEnvironment`: drives an async scenario end to end."""

    def __init__(self, loop: asyncio.AbstractEventLoop, environment: WorkflowEnvironment) -> None:
        self.loop = loop
        self.environment = environment
        self.client = environment.client

    def run(self, coro):
        """Run a coroutine on the test server's loop (the client is bound to it)."""
        return self.loop.run_until_complete(coro)

    def with_unlocked_clock(self, coro):
        """`coro` run while the test server's clock **moves forward** (#457).

        Reserved for code that **queries** a workflow the current process's worker
        did not execute itself: in practice, a resume (`DurableEngine.resume`),
        which is its whole point.

        The server sends a query to the worker it believes holds the workflow in
        cache ("sticky" queue). If that worker is gone, a
        **`sticky_queue_schedule_to_start_timeout`** (10 s by default, see the SDK)
        drops it back onto the normal queue, where any worker serves it. That
        fallback is a **server timer**, so it runs on the server's clock, and
        `WorkflowEnvironment.start_time_skipping()` keeps that clock **locked**
        outside `WorkflowHandle.result()`. A query the first dispatch does not serve
        can then never be served: the fallback that should catch it never fires, and
        it runs out its RPC deadline (30 s). That is the
        `RPCError: Query deadline of 29999 milliseconds exceeded` of #457.

        Unlocking the clock for the duration of the call gives the test the
        mechanism that exists in production: the 10 s of the fallback pass in
        **virtual** time (the query is served in 0.02 s), so the verdict no longer
        depends on which of the dispatch or the pollers' startup wins the race. It
        is not a raised ceiling: without the fallback, no wait would be enough.
        """

        async def _run_unlocked():
            async with self.environment.time_skipping_unlocked():
                return await coro

        return _run_unlocked()


@pytest.fixture(scope="module")
def env():
    """Temporal test server (time-skipping) started once for the module.

    The binary is downloaded on first use then cached. Without network access for
    that download, the module is skipped rather than failing (the CI, which installs
    its dependencies from PyPI, has that access).
    """
    loop = asyncio.new_event_loop()
    try:
        environment = loop.run_until_complete(WorkflowEnvironment.start_time_skipping())
    except Exception as exc:  # pragma: no cover - depends on network/binary availability
        loop.close()
        pytest.skip(f"Temporal test environment unavailable ({exc})")
    try:
        yield _TestEnv(loop, environment)
    finally:
        loop.run_until_complete(environment.shutdown())
        loop.close()


@pytest.fixture(autouse=True)
def _worker_reset():
    """Give the worker back its default configuration after each test (provider, retry).

    Delivery verification (#1177), on by default on workers, is turned off during
    the test: its doubles do not play the verifier, and it is covered by
    `test_verification_taches.py`.
    """
    configure_worker(verification=False)
    yield
    reset_worker()


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    """Isolate the worker's executor from the repository's versioned stores (`core/…`).

    The `execute_task` activity goes through the **same** `LocalExecutor` as the
    in-process loop: it rereads MCP, agents, playbooks, capacity, secrets and
    permissions on the fly. Without isolation, a real declaration (e.g.
    `core/mcp/qa.json`, whose token does not exist in tests) would make the tasks fail,
    as in CI. Each store points to an empty temporary directory: execution falls back
    on the code's default agents, with no external dependency.
    """
    for var in (
        "MAESTRO_MCP_DIR",
        "MAESTRO_AGENTS_DIR",
        "MAESTRO_PLAYBOOKS_DIR",
        "MAESTRO_CAPACITE_DIR",
        "MAESTRO_SECRETS_DIR",
        "MAESTRO_PERMISSIONS_DIR",
    ):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))


@pytest.fixture(autouse=True)
def _client_on_test_env(env, monkeypatch):
    """Plug `DurableEngine` into the test server's client.

    The engine connects through `Client.connect(address)`; it is made to return the
    test server's *time-skipping* client, so that start, queries and resume run
    against the test environment (and not a real server).
    """

    async def _connect(*_args, **_kwargs):
        return env.client

    monkeypatch.setattr("maestro.durable.engine.Client.connect", _connect)


def _engine() -> DurableEngine:
    """A durable engine in **a single attempt** (application retry turned off).

    The tests count executions and simulate failures: the retry would hide the one
    and replay the other. The retry itself is covered by `test_retry.py`.
    """
    return DurableEngine(retry=None)


async def _workflow_input(goal: str, run_id: str) -> dict:
    """The workflow input, as `DurableEngine.run` builds it (no caps)."""
    return {
        "objectif": goal,
        "run_id": run_id,
        "plafond_cout_usd": None,
        "plafond_tokens": None,
    }


# --- ① One run = one workflow, one task = one activity (#95) -----------------------------


def test_a_durable_run_executes_the_plan_through_workflow_and_activities(env):
    """The run unfolds as workflow + activities; report and ledger are aggregated.

    Two chained tasks (bdd → developpeur): each is routed and executed by an
    activity, the result carries the Temporal worker's mark, and the report, rebuilt
    from the aggregate the workflow serializes, gives back the plan order and the
    ledger (one line per task + planning).
    """
    provider = FakeDurableProvider(_chain_plan())
    configure_worker(provider_factory=lambda: provider)
    journal = RunJournal(run_id="run-durable-ok")

    report = env.run(_engine().run(_GOAL, journal=journal))

    assert [r.agent for r in report.resultats] == ["bdd", "developpeur"]
    assert all(r.ok for r in report.resultats)
    assert all(r.worker.startswith("temporal/") for r in report.resultats)
    assert report.run_id == "run-durable-ok"

    # Ledger rebuilt from the aggregate: one entry per task + planning.
    ledger = report.grand_livre
    assert ledger.run_id == "run-durable-ok"
    assert {t.tache_id for t in ledger.taches} == {"schema-bdd", "api-taches"}
    assert provider.plans == 1  # planned once, through the `plan` activity
    # The blackboard travelled: the 2nd task saw the 1st one's deliverable in its prompt.
    assert any("LIVRABLE (1)" in p for p in provider.executions_of("AVAL"))


def test_create_durable_engine_reads_the_address_from_the_settings(env):
    """The `create_durable_engine` factory (counterpart of `OrchestrationEngine.default`) runs."""
    provider = FakeDurableProvider(_chain_plan())
    configure_worker(provider_factory=lambda: provider)
    engine = create_durable_engine(retry=None)

    report = env.run(engine.run(_GOAL, journal=RunJournal(run_id="run-fab")))

    assert len(report.resultats) == 2 and all(r.ok for r in report.resultats)


# --- ② Downstream blocking: a failed dependency never executes downstream (#43) ----------


def test_a_failed_dependency_blocks_downstream_without_executing_it(env):
    """The root fails: downstream is blocked (activity `record_blocker`), never executed."""
    provider = FakeDurableProvider(_chain_plan(), fail="AMONT")
    configure_worker(provider_factory=lambda: provider)

    report = env.run(_engine().run(_GOAL, journal=RunJournal(run_id="run-bloc")))

    upstream, downstream = report.resultats
    assert upstream.statut == "echec"
    assert downstream.statut == "bloquee"
    assert "schema-bdd" in (downstream.erreur or "")  # the blocker names the unmet dependency
    # Upstream was attempted (then failed); downstream never reached the provider.
    assert provider.executions_of("AMONT")  # attempted
    assert provider.executions_of("AVAL") == []  # never executed


# --- ③ Failure recovery: without paying upstream again (#96) -----------------------------


def test_resuming_a_finished_run_returns_the_report_without_rerunning_anything(env):
    """A run that already finished can be resumed: report returned, upstream **not paid again**.

    Models the process killed after the last task, before the display: the workflow
    is `COMPLETED` on the Temporal side. `resume` attaches a fresh process, records
    the resume (reason "already finished") and returns the report, without a single
    extra model call (the execution counter does not move).
    """
    provider = FakeDurableProvider(_chain_plan())
    configure_worker(provider_factory=lambda: provider)
    engine = _engine()

    report = env.run(engine.run(_GOAL, journal=RunJournal(run_id="run-fini")))
    executions_before = list(provider.executions)
    assert len(report.reussies) == 2

    resume_journal = RunJournal(run_id="run-fini")
    # `with_unlocked_clock`: the run's worker has just shut down, and the resume's
    # query needs the "sticky → normal queue" fallback, a server timer that the test
    # server's locked clock freezes (#457).
    resumed = env.run(env.with_unlocked_clock(engine.resume("run-fini", journal=resume_journal)))

    # Nothing executed again: upstream comes from the history, not from a new call.
    assert provider.executions == executions_before
    assert [r.task_id for r in resumed.resultats] == [r.task_id for r in report.resultats]
    # The resume is recorded (visible break), and does not enter the ledger (zero usage).
    resumes = [rec for rec in resume_journal.records if rec.etape == ETAPE_REPRISE]
    assert len(resumes) == 1
    assert "achevé" in resumes[0].sortie or "achevé" in resumes[0].entree


def test_the_checkpoint_exposes_upstream_while_downstream_is_in_flight(env):
    """`checkpoint` (#96): paid upstream shows as soon as it succeeds, in-flight downstream not.

    It is the base of resuming without paying upstream again: a resuming process reads
    this state to know what is already acquired. Downstream (`AVAL`) is held in flight
    by the provider: the checkpoint then exposes the plan and upstream, **not**
    downstream (nothing produced). Once downstream is unblocked, the run finishes, and
    upstream was never executed again (a single `AMONT` pass), its value coming from
    the workflow history.
    """
    provider = FakeDurableProvider(_chain_plan(), block="AVAL")
    configure_worker(provider_factory=lambda: provider)

    async def scenario():
        async with build_worker(env.client):
            handle = await env.client.start_workflow(
                MaestroRunWorkflow.run,
                await _workflow_input(_GOAL, "run-vol"),
                id=workflow_id("run-vol"),
                task_queue="maestro-durable",
            )
            # Wait for upstream to be acquired (blocked downstream never is before unblocking).
            for _ in range(4000):
                checkpoint = await handle.query(MaestroRunWorkflow.checkpoint)
                if any(r["task_id"] == "schema-bdd" for r in checkpoint["resultats"]):
                    break
                await asyncio.sleep(0.005)
            checkpoint = await handle.query(MaestroRunWorkflow.checkpoint)
            # upstream acquired…
            assert {r["task_id"] for r in checkpoint["resultats"]} == {"schema-bdd"}
            assert checkpoint["planification"] is not None  # …plan acquired, downstream in flight
            provider.unblock.set()  # downstream can finally succeed
            return await handle.result()

    aggregate = env.run(scenario())

    results = aggregate["resultats"]
    assert {r["task_id"] for r in results} == {"schema-bdd", "api-taches"}
    assert all(r["statut"] == "terminee" for r in results)
    assert len(provider.executions_of("AMONT")) == 1  # upstream never paid again


def test_resuming_an_unknown_run_raises_a_readable_error(env):
    """Resuming a run_id that no workflow carries: explicit `OrchestratorError`."""
    configure_worker(provider_factory=lambda: FakeDurableProvider(_chain_plan()))

    with pytest.raises(OrchestratorError, match="aucun run durable"):
        env.run(_engine().resume("run-fantome"))


# --- ④ Invalid planning: the workflow fails, translated back into OrchestratorError ------


def test_an_invalid_planning_fails_the_run(env):
    """An undecodable plan fails the workflow; the failure becomes `OrchestratorError`."""
    provider = FakeDurableProvider("ceci n'est pas un plan JSON")
    configure_worker(provider_factory=lambda: provider)

    with pytest.raises(OrchestratorError):
        env.run(_engine().run("Objectif hors sujet", journal=RunJournal(run_id="run-ko")))
    assert provider.executions == []  # no task executed: there never was a plan
