"""Application Celery de la file de tâches Maestro (ticket #41).

Configure l'app Celery commune aux deux côtés de la file : l'**orchestrateur**
(producteur — `maestro.queue.dispatch`) et les **workers** (consommateurs —
`maestro.queue.worker`). Broker et backend de résultats partagent la même
instance Redis (`REDIS_URL`, cf. infra/docker-compose.yml) — l'instance est
mutualisée avec le pub/sub temps réel à venir, d'où une file **dédiée**
(`FILE_TACHES`) plutôt que la file Celery par défaut.

Tout passe en **JSON** (sérialisation des tâches comme des résultats) : le
contrat de la file est le même que celui du schéma partagé
(`packages/shared/schemas/task.schema.json`) et des `to_dict()`/`from_dict()`
des résultats — lisible, rejouable, sans pickle.

Démarrer un worker (un process = un exécutant ; sous Windows, le pool `prefork`
n'est pas supporté par Celery — utiliser `solo` ou `threads`) :

    celery -A maestro.queue worker --pool=solo -n agent1@%h

Plusieurs workers (terminaux) = plusieurs agents réellement en parallèle.
"""

from __future__ import annotations

from celery import Celery

from maestro.config import Settings, load_settings

#: Nom public de la tâche Celery d'exécution — la clé du découplage : le côté
#: orchestrateur l'envoie **par nom** (`send_task`), sans importer le code worker.
NOM_TACHE_EXECUTER = "maestro.executer_tache"

#: File dédiée aux tâches Maestro sur l'instance Redis mutualisée.
FILE_TACHES = "maestro.taches"

#: URL Redis par défaut : l'instance locale du docker-compose (infra/README.md).
REDIS_URL_DEFAUT = "redis://localhost:6379/0"


def create_app(
    settings: Settings | None = None,
    *,
    broker_url: str | None = None,
    result_backend: str | None = None,
) -> Celery:
    """Construit l'app Celery de Maestro, configurée depuis l'environnement.

    Par défaut, broker et backend pointent sur `REDIS_URL` (ou l'instance locale
    du docker-compose). `broker_url`/`result_backend` permettent d'y substituer
    un transport de test (`memory://`, `cache+memory://`) — c'est le levier des
    tests d'intégration, qui exercent une vraie file sans dépendre d'un Redis.
    """
    settings = settings or load_settings()
    redis_url = settings.redis_url or REDIS_URL_DEFAUT
    app = Celery("maestro")
    app.conf.update(
        broker_url=broker_url or redis_url,
        result_backend=result_backend or redis_url,
        # JSON de bout en bout — aucun pickle sur la file.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        task_default_queue=FILE_TACHES,
        # Le statut « démarrée » remonte au backend : l'orchestrateur voit une
        # tâche passer en file → chez un worker → terminée (remontée de statut).
        task_track_started=True,
        # Une tâche d'agent est longue (appels modèle) : chaque worker ne
        # réserve qu'un message à la fois, pour que les tâches se répartissent
        # réellement entre workers au lieu d'être préchargées par le premier.
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # Modules importés au démarrage d'un worker (enregistre la tâche côté
        # consommateur) ; le côté orchestrateur n'importe jamais ce module.
        include=["maestro.queue.worker"],
    )
    return app


#: App par défaut du dépôt — celle que `celery -A maestro.queue worker` démarre
#: et sur laquelle l'orchestrateur pousse ses tâches (configuration : REDIS_URL).
app = create_app()
