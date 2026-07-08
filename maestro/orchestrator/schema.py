"""Schéma de tâche et validation (ticket #3).

Source unique de vérité : la JSON Schema partagée
`packages/shared/schemas/task.schema.json` (cf. son README). Ce module la charge,
en dérive un validateur `jsonschema`, et expose :

- `Task` : la forme Python (immuable) d'une tâche, avec `from_dict`/`to_dict` ;
- `validate_task` : valide un dict brut contre le schéma partagé ;
- `validate_plan` : valide une liste de tâches **et** les règles inter-tâches que
  le schéma seul ne peut exprimer (ids uniques, dépendances résolubles, graphe
  acyclique), et renvoie des `Task` prêtes à l'emploi.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from maestro.orchestrator.errors import TaskValidationError

#: Emplacement de la JSON Schema partagée, relatif à la racine du dépôt.
#: `schema.py` → `orchestrator` → `maestro` → racine ; puis `packages/shared/...`.
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "shared"
    / "schemas"
    / "task.schema.json"
)


@lru_cache(maxsize=1)
def load_task_schema() -> dict[str, Any]:
    """Charge (et met en cache) la JSON Schema de tâche partagée."""
    try:
        raw = SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - dépend de l'arborescence du dépôt
        raise TaskValidationError(
            f"Schéma de tâche introuvable à {SCHEMA_PATH}. Le dépôt est-il complet ?"
        ) from exc
    schema: dict[str, Any] = json.loads(raw)
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_task_schema()
    # Fait remonter une schéma malformée tôt et clairement plutôt qu'au 1er usage.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class Task:
    """Forme Python immuable d'une tâche, miroir de `task.schema.json`.

    Les champs multivalués sont des tuples (immuables) ; `to_dict` les réémet en
    listes pour un aller-retour JSON fidèle au schéma.
    """

    id: str
    titre: str
    description: str
    competences_requises: tuple[str, ...]
    format_sortie: str
    dependances: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Task:
        """Construit une `Task` depuis un dict **déjà validé** contre le schéma."""
        return cls(
            id=data["id"],
            titre=data["titre"],
            description=data["description"],
            competences_requises=tuple(data["competences_requises"]),
            format_sortie=data["format_sortie"],
            dependances=tuple(data.get("dependances", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet la tâche en dict JSON-sérialisable, conforme au schéma."""
        return {
            "id": self.id,
            "titre": self.titre,
            "description": self.description,
            "competences_requises": list(self.competences_requises),
            "format_sortie": self.format_sortie,
            "dependances": list(self.dependances),
        }


def validate_task(data: Mapping[str, Any], *, where: str = "tâche") -> None:
    """Valide un dict de tâche contre la JSON Schema partagée.

    Lève `TaskValidationError` avec un message agrégé (tous les manquements) si le
    dict n'est pas conforme. `where` situe la tâche dans un message de plan.
    """
    # Clé de tri en str : un chemin mêle noms de champs (str) et indices (int),
    # dont la comparaison directe lèverait TypeError.
    errors = sorted(_validator().iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        details = "; ".join(_format_error(e) for e in errors)
        raise TaskValidationError(f"{where} invalide : {details}.")


def validate_plan(tasks: Sequence[Mapping[str, Any]]) -> list[Task]:
    """Valide un plan complet et renvoie les `Task` correspondantes.

    Au-delà du schéma par tâche, garantit les invariants inter-tâches : plan non
    vide, `id` uniques, chaque dépendance résoluble dans le plan, aucune
    auto-dépendance, et graphe **acyclique**. Lève `TaskValidationError` au premier
    invariant enfreint.
    """
    if not tasks:
        raise TaskValidationError("Plan vide : l'orchestrateur doit produire au moins une tâche.")

    parsed: list[Task] = []
    by_id: dict[str, Task] = {}
    for index, raw in enumerate(tasks):
        validate_task(raw, where=f"tâche #{index + 1}")
        task = Task.from_dict(raw)
        if task.id in by_id:
            raise TaskValidationError(f"Identifiant de tâche dupliqué : {task.id!r}.")
        by_id[task.id] = task
        parsed.append(task)

    for task in parsed:
        for dep in task.dependances:
            if dep == task.id:
                raise TaskValidationError(f"La tâche {task.id!r} dépend d'elle-même.")
            if dep not in by_id:
                raise TaskValidationError(
                    f"La tâche {task.id!r} dépend de {dep!r}, absente du plan."
                )

    _ensure_acyclic(parsed)
    return parsed


def _ensure_acyclic(tasks: Sequence[Task]) -> None:
    """Détecte un cycle dans le graphe de dépendances (DFS à 3 couleurs)."""
    deps = {task.id: task.dependances for task in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(deps, WHITE)

    def visit(node: str) -> None:
        color[node] = GREY
        for nxt in deps[node]:
            if color[nxt] == GREY:
                raise TaskValidationError(
                    f"Cycle de dépendances détecté (impliquant {node!r} → {nxt!r})."
                )
            if color[nxt] == WHITE:
                visit(nxt)
        color[node] = BLACK

    for node in deps:
        if color[node] == WHITE:
            visit(node)


def _format_error(error: ValidationError) -> str:
    """Rend un `ValidationError` jsonschema lisible, situé par son chemin."""
    location = "".join(f"[{part!r}]" for part in error.path)
    prefix = f"champ {location} : " if location else ""
    return f"{prefix}{error.message}"
