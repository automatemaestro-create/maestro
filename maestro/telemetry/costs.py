"""Per-task cost accounting — the ledger of a run (ticket #55).

The journal (#8) already records each step with its usage (tokens, cost,
duration); this building block **reorganizes it into accounting**: one entry per
task (`TaskCost`), the planning step apart, and the run aggregate (`RunCost`) —
the "run cost, traceable per task" view of MVP criterion no. 6 (parent #49),
which the Control Tower API exposes (#57) and on which the spending cap rests
(`SpendingCap`, #56): the guardrail (#9) rereads this ledger at each usage
measurement, with no parallel counter.

Attribution follows the journal's step convention: `planification` and `brief`
(#318) for the orchestrator, `<task>` for the step of the task itself, and
`<task>:<annex>` for its ancillary steps (human validation #48, inter-agent
message #44) — each annex is attached to its task. Each journal line is thus
counted exactly once: the accounting total falls back on
`RunJournal.usage_totale`.

This list of non-task steps is **closed by the code, not by convention**: the
default rule is "any other step is a task", so a run step that someone forgot to
declare there would not fail — it would open a phantom task line in the ledger,
which is far worse than an error.

The accounting evaluates **no price**: the estimated cost is the one reported by
the provider through the `ModelProvider` layer (#32) — per-model pricing stays on
its side, never hard-coded here. A provider that reports no cost leaves the task
at an "unknown" cost (None, to be told apart from a zero cost).

That unknown cost would make the spending cap ineffective on such a provider
(#113): `SpendingCap` therefore accepts a **token cap** in addition to (or
instead of) the cost cap — tokens are always reported. The USD cap takes over as
soon as the cost is known; the token cap takes over otherwise, and
`spending_control_summary` tells the operator which of the two really holds
(instead of a silently ineffective cap).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from maestro.references import ReferenceTicket, ticket_en_dict
from maestro.telemetry.journal import RunJournal, StepRecord
from maestro.telemetry.usage import StepUsage


def interval_from(
    timestamp: str, duration_ms: int | None
) -> tuple[datetime, datetime] | None:
    """The `[start, end]` interval a step **occupied**, or None (#989).

    The journal carries only one timestamp, the one of the **recording** — hence
    the end —, and the wall clock duration of the step: the start is deduced from
    them. This is already what the Langfuse export does
    (`maestro.telemetry.langfuse._start_from`), and doing it differently would
    give two starts for the same step.

    None when there is no interval to read: no measured duration, a zero or
    negative duration, an empty or unreadable timestamp. A step without an
    interval does not count in the union — nor does it distort it.

    Public because the ledger is built through **two** paths, and they must read
    time the same way: from the engine journal (`RunCost.from_journal`) and from
    the Control Tower event stream (`EtatExecution.cout`). A time rule copied on
    both sides of a boundary is what #830 saw break.

    ⚠ The timestamp is written to the **second** (`isoformat(timespec="seconds")`):
    the bounds of the union are therefore quantized to the second. This has no
    effect on what the union is for — an overlap of several minutes between two
    tasks run side by side — and making the journal finer for that alone would
    cost all its lines.
    """
    if duration_ms is None or duration_ms <= 0 or not timestamp:
        return None
    try:
        end = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return (end - timedelta(milliseconds=duration_ms), end)


def _interval(record: StepRecord) -> tuple[datetime, datetime] | None:
    """The interval occupied by a journal line — see `interval_from`."""
    return interval_from(record.horodatage, record.usage.duration_ms)


def union_ms(intervals: list[tuple[datetime, datetime]]) -> int | None:
    """The duration covered by the **union** of the intervals, never their sum (#989).

    Two tasks run side by side occupy the run only once over the part they share.
    This is the correction the tooling made on the `/orchestrate` runs (#497 —
    "occupancy is the union of the intervals and never their sum") and that the
    product repeated backwards: 47 min announced for 43.5 min of wall clock
    (review of 2026-08-26).

    None on an empty list — nothing measured is not a zero duration.
    """
    if not intervals:
        return None
    # Sorted by start, the intervals melt into contiguous blocks: we hold the
    # current block and only count when a gap closes it.
    ordered = sorted(intervals)
    current_start, current_end = ordered[0]
    covered = timedelta()
    for start, end in ordered[1:]:
        if start > current_end:
            # A gap: the previous block is closed, we count it and start over.
            covered += current_end - current_start
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    covered += current_end - current_start
    return int(round(covered.total_seconds() * 1000))


#: Journal step that belongs to no task: the planning (#8).
STEP_PLANNING = "planification"

#: Journal step that belongs to no task: the structured **brief** (#318).
#: To declare here and not only to record: without this line, `from_journal`
#: would fall back on its default rule — "any other step is a task" — and would
#: open a phantom task entry named "brief" in the ledger. Its usage would be
#: counted, but attributed to a job that does not exist.
STEP_BRIEF = "brief"

#: Journal step that belongs to no task: the resumption of an interrupted run
#: (#96) — a run marker, not agent work. Null usage by construction (no model is
#: called to resume): excluding it from the ledger spares it a phantom task
#: entry, without changing the total.
STEP_RESUME = "reprise"

#: Journal step that belongs to no task: the **confrontation of the team with the
#: plan** (#1227), between the decomposition and the first task — the missing
#: role, and since #1299 how many tasks each role will take side by side. Null
#: usage by construction — confronting tags with sheets calls no model —, but
#: **counted in the run time** (unlike `STEP_RESUME`): waiting for whoever
#: decides occupies the run like the rest, and a run suspended four minutes on a
#: hiring did not last four minutes less. To declare here for the reason of
#: `STEP_BRIEF`: without this line, the default rule would open a phantom task
#: entry named "equipe" in the ledger.
STEP_TEAM = "equipe"

#: Journal step that belongs to no task: the **cadence** of the run (#1298) — why
#: its tasks go one by one (chained plan, unversioned project, agent at full
#: capacity), and what became of the proposal to version the project. Null usage
#: by construction: stating calls no model. To declare here for the reason of
#: `STEP_BRIEF`: without this line, the default rule would open a phantom task
#: entry named "cadence" in the ledger.
STEP_CADENCE = "cadence"

#: Journal step that belongs to no task: the **spending cap reached** (#1182) —
#: the question put to the person, then what they decided (raise, reduce, stop).
#: Null usage by construction: the suspension calls no model, and the spending
#: of the interrupted task is carried by its own line (`<task>:plafond`), never
#: by this one. Counted in the run time like `STEP_TEAM`, for the same reason: a
#: run suspended for an hour on its budget lasted that hour. To declare here for
#: the reason of `STEP_BRIEF`: without this line, the default rule would open a
#: phantom task entry named "plafond" in the ledger.
STEP_SPENDING_CAP = "plafond"

#: Step that belongs to no task: the **assessment** of a finished run (#1284), a
#: model call rendered **after** the end of the run and counted in its cost. It is
#: never born in the engine journal — the API renders and publishes it —, but the
#: list of non-task steps is closed by the code: declared here, it cannot open a
#: phantom task named "bilan" the day a journal carries it. Its interval does
#: **not** count in the wall clock time of the run, which was over.
STEP_ASSESSMENT = "bilan"

#: Step that belongs to no task: the **checkpoint probe** (#1304), a model session
#: played once per provider, at the start of the first agent session, to prove
#: that a Maestro refusal holds on its side. It costs (≈ 20 k tokens on the real
#: CLI) and used to fall into the first task that armed it: the probe runs in an
#: asyncio task born from the agent session, hence under its collector (#1398).
#: It is now recorded through `report_run_usage`, on this run line. To declare
#: here for the reason of `STEP_BRIEF`: without this line, the default rule would
#: open a phantom task named "sonde".
STEP_PROBE = "sonde"

#: Step that belongs to no task: the **acceptance of the run** (#1530) — each
#: acceptance criterion of the brief confronted with the integrated deliverable,
#: by running it, once all the tasks are settled (`maestro.engine.recette`). It
#: replaces the acceptance task the plan used to carry (p5: 12 min 50 s and
#: $1.633 on the path), so it counts **in the wall clock time of the run**, unlike
#: the assessment: the run is only over once its criteria are verified. Its own
#: line, so that one reads what the final verification costs against the task it
#: replaces. To declare here for the reason of `STEP_BRIEF`: without this line, a
#: phantom "recette" task.
STEP_ACCEPTANCE = "recette"


@dataclass(frozen=True)
class TaskCost:
    """The "task" entry of the ledger: who did what, for what usage.

    `usage` merges the step of the task and its ancillary steps (validation,
    message) — these report a null usage today, the merge would keep them counted
    if they carried one tomorrow. `name`, `agent`, `role` and `status` come from
    the step of the task itself; they stay empty as long as only an annex has been
    recorded (task still running).

    `ticket` (#187) is the ticket the task belongs to: unlike the identity, it is
    taken from **any** step that carries one — the `<task>:reference` annex is
    precisely the only carrier when an agent sets it during execution.

    `project_id` (#222) is the project the task belongs to, taken under the same
    regime as `ticket`: from **any** step that carries one — this is what allows
    filtering the accounting by project.
    """

    task_id: str
    name: str = ""
    agent: str = ""
    role: str = ""
    status: str = ""
    usage: StepUsage = StepUsage()
    ticket: ReferenceTicket | None = None
    project_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Re-emits the entry as a JSON-serializable dict (the API form, #57)."""
        return {
            "tache_id": self.task_id,
            "nom": self.name,
            "agent": self.agent,
            "role": self.role,
            "statut": self.status,
            "usage": self.usage.to_dict(),
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.project_id,
        }


@dataclass(frozen=True)
class RunCost:
    """The ledger of a run: the cost per task and the run aggregate.

    `tasks` follows the order of first appearance in the journal — the order in
    which tasks finished (#7), each entry staying linked to the plan by `task_id`;
    `planning` carries the usage of the orchestrator.

    `brief` (#318) carries the usage of the **brief** step, apart from the
    planning and apart from the tasks. Apart from the planning because these are
    two distinct model calls, and the brief can be **regenerated** several times
    by the clarification round trips (#321): confusing them would hide what
    refining the intent really costs. Null as long as no run went through this
    step — it is lot 6 (#320) that plugs it into the loop.

    `assessment` (#1284) carries the usage of the **evidence-based assessment**,
    rendered once the run is over. Apart for the reason of the brief: it is a
    distinct call, and counting it in the planning would make the framing pay for
    the look taken afterwards. Null as long as no assessment has been rendered.

    `probe` (#1398) carries the usage of the **checkpoint probe** (#1304): played
    during the first task, it is not part of it — counting it in `socle-nextjs`
    made it carry ≈ 20 k tokens it had not consumed. Null as long as no probe
    has cost.

    `acceptance` (#1530) carries the usage of the **run acceptance** — the brief
    criteria verified on the integrated deliverable, at the end of the run. Apart
    so that one reads what the final verification costs, against the acceptance
    task it replaces; in the wall clock time, since it is on the path of the run.
    Null without acceptance.
    """

    run_id: str
    planning: StepUsage = StepUsage()
    brief: StepUsage = StepUsage()
    tasks: tuple[TaskCost, ...] = ()
    #: What the run **occupied**: the union of the intervals of its steps (#989),
    #: `None` when none has a readable duration. This is the value `total` carries
    #: as its duration, in place of a sum that counted tasks run side by side
    #: twice.
    wall_clock_ms: int | None = None
    assessment: StepUsage = StepUsage()
    probe: StepUsage = StepUsage()
    acceptance: StepUsage = StepUsage()

    @property
    def total(self) -> StepUsage:
        """Aggregated usage of the run — counters summed, duration **united** (#989).

        Tokens, calls, turns and cost are summed: two tasks side by side do cost
        twice. The **duration** does not: it is wall clock time, and the run lived
        only one — hence `wall_clock_ms`, the union of the intervals, in place of
        the sum `fusion` would produce.

        Its **waits** fall back to `None`, and this is intended: a sum of waits of
        parallel tasks is not a wait of the run, and subtracting it from a union
        would give a "run work" that means nothing. At the run level, the duration
        is a single measure — the one GitHub Actions calls "Total duration"; the
        work/wait breakdown is read per task, where it makes sense.

        The counters, for their part, do fall back on `RunJournal.usage_totale`.
        The **assessment** (#1284) is added to them: it is not born in the engine
        journal, but it is a spending of the run, and a total that kept quiet about
        it would no longer be the one the run list displays. The **probe** (#1398)
        too: it changed line, not run. And the **acceptance** (#1530), which is a
        spending of the run.
        """
        total = (
            self.planning.merge(self.brief)
            .merge(self.assessment)
            .merge(self.probe)
            .merge(self.acceptance)
        )
        for task in self.tasks:
            total = total.merge(task.usage)
        return replace(
            total,
            duration_ms=self.wall_clock_ms,
            approval_duration_ms=None,
            slot_wait_duration_ms=None,
            workshop_wait_duration_ms=None,
        )

    @classmethod
    def from_journal(cls, journal: RunJournal) -> RunCost:  # noqa: C901
        """Accounts for `journal`: each line attributed to its task, counted once.

        Relies on `RunJournal.records` (the in-memory aggregate): usable at the end
        of a run as well as during it (partial accounting — a task of which only an
        annex is recorded appears without identity or status, its usage already
        counted).
        """
        planning = StepUsage()
        brief = StepUsage()
        assessment = StepUsage()
        probe = StepUsage()
        acceptance = StepUsage()
        entries: dict[str, TaskCost] = {}
        # The intervals of all the counted steps — planning and brief included:
        # they occupy the run like the rest, and it is the wall clock time of the
        # whole run that is measured, not that of its tasks alone.
        intervals: list[tuple[datetime, datetime]] = []
        for record in journal.records:
            if record.etape == STEP_ASSESSMENT:
                # The assessment (#1284): rendered after the end, it costs the
                # run without occupying it — its interval stays out of the wall
                # clock time.
                assessment = assessment.merge(record.usage)
                continue
            if record.etape != STEP_RESUME:
                interval = _interval(record)
                if interval is not None:
                    intervals.append(interval)
            if record.etape == STEP_PLANNING:
                planning = planning.merge(record.usage)
                continue
            if record.etape == STEP_PROBE:
                # The checkpoint probe (#1398): a spending of the run, played
                # during a task without being part of it.
                probe = probe.merge(record.usage)
                continue
            if record.etape == STEP_ACCEPTANCE:
                # The run acceptance (#1530): a step of the run, on its path — its
                # interval is taken above, its usage on its own line.
                acceptance = acceptance.merge(record.usage)
                continue
            if record.etape == STEP_BRIEF:
                # A step of the run, not a task (#318) — and merged rather than
                # assigned: a regenerated brief (#321) records one line per turn,
                # and the ledger must carry the sum.
                brief = brief.merge(record.usage)
                continue
            if record.etape == STEP_RESUME:
                # Run marker (#96), attached to no task: nothing to account for —
                # the steps it announces are themselves reintegrated into the
                # journal (`RunJournal.reconstitue`) and counted in their place.
                continue
            if record.etape in (STEP_TEAM, STEP_SPENDING_CAP, STEP_CADENCE):
                # The confrontation of the team with the plan (#1227), the
                # decision at the spending cap (#1182), the cadence of the run
                # (#1298): steps of the run, without usage and without task. Their
                # interval is already taken above — it is run time, human waiting
                # included.
                continue
            task_id = record.etape.split(":", 1)[0]
            entry = entries.get(task_id)
            if entry is None:
                entry = TaskCost(task_id=task_id)
            if record.ticket is not None:
                # The external ticket (#187) comes from any step of the task —
                # the `:reference` annex carries nothing else.
                entry = replace(entry, ticket=record.ticket)
            if record.projet_id is not None:
                # The project (#222) follows the same rule: any step of the task
                # is authoritative, a step without a project erases nothing.
                entry = replace(entry, project_id=record.projet_id)
            if record.etape == task_id:
                # The step of the task itself: authoritative for the identity.
                entry = replace(
                    entry,
                    name=record.nom,
                    agent=record.agent,
                    role=record.role,
                    status=record.statut,
                    usage=entry.usage.merge(record.usage),
                )
            else:
                entry = replace(entry, usage=entry.usage.merge(record.usage))
            entries[task_id] = entry
        return cls(
            run_id=journal.run_id,
            planning=planning,
            brief=brief,
            tasks=tuple(entries.values()),
            wall_clock_ms=union_ms(intervals),
            assessment=assessment,
            probe=probe,
            acceptance=acceptance,
        )

    def to_dict(self) -> dict[str, Any]:
        """Re-emits the ledger as a JSON-serializable dict (the API form, #57)."""
        return {
            "run_id": self.run_id,
            "planification": self.planning.to_dict(),
            "brief": self.brief.to_dict(),
            "bilan": self.assessment.to_dict(),
            "sonde": self.probe.to_dict(),
            "recette": self.acceptance.to_dict(),
            "total": self.total.to_dict(),
            "taches": [task.to_dict() for task in self.tasks],
        }


class SpendingCapExceeded(RuntimeError):
    """Raised when the spending of a run exceeds its cap (#9).

    Emitted from `report_usage` (hence from the provider, between two model
    calls) when `SpendingCap.check` notices the overrun: it interrupts the
    current step, which the caller records as stopped by the guardrail.
    """


class SpendingCap:
    """Spending guardrail of a run (#9), backed by the ledger (#56).

    This is the control the usage collector consults at each measurement
    (`collect_usage(spending_cap=...)`). It keeps **no counter**: at each check, the
    spending already committed is reread in the accounting of the run
    (`RunCost.from_journal`) — the telemetry is the single source of the cost —
    and completed with the usage of the current step, not yet recorded in the
    journal. Parallel steps still in flight only count once recorded: a slight
    transient undercount, accepted (POC), never a double count.

    Two thresholds, at least one of which must be set: `max_cost_usd` (budget in
    USD, with no grip on a provider that reports no cost) and `max_tokens` (budget
    in tokens, working on any provider — tokens are always reported, #113). Both
    are checked: crossing either one stops the task.

    ⚠ **The token cap counts excluding cache reads** (#1398,
    `StepUsage.tokens_excluding_cache_reads`) — a decision, not a detail. It used to rest
    on `total_tokens`, which adds up the context of **every** turn: a long task
    served by the cache rereads everything that precedes at each call there, and
    got cut on what it rereads (1,845,438 tokens on p5 for $1.87), not on what it
    consumes. Ruled out: the total, for this reason; the context reached alone,
    which says nothing of a task running long on a small context. The fresh
    prompt, the written cache and the output stay counted, and a provider that
    reports no cache is counted exactly as before.
    """

    def __init__(
        self,
        journal: RunJournal,
        max_cost_usd: float | None = None,
        *,
        max_tokens: int | None = None,
    ) -> None:
        if max_cost_usd is None and max_tokens is None:
            raise ValueError(
                "PlafondDepense exige au moins un plafond (coût en USD ou tokens)."
            )
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise ValueError(
                f"plafond_cout_usd doit être > 0 (reçu : {max_cost_usd})."
            )
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError(
                f"plafond_tokens doit être > 0 (reçu : {max_tokens})."
            )
        self._journal = journal
        self._max_cost_usd = max_cost_usd
        self._max_tokens = max_tokens

    def check(self, current: StepUsage) -> None:
        """Raises `SpendingCapExceeded` if the run spending, `current` included, exceeds.

        The cost goes first (the most telling message when it is known), tokens
        next — the token threshold holds even when the cost is unknown.
        """
        total = RunCost.from_journal(self._journal).total.merge(current)
        cost = total.cost_usd
        if (
            self._max_cost_usd is not None
            and cost is not None
            and cost > self._max_cost_usd
        ):
            raise SpendingCapExceeded(
                f"plafond de dépense dépassé : {cost:.4f} $ consommés sur l'exécution "
                f"pour un plafond de {self._max_cost_usd:.4f} $ — tâche stoppée."
            )
        if (
            self._max_tokens is not None
            and total.tokens_excluding_cache_reads > self._max_tokens
        ):
            raise SpendingCapExceeded(
                f"plafond de tokens dépassé : {total.tokens_excluding_cache_reads} "
                "tokens consommés "
                f"hors cache relu sur l'exécution pour un plafond de {self._max_tokens} "
                "— tâche stoppée."
            )


def spending_control_summary(
    max_cost_usd: float | None,
    max_tokens: int | None,
    usage: StepUsage,
) -> str:
    """Describes the active spending control in one line, for the operator (#113).

    Makes the "silently ineffective cap" case visible: a cost cap armed on a
    provider that reports no cost (`usage.cost_usd` None) has no grip — only a
    token cap then really caps. Meant for the run summary and the JSON report
    (`RunReport`), not for the control logic (carried by `SpendingCap.check`).
    """
    controls: list[str] = []
    if max_tokens is not None:
        # What the cap counts, stated with the figure (#1398): a "tokens
        # (175438/500000)" next to a total of 1.8 M would read as an error.
        controls.append(
            f"tokens hors cache relu ({usage.tokens_excluding_cache_reads}/{max_tokens})"
        )
    if max_cost_usd is not None:
        if usage.cost_usd is not None:
            controls.append(f"coût réel ({usage.cost_usd:.4f}/{max_cost_usd:.4f} $)")
        elif max_tokens is None:
            # The only cap set has no grip: spending is bounded by nothing.
            return (
                f"plafond de coût de {max_cost_usd:.4f} $ armé mais SANS PRISE — "
                "le fournisseur ne rapporte pas de coût ; armez un plafond en tokens "
                "(--plafond-tokens) pour plafonner ce run."
            )
        else:
            controls.append(
                f"coût inopérant ({max_cost_usd:.4f} $ — fournisseur sans coût rapporté)"
            )
    if not controls:
        return "aucun plafond armé"
    return "plafond actif — " + ", ".join(controls)
