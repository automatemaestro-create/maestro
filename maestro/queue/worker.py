"""The worker side of the task queue: consumption and execution (#41).

Declares the Celery task `maestro.executer_tache`, consumed by the workers
(`celery -A maestro.queue worker`). Each worker runs the task **outside the
orchestrator's process**, through the same path as local execution
(`maestro.engine.LocalExecutor`): routing to the competent agent, guardrails (#9),
tooled runtime or text call, usage measurement (#8). The full result (status,
deliverable, error, produced files, usage) goes back through the result backend
as `TaskResult.to_dict()`: that is the **return** the orchestrator side
(`maestro.queue.dispatch`) rebuilds.

Two kinds of failure, two channels:

- a **business** failure (no agent to route to, guardrail triggered, agent error)
  is *data*: the Celery task succeeds and returns a `TaskResult` with status
  `echec`, the same resilience as the local loop;
- a **payload or environment** failure (a task that breaks the shared schema, a
  provider that cannot be built) raises: Celery marks the task FAILURE and the
  exception reaches the orchestrator, which turns it into a task failure.

The worker's provider is built **lazily** (on the first message), through a
factory that `configure_worker` replaces: the one the default configuration names
(`MAESTRO_PROVIDER`, #69), a fake provider in integration tests. The
configuration belongs to the worker's process.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from celery import shared_task
from celery.signals import celeryd_after_setup

from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import AgentStore, catalogue_hors_projet
from maestro.config import load_settings
from maestro.engine.executor import LocalExecutor, TaskResult
from maestro.engine.guardrails import Guardrails
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.engine.runner import run_borne
from maestro.engine.verification import VerificateurTaches
from maestro.orchestrator.schema import Brief, Task, validate_brief, validate_task
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.queue.celery_app import EXECUTE_TASK_NAME
from maestro.telemetry import RunJournal

#: A worker's provider factory: called once per process, on the first message
#: consumed.
ProviderFactory = Callable[[], ModelProvider]


def _configured_factory() -> ModelProvider:
    """The workers' default provider: the one the configuration names (#69).

    Imported here rather than at the top of the module, so that the worker stays
    agnostic of the provider: the choice lives in the configuration
    (`MAESTRO_PROVIDER`), as for `OrchestrationEngine.default`.
    """
    from maestro.providers.factory import provider_from_settings

    return provider_from_settings(load_settings())


_provider_factory: ProviderFactory = _configured_factory
_guardrails: Guardrails | None = None
#: Automatic retry of transient failures (#91, ENF-06), on by default on the
#: workers (the real runs), as on `OrchestrationEngine.default()`.
#: `configure_worker(retry=PolitiqueRelance(max_tentatives=1))` turns it off.
_retry: PolitiqueRelance = RELANCE_DEFAUT
#: Delivery verification (#1177), on by default on the workers as on
#: `OrchestrationEngine.default()`: a task consumed from the queue is "Terminée"
#: only once its criteria have been verified by running them, in the workspace the
#: worker opened. `configure_worker(verification=False)` turns it off.
_verification: bool = True
_executor: LocalExecutor | None = None

#: The worker's identity, reported on each result (`TaskResult.worker`). The
#: Celery context (`request.hostname`) only carries the machine name (the nodename
#: is overwritten by `gethostname()` in `trace_task_ret`), so the exact nodename is
#: kept apart: per process for a real CLI worker (`celeryd_after_setup`), per
#: thread for the embedded workers of tests (`name_worker`, one test worker is one
#: thread).
_process_worker_name: str | None = None
_thread_worker_name = threading.local()


@celeryd_after_setup.connect
def _remember_nodename(sender: str, instance: Any, **kwargs: Any) -> None:
    """Remember the CLI worker's nodename (`celery worker -n agent1@%h`) at startup."""
    global _process_worker_name
    _process_worker_name = sender


def name_worker(name: str) -> None:
    """Set the worker name that results report, for the **current thread**.

    The injection point of embedded workers (integration tests): several workers
    live in one process there, one per thread, and `celeryd_after_setup` is not
    emitted.
    """
    _thread_worker_name.name = name


def _worker_name(request: Any) -> str:
    """The current worker's name: the thread's, else the process's, else the machine's."""
    thread_name: str | None = getattr(_thread_worker_name, "name", None)
    return thread_name or _process_worker_name or request.hostname or ""


def configure_worker(
    *,
    provider_factory: ProviderFactory | None = None,
    guardrails: Guardrails | None = None,
    retry: PolitiqueRelance | None = None,
    verification: bool | None = None,
) -> None:
    """Configure execution in THIS worker process (provider, guardrails, retry).

    Call it before consumption (worker startup, or a test fixture). It only touches
    the current process: each worker configures itself. The current executor is
    invalidated and rebuilt on the next message. `retry` (#91) replaces the default
    policy, turned off by `PolitiqueRelance(max_tentatives=1)` (None: unchanged).
    `verification` (#1177) turns delivery verification on or off (None: unchanged).
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

    The executors' model follows the process configuration (`MAESTRO_MODEL`, #69),
    whether the provider factory is the default one or an injected one: the
    configuration carries the model, the factory carries the provider.

    Built once per process, the executor still reads at each consumed task what
    the Control Tower may have changed, in the stores of the configuration, which
    workers and API must share:
    - the system prompts, from the versioned playbook store (#76, #78,
      `MAESTRO_PLAYBOOKS_DIR`): a published edit holds for the next message,
      without restarting the worker;
    - capacity (#86, `MAESTRO_CAPACITE_DIR`): an agent disabled from the Control
      Tower is no longer routed from the next message on. The instance cap bounds
      simultaneous runs **per process** (no coordination between workers, see
      maestro.agents.capacity);
    - the per-agent MCP servers (#104, `MAESTRO_MCP_DIR`), mounted on the agent's
      tooled runs, whose `${VAR}` references resolve in the **per-agent secret
      vault** (#109, `MAESTRO_SECRETS_DIR`) once provisioned: an agent sees only
      its own, on workers as locally.

    The executors' catalog is the **effective** one (#72), default agents plus
    custom ones (`MAESTRO_AGENTS_DIR`), loaded at construction: an agent created
    later waits for the worker's restart. Its **tooled runtime** is not wired here
    (#1037): the executor derives it from the routed agent's definition at each
    task, so a custom agent works tooled on workers as locally.

    The **mirror** in which a task of an unversioned project is verified (#1496)
    is not kept from one task of the run to the next, unlike the in-process engine
    (#1497): no loop holds the run here (`TaskExecutor.tient_le_run`), and the end
    of a run does not travel to the worker. Each task therefore has its own,
    copied on its first verification and removed with its workspace; otherwise a
    worker that lives for days would pile up one mirror per run. The sweep wins if
    the worker dies first.
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
            # The agents of the task's project (#1038): a distributed worker routes
            # like the in-process engine, so it needs the same stores.
            agents_store=agents_store,
            modele=settings.model,
            relance=_retry,
            verificateur=(
                VerificateurTaches(provider, modele=settings.model) if _verification else None
            ),
        )
    return _executor


@shared_task(bind=True, name=EXECUTE_TASK_NAME)
def execute_task(
    self: Any,
    task_data: dict[str, Any],
    dependencies_data: Sequence[dict[str, Any]] = (),
    run_id: str | None = None,
    brief_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume a task from the queue: run it and return `TaskResult.to_dict()`.

    `task_data` is validated against the shared schema before anything runs (a
    malformed task is refused as FAILURE, never run); `dependencies_data` carries
    the results of the prerequisite tasks (the blackboard), resolved on the
    orchestrator side, since workers share no state. `run_id` ties the worker's
    traces (a JSON journal local to its process) to the overall run. That journal,
    rebuilt for each message, sees only the current task: a spending cap configured
    on the worker side (#56) therefore applies to the task alone, not to the run.

    `brief_data` (#1402) is the run's approved brief, when it has one. Validated
    like the task (a malformed brief refuses the message), it is kept for this run,
    and the task receives it next to its description, as does its verifier. When
    absent (a run without a brief, or an orchestrator that does not send it),
    nothing changes.

    The result carries the worker's name (`worker`): that is what makes the
    distribution of tasks across distinct workers visible (MVP criterion 2).
    """
    validate_task(task_data)
    if brief_data is not None:
        validate_brief(brief_data)
    task = Task.from_dict(task_data)
    dependencies = [TaskResult.from_dict(d) for d in dependencies_data]
    journal = RunJournal(run_id=run_id)
    if brief_data is not None:
        _get_executor().retient_le_brief(journal.run_id, Brief.from_dict(brief_data))
    # Bounded stop (#64): since the guardrails apply on the worker side, a run
    # detached by the timeout cannot suspend the return of the result.
    result = run_borne(_get_executor().execute(task, dependencies, journal))
    result = replace(result, worker=_worker_name(self.request))
    return result.to_dict()
