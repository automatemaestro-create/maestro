"""Côté worker de la file de tâches : consommation et exécution (ticket #41).

Déclare la tâche Celery `maestro.executer_tache`, consommée par les workers
(`celery -A maestro.queue worker`). Chaque worker exécute la tâche **hors du
process de l'orchestrateur**, via le même chemin que l'exécution locale
(`maestro.engine.LocalExecutor`) : routage vers l'agent compétent, garde-fous
(#9), runtime outillé ou appel texte, mesure d'usage (#8). Le résultat complet
(statut, livrable, erreur, fichiers produits, usage) repart par le backend de
résultats sous la forme `TaskResult.to_dict()` — c'est la **remontée** que le
côté orchestrateur (`maestro.queue.dispatch`) reconstruit.

Deux familles d'échec, deux canaux :

- un échec **métier** (routage impossible, garde-fou déclenché, agent en erreur)
  est une *donnée* : la tâche Celery réussit et renvoie un `TaskResult` au
  statut `echec` — même résilience que la boucle locale ;
- un échec de **payload ou d'environnement** (tâche non conforme au schéma
  partagé, fournisseur inconstructible) lève : Celery marque la tâche FAILURE
  et l'exception remonte à l'orchestrateur, qui la mue en échec de tâche.

Le fournisseur du worker est construit **paresseusement** (au premier message),
via une fabrique remplaçable par `configurer_worker` — le Claude du POC par
défaut, un fournisseur factice dans les tests d'intégration. La configuration
est propre au process du worker.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from celery import shared_task
from celery.signals import celeryd_after_setup

from maestro.config import load_settings
from maestro.engine.executor import LocalExecutor, TaskResult
from maestro.engine.guardrails import Guardrails
from maestro.orchestrator.schema import Task, validate_task
from maestro.providers.base import ModelProvider
from maestro.queue.celery_app import NOM_TACHE_EXECUTER
from maestro.telemetry import RunJournal

#: Fabrique du fournisseur d'un worker : appelée une fois par process, au premier
#: message consommé.
ProviderFactory = Callable[[], ModelProvider]


def _fabrique_claude() -> ModelProvider:
    """Fournisseur par défaut des workers : le Claude du POC, configuré par l'env.

    Importé ici (et non en tête de module) pour garder le worker agnostique du
    fournisseur — seule cette fabrique par défaut connaît Claude, comme
    `OrchestrationEngine.default`.
    """
    from maestro.providers.claude import ClaudeProvider

    return ClaudeProvider.from_settings(load_settings())


_provider_factory: ProviderFactory = _fabrique_claude
_guardrails: Guardrails | None = None
_executor: LocalExecutor | None = None

#: Identité du worker, rapportée sur chaque résultat (`TaskResult.worker`).
#: Le contexte Celery (`request.hostname`) ne porte que le nom de la machine
#: (le nodename y est écrasé par `gethostname()` dans `trace_task_ret`) : le
#: nodename exact est donc retenu à part — au niveau du process pour un vrai
#: worker CLI (`celeryd_after_setup`), au niveau du thread pour les workers
#: embarqués des tests (`nommer_worker`, un worker de test = un thread).
_nom_worker_process: str | None = None
_nom_worker_thread = threading.local()


@celeryd_after_setup.connect
def _retenir_nodename(sender: str, instance: Any, **kwargs: Any) -> None:
    """Retient le nodename du worker CLI (`celery worker -n agent1@%h`) au démarrage."""
    global _nom_worker_process
    _nom_worker_process = sender


def nommer_worker(nom: str) -> None:
    """Fixe le nom de worker rapporté par les résultats, pour le **thread courant**.

    Point d'injection des workers embarqués (tests d'intégration) : plusieurs
    workers y cohabitent dans un même process — un par thread — et
    `celeryd_after_setup` n'y est pas émis.
    """
    _nom_worker_thread.nom = nom


def _nom_du_worker(request: Any) -> str:
    """Le nom du worker courant : thread, sinon process, sinon la machine (contexte)."""
    nom_thread: str | None = getattr(_nom_worker_thread, "nom", None)
    return nom_thread or _nom_worker_process or request.hostname or ""


def configurer_worker(
    *,
    provider_factory: ProviderFactory | None = None,
    guardrails: Guardrails | None = None,
) -> None:
    """Configure l'exécution de CE process worker (fournisseur, garde-fous).

    À appeler avant la consommation (démarrage du worker, ou fixture de test).
    Ne touche que le process courant : chaque worker se configure lui-même.
    L'exécuteur courant est invalidé — il sera reconstruit au prochain message.
    """
    global _provider_factory, _guardrails, _executor
    if provider_factory is not None:
        _provider_factory = provider_factory
    if guardrails is not None:
        _guardrails = guardrails
    _executor = None


def reinitialiser_worker() -> None:
    """Rétablit la configuration par défaut du worker (fabrique Claude, garde-fous défaut)."""
    global _provider_factory, _guardrails, _executor
    _provider_factory = _fabrique_claude
    _guardrails = None
    _executor = None


def _executeur() -> LocalExecutor:
    """L'exécuteur du process worker, construit paresseusement au premier message."""
    global _executor
    if _executor is None:
        _executor = LocalExecutor(_provider_factory(), guardrails=_guardrails)
    return _executor


@shared_task(bind=True, name=NOM_TACHE_EXECUTER)
def executer_tache(
    self: Any,
    task_data: dict[str, Any],
    dependances_data: Sequence[dict[str, Any]] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Consomme une tâche de la file : exécute et renvoie `TaskResult.to_dict()`.

    `task_data` est validé contre le schéma partagé avant toute exécution (une
    tâche malformée est refusée en FAILURE, jamais exécutée) ; `dependances_data`
    porte les résultats des tâches prérequises (le tableau noir), résolus côté
    orchestrateur — les workers ne partagent aucun état. `run_id` relie les
    traces du worker (journal JSON local à son process) à l'exécution globale.
    Ce journal reconstruit à chaque message ne voit que la tâche courante : un
    plafond de dépense configuré côté worker (#56) s'applique donc à la tâche
    seule, pas à l'exécution entière.

    Le résultat porte le nom du worker (`worker`) : c'est ce qui rend visible la
    répartition des tâches entre workers distincts (critère MVP n°2).
    """
    validate_task(task_data)
    task = Task.from_dict(task_data)
    dependances = [TaskResult.from_dict(d) for d in dependances_data]
    journal = RunJournal(run_id=run_id)
    result = asyncio.run(_executeur().execute(task, dependances, journal))
    result = replace(result, worker=_nom_du_worker(self.request))
    return result.to_dict()
