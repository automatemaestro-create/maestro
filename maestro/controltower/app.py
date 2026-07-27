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
- `GET  /api/executions/{run_id}` — le détail d'une exécution (trace, coût) ;
- `GET  /api/executions/{run_id}/cout` — le grand livre du run (#57) : coût
  par tâche (tokens entrée/sortie, coût estimé, durée) et agrégat ;
- `GET  /api/analytics/couts` — la vue coûts & analytics (#87) : agrégats par
  tâche, par agent et par exécution, total et série temporelle du coût
  (`depuis` pour la période, `pas` pour la granularité des seaux) ;
- `POST /api/taches/{tache_id}/reassigner` — réassignation manuelle (Kanban) ;
- `GET  /api/validations` — les demandes de validation humaine (#48 : en
  attente d'abord le contexte, puis l'issue une fois tranchée) ;
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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
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
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.persistence import (
    EventLog,
    InMemoryEventLog,
    RedisEventLog,
)
from maestro.controltower.state import (
    CAPACITE_ACTIVE,
    CAPACITE_DESACTIVE,
    VALIDATION_APPROUVEE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)
from maestro.messaging import InMemoryMailbox, Mailbox, RedisMailbox


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


class ChatEnvoiRequete(BaseModel):
    """Corps d'un envoi de chat (#84) : le message de l'utilisateur à l'agent."""

    contenu: str


class Diffusion:
    """Fan-out des événements vers les WebSockets connectées.

    Une file par connexion, alimentée par la pompe ; chaque handler WebSocket
    draine la sienne. Files non bornées : le POC diffuse peu d'événements et
    une connexion les consomme au fil de l'eau.
    """

    def __init__(self) -> None:
        self._connexions: set[asyncio.Queue[Event]] = set()

    def connecter(self) -> asyncio.Queue[Event]:
        """Enregistre une connexion : sa file recevra tous les événements à venir."""
        file: asyncio.Queue[Event] = asyncio.Queue()
        self._connexions.add(file)
        return file

    def deconnecter(self, file: asyncio.Queue[Event]) -> None:
        """Retire la connexion ; ses événements en attente disparaissent avec elle."""
        self._connexions.discard(file)

    def diffuser(self, event: Event) -> None:
        """Pousse `event` à toutes les connexions enregistrées."""
        for file in self._connexions:
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
    permissions: PermissionStore | None = None,
    event_log: EventLog | None = None,
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

    `permissions` (#110) est le dépôt des politiques allow/deny par agent,
    affichées en **lecture seule** sur les fiches du catalogue (`permissions`,
    la politique effective appliquée à l'exécution) — par défaut celui de la
    config (`MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/` du dépôt) :
    le même que relisent moteur et workers.

    `event_log` (#97) est le **journal durable des événements** que la pompe
    consigne et que le lifespan **rejoue au démarrage** pour reconstruire la
    projection (exécutions, grands livres, analytics, tâches, agents,
    validations) après un redémarrage de l'API — par défaut un journal mémoire
    (pas de durabilité inter-redémarrage : la configuration des tests et d'une
    démo mono-process). La production câble `RedisEventLog` via
    `create_default_app`. Un état injecté (`state`) reste tel quel puis reçoit
    le rejeu par-dessus (idempotent : les événements reconstruisent le même
    état).
    """
    bus = bus if bus is not None else InMemoryEventBus()
    event_log = event_log if event_log is not None else InMemoryEventLog()
    agents_store = agents_store if agents_store is not None else AgentStore.default()
    capacites = capacites if capacites is not None else CapacityStore.default()
    mcp = mcp if mcp is not None else McpStore.default()
    permissions = permissions if permissions is not None else PermissionStore.default()
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

    @app.get("/api/taches")
    async def taches() -> list[dict[str, Any]]:
        """Les tâches connues : statut, agent, coût détaillé (#57) — la source du Kanban."""
        return [t.to_dict() for t in state.taches()]

    @app.get("/api/agents")
    async def agents() -> list[dict[str, Any]]:
        """L'état des agents : libre/occupé, tâche courante, compteurs, coût cumulé."""
        return [a.to_dict() for a in state.agents()]

    @app.get("/api/executions/{run_id}")
    async def execution(run_id: str) -> dict[str, Any]:
        """Le détail d'une exécution : sa trace événement par événement et son coût."""
        detail = state.execution(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
        return detail.to_dict()

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
    async def analytics_couts(depuis: str | None = None, pas: str = PAS_HEURE) -> dict[str, Any]:
        """La vue coûts & analytics (#87) : agrégats transverses et série temporelle.

        Recalculée des exécutions projetées, avec la même convention
        d'attribution que le grand livre d'un run (#57) : coût agrégé par
        tâche, par agent (planification comprise) et par exécution, total, et
        série temporelle du coût en seaux de `pas` (minute/heure/jour).
        `depuis` (ISO-8601, réputé UTC sans fuseau) restreint la fenêtre — la
        période sélectionnable de l'UI. 422 sur un `pas` ou un `depuis`
        invalide.
        """
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
        return agrege_couts(state.executions(), depuis=borne, pas=pas).to_dict()

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
    async def validations() -> list[dict[str, Any]]:
        """Les demandes de validation humaine (#48) : contexte, statut, décision."""
        return [v.to_dict() for v in state.validations()]

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

    def _volet_mcp(nom: str) -> dict[str, Any]:
        """Le volet « serveurs MCP » d'une fiche catalogue (#104), lecture seule.

        `mcp_serveurs` porte les déclarations du dépôt (forme publique : les
        valeurs d'env/headers sans référence `${VAR}` sont masquées) ;
        `mcp_erreur` porte la cause exacte si la déclaration est invalide — la
        validation à la lecture rend la misconfiguration visible depuis l'UI
        sans casser la fiche ni le listing.
        """
        try:
            serveurs = mcp.lire(nom)
        except ValueError as exc:
            return {"mcp_serveurs": [], "mcp_erreur": str(exc)}
        return {"mcp_serveurs": [s.to_dict() for s in serveurs], "mcp_erreur": None}

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

    @app.websocket("/ws/evenements")
    async def evenements(websocket: WebSocket) -> None:
        """Flux temps réel : chaque événement du bus part en JSON sur la socket.

        Émission et écoute de la déconnexion courent en parallèle : sans cela,
        un client parti resterait connecté jusqu'à la prochaine émission.
        """
        await websocket.accept()
        file = diffusion.connecter()
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
