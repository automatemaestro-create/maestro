"""Usage of model calls: tokens, cost, duration, tools (#8).

`StepUsage` is the usage of one step (one or several model calls, merged). It goes
up through a **context collector** (`contextvars`): the caller opens
`collect_usage()` around a piece of work, and providers report each call through
`report_usage(...)`. Outside a collector, reporting does nothing. This channel
keeps `ModelProvider`'s signature unchanged: a provider without telemetry works as
it is, and its steps simply carry no usage. Since the context propagates through
`await`, the channel crosses every layer (orchestrator, tooled runtime) without an
extra parameter.

The channel also **relays** the spending cap (#9): spending is checked between two
model calls. A collector opened with a spending control (`spending_cap=`) consults
it at each measure, without counting anything itself. The control
(`maestro.telemetry.costs.SpendingCap`, #56) rereads the per-task accounting of the
run, the single source of the cost, and raises `SpendingCapExceeded` from
`report_usage` as soon as the cap is crossed. The model call in progress is already
counted (the cost stays visible), but the rest of the step is stopped. A provider
that reports no cost escapes a cap *in USD* (unknown cost) but stays cappable **in
tokens** (#113): the same control (`SpendingCap`) accepts a token cap, which always
holds.

The channel can also be read **during** the work (#835). A collector opened with
`on_measure=` calls its caller back at each reported measure, with the running
total of the step: this is how the engine reads the spending of a task in progress
instead of waiting for its outcome. `current_usage()` returns that running total to
whoever wants it without waiting for the next measure (when an attempt opens, for
instance). Neither counts anything: the running total stays the one `report_usage`
built, and the run accounting only reads it when the step is recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T", int, float)


def _optional_sum(a: _T | None, b: _T | None) -> _T | None:
    """Adds two optional counters, keeping "unknown" (None ≠ 0)."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def _optional_max(a: int | None, b: int | None) -> int | None:
    """The larger of two optional maxima, keeping "unknown" (#1398)."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


@dataclass(frozen=True)
class StepUsage:
    """Usage of a step: model calls, tokens, cost, durations, turns, tools.

    `duration_ms` is the *wall-clock* duration of the step, set by the caller (the
    engine) through `with_duration`; `api_duration_ms` is the cumulated API time
    the provider reports. `cost_usd` stays None when no provider reported it
    (unknown, as opposed to a zero cost). `input_tokens` includes the provider-side
    cache (see `maestro.providers.claude`), and `cache_read_tokens` says how much.

    `cache_read_tokens` and `cache_write_tokens` (#1398) are two **parts** of
    `input_tokens`, like `unpriced_tokens`: never extra tokens. `max_context` is the
    **context reached**, the largest prompt of a single API call, and it merges by
    **maximum**, never by sum. `input_tokens` adds up the context of **every**
    turn, so a long task served by the cache reads like a greedy one without them:
    `socle-nextjs` showed 1,845,438 tokens on p5 for a context that never went past
    ≈ 66 k, and a cost of $1.87 — that of work reread from the cache at 90 % off.
    The total is right; it measures the volume processed, not the consumption.
    `max_context` is `None`, unknown, wherever nobody measured a call on its own (a
    cumulated result, an older journal line): it is never inferred from a sum.

    `approval_duration_ms` (#584) is the part of `duration_ms` spent **suspended
    on a human decision** (`maestro.deliberation.CreditArbitrage`): never extra
    time, always a part of the time already there. It lets the working time
    (`work_duration_ms`) be read apart from the time an operator took to decide.
    Without it, a task suspended four minutes on a sensitive act reads exactly like
    a slow task, and the audit of a run (#495) counts the second as the first.

    It is `None`, unknown, wherever nobody measured, and `0` where it was measured
    that no approval happened: the distinction is the one `cost_usd` makes, for the
    same reason. It is only set on the **final** step of a task, the only one that
    carries its wall-clock duration; setting it on side steps too (`:validation`,
    `:refus-outil`, with zero usage) would count it twice in `usage_totale`.

    `slot_wait_duration_ms` and `workshop_wait_duration_ms` (#989) are the two other
    parts of `duration_ms` that are not work: the time spent waiting for an
    **instance slot** of the agent (#86), then for the **workshop** of its project
    (#839, one task at a time in the root of an unversioned project). They live
    here for the same reason as `approval_duration_ms`: parts of a duration already
    measured, never extra time. "Waiting for one's turn is not working"
    (`maestro/engine/executor.py`). Without them, a task that stayed eighteen
    minutes in a queue reads exactly like a slow task, measured twice: 21 min 17 s
    announced for 8 min 05 s of work (review of 2026-08-26), 24 min 17 s for
    ~11 min (retex of 2026-09-11).

    **Two fields, not one**, because the two waits do not call for the same action:
    a missing slot is fixed by adding instances to the agent, while a workshop that
    blocks is the serialization regime of an unversioned project, which is not
    changed lightly (#839). Merging them into a single "wait time" would make the
    figure unactionable.

    `unpriced_tokens` (#1280) is the part of `total_tokens` that **no cost
    covers**: a part, as the waits are of the duration, never extra tokens. A
    measure without a cost (`cost_usd` None) has **all** its tokens unpriced, and
    this is set at construction (`__post_init__`): a producer has nothing to declare
    to say "I have no price". Merging **adds up** this part like the other
    counters, and that is what keeps it right where the cost rule alone does not:
    $1.17 priced plus 2 M tokens without a price merge into a cost of $1.17
    (`_optional_sum` keeps the known value) **and** 2 M unpriced tokens. A single
    measure can **remove** from this part: one from a provider whose result prices
    turns already reported after the fact (the rest of the Claude `ResultMessage`,
    `maestro.providers.claude._CompteurTours`), which then carries a negative part,
    exactly that of its turns. A session killed before its result keeps its part,
    and a retry that succeeds only covers its own turns.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int | None = None
    api_duration_ms: int | None = None
    approval_duration_ms: int | None = None
    slot_wait_duration_ms: int | None = None
    workshop_wait_duration_ms: int | None = None
    turns: int = 0
    tools: tuple[str, ...] = ()
    unpriced_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    max_context: int | None = None

    def __post_init__(self) -> None:
        # Without a cost, nothing the measure consumed has a price (#1280). Set
        # here rather than asked of every producer: a provider that does not price
        # (#113), a turn reported before its result (#835) or an older journal line
        # all say it without knowing. Zero means "not provided" because a measure
        # without a cost cannot, in truth, have fewer unpriced tokens than tokens.
        if self.cost_usd is None and not self.unpriced_tokens:
            object.__setattr__(self, "unpriced_tokens", self.total_tokens)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens

    @property
    def tokens_excluding_cache_reads(self) -> int:
        """The total **minus what the cache reread** (#1398): what the token cap counts.

        Rereading a context already in the cache is not consuming it a second time:
        the provider bills it at a fraction of the input price, and every turn of a
        long task does that to everything before it. A token cap (#113) that
        counted it would cut a task on what it **rereads** (1,845,438 tokens on p5
        for $1.87) instead of what it **processes**. The fresh prompt, the written
        cache and the output stay counted.
        """
        return self.total_tokens - self.cache_read_tokens

    @property
    def wait_duration_ms(self) -> int | None:
        """What the task spent **waiting**: approval, slot and workshop (#989).

        `None`, unknown, as long as none of the three was measured; otherwise the
        sum of those that were. These three waits **do not overlap**: they come
        one after the other (slot, then workshop, then the work in which an
        approval can suspend), so adding them up is legitimate here. What does not
        add up is the occupation of two tasks run side by side, and that is handled
        one level up (`maestro.telemetry.costs.RunCost`).
        """
        parts = (
            self.approval_duration_ms,
            self.slot_wait_duration_ms,
            self.workshop_wait_duration_ms,
        )
        measured = [part for part in parts if part is not None]
        return sum(measured) if measured else None

    @property
    def work_duration_ms(self) -> int | None:
        """The wall-clock duration **minus its waits**: the working time (#584, #989).

        `None` when the wall-clock duration is: there is nothing to break down.
        Without a measured wait, it is the wall-clock duration itself: a task whose
        queues nobody measured spent, as far as is known, all its time working.
        Bounded at zero, since the measures come from clocks that do not start at
        the same instant.
        """
        if self.duration_ms is None:
            return None
        wait = self.wait_duration_ms
        if wait is None:
            return self.duration_ms
        return max(0, self.duration_ms - wait)

    def merge(self, other: StepUsage) -> StepUsage:
        """Merges two measures: counters added up, tools joined in order."""
        return StepUsage(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=_optional_sum(self.cost_usd, other.cost_usd),
            duration_ms=_optional_sum(self.duration_ms, other.duration_ms),
            api_duration_ms=_optional_sum(self.api_duration_ms, other.api_duration_ms),
            approval_duration_ms=_optional_sum(
                self.approval_duration_ms, other.approval_duration_ms
            ),
            slot_wait_duration_ms=_optional_sum(
                self.slot_wait_duration_ms, other.slot_wait_duration_ms
            ),
            workshop_wait_duration_ms=_optional_sum(
                self.workshop_wait_duration_ms, other.workshop_wait_duration_ms
            ),
            turns=self.turns + other.turns,
            tools=self.tools + tuple(t for t in other.tools if t not in self.tools),
            unpriced_tokens=self.unpriced_tokens + other.unpriced_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            # A maximum, never a sum (#1398): two turns of 60 k did not reach a
            # context of 120 k, and that is exactly the misreading the additivity
            # of `input_tokens` invites.
            max_context=_optional_max(self.max_context, other.max_context),
        )

    def with_duration(
        self,
        duration_ms: int,
        *,
        approval_ms: int | None = None,
        slot_wait_ms: int | None = None,
        workshop_wait_ms: int | None = None,
    ) -> StepUsage:
        """Copy of the measure with the wall-clock duration set by the caller.

        `approval_ms` (#584) sets in the same move the part of that duration spent
        suspended on a human decision; `slot_wait_ms` and `workshop_wait_ms` (#989)
        the two parts spent waiting for one's turn. Left out, the fields stay
        `None`: a caller that does not measure a wait does not declare there was
        none. Most callers of this verb (queues, durable activities, planning loop)
        have no wait to measure.
        """
        return replace(
            self,
            duration_ms=duration_ms,
            approval_duration_ms=approval_ms,
            slot_wait_duration_ms=slot_wait_ms,
            workshop_wait_duration_ms=workshop_wait_ms,
        )

    def short_summary(self) -> str:
        """Renders the measure as one readable line (for Markdown summaries)."""
        duration = "n/d" if self.duration_ms is None else f"{self.duration_ms / 1000:.1f} s"
        # Named **only** when there were some: announcing "of which 0.0 s of
        # approval" on every task of a run would teach the reader to skip the
        # mention, and it must stand out the day a task spent four minutes waiting
        # for someone, or eighteen waiting for its turn (#989).
        waits = [
            f"{part / 1000:.1f} s {word}"
            for part, word in (
                (self.approval_duration_ms, "d'arbitrage"),
                (self.slot_wait_duration_ms, "de file d'agent"),
                (self.workshop_wait_duration_ms, "d'atelier de projet"),
            )
            if part
        ]
        if waits:
            duration += " (dont " + ", ".join(waits) + ")"
        if not self.calls:
            return f"aucun usage fournisseur rapporté · durée {duration}"
        cost = "n/d" if self.cost_usd is None else f"{self.cost_usd:.4f} $"
        if self.cost_usd is not None and self.unpriced_tokens > 0:
            # An amount that does not cover everything consumed (#1280) is not
            # written as a balance: it is a floor, and it says so.
            cost = f"≥ {cost} ({self.unpriced_tokens} tokens sans coût)"
        # The context reached and the part reread from the cache (#1398), named
        # when known: without them, the total reads as a consumption, which it is
        # not on a long task served by the cache.
        context = (
            "" if self.max_context is None else f" · contexte max {self.max_context}"
        )
        reread = (
            f", dont {self.cache_read_tokens} relus en cache" if self.cache_read_tokens else ""
        )
        return (
            f"{self.calls} appel(s) modèle · {self.total_tokens} tokens "
            f"({self.input_tokens} entrée{reread} / {self.output_tokens} sortie)"
            f"{context} · coût {cost} · durée {duration}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Renders the measure as a JSON-serializable dict.

        The keys keep their French values: the run journal, the cost ledger, the
        API and `apps/web` read them (docs/55, rule 3; `tests/test_wire_names.py`).
        """
        return {
            "appels": self.calls,
            "tokens_entree": self.input_tokens,
            "tokens_sortie": self.output_tokens,
            "tokens_total": self.total_tokens,
            "cout_usd": self.cost_usd,
            "duree_ms": self.duration_ms,
            "duree_api_ms": self.api_duration_ms,
            "duree_arbitrage_ms": self.approval_duration_ms,
            "duree_attente_creneau_ms": self.slot_wait_duration_ms,
            "duree_attente_atelier_ms": self.workshop_wait_duration_ms,
            # The two **derived** values (#989) travel like `total_tokens`:
            # computed here and ignored on the way back. The rule "what is work,
            # what is waiting" must live in one place only; rewriting it in
            # TypeScript for the screen would make two rules that drift apart.
            "duree_attente_ms": self.wait_duration_ms,
            "duree_execution_ms": self.work_duration_ms,
            "tours": self.turns,
            "outils": list(self.tools),
            # The part the cost does not cover (#1280): this is how the ledger and
            # the screen know an amount is only a floor.
            "tokens_non_tarifes": self.unpriced_tokens,
            # The context reached and the two cache parts (#1398): what a task
            # really consumed reads through them, not through `total_tokens`.
            "tokens_cache_lus": self.cache_read_tokens,
            "tokens_cache_ecrits": self.cache_write_tokens,
            "contexte_max": self.max_context,
            # Derived like `total_tokens`: what the token cap counts, computed here
            # so that the screen does not redo the rule.
            "tokens_hors_cache_lu": self.tokens_excluding_cache_reads,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StepUsage:
        """Rebuilds a measure from its `to_dict` form (JSON round trip, #41).

        The derived keys (`tokens_total`, `tokens_hors_cache_lu`,
        `duree_attente_ms`, `duree_execution_ms`) are ignored; missing keys fall
        back to the defaults. The measure of a worker that reports nothing stays
        valid, and a journal line written before #989 reads back without measured
        waits (unknown, not zero). A line from before #1280 reads back with the
        unpriced part its cost says: all its tokens if the cost is unknown, none if
        it is known. A line from before #1398 reads back without a known context
        (`None`) and without a cache part: nothing is rebuilt from a sum of turns.
        """
        return cls(
            calls=data.get("appels", 0),
            input_tokens=data.get("tokens_entree", 0),
            output_tokens=data.get("tokens_sortie", 0),
            cost_usd=data.get("cout_usd"),
            duration_ms=data.get("duree_ms"),
            api_duration_ms=data.get("duree_api_ms"),
            approval_duration_ms=data.get("duree_arbitrage_ms"),
            slot_wait_duration_ms=data.get("duree_attente_creneau_ms"),
            workshop_wait_duration_ms=data.get("duree_attente_atelier_ms"),
            turns=data.get("tours", 0),
            tools=tuple(data.get("outils", ())),
            unpriced_tokens=data.get("tokens_non_tarifes", 0),
            cache_read_tokens=data.get("tokens_cache_lus", 0),
            cache_write_tokens=data.get("tokens_cache_ecrits", 0),
            max_context=data.get("contexte_max"),
        )


class SpendingControl(Protocol):
    """Spending control the collector consults at each measure (#9).

    `check` receives the running usage of the current step and raises
    (`SpendingCapExceeded`) if the spending of the run crosses its cap (in USD
    **or in tokens**, #113). The implementation is
    `maestro.telemetry.costs.SpendingCap`, backed by the per-task accounting (#56):
    the protocol keeps the measuring channel independent of the accounting, which
    is built on top of it.
    """

    def check(self, current: StepUsage) -> None:
        """Raises if the spending of the run, `current` included, crosses the cap."""
        ...


class UsageCollector:
    """Accumulates the measures reported during a `collect_usage()` block.

    With `spending_cap`, it relays the spending guardrail (#9): each measure is
    counted first (the cost stays visible), then the control is consulted with the
    running total of the step. The control confronts the spending of the run with
    its cap (#56); the collector keeps no parallel count.

    With `on_measure` (#835), it **calls back** its caller at each measure, with
    the running total of the step, **before** the spending control, and the order
    matters: a measure that crosses the cap is still spending, and the reading that
    shows it must go out even if the control stops the step right after. The
    observer never breaks the observed (the rule of the activity regulator,
    `maestro.providers.activite`): a callback that raises is swallowed, the measure
    stays counted and the control is still consulted.
    """

    def __init__(
        self,
        *,
        spending_cap: SpendingControl | None = None,
        on_measure: Callable[[StepUsage], None] | None = None,
    ) -> None:
        self._total = StepUsage()
        self._spending_cap = spending_cap
        self._on_measure = on_measure

    @property
    def total(self) -> StepUsage:
        """Merge of the measures collected so far."""
        return self._total

    def add(self, usage: StepUsage) -> None:
        """Adds a measure to the total, calls the observer back, then consults the control."""
        self._total = self._total.merge(usage)
        if self._on_measure is not None:
            try:
                self._on_measure(self._total)
            except Exception:  # noqa: BLE001 — the observer never breaks the observed
                pass
        if self._spending_cap is not None:
            self._spending_cap.check(self._total)


#: Active collector of the current context (None outside any `collect_usage()`).
_COLLECTOR: ContextVar[UsageCollector | None] = ContextVar(
    "maestro_usage_collector", default=None
)


@contextmanager
def collect_usage(
    *,
    spending_cap: SpendingControl | None = None,
    on_measure: Callable[[StepUsage], None] | None = None,
) -> Iterator[UsageCollector]:
    """Opens a collector: the `report_usage` calls of the block accumulate in it.

    A nested block masks the enclosing collector (no double count); on exit, the
    previous collector is restored. `spending_cap` arms the spending guardrail (#9)
    on this block: at each measure, the control confronts the spending of the run
    with its cap, and the offending `report_usage` raises `SpendingCapExceeded` in
    the provider's caller. `on_measure` (#835) receives the running total of the
    block at each reported measure: the reading of a task in progress, the one seen
    while it is spent.
    """
    collector = UsageCollector(spending_cap=spending_cap, on_measure=on_measure)
    token = _COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _COLLECTOR.reset(token)


def current_usage() -> StepUsage:
    """The running total of the active collector, empty outside any `collect_usage()` (#835).

    Read only: this is what a caller reads at an instant when no measure arrives
    (when an attempt opens, for instance) to say what the step has already
    consumed. Outside a collector, an empty measure rather than an error: the
    question "what has been spent so far?" then has the answer "nothing measured",
    which `StepUsage()` says exactly (unknown cost, zero calls).
    """
    collector = _COLLECTOR.get()
    return collector.total if collector is not None else StepUsage()


def report_usage(usage: StepUsage) -> None:
    """Reports the usage of a model call to the active collector (no-op without one).

    This is the entry point of providers: called after each model call, without
    their having to know whether anyone listens. If the active collector is capped
    (#9) and this report makes the spending of the run cross its cap (#56), the
    call raises `SpendingCapExceeded`: this is how the guardrail stops the step.
    """
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.add(usage)


#: Who records a spend of the **run**, one no task made (#1398). Receives the step
#: (a run step of `maestro.telemetry.costs`), its readable name and the measure.
RunSpendRecorder = Callable[[str, str, StepUsage], None]

#: The active recorder of the current context (None outside any `run_spend_channel()`).
_RUN_SPEND_RECORDER: ContextVar[RunSpendRecorder | None] = ContextVar(
    "maestro_run_spend_recorder", default=None
)


@contextmanager
def run_spend_channel(recorder: RunSpendRecorder) -> Iterator[None]:
    """Opens a channel for the run's spending made **during** a task (#1398).

    The collector follows the context, and that is its strength: an agent session,
    its asyncio subtasks and its retries all report to the task that launched them.
    It is also what would charge the first task of a run with what it did not
    spend: the **startup probe** (#1304) runs in an asyncio task that the agent
    session creates, so under its collector, and the ≈ 20 k tokens of the probe
    would fall into `socle-nextjs` on p5. This channel is the other address: what is
    reported to it (`report_run_usage`) goes to the recorder, which writes it on a
    line of the **run**, and enters no task collector.

    Opened by the engine around each task (`maestro.engine.executor`), which holds
    the run journal. Like the collector, it propagates through `await` and to the
    asyncio tasks created in the block.
    """
    token = _RUN_SPEND_RECORDER.set(recorder)
    try:
        yield
    finally:
        _RUN_SPEND_RECORDER.reset(token)


def report_run_usage(step: str, name: str, usage: StepUsage) -> None:
    """Reports a run spend (`step`, declared in `costs`) to the active recorder.

    **Without a recorder, the measure falls back into the active collector**
    (`report_usage`): a caller that keeps no run journal (a provider called outside
    the engine) sees it where it measures. A misfiled figure can be corrected; a
    lost figure cannot be found again. An empty measure is reported to nobody.
    """
    if not (usage.calls or usage.total_tokens or usage.turns or usage.cost_usd):
        return
    recorder = _RUN_SPEND_RECORDER.get()
    if recorder is None:
        report_usage(usage)
        return
    recorder(step, name, usage)
