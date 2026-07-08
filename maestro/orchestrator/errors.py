"""Erreurs de l'orchestrateur (ticket #3).

Hiérarchie plate sous `OrchestratorError` pour que l'appelant puisse attraper
tout échec d'orchestration d'un seul `except`, ou discriminer le parsing de la
validation.
"""

from __future__ import annotations


class OrchestratorError(RuntimeError):
    """Base de tous les échecs de l'orchestrateur."""


class PlanParsingError(OrchestratorError):
    """La réponse du modèle n'a pas pu être décodée en un tableau JSON de tâches."""


class TaskValidationError(OrchestratorError):
    """Une tâche (ou le plan) enfreint le schéma ou les règles inter-tâches."""
