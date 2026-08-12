"""Schémas de l'orchestrateur et validation (tickets #3, #318).

Source unique de vérité : les JSON Schema partagées de
`packages/shared/schemas/` (cf. son README). Ce module les charge, en dérive des
validateurs `jsonschema`, et expose les deux contrats que le Chef de projet
produit.

Le **plan** (#3) — ce qu'il rend une fois l'intention arrêtée :

- `Task` : la forme Python (immuable) d'une tâche, avec `from_dict`/`to_dict` ;
- `validate_task` : valide un dict brut contre le schéma partagé ;
- `validate_plan` : valide une liste de tâches **et** les règles inter-tâches que
  le schéma seul ne peut exprimer (ids uniques, dépendances résolubles, graphe
  acyclique), et renvoie des `Task` prêtes à l'emploi.

Le **brief** (#318) — ce qu'il rend *avant*, pour que l'intention soit arrêtée par
un humain plutôt que devinée :

- `Brief` : la forme Python (immuable) d'un brief structuré, avec sa `synthese`
  Markdown ;
- `validate_brief` : valide un dict brut contre le schéma partagé.

Les deux cohabitent **ici** plutôt que dans deux modules jumeaux : c'est la même
plomberie (chargement du fichier, `Draft202012Validator` mis en cache, mise en
forme des manquements) et la dupliquer ferait diverger les messages d'erreur de
deux contrats que le même agent produit à cinq minutes d'intervalle.
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

from maestro.appartenance import projet_id_valide
from maestro.orchestrator.errors import BriefValidationError, TaskValidationError
from maestro.references import ReferenceTicket

#: Dossier des JSON Schema partagées, relatif à la racine du dépôt.
#: `schema.py` → `orchestrator` → `maestro` → racine ; puis `packages/shared/...`.
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "packages" / "shared" / "schemas"

#: Emplacement de la JSON Schema de **tâche** partagée (#3).
SCHEMA_PATH = SCHEMAS_DIR / "task.schema.json"

#: Emplacement de la JSON Schema de **brief** partagée (#318).
BRIEF_SCHEMA_PATH = SCHEMAS_DIR / "brief.schema.json"


def _load_schema(chemin: Path, *, quoi: str, erreur: type[Exception]) -> dict[str, Any]:
    """Charge une JSON Schema partagée, ou lève `erreur` en nommant le fichier absent."""
    try:
        raw = chemin.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - dépend de l'arborescence du dépôt
        raise erreur(
            f"Schéma de {quoi} introuvable à {chemin}. Le dépôt est-il complet ?"
        ) from exc
    schema: dict[str, Any] = json.loads(raw)
    return schema


@lru_cache(maxsize=1)
def load_task_schema() -> dict[str, Any]:
    """Charge (et met en cache) la JSON Schema de tâche partagée."""
    return _load_schema(SCHEMA_PATH, quoi="tâche", erreur=TaskValidationError)


@lru_cache(maxsize=1)
def load_brief_schema() -> dict[str, Any]:
    """Charge (et met en cache) la JSON Schema de brief partagée (#318)."""
    return _load_schema(BRIEF_SCHEMA_PATH, quoi="brief", erreur=BriefValidationError)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_task_schema()
    # Fait remonter une schéma malformée tôt et clairement plutôt qu'au 1er usage.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@lru_cache(maxsize=1)
def _brief_validator() -> Draft202012Validator:
    schema = load_brief_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class Task:
    """Forme Python immuable d'une tâche, miroir de `task.schema.json`.

    Les champs multivalués sont des tuples (immuables) ; `to_dict` les réémet en
    listes pour un aller-retour JSON fidèle au schéma.

    `ticket` (#187) porte le ticket dont relève la tâche — None dans
    le cas courant, le plan n'en produisant pas de lui-même : elle est posée au
    lancement d'un run (le run part d'un ticket) ou par un agent en cours
    d'exécution. `to_dict` **omet** alors la clé plutôt que d'émettre `null` :
    le schéma refuse les propriétés inconnues comme les objets malformés, et un
    plan sans référence doit rester sérialisable tel quel.

    `projet_id` (#222) porte le **projet** auquel la tâche appartient — même
    régime que `ticket` : None dans le cas courant (le plan n'en produit pas de
    lui-même), posé au lancement d'un run et hérité par chaque tâche, et la clé
    est **omise** de `to_dict` quand il n'y en a pas.
    """

    id: str
    titre: str
    description: str
    competences_requises: tuple[str, ...]
    format_sortie: str
    dependances: tuple[str, ...] = ()
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None

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
            ticket=ReferenceTicket.depuis(data.get("ticket")),
            projet_id=projet_id_valide(data.get("projet_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet la tâche en dict JSON-sérialisable, conforme au schéma."""
        data: dict[str, Any] = {
            "id": self.id,
            "titre": self.titre,
            "description": self.description,
            "competences_requises": list(self.competences_requises),
            "format_sortie": self.format_sortie,
            "dependances": list(self.dependances),
        }
        if self.ticket is not None:
            data["ticket"] = self.ticket.to_dict()
        if self.projet_id is not None:
            data["projet_id"] = self.projet_id
        return data


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


def topological_order(tasks: Sequence[Task]) -> list[Task]:
    """Ordonne les tâches pour que chacune suive ses dépendances (tri de Kahn).

    Suppose un plan **déjà validé** (`validate_plan`) : dépendances résolubles et
    graphe acyclique. À rang de dépendance égal, conserve l'ordre du plan (tri
    stable) pour un résultat déterministe — utile au moteur d'orchestration, qui
    exécute les tâches dans cet ordre. Lève `TaskValidationError` si un cycle rend
    l'ordonnancement impossible (ne devrait pas arriver après `validate_plan`).
    """
    by_id = {task.id: task for task in tasks}
    indegree = {task.id: len(task.dependances) for task in tasks}
    dependents: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dep in task.dependances:
            dependents[dep].append(task.id)

    # File des tâches sans prérequis, dans l'ordre du plan (stabilité).
    ready = [task.id for task in tasks if indegree[task.id] == 0]
    ordered: list[Task] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)

    if len(ordered) != len(tasks):  # pragma: no cover - garanti acyclique en amont
        raise TaskValidationError(
            "Cycle de dépendances : ordonnancement topologique impossible."
        )
    return ordered


@dataclass(frozen=True)
class Brief:
    """Forme Python immuable d'un brief structuré, miroir de `brief.schema.json` (#318).

    Ce que le Chef de projet rend **avant** de décomposer : l'intention reformulée,
    ce qui est dedans, ce qui est dehors, sous quelles contraintes, à quoi on saura
    que c'est fait, ce qu'il a tranché seul et ce qu'il refuse de trancher seul.

    Les champs multivalués sont des tuples (immuables) ; `to_dict` les réémet en
    listes pour un aller-retour JSON fidèle au schéma. Contrairement à `Task`,
    aucune clé n'est **omise** : les quatre listes facultatives sont valides vides,
    donc un brief se réémet toujours entier — c'est ce qui permet à l'écran de
    validation (#322) de présenter les sept sections sans avoir à distinguer
    « absent » de « vide ».

    Les champs requis viennent en tête (donc sans valeur par défaut) : l'ordre du
    dataclass suit la contrainte Python, la **synthèse** suit l'ordre de lecture du
    schéma.
    """

    objectif: str
    perimetre: tuple[str, ...]
    hors_perimetre: tuple[str, ...]
    criteres_acceptation: tuple[str, ...]
    contraintes: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Brief:
        """Construit un `Brief` depuis un dict **déjà validé** contre le schéma."""
        return cls(
            objectif=data["objectif"],
            perimetre=tuple(data["perimetre"]),
            hors_perimetre=tuple(data["hors_perimetre"]),
            criteres_acceptation=tuple(data["criteres_acceptation"]),
            contraintes=tuple(data.get("contraintes", ())),
            hypotheses=tuple(data.get("hypotheses", ())),
            questions=tuple(data.get("questions", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet le brief en dict JSON-sérialisable, conforme au schéma."""
        return {
            "objectif": self.objectif,
            "perimetre": list(self.perimetre),
            "hors_perimetre": list(self.hors_perimetre),
            "contraintes": list(self.contraintes),
            "criteres_acceptation": list(self.criteres_acceptation),
            "hypotheses": list(self.hypotheses),
            "questions": list(self.questions),
        }

    @property
    def a_des_questions(self) -> bool:
        """Le brief laisse-t-il des zones d'ombre à lever avant de décomposer (#321) ?"""
        return bool(self.questions)

    def synthese(self) -> str:
        """Rend le brief en Markdown — les sept sections, dans l'ordre du schéma.

        Rendue **ici** et non par l'appelant, pour la raison qui a fait rendre son
        rapport de lecture par l'extraction (#316) : la CLI, l'API et l'écran de
        validation rendraient sinon chacun à sa façon un même constat, et c'est le
        texte que l'humain relit pour approuver. Les sections vides sont
        **affichées** avec la mention « — » plutôt que masquées : un brief sans
        hors-périmètre est une information, pas une section manquante.
        """
        lignes = ["# Brief", "", self.objectif, ""]
        for titre, entrees in (
            ("Périmètre", self.perimetre),
            ("Hors périmètre", self.hors_perimetre),
            ("Contraintes", self.contraintes),
            ("Critères d'acceptation", self.criteres_acceptation),
            ("Hypothèses", self.hypotheses),
            ("Questions", self.questions),
        ):
            lignes.append(f"## {titre}")
            lignes.append("")
            lignes.extend(f"- {entree}" for entree in entrees or ("—",))
            lignes.append("")
        return "\n".join(lignes).rstrip() + "\n"


def validate_brief(data: Mapping[str, Any], *, where: str = "brief") -> None:
    """Valide un dict de brief contre la JSON Schema partagée (#318).

    Lève `BriefValidationError` avec un message agrégé (tous les manquements) si le
    dict n'est pas conforme. Même régime que `validate_task` — un seul message qui
    dit tout ce qui manque, plutôt qu'un aller-retour par manquement.
    """
    errors = sorted(_brief_validator().iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        details = "; ".join(_format_error(e) for e in errors)
        raise BriefValidationError(f"{where} invalide : {details}.")


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
