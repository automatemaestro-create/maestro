"""The durable workflow: one run is one Temporal workflow (#95).

`MaestroRunWorkflow` **is** the execution of a run, carried by Temporal: its state
(which tasks succeeded) is persisted by the server, the basis of failure recovery
(#96). The workflow **orchestrates** (planning, dependency resolution, scheduling,
downstream blocking, #43) but delegates **all I/O** to activities
(`maestro.durable.activities`): model calls, clock and journaling live outside the
workflow, the condition of the **determinism** Temporal requires from workflow code
(the same sequence of events must replay identically).

The loop reproduces the in-process one (`maestro.engine.loop.OrchestrationEngine`):

1. the `plan` activity splits the goal (one run, one plan);
2. the workflow orders the tasks (deterministic topological sort) and starts each
   one as soon as **its** dependencies are resolved, so independent tasks start in
   parallel (as many concurrent activities);
3. a failed dependency **blocks** the task: it never reaches the execution
   activity, its blocker is recorded (`record_blocker` activity) and cascades;
4. the results are gathered in the plan's topological order (deterministic,
   independent of the order of completion).

**Failure recovery** (#96) is the reason the durable mode exists: the workflow's
state (which tasks succeeded) lives on the Temporal server, not in the process. A
worker killed during a run is replaced: the workflow **replays its history**, and
the activities already finished return their result *from the history* without
being run again, so upstream work is never paid twice. Two pieces make recovery
truly automatic and traceable:

- the `checkpoint` query (registered as `resultats_acquis`) exposes the run's
  acquired state (planning and finished tasks) to a process that resumes midway.
  That is what lets `maestro.durable.engine` rebuild the journal, hence
  consolidate report and ledger across the interruption;
- the activities' retry policy does not turn everything off: Temporal catches the
  **loss of the worker** (see `WORKER_LOSS_RETRY`), the only hazard the application
  retry cannot catch since it dies with the process.

The two retry layers thus compose without stepping on each other (requirement of
the parent #92): **provider** hazards remain the sole concern of the application
retry (#91), which lives *in* the activity; **infrastructure** hazards are the sole
concern of Temporal. Deterministic application failures (invalid plan, task that
breaks the schema) are raised `non_retryable` by the activities, and neither layer
replays them.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

# The business code imported here is **deterministic** (data shapes and a pure
# topological sort) or only referenced as an activity: it is let through the
# workflow sandbox without reimport (Temporal's recommendation).
with workflow.unsafe.imports_passed_through():
    from maestro.durable.activities import (
        execute_task,
        plan,
        record_blocker,
    )
    from maestro.engine.executor import STATUT_TERMINEE
    from maestro.orchestrator.schema import Task, topological_order

#: "Start-to-close" timeouts of the activities. Wide for an agent task (model calls
#: and retries included); short for the steps without a model.
PLANNING_TIMEOUT = timedelta(minutes=30)
TASK_TIMEOUT = timedelta(hours=1)
AUXILIARY_TIMEOUT = timedelta(seconds=30)

#: Expected heartbeat of the long activities (#96). It is what makes recovery
#: **fast**: without a heartbeat, a worker's death only shows at the
#: "start-to-close" (up to an hour for a task) and the run would stay frozen as
#: long. The activities keep it through `maestro.durable.activities`.
ACTIVITY_HEARTBEAT = timedelta(seconds=30)

#: Temporal's activity retry: it only catches what the application retry (#91)
#: cannot, the **loss of the worker** (process killed, heartbeat expired). Provider
#: hazards are retried *in* the activity; deterministic application failures are
#: raised `non_retryable` by the activities, so never replayed here (#96, see the
#: module header).
WORKER_LOSS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


@workflow.defn
class MaestroRunWorkflow:
    """An orchestration run, executed as a durable Temporal workflow (#95).

    The instance carries the run's **acquired state** (planning, finished tasks),
    rebuilt identically by replaying the history: that is what the `checkpoint`
    query makes readable from outside, so that a process resuming after a failure
    (#96) knows what is already paid for.
    """

    def __init__(self) -> None:
        self._goal: str = ""
        self._run_id: str = ""
        self._planning: dict[str, Any] | None = None
        self._order: tuple[str, ...] = ()
        self._acquired: dict[str, dict[str, Any]] = {}

    @workflow.query(name="resultats_acquis")
    def checkpoint(self) -> dict[str, Any]:
        """The run's state already acquired: what is paid for and will not be redone (#96).

        Same shape as the final aggregate, but **partial**: `planification` is
        `None` until the plan is set, and `resultats` only carries the finished
        tasks, in the plan's order. A task in flight at the time of the
        interruption is not there: it produced nothing and will be resumed.

        Queried by `maestro.durable.engine` when resuming: it gives it what it needs
        to rebuild the run's journal (hence consolidate report and ledger across the
        failure). A worker replaying the history rebuilds this state identically,
        without running a single activity again: reading the acquired state costs
        nothing.
        """
        return {
            "objectif": self._goal,
            "run_id": self._run_id,
            "planification": self._planning,
            "resultats": [
                self._acquired[task_id]
                for task_id in self._order
                if task_id in self._acquired
            ],
        }

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the workflow and return the serialized aggregate (rebuilt as a `RunReport`).

        `payload` carries the goal, the `run_id` (ties the activities' journals to
        the execution) and the armed caps, copied as is into the aggregate so that
        the report says which spending control held (#113). The result is a
        JSON-serializable dict: `maestro.durable.engine` turns it back into a
        `RunReport`.
        """
        goal: str = payload["objectif"]
        run_id: str = payload["run_id"]
        self._goal = goal
        self._run_id = run_id

        planned = await workflow.execute_activity(
            plan,
            {"objectif": goal, "run_id": run_id},
            start_to_close_timeout=PLANNING_TIMEOUT,
            heartbeat_timeout=ACTIVITY_HEARTBEAT,
            retry_policy=WORKER_LOSS_RETRY,
        )
        tasks = [Task.from_dict(t) for t in planned["tasks"]]
        ordered = topological_order(tasks)
        self._planning = planned["usage"]
        self._order = tuple(task.id for task in ordered)

        # One asyncio task per task, created in topological order: dependencies
        # therefore always exist before the tasks that await them (the same
        # invariant as the in-process loop). Temporal provides a deterministic
        # asyncio loop: `ensure_future`/`gather` replay identically there.
        in_flight: dict[str, asyncio.Task[dict[str, Any]]] = {}

        async def _when_ready(task: Task) -> dict[str, Any]:
            dependencies = [await in_flight[dep] for dep in task.dependencies]
            unmet = [dep for dep in dependencies if dep["statut"] != STATUT_TERMINEE]
            result: dict[str, Any]
            if unmet:
                # Downstream blocker (#43): the task never reaches execution.
                result = await workflow.execute_activity(
                    record_blocker,
                    {
                        "task": task.to_dict(),
                        "dependances": unmet,
                        "run_id": run_id,
                    },
                    start_to_close_timeout=AUXILIARY_TIMEOUT,
                    retry_policy=WORKER_LOSS_RETRY,
                )
            else:
                result = await workflow.execute_activity(
                    execute_task,
                    {
                        "task": task.to_dict(),
                        "dependances": dependencies,
                        "run_id": run_id,
                    },
                    start_to_close_timeout=TASK_TIMEOUT,
                    heartbeat_timeout=ACTIVITY_HEARTBEAT,
                    retry_policy=WORKER_LOSS_RETRY,
                )
            # The outcome joins the run's acquired state: paid for, it will not be
            # redone. It is what `checkpoint` returns on a resume.
            self._acquired[task.id] = result
            return result

        for task in ordered:
            in_flight[task.id] = asyncio.ensure_future(_when_ready(task))
        await asyncio.gather(*in_flight.values())

        # Deterministic gathering: the plan's order, not the order of completion.
        return {
            "objectif": goal,
            "run_id": run_id,
            "resultats": [in_flight[task.id].result() for task in ordered],
            "planification": planned["usage"],
            "plafond_cout_usd": payload.get("plafond_cout_usd"),
            "plafond_tokens": payload.get("plafond_tokens"),
        }


__all__ = ["MaestroRunWorkflow"]
