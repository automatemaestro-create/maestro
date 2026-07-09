"""File de tâches et workers parallèles — Celery + Redis (ticket #41).

Matérialise `core/queue` pour le MVP : les tâches produites par l'orchestrateur
sont **poussées dans une file** (Celery, broker Redis) et **consommées par des
workers** séparés — plusieurs agents s'exécutent réellement en parallèle, hors
du process de l'orchestrateur, et le résultat de chaque tâche (statut, livrable,
erreur, fichiers produits, usage) **remonte** par le backend de résultats.

Trois pièces, un contrat JSON :

- `celery_app` — l'application Celery commune (broker/backend = `REDIS_URL`,
  file dédiée `FILE_TACHES`, tout en JSON) ;
- `worker` — la tâche Celery `maestro.executer_tache`, consommée par les
  workers ; exécute via le même chemin que la boucle locale (`LocalExecutor`)
  et renvoie `TaskResult.to_dict()` ;
- `dispatch` — `CeleryExecutor`, l'exécuteur **distribué** injectable dans la
  boucle d'orchestration, et `create_distributed_engine`, la boucle complète
  branchée sur la file.

Usage — côté workers (un terminal par worker ; sous Windows, `--pool=solo`) :

    celery -A maestro.queue worker --pool=solo -n agent1@%h
    celery -A maestro.queue worker --pool=solo -n agent2@%h

Côté orchestrateur (Redis lancé, cf. infra/docker-compose.yml) :

    from maestro.queue import create_distributed_engine

    engine = create_distributed_engine()
    report = await engine.run("Créer une API de gestion de tâches")

ou, en démo CLI : `maestro-run --queue "<objectif>"`.
"""

from __future__ import annotations

from maestro.queue.celery_app import (
    FILE_TACHES,
    NOM_TACHE_EXECUTER,
    REDIS_URL_DEFAUT,
    app,
    create_app,
)
from maestro.queue.dispatch import (
    TIMEOUT_RESULTAT_S,
    CeleryExecutor,
    create_distributed_engine,
)

__all__ = [
    "FILE_TACHES",
    "NOM_TACHE_EXECUTER",
    "REDIS_URL_DEFAUT",
    "TIMEOUT_RESULTAT_S",
    "CeleryExecutor",
    "app",
    "create_app",
    "create_distributed_engine",
]
