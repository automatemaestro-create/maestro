"""Maestro's orchestrator: goal → brief, then → tasks (#3, #318).

The orchestrator (the Chef de projet, docs/04-specifications-agents.md) splits a
natural-language goal into structured tasks, validated against the shared schema
`packages/shared/schemas/task.schema.json`.

    from maestro.orchestrator import Orchestrator

    orchestrator = Orchestrator.default()           # provider and model from the config
    tasks = await orchestrator.plan("Créer une API de gestion de tâches")
    for task in tasks:
        print(task.id, task.title, task.dependencies)

Before splitting, it **frames** the goal (#318): the structured brief makes the intent
reviewable, and correctable, by a human before the first paid execution.

    brief = await orchestrator.brief("Créer une API de gestion de tâches")
    print(brief.summary())                          # the seven sections, in Markdown
    if brief.has_questions:
        ...                                         # clarification round (#321)

The core (`Orchestrator.plan`) depends only on the provider abstraction layer
(`ModelProvider`): plugging the orchestrator onto OpenAI, Google or a local model only takes
another `ModelProvider`, without touching the decomposition or the schema.
"""

from __future__ import annotations

from maestro.orchestrator.errors import (
    BriefParsingError,
    BriefValidationError,
    OrchestratorError,
    PlanParsingError,
    RecoveryParsingError,
    RecoveryValidationError,
    TaskValidationError,
)
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.prompt import (
    BRIEF_SYSTEM_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
    build_brief_user_prompt,
    build_user_prompt,
    orchestrator_prompt,
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
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "SCHEMA_PATH",
    "Brief",
    "BriefParsingError",
    "BriefValidationError",
    "Orchestrator",
    "OrchestratorError",
    "PlanParsingError",
    "RecoveryParsingError",
    "RecoveryValidationError",
    "Task",
    "TaskValidationError",
    "build_brief_user_prompt",
    "build_user_prompt",
    "load_brief_schema",
    "load_task_schema",
    "orchestrator_prompt",
    "topological_order",
    "validate_brief",
    "validate_plan",
    "validate_task",
]
