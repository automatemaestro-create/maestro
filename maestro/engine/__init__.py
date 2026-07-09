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
"""

from __future__ import annotations

from maestro.engine.guardrails import (
    MOTS_SENSIBLES,
    DemandeValidation,
    Guardrails,
)
from maestro.engine.loop import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    OrchestrationEngine,
    RunReport,
    TaskResult,
)

__all__ = [
    "DemandeValidation",
    "Guardrails",
    "MOTS_SENSIBLES",
    "OrchestrationEngine",
    "RunReport",
    "STATUT_ECHEC",
    "STATUT_TERMINEE",
    "TaskResult",
]
