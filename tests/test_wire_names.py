"""The names that travel or are stored keep their value when the code is renamed (#1650).

Translating the code into English renames constants, functions and fields, never
what goes over a wire or into a store: a Redis key, a Celery task name, a Temporal
activity type, a JSON key. Workers of an older version, workflows in flight on the
Temporal server and states saved on disk still use those values (docs/55, rule 3).
This suite pins them, so that a rename which reaches a value fails here first.

The values are written out literally on purpose: comparing a constant with itself
would prove nothing.
"""

from __future__ import annotations

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
