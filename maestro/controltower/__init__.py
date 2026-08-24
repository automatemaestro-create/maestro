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
  prise depuis l'UI ; depuis #227 il porte aussi l'**application du travail dans
  le projet de l'utilisateur** (`appliquer_sous_validation`, EF-37), diff en
  pièce jointe ;
- `maestro.controltower.chat` : le chat utilisateur ↔ agent (#84) — fil
  persisté (`ChatStore`), réponse produite par le fournisseur configuré
  (`RepondeurModele`) ou scriptée (`RepondeurScripte`), flux d'un envoi
  (`ServiceChat` : persistance, messagerie #44, diffusion `chat.message`) ;
- `maestro.controltower.executions` : le **pilotage des exécutions** (#185) —
  `ServiceExecutions` confie un run à son hôte, le suit dans la projection,
  l'interrompt à la demande et **ramasse** les hôtes morts sans issue (#446), ses
  étapes partant sur le bus par le pont télémétrie comme celles d'un run lancé en
  ligne de commande ;
- `maestro.controltower.battement` : le **signal de vie** d'un run (#348) —
  l'hôte bat (`RegistreBattements` côté API, `CoeurRun` côté process qui n'a pas
  de boucle), la lecture en tire un verdict (`vitalite` : vivant / orphelin /
  indéterminé) : un run dont l'hôte est tombé cesse de rester `en_cours` pour
  toujours. Depuis #446 un hôte **publie son issue** en partant et retire son
  battement (`bridge.solder_le_run`), si bien qu'`orphelin` ne désigne plus que ce
  qui est mort sans pouvoir le dire ;
- `maestro.controltower.hote` : le **contrat d'hôte de run** (#442) — ce à quoi
  une exécution est confiée (`HoteRun` : lancer un `OrdreRun`, annuler, observer
  ce qu'il porte et ce qu'il a vu mourir), sans que l'appelant sache où elle vit.
  `HoteRunDetache` (`maestro.controltower.hote_detache`, #443) est le **défaut**
  depuis #446 : un process indépendant par run, qui **survit à l'API**, publie sur
  le même Redis et y bat son cœur ; `HoteRunEnProcess`
  (`maestro.controltower.hote_en_process`) reste disponible sous
  `MAESTRO_HOTE_RUN=process` — la tâche de fond de l'API, dont les runs meurent
  avec elle. Le détaché **écoute** aussi ce même Redis (#444) : l'issue « annulee »
  qu'y consigne `ServiceExecutions._solder` est l'ordre par lequel l'annulation
  traverse la frontière, et le run annule alors sa propre tâche — donc
  `Task.cancel` reste le mécanisme réel, à un aller Redis près, quel que soit
  l'hôte. Les **trois attentes humaines** l'empruntent de même (#445) : décision
  sur le brief (#320), réponses de clarification (#321) et validation d'action
  sensible (#9/#48) s'y branchent par les mêmes arbitres que côté API, sur un bus
  unique par process — et le fail-safe est celui de toujours, un bus refermé sans
  décision faisant lever l'attente ou refuser l'action, jamais approuver.
  Ce dernier n'est **pas réexporté ici**, et pas par oubli : son module est aussi
  un point d'entrée (`python -m maestro.controltower.hote_detache`), et un module
  déjà importé par le paquet est ensuite exécuté **une seconde fois** comme
  `__main__` — Python le signale par un `RuntimeWarning` qui atterrirait en tête
  du seul fichier où l'on va chercher la cause d'un démarrage raté ;
- `maestro.controltower.assistance` : le canal d'**aide à l'utilisateur** (#123)
  — même infrastructure que le chat sur le fil réservé `assistance`, mais une
  fiche hors catalogue (`AGENT_ASSISTANCE`) et un répondeur déterministe
  (`RepondeurAssistance`) : les questions portent sur l'outil, pas sur le projet ;
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
from maestro.controltower.assistance import (
    AGENT_ASSISTANCE,
    NOM_ASSISTANCE,
    SUJETS_ASSISTANCE,
    RepondeurAssistance,
    SujetAssistance,
    repondre_assistance,
)
from maestro.controltower.battement import (
    CLE_BATTEMENTS,
    PERIODE_BATTEMENT_S,
    SEUIL_ORPHELIN_S,
    VITALITE_INDETERMINE,
    VITALITE_ORPHELIN,
    VITALITE_VIVANT,
    CoeurRun,
    RegistreBattements,
    RegistreBattementsMemoire,
    RegistreBattementsRedis,
    batteur_redis,
    oublieur_redis,
    vitalite,
)
from maestro.controltower.bridge import (
    JournalEventHandler,
    activer_publication,
    evenements_depuis_step,
    publieur_redis,
    solder_le_run,
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
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_PLAYBOOK_PROPOSITION,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    EtapeTache,
    Event,
    EventBus,
    InMemoryEventBus,
    LienUtile,
    RedisEventBus,
    ReferenceTicket,
)
from maestro.controltower.executions import (
    DELAI_ANNULATION_S,
    FabriqueMoteur,
    ServiceExecutions,
    moteur_par_defaut,
)
from maestro.controltower.fixtures import FixturesControlTower
from maestro.controltower.hote import (
    HOTE_RUN_DETACHE,
    HOTE_RUN_EN_PROCESS,
    HOTES_RUN,
    DemarrageHoteRate,
    HoteMort,
    HoteRun,
    OrdreRun,
)
from maestro.controltower.hote_en_process import DerouleurRun, HoteRunEnProcess
from maestro.controltower.journal import EntreeJournal, ServiceJournal
from maestro.controltower.persistence import (
    CLE_JOURNAL_EVENEMENTS,
    EventLog,
    InMemoryEventLog,
    RedisEventLog,
)
from maestro.controltower.state import (
    CAPACITE_ACTIVE,
    CAPACITE_DESACTIVE,
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
    ControlTowerState,
    EtatAgent,
    EtatExecution,
    EtatTache,
    EtatValidation,
)
from maestro.controltower.validation import (
    ValidateurControlTower,
    appliquer_sous_validation,
    validateur_redis,
)

__all__ = [
    "AGENT_ASSISTANCE",
    "CANAL_EVENEMENTS",
    "CLE_BATTEMENTS",
    "CLE_JOURNAL_EVENEMENTS",
    "CAPACITE_ACTIVE",
    "CAPACITE_DESACTIVE",
    "DELAI_ANNULATION_S",
    "EVENEMENT_AGENT_ACTIVITE",
    "EVENEMENT_AGENT_CAPACITE",
    "EVENEMENT_CHAT_MESSAGE",
    "EVENEMENT_EXECUTION_STATUT",
    "EVENEMENT_MESSAGE_INTER_AGENTS",
    "EVENEMENT_PLAYBOOK_PROPOSITION",
    "EVENEMENT_TACHE_REASSIGNATION",
    "EVENEMENT_TACHE_STATUT",
    "EVENEMENT_VALIDATION_DECISION",
    "EVENEMENT_VALIDATION_DEMANDE",
    "EXECUTION_ANNULEE",
    "EXECUTION_ECHEC",
    "EXECUTION_EN_COURS",
    "EXECUTION_TERMINEE",
    "HOTES_RUN",
    "HOTE_RUN_DETACHE",
    "HOTE_RUN_EN_PROCESS",
    "NOM_ASSISTANCE",
    "PAS_HEURE",
    "PAS_JOUR",
    "PAS_MINUTE",
    "PAS_VALIDES",
    "PERIODE_BATTEMENT_S",
    "SEUIL_ORPHELIN_S",
    "STATUTS_EXECUTION_TERMINAUX",
    "SUJETS_ASSISTANCE",
    "UTILISATEUR",
    "VALIDATION_APPROUVEE",
    "VALIDATION_EN_ATTENTE",
    "VALIDATION_REFUSEE",
    "VITALITE_INDETERMINE",
    "VITALITE_ORPHELIN",
    "VITALITE_VIVANT",
    "AnalyticsCouts",
    "ChatStore",
    "CoeurRun",
    "ControlTowerState",
    "CoutAgent",
    "CoutExecutionResume",
    "CoutTacheAgregee",
    "DemarrageHoteRate",
    "DerouleurRun",
    "EntreeJournal",
    "EtatAgent",
    "EtatExecution",
    "EtatTache",
    "EtatValidation",
    "EtapeTache",
    "Event",
    "EventBus",
    "EventLog",
    "FabriqueMoteur",
    "FixturesControlTower",
    "HoteMort",
    "HoteRun",
    "HoteRunEnProcess",
    "InMemoryEventBus",
    "InMemoryEventLog",
    "JournalEventHandler",
    "LienUtile",
    "MessageChat",
    "OrdreRun",
    "PointCout",
    "RedisEventBus",
    "RedisEventLog",
    "ReferenceTicket",
    "RegistreBattements",
    "RegistreBattementsMemoire",
    "RegistreBattementsRedis",
    "RepondeurAssistance",
    "RepondeurChat",
    "RepondeurModele",
    "RepondeurScripte",
    "ReponseIndisponible",
    "ServiceChat",
    "ServiceExecutions",
    "ServiceJournal",
    "SujetAssistance",
    "ValidateurControlTower",
    "activer_publication",
    "agrege_couts",
    "appliquer_sous_validation",
    "batteur_redis",
    "create_app",
    "create_default_app",
    "evenements_depuis_step",
    "moteur_par_defaut",
    "oublieur_redis",
    "publieur_redis",
    "repondre_assistance",
    "solder_le_run",
    "validateur_redis",
    "vitalite",
]
