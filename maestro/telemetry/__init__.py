"""Maestro telemetry: logging of steps and costs (ticket #8).

Six building blocks, assembled by the orchestration loop (`maestro.engine`):

- `StepUsage` + `collect_usage`/`report_usage`: the usage measurement of a model
  call (tokens, cost, duration, tools) and its per-context reporting channel —
  providers signal, the caller collects, without changing the signature of
  `ModelProvider`;
- `redact_secrets`: secret redaction before any logging — `register_secret`
  (#109) adds to it, on the fly, the secrets actually served (per-agent vault,
  resolved `${VAR}` references);
- `RunJournal`/`StepRecord`: the structured journal of a run — one JSON line per
  step on the `maestro.trace` logger — a base preparing the future Langfuse
  integration;
- `RunCost`/`TaskCost`: the per-task accounting of a run (#55) — the journal
  reorganized into a ledger: one entry per task (tokens, cost, duration), the
  run aggregate, no price evaluated here (pricing on the `ModelProvider` side,
  #32);
- `SpendingCap`: the spending cap (#9) backed by this ledger (#56) — consulted
  by the collector at each measurement, it rereads the accounting of the run
  (single source of the cost, no parallel counter) and raises
  `SpendingCapExceeded` when a cap in USD **or in tokens** is exceeded (#113,
  working even on a provider with no reported cost);
  `spending_control_summary` tells the operator which of the two controls holds;
- `enable_langfuse_export`: the Langfuse exporter of the journal (#81) —
  installed as a handler on the `maestro.trace` logger, it turns each run into a
  Langfuse trace (one observation per step, tokens and costs #55 in native
  format); purely configurative, no-op without Langfuse keys in the environment,
  and **idempotent** — the logger is global and the entry points enable it on
  every invocation without ever removing it (#195).
  `score_run_langfuse` (#80) completes the export at the end of a run: the
  scores derived from the journal (overall success, rate of successful tasks) go
  to the trace of the run — same switch, same resilience.
"""

from __future__ import annotations

from maestro.telemetry.costs import (
    STEP_ACCEPTANCE,
    STEP_ASSESSMENT,
    STEP_BRIEF,
    STEP_CADENCE,
    STEP_PLANNING,
    STEP_PROBE,
    STEP_SPENDING_CAP,
    STEP_TEAM,
    RunCost,
    SpendingCap,
    SpendingCapExceeded,
    TaskCost,
    interval_from,
    spending_control_summary,
    union_ms,
)
from maestro.telemetry.journal import (
    LOGGER_NAME,
    SUFFIXE_ETAPE_USAGE,
    RunJournal,
    StepRecord,
    est_releve_usage,
)
from maestro.telemetry.langfuse import (
    enable_langfuse_export,
    score_run_langfuse,
    scores_from_journal,
)
from maestro.telemetry.redact import SECRET_MARKER, redact_secrets, register_secret
from maestro.telemetry.usage import (
    RunSpendRecorder,
    StepUsage,
    UsageCollector,
    collect_usage,
    current_usage,
    report_run_usage,
    report_usage,
    run_spend_channel,
)

__all__ = [
    "LOGGER_NAME",
    "SECRET_MARKER",
    "STEP_ACCEPTANCE",
    "STEP_ASSESSMENT",
    "STEP_BRIEF",
    "STEP_CADENCE",
    "STEP_PLANNING",
    "STEP_PROBE",
    "STEP_SPENDING_CAP",
    "STEP_TEAM",
    "SUFFIXE_ETAPE_USAGE",
    "RunCost",
    "RunJournal",
    "RunSpendRecorder",
    "SpendingCap",
    "SpendingCapExceeded",
    "StepRecord",
    "StepUsage",
    "TaskCost",
    "UsageCollector",
    "collect_usage",
    "current_usage",
    "enable_langfuse_export",
    "est_releve_usage",
    "interval_from",
    "redact_secrets",
    "register_secret",
    "report_run_usage",
    "report_usage",
    "run_spend_channel",
    "score_run_langfuse",
    "scores_from_journal",
    "spending_control_summary",
    "union_ms",
]
