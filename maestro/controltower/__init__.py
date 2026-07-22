"""Backend Control Tower : API REST + flux temps réel de l'orchestration (ticket #46).

Sept briques, assemblées par l'app FastAPI (`maestro.controltower.app`) :

- `Event` + `EventBus` (`InMemoryEventBus`, `RedisEventBus`) : le fait daté qui
  circule (statut de tâche, activité d'agent, message inter-agents, validation
  humaine, chat) et son bus de diffusion — mémoire pour les tests, Redis
  Pub/Sub en production ;
- `EventLog` (`InMemoryEventLog`, `RedisEventLog`) : le journal **durable** des
  événements (#97) — pendant persistant du bus éphémère, rejoué au démarrage
  pour reconstruire la projection après un redémarrage de l'API (liste Redis en
  production, mémoire pour les tests) ;
- `ControlTowerState` : la projection de l'état courant (tâches, agents,
  exécutions, validations) qui alimente les endpoints REST ;
- `maestro.controltower.bridge` : le pont télémétrie (#8) → bus — chaque ligne
  de journal devient un événement, côté orchestrateur comme côté workers ;
- `maestro.controltower.validation` : le validateur human-in-the-loop (#48) —
  le moteur publie ses demandes d'approbation sur le bus et attend la décision
  prise depuis l'UI ;
- `maestro.controltower.chat` : le chat utilisateur ↔ agent (#84) — fil
  persisté (`ChatStore`), réponse produite par le fournisseur configuré
  (`RepondeurModele`) ou scriptée (`RepondeurScripte`), flux d'un envoi
  (`ServiceChat` : persistance, messagerie #44, diffusion `chat.message`) ;
- `create_app` / `create_default_app` : l'app FastAPI (REST + WebSocket) et sa
  déclinaison de production (`maestro-api`).
"""

from __future__ import annotations

from maestro.controltower.analytics import (
    PAS_HEURE,
    PAS_JOUR,
    PAS_MINUTE,
    PAS_VALIDES,
    AnalyticsCouts,
    CoutAgent,
    CoutExecutionResume,
    CoutTacheAgregee,
    PointCout,
    agrege_couts,
)
from maestro.controltower.app import create_app, create_default_app
from maestro.controltower.bridge import (
    JournalEventHandler,
    activer_publication,
    evenements_depuis_step,
    publieur_redis,
)
from maestro.controltower.chat import (
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurChat,
    RepondeurModele,
    RepondeurScripte,
    ReponseIndisponible,
    ServiceChat,
)
from maestro.controltower.events import (
    CANAL_EVENEMENTS,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_CHAT_MESSAGE,
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
from maestro.controltower.persistence import (
    CLE_JOURNAL_EVENEMENTS,
    EventLog,
    InMemoryEventLog,
    RedisEventLog,
)
from maestro.controltower.state import (
    CAPACITE_ACTIVE,
    CAPACITE_DESACTIVE,
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
    "CLE_JOURNAL_EVENEMENTS",
    "CAPACITE_ACTIVE",
    "CAPACITE_DESACTIVE",
    "EVENEMENT_AGENT_ACTIVITE",
    "EVENEMENT_AGENT_CAPACITE",
    "EVENEMENT_CHAT_MESSAGE",
    "EVENEMENT_MESSAGE_INTER_AGENTS",
    "EVENEMENT_TACHE_REASSIGNATION",
    "EVENEMENT_TACHE_STATUT",
    "EVENEMENT_VALIDATION_DECISION",
    "EVENEMENT_VALIDATION_DEMANDE",
    "PAS_HEURE",
    "PAS_JOUR",
    "PAS_MINUTE",
    "PAS_VALIDES",
    "UTILISATEUR",
    "VALIDATION_APPROUVEE",
    "VALIDATION_EN_ATTENTE",
    "VALIDATION_REFUSEE",
    "AnalyticsCouts",
    "ChatStore",
    "ControlTowerState",
    "CoutAgent",
    "CoutExecutionResume",
    "CoutTacheAgregee",
    "EtatAgent",
    "EtatExecution",
    "EtatTache",
    "EtatValidation",
    "Event",
    "EventBus",
    "EventLog",
    "InMemoryEventBus",
    "InMemoryEventLog",
    "JournalEventHandler",
    "MessageChat",
    "PointCout",
    "RedisEventBus",
    "RedisEventLog",
    "RepondeurChat",
    "RepondeurModele",
    "RepondeurScripte",
    "ReponseIndisponible",
    "ServiceChat",
    "ValidateurControlTower",
    "activer_publication",
    "agrege_couts",
    "create_app",
    "create_default_app",
    "evenements_depuis_step",
    "publieur_redis",
    "validateur_redis",
]
