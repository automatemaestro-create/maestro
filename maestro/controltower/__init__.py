"""Backend Control Tower : API REST + flux temps réel de l'orchestration (ticket #46).

Quatre briques, assemblées par l'app FastAPI (`maestro.controltower.app`) :

- `Event` + `EventBus` (`InMemoryEventBus`, `RedisEventBus`) : le fait daté qui
  circule (statut de tâche, activité d'agent, message inter-agents) et son bus
  de diffusion — mémoire pour les tests, Redis Pub/Sub en production ;
- `ControlTowerState` : la projection de l'état courant (tâches, agents,
  exécutions) qui alimente les endpoints REST ;
- `maestro.controltower.bridge` : le pont télémétrie (#8) → bus — chaque ligne
  de journal devient un événement, côté orchestrateur comme côté workers ;
- `create_app` / `create_default_app` : l'app FastAPI (REST + WebSocket) et sa
  déclinaison de production (`maestro-api`).
"""

from __future__ import annotations

from maestro.controltower.app import create_app, create_default_app
from maestro.controltower.bridge import (
    JournalEventHandler,
    activer_publication,
    evenements_depuis_step,
    publieur_redis,
)
from maestro.controltower.events import (
    CANAL_EVENEMENTS,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.state import ControlTowerState, EtatAgent, EtatExecution, EtatTache

__all__ = [
    "CANAL_EVENEMENTS",
    "EVENEMENT_AGENT_ACTIVITE",
    "EVENEMENT_MESSAGE_INTER_AGENTS",
    "EVENEMENT_TACHE_REASSIGNATION",
    "EVENEMENT_TACHE_STATUT",
    "ControlTowerState",
    "EtatAgent",
    "EtatExecution",
    "EtatTache",
    "Event",
    "EventBus",
    "InMemoryEventBus",
    "JournalEventHandler",
    "RedisEventBus",
    "activer_publication",
    "create_app",
    "create_default_app",
    "evenements_depuis_step",
    "publieur_redis",
]
