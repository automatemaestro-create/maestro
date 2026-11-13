"""The names that travel or are stored keep their value when the code is renamed (#1649).

Translating the code into English renames constants, functions and fields, never
what goes over a wire or into a store: a Redis key, a Celery task name, a Temporal
activity type, a JSON key. Workers of an older version, workflows in flight on the
Temporal server and states saved on disk still use those values (docs/55, rule 3).
This suite pins them, so that a rename which reaches a value fails here first.

The values are written out literally on purpose: comparing a constant with itself
would prove nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from temporalio import activity, workflow

from maestro.durable import DURABLE_QUEUE, WORKFLOW_ID_PREFIX, MaestroRunWorkflow, workflow_id
from maestro.durable.activities import execute_task, plan, record_blocker
from maestro.messaging import (
    BROADCAST,
    BROADCAST_CHANNEL,
    MAILBOX_CHANNEL_PREFIX,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REQUEST,
    MESSAGE_RESPONSE,
    MESSAGE_STEP_SUFFIX,
    RELAY_AGENT,
    STATUS_PROCESSED,
    STATUS_READ,
    STATUS_SENT,
    AgentMessage,
    mailbox_channel,
)
from maestro.queue import EXECUTE_TASK_NAME, TASKS_QUEUE, create_app
from maestro.queue.worker import execute_task as celery_execute_task
from maestro.router import (
    METHOD_CLASSIFIER,
    METHOD_CLOSEST,
    METHOD_COMPETENCIES,
    METHOD_FALLBACK,
)
from maestro.router.classifier import _parse_classification
from maestro.sources import (
    PREVIEW_RUN,
    REASON_SEEN_AT_LAUNCH,
    REASON_VISION_UNAVAILABLE,
    STATES,
    TYPE_DIRECTORY,
    TYPE_FILE,
    TYPE_URL,
    UPLOADS_DIRECTORY,
    Reading,
    ReadingReport,
    Source,
    SourceRejected,
    resolve_sources,
)
from maestro.sources.upload import Upload
from maestro.telemetry.costs import (
    STEP_ACCEPTANCE,
    STEP_ASSESSMENT,
    STEP_BRIEF,
    STEP_CADENCE,
    STEP_PLANNING,
    STEP_PROBE,
    STEP_RESUME,
    STEP_SPENDING_CAP,
    STEP_TEAM,
    RunCost,
    TaskCost,
)
from maestro.telemetry.langfuse import SCORE_RUN_SUCCEEDED, SCORE_SUCCESS_RATE
from maestro.telemetry.redact import SECRET_MARKER
from maestro.telemetry.usage import StepUsage

ROOT = Path(__file__).resolve().parents[1]


def test_mailbox_channels_keep_their_redis_names() -> None:
    assert MAILBOX_CHANNEL_PREFIX == "maestro.boite."
    assert BROADCAST_CHANNEL == "maestro.diffusion"
    assert mailbox_channel("qa") == "maestro.boite.qa"
    assert BROADCAST == ""


def test_message_types_statuses_and_step_suffix_keep_their_values() -> None:
    assert (MESSAGE_HANDOFF, MESSAGE_REQUEST, MESSAGE_RESPONSE, MESSAGE_NOTIFICATION) == (
        "handoff",
        "requete",
        "reponse",
        "notification",
    )
    assert (STATUS_SENT, STATUS_READ, STATUS_PROCESSED) == ("envoye", "lu", "traite")
    assert MESSAGE_STEP_SUFFIX == ":message"
    assert RELAY_AGENT == "orchestrateur"


def test_an_agent_message_keeps_its_json_keys() -> None:
    message = AgentMessage(
        type=MESSAGE_NOTIFICATION,
        from_agent="qa",
        to_agent="dev",
        task_id="t1",
        run_id="r1",
        subject="salut",
        payload={"cle": "valeur"},
        timestamp="2026-11-13T00:00:00+00:00",
    )
    assert message.to_dict() == {
        "type": "notification",
        "de_agent": "qa",
        "a_agent": "dev",
        "tache_id": "t1",
        "run_id": "r1",
        "objet": "salut",
        "payload": {"cle": "valeur"},
        "statut": "envoye",
        "horodatage": "2026-11-13T00:00:00+00:00",
    }
    # A message written by an older version reads back field for field.
    assert AgentMessage.from_dict(message.to_dict()) == message


def test_the_celery_task_keeps_its_registered_name_and_queue() -> None:
    assert EXECUTE_TASK_NAME == "maestro.executer_tache"
    assert celery_execute_task.name == "maestro.executer_tache"
    assert TASKS_QUEUE == "maestro.taches"
    assert create_app().conf.include == ["maestro.queue.worker"]


def test_the_temporal_activities_keep_their_types() -> None:
    # The type a workflow in flight addresses is the registered name, not the
    # Python function name: the SDK resolves it from the `@activity.defn` decoration.
    activities = (plan, execute_task, record_blocker)
    names = [activity._Definition.must_from_callable(f).name for f in activities]
    assert names == ["planifier", "executer_tache", "consigner_blocage"]


def test_the_temporal_workflow_keeps_its_name_query_and_ids() -> None:
    definition = workflow._Definition.must_from_class(MaestroRunWorkflow)
    assert definition.name == "MaestroRunWorkflow"
    assert list(definition.queries) == ["resultats_acquis"]
    assert DURABLE_QUEUE == "maestro-durable"
    assert WORKFLOW_ID_PREFIX == "maestro-run-"
    assert workflow_id("abc") == "maestro-run-abc"


def test_the_workflow_checkpoint_keeps_its_keys() -> None:
    assert set(MaestroRunWorkflow().checkpoint()) == {
        "objectif",
        "run_id",
        "planification",
        "resultats",
    }


def test_routing_methods_keep_their_values() -> None:
    assert (METHOD_COMPETENCIES, METHOD_CLASSIFIER, METHOD_FALLBACK, METHOD_CLOSEST) == (
        "competences",
        "classifieur",
        "repli",
        "plus_proche",
    )


def test_the_classifier_still_reads_the_confiance_key() -> None:
    verdict = _parse_classification('{"agent": "qa", "confiance": 0.7}', frozenset({"qa"}))
    assert (verdict.agent, verdict.confidence) == ("qa", 0.7)


def test_source_types_reading_states_and_reasons_keep_their_values() -> None:
    assert (TYPE_FILE, TYPE_DIRECTORY, TYPE_URL) == ("fichier", "dossier", "url")
    assert STATES == ("lu", "tronque", "ignore")
    assert (REASON_VISION_UNAVAILABLE, REASON_SEEN_AT_LAUNCH) == (
        "vision-indisponible",
        "vue-au-lancement",
    )
    assert (UPLOADS_DIRECTORY, PREVIEW_RUN) == ("_televersements", "apercu")


def test_a_rejected_source_keeps_its_reason_code() -> None:
    with pytest.raises(SourceRejected) as rejected:
        resolve_sources([{"type": "inconnu", "nom": "x"}], run_id="r1")
    assert rejected.value.reason == "type-inconnu"


def test_a_source_keeps_its_json_keys() -> None:
    source = Source(type="fichier", name="a.md", path="/x/a.md", size=3)
    assert source.to_dict() == {
        "type": "fichier",
        "nom": "a.md",
        "chemin": "/x/a.md",
        "valeur": "",
        "taille": 3,
        "lecture_seule": True,
    }
    # A source written by an older version reads back field for field.
    assert Source.from_dict(source.to_dict()) == source


def test_a_reading_report_keeps_its_json_keys() -> None:
    entry = Reading(name="b.md", type="fichier", state="lu", markdown="b", tokens=1)
    reading = Reading(
        name="d",
        type="dossier",
        state="tronque",
        tokens=2,
        reason="r",
        message="m",
        limit="l",
        entries=(entry,),
    )
    entry_keys = {"nom": "b.md", "type": "fichier", "etat": "lu", "tokens": 1, "motif": ""}
    assert ReadingReport(readings=(reading,)).to_dict() == {
        "tokens": 2,
        "lectures": [
            {
                "nom": "d",
                "type": "dossier",
                "etat": "tronque",
                "tokens": 2,
                "motif": "r",
                "message": "m",
                "limite": "l",
                "entrees": [{**entry_keys, "message": "", "limite": "", "entrees": []}],
            }
        ],
    }


def test_an_upload_keeps_its_json_keys() -> None:
    upload = Upload(id="abc123", name="a.md", size=3, path=Path("/x/a.md"))
    assert upload.to_dict() == {"id": "abc123", "type": "fichier", "nom": "a.md", "taille": 3}
    assert upload.declaration() == {"type": "fichier", "nom": "a.md", "taille": 3}


def test_the_cost_ledger_keeps_its_json_keys_and_the_steps_it_reads() -> None:
    task = TaskCost(
        task_id="t1", name="n", agent="dev", role="r", status="terminee", project_id="p"
    )
    task_keys = {"tache_id", "nom", "agent", "role", "statut", "usage", "ticket", "projet_id"}
    assert set(task.to_dict()) == task_keys
    ledger = RunCost(run_id="r1", tasks=(task,)).to_dict()
    run_keys = {"run_id", "planification", "brief", "taches", "bilan", "sonde", "recette", "total"}
    assert set(ledger) == run_keys
    steps = (
        STEP_PLANNING,
        STEP_BRIEF,
        STEP_RESUME,
        STEP_TEAM,
        STEP_CADENCE,
        STEP_SPENDING_CAP,
        STEP_ASSESSMENT,
        STEP_PROBE,
        STEP_ACCEPTANCE,
    )
    assert steps == (
        "planification",
        "brief",
        "reprise",
        "equipe",
        "cadence",
        "plafond",
        "bilan",
        "sonde",
        "recette",
    )


def test_langfuse_scores_and_the_secret_marker_keep_their_values() -> None:
    assert (SCORE_RUN_SUCCEEDED, SCORE_SUCCESS_RATE) == ("run-reussi", "taux-reussite")
    assert SECRET_MARKER == "[secret masqué]"


#: The keys `StepUsage.to_dict` writes. The run journal, the cost ledger, the API
#: and `apps/web` read them, and journals on disk still hold them.
STEP_USAGE_KEYS = {
    "appels",
    "tokens_entree",
    "tokens_sortie",
    "tokens_total",
    "cout_usd",
    "duree_ms",
    "duree_api_ms",
    "duree_arbitrage_ms",
    "duree_attente_creneau_ms",
    "duree_attente_atelier_ms",
    "duree_attente_ms",
    "duree_execution_ms",
    "tours",
    "outils",
    "tokens_non_tarifes",
    "tokens_cache_lus",
    "tokens_cache_ecrits",
    "contexte_max",
    "tokens_hors_cache_lu",
}


def test_a_step_usage_keeps_its_json_keys() -> None:
    # Every field holds a distinct value, so that two swapped keys fail too.
    usage = StepUsage(
        calls=2,
        input_tokens=1000,
        output_tokens=50,
        cost_usd=0.25,
        duration_ms=9000,
        api_duration_ms=7000,
        approval_duration_ms=1000,
        slot_wait_duration_ms=500,
        workshop_wait_duration_ms=250,
        turns=3,
        tools=("Read", "Write"),
        unpriced_tokens=40,
        cache_read_tokens=600,
        cache_write_tokens=100,
        max_context=800,
    )
    assert usage.to_dict() == {
        "appels": 2,
        "tokens_entree": 1000,
        "tokens_sortie": 50,
        "tokens_total": 1050,
        "cout_usd": 0.25,
        "duree_ms": 9000,
        "duree_api_ms": 7000,
        "duree_arbitrage_ms": 1000,
        "duree_attente_creneau_ms": 500,
        "duree_attente_atelier_ms": 250,
        "duree_attente_ms": 1750,
        "duree_execution_ms": 7250,
        "tours": 3,
        "outils": ["Read", "Write"],
        "tokens_non_tarifes": 40,
        "tokens_cache_lus": 600,
        "tokens_cache_ecrits": 100,
        "contexte_max": 800,
        "tokens_hors_cache_lu": 450,
    }
    assert set(usage.to_dict()) == STEP_USAGE_KEYS
    # A measure written by an older version reads back field for field.
    assert StepUsage.from_dict(usage.to_dict()) == usage


def test_a_journal_line_without_the_recent_keys_still_reads_back() -> None:
    line = {
        "appels": 1,
        "tokens_entree": 10,
        "tokens_sortie": 2,
        "cout_usd": None,
        "duree_ms": 5,
        "tours": 1,
        "outils": ["Bash"],
    }
    usage = StepUsage.from_dict(line)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 10, 2)
    assert (usage.duration_ms, usage.turns, usage.tools) == (5, 1, ("Bash",))
    # Unknown stays unknown: no measured wait, no context, every token unpriced.
    assert usage.approval_duration_ms is None and usage.max_context is None
    assert usage.unpriced_tokens == 12


def test_the_screen_reads_the_usage_keys_the_api_writes() -> None:
    types = (ROOT / "apps" / "web" / "lib" / "types.ts").read_text(encoding="utf-8")
    block = re.search(r"^export type Usage = \{\n(.*?)^\};", types, re.MULTILINE | re.DOTALL)
    assert block is not None, "the `Usage` type of apps/web/lib/types.ts is gone"
    keys = set(re.findall(r"^  (\w+)\??:", block.group(1), re.MULTILINE))
    assert keys == STEP_USAGE_KEYS
