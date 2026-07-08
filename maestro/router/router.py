"""Routeur : auto-assignation d'une tâche à un agent par règles de compétences (ticket #6).

Implémente la transition `prete → assignee` de la machine à états des tâches
(docs/03-modele-de-donnees.md §3) : confronte les `competences_requises` d'une tâche
aux `competences` déclarées par chaque agent (entité CAPABILITY) et retient le
**meilleur recouvrement**.

Règle « simple » du POC (docs/06 — auto-assignation) :

- score = nombre de compétences requises couvertes par l'agent ;
- meilleur score gagne ; à égalité, l'**ordre du catalogue** tranche (déterministe) ;
- si aucun agent ne couvre la moindre compétence requise, `RoutingError` — la tâche
  est mal spécifiée ou hors du périmètre des agents disponibles.

Le classifieur léger pour les cas ambigus (docs/01 §3.2) viendra plus tard : ici,
règle de compétences pure, sans appel modèle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.orchestrator.schema import Task


class RoutingError(RuntimeError):
    """Aucun agent du catalogue ne possède les compétences requises par la tâche."""


@dataclass(frozen=True)
class Assignment:
    """Résultat d'un routage : la tâche, l'agent retenu et son score de compétences."""

    task: Task
    agent: Agent
    score: int


def assign(task: Task, agents: Sequence[Agent]) -> Assignment:
    """Assigne `task` au meilleur agent de `agents` selon le recouvrement de compétences.

    Itère dans l'ordre du catalogue et ne remplace le candidat qu'à score
    **strictement** supérieur : à égalité, le premier agent (ordre du catalogue)
    l'emporte, ce qui rend le routage déterministe. Lève `ValueError` si `agents`
    est vide, `RoutingError` si le meilleur score est nul (aucune compétence couverte).
    """
    if not agents:
        raise ValueError("Aucun agent disponible pour le routage.")

    required = frozenset(task.competences_requises)
    best = max(
        (Assignment(task=task, agent=agent, score=agent.couverture(required)) for agent in agents),
        key=lambda a: a.score,
    )
    if best.score == 0:
        demandees = ", ".join(sorted(required)) or "aucune"
        raise RoutingError(
            f"Aucun agent ne couvre les compétences requises par la tâche {task.id!r} "
            f"(demandées : {demandees})."
        )
    return best
