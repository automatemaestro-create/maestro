"""Inter-agent mailboxes: messages and their Pub/Sub transport (#44).

Each agent has a **mailbox** (docs/01 §3.3, docs/08): a direct messaging channel
through which one agent notifies another (hands off, asks, broadcasts) without
going through the orchestrator (EF-32). The vehicle is `AgentMessage`, a light
mirror of the AGENT_MESSAGE entity (docs/03 §2): sender, recipient (empty means a
broadcast to every mailbox), type (`handoff`, `requete`, `reponse`,
`notification`), context task and JSON payload.

The transport (`Mailbox`) has two implementations behind one contract, like the
Control Tower event bus (#46):

- `InMemoryMailbox`: in-process asyncio queues, for tests and single-process
  deployments;
- `RedisMailbox`: **Redis Pub/Sub**, the production path, on the instance shared
  with the task queue (#41) and the event bus (#46). One channel per agent
  (`maestro.boite.<agent>`) plus a broadcast channel (`maestro.diffusion`).

Unlike the event bus, `subscribe` returns an **already-registered** subscription
(`MailboxSubscription`) rather than a generator. Pub/Sub keeps no history, so the
consumer needs the guarantee that its mailbox is open *before* the sender
publishes: the return of `subscribe` is that guarantee.

Every exchange is **recorded** in telemetry (#8) by `record_message`: the journal
step (`<task>:message`) is turned by the Control Tower bridge
(`maestro.controltower.bridge`) into a `message.inter_agents` event in the
real-time stream (EF-34).
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from maestro.espace import espace_courant
from maestro.telemetry import RunJournal, StepUsage

#: Inter-agent message types (AGENT_MESSAGE entity, docs/03 §2): `handoff` passes
#: the baton, `notification` informs (failure, announcement), and the
#: `requete`/`reponse` pair carries a question and its answer. The values are
#: what travels and is recorded.
MESSAGE_HANDOFF = "handoff"
MESSAGE_REQUEST = "requete"
MESSAGE_RESPONSE = "reponse"
MESSAGE_NOTIFICATION = "notification"

#: Message statuses (docs/03 §2). Messages are published as `envoye`; `lu` and
#: `traite` wait for a durable store to acknowledge them.
STATUS_SENT = "envoye"
STATUS_READ = "lu"
STATUS_PROCESSED = "traite"

#: The "broadcast" recipient: the message goes to **every** mailbox (the null
#: `to_agent` of the AGENT_MESSAGE entity).
BROADCAST = ""

#: Redis Pub/Sub channels: one mailbox per agent plus the broadcast channel, with
#: distinct prefixes so that no agent name can collide with the broadcast. The
#: instance is shared (#41, #46), hence the namespaced names. These are the names
#: of the common namespace: `RedisMailbox` puts them in its stack's namespace (#1164).
MAILBOX_CHANNEL_PREFIX = "maestro.boite."
BROADCAST_CHANNEL = "maestro.diffusion"

#: Default Redis URL: the local docker-compose instance (infra/README.md). The
#: task queue (#41) and the event bus (#46) declare the same value on purpose, so
#: that none of the three depends on another.
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

#: Suffix of the journal step that records an inter-agent message: the
#: convention the Control Tower bridge reads (`maestro.controltower.bridge`).
MESSAGE_STEP_SUFFIX = ":message"


def _timestamp() -> str:
    """UTC ISO-8601 timestamp, at the journal's precision (#8)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def mailbox_channel(agent: str) -> str:
    """The Redis channel of `agent`'s mailbox."""
    return f"{MAILBOX_CHANNEL_PREFIX}{agent}"


@dataclass(frozen=True)
class AgentMessage:
    """An inter-agent message, ready to travel as JSON (AGENT_MESSAGE entity).

    `from_agent` is the sender and `to_agent` the recipient (`BROADCAST`, empty,
    for every mailbox). `task_id` places the message in the plan (the task it is
    about) and `run_id` in the run. `subject` is the human line of the message;
    `payload` is the structured content (status passed on, unblocked tasks…),
    always JSON-serializable.

    The JSON keys (`de_agent`, `a_agent`, `tache_id`, `objet`, `statut`,
    `horodatage`) are the wire format shared with every reader of the channel:
    they keep their value whatever the attribute names.
    """

    type: str
    from_agent: str
    to_agent: str = BROADCAST
    task_id: str = ""
    run_id: str = ""
    subject: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    status: str = STATUS_SENT
    timestamp: str = field(default_factory=_timestamp)

    def to_dict(self) -> dict[str, Any]:
        """The message as a JSON-serializable dict (the channel format)."""
        return {
            "type": self.type,
            "de_agent": self.from_agent,
            "a_agent": self.to_agent,
            "tache_id": self.task_id,
            "run_id": self.run_id,
            "objet": self.subject,
            "payload": dict(self.payload),
            "statut": self.status,
            "horodatage": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentMessage:
        """Rebuild a message from its `to_dict` form (JSON round trip)."""
        return cls(
            type=data["type"],
            from_agent=data.get("de_agent", ""),
            to_agent=data.get("a_agent", BROADCAST),
            task_id=data.get("tache_id", ""),
            run_id=data.get("run_id", ""),
            subject=data.get("objet", ""),
            payload=dict(data.get("payload", {})),
            status=data.get("statut", STATUS_SENT),
            timestamp=data.get("horodatage", ""),
        )

    def to_json(self) -> str:
        """Serialize the message as compact JSON (the Redis channel format)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> AgentMessage:
        """Deserialize a message from the Redis channel (`bytes` as received)."""
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"message JSON attendu (objet), reçu : {text!r}")
        return cls.from_dict(data)


class MailboxSubscription(ABC):
    """An open mailbox: an async iterator over the messages it receives.

    It receives the messages **addressed to its agent** and the **broadcasts**.
    There is no replay (Pub/Sub keeps no history): only messages published after
    the mailbox opened arrive. `close` closes the mailbox.
    """

    def __aiter__(self) -> MailboxSubscription:
        return self

    @abstractmethod
    async def __anext__(self) -> AgentMessage:
        """The next message received (waits if there is none yet)."""
        raise NotImplementedError

    # Optional hook, not part of the contract: a deliberate no-op (hence the noqa B027).
    async def close(self) -> None:  # noqa: B027
        """Close the mailbox (unsubscribe, connections); a no-op by default."""


class Mailbox(ABC):
    """Transport of inter-agent messages, the engine's injection point (#44).

    `publish` drops the message in the recipient's mailbox (every mailbox for
    `BROADCAST`); `subscribe(agent)` opens `agent`'s mailbox and returns only once
    the subscription is **registered**, so a message published afterwards is
    guaranteed to arrive. Several mailboxes open for the same agent each receive
    all of its messages.
    """

    @abstractmethod
    async def publish(self, message: AgentMessage) -> None:
        """Publish `message` to the recipient mailbox or mailboxes."""
        raise NotImplementedError

    @abstractmethod
    async def subscribe(self, agent: str) -> MailboxSubscription:
        """Open `agent`'s mailbox (the subscription is registered on return)."""
        raise NotImplementedError

    # Optional hook, not part of the contract: a deliberate no-op (hence the noqa B027).
    async def close(self) -> None:  # noqa: B027
        """Release the transport's resources (connections); a no-op by default."""


class _InMemorySubscription(MailboxSubscription):
    """An open mailbox of the in-memory transport: one asyncio queue per subscription."""

    def __init__(self, agent: str, subscribers: list[_InMemorySubscription]) -> None:
        self.agent = agent
        self.queue: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._subscribers = subscribers

    async def __anext__(self) -> AgentMessage:
        return await self.queue.get()

    async def close(self) -> None:
        if self in self._subscribers:
            self._subscribers.remove(self)


class InMemoryMailbox(Mailbox):
    """In-memory mailboxes: asyncio queues, no network.

    The transport of **tests** and of single-process deployments, where the
    orchestrator and its agents share the asyncio loop. The queues are unbounded:
    messages are few and consumed as they come.
    """

    def __init__(self) -> None:
        self._subscribers: list[_InMemorySubscription] = []

    async def publish(self, message: AgentMessage) -> None:
        # Defensive copy: a mailbox may close during the loop.
        for subscriber in list(self._subscribers):
            if message.to_agent in (BROADCAST, subscriber.agent):
                subscriber.queue.put_nowait(message)

    async def subscribe(self, agent: str) -> MailboxSubscription:
        subscription = _InMemorySubscription(agent, self._subscribers)
        self._subscribers.append(subscription)
        return subscription


class _RedisSubscription(MailboxSubscription):
    """An open mailbox of the Redis transport: a pubsub subscribed to both channels."""

    def __init__(self, pubsub: Any, channels: tuple[str, ...]) -> None:
        self._pubsub = pubsub
        self._channels = channels
        self._stream = pubsub.listen()

    async def __anext__(self) -> AgentMessage:
        # `listen` interleaves control messages (subscribe…): only data messages
        # carry an inter-agent message.
        async for raw in self._stream:
            if raw.get("type") != "message":
                continue
            return AgentMessage.from_json(raw["data"])
        raise StopAsyncIteration

    async def close(self) -> None:
        await self._pubsub.unsubscribe(*self._channels)
        await self._pubsub.aclose()


class RedisMailbox(Mailbox):
    """Mailboxes on Redis Pub/Sub, the multi-process path.

    Publishes on the recipient's mailbox channel (`maestro.boite.<agent>`), or on
    `maestro.diffusion` for a broadcast; each open mailbox listens to both. The
    `redis` dependency comes with `celery[redis]` (#41); the connection opens
    lazily, on the first call.

    The channels are those of **the stack's namespace**, resolved once here
    (#1164): two working copies on the same Redis do not hear each other.
    """

    def __init__(self, url: str | None = None) -> None:
        # Local import: only the Redis branch needs the client (an engine tested
        # on in-memory mailboxes does not).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or DEFAULT_REDIS_URL)
        self._namespace = espace_courant()

    async def publish(self, message: AgentMessage) -> None:
        to = message.to_agent
        channel = BROADCAST_CHANNEL if to == BROADCAST else mailbox_channel(to)
        await self._client.publish(self._namespace.nommer(channel), message.to_json())

    async def subscribe(self, agent: str) -> MailboxSubscription:
        channels = (
            self._namespace.nommer(mailbox_channel(agent)),
            self._namespace.nommer(BROADCAST_CHANNEL),
        )
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        return _RedisSubscription(pubsub, channels)

    async def close(self) -> None:
        await self._client.aclose()


def record_message(
    journal: RunJournal,
    message: AgentMessage,
    *,
    role: str = "",
    project_id: str | None = None,
) -> None:
    """Record `message` in telemetry (#8), which makes the exchange traceable (EF-34).

    Records a `<task>:message` step in the run's journal: the JSON line goes to
    the traces like every other step, and the Control Tower bridge (#46) turns it
    into a `message.inter_agents` event in the real-time stream. `role` is the
    sender's role when it is known.

    `project_id` (#222, #720) is the project of the task that produced the
    message. It is **optional** because not every message has one (a handoff
    announces the outcome of a task that may belong to no project), but a caller
    that knows it must pass it: the project is a filter, and a step that does not
    carry it disappears from the views restricted to it. The executor applies the
    same rule to its side steps (`maestro.engine.executor._consigne_validation`).
    """
    recipient = message.to_agent or "diffusion"
    journal.consigne(
        etape=f"{message.task_id}{MESSAGE_STEP_SUFFIX}",
        nom=message.subject or f"Message inter-agents ({message.type})",
        agent=message.from_agent,
        role=role,
        statut=message.status,
        entree="",
        sortie=f"{message.type} de {message.from_agent} à {recipient} : {message.subject}",
        usage=StepUsage(),
        projet_id=project_id,
    )
