"""Tests of the Langfuse integration (parent #79): instrumentation and scoring.

No outgoing network call: the tests' "Langfuse" is a fake local HTTP server that
receives the ingestion API. Covers the criteria of the final batch (#80):

- **instrumentation** (#81, tests deferred here — docs/10 §5.1): each line of the
  journal (#8) becomes a Langfuse trace + observation (a generation with native
  tokens/cost #55, a span otherwise), statuses carry the levels, and the export —
  installed as a logging handler — never fails the run it observes;
- **scoring** (#80): at the end of a run, usable scores (overall success, rate of
  succeeded tasks) go onto the run's trace through the same ingestion API, derived
  from the per-task accounting (#55);
- **degraded mode without Langfuse**: without both keys, no handler is installed
  and no score is published — behaviour strictly identical;
- **idempotent activation** (#195): `maestro.trace` being a global logger that no
  caller cleans up, N activations leave a single handler — and the test suite
  itself never publishes to the real Langfuse (guard of `tests/conftest.py`).
"""

import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx2
import pytest

from maestro.config import Settings, load_settings
from maestro.telemetry import (
    LOGGER_NAME,
    STEP_PLANNING,
    RunJournal,
    StepUsage,
    enable_langfuse_export,
    score_run_langfuse,
    scores_from_journal,
)
from maestro.telemetry.langfuse import (
    SCORE_RUN_SUCCEEDED,
    SCORE_SUCCESS_RATE,
    LangfuseExportHandler,
    events_from_step,
    langfuse_publisher,
)


def _settings(**overrides) -> Settings:
    """A fake `Settings`; only the Langfuse fields matter here."""
    defaults = dict(
        anthropic_api_key=None,
        anthropic_model="claude-opus-4-8",
        claude_auth_mode=None,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _line(**overrides) -> dict:
    """A journal line (`StepRecord.to_dict`) ready to convert."""
    line = {
        "run_id": "run-lf",
        "etape": "t1",
        "nom": "Tâche 1",
        "agent": "dev",
        "role": "Développeur",
        "statut": "terminee",
        "horodatage": "2026-07-13T10:00:10+00:00",
        "entree": "fais X",
        "sortie": "fait",
        "erreur": None,
        "usage": StepUsage().to_dict(),
    }
    line.update(overrides)
    return line


def _record(journal: RunJournal, **overrides):
    """Records a default step, overridden field by field by the test."""
    fields = {
        "etape": "t1",
        "nom": "Tâche 1",
        "agent": "dev",
        "role": "Développeur",
        "statut": "terminee",
        "entree": "fais X",
        "sortie": "fait",
        "usage": StepUsage(),
    }
    fields.update(overrides)
    return journal.consigne(**fields)


# --- Journal → ingestion events conversion (#81) ----------------------------------------


def test_planning_upserts_the_trace_with_goal_and_timestamp():
    usage = StepUsage(appels=1, tokens_entree=100, tokens_sortie=10).avec_duree(10_000)
    line = _line(
        etape=STEP_PLANNING, nom="Planification de l'objectif",
        agent="orchestrateur", role="Orchestrateur",
        entree="construire un mini-CRM", sortie="2 tâche(s) planifiée(s)",
        usage=usage.to_dict(),
    )

    trace, observation = events_from_step(line)

    assert trace["type"] == "trace-create"
    # Only planning sets the trace's input and timestamp — the start of the step,
    # i.e. its recorded end minus the measured duration (#8).
    assert trace["body"]["id"] == "run-lf"
    assert trace["body"]["input"] == "construire un mini-CRM"
    assert trace["body"]["timestamp"] == "2026-07-13T10:00:00+00:00"
    assert observation["type"] == "generation-create"
    assert observation["body"]["traceId"] == "run-lf"
    # Ingestion envelopes: unique event id, timestamp carried.
    assert trace["id"] != observation["id"]
    assert trace["timestamp"] == "2026-07-13T10:00:10+00:00"


def test_a_task_with_model_calls_becomes_a_priced_generation():
    usage = StepUsage(
        appels=2, tokens_entree=100, tokens_sortie=25, cout_usd=0.0375, outils=("Read",)
    )
    line = _line(usage=usage.to_dict())

    trace, observation = events_from_step(line)

    # The trace upsert of an ordinary step rewrites neither input nor timestamp.
    assert "input" not in trace["body"] and "timestamp" not in trace["body"]
    assert observation["type"] == "generation-create"
    body = observation["body"]
    # Tokens and cost in the native Langfuse format (#55): visible per observation.
    assert body["usage"] == {
        "input": 100, "output": 25, "unit": "TOKENS", "totalCost": 0.0375,
    }
    assert body["name"] == "Tâche 1"
    assert body["level"] == "DEFAULT"
    assert body["metadata"]["agent"] == "dev"
    assert body["metadata"]["outils"] == ["Read"]


def test_the_playbook_version_travels_in_the_metadata():
    # Traceability of the version used (#78 → #75): visible in Langfuse too.
    _, observation = events_from_step(_line(playbook_version=3))
    assert observation["body"]["metadata"]["playbook_version"] == 3

    # Prompt from the code (playbook never edited, or a pre-#78 line): None, not 0.
    _, without_version = events_from_step(_line())
    assert without_version["body"]["metadata"]["playbook_version"] is None


def test_a_step_without_model_call_becomes_a_span():
    line = _line(etape="t1:validation", statut="approuve", usage=StepUsage().to_dict())

    _, observation = events_from_step(line)

    assert observation["type"] == "span-create"
    assert "usage" not in observation["body"]


def test_an_unknown_cost_stays_absent_from_the_native_usage():
    # A provider silent on cost (#55): no totalCost, rather than a false 0.
    line = _line(usage=StepUsage(appels=1, tokens_entree=10).to_dict())

    _, observation = events_from_step(line)

    assert "totalCost" not in observation["body"]["usage"]


@pytest.mark.parametrize(
    ("status", "level"),
    [("terminee", "DEFAULT"), ("echec", "ERROR"), ("bloquee", "WARNING"), ("refuse", "WARNING")],
)
def test_the_step_status_carries_the_langfuse_level(status, level):
    error = "boum" if status == "echec" else None
    _, observation = events_from_step(_line(statut=status, erreur=error))

    assert observation["body"]["level"] == level
    assert observation["body"]["statusMessage"] == error


def test_without_duration_or_with_an_unreadable_timestamp_the_observation_is_a_point():
    no_duration = _line(usage=StepUsage(appels=1).to_dict())
    _, obs = events_from_step(no_duration)
    assert obs["body"]["startTime"] == obs["body"]["endTime"]

    unreadable = _line(horodatage="pas-une-date", usage=StepUsage().avec_duree(500).to_dict())
    _, obs = events_from_step(unreadable)
    assert obs["body"]["startTime"] == "pas-une-date"


def test_an_unreadable_line_produces_no_event():
    # The export mirrors the journal: a foreign line fabricates nothing.
    assert events_from_step({}) == ()
    assert events_from_step({"etape": "t1"}) == ()  # run_id missing
    assert events_from_step(_line(etape="")) == ()
    assert events_from_step(_line(run_id=42)) == ()


# --- Logging handler: the journal goes to Langfuse without ever getting in its way ------


@pytest.fixture()
def trace_logger():
    """The journal's logger, set back to INFO then cleaned of any handler added."""
    logger = logging.getLogger(LOGGER_NAME)
    handlers_before = list(logger.handlers)
    logger.setLevel(logging.INFO)
    try:
        yield logger
    finally:
        for handler in list(logger.handlers):
            if handler not in handlers_before:
                logger.removeHandler(handler)


def test_the_handler_publishes_each_recorded_step(trace_logger):
    batches: list[list[dict]] = []
    trace_logger.addHandler(LangfuseExportHandler(lambda events: batches.append(list(events))))

    journal = RunJournal(run_id="run-handler")
    _record(journal, usage=StepUsage(appels=1))
    _record(journal, etape="t2", statut="echec", erreur="boum")

    assert len(batches) == 2  # one batch (trace + observation) per recorded step
    assert [e["type"] for e in batches[0]] == ["trace-create", "generation-create"]
    assert [e["type"] for e in batches[1]] == ["trace-create", "span-create"]
    # Everything attaches to the run's trace: by `traceId` (observations) or `id` (trace).
    assert all(e["body"].get("traceId", e["body"].get("id")) == "run-handler"
               for batch in batches for e in batch)


def test_a_non_journal_line_is_ignored_without_publishing(trace_logger, monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", False)  # silent handleError
    batches: list[list[dict]] = []
    trace_logger.addHandler(LangfuseExportHandler(lambda events: batches.append(list(events))))

    trace_logger.info("pas du JSON")  # unreadable: reported via handleError, without raising
    trace_logger.info('"du JSON, mais pas une étape"')

    assert batches == []


def test_a_publication_failure_does_not_break_recording(trace_logger, monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", False)

    def _failing_publish(events):
        raise ConnectionError("Langfuse injoignable")

    trace_logger.addHandler(LangfuseExportHandler(_failing_publish))
    journal = RunJournal()

    record = _record(journal)  # must not raise: the export never fails the run

    assert journal.records == (record,)


# --- HTTP publication and configuration switch ------------------------------------------


class _IngestionHandler(BaseHTTPRequestHandler):
    """Receives the Langfuse ingestion API and archives each request for inspection."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(
            {
                "path": self.path,
                "body": body,
                "authorization": self.headers.get("Authorization"),
            }
        )
        # 207: the nominal multi-status response of the Langfuse ingestion.
        raw = json.dumps({"successes": [], "errors": []}).encode("utf-8")
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silent: no noise in the test output
        pass


@pytest.fixture()
def local_langfuse():
    """Fake Langfuse on a free port; `host` points at it, `requests` archives."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IngestionHandler)
    server.requests = []
    server.status = 207
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.host = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_the_publisher_posts_the_authenticated_batch_to_the_ingestion_api(local_langfuse):
    settings = _settings(
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_host=local_langfuse.host + "/",  # the trailing / does not double the path
    )

    langfuse_publisher(settings)([{"type": "trace-create"}, {"type": "span-create"}])

    (request,) = local_langfuse.requests
    assert request["path"] == "/api/public/ingestion"
    assert [e["type"] for e in request["body"]["batch"]] == ["trace-create", "span-create"]
    expected = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    assert request["authorization"] == f"Basic {expected}"


def test_the_publisher_raises_on_an_error_response(local_langfuse):
    local_langfuse.status = 500
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=local_langfuse.host
    )

    with pytest.raises(httpx2.HTTPStatusError):
        langfuse_publisher(settings)([{"type": "trace-create"}])


def test_without_keys_no_handler_is_installed(trace_logger):
    # Degraded mode: without both keys, the activation plugs nothing at all.
    handlers_before = list(trace_logger.handlers)

    assert enable_langfuse_export(_settings()) is None
    assert enable_langfuse_export(_settings(langfuse_public_key="pk")) is None
    assert enable_langfuse_export(_settings(langfuse_secret_key="sk")) is None
    assert trace_logger.handlers == handlers_before


def test_with_both_keys_the_handler_is_installed_and_removable(trace_logger):
    handler = enable_langfuse_export(_settings(langfuse_public_key="pk",
                                               langfuse_secret_key="sk"))

    assert handler is not None
    assert handler in trace_logger.handlers
    trace_logger.removeHandler(handler)
    assert handler not in trace_logger.handlers


def test_repeated_activation_installs_a_single_handler(trace_logger):
    # `maestro.trace` is a *global* logger and the entry points enable the export on
    # every invocation, never removing it (#195): N activations in one process must
    # not hang N handlers, otherwise each recorded line goes out N times — duplicated
    # traces and costs, and N synchronous POSTs per line.
    settings = _settings(langfuse_public_key="pk", langfuse_secret_key="sk")

    first = enable_langfuse_export(settings)
    for _ in range(3):
        assert enable_langfuse_export(settings) is first  # the handler already installed

    exporters = [h for h in trace_logger.handlers if isinstance(h, LangfuseExportHandler)]
    assert exporters == [first]

    # Idempotence does not forbid re-enabling: once removed, the handler is installed again.
    trace_logger.removeHandler(first)
    reinstalled = enable_langfuse_export(settings)
    assert reinstalled is not None and reinstalled is not first


def test_the_test_suite_never_publishes_to_the_real_langfuse(trace_logger):
    # Guard of `tests/conftest.py`: on a workstation whose `.env` carries the real
    # keys, the "default" activation (without explicit settings — the one the
    # `main()` functions make) would install a handler, and the suite would go out on
    # the network at one synchronous POST per journaled line: 17 min 51 s instead of
    # 7 min 08 s (#195).
    assert enable_langfuse_export() is None
    assert not [h for h in trace_logger.handlers if isinstance(h, LangfuseExportHandler)]

    # Nothing can target the real host, even by building a publisher oneself.
    settings = load_settings()
    assert not settings.langfuse_public_key and not settings.langfuse_secret_key
    assert urlparse(settings.langfuse_host).hostname in {"127.0.0.1", "localhost"}


# --- Run scoring (#80) ------------------------------------------------------------------


def test_a_fully_succeeded_run_scores_1_on_its_trace():
    journal = RunJournal(run_id="run-eval")
    _record(journal, etape=STEP_PLANNING, agent="orchestrateur", role="Orchestrateur")
    _record(journal, etape="t1")
    _record(journal, etape="t2", agent="qa", role="QA / Testeur")

    succeeded, rate = scores_from_journal(journal)

    assert succeeded["type"] == "score-create" and rate["type"] == "score-create"
    assert succeeded["body"]["traceId"] == "run-eval"
    assert succeeded["body"]["name"] == SCORE_RUN_SUCCEEDED
    assert succeeded["body"]["value"] == 1
    assert succeeded["body"]["dataType"] == "BOOLEAN"
    assert rate["body"]["name"] == SCORE_SUCCESS_RATE
    assert rate["body"]["value"] == 1.0
    assert rate["body"]["dataType"] == "NUMERIC"
    assert "2/2 tâche(s) réussie(s)" in rate["body"]["comment"]


def test_a_run_with_failure_and_block_scores_0_and_the_real_rate():
    journal = RunJournal()
    _record(journal, etape="t1")
    _record(journal, etape="t2", statut="echec", erreur="boum")
    _record(journal, etape="t3", statut="bloquee", erreur="dépendance t2 en échec")

    succeeded, rate = scores_from_journal(journal)

    assert succeeded["body"]["value"] == 0
    assert rate["body"]["value"] == pytest.approx(1 / 3)
    assert "1 échec(s), 1 bloquée(s)" in succeeded["body"]["comment"]


def test_side_steps_do_not_count_as_tasks():
    # A task's outcome comes from its own step; its side steps (#55) create none.
    journal = RunJournal()
    _record(journal, etape="t1")
    _record(journal, etape="t1:validation", statut="approuve")

    succeeded, rate = scores_from_journal(journal)

    assert succeeded["body"]["value"] == 1
    assert "1/1 tâche(s) réussie(s)" in rate["body"]["comment"]


def test_without_a_task_there_is_nothing_to_score():
    # No score rather than a false one: empty journal, or planning alone (failed
    # before any plan — the run did not take place).
    assert scores_from_journal(RunJournal()) == ()

    journal = RunJournal()
    _record(journal, etape=STEP_PLANNING, statut="echec", erreur="plan illisible")
    assert scores_from_journal(journal) == ()


def test_scoring_is_a_no_op_without_keys():
    journal = RunJournal()
    _record(journal)

    # Degraded mode: nothing is published, nothing goes out on the network.
    assert score_run_langfuse(journal, _settings()) == ()


def test_scoring_publishes_the_scores_to_the_ingestion_api(local_langfuse):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=local_langfuse.host
    )
    journal = RunJournal(run_id="run-publie")
    _record(journal)

    published = score_run_langfuse(journal, settings)

    (request,) = local_langfuse.requests
    batch = request["body"]["batch"]
    assert [e["body"]["name"] for e in batch] == [SCORE_RUN_SUCCEEDED, SCORE_SUCCESS_RATE]
    assert all(e["body"]["traceId"] == "run-publie" for e in batch)
    assert len(published) == 2


def test_an_unreachable_langfuse_does_not_affect_the_scored_run(caplog):
    # Resilience: the publication fails (closed port), scoring swallows it and reports it.
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host="http://127.0.0.1:9"
    )
    journal = RunJournal()
    _record(journal)

    with caplog.at_level(logging.WARNING, logger="maestro.telemetry.langfuse"):
        assert score_run_langfuse(journal, settings) == ()

    assert "Scores Langfuse non publiés" in caplog.text


def test_with_nothing_to_score_nothing_is_published_even_with_keys(local_langfuse):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=local_langfuse.host
    )

    assert score_run_langfuse(RunJournal(), settings) == ()
    assert local_langfuse.requests == []


# --- End to end: traces along the steps, scores at the end of the run -------------------


def test_end_to_end_traces_then_scores_on_the_same_trace(trace_logger, local_langfuse):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=local_langfuse.host
    )
    handler = enable_langfuse_export(settings)
    try:
        journal = RunJournal(run_id="run-complet")
        _record(journal, etape=STEP_PLANNING, agent="orchestrateur",
                role="Orchestrateur", usage=StepUsage(appels=1, cout_usd=0.01))
        _record(journal, etape="t1", usage=StepUsage(appels=1, cout_usd=0.02))
        score_run_langfuse(journal, settings)
    finally:
        trace_logger.removeHandler(handler)

    # One batch per recorded step (#81), then the batch of scores (#80).
    types = [
        [e["type"] for e in request["body"]["batch"]] for request in local_langfuse.requests
    ]
    assert types == [
        ["trace-create", "generation-create"],
        ["trace-create", "generation-create"],
        ["score-create", "score-create"],
    ]
    # Everything lines up on the same trace: the run_id's.
    last = local_langfuse.requests[-1]["body"]["batch"]
    assert all(e["body"]["traceId"] == "run-complet" for e in last)
