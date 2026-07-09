"""Routeur d'auto-assignation de Maestro (tickets #6 et #42).

Assigne chaque tâche à l'agent le plus compétent en combinant les deux signaux
du routage (docs/01 §3.2) : les **règles de compétences** (recouvrement
`competences_requises` ∩ `competences` de l'agent) et un **classifieur léger**
(modèle rapide via `ModelProvider`) pour les cas ambigus. Point d'entrée :

    from maestro.agents import DEFAULT_AGENTS
    from maestro.router import Router, TaskClassifier

    router = Router(DEFAULT_AGENTS, classifier=TaskClassifier(provider))
    decision = await router.route(task)   # -> RoutingDecision
    if decision.a_assigner:               # repli explicite : confiance trop faible
        ...                               # la tâche attend une assignation manuelle

Correspond à la transition `prete → assignee` de la machine à états des tâches ;
le repli « à assigner » laisse la tâche à l'assignation manuelle plutôt que de
la mal router (critère MVP n°3). `assign` reste la règle de compétences pure du
POC (#6). La précision du routage est mesurée sur le jeu versionné
(`maestro.router.evaluation`).
"""

from __future__ import annotations

from maestro.router.classifier import (
    MODELE_CLASSIFIEUR,
    Classification,
    TaskClassifier,
)
from maestro.router.evaluation import (
    DATASET_PATH,
    CasAssignation,
    DetailCas,
    ResultatEvaluation,
    charger_jeu,
    evaluer,
)
from maestro.router.router import (
    METHODE_CLASSIFIEUR,
    METHODE_COMPETENCES,
    METHODE_REPLI,
    SEUIL_CONFIANCE_DEFAUT,
    Assignment,
    Router,
    RoutingDecision,
    RoutingError,
    assign,
)

__all__ = [
    "DATASET_PATH",
    "METHODE_CLASSIFIEUR",
    "METHODE_COMPETENCES",
    "METHODE_REPLI",
    "MODELE_CLASSIFIEUR",
    "SEUIL_CONFIANCE_DEFAUT",
    "Assignment",
    "CasAssignation",
    "Classification",
    "DetailCas",
    "ResultatEvaluation",
    "Router",
    "RoutingDecision",
    "RoutingError",
    "TaskClassifier",
    "assign",
    "charger_jeu",
    "evaluer",
]
