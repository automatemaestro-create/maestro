"""The orchestrator side of the task queue: queuing and result return (#41).

`CeleryExecutor` is the **distributed** executor of the orchestration loop. Where
`LocalExecutor` runs in process, it **pushes** each task to the Celery + Redis
queue (`send_task`, by name: the worker code is never imported here, so queuing
and consumption stay decoupled) and waits for the result a worker sends back. The
loop (`maestro.engine.loop`) does not change: it still resolves dependencies and
starts independent tasks in parallel, each becoming a queued message that any
available worker can consume. Two workers are two agents truly in parallel,
outside the orchestrator's process.

Same contract as every `TaskExecutor`: `execute` never raises. A **transport**
failure (unreachable queue, lost worker, timeout, worker exception) becomes a
failed `TaskResult`, recorded in the journal like any other, so the loop stays
resilient. *Business* failures travel as data: the worker returns a `TaskResult`
with status `echec`, passed on as is.

Waiting for the result **blocks on the Celery side** (`AsyncResult.get`): it runs
in a thread (`asyncio.to_thread`) so that the asyncio loop keeps dispatching the
other tasks meanwhile.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter

from celery import Celery
from celery.exceptions import TimeoutError as CeleryTimeoutError

from maestro.config import Settings, load_settings
from maestro.engine.executor import (
    TaskExecutor,
    TaskResult,
    _build_task_description,
    _echec,
    _ecoule_ms,
)
from maestro.engine.loop import OrchestrationEngine
from maestro.messaging.mailbox import Mailbox
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Brief, Task
from maestro.queue.celery_app import EXECUTE_TASK_NAME
from maestro.telemetry import RunJournal, StepUsage

#: Default wait for a task's result (seconds). It keeps a lost worker, or a queue
#: without consumers, from suspending the loop forever; an agent task, model calls
#: included, fits well within it.
RESULT_TIMEOUT_S = 600.0


class CeleryExecutor(TaskExecutor):
    """Distributed executor: each task goes to the queue, and its result comes back.

    It is injected into the loop (`OrchestrationEngine(..., executor=...)`) in place
    of local execution. `app` is the Celery application to use, the repository's
    by default (`maestro.queue.celery_app.app`, configured on `REDIS_URL`); tests
    substitute an app on the in-memory transport. `timeout_s` bounds the wait for
    the result of **each** task (None: unbounded, not advised outside debugging).
    """

    def __init__(
        self, app: Celery | None = None, *, timeout_s: float | None = RESULT_TIMEOUT_S
    ) -> None:
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError(f"timeout_s doit être > 0 (reçu : {timeout_s}).")
        if app is None:
            from maestro.queue.celery_app import app as default_app

            app = default_app
        self._app = app
        self._timeout_s = timeout_s
        # The approved brief of each run (#1402), by `run_id`: it travels with each
        # message of that run, like the blackboard. A worker shares nothing with the
        # orchestrator, so it would not know it otherwise.
        self._briefs: dict[str, Brief] = {}

    def retient_le_brief(self, run_id: str, brief: Brief) -> None:
        """See `TaskExecutor.retient_le_brief`: carried by every message of this run (#1402)."""
        self._briefs[run_id] = brief

    async def execute(
        self, task: Task, dependances: Sequence[TaskResult], journal: RunJournal
    ) -> TaskResult:
        """Push `task` to the queue, wait for its result and record it in the journal.

        The blackboard (the dependencies' results) travels **with** the message:
        workers share no state with the orchestrator nor with each other. So does
        the run's approved brief (#1402), when there is one. The usage carried by
        the result is the one the worker measured (the task's real cost); only a
        transport failure is timed here.
        """
        brief = self._briefs.get(journal.run_id)
        task_input = _build_task_description(task, dependances, brief)
        start = perf_counter()
        try:
            result = await asyncio.to_thread(
                self._send_and_wait, task, dependances, journal.run_id, brief
            )
        # celery.exceptions.TimeoutError does not derive from the built-in
        # TimeoutError: catching both covers every transport.
        except (TimeoutError, CeleryTimeoutError):
            result = replace(
                _echec(
                    task,
                    agent="—",
                    role="non exécuté",
                    score=0,
                    erreur=(
                        f"file de tâches : résultat non remonté après {self._timeout_s:g} s "
                        "— worker indisponible ou tâche toujours en file."
                    ),
                ),
                usage=StepUsage().with_duration(_ecoule_ms(start)),
            )
        except Exception as exc:  # transport or worker error: recorded as a failure, never raised
            result = replace(
                _echec(
                    task,
                    agent="—",
                    role="non exécuté",
                    score=0,
                    erreur=f"file de tâches : {exc}",
                ),
                usage=StepUsage().with_duration(_ecoule_ms(start)),
            )
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=result.agent,
            role=result.role,
            statut=result.statut,
            entree=task_input,
            sortie=result.sortie,
            erreur=result.erreur,
            usage=result.usage,
            # The playbook version used (#78) travels with the result: the worker
            # resolved and stamped it, and the orchestrator traces it too.
            playbook_version=result.playbook_version,
        )
        return result

    def _send_and_wait(
        self,
        task: Task,
        dependencies: Sequence[TaskResult],
        run_id: str,
        brief: Brief | None = None,
    ) -> TaskResult:
        """Blocking (run in a thread): `send_task`, then wait for the result.

        Sending is **by name** (`EXECUTE_TASK_NAME`): the orchestrator depends only
        on the queue's JSON contract, not on the workers' code. The brief (#1402)
        is a fourth argument, **left out** when there is none, so the message of a
        run without a brief stays readable by a worker that does not know briefs.
        """
        args: list[object] = [task.to_dict(), [d.to_dict() for d in dependencies], run_id]
        if brief is not None:
            args.append(brief.to_dict())
        async_result = self._app.send_task(EXECUTE_TASK_NAME, args=args)
        raw = async_result.get(timeout=self._timeout_s)
        return TaskResult.from_dict(raw)


def create_distributed_engine(
    settings: Settings | None = None,
    *,
    app: Celery | None = None,
    timeout_s: float | None = RESULT_TIMEOUT_S,
    mailbox: Mailbox | None = None,
    max_parallel: int | None = None,
) -> OrchestrationEngine:
    """The orchestration loop plugged into the queue: the distributed counterpart of `default`.

    **Planning** stays in the orchestrator's process (provider named by the
    configuration, like `OrchestrationEngine.default`, #69); task **execution**
    goes to the queue, towards workers started elsewhere
    (`celery -A maestro.queue worker`). Execution guardrails (#9) apply on the
    worker side: see `maestro.queue.worker.configure_worker`. `mailbox` (#44)
    plugs inter-agent messaging (observable handoff) into the loop, as in local
    runs. `max_parallel` (#100) bounds how many tasks are **queued at the same
    time**, the global cap on the orchestrator side; per-agent capacity (#86)
    plays out in each worker (per process, see maestro.agents.capacity).
    """
    from maestro.providers.factory import default_model, provider_from_settings

    settings = settings or load_settings()
    provider = provider_from_settings(settings)
    orchestrator = Orchestrator(provider, model=default_model(settings))
    return OrchestrationEngine(
        provider,
        orchestrator,
        executor=CeleryExecutor(app, timeout_s=timeout_s),
        mailbox=mailbox,
        max_parallele=max_parallel,
    )
