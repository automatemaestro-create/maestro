"""Observable handoff: task end, then message, then the downstream task unblocks (#44).

Passes the **baton between agents through messaging** (MVP criterion 7): when an
agent finishes a task that others depend on, it **announces** the outcome as a
broadcast (an `AgentMessage` of type `handoff`, or `notification` when the task
did not succeed), and the orchestration loop **unblocks** each downstream task
only once it has received the message of each of its dependencies. The exchange
is recorded in telemetry (#8) when it is published, so it shows in the Control
Tower event stream (#46) through the existing bridge.

The announcement is a **broadcast** (every mailbox), not a direct message: when
an agent finishes, the agent that will take the downstream task is not known yet,
since assignment (routing, #42) happens when that task starts. The broadcast is
the team announcement ("I'm done, the baton passes"); direct messaging remains
available for targeted exchanges (EF-32).

The relay is **resilient**, like the rest of the loop: a failed publication
(Redis unreachable) is dropped without failing the task, and waiting for a
message is bounded (`HANDOFF_TIMEOUT_S`). Beyond that, the loop falls back on
in-process synchronization (#43), which keeps the last word on execution order.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

from maestro.messaging.mailbox import (
    BROADCAST,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    AgentMessage,
    Mailbox,
    MailboxSubscription,
    record_message,
)
from maestro.telemetry import RunJournal

if TYPE_CHECKING:  # typing-only import: no runtime dependency on the engine
    from maestro.engine.executor import TaskResult
    from maestro.orchestrator.schema import Task

#: The identity under which the loop collects the broadcast: the orchestration
#: process listens to team announcements to unblock downstream tasks. The value
#: names a mailbox, so it travels.
RELAY_AGENT = "orchestrateur"

#: Maximum wait for a dependency's handoff (s). The dependency has already ended
#: in process when the wait starts, so only a failing transport (a message lost on
#: Redis) can stretch it: past this delay, the loop falls back on in-process
#: synchronization rather than suspending the run.
HANDOFF_TIMEOUT_S = 30.0


class HandoffRelay:
    """The handoff desk of a run: announces task ends, unblocks downstream tasks.

    It opens at the start of a run (`await HandoffRelay.open(...)`): the
    broadcast mailbox is registered **before** anything runs, so no message can be
    missed. On the producer side, `announce` publishes and records the outcome of a
    task that has dependents; on the consumer side, `wait_for` returns a
    dependency's message (already received or still to come). `close` releases the
    mailbox at the end of the run.
    """

    def __init__(
        self,
        mailbox: Mailbox,
        subscription: MailboxSubscription,
        journal: RunJournal,
        *,
        timeout_s: float = HANDOFF_TIMEOUT_S,
    ) -> None:
        self._mailbox = mailbox
        self._subscription = subscription
        self._journal = journal
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[AgentMessage]] = {}
        self._reader: asyncio.Task[None] | None = None

    @classmethod
    async def open(
        cls,
        mailbox: Mailbox,
        journal: RunJournal,
        *,
        timeout_s: float = HANDOFF_TIMEOUT_S,
    ) -> HandoffRelay:
        """Open the broadcast mailbox and start collecting the mail."""
        subscription = await mailbox.subscribe(RELAY_AGENT)
        relay = cls(mailbox, subscription, journal, timeout_s=timeout_s)
        relay._reader = asyncio.create_task(relay._collect())
        return relay

    async def announce(
        self, task: Task, result: TaskResult, unblocked: Sequence[str]
    ) -> AgentMessage | None:
        """Publish and record the outcome of `task` for its downstream tasks.

        `handoff` when the task succeeded (the baton passes, `unblocked` names the
        tasks the announcement unblocks), `notification` otherwise (failure or
        blocker: the downstream tasks are warned and will block in turn, #43). A
        failed publication is dropped (None, nothing recorded): messaging must
        never fail the task it announces.
        """
        downstream = ", ".join(unblocked)
        if result.ok:
            message_type = MESSAGE_HANDOFF
            subject = f"Tâche « {task.titre} » terminée — la main passe à l'aval ({downstream})."
        else:
            message_type = MESSAGE_NOTIFICATION
            subject = (
                f"Tâche « {task.titre} » non aboutie ({result.statut}) — "
                f"aval prévenu ({downstream})."
            )
        message = AgentMessage(
            type=message_type,
            from_agent=result.agent,
            to_agent=BROADCAST,
            task_id=task.id,
            run_id=self._journal.run_id,
            subject=subject,
            payload={"statut": result.statut, "debloque": list(unblocked)},
        )
        try:
            await self._mailbox.publish(message)
        except Exception:
            return None
        record_message(self._journal, message, role=result.role)
        return message

    async def wait_for(self, task_id: str) -> AgentMessage | None:
        """The message announcing the outcome of `task_id`; None if nothing came in time.

        This is the **consumption** of the handoff: the downstream task starts only
        once the message of each dependency has been collected. The wait is bounded
        (`timeout_s`) so that a lost message does not suspend the run.
        """
        try:
            # `shield`: the timeout must not cancel the shared future (several
            # dependents may wait for the same announcement).
            return await asyncio.wait_for(asyncio.shield(self._future(task_id)), self._timeout_s)
        except TimeoutError:
            return None

    async def close(self) -> None:
        """Stop collecting and close the broadcast mailbox (end of the run)."""
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        await self._subscription.close()

    async def _collect(self) -> None:
        """Collect the mail: each announcement received resolves its task's wait."""
        try:
            async for message in self._subscription:
                if not message.task_id or message.type not in (
                    MESSAGE_HANDOFF,
                    MESSAGE_NOTIFICATION,
                ):
                    continue
                future = self._future(message.task_id)
                if not future.done():
                    future.set_result(message)
        except Exception:
            # Broken transport (lost Redis connection…): collecting stops, the waits
            # fall back on their timeout, and the loop goes on.
            return

    def _future(self, task_id: str) -> asyncio.Future[AgentMessage]:
        future = self._pending.get(task_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._pending[task_id] = future
        return future
