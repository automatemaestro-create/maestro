"""The role a team would lack to take a task (#1041), or a plan (#1227).

**An agent does not recruit during a run**:
[docs/31 §3.5](../../docs/31-decision-surface-ecriture-agents.md) says so, for good. The one who
proposes is the orchestrator, to the person, before executing (#1227). The last two bullets of
[docs/37 §3](../../docs/37-decision-equipe-sur-mesure.md) — "recruiting stays a validated gesture
outside the run", "the team is not formed inside a run" — no longer hold; nothing is recruited
without consent (#1040), and that is the only promise that counts.

This module is the "and what exactly is missing?" half of that signal. It reads nothing, writes
nothing and creates no one: it **names**, from two things the caller already holds — the
competencies the work requires, and the team's definitions. It does so at two moments, which do
not merge:

- `uncovered_competencies` / `missing_role` — **one task**, at routing time
  (`maestro.engine.executor`), where we know nobody took it;
- `plan_gap` (#1227) — **the whole plan**, right after decomposition (`maestro.engine.loop`),
  before anything is executed. That is the useful moment: a role recruited there takes its
  tasks, a role recruited at routing arrives after the task has left as "to assign".

**Name a role, not a list of tags.** A signal saying "nobody covers `sql`, `migration`" asks
whoever reads it to translate it into a position to fill themselves. The role is therefore looked
up in the **role templates** (`maestro.team.role_templates`), which are exactly what Maestro knows
how to propose to recruit: the signal then names the thing that can actually be done. When no
role template covers the gap — a tag the catalogue does not know —, the competencies are returned
as they are rather than inventing a role: *a made-up role would read as an existing role*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from maestro.agents.catalog import Agent
from maestro.team.role_templates import ROLE_TEMPLATES


@dataclass(frozen=True)
class MissingRole:
    """What no role of the team can take, and the position that would answer it.

    `competencies` holds the required competencies **that no definition of the team has** — the
    raw fact, the one routing observed. `role` is the label of the missing position, and
    `role_template` the name of the role template to recruit when there is one (`None`: no role
    template covers this gap, and `role` then only restates the competencies — see the module
    docstring).

    `covers` (#1227) holds **what this position covers** among the missing competencies. It is
    not redundant with `competencies`: the retained role template is the one that covers the
    most of them, not necessarily all — and proposing a role while letting it be believed that it
    takes everything missing would be a recruitment sold for more than it is. Empty when no role
    template covers the gap: there is then no position, so nothing that covers anything.
    """

    competencies: tuple[str, ...]
    role: str
    role_template: str | None = None
    covers: tuple[str, ...] = ()

    def sentence(self) -> str:
        """The signal in one sentence — what the chat line displays as is.

        It says the two things needed to act: what is missing, and the position it designates.
        It does not say "recruiting happens outside the run" (#1227): that is false, since the
        orchestrator proposes completing the team before executing, and a sentence pointing
        elsewhere would have the reader look for a screen while an action waits in the chat.
        """
        missing = ", ".join(self.competencies)
        position = (
            f"il manque un rôle « {self.role} » (gabarit `{self.role_template}`)"
            if self.role_template is not None
            else f"il manque un rôle couvrant {self.role}"
        )
        return f"aucun rôle de l'équipe ne couvre : {missing} — {position}."


class PlanTask(Protocol):
    """What a task must carry to be confronted with the team (#1227).

    Two attributes, and not the schema's `Task`: this module is pure and has no business knowing
    `maestro.orchestrator` — neither its `id`, nor its dependencies, nor its description enter
    the question *who can take this?*.
    """

    @property
    def title(self) -> str:
        """The title, as it will be named to whoever decides on the recruitment."""
        ...  # pragma: no cover - protocol

    @property
    def required_competencies(self) -> tuple[str, ...]:
        """The tags the task requires, as the decomposition wrote them."""
        ...  # pragma: no cover - protocol


@dataclass(frozen=True)
class PlanGap:
    """The position **this plan** calls for and the team does not have (#1227).

    `gap` is the position, computed on the union of what the plan requires; `tasks` names the
    tasks it would take — those with a competency that falls within what it covers. Together
    they are what is shown before executing: the role, its reason, and the work awaiting it.

    Titles rather than tasks: what is shown to whoever decides is what they read, and carrying
    whole `Task`s would send all the way to the chat a four-section description nobody needs to
    say yes.
    """

    gap: MissingRole
    tasks: tuple[str, ...]

    @property
    def recruitable(self) -> bool:
        """Is there a position to propose? — otherwise there is only a fact to name.

        False when no role template covers the gap: Maestro does not know how to recruit for
        those competencies, and proposing an invented role would have it read as an existing
        role (see `missing_role`). The gap is then **said** in the journal, and the run goes on —
        the conduct that stays right in this one case (#1227).
        """
        return self.gap.role_template is not None

    def reason(self) -> str:
        """Why this role, to show whoever decides — the plan, not the project.

        It is the `reason` the proposed role and the chat card will carry. It names what the
        plan requires and nobody covers: a team is challenged by rereading what designated it,
        and here that is not a file of the project but **the plan that was just paid for**.

        It **does not list the tasks**, whereas `sentence` does: the chat card displays them
        separately (`tasks` travels with the request), and saying them twice in the same place
        would read the same thing as two facts.
        """
        missing = ", ".join(self.gap.covers or self.gap.competencies)
        return (
            f"le plan de ce travail demande {missing}, et aucun rôle de l'équipe "
            "ne le couvre."
        )

    def sentence(self) -> str:
        """The gap in one line, tasks included — what the run's journal carries.

        The counterpart of `MissingRole.sentence` for a whole plan. The tasks are **named**, not
        counted: "3 tasks" does not say whether a developer is about to be handed an animation,
        and that is what gets reread six months later to understand what went wrong.
        """
        if not self.tasks:
            return self.reason()
        work = ", ".join(f"« {title} »" for title in self.tasks)
        return f"{self.reason()} {len(self.tasks)} tâche(s) le demandent : {work}."


def plan_gap(tasks: Sequence[PlanTask], team: Sequence[Agent]) -> PlanGap | None:
    """The position `team` lacks to execute `tasks` — `None` if nothing is missing.

    The confrontation of #1227, in one pure function: the union of what the plan requires, what
    the team does not cover, the position that designates, and the tasks awaiting it. It is
    computed **once per run**, right after decomposition — routing asks the same question task
    by task, with the same two verbs (`uncovered_competencies`, `missing_role`).

    **A single position**, and it is a decision: a plan that would call for two absent roles
    returns here the one that covers the most, and the person is offered only one recruitment at
    a time. Two proposals in a row for a single plan would make two human waits on a run that
    has not started yet, and the team completes itself just as well on the next turn: what stays
    uncovered after this recruitment is signalled at routing (#1041), and discovered during
    execution it falls under #1181.

    **An empty team is not an incomplete team**: it returns `None`. A project without any agent
    is offered its whole team before even opening a run (#1146); deducing "a role is missing"
    here would propose a single position where a team is needed.
    """
    if not team or not tasks:
        return None
    requested = {tag for task in tasks for tag in task.required_competencies}
    uncovered = uncovered_competencies(requested, team)
    gap = missing_role(uncovered)
    if gap is None:
        return None
    targeted = frozenset(gap.covers or gap.competencies)
    return PlanGap(
        gap=gap,
        tasks=tuple(
            task.title
            for task in tasks
            if targeted & frozenset(task.required_competencies)
        ),
    )


def uncovered_competencies(
    required_competencies: Iterable[str], team: Sequence[Agent]
) -> tuple[str, ...]:
    """The requested competencies that **nobody** in `team` has, sorted.

    The raw fact of routing, isolated here so that it is the same everywhere: the router
    computes it on its active candidates, and this module translates it back into a position.
    Sorted, because `Agent.competences` is a `frozenset` and a signal whose order moves from one
    run to the next is not comparable.
    """
    requested = frozenset(required_competencies)
    covered: frozenset[str] = frozenset().union(*(a.competences for a in team))
    return tuple(sorted(requested - covered))


def missing_role(uncovered: Sequence[str]) -> MissingRole | None:
    """The position that would answer `uncovered` — `None` if there is nothing to fill.

    The retained role template is the one that covers **the most** missing competencies; on a
    tie, the first of `ROLE_TEMPLATES`, whose order is the catalogue's — the same deterministic
    tiebreak as competency routing (`maestro.router.assign`). A role template that would cover
    none of them is not a candidate: proposing "Designer" for a task that requires `monitoring`
    would be a recruitment at random.
    """
    if not uncovered:
        return None
    missing = frozenset(uncovered)
    best = max(
        ROLE_TEMPLATES,
        key=lambda template: len(missing & frozenset(template.competencies)),
    )
    covered = missing & frozenset(best.competencies)
    if not covered:
        return MissingRole(
            competencies=tuple(uncovered),
            role=", ".join(uncovered),
        )
    return MissingRole(
        competencies=tuple(uncovered),
        role=best.role,
        role_template=best.name,
        # Sorted, like `competencies` and for the same reason: a signal whose order moves from
        # one run to the next is not comparable, and this one travels all the way to the chat
        # card.
        covers=tuple(sorted(covered)),
    )
