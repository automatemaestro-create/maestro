"""Backend Control Tower : API REST + flux temps réel de l'orchestration (ticket #46).

Cinq briques, assemblées par l'app FastAPI (`maestro.controltower.app`) :

- `Event` + `EventBus` (`InMemoryEventBus`, `RedisEventBus`) : le fait daté qui
  circule (statut de tâche, activité d'agent, message inter-agents, validation
  humaine) et son bus de diffusion — mémoire pour les tests, Redis Pub/Sub en
  production ;
- `ControlTowerState` : la projection de l'état courant (tâches, agents,
  exécutions, validations) qui alimente les endpoints REST ;
- `maestro.controltower.bridge` : le pont télémétrie (#8) → bus — chaque ligne
  de journal devient un événement, côté orchestrateur comme côté workers ;
- `maestro.controltower.validation` : le validateur human-in-the-loop (#48) —
  le moteur publie ses demandes d'approbation sur le bus et attend la décision
  prise depuis l'UI ;
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
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.state import (
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
    EtatAgent,
    EtatExecution,
    EtatTache,
    EtatValidation,
)
from maestro.controltower.validation import ValidateurControlTower, validateur_redis

__all__ = [
    "CANAL_EVENEMENTS",
    "EVENEMENT_AGENT_ACTIVITE",
    "EVENEMENT_MESSAGE_INTER_AGENTS",
    "EVENEMENT_TACHE_REASSIGNATION",
    "EVENEMENT_TACHE_STATUT",
    "EVENEMENT_VALIDATION_DECISION",
    "EVENEMENT_VALIDATION_DEMANDE",
    "VALIDATION_APPROUVEE",
    "VALIDATION_EN_ATTENTE",
    "VALIDATION_REFUSEE",
    "ControlTowerState",
    "EtatAgent",
    "EtatExecution",
    "EtatTache",
    "EtatValidation",
    "Event",
    "EventBus",
    "InMemoryEventBus",
    "JournalEventHandler",
    "RedisEventBus",
    "ValidateurControlTower",
    "activer_publication",
    "create_app",
    "create_default_app",
    "evenements_depuis_step",
    "publieur_redis",
    "validateur_redis",
]
