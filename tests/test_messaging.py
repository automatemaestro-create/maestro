"""Tests of inter-agent messaging: mailboxes and handoff (#44).

No network: the mailboxes run in memory (`InMemoryMailbox`, same contract as
`RedisMailbox`) and the orchestration loop runs on fake providers. Covers the
acceptance criteria of #44:

① one mailbox per agent, publishing and consuming messages: a direct message
   reaches its recipient alone, a broadcast reaches everyone, and the
   subscription is registered when `subscribe` returns (no missed message);
② handoff demonstrated (MVP criterion 7): in the orchestration loop, the agent
   that finishes a task announces the outcome by message, and the downstream
   task runs only once it has received it, including the notification of a
   failure, which warns the downstream task before it blocks (#43);
③ the exchange is recorded in telemetry (#8, `<task>:message` step) and shows in
   the Control Tower event stream (#46, `message.inter_agents` event through the
   telemetry bridge).

Plus the relay's resilience: the wait is bounded when no message comes, and a
failed publication is dropped without failing the task.
"""

import asyncio
import json

from maestro.controltower import EVENEMENT_MESSAGE_INTER_AGENTS, activer_publication
from maestro.engine import STATUT_BLOQUEE, STATUT_ECHEC, STATUT_TERMINEE, OrchestrationEngine
from maestro.engine.executor import TaskResult
from maestro.messaging import (
    BROADCAST,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REQUEST,
    STATUS_SENT,
    AgentMessage,
    HandoffRelay,
    InMemoryMailbox,
    mailbox_channel,
    record_message,
)
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.telemetry import LOGGER_NAME, RunJournal


class ConstantProvider(ModelProvider):
    """Always returns the same response (the planner, or a failing executor)."""

    name = "constant"

    def __init__(self, response):
        self._response = response

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class RecordingProvider(ModelProvider):
    """Fake executor: records each call and returns a distinct deliverable."""

    name = "recording"

    def __init__(self):
        self.calls = []

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model})
        return f"LIVRABLE pour l'appel #{len(self.calls)}"


def _plan_json():
    # Three chained tasks: bdd -> developpeur -> qa (through the required competencies).
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma des tâches.",
                "competences_requises": ["sql", "schema"],
                "format_sortie": "Fichier SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API",
                "dependances": ["schema-bdd"],
            },
            {
                "id": "tests-api",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


def _engine(mailbox, *, exec_provider=None):
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    executor = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(executor, orchestrator, mailbox=mailbox)


def _message(type_=MESSAGE_HANDOFF, *, from_agent="bdd", to_agent=BROADCAST, task_id="t1"):
    return AgentMessage(
        type=type_,
        from_agent=from_agent,
        to_agent=to_agent,
        task_id=task_id,
        run_id="run-44",
        subject="Tâche « Schéma » terminée — la main passe à l'aval (t2).",
        payload={"statut": STATUT_TERMINEE, "debloque": ["t2"]},
    )


def _task(task_id="t1", title="Schéma", dependencies=()):
    return Task(
        id=task_id,
        title=title,
        description="…",
        required_competencies=("sql",),
        output_format="SQL",
        dependencies=tuple(dependencies),
    )


def _result(status=STATUT_TERMINEE, *, agent="bdd", role="Base de données"):
    return TaskResult(
        task_id="t1",
        titre="Schéma",
        agent=agent,
        role=role,
        competences_requises=("sql",),
        score=2,
        statut=status,
        sortie="livrable" if status == STATUT_TERMINEE else "",
        erreur=None if status == STATUT_TERMINEE else "cause",
    )


# ---------------------------------------------------------------- ① Mailboxes


def test_message_json_round_trip():
    message = _message()
    assert AgentMessage.from_json(message.to_json()) == message
    assert AgentMessage.from_json(message.to_json().encode("utf-8")) == message


def test_direct_message_reaches_its_recipient_alone_and_broadcast_reaches_all():
    """Each agent's mailbox receives its direct messages and the broadcasts."""

    async def scenario():
        mailbox = InMemoryMailbox()
        qa_mailbox = await mailbox.subscribe("qa")
        developer_mailbox = await mailbox.subscribe("developpeur")

        # The subscriptions are registered when `subscribe` returns: publishing
        # right away loses nothing (the contract at the heart of the handoff,
        # Pub/Sub without replay).
        direct = _message(MESSAGE_REQUEST, to_agent="qa")
        broadcast = _message(MESSAGE_NOTIFICATION, to_agent=BROADCAST)
        await mailbox.publish(direct)
        await mailbox.publish(broadcast)

        assert await anext(qa_mailbox) == direct
        assert await anext(qa_mailbox) == broadcast
        # The first message in the developer's mailbox is the broadcast: the
        # direct message addressed to qa never reached it.
        assert await anext(developer_mailbox) == broadcast

        await qa_mailbox.close()
        await developer_mailbox.close()

    asyncio.run(scenario())


def test_redis_channel_of_an_agent_mailbox():
    """The Redis transport names one channel per mailbox (dedicated prefix, docs/03 §4)."""
    assert mailbox_channel("qa") == "maestro.boite.qa"


# ------------------------------------------- ③ Journal (#8) and Control Tower (#46)


def test_record_message_writes_the_exchange_to_the_journal():
    journal = RunJournal(run_id="run-44")
    record_message(journal, _message(), role="Base de données")

    (record,) = journal.records
    assert record.etape == "t1:message"
    assert record.agent == "bdd" and record.role == "Base de données"
    assert record.statut == STATUS_SENT
    assert record.nom.startswith("Tâche « Schéma » terminée")
    assert "handoff de bdd à diffusion" in record.sortie


def test_message_step_becomes_an_inter_agent_event():
    from maestro.controltower import evenements_depuis_step

    journal = RunJournal(run_id="run-44")
    record_message(journal, _message(), role="Base de données")

    (event,) = evenements_depuis_step(journal.records[0].to_dict())

    assert event.type == EVENEMENT_MESSAGE_INTER_AGENTS
    assert event.run_id == "run-44" and event.tache_id == "t1"
    assert event.agent == "bdd" and event.statut == STATUS_SENT
    assert "handoff de bdd à diffusion" in event.detail


# ---------------------------------------- ② Handoff in the loop (MVP criterion 7)


def test_handoff_announces_then_unblocks_the_downstream_task():
    """Chain bdd → developpeur → qa: each task end announces, the downstream task follows."""
    journal = RunJournal(run_id="run-44")
    report = asyncio.run(_engine(InMemoryMailbox()).run("Objectif", journal=journal))

    assert [r.statut for r in report.resultats] == [STATUT_TERMINEE] * 3
    steps = [r.etape for r in journal.records]
    # The handoff announcement is recorded between the end of the upstream task
    # and the start (#98) of the downstream task: the exchange precedes (and
    # unblocks) the execution. The chain states itself before the first task
    # (#1298, `cadence` step).
    assert steps == [
        "planification",
        "cadence",
        "schema-bdd:debut", "schema-bdd", "schema-bdd:message",
        "api-taches:debut", "api-taches", "api-taches:message",
        "tests-api:debut", "tests-api",  # last task of the plan: no announcement
    ]

    announcement = next(r for r in journal.records if r.etape == "schema-bdd:message")
    assert announcement.agent == "bdd" and announcement.statut == STATUS_SENT
    assert "la main passe" in announcement.nom
    assert "handoff" in announcement.sortie


def test_upstream_failure_notifies_the_downstream_task_before_it_blocks():
    """A failure travels by message too: the downstream task is warned, then blocks (#43)."""
    journal = RunJournal(run_id="run-44")
    report = asyncio.run(
        _engine(InMemoryMailbox(), exec_provider=ConstantProvider("   ")).run(
            "Objectif", journal=journal
        )
    )

    root, *downstream = report.resultats
    assert root.statut == STATUT_ECHEC
    assert all(r.statut == STATUT_BLOQUEE for r in downstream)

    by_step = {r.etape: r for r in journal.records}
    notification = by_step["schema-bdd:message"]
    assert "notification" in notification.sortie and "echec" in notification.nom
    # The blocked task has a downstream task too: it warns it in turn.
    assert "bloquee" in by_step["api-taches:message"].nom


def test_handoff_shows_in_the_control_tower_stream():
    """Criterion ③ end to end: loop + messaging ⇒ `message.inter_agents` event."""
    import logging

    captured = []
    handler = activer_publication(captured.append)
    try:
        asyncio.run(_engine(InMemoryMailbox()).run("Objectif"))
    finally:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)

    messages = [e for e in captured if e.type == EVENEMENT_MESSAGE_INTER_AGENTS]
    assert [m.tache_id for m in messages] == ["schema-bdd", "api-taches"]
    assert messages[0].agent == "bdd"
    assert "handoff" in messages[0].detail


# ------------------------------------------------------------- Relay resilience


def test_wait_is_bounded_when_no_announcement_arrives():
    """A lost message does not suspend the loop: the wait returns None after the timeout."""

    async def scenario():
        mailbox = InMemoryMailbox()
        relay = await HandoffRelay.open(mailbox, RunJournal(), timeout_s=0.05)
        try:
            assert await relay.wait_for("fantome") is None
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_announcement_dropped_when_publication_fails():
    """Messaging never fails the task it announces (resilience)."""

    class UnreachableMailbox(InMemoryMailbox):
        async def publish(self, message):
            raise ConnectionError("Redis injoignable")

    async def scenario():
        journal = RunJournal()
        relay = await HandoffRelay.open(UnreachableMailbox(), journal)
        try:
            announcement = await relay.announce(_task(), _result(), ["t2"])
        finally:
            await relay.close()
        assert announcement is None
        assert journal.records == ()  # nothing sent ⇒ nothing recorded

    asyncio.run(scenario())
