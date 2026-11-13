"""The Celery application of Maestro's task queue (#41).

Configures the Celery app shared by both sides of the queue: the
**orchestrator** (producer, `maestro.queue.dispatch`) and the **workers**
(consumers, `maestro.queue.worker`). Broker and result backend share the same
Redis instance (`REDIS_URL`, see infra/docker-compose.yml). That instance is
shared with real-time Pub/Sub, hence a **dedicated** queue (`TASKS_QUEUE`) rather
than Celery's default one.

Everything is **JSON**, tasks and results alike: the queue's contract is the one
of the shared schema (`packages/shared/schemas/task.schema.json`) and of the
results' `to_dict()`/`from_dict()`, readable and replayable, without pickle.

Starting a worker (one process is one executor; on Windows Celery does not
support the `prefork` pool, use `solo` or `threads`):

    celery -A maestro.queue worker --pool=solo -n agent1@%h

Several workers (terminals) mean several agents truly in parallel.
"""

from __future__ import annotations

from celery import Celery

from maestro.config import Settings, load_settings
from maestro.espace import nom_redis

#: Public name of the Celery execution task, the key of the decoupling: the
#: orchestrator sends it **by name** (`send_task`), without importing the worker
#: code. The value is registered by the workers and travels on the queue.
EXECUTE_TASK_NAME = "maestro.executer_tache"

#: The queue dedicated to Maestro tasks on the shared Redis instance.
TASKS_QUEUE = "maestro.taches"

#: Default Redis URL: the local docker-compose instance (infra/README.md).
DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def create_app(
    settings: Settings | None = None,
    *,
    broker_url: str | None = None,
    result_backend: str | None = None,
) -> Celery:
    """Build Maestro's Celery app, configured from the environment.

    Broker and backend point at `REDIS_URL` by default (or at the local
    docker-compose instance). `broker_url`/`result_backend` substitute a test
    transport (`memory://`, `cache+memory://`): that is how the integration tests
    exercise a real queue without a Redis.
    """
    settings = settings or load_settings()
    redis_url = settings.redis_url or DEFAULT_REDIS_URL
    app = Celery("maestro")
    app.conf.update(
        broker_url=broker_url or redis_url,
        result_backend=result_backend or redis_url,
        # JSON end to end: no pickle on the queue.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        # The queue of **the stack's namespace** (#1164): a worker started from a
        # working copy takes only that copy's tasks.
        task_default_queue=nom_redis(TASKS_QUEUE),
        # The "started" status reaches the backend: the orchestrator sees a task go
        # from queued to a worker to done.
        task_track_started=True,
        # An agent task is long (model calls): each worker reserves one message at a
        # time, so that tasks really spread across workers instead of being
        # prefetched by the first one.
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # Modules imported when a worker starts (registers the task on the consumer
        # side); the orchestrator side never imports this module.
        include=["maestro.queue.worker"],
    )
    return app


#: The repository's default app: the one `celery -A maestro.queue worker` starts
#: and the orchestrator pushes its tasks to (configuration: REDIS_URL).
app = create_app()
