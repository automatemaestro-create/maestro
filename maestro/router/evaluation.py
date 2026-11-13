"""Routing evaluation on the versioned assignment dataset (#42).

Measures the **precision** of a `Router` on the shared test dataset
(`packages/shared/datasets/assignation.json`): at least 10 varied tasks, each with
the expected agent, or `null` when the right behaviour is the "à assigner"
fallback. It backs MVP criterion 3 (specification §8): at least 9 tasks out of 10
correctly assigned, checked by an automated test (`tests/test_router.py`).

Each task of the dataset is validated against the shared JSON Schema on loading:
the dataset thus stays aligned with the real task contract, not with a copy.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.orchestrator.schema import Task, validate_task
from maestro.router.router import Router, RoutingDecision

#: Location of the shared assignment dataset, relative to the repository root
#: (`evaluation.py` → `router` → `maestro` → root; then `packages/shared/...`).
DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "shared"
    / "datasets"
    / "assignation.json"
)


@dataclass(frozen=True)
class AssignmentCase:
    """A case of the dataset: the task to route and the expected agent (None: "à assigner")."""

    task: Task
    expected_agent: str | None


@dataclass(frozen=True)
class CaseDetail:
    """The router's verdict on a case, compared with what was expected."""

    case: AssignmentCase
    decision: RoutingDecision

    @property
    def correct(self) -> bool:
        """Does the routing match what was expected (the target agent, or the expected fallback)?"""
        if self.case.expected_agent is None:
            return self.decision.needs_assignment
        return self.decision.agent is not None and (
            self.decision.agent.nom == self.case.expected_agent
        )


@dataclass(frozen=True)
class EvaluationResult:
    """A router's precision on the dataset: the detail per case and the aggregates."""

    details: tuple[CaseDetail, ...]

    @property
    def total(self) -> int:
        return len(self.details)

    @property
    def correct_count(self) -> int:
        return sum(1 for d in self.details if d.correct)

    @property
    def precision(self) -> float:
        """Share of the cases correctly routed, in [0, 1]."""
        return self.correct_count / self.total

    @property
    def errors(self) -> tuple[CaseDetail, ...]:
        """The wrongly routed cases, to diagnose a score under the threshold."""
        return tuple(d for d in self.details if not d.correct)

    def summary(self) -> str:
        """Render the score on one line, with the failing cases if any."""
        lines = [f"{self.correct_count}/{self.total} assignations correctes."]
        for d in self.errors:
            obtained = d.decision.agent.nom if d.decision.agent else "à assigner"
            expected = d.case.expected_agent or "à assigner"
            lines.append(f"- {d.case.task.id} : attendu {expected}, obtenu {obtained}")
        return "\n".join(lines)


def load_dataset(path: Path = DATASET_PATH) -> tuple[AssignmentCase, ...]:
    """Load the assignment dataset and validate each task against the shared schema.

    Raises `ValueError` if the dataset is empty or if an `agent_attendu` is neither a
    string nor null; `TaskValidationError` if a task breaks the schema.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = data.get("cas", [])
    if not raw_cases:
        raise ValueError(f"Jeu d'assignation vide ou malformé : {path}")

    dataset: list[AssignmentCase] = []
    for index, raw in enumerate(raw_cases):
        task_data = raw.get("tache", {})
        validate_task(task_data, where=f"cas #{index + 1}")
        expected = raw.get("agent_attendu")
        if expected is not None and not isinstance(expected, str):
            raise ValueError(
                f"cas #{index + 1} : agent_attendu doit être un nom d'agent ou null "
                f"(reçu : {expected!r})."
            )
        dataset.append(AssignmentCase(task=Task.from_dict(task_data), expected_agent=expected))
    return tuple(dataset)


async def evaluate(
    router: Router, dataset: Sequence[AssignmentCase] | None = None
) -> EvaluationResult:
    """Route each case of the dataset with `router` and return the measured precision."""
    cases = tuple(dataset) if dataset is not None else load_dataset()
    details = [CaseDetail(case=c, decision=await router.route(c.task)) for c in cases]
    return EvaluationResult(details=tuple(details))
