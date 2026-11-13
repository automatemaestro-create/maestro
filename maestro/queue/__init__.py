"""Task queue and parallel workers: Celery + Redis (#41).

The tasks the orchestrator produces are **pushed to a queue** (Celery, Redis
broker) and **consumed by separate workers**: several agents truly run in
parallel, outside the orchestrator's process, and each task's result (status,
deliverable, error, produced files, usage) **comes back** through the result
backend.

Three parts, one JSON contract:

- `celery_app`: the shared Celery application (broker and backend on
  `REDIS_URL`, dedicated queue `TASKS_QUEUE`, everything in JSON);
- `worker`: the Celery task `maestro.executer_tache`, consumed by the workers.
  It runs through the same path as the local loop (`LocalExecutor`) and returns
  `TaskResult.to_dict()`;
- `dispatch`: `CeleryExecutor`, the **distributed** executor that the
  orchestration loop accepts, and `create_distributed_engine`, the whole loop
  plugged into the queue.

On the workers' side (one terminal per worker; on Windows, `--pool=solo`):

    celery -A maestro.queue worker --pool=solo -n agent1@%h
    celery -A maestro.queue worker --pool=solo -n agent2@%h

On the orchestrator's side (Redis running, see infra/docker-compose.yml):

    from maestro.queue import create_distributed_engine

    engine = create_distributed_engine()
    report = await engine.run("Créer une API de gestion de tâches")

or, from the CLI: `maestro-run --queue "<objectif>"`.
"""

from __future__ import annotations

from maestro.queue.celery_app import (
    DEFAULT_REDIS_URL,
    EXECUTE_TASK_NAME,
    TASKS_QUEUE,
    app,
    create_app,
)
from maestro.queue.dispatch import (
    RESULT_TIMEOUT_S,
    CeleryExecutor,
    create_distributed_engine,
)

__all__ = [
    "DEFAULT_REDIS_URL",
    "EXECUTE_TASK_NAME",
    "RESULT_TIMEOUT_S",
    "TASKS_QUEUE",
    "CeleryExecutor",
    "app",
    "create_app",
    "create_distributed_engine",
]
