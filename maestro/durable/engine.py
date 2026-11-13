"""The orchestrator side of the durable mode: starts the workflow, returns the report (#95).

`DurableEngine` offers **the same interface** as `OrchestrationEngine`
(`await engine.run(goal, journal=...) -> RunReport`): the CLI
(`maestro-run --durable`) and every caller use it without changing anything. Under
the hood, instead of running the loop in process, it:

1. configures the worker process (guardrails, retry). The human validator (#9)
   lives in the process and cannot be serialized, so it is wired here and read by
   the activities;
2. connects the Temporal client (`TEMPORAL_ADDRESS`) and **embeds an ephemeral
   worker** (`maestro.durable.worker`) for the duration of the run. The activities
   then share the process's `maestro.trace` logger, so tracing/publishing (#8/#46)
   and console validation work as in local mode;
3. runs `MaestroRunWorkflow` (one run, one workflow) and rebuilds the `RunReport`
   from the serialized aggregate it returns.

The `journal` passed in carries the `run_id` (continuity of traces). In durable
mode the steps are recorded by the **activities** (each its own `RunJournal` on the
same `run_id`), not by that journal: the Control Tower's event feed and ledger are
therefore fed from the activities, exactly as in the in-process mode (criterion of
#95).

A **planning** error (invalid plan) fails the workflow; it is translated back into
`OrchestratorError` so that the CLI presents it as in local mode (without a plan,
nothing to orchestrate).

**Failure recovery** (#96): `resume(run_id)` attaches a fresh process to an
interrupted run instead of starting a new one. That works because the workflow id
is **derived from the `run_id`** (`workflow_id`): the `run_id` that journal, trace
and Control Tower show is enough to find the run on the Temporal side. The resuming
process embeds a worker, and:

1. **queries the acquired state** of the workflow (the `checkpoint` query): what is
   already paid for, planning and finished tasks;
2. **records the resume** with its reason and that summary (step `reprise`, hence
   trace and real-time feed), then awaits the outcome of the run.

Tasks that already succeeded are **never** run again: their result comes from the
Temporal history, not from a new model call, so upstream work is not paid twice.
Only a task in flight at the time of the interruption resumes (it had produced
nothing); Temporal's retry on worker loss hands it to the next worker (see
`maestro.durable.workflow`).

**Consolidation before and after a resume** goes through the aggregate, not through
the journal of the resuming process: that journal did not see what happened before
the failure (the acquired steps were recorded by the vanished process) and will not
see the later tasks either, each activity keeping its own journal on the same
`run_id`. The report returned here therefore carries the whole run, and
`RunReport.grand_livre` gives its consolidated accounting view: costs, tokens,
durations and statuses per task, on both sides of the interruption. On the Control
Tower side, the real-time feed consolidates by itself: the earlier steps were
published at their time, the later ones are published on resume (that this state
survives an API restart is the subject of #97).

`run()` is idempotent by the same mechanism: replaying a `run_id` already in flight
**attaches** to it instead of opening a second run (`USE_EXISTING`).
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from maestro.config import Settings, load_settings
from maestro.durable.worker import DURABLE_QUEUE, build_worker
from maestro.durable.workflow import MaestroRunWorkflow
from maestro.engine.executor import STATUT_TERMINEE, TaskResult
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import RunReport
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import RunJournal, StepUsage
from maestro.telemetry.costs import STEP_RESUME

#: Prefix of a run's workflow id. Derived from the `run_id`, hence **findable**:
#: that is what lets an interrupted run be resumed from the only identifier that
#: journal and Control Tower already show (#96).
WORKFLOW_ID_PREFIX = "maestro-run-"


def workflow_id(run_id: str) -> str:
    """The Temporal id of the run `run_id` (one run, one workflow, #95)."""
    return f"{WORKFLOW_ID_PREFIX}{run_id}"


class DurableEngine:
    """Durable engine: a run becomes a Temporal workflow (#95).

    Used like `OrchestrationEngine` (`await engine.run(goal)`), plus the resume of
    an interrupted run, `await engine.resume(run_id)` (#96). `address` is the
    Temporal server (default: configuration `TEMPORAL_ADDRESS`); `guardrails` (#9)
    and `retry` (#91) are set on the embedded worker process before the run.
    `retry=None` **turns off** the application retry (translated into a single
    attempt); Temporal's retry only covers the loss of the worker and is not
    affected by this setting.
    """

    def __init__(
        self,
        *,
        address: str | None = None,
        task_queue: str = DURABLE_QUEUE,
        guardrails: Guardrails | None = None,
        retry: PolitiqueRelance | None = RELANCE_DEFAUT,
    ) -> None:
        self._address = address or load_settings().temporal_address
        self._task_queue = task_queue
        self._guardrails = guardrails if guardrails is not None else Guardrails()
        # None means the application retry is off: a single attempt (Temporal does
        # not retry either). Otherwise the requested policy drives the retry (#91).
        self._retry = retry if retry is not None else PolitiqueRelance(max_tentatives=1)

    async def run(self, objective: str, *, journal: RunJournal | None = None) -> RunReport:
        """Run through Temporal and return the aggregate (`RunReport`).

        `journal` sets the `run_id` (continuity of traces); in durable mode its
        steps are produced by the activities. Raises `OrchestratorError` if
        planning fails (failed workflow translated back).
        """
        # Local import: the activity layer (provider, SDK) stays out of the module's
        # import path, as the Celery queue does on the queue side.
        from maestro.durable.activities import configure_worker

        journal = journal if journal is not None else RunJournal()
        run_id = journal.run_id
        configure_worker(guardrails=self._guardrails, retry=self._retry)

        client = await Client.connect(self._address)
        payload = {
            "objectif": objective,
            "run_id": run_id,
            "plafond_cout_usd": self._guardrails.plafond_cout_usd,
            "plafond_tokens": self._guardrails.plafond_tokens,
        }
        async with build_worker(client, task_queue=self._task_queue):
            # `USE_EXISTING`: restarting a `run_id` already in flight attaches to it
            # instead of opening a second run. Starting is idempotent, and the run
            # stays unique whatever happens to the calling process (#96).
            handle = await client.start_workflow(
                MaestroRunWorkflow.run,
                payload,
                id=workflow_id(run_id),
                task_queue=self._task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            return await self._outcome(handle)

    async def resume(
        self,
        run_id: str,
        *,
        journal: RunJournal | None = None,
        reason: str = "",
    ) -> RunReport:
        """Attach this process to the interrupted run `run_id` and return its report (#96).

        For when the process that followed a durable run has vanished (CLI killed,
        API restarted) while the run itself still lives on the Temporal side. It
        starts nothing: the workflow is found by its id, its acquired state put back
        into the journal, the resume recorded with its `reason`, then the outcome
        awaited. Tasks that already succeeded are not run again: their result comes
        from the history.

        A run that **already finished** can be resumed too: its report is simply
        returned (the process was killed after the last task, before the display).
        Raises `OrchestratorError` if no durable run carries that id.
        """
        from maestro.durable.activities import configure_worker

        journal = journal if journal is not None else RunJournal(run_id=run_id)
        configure_worker(guardrails=self._guardrails, retry=self._retry)

        client = await Client.connect(self._address)
        handle: WorkflowHandle[Any, Any] = client.get_workflow_handle(workflow_id(run_id))
        try:
            description = await handle.describe()
        except RPCError as exc:
            raise OrchestratorError(
                f"aucun run durable connu de Temporal sous l'identifiant « {run_id} » "
                f"({self._address}) — vérifiez le run_id, ou lancez un nouveau run."
            ) from exc

        async with build_worker(client, task_queue=self._task_queue):
            # The query is served by a worker that replays the history: the embedded
            # worker must therefore be running before it is asked.
            acquired = await handle.query(MaestroRunWorkflow.checkpoint)
            _record_resume(
                journal,
                reason=reason or _default_reason(description.status),
                acquired=acquired,
            )
            return await self._outcome(handle)

    async def _outcome(self, handle: WorkflowHandle[Any, Any]) -> RunReport:
        """Await the outcome of the workflow and turn it into a `RunReport`.

        Shared by start and resume: in both cases the aggregate returned by the
        workflow carries the **whole** run, the tasks before the interruption as
        well as those after it, since it is assembled from the history (that is
        the consolidation of the report, #96).
        """
        try:
            aggregate = await handle.result()
        except WorkflowFailureError as exc:
            # Planning failure (invalid plan): without a plan, nothing to
            # orchestrate. Translated back the way the in-process loop raises it.
            raise OrchestratorError(_readable_cause(exc)) from exc
        return _report_from_aggregate(aggregate)


def create_durable_engine(
    settings: Settings | None = None,
    *,
    guardrails: Guardrails | None = None,
    retry: PolitiqueRelance | None = RELANCE_DEFAUT,
) -> DurableEngine:
    """The durable engine plugged into the configuration: the counterpart of `default`.

    The provider (planning and execution) stays named by the configuration
    (`MAESTRO_PROVIDER`, #69), resolved **on the worker side** by the activities;
    the Temporal address comes from `TEMPORAL_ADDRESS`.
    """
    settings = settings or load_settings()
    return DurableEngine(address=settings.temporal_address, guardrails=guardrails, retry=retry)


def _report_from_aggregate(aggregate: dict[str, Any]) -> RunReport:
    """Rebuild the `RunReport` from the workflow's serialized aggregate."""
    raw_results = aggregate.get("resultats", [])
    results = raw_results if isinstance(raw_results, list) else []
    planning = aggregate.get("planification", {})
    return RunReport(
        objectif=str(aggregate.get("objectif", "")),
        resultats=tuple(TaskResult.from_dict(r) for r in results),
        run_id=str(aggregate.get("run_id", "")),
        planification=StepUsage.from_dict(planning if isinstance(planning, dict) else {}),
        plafond_cout_usd=_opt_float(aggregate.get("plafond_cout_usd")),
        plafond_tokens=_opt_int(aggregate.get("plafond_tokens")),
    )


def _record_resume(journal: RunJournal, *, reason: str, acquired: dict[str, Any]) -> None:
    """Record the `reprise` step: the run restarts, why, and what is already paid (#96).

    It is the run's **visible break**: it goes to the trace and to the Control
    Tower's real-time feed, where it tells the operator why the run restarts and
    what it will not pay twice. The summary is computed from the workflow's
    acquired state (planning and finished tasks), not from this process's journal,
    which saw nothing of what came before the failure.

    Zero usage: resuming calls on no model. The ledger therefore ignores this step
    (`STEP_RESUME`) and the run's total stays that of the real work.
    """
    recovered = StepUsage()
    steps = 0
    planning = acquired.get("planification")
    if isinstance(planning, dict):
        recovered = recovered.merge(StepUsage.from_dict(planning))
        steps += 1
    results = acquired.get("resultats", [])
    for raw in results if isinstance(results, list) else []:
        recovered = recovered.merge(TaskResult.from_dict(raw).usage)
        steps += 1
    journal.consigne(
        etape=STEP_RESUME,
        nom="Reprise du run interrompu",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=STATUT_TERMINEE,
        entree=reason,
        sortie=(
            f"{steps} étape(s) déjà acquise(s), non ré-exécutées — "
            f"usage récupéré : {recovered.short_summary()}"
        ),
        usage=StepUsage(),
    )


def _default_reason(status: Any) -> str:
    """Describe the reason for the resume from the run's state on the Temporal side.

    Used when the caller gives none: rather than a bare "reprise", the journal says
    *what happened*. The run was still alive with no one following it, or it had
    finished without its report being returned.
    """
    name = getattr(status, "name", str(status))
    if name == "RUNNING":
        return (
            "run toujours en vol côté Temporal : le process qui le suivait a été "
            "interrompu, l'exécution reprend là où elle en était."
        )
    if name == "COMPLETED":
        return (
            "run déjà achevé côté Temporal : seul le process qui le suivait avait "
            "été interrompu, son rapport est rendu sans rien ré-exécuter."
        )
    return f"run repris dans l'état « {name} » côté Temporal."


def _readable_cause(exc: WorkflowFailureError) -> str:
    """Extract the root message of a workflow failure (Temporal's chain of causes)."""
    cause: BaseException | None = exc
    message = str(exc)
    while cause is not None:
        message = str(cause) or message
        cause = cause.__cause__
    return message


def _opt_float(value: object) -> float | None:
    """Cast an aggregate value to `float | None` (cost cap)."""
    return float(value) if isinstance(value, int | float) else None


def _opt_int(value: object) -> int | None:
    """Cast an aggregate value to `int | None` (token cap)."""
    return int(value) if isinstance(value, int) else None


__all__ = ["WORKFLOW_ID_PREFIX", "DurableEngine", "create_durable_engine", "workflow_id"]
