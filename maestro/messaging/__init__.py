"""Messagerie inter-agents de Maestro — boîtes aux lettres Pub/Sub (ticket #44).

Deux briques :

- `maestro.messaging.mailbox` : le message (`AgentMessage`, miroir de l'entité
  AGENT_MESSAGE de docs/03) et son transport (`Mailbox`) — boîtes aux lettres
  par agent, en mémoire (`InMemoryMailbox`) ou sur Redis Pub/Sub
  (`RedisMailbox`), avec journalisation des échanges dans la télémétrie (#8,
  `consigne_message`) ;
- `maestro.messaging.handoff` : le **handoff observable** (`HandoffRelais`,
  critère MVP n°7) — un agent termine, annonce l'issue par message, et la
  boucle d'orchestration (`OrchestrationEngine(..., mailbox=...)`) ne débloque
  l'aval qu'à réception.
"""

from __future__ import annotations

from maestro.messaging.handoff import AGENT_RELAIS, TIMEOUT_HANDOFF_S, HandoffRelais
from maestro.messaging.mailbox import (
    CANAL_BOITE_PREFIXE,
    CANAL_DIFFUSION,
    DIFFUSION,
    MESSAGE_HANDOFF,
    MESSAGE_NOTIFICATION,
    MESSAGE_REPONSE,
    MESSAGE_REQUETE,
    STATUT_ENVOYE,
    STATUT_LU,
    STATUT_TRAITE,
    SUFFIXE_ETAPE_MESSAGE,
    AgentMessage,
    InMemoryMailbox,
    Mailbox,
    MailboxSubscription,
    RedisMailbox,
    canal_boite,
    consigne_message,
)

__all__ = [
    "AGENT_RELAIS",
    "CANAL_BOITE_PREFIXE",
    "CANAL_DIFFUSION",
    "DIFFUSION",
    "MESSAGE_HANDOFF",
    "MESSAGE_NOTIFICATION",
    "MESSAGE_REPONSE",
    "MESSAGE_REQUETE",
    "STATUT_ENVOYE",
    "STATUT_LU",
    "STATUT_TRAITE",
    "SUFFIXE_ETAPE_MESSAGE",
    "TIMEOUT_HANDOFF_S",
    "AgentMessage",
    "HandoffRelais",
    "InMemoryMailbox",
    "Mailbox",
    "MailboxSubscription",
    "RedisMailbox",
    "canal_boite",
    "consigne_message",
]
