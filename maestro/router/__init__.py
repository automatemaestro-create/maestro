"""Maestro's automatic assignment router (#6 and #42).

Assigns each task to the most competent agent by combining the two routing signals
(docs/01 §3.2): the **competency rules** (overlap of `competences_requises` and the
agent's `competences`) and a **lightweight classifier** (a fast model through
`ModelProvider`) for ambiguous cases. Entry point:

    from maestro.agents import catalogue
    from maestro.router import Router, TaskClassifier

    router = Router(catalogue(), classifier=TaskClassifier(provider))
    decision = await router.route(task)   # -> RoutingDecision
    if decision.needs_assignment:         # explicit fallback: confidence too low
        ...                               # the task awaits a manual assignment

It matches the `prete → assignee` transition of the task state machine; the "à
assigner" fallback leaves the task to manual assignment rather than routing it
wrongly (MVP criterion 3). `assign` remains the pure competency rule (#6). Routing
precision is measured on the versioned dataset (`maestro.router.evaluation`).
"""

from __future__ import annotations

from maestro.router.classifier import (
    CLASSIFIER_CLOSEST_SYSTEM_PROMPT,
    CLASSIFIER_MODEL,
    Classification,
    TaskClassifier,
)
from maestro.router.evaluation import (
    DATASET_PATH,
    AssignmentCase,
    CaseDetail,
    EvaluationResult,
    evaluate,
    load_dataset,
)
from maestro.router.router import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    METHOD_CLASSIFIER,
    METHOD_CLOSEST,
    METHOD_COMPETENCIES,
    METHOD_FALLBACK,
    Assignment,
    Router,
    RoutingDecision,
    RoutingError,
    assign,
)

__all__ = [
    "CLASSIFIER_CLOSEST_SYSTEM_PROMPT",
    "CLASSIFIER_MODEL",
    "DATASET_PATH",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "METHOD_CLASSIFIER",
    "METHOD_CLOSEST",
    "METHOD_COMPETENCIES",
    "METHOD_FALLBACK",
    "Assignment",
    "AssignmentCase",
    "CaseDetail",
    "Classification",
    "EvaluationResult",
    "Router",
    "RoutingDecision",
    "RoutingError",
    "TaskClassifier",
    "assign",
    "evaluate",
    "load_dataset",
]
