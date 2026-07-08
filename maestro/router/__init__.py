"""Routeur d'auto-assignation de Maestro (ticket #6).

Assigne chaque tâche à l'agent le plus compétent, par règles de compétences
(recouvrement `competences_requises` ∩ `competences` de l'agent). Point d'entrée :

    from maestro.router import assign
    from maestro.agents import DEFAULT_AGENTS

    assignation = assign(task, DEFAULT_AGENTS)  # -> Assignment(task, agent, score)

Correspond à la transition `prete → assignee` de la machine à états des tâches.
"""

from __future__ import annotations

from maestro.router.router import Assignment, RoutingError, assign

__all__ = [
    "Assignment",
    "RoutingError",
    "assign",
]
