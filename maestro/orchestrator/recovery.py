"""Recovery of a failed task: what the orchestrator reads, and what it returns (#1178).

A failed task is judged before it is retried. The orchestrator reads the cause, says what
happened, and says what changes before the next attempt: a blind retry (`maestro.engine.retry`)
would repeat an access denied, an unknown model or a context that is too long identically, then
stop the downstream chain.

This module holds the **contract** of that judgement. It lives here, next to `Brief` and
`Clarification`, for the reason that put them here: they are values of the orchestrator's domain,
which its prompt reads and the loop only carries. The reverse would make `maestro.orchestrator`
import `maestro.engine`, against the dependency direction of the package.

## Three natures, a few actions — and why they are not catalogues

The **nature** is the judgement: *transient* (`passager`), *configuration* or *approach*
(`approche`). It does not classify the error in the model's place — the model reads the cause,
never a pattern on its text (#1315) — and only one of its values drives code: **only a transient
failure is replayed identically**. The other two say why something must change.

The **actions** are the verbs Maestro knows how to execute, not a list of causes: replay, retry
differently (one reworked task, or a resplit into several), ask the user, abandon on their
answer, and propose the missing prerequisite (below). What varies without bound — the new
approach, the competencies that route to another agent, the resplit, the adjustment of the
waiting tasks — is written **inside** the action, as tasks in the plan's format, and the model
writes it.

## What execution checks in what the model writes

`validate_recovery` holds the rules the model could forget, in Python, so whatever it answered —
the same guarantee `Brief.questions_as_assumptions` gives the clarification cap:

- a failure judged other than transient **is not replayed** identically — except after an answer
  from the user, which may have changed the environment (#1181);
- a new attempt **changes something** compared with those that already failed — the approach
  (description), the competencies (which agent takes it), or the split;
- it adds **no granted act** the original task did not carry: a recovery never bypasses an
  approval (#1198);
- it **abandons** only on an answer from the user: what Maestro cannot lift, it asks; it never
  bars it silently.

## Proposing what is missing (#1181)

An agent that is stuck **says so** (`signaler_blocage`, #719) — "I lack the Stripe token",
"nobody here can do the design" — and that reason reaches the orchestrator with the failure
(`Attempt.blockers`). The orchestrator can draw from it a **prerequisite** to propose: a secret, a
server, a tool or a role (`maestro.prerequis`). It is not an open question — `ACTION_ASK` covers
that —, it is a named thing, with the way to provide it, that the person accepts in one action;
the task then resumes as is, without restarting the run.

An answer from the user **changes the environment**: "done, the token is in the vault" makes it
legitimate to replay identically a failure that was not transient. Replaying is therefore
reserved for a transient failure — **or** for a task the user has just answered about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from maestro.orchestrator.errors import RecoveryValidationError, TaskValidationError
from maestro.orchestrator.schema import Task, validate_plan, validate_task
from maestro.prerequis import GENRE_ROLE, GENRES, PrerequisManquant

#: The failure will not recur: a hazard (outage, process dead with no cause in the task, rate
#: limit). The only case where replaying identically makes sense.
NATURE_TRANSIENT = "passager"
#: What is missing lies in the environment: an access, a secret, a model, a tool, a context
#: limit. Replaying would change nothing.
NATURE_CONFIGURATION = "configuration"
#: The way the task is taken fails: too big, wrong assumption, wrong tool, wrong competencies.
#: The task itself must be taken differently.
NATURE_APPROACH = "approche"
NATURES = (NATURE_TRANSIENT, NATURE_CONFIGURATION, NATURE_APPROACH)

#: Replay the task as is — reserved for a transient failure.
ACTION_REPLAY = "rejouer"
#: Retry differently: one reworked task (another approach, other competencies) or several (a
#: resplit), which replace the one that failed.
ACTION_RETRY = "retenter"
#: Ask the user what Maestro cannot lift on its own.
ACTION_ASK = "demander"
#: Leave the task failed — only on the user's answer.
ACTION_ABANDON = "abandonner"
#: Propose to the user the missing prerequisite (#1181) — a secret, a server, a tool, a role —,
#: then resume the task as is.
ACTION_PROPOSE = "proposer"
ACTIONS = (ACTION_REPLAY, ACTION_RETRY, ACTION_ASK, ACTION_ABANDON, ACTION_PROPOSE)


@dataclass(frozen=True)
class Attempt:
    """One attempt at the task — or at what replaced it — and its failure.

    `tasks` is what was executed: the plan's task on the first attempt, then its reworked version
    or the tasks of the resplit. It is what lets execution judge that a proposal **changes
    something** compared with what already failed. `action` says in a few words how the attempt
    was taken ("telle que planifiée", "rejouée à l'identique"…), for the prompt and for the
    question asked in the chat; `diagnosis` is what the model said about it.

    `blockers` (#1181) carries what the agent **reported** during the attempt — what it lacked, in
    its own words. It is often the real cause, which the error does not state: a blocked agent
    returns an empty deliverable.

    `verification_outage` (#1396) says the agent **delivered** and the verifier could not judge
    (`TaskResult.verification_en_panne`, the typed outage of #1388). It is a field, not a phrase
    of the error: read as text, a "Prompt is too long" from the judge points to a task too big,
    hence to a resplit that redoes from scratch work that succeeded.
    """

    tasks: tuple[Task, ...]
    agent: str
    role: str
    error: str
    action: str = "telle que planifiée"
    diagnosis: str = ""
    blockers: tuple[str, ...] = ()
    verification_outage: bool = False


@dataclass(frozen=True)
class TaskFailure:
    """What the orchestrator reads to judge a failure (#1178).

    `task` is the **plan's** task, as planned — the downstream tasks wait for it, whatever runs
    in its place. `attempts` is the history, in order: the latest is the one that just failed.

    `downstream` holds the tasks that wait for this one (directly or not): the model may adjust
    their description when what it changes upstream changes what they will receive. `goal` is
    what the user asked of the run — a task is not judged without knowing what it is for.

    `question`/`answer` carry the last exchange with the user, when they were asked: their answer
    **is authoritative**, above any diagnosis.
    """

    task: Task
    attempts: tuple[Attempt, ...]
    goal: str = ""
    downstream: tuple[Task, ...] = ()
    question: str = ""
    answer: str | None = None

    @property
    def latest(self) -> Attempt:
        """The attempt that just failed."""
        return self.attempts[-1]


@dataclass(frozen=True)
class Recovery:
    """The orchestrator's judgement on a failure, and what it does next (#1178).

    `nature` and `diagnosis` are the **judgement**: what happened, read from the cause. `action`
    is the **decision**, with its own fields:

    - `tasks` for `ACTION_RETRY` — one: the task reworked differently; several: the resplit,
      whose deliverables together replace the missing one;
    - `adjustments` for `ACTION_RETRY` too: the downstream tasks whose description changes,
      because what they will receive has changed;
    - `question` for `ACTION_ASK` — what is expected from the user;
    - `prerequisite` for `ACTION_PROPOSE` (#1181) — what is missing, and how to provide it.
    """

    nature: str
    diagnosis: str
    action: str
    tasks: tuple[Task, ...] = ()
    adjustments: tuple[tuple[str, str], ...] = ()
    question: str = ""
    prerequisite: PrerequisManquant | None = None

    @property
    def replays(self) -> bool:
        """Is the verdict to replay the task as is (transient failure)?"""
        return self.action == ACTION_REPLAY

    @property
    def reruns(self) -> bool:
        """Does the verdict start a new execution (replay or retry)?"""
        return self.action in (ACTION_REPLAY, ACTION_RETRY)


def validate_recovery(data: Mapping[str, Any], failure: TaskFailure) -> Recovery:
    """Validate the model's raw answer against what execution requires (#1178).

    Raises `RecoveryValidationError` with a message that says **what was refused**: it is
    recorded in the journal, and it explains why what follows is a question rather than a new
    attempt.
    """
    if not isinstance(data, Mapping):
        raise RecoveryValidationError("le rattrapage n'est pas un objet JSON.")
    nature = _text(data, "nature")
    if nature not in NATURES:
        raise RecoveryValidationError(
            f"nature inconnue {nature!r} (attendues : {', '.join(NATURES)})."
        )
    action = _text(data, "geste")
    if action not in ACTIONS:
        raise RecoveryValidationError(
            f"geste inconnu {action!r} (attendus : {', '.join(ACTIONS)})."
        )
    diagnosis = _text(data, "diagnostic")
    if not diagnosis:
        raise RecoveryValidationError("le diagnostic est vide.")
    if action == ACTION_REPLAY and nature != NATURE_TRANSIENT and failure.answer is None:
        # An answer from the user may have changed the environment (#1181): an access granted,
        # a secret provided. Without one, nothing has moved.
        raise RecoveryValidationError(
            f"un échec jugé « {nature} » ne se rejoue pas à l'identique : "
            "il faut changer quelque chose, ou demander."
        )
    if action == ACTION_ABANDON and failure.answer is None:
        raise RecoveryValidationError(
            "abandonner n'appartient qu'à l'utilisateur : ce que Maestro ne sait pas "
            "lever se demande, il ne se barre pas en silence."
        )
    question = _text(data, "question")
    if action == ACTION_ASK and not question:
        raise RecoveryValidationError("demander sans question : rien à poser.")
    tasks: tuple[Task, ...] = ()
    adjustments: tuple[tuple[str, str], ...] = ()
    if action == ACTION_RETRY:
        tasks = _recovery_tasks(data.get("taches"), failure)
        adjustments = _adjustments(data.get("aval"), failure)
    prerequisite = _prerequisite(data.get("prerequis")) if action == ACTION_PROPOSE else None
    return Recovery(
        nature=nature,
        diagnosis=diagnosis,
        action=action,
        tasks=tasks,
        adjustments=adjustments,
        question=question if action == ACTION_ASK else "",
        prerequisite=prerequisite,
    )


def _prerequisite(raw: Any) -> PrerequisManquant:
    """The prerequisite the orchestrator proposes, validated (#1181).

    It must **name** what is missing (`objet`), say why (`raison`), and be of one of the kinds
    Maestro knows how to propose (`maestro.prerequis.GENRES`) — a remedy that cannot be shown in
    the chat is not a prerequisite, it is a question. A role also names the competencies it
    would cover: the team recruits it by them, never by its label.
    """
    if not isinstance(raw, Mapping):
        raise RecoveryValidationError("proposer sans « prerequis » : rien à proposer.")
    kind = _text(raw, "genre")
    if kind not in GENRES:
        raise RecoveryValidationError(
            f"genre de prérequis inconnu {kind!r} (attendus : {', '.join(GENRES)})."
        )
    subject = _text(raw, "objet")
    reason = _text(raw, "raison")
    if not subject or not reason:
        raise RecoveryValidationError(
            "un prérequis nomme ce qui manque (« objet ») et pourquoi (« raison »)."
        )
    competencies: tuple[str, ...] = ()
    if kind == GENRE_ROLE:
        raw_competencies = raw.get("competences")
        competencies = tuple(
            c.strip() for c in raw_competencies if isinstance(c, str) and c.strip()
        ) if isinstance(raw_competencies, list) else ()
        if not competencies:
            raise RecoveryValidationError(
                "un rôle à recruter nomme les compétences qu'il couvrirait (« competences »)."
            )
    return PrerequisManquant(
        genre=kind,
        objet=subject,
        raison=reason,
        procedure=_text(raw, "procedure"),
        competences=competencies,
    )


def _text(data: Mapping[str, Any], key: str) -> str:
    """The text value of `key`, stripped — empty when absent or of another type."""
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _recovery_tasks(raw: Any, failure: TaskFailure) -> tuple[Task, ...]:
    """The tasks that replace the failed one, validated as a plan.

    Same contract as the plan (`task.schema.json`, unique ids, dependencies resolvable **among
    themselves**, acyclic graph): `validate_plan` already holds it, and a second rule written
    here would end up saying something else. The tasks depend only on one another — what the
    original task received from its own dependencies, they all receive.
    """
    if not isinstance(raw, list) or not raw:
        raise RecoveryValidationError(
            "retenter sans tâche : dis ce qu'on exécute à la place."
        )
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise RecoveryValidationError(
                f"la tâche de rattrapage #{index + 1} n'est pas un objet JSON."
            )
    try:
        for index, entry in enumerate(raw):
            validate_task(entry, where=f"tâche de rattrapage #{index + 1}")
        tasks = validate_plan(raw)
    except TaskValidationError as exc:
        raise RecoveryValidationError(str(exc)) from exc
    granted = failure.task.granted_act
    for task in tasks:
        if task.granted_act and task.granted_act != granted:
            raise RecoveryValidationError(
                f"la tâche {task.id!r} porte un acte accordé que la tâche d'origine "
                "n'avait pas : un rattrapage ne contourne jamais un arbitrage."
            )
    proposed = _fingerprint(tasks)
    for attempt in failure.attempts:
        if _fingerprint(attempt.tasks) == proposed:
            raise RecoveryValidationError(
                "la proposition est identique à une tentative qui a déjà échoué : "
                "mêmes descriptions, mêmes compétences — rien n'y a changé."
            )
    return tuple(tasks)


def _fingerprint(tasks: Sequence[Task]) -> frozenset[tuple[str, frozenset[str]]]:
    """What makes an attempt "the same": its approaches and its competencies.

    The description carries the approach, the competencies designate the agent that will take
    it. The title and the id do not count: rewriting them changes nothing in what will be
    executed — nor does the order of a resplit's tasks, hence the set.
    """
    return frozenset(
        (" ".join(task.description.split()), frozenset(task.required_competencies))
        for task in tasks
    )


def _adjustments(raw: Any, failure: TaskFailure) -> tuple[tuple[str, str], ...]:
    """The downstream tasks whose description changes — `(id, description)`.

    A downstream task has not started yet (it waits for the failed one): giving it a description
    that accounts for what changed upstream is safe, and keeps it from consuming a deliverable it
    will no longer receive. An id that is not downstream is refused: adjusting a task already
    started, or unrelated to the failure, would rewrite history.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RecoveryValidationError("« aval » doit être un tableau.")
    known = {task.id for task in failure.downstream}
    adjustments: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise RecoveryValidationError("un ajustement d'aval n'est pas un objet JSON.")
        task_id = _text(entry, "id")
        description = _text(entry, "description")
        if task_id not in known:
            raise RecoveryValidationError(
                f"ajustement de {task_id!r}, qui n'attend pas la tâche en échec."
            )
        if not description:
            raise RecoveryValidationError(f"ajustement de {task_id!r} sans description.")
        adjustments.append((task_id, description))
    return tuple(adjustments)


def reworked_task(task: Task, rework: Task) -> Task:
    """The plan's task, reworked differently — **under its own id**.

    A rework with a single task is the same task taken differently: it keeps its id, title,
    ticket, project and dependencies, so its card goes back to "in progress" then "done" instead
    of another card appearing next to it. What changes is what the model changed: the approach
    (`description`), the competencies (`required_competencies`), the expected deliverable, and
    the checklist skeleton that goes with it.
    """
    return replace(
        task,
        description=rework.description,
        required_competencies=rework.required_competencies,
        output_format=rework.output_format,
        steps=rework.steps,
    )


def resplit_tasks(task: Task, tasks: Sequence[Task], round_number: int) -> tuple[Task, ...]:
    """The tasks of a resplit, filed under the plan's task they replace.

    The ids are prefixed (`<task>-r<round>-<id>`): those the model chooses are local to the
    recovery, and two rounds could reuse them — or collide with the plan's. The ticket and the
    project are inherited, as when a run starts.

    The **leading** tasks — those that wait for no other task of the resplit — **resume** the
    work of the replaced task (`Task.resumes_from`, #1396): on a versioned project, their branch
    starts from its branch, and what is done is not redone. The others start from the project's
    base, where the merge of the tasks they wait for already carries that work.
    """
    prefix = f"{task.id}-r{round_number}-"
    return tuple(
        replace(
            subtask,
            id=f"{prefix}{subtask.id}",
            dependencies=tuple(f"{prefix}{dep}" for dep in subtask.dependencies),
            ticket=task.ticket,
            project_id=task.project_id,
            resumes_from="" if subtask.dependencies else task.id,
        )
        for subtask in tasks
    )
