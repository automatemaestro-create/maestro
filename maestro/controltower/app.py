"""API Control Tower — REST + WebSocket temps réel (ticket #46).

Le backend du poste de pilotage (docs/05) : expose l'**état courant** de
l'orchestration en REST et **pousse les événements** en temps réel par
WebSocket. Stack retenue : FastAPI + WebSocket + Redis Pub/Sub (docs/02 §4).

Endpoints :

- `GET  /api/sante` — vitalité du service ;
- `GET  /api/taches` — les tâches (statut, agent, coût) : la source du Kanban ;
- `GET  /api/agents` — l'état des agents (libre/occupé, charge, compteurs) ;
- `GET  /api/executions/{run_id}` — le détail d'une exécution (trace, coût) ;
- `POST /api/taches/{tache_id}/reassigner` — réassignation manuelle (Kanban) ;
- `GET  /api/validations` — les demandes de validation humaine (#48 : en
  attente d'abord le contexte, puis l'issue une fois tranchée) ;
- `POST /api/validations/{tache_id}/decision` — la décision humaine
  (approuver/refuser) : le moteur, en attente sur le bus, reprend ou annule ;
- `WS   /ws/evenements` — le flux d'événements (statuts de tâches, activité
  des agents, messages inter-agents, validations), au format `Event.to_dict`.

Assemblage : une **pompe** unique s'abonne au bus (`EventBus`), projette chaque
événement sur l'état (`ControlTowerState`) puis le rediffuse aux WebSockets
connectées — l'ordre « état d'abord, diffusion ensuite » garantit qu'un client
qui reçoit un événement lit un REST déjà à jour. `create_app` s'injecte bus et
état (les tests d'API tournent sur `InMemoryEventBus`, sans Redis) ;
`create_default_app` câble le `RedisEventBus` de production (canal
`maestro.evenements`, alimenté par `maestro.controltower.bridge` côté moteur).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from maestro.config import load_settings
from maestro.controltower.events import (
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_VALIDATION_DECISION,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.state import (
    VALIDATION_APPROUVEE,
    VALIDATION_REFUSEE,
    ControlTowerState,
)


class ReassignationRequete(BaseModel):
    """Corps de la réassignation manuelle : l'agent qui reprend la tâche."""

    agent: str


class DecisionRequete(BaseModel):
    """Corps de la décision humaine (#48) : approuver ou refuser l'action sensible."""

    approuve: bool


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


async def _pompe(bus: EventBus, state: ControlTowerState, diffusion: Diffusion) -> None:
    """Le seul consommateur du bus : projette sur l'état **puis** rediffuse.

    Cet ordre rend le flux cohérent pour les clients : à réception d'un
    événement WebSocket, l'état REST le reflète déjà. Une panne du bus (Redis
    injoignable…) arrête le flux temps réel mais pas l'API : le REST continue
    de servir l'état déjà projeté — la panne est tracée, pas avalée.
    """
    try:
        async for event in bus.subscribe():
            state.appliquer(event)
            diffusion.diffuser(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception(
            "La pompe d'événements s'est arrêtée : flux temps réel interrompu "
            "(le REST reste servi sur le dernier état projeté)."
        )


def create_app(
    *, bus: EventBus | None = None, state: ControlTowerState | None = None
) -> FastAPI:
    """Construit l'app FastAPI de la Control Tower autour d'un bus et d'un état.

    Par défaut : bus mémoire et état neuf (agents du catalogue, statut libre) —
    la configuration des tests et d'une démo mono-process. La production passe
    par `create_default_app` (bus Redis). La pompe vit avec l'app (lifespan) :
    démarrée à l'ouverture, annulée à l'arrêt, bus refermé derrière elle.
    """
    bus = bus if bus is not None else InMemoryEventBus()
    state = state if state is not None else ControlTowerState()
    diffusion = Diffusion()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pompe = asyncio.create_task(_pompe(bus, state, diffusion))
        try:
            yield
        finally:
            pompe.cancel()
            with suppress(asyncio.CancelledError):
                await pompe
            await bus.close()

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
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/sante")
    async def sante() -> dict[str, str]:
        """Vitalité du service (sonde de supervision)."""
        return {"statut": "ok"}

    @app.get("/api/taches")
    async def taches() -> list[dict[str, Any]]:
        """Les tâches connues : statut, agent assigné, coût — la source du Kanban."""
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

    @app.post("/api/taches/{tache_id}/reassigner")
    async def reassigner(tache_id: str, requete: ReassignationRequete) -> dict[str, Any]:
        """Réassigne manuellement une tâche à un agent (glisser-déposer du Kanban).

        Applique l'événement à l'état (le REST répond déjà à jour) puis le
        publie sur le bus — les clients WebSocket voient la réassignation, et
        la pompe la réapplique sans effet (idempotence). 404 si la tâche est
        inconnue, 422 si l'agent ne l'est pas.
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
    côté moteur par `maestro.controltower.bridge`. C'est la cible *factory*
    d'uvicorn : `uvicorn --factory maestro.controltower.app:create_default_app`
    (ou le script `maestro-api`).
    """
    settings = load_settings()
    return create_app(bus=RedisEventBus(settings.redis_url))
