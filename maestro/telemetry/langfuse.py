"""Langfuse exporter of the run journal — traces and costs (ticket #81).

The journal (#8) records each step of a run as JSON Lines on the
`maestro.trace` logger, in a format designed to map onto Langfuse traces and
observations: this exporter is that link. Installed as a `logging.Handler`
(same pattern as the Control Tower bridge, `maestro.controltower.bridge`), it
converts each recorded line into events of the Langfuse ingestion API
(`POST /api/public/ingestion`):

- the run becomes a **trace** (id = `run_id`: the trace is found from the
  report, the journal or the Control Tower), its input is the objective carried
  by the planning step;
- each step becomes an **observation**: a *generation* if it consumed model
  calls — tokens and cost from the per-task accounting (#55) in Langfuse's
  native format, so visible per observation and aggregated per trace —, a
  *span* otherwise (human validation, inter-agent message, block). Agent, role,
  status and called tools go out as metadata; the measured duration (#8)
  places the step in the timeline.

The integration is **optional**: without `LANGFUSE_PUBLIC_KEY` +
`LANGFUSE_SECRET_KEY` in the config, `enable_langfuse_export` installs nothing
and the system behaves identically. The export speaks the public HTTP API
through the httpx client already present — same choice as the OpenAI-compatible
provider (#69): no dedicated SDK for a stable dialect.

In queue mode (#41), the export is enabled on the **orchestrator** side only:
the result of each task (worker usage included) is already recorded there by
`CeleryExecutor` — enabling the workers too would count each task twice.

The **scoring** of runs (#80) completes the export: at the end of a run,
`score_run_langfuse` puts **scores** derived from the journal on the trace —
overall success and rate of successful tasks — through the same ingestion API,
usable in Langfuse to filter, aggregate and compare runs. Same configuration
switch, same resilience: without keys, nothing is sent; an unreachable Langfuse
never affects the run being scored.

The upstream journal already redacts secrets (`redact_secrets`): what goes to
Langfuse is what was already going into the logs.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maestro.config import Settings, load_settings
from maestro.telemetry.costs import STEP_PLANNING, RunCost
from maestro.telemetry.journal import LOGGER_NAME, RunJournal, est_releve_usage

#: Maximum delay of a publication (connection + response): the export is
#: synchronous (logging handler), an unreachable Langfuse slows the step down
#: but never suspends the run — the failure goes to `handleError`.
_TIMEOUT_S = 10.0

#: Name of the exported traces — one trace = one run, linked by `run_id`.
_TRACE_NAME = "maestro-run"

#: Langfuse level per step status (default: DEFAULT). `echec` is an error;
#: `bloquee` (#43) and `refuse` (#9) signal without being errors of the step.
_LEVELS = {"echec": "ERROR", "bloquee": "WARNING", "refuse": "WARNING"}

#: The only successful outcome of a task (journal status convention, see
#: `maestro.engine.executor.STATUT_TERMINEE`) — failure, block or refusal are not.
_SUCCESS_STATUS = "terminee"

#: Names of the run scores (#80), put on its trace: overall success (boolean)
#: and the share of successful tasks (0..1).
SCORE_RUN_SUCCEEDED = "run-reussi"
SCORE_SUCCESS_RATE = "taux-reussite"


def events_from_step(record: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Converts a journal line (`StepRecord.to_dict`) into ingestion events.

    Two events per line: the **trace upsert** of the run (id = `run_id` —
    successive upserts merge; only the planning step sets the trace timestamp
    and its input, the objective) and the step **observation** — a generation
    if it consumed model calls (tokens and cost in native format, #55), a span
    otherwise. An unreadable line (not a journal step) produces **no** event
    rather than a wrong one — the export is a mirror, not a source of truth.

    A **usage reading** (#835, `<task>:usage`) produces none either, for an
    accounting reason: it carries the running **total** of the current task,
    which its final step will export in full. Making it a generation would
    count each turn as many times as it was read — on a Langfuse trace, a tripled
    cost does not look like an error, it looks like a run three times as
    expensive.
    """
    step = record.get("etape")
    run_id = record.get("run_id")
    if not isinstance(step, str) or not step or not isinstance(run_id, str) or not run_id:
        return ()
    if est_releve_usage(step):
        return ()
    raw_usage = record.get("usage")
    usage: dict[str, Any] = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    end = str(record.get("horodatage", ""))
    start = _start_from(end, usage.get("duree_ms"))
    trace: dict[str, Any] = {"id": run_id, "name": _TRACE_NAME}
    if step == STEP_PLANNING:
        trace["timestamp"] = start
        trace["input"] = record.get("entree", "")
    status = str(record.get("statut", ""))
    error = record.get("erreur")
    observation: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "traceId": run_id,
        "name": str(record.get("nom") or step),
        "startTime": start,
        "endTime": end,
        "input": record.get("entree", ""),
        "output": record.get("sortie", ""),
        "level": _LEVELS.get(status, "DEFAULT"),
        "statusMessage": error if isinstance(error, str) else None,
        "metadata": {
            "etape": step,
            "agent": record.get("agent", ""),
            "role": record.get("role", ""),
            "statut": status,
            "outils": usage.get("outils", []),
            "usage": usage,
            "playbook_version": record.get("playbook_version"),
        },
    }
    calls = usage.get("appels")
    if isinstance(calls, int) and calls > 0:
        observation_type = "generation-create"
        observation["usage"] = _langfuse_usage(usage)
    else:
        observation_type = "span-create"
    return (
        _event("trace-create", end, trace),
        _event(observation_type, end, observation),
    )


def _event(event_type: str, timestamp: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Langfuse ingestion envelope: unique event id (server-side deduplication)."""
    return {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "timestamp": timestamp,
        "body": dict(body),
    }


def _langfuse_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    """The native `usage` block of a Langfuse generation: tokens, and cost if known.

    The cost is the one reported by the provider through the telemetry (#55) —
    absent (None) when the provider reports none, never estimated here.
    """
    block: dict[str, Any] = {
        "input": int(usage.get("tokens_entree") or 0),
        "output": int(usage.get("tokens_sortie") or 0),
        "unit": "TOKENS",
    }
    cost = usage.get("cout_usd")
    if isinstance(cost, int | float):
        block["totalCost"] = float(cost)
    return block


def _start_from(timestamp: str, duration_ms: Any) -> str:
    """The start timestamp of a step: its recorded end minus its measured duration.

    The journal records on completion (`horodatage` = end of step); the wall
    clock duration (#8) places the start back — the extent of the observation in
    the Langfuse timeline. Without a duration (ancillary step with null usage) or
    with an unreadable timestamp, the start falls back on the end (point-in-time
    observation).
    """
    if not isinstance(duration_ms, int | float) or duration_ms <= 0:
        return timestamp
    try:
        end = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    return (end - timedelta(milliseconds=duration_ms)).isoformat(timespec="seconds")


class LangfuseExportHandler(logging.Handler):
    """Handler of the `maestro.trace` logger: each recorded line goes to Langfuse.

    `publish` is a **synchronous** callable (the journal records synchronously);
    in production it is the ingestion `POST` of `langfuse_publisher`. A line that
    cannot be converted or a failed publication is reported through
    `handleError` (silent by default, like any logging handler): the export must
    never make the run it observes fail.
    """

    def __init__(self, publish: Callable[[Sequence[dict[str, Any]]], None]) -> None:
        super().__init__(level=logging.INFO)
        self._publish = publish

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = json.loads(record.getMessage())
            if not isinstance(data, dict):
                return
            events = events_from_step(data)
            if events:
                self._publish(events)
        except Exception:
            self.handleError(record)


def langfuse_publisher(settings: Settings) -> Callable[[Sequence[dict[str, Any]]], None]:
    """Builds the (synchronous) HTTP publishing callable towards Langfuse.

    **Synchronous** httpx client (the logging handler is), authenticated with
    Basic (public key / secret key) on the batch ingestion API of
    `LANGFUSE_HOST`. The connection is lazy (opened on first send) and each
    send is bounded (`_TIMEOUT_S`).
    """
    # Local import: only publishing depends on the HTTP client (see Redis bridge).
    import httpx2

    client = httpx2.Client(
        auth=(settings.langfuse_public_key or "", settings.langfuse_secret_key or ""),
        timeout=_TIMEOUT_S,
    )
    url = f"{settings.langfuse_host.rstrip('/')}/api/public/ingestion"

    def publish(events: Sequence[dict[str, Any]]) -> None:
        client.post(url, json={"batch": list(events)}).raise_for_status()

    return publish


def enable_langfuse_export(settings: Settings | None = None) -> logging.Handler | None:
    """Installs the Langfuse export if the config asks for it; otherwise does nothing (None).

    The switch is purely configurative (optional integration, #81): without the
    two keys (`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`), no handler is
    installed and the system behaves identically. With them, a
    `LangfuseExportHandler` is installed on the `maestro.trace` logger: each
    journaled run (#8) produces its Langfuse trace, costs included (#55).
    Enabled on the orchestrator side (see the module docstring); returns the
    installed handler (to remove via `logging.getLogger(LOGGER_NAME).removeHandler(...)`).

    **Idempotent** (#195): `maestro.trace` is a *global* logger and the entry
    points call this function on every invocation (`engine_cli.main`) without
    ever removing the handler. Without a guard, N calls in the same process
    attach N handlers, and each journaled line then goes N times to Langfuse:
    duplicated traces and costs, and as many synchronous POSTs (`_TIMEOUT_S`
    each) per recorded line. An already installed handler is therefore returned
    as is, without building a second one — changing `settings` mid-process
    means removing the handler first.
    """
    settings = settings or load_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for existing in logger.handlers:
        if isinstance(existing, LangfuseExportHandler):
            return existing
    handler = LangfuseExportHandler(langfuse_publisher(settings))
    logger.addHandler(handler)
    return handler


def scores_from_journal(journal: RunJournal) -> tuple[dict[str, Any], ...]:
    """Scores a recorded run: the `score-create` events of its trace (#80).

    The scoring rereads the per-task accounting (#55) — the telemetry is the
    single source of the outcome, no parallel verdict — and puts two scores on
    the run trace (`run_id`), usable in Langfuse (filters, aggregates, comparison
    between runs):

    - **`run-reussi`** (boolean): 1 if all tasks finished successfully, 0 as soon
      as one failed or was blocked (#43);
    - **`taux-reussite`** (numeric, 0..1): the share of successful tasks.

    The comment of each score details the count (successes, failures, blocks). A
    journal without any task (planning only, or failed) has nothing to score:
    no score rather than a wrong one.
    """
    tasks = RunCost.from_journal(journal).tasks
    if not tasks:
        return ()
    succeeded = sum(1 for t in tasks if t.status == _SUCCESS_STATUS)
    failures = sum(1 for t in tasks if t.status == "echec")
    blocked = sum(1 for t in tasks if t.status == "bloquee")
    comment = (
        f"{succeeded}/{len(tasks)} tâche(s) réussie(s) — "
        f"{failures} échec(s), {blocked} bloquée(s)."
    )
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    scores = (
        (SCORE_RUN_SUCCEEDED, "BOOLEAN", 1 if succeeded == len(tasks) else 0),
        (SCORE_SUCCESS_RATE, "NUMERIC", succeeded / len(tasks)),
    )
    return tuple(
        _event(
            "score-create",
            timestamp,
            {
                "id": str(uuid.uuid4()),
                "traceId": journal.run_id,
                "name": name,
                "value": value,
                "dataType": score_type,
                "comment": comment,
            },
        )
        for name, score_type, value in scores
    )


def score_run_langfuse(
    journal: RunJournal, settings: Settings | None = None
) -> tuple[dict[str, Any], ...]:
    """Publishes the scores of a run; no-op without Langfuse keys (#80).

    To call at the **end of a run** (report rendered, journal complete): the
    scores derived from the journal go to the trace the exporter (#81) built
    along the steps, through the same ingestion API. Same policy as the export:
    purely configurative (without the two keys, nothing is sent, identical
    behaviour — the degraded mode) and **resilient** (an unreachable Langfuse is
    reported as a warning on the module logger, never propagated to the run being
    scored). Returns the published events — empty if the integration is disabled,
    if there is nothing to score or if the publication failed.
    """
    settings = settings or load_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return ()
    events = scores_from_journal(journal)
    if not events:
        return ()
    try:
        langfuse_publisher(settings)(events)
    except Exception:
        logging.getLogger(__name__).warning(
            "Scores Langfuse non publiés (run %s) — l'exécution n'est pas affectée.",
            journal.run_id,
            exc_info=True,
        )
        return ()
    return events
