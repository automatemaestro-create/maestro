"""Orchestrator schemas and validation (#3, #318).

Single source of truth: the shared JSON Schemas of `packages/shared/schemas/` (see its
README). This module loads them, derives `jsonschema` validators from them, and exposes the
two contracts the orchestrator produces.

The **plan** (#3), what it returns once the intent is settled:

- `Task`: the immutable Python form of a task, with `from_dict`/`to_dict`;
- `validate_task`: validates a raw dict against the shared schema;
- `validate_plan`: validates a list of tasks **and** the cross-task rules the schema alone
  cannot express (unique ids, resolvable dependencies, acyclic graph), and returns
  ready-to-use `Task` objects.

The **brief** (#318), what it returns *before*, so that a human settles the intent rather
than leaving it guessed:

- `Brief`: the immutable Python form of a structured brief, with its Markdown `summary`;
- `validate_brief`: validates a raw dict against the shared schema.

Both live **here** rather than in two twin modules: the plumbing is the same (file
loading, cached `Draft202012Validator`, formatting of violations), and duplicating it would
let the error messages of two contracts produced by the same agent drift apart.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from maestro.appartenance import projet_id_valide
from maestro.orchestrator.errors import BriefValidationError, TaskValidationError
from maestro.references import ReferenceTicket

#: Folder of the shared JSON Schemas, relative to the repository root.
#: `schema.py` → `orchestrator` → `maestro` → root; then `packages/shared/...`.
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "packages" / "shared" / "schemas"

#: Location of the shared **task** JSON Schema (#3).
SCHEMA_PATH = SCHEMAS_DIR / "task.schema.json"

#: Location of the shared **brief** JSON Schema (#318).
BRIEF_SCHEMA_PATH = SCHEMAS_DIR / "brief.schema.json"


def _load_schema(path: Path, *, what: str, error: type[Exception]) -> dict[str, Any]:
    """Loads a shared JSON Schema, or raises `error` naming the missing file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - depends on the repository layout
        raise error(
            f"Schéma de {what} introuvable à {path}. Le dépôt est-il complet ?"
        ) from exc
    schema: dict[str, Any] = json.loads(raw)
    return schema


@lru_cache(maxsize=1)
def load_task_schema() -> dict[str, Any]:
    """Loads (and caches) the shared task JSON Schema."""
    return _load_schema(SCHEMA_PATH, what="tâche", error=TaskValidationError)


@lru_cache(maxsize=1)
def load_brief_schema() -> dict[str, Any]:
    """Loads (and caches) the shared brief JSON Schema (#318)."""
    return _load_schema(BRIEF_SCHEMA_PATH, what="brief", error=BriefValidationError)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = load_task_schema()
    # Surfaces a malformed schema early and clearly rather than at first use.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@lru_cache(maxsize=1)
def _brief_validator() -> Draft202012Validator:
    schema = load_brief_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class Task:
    """Immutable Python form of a task, mirroring `task.schema.json`.

    Multi-valued fields are tuples (immutable); `to_dict` emits them back as lists, for a
    JSON round trip faithful to the schema.

    `ticket` (#187) carries the ticket the task belongs to. It is None in the common case,
    since the plan does not produce one by itself: it is set when a run starts (the run
    starts from a ticket) or by an agent during execution. `to_dict` then **omits** the key
    rather than emitting `null`: the schema rejects unknown properties as well as malformed
    objects, and a plan without a reference must stay serializable as is.

    `project_id` (#222) carries the **project** the task belongs to, under the same regime
    as `ticket`: None in the common case, set when a run starts and inherited by each task,
    and the key is **omitted** from `to_dict` when there is none.

    `steps` (#489) carries the **skeleton** of the checklist: the labels of the planned
    steps, in order, without any state. The plan says *what there is to do*, never *where
    things stand*: progress is checked off during execution and does not exist when the
    plan is written. Empty in the common case (the orchestrator may declare none), and the
    key is then **omitted** from `to_dict`, as for `ticket` and `project_id`.

    `granted_act` (#1198) carries the **act the goal itself names** and that this task
    executes, written as it was granted ("supprimer tout le contenu du dossier du projet").
    It carries the consent through to execution, where the execution tool classified
    `ask`/`humain` would otherwise ask for it again **at every command** (#1149). Empty in
    the common case, since a task that *builds* executes no act granted in advance, and the
    key is then **omitted** from `to_dict`, like the three above.

    What this field does **not** do, to read before widening it: it only covers the
    **execution tool** (`maestro.engine.executor`), not everything a policy classifies
    `ask` (posting in Slack is not covered by "empty the folder"); it lifts neither the
    `deny` list, nor the write boundary, nor the project's excluded perimeter; and it does
    not exempt from the trace: every call that passes on this consent leaves its line in
    the journal and in the chat, with the granted act in it.

    `resumes_from` (#1396) names the task whose work this one **resumes**: on a versioned
    project, its branch is born from that task's branch rather than from the base
    (`maestro.sandbox.projet`). The **engine** sets it, on the leading tasks of a re-split
    (`resplit_tasks`), never a plan: whatever the model writes there is removed at
    validation (`validate_plan`). Empty in the common case, and the key is then omitted
    from `to_dict`. It travels with the task to the queue worker, which revalidates it
    against the shared schema.
    """

    id: str
    title: str
    description: str
    required_competencies: tuple[str, ...]
    output_format: str
    dependencies: tuple[str, ...] = ()
    ticket: ReferenceTicket | None = None
    project_id: str | None = None
    steps: tuple[str, ...] = ()
    granted_act: str = ""
    resumes_from: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Task:
        """Builds a `Task` from a dict **already validated** against the schema."""
        return cls(
            id=data["id"],
            title=data["titre"],
            description=data["description"],
            required_competencies=tuple(data["competences_requises"]),
            output_format=data["format_sortie"],
            dependencies=tuple(data.get("dependances", ())),
            ticket=ReferenceTicket.depuis(data.get("ticket")),
            project_id=projet_id_valide(data.get("projet_id")),
            steps=tuple(data.get("etapes", ())),
            granted_act=str(data.get("acte_accorde", "") or "").strip(),
            resumes_from=str(data.get("reprend", "") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Emits the task back as a JSON-serializable dict that conforms to the schema."""
        data: dict[str, Any] = {
            "id": self.id,
            "titre": self.title,
            "description": self.description,
            "competences_requises": list(self.required_competencies),
            "format_sortie": self.output_format,
            "dependances": list(self.dependencies),
        }
        if self.ticket is not None:
            data["ticket"] = self.ticket.to_dict()
        if self.project_id is not None:
            data["projet_id"] = self.project_id
        if self.steps:
            data["etapes"] = list(self.steps)
        if self.granted_act:
            data["acte_accorde"] = self.granted_act
        if self.resumes_from:
            data["reprend"] = self.resumes_from
        return data


def validate_task(data: Mapping[str, Any], *, where: str = "tâche") -> None:
    """Validates a task dict against the shared JSON Schema.

    Raises `TaskValidationError` with an aggregated message (every violation) when the dict
    does not conform. `where` locates the task in a plan message.
    """
    # Sort key as str: a path mixes field names (str) and indices (int), whose direct
    # comparison would raise TypeError.
    errors = sorted(_validator().iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        details = "; ".join(_format_error(e) for e in errors)
        raise TaskValidationError(f"{where} invalide : {details}.")


def validate_plan(tasks: Sequence[Mapping[str, Any]]) -> list[Task]:
    """Validates a complete plan and returns the corresponding `Task` objects.

    Beyond the per-task schema, guarantees the cross-task invariants: non-empty plan,
    unique `id`s, every dependency resolvable within the plan, no self-dependency, and an
    **acyclic** graph. Raises `TaskValidationError` at the first broken invariant.

    A plan is what the model writes, the plan of a run as well as the tasks of a recovery:
    `resumes_from`, which only the engine sets (#1396), is removed from it. A planned task
    carrying it would start from another task's branch.
    """
    if not tasks:
        raise TaskValidationError("Plan vide : l'orchestrateur doit produire au moins une tâche.")

    parsed: list[Task] = []
    by_id: dict[str, Task] = {}
    for index, raw in enumerate(tasks):
        validate_task(raw, where=f"tâche #{index + 1}")
        task = replace(Task.from_dict(raw), resumes_from="")
        if task.id in by_id:
            raise TaskValidationError(f"Identifiant de tâche dupliqué : {task.id!r}.")
        by_id[task.id] = task
        parsed.append(task)

    for task in parsed:
        for dep in task.dependencies:
            if dep == task.id:
                raise TaskValidationError(f"La tâche {task.id!r} dépend d'elle-même.")
            if dep not in by_id:
                raise TaskValidationError(
                    f"La tâche {task.id!r} dépend de {dep!r}, absente du plan."
                )

    _ensure_acyclic(parsed)
    return parsed


def topological_order(tasks: Sequence[Task]) -> list[Task]:
    """Orders the tasks so that each one follows its dependencies (Kahn's sort).

    Assumes a plan **already validated** (`validate_plan`): resolvable dependencies and an
    acyclic graph. At equal dependency rank, keeps the plan order (stable sort) for a
    deterministic result, which the orchestration engine relies on since it executes the
    tasks in this order. Raises `TaskValidationError` when a cycle makes the ordering
    impossible (which cannot happen after `validate_plan`).
    """
    by_id = {task.id: task for task in tasks}
    indegree = {task.id: len(task.dependencies) for task in tasks}
    dependents: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dep in task.dependencies:
            dependents[dep].append(task.id)

    # Queue of the tasks without prerequisites, in plan order (stability).
    ready = [task.id for task in tasks if indegree[task.id] == 0]
    ordered: list[Task] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)

    if len(ordered) != len(tasks):  # pragma: no cover - guaranteed acyclic upstream
        raise TaskValidationError(
            "Cycle de dépendances : ordonnancement topologique impossible."
        )
    return ordered


@dataclass(frozen=True)
class Brief:
    """Immutable Python form of a structured brief, mirroring `brief.schema.json` (#318).

    What the orchestrator returns **before** decomposing: the restated intent, what is in,
    what is out, under which constraints, how we will know it is done, what it settled
    alone and what it refuses to settle alone.

    Multi-valued fields are tuples (immutable); `to_dict` emits them back as lists, for a
    JSON round trip faithful to the schema. Unlike `Task`, no key is **omitted**: the four
    optional lists are valid when empty, so a brief is always emitted whole, which lets the
    approval screen (#322) present the seven sections without telling "absent" from
    "empty".

    Required fields come first (hence without a default): the dataclass order follows the
    Python constraint, the **summary** follows the reading order of the schema.
    """

    goal: str
    in_scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Brief:
        """Builds a `Brief` from a dict **already validated** against the schema."""
        return cls(
            goal=data["objectif"],
            in_scope=tuple(data["perimetre"]),
            out_of_scope=tuple(data["hors_perimetre"]),
            acceptance_criteria=tuple(data["criteres_acceptation"]),
            constraints=tuple(data.get("contraintes", ())),
            assumptions=tuple(data.get("hypotheses", ())),
            questions=tuple(data.get("questions", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        """Emits the brief back as a JSON-serializable dict that conforms to the schema."""
        return {
            "objectif": self.goal,
            "perimetre": list(self.in_scope),
            "hors_perimetre": list(self.out_of_scope),
            "contraintes": list(self.constraints),
            "criteres_acceptation": list(self.acceptance_criteria),
            "hypotheses": list(self.assumptions),
            "questions": list(self.questions),
        }

    @property
    def has_questions(self) -> bool:
        """Does the brief leave grey areas to clear before decomposing (#321)?"""
        return bool(self.questions)

    def questions_as_assumptions(self, reason: str) -> Brief:
        """Records the unanswered questions as **explicit assumptions** (#321).

        What becomes of the grey areas once the cap on clarification rounds is reached:
        rather than asking the same questions forever, the brief goes to approval
        **assuming in writing** what it could not clear. `reason` prefixes each entry and
        says where it comes from ("Sans réponse après 2 tour(s) de clarification"): without
        it, a question turned assumption would be indistinguishable from an assumption the
        orchestrator really settled, and the human who approves would lose the only
        information that matters here: nobody answered.

        The transformation is **deterministic and done in Python** on purpose: it is what
        guarantees the cap. Asking the model for it would make the bound depend on its
        compliance, and a round where it asks its questions again would restart the loop.
        The prompt still asks for it (the model writes better assumptions than this
        mechanical prefix); this is the net that catches the case where it did not.

        Returns `self` unchanged when there is nothing to convert: a brief without questions
        already has the expected shape.
        """
        if not self.questions:
            return self
        return replace(
            self,
            assumptions=self.assumptions + tuple(f"{reason} : {q}" for q in self.questions),
            questions=(),
        )

    def summary(self) -> str:
        """Renders the brief in Markdown: the seven sections, in schema order.

        Rendered **here** rather than by the caller, for the reason the extraction renders
        its own reading report (#316): the CLI, the API and the approval screen would
        otherwise each render the same finding their own way, and this is the text the
        human rereads to approve. Empty sections are **shown** with "—" rather than hidden:
        a brief without out-of-scope items is information, not a missing section.
        """
        lines = ["# Brief", "", self.goal, ""]
        for title, entries in (
            ("Périmètre", self.in_scope),
            ("Hors périmètre", self.out_of_scope),
            ("Contraintes", self.constraints),
            ("Critères d'acceptation", self.acceptance_criteria),
            ("Hypothèses", self.assumptions),
            ("Questions", self.questions),
        ):
            lines.append(f"## {title}")
            lines.append("")
            lines.extend(f"- {entry}" for entry in entries or ("—",))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def frame(self) -> str:
        """The frame every task of the run receives next to its description (#1402).

        Not the whole summary: what a task must know about the run it contributes to, that
        is what the run is for (the goal), what it does not do (out of scope), under which
        constraints, and how we will know it is done (its acceptance criteria). The plan
        has already split the scope into tasks, and assumptions and questions are the
        material of approval, not of the work.

        Rendered **here**, for the reason given in `summary`: the agent doing the task and
        the verifier judging its delivery read the same frame, word for word. An empty
        out-of-scope shows "—", since that is information; absent constraints are not
        shown, as most briefs have none.
        """
        lines = [f"Objectif : {self.goal}", "", "Hors périmètre :"]
        lines.extend(f"- {entry}" for entry in self.out_of_scope or ("—",))
        if self.constraints:
            lines += ["", "Contraintes :"]
            lines.extend(f"- {entry}" for entry in self.constraints)
        lines += ["", "Critères d'acceptation du run :"]
        lines.extend(f"- {entry}" for entry in self.acceptance_criteria or ("—",))
        return "\n".join(lines)


@dataclass(frozen=True)
class Clarification:
    """A question of the brief and the human answer given to it (#321).

    The material of a clarification round: what the orchestrator refused to settle alone,
    and what the human answered. It lives **here**, next to `Brief`, rather than in the
    engine contract that drives the rounds: it is a value of the brief domain, which the
    regeneration prompt reads and the loop only carries. The reverse would force
    `maestro.orchestrator` to import `maestro.engine`, against the package's dependency
    direction.

    No question identifier, and that is a **decision** (#318, schema note): the brief is
    regenerated **whole** at each round, so a question has no stable identity from one
    version to the next. Pairing is done by **position** against the run's stored brief,
    whose question list is frozen between its publication and its answer; an identifier
    would suggest a permanence that does not exist.

    An empty `answer` is a nominal case, not a missing entry: "I don't know" is an answer,
    and the brief must be able to derive an assumption from it rather than ask the question
    again at the next round.
    """

    question: str
    answer: str

    @property
    def unanswered(self) -> bool:
        """Was the question left unanswered (empty or blank)?"""
        return not self.answer.strip()


def validate_brief(data: Mapping[str, Any], *, where: str = "brief") -> None:
    """Validates a brief dict against the shared JSON Schema (#318).

    Raises `BriefValidationError` with an aggregated message (every violation) when the
    dict does not conform. Same regime as `validate_task`: a single message that states
    everything missing, rather than one round trip per violation.
    """
    errors = sorted(_brief_validator().iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        details = "; ".join(_format_error(e) for e in errors)
        raise BriefValidationError(f"{where} invalide : {details}.")


def _ensure_acyclic(tasks: Sequence[Task]) -> None:
    """Detects a cycle in the dependency graph (three-colour DFS)."""
    deps = {task.id: task.dependencies for task in tasks}
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
    """Renders a jsonschema `ValidationError` readably, located by its path."""
    location = "".join(f"[{part!r}]" for part in error.path)
    prefix = f"champ {location} : " if location else ""
    return f"{prefix}{error.message}"
