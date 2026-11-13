"""Maestro's inter-agent messaging: Pub/Sub mailboxes (#44).

Two parts:

- `maestro.messaging.mailbox`: the message (`AgentMessage`, the mirror of the
  AGENT_MESSAGE entity of docs/03) and its transport (`Mailbox`), one mailbox per
  agent, in memory (`InMemoryMailbox`) or on Redis Pub/Sub (`RedisMailbox`), each
  exchange recorded in telemetry (#8, `record_message`);
- `maestro.messaging.handoff`: the **observable handoff** (`HandoffRelay`, MVP
  criterion 7). An agent finishes and announces the outcome by message, and the
  orchestration loop (`OrchestrationEngine(..., mailbox=...)`) unblocks the
  downstream tasks only once it has received it.
"""

from __future__ import annotations

from maestro.messaging.handoff import HANDOFF_TIMEOUT_S, RELAY_AGENT, HandoffRelay
from maestro.messaging.mailbox import (
    BROADCAST,
    BROADCAST_CHANNEL,
    MAILBOX_CHANNEL_PREFIX,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REQUEST,
    MESSAGE_RESPONSE,
    MESSAGE_STEP_SUFFIX,
    STATUS_PROCESSED,
    STATUS_READ,
    STATUS_SENT,
    AgentMessage,
    InMemoryMailbox,
    Mailbox,
    MailboxSubscription,
    RedisMailbox,
    mailbox_channel,
    record_message,
)

__all__ = [
    "BROADCAST",
    "BROADCAST_CHANNEL",
    "HANDOFF_TIMEOUT_S",
    "MAILBOX_CHANNEL_PREFIX",
    "MESSAGE_HANDOFF",
    "MESSAGE_NOTIFICATION",
    "MESSAGE_REQUEST",
    "MESSAGE_RESPONSE",
    "MESSAGE_STEP_SUFFIX",
    "RELAY_AGENT",
    "STATUS_PROCESSED",
    "STATUS_READ",
    "STATUS_SENT",
    "AgentMessage",
    "HandoffRelay",
    "InMemoryMailbox",
    "Mailbox",
    "MailboxSubscription",
    "RedisMailbox",
    "mailbox_channel",
    "record_message",
]
