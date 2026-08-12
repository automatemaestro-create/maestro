"""Erreurs de l'orchestrateur (tickets #3, #318).

Hiérarchie plate sous `OrchestratorError` pour que l'appelant puisse attraper
tout échec d'orchestration d'un seul `except`, ou discriminer le parsing de la
validation.

Le brief (#318) a ses **propres** erreurs plutôt que de réemployer celles du plan :
les deux étapes attendent des formes différentes (un objet, un tableau), donc un
message qui parle de « tableau JSON de tâches » là où l'on attendait un brief
envoie chercher au mauvais endroit. Elles restent sous la même base : un appelant
qui ne veut que « l'orchestrateur a échoué » n'a toujours qu'un `except` à écrire.
"""

from __future__ import annotations


class OrchestratorError(RuntimeError):
    """Base de tous les échecs de l'orchestrateur."""


class PlanParsingError(OrchestratorError):
    """La réponse du modèle n'a pas pu être décodée en un tableau JSON de tâches."""


class TaskValidationError(OrchestratorError):
    """Une tâche (ou le plan) enfreint le schéma ou les règles inter-tâches."""


class BriefParsingError(OrchestratorError):
    """La réponse du modèle n'a pas pu être décodée en un objet JSON de brief (#318)."""


class BriefValidationError(OrchestratorError):
    """Le brief enfreint la JSON Schema partagée `brief.schema.json` (#318)."""
