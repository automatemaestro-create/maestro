# core/queue — File de tâches & workers

Découple création et exécution des tâches ; plusieurs workers = agents en parallèle. Celery/BullMQ + Redis.

> **Implémenté** (ticket #41) dans [`maestro/queue/`](../../maestro/queue/) : file **Celery + Redis**
> (broker et backend de résultats sur `REDIS_URL`), tâche `maestro.executer_tache` consommée par des
> workers séparés, et `CeleryExecutor` injecté dans la boucle d'orchestration
> ([`maestro/engine/`](../../maestro/engine/)). Démo : `maestro-run --queue "<objectif>"` avec Redis
> lancé ([infra/](../../infra/)) et des workers démarrés :
>
> ```bash
> celery -A maestro.queue worker --pool=solo -n agent1@%h
> celery -A maestro.queue worker --pool=solo -n agent2@%h   # 2e terminal = 2e agent en parallèle
> ```
