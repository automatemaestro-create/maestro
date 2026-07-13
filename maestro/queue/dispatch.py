"""Côté orchestrateur de la file de tâches : mise en file et remontée (ticket #41).

`CeleryExecutor` est l'exécuteur **distribué** de la boucle d'orchestration : là
où `LocalExecutor` exécute dans le process, il **pousse** chaque tâche dans la
file Celery + Redis (`send_task`, par nom — le code worker n'est jamais importé
ici : mise en file et consommation sont découplées) puis attend le résultat
renvoyé par un worker. La boucle (`maestro.engine.loop`) ne change pas : elle
continue de résoudre les dépendances et de lancer les tâches indépendantes en
parallèle — chacune devient un message en file, consommable par n'importe quel
worker disponible ; deux workers = deux agents réellement en parallèle, hors du
process de l'orchestrateur.

Même contrat que tout `TaskExecutor` : `execute` ne lève jamais. Un échec de
**transport** (file injoignable, worker perdu, délai dépassé, exception worker)
devient un `TaskResult` en échec, consigné au journal comme les autres — la
boucle reste résiliente. Les échecs *métier* voyagent, eux, en données : le
worker renvoie un `TaskResult` au statut `echec`, remonté tel quel.

L'attente du résultat est **bloquante côté Celery** (`AsyncResult.get`) : elle
est déportée dans un thread (`asyncio.to_thread`) pour que la boucle asyncio
continue de dispatcher les autres tâches pendant ce temps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter

from celery import Celery
from celery.exceptions import TimeoutError as CeleryTimeoutError

from maestro.config import Settings, load_settings
from maestro.engine.executor import (
    TaskExecutor,
    TaskResult,
    _build_task_description,
    _echec,
    _ecoule_ms,
)
from maestro.engine.loop import OrchestrationEngine
from maestro.messaging.mailbox import Mailbox
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.queue.celery_app import NOM_TACHE_EXECUTER
from maestro.telemetry import RunJournal, StepUsage

#: Délai d'attente par défaut du résultat d'une tâche (secondes). Évite qu'un
#: worker perdu ou une file vide de consommateurs suspende la boucle pour
#: toujours ; une tâche d'agent (appels modèle compris) tient largement dedans.
TIMEOUT_RESULTAT_S = 600.0


class CeleryExecutor(TaskExecutor):
    """Exécuteur distribué : chaque tâche part dans la file, son résultat en revient.

    S'injecte dans la boucle (`OrchestrationEngine(..., executor=...)`) à la
    place de l'exécution locale. `app` est l'application Celery à utiliser —
    celle du dépôt par défaut (`maestro.queue.celery_app.app`, configurée sur
    `REDIS_URL`) ; les tests y substituent une app sur transport mémoire.
    `timeout_s` borne l'attente du résultat de **chaque** tâche (None : attente
    illimitée — déconseillé hors débogage).
    """

    def __init__(
        self, app: Celery | None = None, *, timeout_s: float | None = TIMEOUT_RESULTAT_S
    ) -> None:
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError(f"timeout_s doit être > 0 (reçu : {timeout_s}).")
        if app is None:
            from maestro.queue.celery_app import app as app_defaut

            app = app_defaut
        self._app = app
        self._timeout_s = timeout_s

    async def execute(
        self, task: Task, dependances: Sequence[TaskResult], journal: RunJournal
    ) -> TaskResult:
        """Pousse `task` dans la file, attend son résultat et le consigne au journal.

        Le tableau noir (résultats des dépendances) voyage **avec** le message :
        les workers ne partagent aucun état avec l'orchestrateur ni entre eux.
        L'usage porté par le résultat est celui mesuré par le worker (le vrai
        coût de la tâche) ; seul un échec de transport est chronométré ici.
        """
        entree = _build_task_description(task, dependances)
        debut = perf_counter()
        try:
            result = await asyncio.to_thread(
                self._envoie_et_attend, task, dependances, journal.run_id
            )
        # celery.exceptions.TimeoutError ne descend pas du TimeoutError natif :
        # on attrape les deux pour couvrir tous les transports.
        except (TimeoutError, CeleryTimeoutError):
            result = replace(
                _echec(
                    task,
                    agent="—",
                    role="non exécuté",
                    score=0,
                    erreur=(
                        f"file de tâches : résultat non remonté après {self._timeout_s:g} s "
                        "— worker indisponible ou tâche toujours en file."
                    ),
                ),
                usage=StepUsage().avec_duree(_ecoule_ms(debut)),
            )
        except Exception as exc:  # transport ou worker en erreur : échec consigné, jamais levé
            result = replace(
                _echec(
                    task,
                    agent="—",
                    role="non exécuté",
                    score=0,
                    erreur=f"file de tâches : {exc}",
                ),
                usage=StepUsage().avec_duree(_ecoule_ms(debut)),
            )
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=result.agent,
            role=result.role,
            statut=result.statut,
            entree=entree,
            sortie=result.sortie,
            erreur=result.erreur,
            usage=result.usage,
        )
        return result

    def _envoie_et_attend(
        self, task: Task, dependances: Sequence[TaskResult], run_id: str
    ) -> TaskResult:
        """Bloquant (déporté dans un thread) : `send_task` puis attente du résultat.

        L'envoi se fait **par nom** (`NOM_TACHE_EXECUTER`) : l'orchestrateur ne
        dépend que du contrat JSON de la file, pas du code des workers.
        """
        async_result = self._app.send_task(
            NOM_TACHE_EXECUTER,
            args=[task.to_dict(), [d.to_dict() for d in dependances], run_id],
        )
        brut = async_result.get(timeout=self._timeout_s)
        return TaskResult.from_dict(brut)


def create_distributed_engine(
    settings: Settings | None = None,
    *,
    app: Celery | None = None,
    timeout_s: float | None = TIMEOUT_RESULTAT_S,
    mailbox: Mailbox | None = None,
) -> OrchestrationEngine:
    """Boucle d'orchestration branchée sur la file : le pendant distribué de `default`.

    La **planification** reste dans le process de l'orchestrateur (fournisseur
    désigné par la config, comme `OrchestrationEngine.default`, #69) ;
    l'**exécution** des tâches part dans la file, vers les workers démarrés par
    ailleurs (`celery -A maestro.queue worker`). Les garde-fous d'exécution (#9)
    s'appliquent côté worker — voir `maestro.queue.worker.configurer_worker`.
    `mailbox` (#44) branche la messagerie inter-agents (handoff observable) sur
    la boucle, comme en local.
    """
    from maestro.providers.factory import default_model, provider_from_settings

    settings = settings or load_settings()
    provider = provider_from_settings(settings)
    orchestrator = Orchestrator(provider, model=default_model(settings))
    return OrchestrationEngine(
        provider,
        orchestrator,
        executor=CeleryExecutor(app, timeout_s=timeout_s),
        mailbox=mailbox,
    )
