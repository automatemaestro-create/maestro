"""Orchestrateur de Maestro — objectif → liste de tâches JSON (ticket #3).

Le Chef de projet (docs/04-specifications-agents.md) découpe un objectif en langage
naturel en tâches structurées, validées contre le schéma partagé
`packages/shared/schemas/task.schema.json`.

    from maestro.orchestrator import Orchestrator

    orchestrator = Orchestrator.default()           # fournisseur Claude (POC)
    tasks = await orchestrator.plan("Créer une API de gestion de tâches")
    for task in tasks:
        print(task.id, task.titre, task.dependances)

Le cœur (`Orchestrator.plan`) ne dépend que de la couche d'abstraction fournisseur
(`ModelProvider`) : brancher un autre orchestrateur sur OpenAI/Google/local ne
demande qu'un autre `ModelProvider`, sans toucher au découpage ni au schéma.
"""

from __future__ import annotations

from maestro.orchestrator.errors import (
    OrchestratorError,
    PlanParsingError,
    TaskValidationError,
)
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.prompt import (
    MAX_TASKS,
    MIN_TASKS,
    ORCHESTRATOR_SYSTEM_PROMPT,
    build_user_prompt,
)
from maestro.orchestrator.schema import (
    SCHEMA_PATH,
    Task,
    load_task_schema,
    validate_plan,
    validate_task,
)

__all__ = [
    "MAX_TASKS",
    "MIN_TASKS",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "Orchestrator",
    "OrchestratorError",
    "PlanParsingError",
    "SCHEMA_PATH",
    "Task",
    "TaskValidationError",
    "build_user_prompt",
    "load_task_schema",
    "validate_plan",
    "validate_task",
]
