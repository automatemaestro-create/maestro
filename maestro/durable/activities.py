"""Temporal activities of the durable mode: all of a run's I/O (#95).

The durable mode makes a run a Temporal **workflow** (`maestro.durable.workflow`)
and each task an **activity**. The workflow must stay **deterministic**, so model
calls, clock and journaling live here, in the activities, never in the workflow
code. Three activities, all recording to the `RunJournal` (#8) exactly as the
in-process loop does: that is what keeps the Control Tower fed without any change
(real-time events #46, ledger #55). Wherever local execution or a queue worker
already records, the activity records the same way.

- `plan` runs the orchestrator (#3) and records the `planification` step;
- `execute_task` runs a task through the **same path as the local loop**
  (`maestro.engine.LocalExecutor`): routing, guardrails (#9: cap, timeout, human
  validation), tooled runtime or text call, usage measurement (#8). The guardrails
  therefore do go "through the activities" of the workflow;
- `record_blocker` turns a task downstream of a failure into a blocked
  `TaskResult` (#43). The workflow takes the decision, but it is **recorded here**
  (the journal is I/O).

The Temporal activity types are the registered names `planifier`,
`executer_tache` and `consigner_blocage` (`name=`): a workflow in flight on the
server addresses them by those names, so they never change.

The configuration **belongs to the worker's process** (provider, guardrails,
retry), set by `configure_worker` before consumption, the same model as
`maestro.queue.worker`. The human validator (#9) cannot be serialized, so it
cannot travel as an activity argument: it lives in the worker's process
(embedded in the CLI's, see `maestro.durable.engine`).

Retry: the application retry (#91, ENF-06) remains the only layer that catches
**provider hazards**. It lives in the activity, as close as possible to the model
call. Temporal never doubles that retry; it only catches what the application
retry cannot, because it dies with the process: the **loss of the worker** (#96).
The two layers therefore compose by sharing the domain, not by turning one of them
off (requirement of the parent #92):

- each activity keeps a **heartbeat** during its work (`_with_heartbeat`): a
  killed worker stops beating, the server notices within seconds and hands the
  activity to the next worker, instead of waiting for the "start-to-close" (up to
  an hour for a task);
- **deterministic application** failures (invalid plan, task that breaks the
  schema) are raised `non_retryable`: replaying them would never give another
  result, so neither layer tries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from time import perf_counter
from typing import Any, TypeVar

from temporalio import activity
from temporalio.exceptions import ApplicationError

from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import AgentStore, catalogue_hors_projet
from maestro.config import load_settings
from maestro.engine.executor import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskResult,
    _ecoule_ms,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import _consigne_blocage
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.engine.verification import VerificateurTaches
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task, validate_task
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, collect_usage

#: The worker's provider factory: called once per process, when the executor is
#: built lazily (as in `maestro.queue.worker`).
ProviderFactory = Callable[[], ModelProvider]

#: Heartbeat period of the activities (#96), well under the `heartbeat_timeout`
#: set on the workflow side (`ACTIVITY_HEARTBEAT`), so that a live but busy worker
#: is never taken for a dead one.
HEARTBEAT_PERIOD_S = 10.0

T = TypeVar("T")


def _configured_factory() -> ModelProvider:
    """The worker's default provider: the one the configuration names (#69).

    Imported here rather than at the top of the module, so that the worker stays
    agnostic of the provider: the choice lives in the configuration
    (`MAESTRO_PROVIDER`), as for `OrchestrationEngine.default`.
    """
    from maestro.providers.factory import provider_from_settings

    return provider_from_settings(load_settings())


_provider_factory: ProviderFactory = _configured_factory
_guardrails: Guardrails | None = None
#: Automatic retry of transient failures (#91, ENF-06), on by default, as on
#: `OrchestrationEngine.default()`. It is the **only** retry layer for provider
#: hazards (Temporal's activity retry only covers the loss of the worker, see
#: workflow).
_retry: PolitiqueRelance = RELANCE_DEFAUT
#: Delivery verification (#1177), on by default like the retry: an agent activity
#: is "Terminée" only once its criteria have been verified by running them. The
#: **QA send-back** lives in the in-process loop (`maestro.engine.loop`), not in
#: the durable workflow, which schedules its activities itself: a limit stated in
#: docs/45 §4.
_verification: bool = True
_executor: LocalExecutor | None = None


def configure_worker(
    *,
    provider_factory: ProviderFactory | None = None,
    guardrails: Guardrails | None = None,
    retry: PolitiqueRelance | None = None,
    verification: bool | None = None,
) -> None:
    """Configure execution in THIS worker process (provider, guardrails, retry).

    Call it before consumption (worker startup, test fixture, or
    `maestro.durable.engine.DurableEngine.run` for the embedded worker). It only
    touches the current process. The current executor is invalidated and rebuilt
    on the next message. `retry` (#91) replaces the default policy, turned off by
    `PolitiqueRelance(max_tentatives=1)` (None: unchanged). `verification` (#1177)
    turns delivery verification on or off (None: unchanged).
    """
    global _provider_factory, _guardrails, _retry, _verification, _executor
    if provider_factory is not None:
        _provider_factory = provider_factory
    if guardrails is not None:
        _guardrails = guardrails
    if retry is not None:
        _retry = retry
    if verification is not None:
        _verification = verification
    _executor = None


def reset_worker() -> None:
    """Restore the worker's default configuration (configured factory, default guardrails)."""
    global _provider_factory, _guardrails, _retry, _verification, _executor
    _provider_factory = _configured_factory
    _guardrails = None
    _retry = RELANCE_DEFAUT
    _verification = True
    _executor = None


def _get_executor() -> LocalExecutor:
    """The worker process's executor, built lazily on the first message.

    Wired like the local loop's and the queue workers' (#41): effective catalog
    (#72), playbooks (#78), capacity (#86), MCP (#104), secrets (#109),
    permissions (#110) and projects (#224) are reread **live** from the
    configuration's stores at each task, so an edit published from the Control
    Tower holds for the next message without restarting the worker. The guardrails
    (#9) and the retry (#91) are the ones `configure_worker` set.

    The **tooled runtime** is not wired here (#1037): the executor derives it from
    the routed agent's definition at each task, so a custom agent works tooled in a
    durable activity as elsewhere. Passing it `default_runtimes(...)` would restrict
    it to the five roles of the code.

    The verification **mirror** of an unversioned project (#1496, #1497) is not kept
    for the run there: the workflow schedules its activities, no loop holds the run
    with the executor (`TaskExecutor.tient_le_run`), and a closing activity could
    land on any worker of the queue. A project task would have its own, removed
    with its workspace; in practice a durable run carries no project
    (`DurableEngine.run` receives none).
    """
    global _executor
    if _executor is None:
        provider = _provider_factory()
        settings = load_settings()
        agents_store = AgentStore.default(settings)
        _executor = LocalExecutor(
            provider,
            agents=catalogue_hors_projet(agents_store, modele=settings.model),
            guardrails=_guardrails,
            playbooks=PlaybookStore.default(settings),
            capacites=CapacityStore.default(settings),
            mcp=McpStore.default(settings),
            secrets=SecretStore.default(settings),
            permissions=PermissionStore.default(settings),
            projets=ProjetStore.default(settings),
            # The agents of the task's project (#1038), as in process and on the
            # Celery side: three wirings, one routing contract.
            agents_store=agents_store,
            modele=settings.model,
            relance=_retry,
            verificateur=(
                VerificateurTaches(provider, modele=settings.model)
                if _verification
                else None
            ),
        )
    return _executor


async def _with_heartbeat(work: Awaitable[T]) -> T:
    """Await `work` while periodically signalling that this worker is alive (#96).

    The heartbeat is what makes failure recovery **fast**: an agent activity can
    run for an hour, and without a sign of life the server could not tell a busy
    worker from a dead one before the "start-to-close" expires. Beating every
    `HEARTBEAT_PERIOD_S` brings detection down to a few seconds; past that, the
    server hands the activity to the next worker, which picks it up where the
    workflow awaits it.

    Outside an activity context (direct call, test), it is a plain await: the
    heartbeat then has no one to talk to.
    """
    if not activity.in_activity():
        return await work
    beater = asyncio.ensure_future(_beat())
    try:
        return await work
    finally:
        beater.cancel()
        with suppress(asyncio.CancelledError):
            await beater


async def _beat() -> None:
    """Signal the activity as alive until cancelled (companion of `_with_heartbeat`)."""
    while True:
        await asyncio.sleep(HEARTBEAT_PERIOD_S)
        activity.heartbeat()


@activity.defn(name="planifier")
async def plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Planning activity: split the goal into tasks and record the step.

    The activity-side mirror of `OrchestrationEngine._plan`: the orchestrator (#3)
    runs on the worker's configured provider, usage is measured (#8) and the
    `planification` step is recorded in the journal (hence in the Control Tower's
    real-time feed, #46). A planning error is recorded then **raised**: without a
    plan there is nothing to orchestrate, so the activity fails and the workflow
    with it (the client-side orchestrator passes it on, see
    `maestro.durable.engine`).
    """
    goal: str = payload["objectif"]
    journal = RunJournal(run_id=payload.get("run_id"))
    start = perf_counter()
    orchestrator = _orchestrator()
    with collect_usage() as collected:
        try:
            tasks = await _with_heartbeat(orchestrator.plan(goal))
        except Exception as exc:
            journal.consigne(
                etape="planification",
                nom="Planification de l'objectif",
                agent="orchestrateur",
                role="Orchestrateur",
                statut=STATUT_ECHEC,
                entree=goal,
                sortie="",
                erreur=str(exc),
                usage=collected.total.avec_duree(_ecoule_ms(start)),
            )
            # Not retryable: a rejected plan (undecodable answer, broken schema)
            # will be rejected the same way next time. The CLI presents it as in
            # local mode: without a plan, nothing to orchestrate.
            raise ApplicationError(
                str(exc), type=type(exc).__name__, non_retryable=True
            ) from exc
    usage = collected.total.avec_duree(_ecoule_ms(start))
    journal.consigne(
        etape="planification",
        nom="Planification de l'objectif",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=STATUT_TERMINEE,
        entree=goal,
        sortie=f"{len(tasks)} tâche(s) planifiée(s)",
        usage=usage,
    )
    return {"tasks": [t.to_dict() for t in tasks], "usage": usage.to_dict()}


@activity.defn(name="executer_tache")
async def execute_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Task execution activity: routing, guardrails, deliverable, journal.

    The contract is that of every `TaskExecutor`: it never raises for a business
    failure (routing, guardrail, agent error). The failure is a `TaskResult` with
    status `echec`, recorded. Only a payload that breaks the shared schema raises
    (task refused before anything runs). The blackboard (the dependencies' results)
    travels **with** the message: activities share no memory. The usage carried by
    the result is the real cost measured by `LocalExecutor` (#8), recorded in the
    same journal as everything else (#46/#55).
    """
    try:
        validate_task(payload["task"])
    except Exception as exc:
        # Not retryable: a task that breaks the schema will keep breaking it. The
        # task is refused before anything runs, so nothing was spent.
        raise ApplicationError(
            str(exc), type=type(exc).__name__, non_retryable=True
        ) from exc
    task = Task.from_dict(payload["task"])
    dependencies = [TaskResult.from_dict(d) for d in payload.get("dependances", ())]
    journal = RunJournal(run_id=payload.get("run_id"))
    result = await _with_heartbeat(_get_executor().execute(task, dependencies, journal))
    # The executor's identity, reported on the result (`TaskResult.worker`): here
    # the Temporal activity queue that carried the task (MVP criterion 2).
    result = replace(result, worker=f"temporal/{activity.info().task_queue}")
    return result.to_dict()


@activity.defn(name="consigner_blocage")
async def record_blocker(payload: dict[str, Any]) -> dict[str, Any]:
    """Downstream blocker activity (#43): decided in the workflow, recorded here.

    The workflow sees that a dependency failed (or is itself blocked) and delegates
    to this activity the construction **and** the recording of the blocked
    `TaskResult`, because recording in the journal is I/O, forbidden to the
    deterministic workflow. It reuses `maestro.engine.loop._consigne_blocage`: same
    error message, same journal fields as the in-process loop, hence the same event
    on the Control Tower side. No agent is called on: zero usage.
    """
    task = Task.from_dict(payload["task"])
    unmet = [TaskResult.from_dict(d) for d in payload.get("dependances", ())]
    journal = RunJournal(run_id=payload.get("run_id"))
    result = _consigne_blocage(task, unmet, journal)
    return result.to_dict()


def _orchestrator() -> Orchestrator:
    """Build the planning orchestrator on the worker's provider.

    Rebuilt for each run (planning happens once per run, so caching it saves
    nothing): the provider comes from the process factory (`configure_worker`),
    the model from the configuration (`MAESTRO_MODEL`, #69), the same
    configuration/factory coupling as the executor.
    """
    from maestro.providers.factory import default_model

    return Orchestrator(_provider_factory(), model=default_model(load_settings()))


__all__ = [
    "HEARTBEAT_PERIOD_S",
    "ProviderFactory",
    "configure_worker",
    "execute_task",
    "plan",
    "record_blocker",
    "reset_worker",
]
