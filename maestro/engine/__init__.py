"""Moteur d'orchestration de Maestro — la boucle du POC (ticket #6).

Relie l'orchestrateur (#3), le routeur (#6) et les agents (#6) en une boucle qui
transforme un objectif en résultats agrégés :

    from maestro.engine import OrchestrationEngine

    engine = OrchestrationEngine.default()          # fournisseur Claude (POC)
    report = await engine.run("Créer une API de gestion de tâches")
    print(report.synthese())                        # agrégat Markdown

Le moteur ne dépend que de la couche d'abstraction fournisseur (`ModelProvider`) :
il reste agnostique du fournisseur, exactement comme l'orchestrateur.

Chaque tâche s'exécute sous **garde-fous** (#9, `maestro.engine.guardrails`) :
plafond de dépense, time-out, et validation humaine des actions sensibles —
configurables via `Guardrails` injecté au moteur.

L'exécution d'une tâche passe par la frontière injectable `TaskExecutor` (#41) :
`LocalExecutor` en process par défaut, ou `maestro.queue.CeleryExecutor` pour
distribuer les tâches à des workers via la file Celery + Redis.
"""

from __future__ import annotations

from maestro.engine.executor import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskExecutor,
    TaskResult,
)
from maestro.engine.guardrails import (
    MOTS_SENSIBLES,
    DemandeValidation,
    Guardrails,
)
from maestro.engine.loop import (
    OrchestrationEngine,
    RunReport,
)

__all__ = [
    "DemandeValidation",
    "Guardrails",
    "LocalExecutor",
    "MOTS_SENSIBLES",
    "OrchestrationEngine",
    "RunReport",
    "STATUT_ECHEC",
    "STATUT_TERMINEE",
    "TaskExecutor",
    "TaskResult",
]
