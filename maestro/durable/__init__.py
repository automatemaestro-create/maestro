"""Durable mode: the execution engine on Temporal (#95).

Moves a run from the current process to a durable **Temporal workflow**, as an
**opt-in** mode: one run is one workflow, one task is one activity. The in-process
engine (`maestro.engine`) stays the default and does not change; this module is its
durable counterpart, modelled on `maestro.queue` (distributed execution through a
queue).

Four parts:

- `workflow`: `MaestroRunWorkflow`, the **deterministic** run (planning,
  dependencies, scheduling) that delegates all I/O to activities;
- `activities`: `plan` / `execute_task` / `record_blocker`, the run's I/O (model
  calls, guardrails #9, journal #8); `configure_worker` sets the worker process's
  configuration;
- `worker`: the Temporal `Worker` (`build_worker`) and its standalone mode;
- `engine`: `DurableEngine`, on the orchestrator side, the same interface as
  `OrchestrationEngine` (`await engine.run(goal)`), plus **failure recovery**
  (`await engine.resume(run_id)`, #96), and `create_durable_engine`.

Usage from the CLI (Temporal running, see infra/docker-compose.yml):

    maestro-run --durable "<objectif>"

or from code:

    from maestro.durable import create_durable_engine

    engine = create_durable_engine()
    report = await engine.run("Créer une API de gestion de tâches")

**Failure recovery** (#96): the run survives the process that follows it. If that
process vanishes midway, the run is reattached by its `run_id` (the one of the
journal, the trace and the Control Tower):

    maestro-run --reprendre <run_id>

Tasks that already succeeded are not run again: their result comes from the
Temporal history, so upstream work is not paid twice. If the **worker** goes down,
there is nothing to do: the run resumes by itself as soon as a worker is back on
the queue (`python -m maestro.durable.worker`).
"""

from __future__ import annotations

from maestro.durable.engine import (
    WORKFLOW_ID_PREFIX,
    DurableEngine,
    create_durable_engine,
    workflow_id,
)
from maestro.durable.worker import DURABLE_QUEUE, build_worker
from maestro.durable.workflow import MaestroRunWorkflow

__all__ = [
    "DURABLE_QUEUE",
    "WORKFLOW_ID_PREFIX",
    "DurableEngine",
    "MaestroRunWorkflow",
    "build_worker",
    "create_durable_engine",
    "workflow_id",
]
