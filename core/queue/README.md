# core/queue — File de tâches & workers

Découple création et exécution des tâches ; plusieurs workers = agents en parallèle. Celery/BullMQ + Redis.

> **Placeholder** — pas encore implémenté au POC (prévu en Phase 1). Au POC, l'exécution
> parallèle est portée par la boucle d'orchestration : [`maestro/engine/`](../../maestro/engine/).
