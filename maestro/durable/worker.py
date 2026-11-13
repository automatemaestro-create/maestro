"""Temporal worker of the durable mode: consumes workflow and activities (#95).

Assembles the `temporalio.worker.Worker` that runs the durable run: it registers
the workflow (`MaestroRunWorkflow`) and its three activities
(`maestro.durable.activities`) on a **dedicated task queue** (`DURABLE_QUEUE`),
against the Temporal server (`TEMPORAL_ADDRESS`, see infra/docker-compose.yml).

Two uses:

- **embedded**: `maestro.durable.engine.DurableEngine` starts an ephemeral worker
  in the CLI's process for the duration of a run (`maestro-run --durable`). The
  activities then share the process's `maestro.trace` logger, so the
  tracing/publishing handlers (#8/#46) and console validation (#9) work as in
  local mode;
- **standalone**: `python -m maestro.durable.worker` runs a persistent worker
  (real deployment), like `celery -A maestro.queue worker`.

The execution configuration (provider, guardrails, retry) **belongs to the
process** and is set through `configure_worker` (see `activities`).
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from maestro.config import load_settings
from maestro.durable.activities import execute_task, plan, record_blocker
from maestro.durable.workflow import MaestroRunWorkflow

#: The Temporal task queue dedicated to the durable run (the counterpart of
#: `TASKS_QUEUE` on the Celery side): the workflow and its activities register on
#: it, and the client schedules on it.
DURABLE_QUEUE = "maestro-durable"

#: The workflow sandbox, with the `maestro` package **passed through**.
#:
#: By default the sandbox reimports every module the workflow code touches into an
#: isolated space, replacing it with a proxy that watches for non-deterministic
#: access. Our modules define classes that inherit from others (providers,
#: executors): reimported that way, they subclass a proxy, which the sandbox cannot
#: do ("Using subclasses of proxied objects is unsupported"), and building the
#: worker would fail whatever the run.
#:
#: Passing through removes the ambiguity: Maestro's code is imported **once**,
#: normally. The workflow's determinism loses nothing: it does not rest on the
#: sandbox but on the discipline of the `workflow` module (no I/O, no clock:
#: everything goes through activities). Temporal recommends it for first-party
#: modules whose content one controls.
SANDBOX_RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("maestro")
)


def build_worker(client: Client, *, task_queue: str = DURABLE_QUEUE) -> Worker:
    """Build the worker that registers the durable run on `task_queue`.

    The `Worker` is an async context manager: `async with` starts it then shuts it
    down cleanly. That is how the `engine` embeds it for the duration of a run.
    """
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[MaestroRunWorkflow],
        activities=[plan, execute_task, record_blocker],
        workflow_runner=SANDBOX_RUNNER,
    )


async def _serve(address: str, task_queue: str) -> None:
    """Connect to the server, then serve the worker indefinitely (standalone mode)."""
    client = await Client.connect(address)
    async with build_worker(client, task_queue=task_queue):
        print(
            f"[OK  ] Worker durable en écoute sur « {task_queue} » "
            f"(Temporal {address}). Ctrl-C pour arrêter."
        )
        # Serve until interrupted: an Event never set holds the coroutine.
        await asyncio.Event().wait()


def main() -> int:  # pragma: no cover - persistent worker, needs a live server
    """Entry point of the standalone worker: serve until interrupted.

    Exits with code 0 on a keyboard interrupt, 1 if the Temporal server cannot be
    reached.
    """
    settings = load_settings()
    address = settings.temporal_address
    try:
        asyncio.run(_serve(address, DURABLE_QUEUE))
    except KeyboardInterrupt:
        print("\n[OK  ] Worker durable arrêté.")
        return 0
    except (RPCError, RuntimeError) as exc:
        print(f"[FAIL] Temporal injoignable sur {address} ({exc}).")
        print("Démarrez-le : docker compose -f infra/docker-compose.yml up -d temporal")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
