"""Handoff observable : fin de tâche → message → déblocage de l'aval (ticket #44).

Fait passer le **relais entre agents par la messagerie** (critère MVP n°7) : quand
un agent termine une tâche dont d'autres dépendent, il **annonce** l'issue en
diffusion (`AgentMessage` de type `handoff` — `notification` si la tâche n'a pas
abouti), et la boucle d'orchestration ne **débloque** chaque tâche aval qu'à
réception du message de chacune de ses dépendances. L'échange est journalisé
dans la télémétrie (#8) à la publication, donc visible dans le flux d'événements
de la Control Tower (#46) via le pont existant.

L'annonce part en **diffusion** (toutes les boîtes) et non en message direct :
au moment où un agent termine, l'agent qui prendra la tâche aval n'est pas
encore connu — l'assignation (routage #42) n'a lieu qu'au démarrage de la
tâche. La diffusion est l'annonce d'équipe (« j'ai terminé, la main passe ») ;
la messagerie directe reste disponible pour les échanges ciblés (EF-32).

Le relais est **résilient**, comme le reste de la boucle : une publication en
échec (Redis injoignable) est abandonnée sans faire échouer la tâche, et
l'attente d'un message est bornée (`TIMEOUT_HANDOFF_S`) — au-delà, la boucle
retombe sur la synchronisation en process (#43), qui garde le dernier mot sur
l'ordre d'exécution.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

from maestro.messaging.mailbox import (
    DIFFUSION,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    AgentMessage,
    Mailbox,
    MailboxSubscription,
    consigne_message,
)
from maestro.telemetry import RunJournal

if TYPE_CHECKING:  # import de typage seul : pas de dépendance d'exécution au moteur
    from maestro.engine.executor import TaskResult
    from maestro.orchestrator.schema import Task

#: Identité sous laquelle la boucle relève la diffusion : le process
#: d'orchestration écoute les annonces d'équipe pour débloquer l'aval.
AGENT_RELAIS = "orchestrateur"

#: Attente maximale du handoff d'une dépendance (s). La dépendance étant déjà
#: terminée en process quand l'attente commence, seul un transport défaillant
#: (message perdu sur Redis) peut la faire durer : au-delà, on retombe sur la
#: synchronisation en process plutôt que de suspendre l'exécution.
TIMEOUT_HANDOFF_S = 30.0


class HandoffRelais:
    """Le guichet des handoffs d'une exécution : annonce les fins, débloque l'aval.

    S'ouvre en début de `run` (`await HandoffRelais.ouvrir(...)`) : la boîte de
    diffusion est posée **avant** toute exécution, aucun message ne peut être
    manqué. Côté producteur, `annonce` publie et journalise l'issue d'une tâche
    qui a des dépendants ; côté consommateur, `attend` rend le message d'une
    dépendance (déjà reçu ou à venir). `fermer` libère la boîte en fin de run.
    """

    def __init__(
        self,
        mailbox: Mailbox,
        abonnement: MailboxSubscription,
        journal: RunJournal,
        *,
        timeout_s: float = TIMEOUT_HANDOFF_S,
    ) -> None:
        self._mailbox = mailbox
        self._abonnement = abonnement
        self._journal = journal
        self._timeout_s = timeout_s
        self._attentes: dict[str, asyncio.Future[AgentMessage]] = {}
        self._lecteur: asyncio.Task[None] | None = None

    @classmethod
    async def ouvrir(
        cls,
        mailbox: Mailbox,
        journal: RunJournal,
        *,
        timeout_s: float = TIMEOUT_HANDOFF_S,
    ) -> HandoffRelais:
        """Ouvre la boîte de diffusion et démarre la relève du courrier."""
        abonnement = await mailbox.subscribe(AGENT_RELAIS)
        relais = cls(mailbox, abonnement, journal, timeout_s=timeout_s)
        relais._lecteur = asyncio.create_task(relais._releve())
        return relais

    async def annonce(
        self, task: Task, result: TaskResult, debloquees: Sequence[str]
    ) -> AgentMessage | None:
        """Publie et journalise l'issue de `task` à destination de son aval.

        `handoff` si la tâche a réussi (le relais passe, `debloquees` cite les
        tâches que l'annonce débloque), `notification` sinon (échec ou blocage :
        l'aval est prévenu et se bloquera à son tour, #43). Une publication en
        échec est abandonnée (None, rien de journalisé) : la messagerie ne doit
        jamais faire échouer la tâche qu'elle annonce.
        """
        aval = ", ".join(debloquees)
        if result.ok:
            type_message = MESSAGE_HANDOFF
            objet = f"Tâche « {task.titre} » terminée — la main passe à l'aval ({aval})."
        else:
            type_message = MESSAGE_NOTIFICATION
            objet = f"Tâche « {task.titre} » non aboutie ({result.statut}) — aval prévenu ({aval})."
        message = AgentMessage(
            type=type_message,
            de_agent=result.agent,
            a_agent=DIFFUSION,
            tache_id=task.id,
            run_id=self._journal.run_id,
            objet=objet,
            payload={"statut": result.statut, "debloque": list(debloquees)},
        )
        try:
            await self._mailbox.publish(message)
        except Exception:
            return None
        consigne_message(self._journal, message, role=result.role)
        return message

    async def attend(self, tache_id: str) -> AgentMessage | None:
        """Le message annonçant l'issue de `tache_id` — None si rien dans les délais.

        C'est la **consommation** du handoff : la tâche aval ne démarre qu'une
        fois le message de chaque dépendance relevé. L'attente est bornée
        (`timeout_s`) pour qu'un message perdu ne suspende pas l'exécution.
        """
        try:
            # `shield` : le time-out ne doit pas annuler la future partagée
            # (plusieurs dépendants peuvent attendre la même annonce).
            return await asyncio.wait_for(
                asyncio.shield(self._future(tache_id)), self._timeout_s
            )
        except TimeoutError:
            return None

    async def fermer(self) -> None:
        """Arrête la relève et referme la boîte de diffusion (fin de run)."""
        if self._lecteur is not None:
            self._lecteur.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._lecteur
        await self._abonnement.close()

    async def _releve(self) -> None:
        """Relève le courrier : chaque annonce reçue résout l'attente de sa tâche."""
        try:
            async for message in self._abonnement:
                if not message.tache_id or message.type not in (
                    MESSAGE_HANDOFF,
                    MESSAGE_NOTIFICATION,
                ):
                    continue
                future = self._future(message.tache_id)
                if not future.done():
                    future.set_result(message)
        except Exception:
            # Transport rompu (connexion Redis perdue…) : la relève s'arrête,
            # les attentes retombent sur leur time-out — la boucle continue.
            return

    def _future(self, tache_id: str) -> asyncio.Future[AgentMessage]:
        future = self._attentes.get(tache_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._attentes[tache_id] = future
        return future
