"""Tests of telemetry (#8): usage measure, collector, redaction, journal.

No network call. Covers the acceptance criteria: the cost of each step is
measured, aggregated and traceable (run_id); journaled texts are redacted of
secrets (environment values as well as key patterns).

Also covers the per-task cost tracking (#49):

- **per-task accounting** (#55): `RunCost.from_journal` assigns each line of the
  journal to its task (side steps included), exactly once (the total matches
  `RunJournal.usage_totale`), and exports to JSON (the shape of the API, #57);
- **spending cap** (#56): `SpendingCap` rereads that ledger at each check (no
  parallel counter) and raises `SpendingCapExceeded` when the cap is crossed,
  relayed by `report_usage` to the caller of the provider, the faulty measure
  staying counted (the cost stays visible).

And **the time** these two objects report (#989):

- the duration of a task is its **working time**: the wall clock minus its
  waits (approval #584, agent slot #86, project workshop #839), which read apart
  and each keep their own name;
- the duration of a **run** is the **union** of the intervals of its steps, never
  their sum: two tasks run side by side occupy it only once.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from maestro.telemetry import (
    LOGGER_NAME,
    SECRET_MARKER,
    STEP_PLANNING,
    RunCost,
    RunJournal,
    SpendingCap,
    SpendingCapExceeded,
    StepUsage,
    collect_usage,
    interval_from,
    redact_secrets,
    report_usage,
    spending_control_summary,
    union_ms,
)

# --- StepUsage: aggregation ------------------------------------------------------------


def test_merge_adds_up_counters_and_joins_tools():
    a = StepUsage(
        calls=1, input_tokens=100, output_tokens=10, cost_usd=0.01,
        api_duration_ms=1000, turns=2, tools=("Read", "Write"),
    )
    b = StepUsage(
        calls=1, input_tokens=50, output_tokens=5, cost_usd=0.02,
        api_duration_ms=500, turns=1, tools=("Write", "Bash"),
    )
    total = a.merge(b)

    assert total.calls == 2
    assert total.input_tokens == 150
    assert total.output_tokens == 15
    assert total.total_tokens == 165
    assert total.cost_usd == pytest.approx(0.03)
    assert total.api_duration_ms == 1500
    assert total.turns == 3
    assert total.tools == ("Read", "Write", "Bash")


def test_merge_keeps_an_absent_cost():
    # A provider that reports no cost leaves cost_usd at None (unknown ≠ 0).
    assert StepUsage().merge(StepUsage()).cost_usd is None
    assert StepUsage(cost_usd=0.5).merge(StepUsage()).cost_usd == 0.5


def test_with_duration_sets_the_wall_clock_duration():
    usage = StepUsage(calls=1).with_duration(1234)
    assert usage.duration_ms == 1234
    assert usage.calls == 1  # the rest of the measure is kept


def test_short_summary_with_and_without_provider_usage():
    without_usage = StepUsage().with_duration(2000).short_summary()
    assert "aucun usage fournisseur rapporté" in without_usage
    assert "2.0 s" in without_usage

    with_usage = StepUsage(calls=1, input_tokens=10, output_tokens=5, cost_usd=0.0123)
    summary = with_usage.with_duration(500).short_summary()
    assert "15 tokens" in summary
    assert "0.0123 $" in summary

    # Without a reported cost, the summary shows it as unknown, not as zero.
    assert "coût n/d" in StepUsage(calls=1).short_summary()


# --- Context collector -----------------------------------------------------------------


def test_report_usage_outside_a_collector_does_nothing():
    report_usage(StepUsage(calls=1))  # must not raise


def test_collect_usage_gathers_then_closes_the_channel():
    with collect_usage() as collector:
        report_usage(StepUsage(calls=1, input_tokens=10, cost_usd=0.01))
        report_usage(StepUsage(calls=1, output_tokens=5, cost_usd=0.02))

    assert collector.total.calls == 2
    assert collector.total.total_tokens == 15
    assert collector.total.cost_usd == pytest.approx(0.03)

    # Outside the block, nothing is gathered any more.
    report_usage(StepUsage(calls=1))
    assert collector.total.calls == 2


def test_nested_collect_usage_masks_the_enclosing_collector():
    # No double counting: a nested block alone captures the measures of its step.
    with collect_usage() as outer:
        with collect_usage() as inner:
            report_usage(StepUsage(calls=1))
        assert inner.total.calls == 1
        assert outer.total.calls == 0


# --- Secret redaction ------------------------------------------------------------------


def test_redact_masks_environment_values(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-tres-secrete-123")
    text = redact_secrets("la clé est cle-tres-secrete-123, la voilà")
    assert "cle-tres-secrete-123" not in text
    assert SECRET_MARKER in text


def test_redact_masks_key_patterns_outside_the_environment(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    text = redact_secrets("export ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij")
    assert "sk-ant-api03-abcdefghij" not in text
    assert SECRET_MARKER in text


def test_redact_masks_a_whole_private_key_block():
    # An SSH or TLS key is recognized by its block, not by a prefix. Sources are
    # read by their content (#1163), so an `id_rsa` without an extension comes in
    # as text, and this pattern is what stops it (#1264).
    key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAsecret1264\nligne2secrete\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = redact_secrets(f"avant\n{key}\naprès")
    assert "secret1264" not in text
    assert "ligne2secrete" not in text
    assert text == f"avant\n{SECRET_MARKER}\naprès"


def test_redact_masks_a_private_key_cut_before_its_end():
    # A truncated read can cut the block before its `END` line: what follows the
    # header is masked to the end rather than left in clear.
    text = redact_secrets("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE-coupee")
    assert "b3BlbnNzaC1rZXktdjE" not in text
    assert SECRET_MARKER in text


def test_redact_leaves_a_certificate_and_a_public_key_intact():
    # A certificate or a public key is not a secret: masking them would remove
    # from the context what a deliverable publishes on purpose.
    public = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYI\n-----END PUBLIC KEY-----"
    certificate = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmg\n-----END CERTIFICATE-----"
    assert redact_secrets(public) == public
    assert redact_secrets(certificate) == certificate


def test_redact_leaves_ordinary_text_intact():
    assert redact_secrets("bonjour le monde") == "bonjour le monde"
    assert redact_secrets("") == ""


# --- Journal d'exécution ---------------------------------------------------------------


def _consigne(journal, **surcharges):
    """Consigne une étape par défaut, surchargée champ à champ par le test."""
    champs = {
        "etape": "t1",
        "nom": "Tâche 1",
        "agent": "dev",
        "role": "Développeur",
        "statut": "terminee",
        "entree": "fais X",
        "sortie": "fait",
        "usage": StepUsage(),
    }
    champs.update(surcharges)
    return journal.consigne(**champs)


def test_journal_emet_une_ligne_json_par_etape(caplog):
    journal = RunJournal(run_id="run-test")
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _consigne(journal, usage=StepUsage(calls=1, cost_usd=0.01).with_duration(10))

    assert len(caplog.messages) == 1
    ligne = json.loads(caplog.messages[0])
    assert ligne["run_id"] == "run-test"
    assert ligne["etape"] == "t1"
    assert ligne["statut"] == "terminee"
    assert ligne["horodatage"]
    assert ligne["usage"]["cout_usd"] == 0.01
    assert ligne["usage"]["duree_ms"] == 10


def test_journal_expurge_les_secrets(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-super-secrete-42")
    journal = RunJournal()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        record = _consigne(
            journal,
            statut="echec",
            entree="utilise cle-super-secrete-42 pour appeler l'API",
            sortie="clé sk-ant-api03-abcdef123456 employée",
            erreur="rejet de cle-super-secrete-42",
        )

    assert "cle-super-secrete-42" not in record.entree
    assert "sk-ant-api03-abcdef123456" not in record.sortie
    assert "cle-super-secrete-42" not in (record.erreur or "")
    assert "cle-super-secrete-42" not in caplog.text
    assert "sk-ant-api03-abcdef123456" not in caplog.text
    assert SECRET_MARKER in caplog.text


def test_usage_totale_agrege_les_etapes():
    journal = RunJournal()
    _consigne(journal, etape="t1", usage=StepUsage(calls=1, cost_usd=0.01))
    _consigne(journal, etape="t2", usage=StepUsage(calls=2, cost_usd=0.02))

    assert [r.etape for r in journal.records] == ["t1", "t2"]
    assert journal.usage_totale.calls == 3
    assert journal.usage_totale.cost_usd == pytest.approx(0.03)


def test_chaque_journal_recoit_un_run_id_distinct():
    assert RunJournal().run_id != RunJournal().run_id


# --- Per-task accounting (#55) ---------------------------------------------------------


def test_from_journal_assigns_each_line_to_its_task():
    journal = RunJournal(run_id="run-compta")
    _consigne(journal, etape=STEP_PLANNING, nom="Planification",
              agent="orchestrateur", role="Orchestrateur",
              usage=StepUsage(calls=1, cost_usd=0.01))
    _consigne(journal, etape="t1", usage=StepUsage(
        calls=1, input_tokens=100, output_tokens=10, cost_usd=0.02))
    _consigne(journal, etape="t2", nom="Tâche 2", agent="qa", role="QA / Testeur",
              usage=StepUsage(calls=1, cost_usd=0.04))
    # Side step of t1 (human approval): attached to its task, not an entry of its own.
    _consigne(journal, etape="t1:validation", statut="approuve",
              usage=StepUsage(cost_usd=0.005))

    cost = RunCost.from_journal(journal)

    assert cost.run_id == "run-compta"
    assert cost.planning.cost_usd == pytest.approx(0.01)
    # One entry per task, in the order of first appearance in the journal.
    assert [t.task_id for t in cost.tasks] == ["t1", "t2"]
    t1, t2 = cost.tasks
    assert t1.name == "Tâche 1" and t1.agent == "dev"  # the identity comes from the task step
    assert t1.usage.cost_usd == pytest.approx(0.025)  # step and side step merged
    assert t2.usage.cost_usd == pytest.approx(0.04)
    # Each line counted exactly once: the total matches the journal's.
    assert cost.total.cost_usd == pytest.approx(journal.usage_totale.cost_usd)
    assert cost.total.calls == journal.usage_totale.calls


def test_a_task_known_only_by_its_side_step_stays_without_identity():
    # Partial accounting during a run: a side step can come before the task step.
    journal = RunJournal()
    _consigne(journal, etape="t1:message", statut="envoye",
              usage=StepUsage(calls=1, cost_usd=0.01))

    (task,) = RunCost.from_journal(journal).tasks

    assert task.task_id == "t1"
    assert task.name == "" and task.agent == "" and task.status == ""
    assert task.usage.cost_usd == pytest.approx(0.01)  # the usage is already counted


def test_an_unknown_cost_stays_unknown_in_the_accounting():
    # A provider that reports no cost leaves the task at a None cost (≠ 0).
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(calls=1))

    cost = RunCost.from_journal(journal)

    assert cost.tasks[0].usage.cost_usd is None
    assert cost.total.cost_usd is None


def test_the_accounting_exports_to_json():
    journal = RunJournal(run_id="run-json")
    _consigne(journal, etape=STEP_PLANNING, nom="Planification",
              agent="orchestrateur", role="Orchestrateur",
              usage=StepUsage(calls=1, cost_usd=0.01))
    _consigne(journal, etape="t1", usage=StepUsage(calls=1, input_tokens=7, cost_usd=0.02))

    shape = RunCost.from_journal(journal).to_dict()

    assert shape["run_id"] == "run-json"
    assert shape["planification"]["cout_usd"] == pytest.approx(0.01)
    assert shape["total"]["cout_usd"] == pytest.approx(0.03)
    (t1,) = shape["taches"]
    assert t1["tache_id"] == "t1" and t1["usage"]["tokens_entree"] == 7
    json.dumps(shape)  # the shape of the API (#57): JSON-serializable end to end


# --- Spending cap (#56) ----------------------------------------------------------------


def test_the_cap_rereads_the_accounting_at_each_check():
    journal = RunJournal()
    spending_cap = SpendingCap(journal, max_cost_usd=0.03)
    spending_cap.check(StepUsage(cost_usd=0.02))  # 0.02 ≤ 0.03: nothing to report

    _consigne(journal, usage=StepUsage(cost_usd=0.02))

    # Same measure in progress, but the ledger has moved: the control sees it
    # (no internal counter: telemetry is the single source of the cost).
    with pytest.raises(SpendingCapExceeded) as exc:
        spending_cap.check(StepUsage(cost_usd=0.02))
    assert "plafond de dépense dépassé" in str(exc.value)


def test_reaching_the_cap_without_crossing_it_stops_nothing():
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(cost_usd=0.01))
    SpendingCap(journal, max_cost_usd=0.01).check(StepUsage())  # does not raise


def test_an_unknown_cost_cannot_be_capped():
    # A provider silent on the cost: the spend is unknown and the cap *in USD* has
    # no hold (the token cap, however, does cap: see the #113 tests below).
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(calls=3))
    SpendingCap(journal, max_cost_usd=0.0001).check(StepUsage(calls=1))


def test_an_invalid_cap_is_refused():
    for invalid in (0, -1.5):
        with pytest.raises(ValueError):
            SpendingCap(RunJournal(), invalid)


# --- Token cap: effective without a reported cost (#113) -------------------------------


def test_the_token_cap_stops_a_provider_without_a_reported_cost():
    # The heart of #113: the cost is unknown (None), but tokens are always
    # reported, so the token cap caps where the cap in USD would have no hold.
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(calls=1, output_tokens=800, cost_usd=None))
    spending_cap = SpendingCap(journal, max_tokens=1000)

    # 800 (already recorded) + 150 in progress ≤ 1000: nothing to report.
    spending_cap.check(StepUsage(input_tokens=150))

    # 800 + 300 > 1000: the guardrail stops, even though the cost stays unknown.
    with pytest.raises(SpendingCapExceeded) as exc:
        spending_cap.check(StepUsage(input_tokens=300))
    assert "plafond de tokens dépassé" in str(exc.value)


def test_both_caps_coexist_and_the_first_crossed_stops():
    # Cost and tokens armed together: the known cost decides first, then the tokens.
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(calls=1, output_tokens=10, cost_usd=0.05))
    spending_cap = SpendingCap(journal, max_cost_usd=0.04, max_tokens=10_000)
    with pytest.raises(SpendingCapExceeded) as exc:
        spending_cap.check(StepUsage())
    assert "plafond de dépense dépassé" in str(exc.value)  # the cost, not the tokens


def test_an_invalid_token_cap_is_refused():
    for invalid in (0, -3):
        with pytest.raises(ValueError):
            SpendingCap(RunJournal(), max_tokens=invalid)


def test_a_control_without_any_cap_is_refused():
    with pytest.raises(ValueError):
        SpendingCap(RunJournal())


# --- spending_control_summary: which control actually held (#113) ----------------------


def test_summary_says_real_cost_when_the_provider_reports_a_cost():
    summary = spending_control_summary(1.0, None, StepUsage(cost_usd=0.5, output_tokens=42))
    assert "coût réel" in summary and "0.5000/1.0000 $" in summary


def test_summary_flags_a_cost_cap_without_hold():
    # The case #113 targets: a cap in USD armed, a provider silent on the cost.
    summary = spending_control_summary(1.0, None, StepUsage(calls=1, output_tokens=42))
    assert "SANS PRISE" in summary
    assert "--plafond-tokens" in summary


def test_summary_announces_the_active_token_cap():
    summary = spending_control_summary(None, 1000, StepUsage(calls=1, output_tokens=200))
    assert "tokens hors cache relu (200/1000)" in summary


def test_summary_shows_the_cost_cap_inoperative_when_tokens_take_over():
    summary = spending_control_summary(1.0, 1000, StepUsage(calls=1, output_tokens=200))
    assert "tokens hors cache relu (200/1000)" in summary
    assert "coût inopérant" in summary


def test_summary_without_any_cap():
    assert spending_control_summary(None, None, StepUsage()) == "aucun plafond armé"


def test_report_usage_raises_at_the_caller_when_the_cap_is_crossed():
    # The measuring channel relays the guardrail (#9): the faulty report raises,
    # but the measure is already counted, so the cost of the overrun stays visible.
    spending_cap = SpendingCap(RunJournal(), max_cost_usd=0.01)
    with collect_usage(spending_cap=spending_cap) as collector:
        report_usage(StepUsage(calls=1, cost_usd=0.008))
        with pytest.raises(SpendingCapExceeded):
            report_usage(StepUsage(calls=1, cost_usd=0.008))

    assert collector.total.calls == 2
    assert collector.total.cost_usd == pytest.approx(0.016)


# --- The duration of a task is its working time (#989) ---------------------------------


def test_the_work_duration_removes_the_three_waits():
    # The case of the retex of 2026-09-11 (G4), figures included: 24 min 17 s
    # announced, of which 13 min spent waiting for its turn: ~11 min of work remain.
    usage = StepUsage().with_duration(
        1_457_000,
        approval_ms=12_000,
        slot_wait_ms=48_000,
        workshop_wait_ms=780_000,
    )

    assert usage.wait_duration_ms == 840_000
    assert usage.work_duration_ms == 617_000


def test_an_unmeasured_wait_is_not_zero():
    # The distinction `cost_usd` makes, and for the same reason: an absent
    # measure is not a zero measure. A caller that measures no wait (queues,
    # planning loop) does not declare there was none, and its work duration
    # stays its wall clock.
    unmeasured = StepUsage().with_duration(5_000)
    assert unmeasured.wait_duration_ms is None
    assert unmeasured.work_duration_ms == 5_000

    # Measured at zero, on the other hand, the wait is a fact: someone looked.
    measured = StepUsage().with_duration(5_000, slot_wait_ms=0, workshop_wait_ms=0)
    assert measured.wait_duration_ms == 0
    assert measured.work_duration_ms == 5_000


def test_the_work_duration_never_goes_negative():
    # The measures come from clocks that do not start at the same instant:
    # bounded at zero rather than returning a negative duration.
    usage = StepUsage().with_duration(1_000, workshop_wait_ms=4_000)
    assert usage.work_duration_ms == 0


def test_the_waits_travel_in_json_with_their_derived_values():
    usage = StepUsage().with_duration(
        10_000, approval_ms=1_000, slot_wait_ms=2_000, workshop_wait_ms=3_000
    )
    shape = usage.to_dict()

    assert shape["duree_attente_creneau_ms"] == 2_000
    assert shape["duree_attente_atelier_ms"] == 3_000
    # The derived values travel computed, like `tokens_total`: the screen does
    # not rewrite the rule "what is work, what is waiting".
    assert shape["duree_attente_ms"] == 6_000
    assert shape["duree_execution_ms"] == 4_000
    # …and are ignored on the way back: the round trip stays faithful.
    assert StepUsage.from_dict(shape) == usage


def test_a_journal_line_from_before_989_reads_back_without_a_measured_wait():
    # Unknown, not zero: nothing tells after the fact what a task waited for,
    # so its work duration stays its wall clock.
    reread = StepUsage.from_dict({"duree_ms": 4_000})
    assert reread.slot_wait_duration_ms is None
    assert reread.wait_duration_ms is None
    assert reread.work_duration_ms == 4_000


def test_the_short_summary_names_each_wait_and_omits_the_zero_ones():
    summary = StepUsage(calls=1).with_duration(
        20_000, approval_ms=0, slot_wait_ms=0, workshop_wait_ms=8_000
    ).short_summary()

    assert "d'atelier de projet" in summary
    # The two zero waits are not announced: "of which 0.0 s" on every task of a
    # run would teach the reader to skip the mention (#584).
    assert "d'arbitrage" not in summary
    assert "de file d'agent" not in summary


# --- The duration of a run is the union of its intervals (#989) ------------------------


def _interval(start_s: float, end_s: float) -> tuple[datetime, datetime]:
    """An interval in seconds from a fixed origin, so that the test reads easily."""
    origin = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    return (origin + timedelta(seconds=start_s), origin + timedelta(seconds=end_s))


def test_the_union_does_not_count_an_overlap_twice():
    # Adding up the durations announces 47 min for 43.5 min of wall clock (#497).
    # Two tasks of 60 s that overlap by 30 s occupy 90 s, not 120.
    assert union_ms([_interval(0, 60), _interval(30, 90)]) == 90_000


def test_the_union_adds_up_disjoint_intervals():
    # The other half: without overlap, the union **is** the sum, and the gap
    # between the two is not occupied time.
    assert union_ms([_interval(0, 10), _interval(40, 50)]) == 20_000


def test_the_union_absorbs_a_fully_contained_interval():
    # A short task during a long one adds nothing, and shortens nothing.
    assert union_ms([_interval(0, 100), _interval(20, 30)]) == 100_000


def test_the_union_ignores_the_order_in_which_steps_are_recorded():
    # `RunJournal.records` follows the order of **completion**: tasks run side by
    # side arrive there out of the order of their starts.
    out_of_order = [_interval(30, 90), _interval(0, 60)]
    assert union_ms(out_of_order) == 90_000


def test_the_union_without_any_interval_is_unknown_not_zero():
    assert union_ms([]) is None


def test_an_interval_derives_from_the_timestamp_and_the_duration():
    start, end = interval_from("2026-09-20T10:00:40+00:00", 40_000)
    assert end - start == timedelta(seconds=40)
    assert start == datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "timestamp, duration_ms",
    [
        ("2026-09-20T10:00:40+00:00", None),  # nothing measured
        ("2026-09-20T10:00:40+00:00", 0),  # zero duration: no occupied time
        ("2026-09-20T10:00:40+00:00", -5),  # clocks out of step
        ("pas une date", 40_000),  # unreadable timestamp
        ("", 40_000),  # missing timestamp
    ],
)
def test_a_step_without_a_readable_interval_does_not_skew_the_union(timestamp, duration_ms):
    assert interval_from(timestamp, duration_ms) is None


def test_the_ledger_returns_the_union_not_the_sum_of_durations():
    # End to end, on a real journal: two one-minute tasks recorded back to back
    # overlap almost entirely. The sum would announce two minutes; the run is
    # occupied for one.
    journal = RunJournal()
    _consigne(journal, etape="t1", usage=StepUsage(calls=1).with_duration(60_000))
    _consigne(journal, etape="t2", usage=StepUsage(calls=1).with_duration(60_000))
    cost = RunCost.from_journal(journal)

    # The counters, however, do add up: two tasks run side by side cost twice.
    assert cost.total.calls == 2
    # The duration does not. One second of margin: the two records can fall on
    # either side of a clock tick (timestamps to the second).
    assert cost.wall_clock_ms is not None
    assert 60_000 <= cost.wall_clock_ms <= 61_000
    assert cost.total.duration_ms == cost.wall_clock_ms


def test_the_run_total_carries_no_wait():
    # A sum of the waits of parallel tasks is not a wait of the run, and
    # subtracting it from a union would give a "work of the run" that means
    # nothing. At the level of the run, the duration is a single measure.
    journal = RunJournal()
    _consigne(
        journal,
        etape="t1",
        usage=StepUsage(calls=1).with_duration(60_000, workshop_wait_ms=30_000),
    )
    total = RunCost.from_journal(journal).total

    assert total.wait_duration_ms is None
    assert total.work_duration_ms == total.duration_ms


def test_a_resume_step_does_not_occupy_the_run():
    # A run marker (#96), attached to no task: it does not count in the ledger,
    # so it occupies nothing either.
    journal = RunJournal()
    _consigne(journal, etape="reprise", usage=StepUsage().with_duration(90_000))
    assert RunCost.from_journal(journal).wall_clock_ms is None
