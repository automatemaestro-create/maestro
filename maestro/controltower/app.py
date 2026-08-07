"""API Control Tower — REST + WebSocket temps réel (ticket #46).

Le backend du poste de pilotage (docs/05) : expose l'**état courant** de
l'orchestration en REST et **pousse les événements** en temps réel par
WebSocket. Stack retenue : FastAPI + WebSocket + Redis Pub/Sub (docs/02 §4).

Endpoints :

- `GET  /api/sante` — vitalité du service ;
- `GET  /api/taches` — les tâches (statut, agent, coût détaillé — tokens,
  durée) : la source du Kanban ;
- `GET  /api/agents` — l'état des agents (libre/occupé, charge, compteurs,
  capacité : actif/instances) ;
- `POST /api/agents/{nom}/capacite` — le contrôle de capacité (#86, EF-21) :
  active/désactive l'agent et/ou ajuste son plafond d'instances — persisté
  (`maestro.agents.capacity`) et relu à chaud par moteur et workers ;
- `GET  /api/executions` — les runs connus (en cours et passés), récents d'abord :
  objectif, statut, volume, coût et bornes temporelles (#185) ;
- `POST /api/executions` — **lance** une exécution (objectif + garde-fous) en
  tâche de fond et rend son `run_id` immédiatement (#185) ;
- `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `GET  /api/executions/{run_id}` — le détail d'une exécution (état, trace, coût) ;
- `GET  /api/executions/{run_id}/cout` — le grand livre du run (#57) : coût
  par tâche (tokens entrée/sortie, coût estimé, durée) et agrégat ;
- `GET  /api/analytics/couts` — la vue coûts & analytics (#87) : agrégats par
  tâche, par agent et par exécution, total et série temporelle du coût
  (`depuis` pour la période, `pas` pour la granularité des seaux) ;
- `POST /api/taches/{tache_id}/reassigner` — réassignation manuelle (Kanban) ;
- `GET  /api/validations` — les demandes de validation humaine (#48 : en
  attente d'abord le contexte, puis l'issue une fois tranchée). Une demande
  d'**application dans le projet** (#227, EF-37) y porte en plus son `diff` :
  les fichiers que l'accord écrirait et la branche qu'il fusionnerait ;
- `POST /api/validations/{tache_id}/decision` — la décision humaine
  (approuver/refuser) : le moteur, en attente sur le bus, reprend ou annule ;
- `GET  /api/playbooks` — les playbooks des agents (#76 : version courante et
  provenance — défaut du code ou stockage versionné) ;
- `GET  /api/playbooks/{agent}` — le playbook courant d'un agent (contenu) ;
- `PUT  /api/playbooks/{agent}` — publie une nouvelle version du playbook ;
- `GET  /api/playbooks/{agent}/versions` — l'historique des versions ;
- `GET  /api/playbooks/{agent}/versions/{version}` — une version passée ;
- `POST /api/playbooks/{agent}/restaurer` — retour arrière (EF-25) : republie
  une version passée comme nouvelle version courante ;
- `GET  /api/playbooks/{agent}/propositions` — les propositions d'auto-amélioration
  en brouillon (#111 : jamais courantes, jamais chargées tant que non appliquées) ;
- `POST /api/playbooks/{agent}/propositions` — analyse **à la demande** les échecs
  d'un run (`run_id`) et génère une proposition de révision du playbook (#139) ;
- `GET  /api/playbooks/{agent}/propositions/{numero}` — une proposition, contenu compris ;
- `POST /api/playbooks/{agent}/propositions/{numero}/appliquer` — l'action humaine (#140) :
  la proposition devient la version courante (chargée à chaud, #78) et quitte les brouillons ;
- `POST /api/playbooks/{agent}/propositions/{numero}/rejeter` — écarte la proposition sans
  toucher à la version courante ;
- `GET  /api/mcp/registre` — la bibliothèque curée de serveurs MCP (#131) :
  templates recherchables (`q`, par nom/tag) portant transport, gabarit `${VAR}`,
  mode d'auth (docs/21), variables à fournir et procédure côté outil ; seule une
  entrée servie ici est instanciable (garde-fou supply-chain, docs/19) ;
- `GET  /api/mcp/registre/{id}` — une entrée curée (404 hors allowlist) ;
- `GET  /api/mcp/pool` — le **pool projet** des intégrations MCP configurées
  (#133) : chaque intégration avec son mode d'auth et l'état (présent/valide)
  de ses secrets côté coffre projet — jamais une valeur de secret ;
- `POST /api/mcp/pool` — ajoute (ou reconfigure) une intégration au pool depuis
  la bibliothèque : instancie l'entrée curée (garde-fou supply-chain) et pose
  ses secrets **une seule fois** dans le coffre projet chiffré (#133) ;
- `DELETE /api/mcp/pool/{id}` — retire une intégration du pool, désactive son id
  chez chaque agent et purge les secrets qu'elle était seule à référencer ;
- `PUT  /api/mcp/activations/{agent}` — fixe les intégrations du pool **activées**
  pour un agent (#133) : l'écriture derrière l'interrupteur par agent qui
  remplace l'affichage lecture seule des serveurs MCP ;
- `GET  /api/projets` — les projets déclarés de l'utilisateur (#223, EF-35) :
  racine canonicalisée sur le disque, origine, `vcs` détecté et périmètre ;
- `GET  /api/projets/explorateur` — l'**explorateur de dossiers servi par
  l'API** (docs/05 §2.7) : énumère les sous-dossiers d'un chemin (marqueur
  « dépôt Git », projet déjà déclaré), borné aux **racines explorables** et aux
  zones non sensibles — un dépassement est un **refus motivé**, jamais une
  liste vide ;
- `GET  /api/projets/{id}` — un projet déclaré ;
- `POST /api/projets` — déclare un projet : racine **validée** (EF-38, refus
  motivé en 422) et VCS **constaté** sur le disque ;
- `PUT  /api/projets/{id}` — remplace la déclaration (l'intégrale, pas un diff) ;
- `DELETE /api/projets/{id}` — oublie un projet, sans jamais toucher au dossier
  sur le disque ;
- `GET  /api/catalogue` — le catalogue d'agents (#72, EF-03) : les agents par
  défaut du code et les personnalisés persistés, avec leur provenance, leurs
  serveurs MCP déclarés (#104, lecture seule — `mcp_serveurs`/`mcp_erreur`) et
  leur politique de permissions effective (#110, lecture seule —
  `permissions`/`permissions_erreur`) ;
- `GET  /api/catalogue/{nom}` — la définition complète d'un agent (playbook
  compris) ;
- `POST /api/catalogue` — crée un agent personnalisé (persisté hors du code,
  routable et exécutable par les moteurs construits ensuite) ;
- `PUT  /api/catalogue/{nom}` — remplace la définition d'un agent personnalisé ;
- `DELETE /api/catalogue/{nom}` — supprime un agent personnalisé ;
- `GET  /api/chat/{agent}` — le fil de conversation utilisateur ↔ agent (#84),
  persisté et relu du `ChatStore` ;
- `POST /api/chat/{agent}/messages` — envoie un message à l'agent et rend la
  paire message/réponse (le fil est aussi diffusé en `chat.message` sur le
  WebSocket, réponse comprise) ; `assistance` (#123) y désigne le **canal
  d'aide** — mêmes endpoints, fiche hors catalogue et réponses sans modèle
  (`maestro.controltower.assistance`) ;
- `WS   /ws/evenements` — le flux d'événements (statuts de tâches, activité
  des agents, messages inter-agents, validations, chat), au format
  `Event.to_dict`.

Contrats d'API **v2** (#183 — formes figées des Phases 5/6, servies en fixtures
par la démo ; **501** en production tant que leur lot n'est pas livré) :

- `GET  /api/executions` — la liste des runs (résumés : statut, coût, dates,
  ticket) ; `POST /api/executions` — lance un run (objectif + garde-fous) ;
  `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `GET  /api/journal` — le journal requêtable (filtres agent / type / run /
  période, tri, pagination) ;
- `GET  /api/configuration` — le registre de configuration éditable (réglages
  produit, couche 1 du cadrage sécurité #182) ;
- `GET  /api/playbooks/propositions` — les propositions d'auto-amélioration
  **tous agents confondus** (badge + notifications) ;
- `GET  /api/chat/{agent}/flux` — le flux **SSE** d'une réponse de chat (trames
  `debut`/`fragment`/`fin`).

Assemblage : une **pompe** unique s'abonne au bus (`EventBus`), projette chaque
événement sur l'état (`ControlTowerState`), le **consigne** au journal durable
(`EventLog`, #97) puis le rediffuse aux WebSockets connectées — l'ordre « état
d'abord, diffusion ensuite » garantit qu'un client qui reçoit un événement lit
un REST déjà à jour. Au démarrage, le lifespan **rejoue** le journal pour
reconstruire la projection (exécutions, grands livres, analytics, tâches,
agents, validations) d'avant un redémarrage de l'API : l'état survit à la vie du
process (#97). `create_app` s'injecte bus, état et journal (les tests d'API
tournent sur `InMemoryEventBus`/`InMemoryEventLog`, sans Redis) ;
`create_default_app` câble le `RedisEventBus` et le `RedisEventLog` de production
(canal `maestro.evenements`, alimenté par `maestro.controltower.bridge` côté
moteur ; journal persistant sur la liste `maestro.evenements:journal`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import IntegrationMcp, McpStore, references_env
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import NOMS_RESERVES, AgentDefinition, AgentStore, catalogue
from maestro.config import load_settings
from maestro.controltower.analytics import PAS_HEURE, PAS_VALIDES, agrege_couts
from maestro.controltower.assistance import (
    AGENT_ASSISTANCE,
    NOM_ASSISTANCE,
    RepondeurAssistance,
)
from maestro.controltower.auto_amelioration import (
    AnalyseurEchecs,
    RevisionIndisponible,
    echecs_du_run,
)
from maestro.controltower.chat import (
    ChatStore,
    RepondeurChat,
    RepondeurModele,
    ReponseIndisponible,
    ServiceChat,
)
from maestro.controltower.events import (
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_REFERENCE,
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.executions import FabriqueMoteur, ServiceExecutions
from maestro.controltower.fixtures import (
    ORDRE_DESC,
    ORDRES_JOURNAL,
    TAILLE_PAGE_DEFAUT,
    TAILLE_PAGE_MAX,
    TRI_JOURNAL_HORODATAGE,
    TRIS_JOURNAL,
    FixturesControlTower,
)
from maestro.controltower.persistence import (
    EventLog,
    InMemoryEventLog,
    RedisEventLog,
)
from maestro.controltower.portee import PorteeProjet, PorteeRefusee, resoudre_portee
from maestro.controltower.projets import (
    ProjetInconnu,
    ServiceProjets,
    detail_refus,
    statut_http,
)
from maestro.controltower.state import (
    CAPACITE_ACTIVE,
    CAPACITE_DESACTIVE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)
from maestro.messaging import InMemoryMailbox, Mailbox, RedisMailbox
from maestro.references import ReferenceTicket


class ReferenceTicketRequete(BaseModel):
    """La référence d'un ticket externe posée au lancement d'un run (#185, contrat #183).

    Générique : un identifiant lisible (`id`, ex. « #185 », « PROJ-42 ») et son
    `url` — vide quand seul l'identifiant est connu. GitLab, Jira ou Linear
    passent par la même forme, aucun champ propre à un outil. La référence
    descend ensuite jusqu'aux tâches du run (contrat #183).
    """

    id: str
    url: str = ""

    def en_reference(self) -> ReferenceTicket | None:
        """La référence du modèle métier — None si elle n'apprend rien (ni id, ni URL).

        Passe par `ReferenceTicket.depuis`, donc par la normalisation (#187) :
        identifiant borné, URL non suivable (`javascript:`, chemin relatif)
        jetée. Ce qui entre par l'API est traité comme ce qui arrive par le
        flux — non fiable jusqu'à validation.
        """
        return ReferenceTicket.depuis({"id": self.id, "url": self.url})


class LancementExecutionRequete(BaseModel):
    """Corps de lancement d'une exécution (#185) : objectif, garde-fous, ticket.

    `objectif` est l'énoncé en langage naturel que l'orchestrateur décompose ;
    les garde-fous (#9) plafonnent l'exécution — coût, tokens, time-out par
    tâche, parallélisme —, chacun optionnel, None laissant le défaut du moteur.
    `ticket` rattache le run à un ticket externe (optionnel). `projet_id` (#222)
    le rattache au **projet** dans lequel il travaille (optionnel, `null` :
    aucun projet — le comportement d'avant ce lot). Une requête invalide
    (objectif vide, garde-fou hors bornes) est refusée en 422 ; un `projet_id`
    mal formé, lui, est **écarté** (le run part sans projet) plutôt que de faire
    échouer le lancement — cf. `maestro.appartenance`.
    """

    objectif: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None
    ticket: ReferenceTicketRequete | None = None
    projet_id: str | None = None


class ReassignationRequete(BaseModel):
    """Corps de la réassignation manuelle : l'agent qui reprend la tâche."""

    agent: str


class DecisionRequete(BaseModel):
    """Corps de la décision humaine (#48) : approuver ou refuser l'action sensible."""

    approuve: bool


class CapaciteRequete(BaseModel):
    """Corps du contrôle de capacité (#86) : les réglages à poser, champ par champ.

    Chaque champ est optionnel — None laisse la valeur en place : on peut
    désactiver sans toucher aux instances, et inversement. Au moins un champ
    doit être renseigné (sinon 422 : il n'y a rien à régler).
    """

    actif: bool | None = None
    instances: int | None = None


class PlaybookEcritureRequete(BaseModel):
    """Corps d'écriture d'un playbook (#76) : le nouveau contenu Markdown, intégral."""

    contenu: str


class PlaybookRestaurationRequete(BaseModel):
    """Corps du retour arrière (#76) : la version passée à republier comme courante."""

    version: int


class PlaybookPropositionRequete(BaseModel):
    """Corps d'une demande de proposition d'auto-amélioration (#139) : le run à analyser.

    `run_id` désigne l'exécution dont les échecs de l'agent alimentent l'analyse. Le
    déclenchement est **à la demande** (ce POST), jamais automatique en fin de run.
    """

    run_id: str


class AgentCreationRequete(BaseModel):
    """Corps de création d'un agent personnalisé (#72) : sa définition complète.

    `modele` est optionnel (None : le modèle par défaut des exécutants) ;
    `fournisseur` est déclaratif au POC (le moteur est mono-fournisseur).
    """

    nom: str
    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None


class AgentModificationRequete(BaseModel):
    """Corps de modification d'un agent personnalisé (#72) : la définition intégrale.

    Un remplacement, pas un diff — le nom, lui, vit dans l'URL et ne change pas
    (c'est la clé de routage et de stockage).
    """

    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None


class ProjetRequete(BaseModel):
    """Corps de déclaration d'un projet (#223) : sa racine sur le disque et son périmètre.

    `racine` est le chemin **absolu** du projet sur le poste ; il est
    canonicalisé et confronté aux racines interdites côté serveur (EF-38) — ce
    qui arrive ici est une saisie, jamais une vérité. `origine` vaut `existant`
    (on reprend un dossier) ou `nouveau` (on l'initie : le dossier est créé s'il
    manque). `inclus`/`exclus` sont des motifs **relatifs à la racine** ; `null`
    laisse les défauts du modèle (`.`, et l'exclusion de `.git`, `node_modules`,
    `.env`, `**/secrets/**`).

    Le `vcs` n'est délibérément **pas** un champ de requête : il est constaté sur
    le disque, jamais déclaré — un client qui l'annoncerait pourrait mentir.
    """

    nom: str
    racine: str
    origine: str = "existant"
    inclus: list[str] | None = None
    exclus: list[str] | None = None


class ChatEnvoiRequete(BaseModel):
    """Corps d'un envoi de chat (#84) : le message de l'utilisateur à l'agent."""

    contenu: str


class SecretPoolRequete(BaseModel):
    """Une valeur de secret saisie pour une intégration du pool (#133).

    `cle` est le nom de la variable `${VAR}` du gabarit (ex. `GITLAB_TOKEN`),
    `valeur` ce que l'humain saisit (token, canal d'appairage, token OAuth
    importé). `expire_le` (ISO 8601) n'a de sens que pour un token OAuth
    importé (`oauth_importe`) : c'est l'échéance qui rend sa validité visible.
    Une valeur vide est ignorée (le secret reste à configurer).
    """

    cle: str
    valeur: str
    expire_le: str | None = None


class IntegrationPoolRequete(BaseModel):
    """Corps d'ajout d'une intégration au pool projet (#133) depuis la bibliothèque.

    `registre_id` désigne l'entrée **curée** à instancier (garde-fou
    supply-chain, docs/19 — un id hors registre est refusé). `nom` nomme la
    liaison (le préfixe d'outils `mcp__<nom>__…`, défaut : l'id) ; `secrets`
    porte les valeurs saisies **une seule fois**, stockées chiffrées côté
    serveur dans le coffre projet partagé.
    """

    registre_id: str
    nom: str | None = None
    secrets: list[SecretPoolRequete] = []


class ActivationsMcpRequete(BaseModel):
    """Corps de l'activation des intégrations du pool pour un agent (#133).

    Remplacement intégral : la liste `integrations` (des ids du pool) devient
    l'ensemble activé de l'agent — une liste vide le désactive de toutes. C'est
    ce que pose l'interrupteur par agent qui **remplace** l'affichage lecture
    seule des serveurs MCP (critère #133).
    """

    integrations: list[str]


class Diffusion:
    """Fan-out des événements vers les WebSockets connectées, **à leur portée**.

    Une file par connexion, alimentée par la pompe ; chaque handler WebSocket
    draine la sienne. Files non bornées : le POC diffuse peu d'événements et
    une connexion les consomme au fil de l'eau.

    Chaque connexion déclare sa **portée projet** à l'ouverture (#277) et ne
    reçoit que les événements que celle-ci retient — le tri se fait donc **à
    l'entrée de la file**, pas à l'émission : une socket cadrée sur un projet
    n'est même pas réveillée par le travail d'un autre, et aucun événement
    étranger ne peut être servi par erreur en aval. C'est la même règle que les
    vues REST (`PorteeProjet.retient`), appliquée au flux : un client qui
    recharge son état puis suit les événements voit deux fois le même périmètre.
    """

    def __init__(self) -> None:
        self._connexions: dict[asyncio.Queue[Event], PorteeProjet] = {}

    def connecter(self, portee: PorteeProjet | None = None) -> asyncio.Queue[Event]:
        """Enregistre une connexion : sa file recevra les événements de sa portée.

        `None` vaut la vue transverse — le comportement d'avant #277, conservé
        pour les appels internes ; les routes, elles, exigent une portée.
        """
        file: asyncio.Queue[Event] = asyncio.Queue()
        self._connexions[file] = portee if portee is not None else PorteeProjet.tous()
        return file

    def deconnecter(self, file: asyncio.Queue[Event]) -> None:
        """Retire la connexion ; ses événements en attente disparaissent avec elle."""
        self._connexions.pop(file, None)

    def diffuser(self, event: Event) -> None:
        """Pousse `event` aux connexions dont la portée le retient."""
        for file, portee in self._connexions.items():
            if portee.retient(event.projet_id):
                file.put_nowait(event)


_LOGGER = logging.getLogger("maestro.controltower")


async def _pompe(
    bus: EventBus,
    state: ControlTowerState,
    diffusion: Diffusion,
    event_log: EventLog,
) -> None:
    """Le seul consommateur du bus : projette sur l'état, **persiste**, puis rediffuse.

    Cet ordre rend le flux cohérent pour les clients : à réception d'un
    événement WebSocket, l'état REST le reflète déjà. Chaque événement est
    consigné au journal durable (#97) entre la projection et la diffusion —
    c'est la mémoire longue qui, rejouée au démarrage, reconstruit l'état après
    un redémarrage de l'API. Une panne de **persistance** (Redis injoignable le
    temps d'un événement) est tracée mais n'interrompt pas le flux temps réel :
    le seul risque est que cet événement manque au prochain rejeu. Une panne du
    **bus**, elle, arrête le flux temps réel mais pas l'API : le REST continue
    de servir l'état déjà projeté — la panne est tracée, pas avalée.
    """
    try:
        async for event in bus.subscribe():
            state.appliquer(event)
            try:
                await event_log.consigner(event)
            except Exception:
                _LOGGER.exception(
                    "Échec de persistance d'un événement : il manquera au prochain "
                    "rejeu au démarrage (flux temps réel et projection préservés)."
                )
            diffusion.diffuser(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception(
            "La pompe d'événements s'est arrêtée : flux temps réel interrompu "
            "(le REST reste servi sur le dernier état projeté)."
        )


def create_app(
    *,
    bus: EventBus | None = None,
    state: ControlTowerState | None = None,
    playbooks: PlaybookStore | None = None,
    agents_store: AgentStore | None = None,
    mailbox: Mailbox | None = None,
    chat_store: ChatStore | None = None,
    chat_repondeur: RepondeurChat | None = None,
    assistance_repondeur: RepondeurChat | None = None,
    analyseur: AnalyseurEchecs | None = None,
    capacites: CapacityStore | None = None,
    mcp: McpStore | None = None,
    registre_mcp: RegistreMcp | None = None,
    secrets: SecretStore | None = None,
    permissions: PermissionStore | None = None,
    projets: ServiceProjets | None = None,
    event_log: EventLog | None = None,
    fixtures: FixturesControlTower | None = None,
    fabrique_moteur: FabriqueMoteur | None = None,
) -> FastAPI:
    """Construit l'app FastAPI de la Control Tower autour d'un bus et d'un état.

    Par défaut : bus mémoire et état neuf (agents du catalogue, statut libre) —
    la configuration des tests et d'une démo mono-process. La production passe
    par `create_default_app` (bus Redis). La pompe vit avec l'app (lifespan) :
    démarrée à l'ouverture, annulée à l'arrêt, bus refermé derrière elle.

    `playbooks` (#76) est le dépôt versionné servi par les endpoints
    `/api/playbooks` — par défaut celui de la config (`MAESTRO_PLAYBOOKS_DIR`,
    sinon `core/playbooks/` du dépôt) ; les tests en injectent un temporaire.

    `agents_store` (#72) est le dépôt des agents personnalisés servi par les
    endpoints `/api/catalogue` — par défaut celui de la config
    (`MAESTRO_AGENTS_DIR`, sinon `core/agents/` du dépôt). L'état par défaut se
    construit sur le catalogue **effectif** : les agents personnalisés déjà
    persistés sont présents dès le démarrage, comme ceux du code.

    `mailbox`, `chat_store` et `chat_repondeur` (#84) portent le chat
    utilisateur ↔ agent des endpoints `/api/chat` : la messagerie inter-agents
    où transitent les échanges (mémoire par défaut, Redis en production), le
    fil persisté (`MAESTRO_CHAT_DIR`, sinon `core/chat/` du dépôt) et la
    production de la réponse — par défaut le fournisseur configuré, cadré par
    le playbook courant de l'agent ; la démo (#65) et les tests injectent un
    répondeur scripté.

    `assistance_repondeur` (#123) porte le **canal d'aide** `/api/chat/assistance` :
    un second `ServiceChat` sur le même fil persisté, la même messagerie et le même
    bus que le chat, mais avec son propre répondeur — par défaut
    `RepondeurAssistance`, déterministe et sans modèle (les questions portent sur
    l'outil, pas sur le projet ; voir `maestro.controltower.assistance`).

    `analyseur` (#139) produit les propositions d'auto-amélioration servies par
    `POST /api/playbooks/{agent}/propositions` : à la demande, il analyse les
    échecs d'un run via la couche fournisseur et enregistre un brouillon (#138).
    Par défaut il partage le dépôt `playbooks` et résout son fournisseur par
    config ; les tests (#137) en injectent un à fournisseur factice.

    `capacites` (#86) est le dépôt du contrôle de capacité servi par
    `POST /api/agents/{nom}/capacite` — par défaut celui de la config
    (`MAESTRO_CAPACITE_DIR`, sinon `core/capacite/` du dépôt) : le même que
    relisent moteur et workers, pour que le réglage ait un effet réel. L'état
    par défaut est amorcé des réglages déjà persistés (un état injecté reste,
    lui, tel quel — la configuration des tests).

    `mcp` (#104) est le dépôt des serveurs MCP déclarés par agent, affichés en
    **lecture seule** sur les fiches du catalogue (`mcp_serveurs`, valeurs de
    secrets masquées) — par défaut celui de la config (`MAESTRO_MCP_DIR`, sinon
    `core/mcp/` du dépôt) : le même que montent moteur et workers.

    `registre_mcp` (#131) est la **bibliothèque curée** de serveurs MCP servie
    par `/api/mcp/registre` : des templates recherchables (nom/tag) portant
    transport, gabarit `${VAR}`, mode d'auth (docs/21) et procédure côté outil —
    par défaut le seed en code (`RegistreMcp.curee()`, l'allowlist supply-chain
    du garde-fou docs/19). Les tests en injectent un registre restreint.

    `secrets` (#132/#133) est le **coffre chiffré** des secrets d'intégrations :
    l'UI d'écriture (`POST /api/mcp/pool`) y pose les secrets du pool projet
    **une seule fois** (coffre projet, `SecretStore.enregistrer_projet`), partagés
    par tout agent qui active l'intégration — par défaut celui de la config
    (`MAESTRO_SECRETS_DIR`, sinon `core/secrets/` du dépôt), le même que résolvent
    moteur et workers au montage. Les tests en injectent un coffre temporaire.

    `permissions` (#110) est le dépôt des politiques allow/deny par agent,
    affichées en **lecture seule** sur les fiches du catalogue (`permissions`,
    la politique effective appliquée à l'exécution) — par défaut celui de la
    config (`MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/` du dépôt) :
    le même que relisent moteur et workers.

    `projets` (#223) porte le CRUD des projets de l'utilisateur et l'explorateur
    de dossiers servis par `/api/projets` — par défaut le service de la config
    (dépôt `MAESTRO_PROJETS_DIR`, sinon `core/projets/` du dépôt ; racines
    explorables `MAESTRO_EXPLORATEUR_RACINES`, sinon le dossier utilisateur).
    Les tests en injectent un service sur dépôt temporaire, seule façon de fixer
    les racines explorables sans dépendre du poste qui joue la suite.

    `event_log` (#97) est le **journal durable des événements** que la pompe
    consigne et que le lifespan **rejoue au démarrage** pour reconstruire la
    projection (exécutions, grands livres, analytics, tâches, agents,
    validations) après un redémarrage de l'API — par défaut un journal mémoire
    (pas de durabilité inter-redémarrage : la configuration des tests et d'une
    démo mono-process). La production câble `RedisEventLog` via
    `create_default_app`. Un état injecté (`state`) reste tel quel puis reçoit
    le rejeu par-dessus (idempotent : les événements reconstruisent le même
    état).

    `fixtures` (#183) branche les **contrats d'API v2** (routes des Phases 5/6 :
    exécutions, journal requêtable, registre de configuration, propositions de
    playbook globales, flux SSE d'un fil de chat) sur des **données factices**.
    None (production) : ces routes répondent **501** — le contrat est stable, son
    lot d'implémentation n'est pas encore livré. Fourni (la démo, #65) : elles
    servent les fixtures, et la voie front code contre elles sans backend réel.

    `fabrique_moteur` (#185) construit le moteur de chaque exécution lancée par
    `POST /api/executions` — par défaut `OrchestrationEngine.default`, résolu au
    **premier lancement** et non à la construction de l'app : une API qui ne
    lance aucun run ne résout aucun fournisseur. Les tests en injectent une
    fabrique factice pour exercer le pilotage sans appeler de modèle.
    """
    bus = bus if bus is not None else InMemoryEventBus()
    event_log = event_log if event_log is not None else InMemoryEventLog()
    agents_store = agents_store if agents_store is not None else AgentStore.default()
    capacites = capacites if capacites is not None else CapacityStore.default()
    mcp = mcp if mcp is not None else McpStore.default()
    registre_mcp = registre_mcp if registre_mcp is not None else RegistreMcp.curee()
    secrets = secrets if secrets is not None else SecretStore.default()
    permissions = permissions if permissions is not None else PermissionStore.default()
    projets = projets if projets is not None else ServiceProjets.default()
    state = (
        state
        if state is not None
        else ControlTowerState(catalogue(agents_store), capacites=capacites.lister())
    )
    playbooks = playbooks if playbooks is not None else PlaybookStore.default()
    analyseur = analyseur if analyseur is not None else AnalyseurEchecs(playbooks=playbooks)
    mailbox = mailbox if mailbox is not None else InMemoryMailbox()
    chat_store = chat_store if chat_store is not None else ChatStore.default()
    chat = ServiceChat(
        store=chat_store,
        repondeur=(
            chat_repondeur if chat_repondeur is not None else RepondeurModele(playbooks=playbooks)
        ),
        mailbox=mailbox,
        bus=bus,
    )
    # Le canal d'aide (#123) : mêmes rouages que le chat — persistance, messagerie,
    # bus — pour que le fil se relise et se diffuse à l'identique ; seul le
    # répondeur change, l'assistant ne parlant pas du projet mais de l'outil.
    assistance = ServiceChat(
        store=chat_store,
        repondeur=(
            assistance_repondeur if assistance_repondeur is not None else RepondeurAssistance()
        ),
        mailbox=mailbox,
        bus=bus,
    )
    diffusion = Diffusion()
    # Pilotage des exécutions (#185) : lance sur le bus et la projection de
    # cette app — le run est donc suivi par les mêmes rouages que n'importe
    # quelle orchestration observée.
    executions = ServiceExecutions(bus, state, fabrique_moteur=fabrique_moteur)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Rejeu du journal durable (#97) **avant** d'ouvrir la pompe : la
        # projection retrouve l'historique (exécutions, grands livres, analytics)
        # d'avant le redémarrage, puis la pompe prend le relais du flux à venir.
        # Un journal illisible (Redis absent au démarrage…) est tracé sans bloquer
        # l'API : elle repart sur la projection courante (vide en production).
        try:
            for event in await event_log.relire():
                state.appliquer(event)
        except Exception:
            _LOGGER.exception(
                "Rejeu du journal des événements impossible : démarrage sur la "
                "projection courante (l'historique persisté n'a pas pu être relu)."
            )
        pompe = asyncio.create_task(_pompe(bus, state, diffusion, event_log))
        try:
            yield
        finally:
            # Les runs en vol s'arrêtent **avant** la pompe et le bus : leur
            # issue (annulation) a encore un canal pour être consignée.
            await executions.fermer()
            pompe.cancel()
            with suppress(asyncio.CancelledError):
                await pompe
            await mailbox.close()
            await bus.close()
            await event_log.close()

    app = FastAPI(
        title="Maestro — Control Tower",
        description="État de l'orchestration (REST) et flux d'événements (WebSocket).",
        lifespan=lifespan,
    )
    # L'UI (apps/web, ticket #47) est servie sur une autre origine que l'API
    # (Next.js sur :3000, API sur :8000) : sans CORS le navigateur bloque les
    # appels REST. Origines ouvertes au POC — l'API n'écoute qu'en local
    # (127.0.0.1, cf. cli.py) et ne porte aucune authentification à restreindre.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/sante")
    async def sante() -> dict[str, str]:
        """Vitalité du service (sonde de supervision)."""
        return {"statut": "ok"}

    # --- Contrats d'API v2 (#183) : routes des Phases 5/6, formes JSON figées ---
    # Le contrat (chemin, méthode, forme) est stable ; l'implémentation réelle
    # vient dans les lots dédiés (Phase 5, #184+). Sans `fixtures` (production),
    # ces routes répondent 501 ; branchées sur les fixtures (la démo), elles
    # servent des données factices contre lesquelles la voie front code (docs/05 §6).
    def _exige_fixtures() -> FixturesControlTower:
        """Les fixtures v2, ou un 501 explicite tant que le lot réel n'est pas livré."""
        if fixtures is None:
            raise HTTPException(
                status_code=501,
                detail=(
                    "route de contrat (API v2) non encore implémentée : servie en "
                    "fixtures par la démo (maestro.controltower.demo, #183)."
                ),
            )
        return fixtures

    def _portee(projet: str | None) -> PorteeProjet:
        """La **portée projet** d'une lecture (#277), ou un refus motivé.

        Le contrat unique des vues qui agrègent — Kanban, exécutions, coûts,
        validations, journal, flux temps réel : `?projet=<id>` pour un projet,
        `?projet=tous` pour la vue transverse, `?projet=aucun` pour les travaux
        hors projet. **Omis, le paramètre est refusé** (422 `projet-requis`) :
        une lecture sans périmètre n'est plus un mélange silencieux de tous les
        projets, et un refus motivé se diagnostique là où une liste vide se
        confondrait avec un projet sans activité. Un identifiant inconnu du
        dépôt sort en 404 `projet-inconnu`, par la même porte que les refus de
        `ServiceProjets` (#223) — motif, message, code.
        """
        try:
            return resoudre_portee(projet, projet_connu=projets)
        except PorteeRefusee as exc:
            raise _refus_projet(exc) from exc

    @app.get("/api/journal")
    async def journal_requetable(
        agent: str | None = None,
        type: str | None = None,
        run_id: str | None = None,
        projet: str | None = None,
        depuis: str | None = None,
        jusqua: str | None = None,
        tri: str = TRI_JOURNAL_HORODATAGE,
        ordre: str = ORDRE_DESC,
        page: int = 1,
        taille: int = TAILLE_PAGE_DEFAUT,
    ) -> dict[str, Any]:
        """Le journal requêtable : filtres (agent / type / run / projet / période), tri, pagination.

        `depuis`/`jusqua` sont des horodatages ISO-8601 (bornes incluses).
        `projet` est **obligatoire** (#277) et suit le contrat commun :
        `<id>` | `tous` | `aucun` — 422 `projet-requis` s'il manque, 404
        `projet-inconnu` sur un identifiant non déclaré. 422 aussi sur un
        `tri`/`ordre` inconnu, une `page` < 1 ou une `taille` hors [1, {max}].
        """
        # La gate 501 passe **avant** la portée : une route dont le lot n'est pas
        # livré doit le dire, plutôt que reprocher un paramètre à qui l'appelle.
        fx = _exige_fixtures()
        portee = _portee(projet)
        if tri not in TRIS_JOURNAL:
            raise HTTPException(
                status_code=422,
                detail=f"tri invalide : {tri} (attendus : {', '.join(TRIS_JOURNAL)}).",
            )
        if ordre not in ORDRES_JOURNAL:
            raise HTTPException(
                status_code=422,
                detail=f"ordre invalide : {ordre} (attendus : {', '.join(ORDRES_JOURNAL)}).",
            )
        if page < 1:
            raise HTTPException(status_code=422, detail=f"page invalide : {page} (attendu ≥ 1).")
        if not 1 <= taille <= TAILLE_PAGE_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"taille invalide : {taille} (attendu entre 1 et {TAILLE_PAGE_MAX}).",
            )
        return fx.journal(
            agent=agent,
            type=type,
            run_id=run_id,
            portee=portee,
            depuis=depuis,
            jusqua=jusqua,
            tri=tri,
            ordre=ordre,
            page=page,
            taille=taille,
        )

    @app.get("/api/configuration")
    async def configuration() -> dict[str, Any]:
        """Le registre de configuration éditable (couche 1 du cadrage sécurité #182).

        Les réglages produit (fournisseur, modèle, plafonds, isolation,
        intégrations, rétention) : type, valeur courante (masquée si secret),
        valeur par défaut, s'ils sont modifiables. Liste blanche stricte : aucune
        écriture arbitraire de variable d'environnement.
        """
        return _exige_fixtures().configuration()

    # Enregistrée **avant** `/api/playbooks/{agent}` (plus bas) pour que le chemin
    # littéral l'emporte sur la capture `{agent}` (sinon agent = "propositions").
    @app.get("/api/playbooks/propositions")
    async def propositions_playbook_globales() -> list[dict[str, Any]]:
        """Les propositions d'auto-amélioration, **tous agents confondus** (#111 global).

        L'agrégat transverse qui alimente le badge d'attente et les notifications
        (cadrage #182, items 8/9) — chaque proposition enrichie du `role` de son
        agent. Le pendant temps réel est l'événement `playbook.proposition` du bus.
        """
        return _exige_fixtures().propositions_playbook()

    @app.get("/api/taches")
    async def taches(projet: str | None = None) -> list[dict[str, Any]]:
        """Les tâches connues : statut, agent, coût détaillé (#57) — la source du Kanban.

        `projet` est **obligatoire** (#277) : `<id>` cadre le Kanban sur un
        projet, `tous` rend la vue transverse, `aucun` les tâches hors projet.
        Omis, 422 `projet-requis` ; inconnu, 404 `projet-inconnu`. Une tâche
        sans projet n'apparaît dans la vue d'aucun projet — on ne devine pas à
        quel projet elle appartiendrait.
        """
        return [t.to_dict() for t in state.taches(_portee(projet))]

    @app.get("/api/agents")
    async def agents() -> list[dict[str, Any]]:
        """L'état des agents : libre/occupé, tâche courante, compteurs, coût cumulé."""
        return [a.to_dict() for a in state.agents()]

    @app.get("/api/executions")
    async def executions_liste(projet: str | None = None) -> list[dict[str, Any]]:
        """Les runs connus (#185) : résumés, **récents d'abord**.

        En cours comme passés, lancés depuis la Control Tower comme publiés par
        un autre process (`maestro-run --publier`) : le suivi ne distingue pas
        leur origine — la projection est la même. C'est la source de l'écran
        *Exécutions & traces* (docs/05 §2.4). `projet` est **obligatoire**
        (#277), au contrat commun : `<id>` | `tous` | `aucun`.
        """
        return executions.resumes(_portee(projet))

    @app.post("/api/executions", status_code=202)
    async def lancer_execution(requete: LancementExecutionRequete) -> dict[str, Any]:
        """Lance une exécution (#185) et rend son résumé, `run_id` compris, **aussitôt**.

        Le run se déroule **hors** de la requête HTTP (tâche de fond de l'API) :
        la réponse ne dit pas ce qu'il a produit, elle dit qu'il est parti. La
        suite arrive par le flux d'événements existant — chaque étape devient un
        `tache.statut` du WebSocket et une ligne du Kanban — et se relit sur
        `GET /api/executions/{run_id}`. 422 sur un objectif vide ou un garde-fou
        hors bornes (les plafonds sont des maximums : ils doivent être > 0).
        """
        try:
            return executions.lancer(
                requete.objectif,
                plafond_cout_usd=requete.plafond_cout_usd,
                plafond_tokens=requete.plafond_tokens,
                timeout_tache_s=requete.timeout_tache_s,
                parallelisme=requete.parallelisme,
                ticket=None if requete.ticket is None else requete.ticket.en_reference(),
                projet_id=requete.projet_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/executions/{run_id}/annuler")
    async def annuler_execution(run_id: str) -> dict[str, Any]:
        """Interrompt un run en cours (#185) : rend son résumé passé à « annulée ».

        L'issue est consignée comme n'importe quel fait du run : elle apparaît
        dans la projection (statut `annulee`, `fin` posée) **et** sur le flux
        d'événements. 404 si le run est inconnu, 409 s'il est déjà soldé — un run
        terminé n'est plus interruptible, et le dire vaut mieux que faire croire
        à une annulation.
        """
        resume = executions.resume(run_id)
        if resume is None:
            raise HTTPException(
                status_code=404,
                detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
            )
        if resume["statut"] in STATUTS_EXECUTION_TERMINAUX:
            raise HTTPException(
                status_code=409,
                detail=f"exécution déjà soldée ({resume['statut']}) : {run_id}.",
            )
        annulee = await executions.annuler(run_id)
        if annulee is None:  # pragma: no cover - le résumé vient d'être lu
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return annulee

    @app.get("/api/executions/{run_id}")
    async def execution(run_id: str) -> dict[str, Any]:
        """L'état d'une exécution (#185) : son résumé, sa trace et son coût.

        Le résumé de `GET /api/executions` (objectif, statut, volume, bornes)
        enrichi de ce qu'il ne porte pas : le grand livre du run (#57) et sa
        trace événement par événement.
        """
        detail = state.execution(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return {**detail.to_dict(), **(executions.resume(run_id) or {})}

    @app.get("/api/executions/{run_id}/cout")
    async def cout_execution(run_id: str) -> dict[str, Any]:
        """Le grand livre d'une exécution (#57) : coût par tâche et agrégat du run.

        La comptabilité du lot #55 (forme `RunCost.to_dict`) reconstruite du
        flux d'événements : `planification` (l'usage de l'orchestrateur),
        `taches` (tokens entrée/sortie, coût estimé, durée par tâche) et
        `total` (l'agrégat de l'exécution) — sans la trace événement par
        événement du détail. 404 si aucune trace reçue pour ce `run_id`.
        """
        detail = state.execution(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return detail.cout.to_dict()

    @app.get("/api/analytics/couts")
    async def analytics_couts(
        depuis: str | None = None, pas: str = PAS_HEURE, projet: str | None = None
    ) -> dict[str, Any]:
        """La vue coûts & analytics (#87) : agrégats transverses et série temporelle.

        Recalculée des exécutions projetées, avec la même convention
        d'attribution que le grand livre d'un run (#57) : coût agrégé par
        tâche, par agent (planification comprise) et par exécution, total, et
        série temporelle du coût en seaux de `pas` (minute/heure/jour).
        `depuis` (ISO-8601, réputé UTC sans fuseau) restreint la fenêtre — la
        période sélectionnable de l'UI. `projet` est **obligatoire** (#277) et
        restreint la dépense : seuls les événements que la portée retient
        comptent, planification comprise — un travail sans projet n'entre dans
        le total d'aucun projet. La réponse rappelle la `portee` servie, un
        total ne se lisant pas sans savoir de quoi il est le total. 422 sur un
        `pas` ou un `depuis` invalide.
        """
        portee = _portee(projet)
        if pas not in PAS_VALIDES:
            raise HTTPException(
                status_code=422,
                detail=f"pas invalide : {pas} (attendus : {', '.join(PAS_VALIDES)})",
            )
        borne = None
        if depuis is not None:
            try:
                borne = datetime.fromisoformat(depuis)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"depuis invalide : {depuis} (attendu : horodatage ISO-8601)",
                ) from exc
            if borne.tzinfo is None:
                borne = borne.replace(tzinfo=UTC)
        # La portée est appliquée **événement par événement** par l'agrégat (un
        # run peut en mélanger) : la liste des exécutions lui arrive entière.
        return agrege_couts(
            state.executions(), depuis=borne, pas=pas, portee=portee
        ).to_dict()

    @app.post("/api/taches/{tache_id}/reassigner")
    async def reassigner(tache_id: str, requete: ReassignationRequete) -> dict[str, Any]:
        """Réassigne manuellement une tâche à un agent (glisser-déposer du Kanban).

        Applique l'événement à l'état (le REST répond déjà à jour) puis le
        publie sur le bus — les clients WebSocket voient la réassignation, et
        la pompe la réapplique sans effet (idempotence). 404 si la tâche est
        inconnue, 422 si l'agent ne l'est pas — ou s'il est **désactivé** (#86 :
        un agent désactivé ne reçoit plus de tâches, réassignation comprise).
        """
        tache = state.tache(tache_id)
        if tache is None:
            raise HTTPException(status_code=404, detail=f"tâche inconnue : {tache_id}")
        agent = state.agent(requete.agent)
        if agent is None:
            raise HTTPException(
                status_code=422,
                detail=f"agent inconnu : {requete.agent} (voir GET /api/agents)",
            )
        if not agent.actif:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"agent désactivé : {agent.nom} — il ne reçoit plus de tâches "
                    "(le réactiver via POST /api/agents/{nom}/capacite)."
                ),
            )
        event = Event(
            type=EVENEMENT_TACHE_REASSIGNATION,
            run_id=tache.run_id,
            tache_id=tache_id,
            titre=tache.titre,
            agent=agent.nom,
            role=agent.role,
            statut="assignee",
            detail="réassignation manuelle (Control Tower)",
            # Le projet de la tâche (#277) voyage avec l'événement : sans lui,
            # un geste posé **depuis** la Control Tower d'un projet sortirait de
            # son propre flux temps réel, la portée ne retenant que ce qui porte
            # l'appartenance. La projection l'a déjà, la relayer suffit.
            projet_id=tache.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return tache.to_dict()

    @app.post("/api/taches/{tache_id}/reference")
    async def poser_reference(
        tache_id: str, requete: ReferenceTicketRequete
    ) -> dict[str, Any]:
        """Rattache une tâche au **ticket externe** dont elle relève (#187).

        Le second chemin de pose, celui de l'exécution en cours : un agent
        équipé du serveur MCP de son outil de ticketing (#104) découvre le
        ticket au fil de sa tâche et le pose ici — l'autre chemin étant le
        lancement du run, qui part déjà d'un ticket. Ni cette route ni le moteur
        ne connaissent l'outil : ils transportent un identifiant et une URL.

        N'affecte **que** le ticket : ni statut, ni agent, ni coût ne bougent —
        dire d'où vient une tâche ne la fait pas changer de colonne. Applique
        l'événement à l'état (le REST répond déjà à jour) puis le publie sur le
        bus, où le journal durable le reprend : le ticket survit au redémarrage.
        404 si la tâche est inconnue, 422 si la référence n'apprend rien (ni
        identifiant, ni URL en http(s)).
        """
        tache = state.tache(tache_id)
        if tache is None:
            raise HTTPException(status_code=404, detail=f"tâche inconnue : {tache_id}")
        reference = requete.en_reference()
        if reference is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "référence vide : il faut au moins un identifiant lisible, "
                    "ou une URL en http(s)."
                ),
            )
        event = Event(
            type=EVENEMENT_TACHE_REFERENCE,
            run_id=tache.run_id,
            tache_id=tache_id,
            titre=tache.titre,
            detail=f"ticket externe {reference.id or reference.url}",
            ticket=reference,
            # Même raison qu'à la réassignation (#277) : l'appartenance de la
            # tâche accompagne l'événement, faute de quoi il n'atteindrait pas
            # le flux temps réel du projet dont il parle.
            projet_id=tache.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return tache.to_dict()

    @app.post("/api/agents/{nom}/capacite")
    async def regler_capacite(nom: str, requete: CapaciteRequete) -> dict[str, Any]:
        """Règle la capacité d'un agent (#86, EF-21) : actif/désactivé, instances.

        Persiste le réglage dans le dépôt partagé — celui que moteur et workers
        relisent à chaque tâche : un agent désactivé ne reçoit plus de tâches,
        le plafond d'instances borne ses exécutions simultanées. Applique
        l'événement `agent.capacite` à l'état (le REST répond déjà à jour) puis
        le publie sur le bus — les fiches agents des clients WebSocket suivent
        en temps réel, et la pompe le réapplique sans effet (idempotence).
        404 si l'agent est inconnu, 422 si la requête ne règle rien ou si le
        plafond d'instances est invalide (< 1).
        """
        fiche = state.agent(nom)
        if fiche is None:
            raise HTTPException(
                status_code=404, detail=f"agent inconnu : {nom} (voir GET /api/agents)"
            )
        if requete.actif is None and requete.instances is None:
            raise HTTPException(
                status_code=422,
                detail="rien à régler : renseigner `actif` et/ou `instances`.",
            )
        courante = capacites.lire(nom)
        try:
            capacite = capacites.ecrire(
                CapaciteAgent(
                    nom=nom,
                    actif=courante.actif if requete.actif is None else requete.actif,
                    instances=(
                        courante.instances
                        if requete.instances is None
                        else requete.instances
                    ),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        event = Event(
            type=EVENEMENT_AGENT_CAPACITE,
            agent=nom,
            role=fiche.role,
            statut=CAPACITE_ACTIVE if capacite.actif else CAPACITE_DESACTIVE,
            instances=capacite.instances,
            detail="capacité réglée depuis la Control Tower",
        )
        state.appliquer(event)
        await bus.publish(event)
        return fiche.to_dict()

    @app.get("/api/validations")
    async def validations(projet: str | None = None) -> list[dict[str, Any]]:
        """Les demandes de validation humaine (#48) : contexte, statut, décision.

        `projet` est **obligatoire** (#277), au contrat commun
        (`<id>` | `tous` | `aucun`) : une validation appartient au projet de sa
        tâche, et une Control Tower cadrée sur un projet n'a pas à faire
        arbitrer une action qui se déroule ailleurs.
        """
        return [v.to_dict() for v in state.validations(_portee(projet))]

    @app.post("/api/validations/{tache_id}/decision")
    async def decider(tache_id: str, requete: DecisionRequete) -> dict[str, Any]:
        """Tranche une demande de validation (#48) : la décision part vers le moteur.

        Applique la décision à l'état (le REST répond déjà à jour) puis la
        publie sur le bus — le moteur, en attente sur ce même bus, reprend la
        tâche (approbation) ou l'annule proprement (refus), et la pompe
        réapplique l'événement sans effet (idempotence). 404 si aucune demande
        pour cette tâche, 409 si elle est déjà tranchée (jamais deux décisions).
        """
        demande = state.validation(tache_id)
        if demande is None:
            raise HTTPException(
                status_code=404, detail=f"aucune demande de validation : {tache_id}"
            )
        if not demande.en_attente:
            raise HTTPException(
                status_code=409,
                detail=f"demande déjà tranchée ({demande.statut}) : {tache_id}",
            )
        approuve = requete.approuve
        event = Event(
            type=EVENEMENT_VALIDATION_DECISION,
            tache_id=tache_id,
            titre=demande.titre,
            agent=demande.agent,
            role=demande.role,
            statut=VALIDATION_APPROUVEE if approuve else VALIDATION_REFUSEE,
            detail=(
                "approuvée depuis la Control Tower"
                if approuve
                else "refusée depuis la Control Tower"
            ),
            # Le projet de la validation (#277), recollé par la projection depuis
            # sa tâche : la décision doit atteindre le flux du projet où elle se
            # joue — c'est ce que le moteur y attend.
            projet_id=demande.projet_id,
        )
        state.appliquer(event)
        await bus.publish(event)
        return demande.to_dict()

    def _exige_playbook_connu(agent: str) -> None:
        """404 si `agent` n'est pas un agent à playbook (la clé de `PLAYBOOK_DEFAUTS`).

        L'API n'édite que les playbooks des rôles du catalogue : pas de création
        de playbook orphelin par une simple faute de frappe dans l'URL.
        """
        if agent not in PLAYBOOK_DEFAUTS:
            raise HTTPException(
                status_code=404,
                detail=f"playbook inconnu : {agent} (voir GET /api/playbooks)",
            )

    def _fiche_playbook(agent: str, *, avec_contenu: bool) -> dict[str, Any]:
        """La fiche du playbook d'un agent : version courante et provenance.

        `version` 0 et `source` « defaut » tant que le playbook n'a jamais été
        édité : le contenu effectif est alors le prompt du code (#76, repli).
        `provenance` est celle de la version courante (« humain » — une proposition
        n'est jamais courante, #111), None quand le contenu vient du code.
        """
        defaut = PLAYBOOK_DEFAUTS[agent]
        courant = playbooks.lire(agent)
        fiche: dict[str, Any] = {
            "agent": agent,
            "role": defaut.role,
            "version": courant.version if courant else 0,
            "nb_versions": len(playbooks.numeros(agent)),
            "source": "stockage" if courant else "defaut",
            "provenance": courant.provenance if courant else None,
            "cree_le": courant.cree_le if courant else None,
        }
        if avec_contenu:
            fiche["contenu"] = courant.contenu if courant else defaut.contenu
        return fiche

    @app.get("/api/playbooks")
    async def playbooks_liste() -> list[dict[str, Any]]:
        """Les playbooks des agents (#76) : version courante et provenance de chacun."""
        return [_fiche_playbook(agent, avec_contenu=False) for agent in PLAYBOOK_DEFAUTS]

    @app.get("/api/playbooks/{agent}")
    async def playbook_courant(agent: str) -> dict[str, Any]:
        """Le playbook courant d'un agent : le contenu effectivement chargé par le moteur."""
        _exige_playbook_connu(agent)
        return _fiche_playbook(agent, avec_contenu=True)

    @app.put("/api/playbooks/{agent}")
    async def ecrire_playbook(agent: str, requete: PlaybookEcritureRequete) -> dict[str, Any]:
        """Publie une nouvelle version du playbook (le contenu intégral, pas un diff).

        La version créée devient la courante : elle sera chargée par les moteurs
        construits ensuite (l'application à chaud d'un moteur déjà en vie est le
        lot #78). 422 si le contenu est vide.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.ecrire(agent, requete.contenu)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return version.to_dict()

    @app.get("/api/playbooks/{agent}/versions")
    async def versions_playbook(agent: str) -> list[dict[str, Any]]:
        """L'historique consultable (EF-25) : les versions, de la première à la courante.

        Métadonnées seules — le contenu d'une version passée se lit sur
        `GET /api/playbooks/{agent}/versions/{version}`.
        """
        _exige_playbook_connu(agent)
        return [v.to_dict(avec_contenu=False) for v in playbooks.versions(agent)]

    @app.get("/api/playbooks/{agent}/versions/{version}")
    async def version_playbook(agent: str, version: int) -> dict[str, Any]:
        """Une version passée du playbook, contenu compris. 404 si elle n'existe pas."""
        _exige_playbook_connu(agent)
        lue = playbooks.lire(agent, version)
        if lue is None:
            raise HTTPException(
                status_code=404, detail=f"version inconnue : {agent} v{version}"
            )
        return lue.to_dict()

    @app.get("/api/playbooks/{agent}/propositions")
    async def propositions_playbook(agent: str) -> list[dict[str, Any]]:
        """Les propositions d'auto-amélioration en brouillon (#111), listées à part des versions.

        Métadonnées + justification (sans le contenu) — une proposition n'est jamais la
        version courante et le moteur ne la charge pas tant qu'elle n'est pas appliquée.
        """
        _exige_playbook_connu(agent)
        return [p.to_dict(avec_contenu=False) for p in playbooks.propositions(agent)]

    @app.get("/api/playbooks/{agent}/propositions/{numero}")
    async def proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """Une proposition en brouillon, contenu compris — de quoi la relire avant d'agir.

        404 si elle n'existe pas (jamais créée, ou déjà appliquée/rejetée).
        """
        _exige_playbook_connu(agent)
        lue = playbooks.lire_proposition(agent, numero)
        if lue is None:
            raise HTTPException(
                status_code=404, detail=f"proposition inconnue : {agent} p{numero}"
            )
        return lue.to_dict()

    @app.post("/api/playbooks/{agent}/propositions/{numero}/appliquer")
    async def appliquer_proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """L'action humaine qui adopte une proposition (#140) : elle devient la courante.

        Le contenu candidat rejoint l'historique comme version ordinaire — donc chargée
        à chaud par le moteur dès la tâche suivante (#78) — et sort des brouillons.
        Renvoie la version publiée. 404 si la proposition n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.appliquer_proposition(agent, numero)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return version.to_dict()

    @app.post("/api/playbooks/{agent}/propositions/{numero}/rejeter")
    async def rejeter_proposition_playbook(agent: str, numero: int) -> dict[str, Any]:
        """Écarte une proposition (#140) : elle disparaît, la version courante ne bouge pas.

        Renvoie la proposition rejetée (contenu compris — l'appelant garde une trace de
        ce qu'il vient d'écarter). 404 si elle n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            rejetee = playbooks.rejeter_proposition(agent, numero)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return rejetee.to_dict()

    def _agent_du_catalogue(nom: str) -> Agent:
        """La fiche catalogue de `nom` (modèle, prompt du code) — un rôle du code au pire.

        Sert à l'analyse d'auto-amélioration, qui a besoin du modèle de l'agent. Les
        playbooks n'existent que pour les rôles du code (`_exige_playbook_connu`), toujours
        présents dans `DEFAULT_AGENTS` : le repli garantit une fiche même si le catalogue
        effectif ne renvoyait pas ce nom.
        """
        for agent in (*catalogue(agents_store), *DEFAULT_AGENTS):
            if agent.nom == nom:
                return agent
        # Injoignable en pratique : `_exige_playbook_connu` a déjà garanti un rôle du code.
        raise HTTPException(  # pragma: no cover - garanti connu en amont
            status_code=404, detail=f"agent inconnu : {nom}"
        )

    @app.post("/api/playbooks/{agent}/propositions")
    async def proposer_playbook(
        agent: str, requete: PlaybookPropositionRequete
    ) -> dict[str, Any]:
        """Analyse **à la demande** les échecs d'un run → proposition de révision (#139).

        L'analyse relit les échecs consignés du run `run_id` imputables à `agent`, confie
        à la couche fournisseur la rédaction d'une version révisée du playbook, et
        l'enregistre en **brouillon** (provenance « proposition », #138) — jamais la version
        courante, jamais appliquée sans action humaine (lot UI #140). Renvoie la proposition
        créée (métadonnées + justification + contenu). 404 si le run est inconnu, 422 s'il
        n'a aucun échec pour cet agent, 502 si la génération échoue.
        """
        _exige_playbook_connu(agent)
        execution = state.execution(requete.run_id)
        if execution is None:
            raise HTTPException(
                status_code=404, detail=f"exécution inconnue : {requete.run_id}"
            )
        echecs = echecs_du_run(execution, agent)
        if not echecs:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"aucun échec consigné pour l'agent {agent} dans le run "
                    f"{requete.run_id} : rien à proposer."
                ),
            )
        try:
            proposition = await analyseur.proposer_revision(
                _agent_du_catalogue(agent), requete.run_id, echecs
            )
        except RevisionIndisponible as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return proposition.to_dict()

    @app.post("/api/playbooks/{agent}/restaurer")
    async def restaurer_playbook(
        agent: str, requete: PlaybookRestaurationRequete
    ) -> dict[str, Any]:
        """Retour arrière (EF-25) : republie une version passée comme nouvelle courante.

        L'historique reste linéaire — rien n'est supprimé, la restauration crée
        une version de plus. 404 si la version demandée n'existe pas.
        """
        _exige_playbook_connu(agent)
        try:
            version = playbooks.restaurer(agent, requete.version)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return version.to_dict()

    def _integration_pool_dict(
        integration: IntegrationMcp, etats: dict[str, Any]
    ) -> dict[str, Any]:
        """Une intégration du pool + l'état de ses secrets côté coffre projet (#133).

        Enrichit `IntegrationMcp.to_dict` (id + déclaration à secrets masqués) de
        ce dont l'UI a besoin pour dire *où en est* l'intégration : son
        `mode_auth` et sa `procedure_url` (repris de l'entrée curée quand l'id y
        correspond), et pour **chaque** variable `${VAR}` requise, si son secret
        est **présent** dans le coffre projet et **valide** (un token OAuth
        expiré ne l'est plus). Aucune valeur de secret n'est jamais réémise.
        """
        entree = registre_mcp.get(integration.id)
        requis = (
            {v.cle: v for v in entree.secrets}
            if entree is not None
            else {cle: None for cle in references_env(integration.serveur)}
        )
        secrets_etat: list[dict[str, Any]] = []
        for cle in requis:
            variable = requis[cle]
            etat = etats.get(cle)
            secrets_etat.append(
                {
                    "cle": cle,
                    "description": variable.description if variable is not None else "",
                    "secret": variable.secret if variable is not None else True,
                    "present": etat is not None,
                    "valide": etat["valide"] if etat is not None else False,
                    "ephemere": etat["ephemere"] if etat is not None else False,
                    "expire_le": etat["expire_le"] if etat is not None else None,
                }
            )
        return {
            **integration.to_dict(),
            "mode_auth": entree.mode_auth if entree is not None else None,
            "procedure_url": entree.procedure_url if entree is not None else "",
            "curee": entree is not None,
            "secrets": secrets_etat,
        }

    def _pool_mcp() -> dict[str, Any]:
        """Le pool projet des intégrations MCP + l'état de leurs secrets (#133).

        `{integrations: [...], erreur}` : chaque intégration est enrichie de
        l'état de ses secrets (coffre projet). `erreur` porte la cause si le
        pool stocké est invalide — même contrat de visibilité que `mcp_erreur`
        (la misconfiguration s'affiche sans casser la fiche ni le listing).
        """
        try:
            integrations = mcp.pool()
        except ValueError as exc:
            return {"integrations": [], "erreur": str(exc)}
        try:
            etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
        except ValueError:
            # Coffre projet illisible : on sert le pool sans état de secret
            # plutôt que de masquer les intégrations déjà configurées.
            etats = {}
        return {
            "integrations": [_integration_pool_dict(i, etats) for i in integrations],
            "erreur": None,
        }

    def _volet_mcp(nom: str) -> dict[str, Any]:
        """Le volet « serveurs MCP » d'une fiche catalogue (#104, #133).

        `mcp_serveurs` porte les serveurs **effectifs** montés pour l'agent
        (déclaration héritée `<agent>.json` composée avec le pool activé, forme
        publique à secrets masqués) ; `mcp_erreur` porte la cause exacte si une
        source est invalide. `mcp_pool`/`mcp_pool_erreur` exposent le **pool
        projet** (les intégrations configurables, avec l'état de leurs secrets)
        et `mcp_activations` les ids **activés** pour cet agent — de quoi
        remplacer l'affichage lecture seule par des interrupteurs par agent (#133).
        """
        try:
            serveurs = mcp.lire(nom)
            volet_serveurs: dict[str, Any] = {
                "mcp_serveurs": [s.to_dict() for s in serveurs],
                "mcp_erreur": None,
            }
        except ValueError as exc:
            volet_serveurs = {"mcp_serveurs": [], "mcp_erreur": str(exc)}
        pool = _pool_mcp()
        try:
            activations = list(mcp.activations(nom))
        except ValueError:
            activations = []
        return {
            **volet_serveurs,
            "mcp_pool": pool["integrations"],
            "mcp_pool_erreur": pool["erreur"],
            "mcp_activations": activations,
        }

    def _volet_permissions(nom: str) -> dict[str, Any]:
        """Le volet « permissions » d'une fiche catalogue (#110), lecture seule.

        `permissions` porte la politique allow/deny effective (celle que le
        moteur applique à l'exécution — None : aucune politique, tout ce que
        le profil expose est permis) ; `permissions_erreur` porte la cause
        exacte si la politique stockée est invalide — même contrat de
        visibilité que `mcp_erreur`.
        """
        try:
            politique = permissions.lire(nom)
        except ValueError as exc:
            return {"permissions": None, "permissions_erreur": str(exc)}
        return {
            "permissions": politique.to_dict() if politique is not None else None,
            "permissions_erreur": None,
        }

    def _fiche_defaut(agent: Agent, *, avec_playbook: bool) -> dict[str, Any]:
        """La fiche catalogue d'un agent par défaut : sa définition « du code ».

        `source` « defaut », sans dates : la définition vit dans le code
        (`maestro.agents.catalog`), seule l'édition de son playbook passe par
        le stockage versionné (`/api/playbooks`).
        """
        fiche: dict[str, Any] = {
            "nom": agent.nom,
            "role": agent.role,
            "competences": sorted(agent.competences),
            "modele": agent.modele,
            "fournisseur": None,
            "source": "defaut",
            "cree_le": None,
            "modifie_le": None,
            **_volet_mcp(agent.nom),
            **_volet_permissions(agent.nom),
        }
        if avec_playbook:
            fiche["playbook"] = agent.prompt_systeme
        return fiche

    def _fiche_personnalise(
        definition: AgentDefinition, *, avec_playbook: bool
    ) -> dict[str, Any]:
        """La fiche catalogue d'un agent personnalisé : sa définition persistée (#72)."""
        fiche = definition.to_dict(avec_playbook=avec_playbook)
        fiche["source"] = "personnalise"
        fiche.update(_volet_mcp(definition.nom))
        fiche.update(_volet_permissions(definition.nom))
        return fiche

    def _personnalise_ou_none(nom: str) -> AgentDefinition | None:
        """La définition persistée de `nom`, ou None (nom hors slug compris — jamais levé)."""
        try:
            return agents_store.lire(nom)
        except ValueError:
            return None

    def _exige_personnalise(nom: str) -> AgentDefinition:
        """La définition personnalisée de `nom`, ou l'erreur HTTP qui explique son absence.

        403 sur un agent par défaut (défini par le code : ni modifiable ni
        supprimable ici — son playbook s'édite via `/api/playbooks`), 404 sur
        un nom inconnu du dépôt.
        """
        if nom in NOMS_RESERVES:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"agent par défaut ou acteur système : {nom} — défini par le "
                    "code, ni modifiable ni supprimable (playbook éditable via "
                    "PUT /api/playbooks/{agent})."
                ),
            )
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent personnalisé inconnu : {nom} (voir GET /api/catalogue)",
            )
        return definition

    @app.get("/api/mcp/registre")
    async def mcp_registre(q: str = "") -> list[dict[str, Any]]:
        """La bibliothèque curée de serveurs MCP (#131), recherchable par nom/tag.

        `q` filtre par nom, id, description ou tag (recherche libre, insensible
        à la casse et aux accents ; vide → tout le registre). Chaque entrée est
        un **template** : transport, gabarit d'exécution `${VAR}` (jamais de
        secret), mode d'auth (docs/21), variables à fournir (`secrets`) et lien
        de procédure côté outil (`procedure_url`) — de quoi guider la
        configuration. `curee: true` marque l'appartenance à l'allowlist : seule
        une entrée servie ici est instanciable (garde-fou supply-chain, docs/19).
        """
        return [e.to_dict() for e in registre_mcp.rechercher(q)]

    @app.get("/api/mcp/registre/{id}")
    async def mcp_registre_entree(id: str) -> dict[str, Any]:
        """Une entrée curée du registre MCP (#131) — 404 si l'id est hors allowlist."""
        entree = registre_mcp.get(id)
        if entree is None:
            raise HTTPException(
                status_code=404,
                detail=f"serveur MCP inconnu du registre curé : {id} (voir GET /api/mcp/registre)",
            )
        return entree.to_dict()

    @app.get("/api/mcp/pool")
    async def mcp_pool() -> dict[str, Any]:
        """Le pool projet des intégrations MCP configurées (#133), avec l'état des secrets.

        `{integrations: [...], erreur}` : les intégrations ajoutées au pool
        depuis la bibliothèque, chacune enrichie de son mode d'auth et de
        l'état (présent/valide) de ses secrets côté coffre projet — jamais une
        valeur de secret. `erreur` porte la cause si le pool stocké est invalide.
        C'est ce que liste la section « Intégrations MCP » des Paramètres.
        """
        return _pool_mcp()

    @app.post("/api/mcp/pool", status_code=201)
    async def ajouter_integration_pool(requete: IntegrationPoolRequete) -> dict[str, Any]:
        """Ajoute (ou reconfigure) une intégration du registre dans le pool projet (#133).

        Le parcours **configuration** du critère 1 : instancie l'entrée **curée**
        `registre_id` (garde-fou supply-chain — 404 si hors allowlist, docs/19),
        l'inscrit au pool (remplacement si l'id y est déjà — reconfiguration) et
        pose ses secrets **une seule fois** dans le coffre projet chiffré, selon
        le mode d'auth de l'entrée (token statique/appairage/OAuth importé). Une
        valeur de secret vide est ignorée (le secret reste à configurer). 404 si
        l'id est hors registre, 422 si une valeur/échéance est invalide.
        """
        entree = registre_mcp.get(requete.registre_id)
        if entree is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"serveur MCP inconnu du registre curé : {requete.registre_id} "
                    "(découverte ≠ installation, docs/19 ; voir GET /api/mcp/registre)."
                ),
            )
        try:
            serveur = registre_mcp.instancier(requete.registre_id, nom=requete.nom or None)
            integration = IntegrationMcp(id=requete.registre_id, serveur=serveur)
            pool = [i for i in mcp.pool() if i.id != integration.id]
            pool.append(integration)
            mcp.ecrire_pool(pool)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Les secrets, une seule fois, dans le coffre projet partagé. On ne
        # retient que les variables déclarées par l'entrée curée (les autres
        # n'ont pas de sens pour ce serveur) et on ignore les valeurs vides.
        variables = {v.cle: v for v in entree.secrets}
        for saisi in requete.secrets:
            variable = variables.get(saisi.cle)
            if variable is None or not saisi.valeur:
                continue
            try:
                secrets.enregistrer_projet(
                    saisi.cle,
                    saisi.valeur,
                    mode_auth=entree.mode_auth,
                    secret=variable.secret,
                    expire_le=saisi.expire_le or None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
        except ValueError:
            etats = {}
        return _integration_pool_dict(integration, etats)

    @app.delete("/api/mcp/pool/{id}")
    async def retirer_integration_pool(id: str) -> dict[str, Any]:
        """Retire une intégration du pool projet (#133) et fait le ménage derrière elle.

        Sort l'intégration du pool, **désactive** son id chez tout agent qui
        l'avait activée (sinon `McpStore.lire` casserait sur une activation
        orpheline) et **supprime** du coffre projet les secrets qu'elle était
        seule à référencer (un secret encore utilisé par une autre intégration
        du pool reste). 404 si l'id n'est pas dans le pool.
        """
        try:
            pool = list(mcp.pool())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        cible = next((i for i in pool if i.id == id), None)
        if cible is None:
            raise HTTPException(
                status_code=404,
                detail=f"intégration inconnue du pool : {id} (voir GET /api/mcp/pool).",
            )
        mcp.ecrire_pool([i for i in pool if i.id != id])
        for agent in mcp.agents():
            actives = mcp.activations(agent)
            if id in actives:
                mcp.ecrire_activations(agent, [a for a in actives if a != id])
        # Secrets : ne retirer que ceux qu'aucune intégration restante ne référence.
        references_restantes = {
            cle for i in mcp.pool() for cle in references_env(i.serveur)
        }
        for cle in references_env(cible.serveur):
            if cle not in references_restantes:
                secrets.supprimer_projet(cle)
        return {"id": id, "supprime": True}

    @app.put("/api/mcp/activations/{agent}")
    async def definir_activations_mcp(
        agent: str, requete: ActivationsMcpRequete
    ) -> dict[str, Any]:
        """Fixe les intégrations du pool **activées** pour un agent (#133) — critère 2.

        Remplacement intégral : `integrations` (des ids du pool) devient
        l'ensemble activé de l'agent, vide pour tout désactiver. C'est l'écriture
        derrière l'interrupteur par agent qui remplace l'affichage lecture seule
        des serveurs MCP. 404 si l'agent n'est pas au catalogue, 422 si un id
        n'est pas dans le pool (une activation orpheline casserait la lecture).
        """
        _exige_agent_du_catalogue(agent)
        try:
            pool_ids = {i.id for i in mcp.pool()}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inconnues = sorted(set(requete.integrations) - pool_ids)
        if inconnues:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"intégration(s) absente(s) du pool : {', '.join(inconnues)} "
                    "(voir GET /api/mcp/pool)."
                ),
            )
        try:
            activees = mcp.ecrire_activations(agent, requete.integrations)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"agent": agent, "integrations": list(activees)}

    # --- Projets de l'utilisateur (#223) : CRUD et explorateur de dossiers ---
    def _refus_projet(exc: Exception, *, explorateur: bool = False) -> HTTPException:
        """Un refus du service des projets en `HTTPException` — motif compris.

        Le corps est `{motif, message}` et non une phrase libre : l'écran
        Projets doit pouvoir dire *pourquoi* une racine est refusée (docs/05
        §2.7), ce qu'un texte à analyser ne lui permettrait pas. Le code vient
        du motif (`statut_http`), avec 422 en repli — **jamais un 500** : une
        racine hors périmètre est une réponse, pas une panne (critère #223).
        """
        return HTTPException(
            status_code=statut_http(exc, explorateur=explorateur),
            detail=detail_refus(exc),
        )

    def _motifs(valeur: list[str] | None) -> tuple[str, ...] | None:
        """Les motifs de périmètre d'une requête — None laissant les défauts du modèle."""
        return None if valeur is None else tuple(valeur)

    # Enregistrée **avant** `/api/projets/{id_projet}` : « explorateur » est un
    # identifiant de projet valide au regard du slug `ID_PROJET`, la capture
    # l'avalerait donc si elle passait la première (même piège qu'au-dessus avec
    # `/api/playbooks/propositions`).
    @app.get("/api/projets/explorateur")
    async def explorer_dossiers(chemin: str | None = None) -> dict[str, Any]:
        """Énumère les **dossiers** de `chemin`, ou les racines explorables sans `chemin`.

        La brique sans laquelle l'écran Projets ne peut pas exister : un
        navigateur ne livre jamais de chemin absolu, c'est donc le backend — qui
        tourne déjà sur le poste — qui énumère (docs/05 §2.7). Chaque entrée
        porte son marqueur « dépôt Git » et l'identifiant du projet qui l'a déjà
        déclarée. `parent` est `null` quand remonter sortirait des racines.

        Ce n'est **pas** un « lis n'importe quel chemin » : hors des racines
        explorables (dossier utilisateur par défaut, `MAESTRO_EXPLORATEUR_RACINES`,
        et les racines des projets déclarés) ou dans une zone sensible (`.ssh`,
        `AppData`, dossiers système, dépôt de Maestro), la route **refuse avec
        son motif** — 403 — au lieu de rendre une liste vide. 404 sur un dossier
        absent, 422 sur un chemin relatif ou un fichier.
        """
        try:
            return projets.explorer(chemin)
        except ValueError as exc:
            raise _refus_projet(exc, explorateur=True) from exc

    @app.get("/api/projets")
    async def projets_liste() -> list[dict[str, Any]]:
        """Les projets déclarés (#223), dans l'ordre des identifiants.

        Forme du fichier stocké (docs/24 §2.3) : racine canonicalisée, origine,
        `vcs` détecté (`null` si le projet n'est pas versionné) et périmètre. Un
        fichier du dépôt illisible est **sauté** plutôt que de rendre la page
        entière inutilisable ; `GET /api/projets/{id}` l'explique.
        """
        return projets.lister()

    @app.get("/api/projets/{id_projet}")
    async def projet_detail(id_projet: str) -> dict[str, Any]:
        """Un projet déclaré. 404 s'il est inconnu, 422 si son fichier est illisible."""
        try:
            return projets.detail(id_projet)
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.post("/api/projets", status_code=201)
    async def creer_projet(requete: ProjetRequete) -> dict[str, Any]:
        """Déclare un projet : racine validée (EF-38), VCS détecté, identifiant engendré.

        `origine="nouveau"` crée le dossier s'il manque ; `existant` exige qu'il
        soit là. 422 avec son **motif** si la racine est refusée par la
        validation du lot 1 (racine de disque, dossier utilisateur nu, `.ssh`,
        `AppData`, dossier système, dépôt de Maestro, chemin relatif, dossier
        absent) ou déjà déclarée par un autre projet — jamais un 500.
        """
        try:
            return projets.creer(
                requete.nom,
                requete.racine,
                origine=requete.origine,
                inclus=_motifs(requete.inclus),
                exclus=_motifs(requete.exclus),
            )
        except ValueError as exc:
            raise _refus_projet(exc) from exc

    @app.put("/api/projets/{id_projet}")
    async def modifier_projet(id_projet: str, requete: ProjetRequete) -> dict[str, Any]:
        """Remplace la déclaration d'un projet — l'intégrale, pas un diff (cf. `/api/catalogue`).

        Le `vcs` est **re-détecté** sur la racine servie et `cree_le` est
        préservé. 404 si le projet est inconnu, 422 motivé si la nouvelle racine
        est refusée ou déjà déclarée ailleurs.
        """
        try:
            return projets.remplacer(
                id_projet,
                requete.nom,
                requete.racine,
                origine=requete.origine,
                inclus=_motifs(requete.inclus),
                exclus=_motifs(requete.exclus),
            )
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.delete("/api/projets/{id_projet}")
    async def supprimer_projet(id_projet: str) -> dict[str, Any]:
        """Oublie un projet. Ne touche **jamais** au dossier sur le disque (#221).

        Supprimer une déclaration n'est pas supprimer le travail de
        l'utilisateur : seul le fichier du dépôt part. 404 si le projet est
        inconnu.
        """
        try:
            return projets.supprimer(id_projet)
        except (ValueError, ProjetInconnu) as exc:
            raise _refus_projet(exc) from exc

    @app.get("/api/catalogue")
    async def catalogue_liste() -> list[dict[str, Any]]:
        """Le catalogue d'agents (#72) : les agents par défaut puis les personnalisés.

        Métadonnées seules (le playbook d'une fiche se lit sur
        `GET /api/catalogue/{nom}`), dans l'ordre du catalogue effectif — celui
        que les moteurs chargent au démarrage.
        """
        return [_fiche_defaut(a, avec_playbook=False) for a in DEFAULT_AGENTS] + [
            _fiche_personnalise(d, avec_playbook=False) for d in agents_store.lister()
        ]

    @app.get("/api/catalogue/{nom}")
    async def catalogue_fiche(nom: str) -> dict[str, Any]:
        """La définition complète d'un agent du catalogue, playbook compris."""
        defaut = next((a for a in DEFAULT_AGENTS if a.nom == nom), None)
        if defaut is not None:
            return _fiche_defaut(defaut, avec_playbook=True)
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
            )
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.post("/api/catalogue", status_code=201)
    async def creer_agent(requete: AgentCreationRequete) -> dict[str, Any]:
        """Crée un agent personnalisé (#72) : définition persistée hors du code.

        L'agent entre immédiatement dans la vue `GET /api/agents` (cible de
        réassignation manuelle) ; il est routable et exécutable par les moteurs
        et workers construits ensuite, qui chargent le catalogue effectif à
        leur démarrage. 409 si le nom est déjà pris (agent par défaut, acteur
        système ou personnalisé existant), 422 si la définition est invalide.
        """
        if requete.nom in NOMS_RESERVES or _personnalise_ou_none(requete.nom) is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"nom déjà pris : {requete.nom} — modification d'un agent "
                    "personnalisé via PUT /api/catalogue/{nom}."
                ),
            )
        try:
            definition = agents_store.ecrire(
                AgentDefinition(
                    nom=requete.nom,
                    role=requete.role,
                    competences=tuple(requete.competences),
                    playbook=requete.playbook,
                    modele=requete.modele,
                    fournisseur=requete.fournisseur,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state.ajouter_agent(definition.nom, definition.role)
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.put("/api/catalogue/{nom}")
    async def modifier_agent(nom: str, requete: AgentModificationRequete) -> dict[str, Any]:
        """Remplace la définition d'un agent personnalisé (l'intégrale, pas un diff).

        La définition modifiée vaut pour les moteurs construits ensuite. 403
        sur un agent par défaut (défini par le code), 404 si l'agent n'existe
        pas, 422 si la nouvelle définition est invalide.
        """
        _exige_personnalise(nom)
        try:
            definition = agents_store.ecrire(
                AgentDefinition(
                    nom=nom,
                    role=requete.role,
                    competences=tuple(requete.competences),
                    playbook=requete.playbook,
                    modele=requete.modele,
                    fournisseur=requete.fournisseur,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state.ajouter_agent(definition.nom, definition.role)
        return _fiche_personnalise(definition, avec_playbook=True)

    @app.delete("/api/catalogue/{nom}")
    async def supprimer_agent(nom: str) -> dict[str, Any]:
        """Supprime un agent personnalisé du catalogue.

        Sa fiche quitte la vue `GET /api/agents` ; les moteurs construits
        ensuite ne le chargent plus. Son réglage de capacité (#86) part avec
        lui — un homonyme recréé plus tard repartira des défauts. 403 sur un
        agent par défaut, 404 s'il n'existe pas.
        """
        _exige_personnalise(nom)
        agents_store.supprimer(nom)
        capacites.supprimer(nom)
        state.retirer_agent(nom)
        return {"nom": nom, "supprime": True}

    def _exige_agent_du_catalogue(nom: str) -> Agent:
        """La fiche catalogue de `nom` (défaut ou personnalisé), ou l'erreur 404.

        Le chat ne s'adresse qu'aux agents du catalogue effectif — celui que
        les moteurs chargent : un nom inconnu n'ouvre pas de fil orphelin.
        """
        defaut = next((a for a in DEFAULT_AGENTS if a.nom == nom), None)
        if defaut is not None:
            return defaut
        definition = _personnalise_ou_none(nom)
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
            )
        return definition.to_agent()

    def _canal_chat(nom: str) -> tuple[Agent, ServiceChat]:
        """La fiche et le service qui portent le fil `nom` — agent, ou assistant (#123).

        Le canal d'aide se sert des mêmes endpoints que le chat : `assistance`
        résout sur une fiche hors catalogue et sur son propre `ServiceChat`, tout
        autre nom sur l'agent du catalogue et le chat ordinaire. L'UI n'a donc
        qu'un seul contrat REST à connaître, le nom du fil départage.
        """
        if nom == NOM_ASSISTANCE:
            return AGENT_ASSISTANCE, assistance
        return _exige_agent_du_catalogue(nom), chat

    @app.get("/api/chat/{agent}")
    async def fil_chat(agent: str) -> dict[str, Any]:
        """Le fil de conversation utilisateur ↔ agent (#84), relu de la persistance.

        Vide tant que l'agent n'a jamais été contacté ; 404 si l'agent n'est
        pas au catalogue. `assistance` (#123) désigne le canal d'aide.
        """
        fiche, service = _canal_chat(agent)
        return {
            "agent": fiche.nom,
            "role": fiche.role,
            "messages": [m.to_dict() for m in service.fil(fiche.nom)],
        }

    @app.post("/api/chat/{agent}/messages", status_code=201)
    async def envoyer_chat(agent: str, requete: ChatEnvoiRequete) -> dict[str, Any]:
        """Envoie un message utilisateur à l'agent et rend la paire message/réponse.

        Le message et la réponse sont persistés au fil, passés par la
        messagerie inter-agents (#44) et diffusés en `chat.message` sur le
        WebSocket — les clients temps réel voient le message dès l'envoi puis
        la réponse quand elle tombe. 404 si l'agent n'est pas au catalogue,
        422 sur un message vide, 502 si la réponse n'a pas pu être produite
        (le message utilisateur reste acquis : relancer ne perd pas le fil).
        `assistance` (#123) désigne le canal d'aide, qui répond sans modèle.
        """
        fiche, service = _canal_chat(agent)
        try:
            message, reponse = await service.envoyer(fiche, requete.contenu)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ReponseIndisponible as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "agent": fiche.nom,
            "role": fiche.role,
            "messages": [message.to_dict(), reponse.to_dict()],
        }

    @app.get("/api/chat/{agent}/flux")
    async def flux_chat(agent: str, contenu: str = "") -> StreamingResponse:
        """Flux SSE d'une réponse de chat (#183, chantier *Conversation* Phase 5).

        `GET /api/chat/{agent}/flux?contenu=…` ouvre un `text/event-stream` : une
        trame `debut`, des trames `fragment` (incréments `delta`), puis une trame
        `fin` portant le `MessageChat` complet — chacune en `data: <json>`. 404 si
        l'agent n'est pas au catalogue (`assistance` désigne le canal d'aide) ;
        501 tant que le streaming réel n'est pas livré (servi en fixtures par la
        démo). La forme des trames est le contrat ; le contenu réel (modèle en
        streaming) vient dans le lot dédié.
        """
        fx = _exige_fixtures()
        fiche, _ = _canal_chat(agent)

        async def flux() -> AsyncIterator[str]:
            for trame in fx.flux_chat(fiche.nom, fiche.role, contenu):
                yield f"data: {json.dumps(trame, ensure_ascii=False)}\n\n"

        return StreamingResponse(flux(), media_type="text/event-stream")

    @app.websocket("/ws/evenements")
    async def evenements(websocket: WebSocket, projet: str | None = None) -> None:
        """Flux temps réel : les événements de la portée demandée, en JSON sur la socket.

        Émission et écoute de la déconnexion courent en parallèle : sans cela,
        un client parti resterait connecté jusqu'à la prochaine émission.

        `?projet=` suit **le même contrat que le REST** (#277) —
        `<id>` | `tous` | `aucun`, obligatoire : un client qui charge son état
        filtré puis suit le flux voit deux fois le même périmètre, et un
        événement d'un autre projet n'arrive jamais dans une vue filtrée. Un
        événement **sans** projet n'entre pas non plus dans une vue de projet,
        exactement comme une tâche sans projet n'entre dans aucun Kanban filtré.

        Le refus se dit **sur la socket** avant de la fermer : la connexion est
        acceptée, le motif part en une trame `{"erreur": {motif, message}}`,
        puis la fermeture porte le code 1008 (violation de politique). Refuser
        la poignée de main laisserait le client devant un échec réseau muet,
        alors que la cause tient en un mot.
        """
        await websocket.accept()
        try:
            portee = resoudre_portee(projet, projet_connu=projets)
        except PorteeRefusee as exc:
            await websocket.send_json({"erreur": detail_refus(exc)})
            await websocket.close(code=1008, reason=exc.motif)
            return
        file = diffusion.connecter(portee)
        emission = asyncio.create_task(_emet(websocket, file))
        deconnexion = asyncio.create_task(_attend_deconnexion(websocket))
        try:
            _, en_attente = await asyncio.wait(
                {emission, deconnexion}, return_when=asyncio.FIRST_COMPLETED
            )
            for tache_asyncio in en_attente:
                tache_asyncio.cancel()
                with suppress(asyncio.CancelledError):
                    await tache_asyncio
        finally:
            diffusion.deconnecter(file)

    return app


async def _emet(websocket: WebSocket, file: asyncio.Queue[Event]) -> None:
    """Draine la file de la connexion : chaque événement part en JSON."""
    while True:
        event = await file.get()
        await websocket.send_json(event.to_dict())


async def _attend_deconnexion(websocket: WebSocket) -> None:
    """Rend la main dès que le client ferme (on ne lit rien du flux entrant)."""
    with suppress(WebSocketDisconnect):
        while True:
            await websocket.receive_text()


def create_default_app() -> FastAPI:
    """L'app de production : bus Redis Pub/Sub configuré depuis l'environnement.

    Consomme le canal `maestro.evenements` de l'instance `REDIS_URL` (celle du
    docker-compose par défaut — la même que la file de tâches #41), alimenté
    côté moteur par `maestro.controltower.bridge` ; le chat (#84) transite par
    la messagerie Redis de la même instance (boîtes `maestro.boite.<agent>`).
    Les événements y sont aussi **persistés** (#97) sur la liste Redis
    `maestro.evenements:journal` et rejoués au démarrage : l'état de la Control
    Tower survit au redémarrage de l'API.
    C'est la cible *factory* d'uvicorn :
    `uvicorn --factory maestro.controltower.app:create_default_app`
    (ou le script `maestro-api`).
    """
    settings = load_settings()
    return create_app(
        bus=RedisEventBus(settings.redis_url),
        mailbox=RedisMailbox(settings.redis_url),
        event_log=RedisEventLog(settings.redis_url),
    )
