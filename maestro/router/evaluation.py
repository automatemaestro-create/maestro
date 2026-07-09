"""Évaluation du routage sur le jeu d'assignation versionné (ticket #42).

Mesure la **précision** d'un `Router` sur le jeu de test partagé
(`packages/shared/datasets/assignation.json`) : ≥ 10 tâches variées, chacune
avec l'agent attendu — ou `null` quand le bon comportement est le repli
« à assigner ». C'est le support du critère MVP n°3 (cahier des charges §8) :
au moins 9 tâches sur 10 correctement assignées, vérifié par un test automatisé
(`tests/test_router.py`).

Chaque tâche du jeu est validée contre la JSON Schema partagée au chargement :
le jeu reste ainsi aligné sur le contrat de tâche réel, pas sur une copie.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.orchestrator.schema import Task, validate_task
from maestro.router.router import Router, RoutingDecision

#: Emplacement du jeu d'assignation partagé, relatif à la racine du dépôt
#: (`evaluation.py` → `router` → `maestro` → racine ; puis `packages/shared/...`).
DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "shared"
    / "datasets"
    / "assignation.json"
)


@dataclass(frozen=True)
class CasAssignation:
    """Un cas du jeu : la tâche à router et l'agent attendu (None ⇒ « à assigner »)."""

    task: Task
    agent_attendu: str | None


@dataclass(frozen=True)
class DetailCas:
    """Le verdict du routeur sur un cas, confronté à l'attendu."""

    cas: CasAssignation
    decision: RoutingDecision

    @property
    def correct(self) -> bool:
        """Le routage correspond-il à l'attendu (agent visé, ou repli attendu) ?"""
        if self.cas.agent_attendu is None:
            return self.decision.a_assigner
        return self.decision.agent is not None and (
            self.decision.agent.nom == self.cas.agent_attendu
        )


@dataclass(frozen=True)
class ResultatEvaluation:
    """Précision d'un routeur sur le jeu : le détail par cas et les agrégats."""

    details: tuple[DetailCas, ...]

    @property
    def total(self) -> int:
        return len(self.details)

    @property
    def corrects(self) -> int:
        return sum(1 for d in self.details if d.correct)

    @property
    def precision(self) -> float:
        """Part des cas correctement routés, dans [0, 1]."""
        return self.corrects / self.total

    @property
    def erreurs(self) -> tuple[DetailCas, ...]:
        """Les cas mal routés — pour diagnostiquer un score sous le seuil."""
        return tuple(d for d in self.details if not d.correct)

    def resume(self) -> str:
        """Rend le score en une ligne, avec les cas fautifs le cas échéant."""
        lignes = [f"{self.corrects}/{self.total} assignations correctes."]
        for d in self.erreurs:
            obtenu = d.decision.agent.nom if d.decision.agent else "à assigner"
            attendu = d.cas.agent_attendu or "à assigner"
            lignes.append(f"- {d.cas.task.id} : attendu {attendu}, obtenu {obtenu}")
        return "\n".join(lignes)


def charger_jeu(path: Path = DATASET_PATH) -> tuple[CasAssignation, ...]:
    """Charge le jeu d'assignation et valide chaque tâche contre le schéma partagé.

    Lève `ValueError` si le jeu est vide ou si un `agent_attendu` n'est ni une
    chaîne ni null ; `TaskValidationError` si une tâche enfreint le schéma.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    cas_bruts = data.get("cas", [])
    if not cas_bruts:
        raise ValueError(f"Jeu d'assignation vide ou malformé : {path}")

    jeu: list[CasAssignation] = []
    for index, brut in enumerate(cas_bruts):
        tache = brut.get("tache", {})
        validate_task(tache, where=f"cas #{index + 1}")
        attendu = brut.get("agent_attendu")
        if attendu is not None and not isinstance(attendu, str):
            raise ValueError(
                f"cas #{index + 1} : agent_attendu doit être un nom d'agent ou null "
                f"(reçu : {attendu!r})."
            )
        jeu.append(CasAssignation(task=Task.from_dict(tache), agent_attendu=attendu))
    return tuple(jeu)


async def evaluer(
    router: Router, jeu: Sequence[CasAssignation] | None = None
) -> ResultatEvaluation:
    """Route chaque cas du jeu avec `router` et renvoie la précision mesurée."""
    cas = tuple(jeu) if jeu is not None else charger_jeu()
    details = [DetailCas(cas=c, decision=await router.route(c.task)) for c in cas]
    return ResultatEvaluation(details=tuple(details))
