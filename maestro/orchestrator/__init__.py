"""Orchestrateur de Maestro — objectif → brief, puis → tâches (#3, #318).

Le Chef de projet (docs/04-specifications-agents.md) découpe un objectif en langage
naturel en tâches structurées, validées contre le schéma partagé
`packages/shared/schemas/task.schema.json`.

    from maestro.orchestrator import Orchestrator

    orchestrator = Orchestrator.default()           # fournisseur Claude (POC)
    tasks = await orchestrator.plan("Créer une API de gestion de tâches")
    for task in tasks:
        print(task.id, task.titre, task.dependances)

Avant de découper, il sait **cadrer** (#318) : le brief structuré rend l'intention
relisable — et corrigeable — par un humain avant la première exécution payante.

    brief = await orchestrator.brief("Créer une API de gestion de tâches")
    print(brief.synthese())                         # les sept sections, en Markdown
    if brief.a_des_questions:
        ...                                         # aller-retour de clarification (#321)

Le cœur (`Orchestrator.plan`) ne dépend que de la couche d'abstraction fournisseur
(`ModelProvider`) : brancher un autre orchestrateur sur OpenAI/Google/local ne
demande qu'un autre `ModelProvider`, sans toucher au découpage ni au schéma.
"""

from __future__ import annotations

from maestro.orchestrator.errors import (
    BriefParsingError,
    BriefValidationError,
    OrchestratorError,
    PlanParsingError,
    TaskValidationError,
)
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.prompt import (
    BRIEF_SYSTEM_PROMPT,
    MAX_TASKS,
    MIN_TASKS,
    ORCHESTRATOR_SYSTEM_PROMPT,
    build_brief_user_prompt,
    build_user_prompt,
)
from maestro.orchestrator.schema import (
    BRIEF_SCHEMA_PATH,
    SCHEMA_PATH,
    Brief,
    Task,
    load_brief_schema,
    load_task_schema,
    topological_order,
    validate_brief,
    validate_plan,
    validate_task,
)

__all__ = [
    "BRIEF_SCHEMA_PATH",
    "BRIEF_SYSTEM_PROMPT",
    "MAX_TASKS",
    "MIN_TASKS",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "SCHEMA_PATH",
    "Brief",
    "BriefParsingError",
    "BriefValidationError",
    "Orchestrator",
    "OrchestratorError",
    "PlanParsingError",
    "Task",
    "TaskValidationError",
    "build_brief_user_prompt",
    "build_user_prompt",
    "load_brief_schema",
    "load_task_schema",
    "topological_order",
    "validate_brief",
    "validate_plan",
    "validate_task",
]
