"""Le WebSocket des événements, poussés en temps réel aux écrans."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from maestro.controltower.events import Event
from maestro.controltower.portee import PorteeRefusee, resoudre_portee
from maestro.controltower.projets import detail_refus
from maestro.controltower.routes.contexte import Ctx

router = APIRouter()


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



@router.websocket("/ws/evenements")
async def evenements(ctx: Ctx, websocket: WebSocket, projet: str | None = None) -> None:
    """Flux temps réel : les événements de la portée demandée, en JSON sur la socket.

    Émission et écoute de la déconnexion courent en parallèle : sans cela,
    un client parti resterait connecté jusqu'à la prochaine émission.

    `?projet=` suit **le même contrat que le REST** (#277) —
    `<id>` | `tous` | `aucun`, obligatoire : un client qui charge son état
    filtré puis suit le flux voit deux fois le même périmètre, et un
    événement d'un autre projet n'arrive jamais dans une vue filtrée. Un
    événement **sans** projet n'entre pas non plus dans une vue de projet,
    exactement comme une tâche sans projet n'entre dans aucun Kanban filtré —
    une fois la pompe passée : celui d'un run qui relève d'un projet y a été
    rattaché (`ControlTowerState.au_projet_de_son_run`, #1290).

    Le refus se dit **sur la socket** avant de la fermer : la connexion est
    acceptée, le motif part en une trame `{"erreur": {motif, message}}`,
    puis la fermeture porte le code 1008 (violation de politique). Refuser
    la poignée de main laisserait le client devant un échec réseau muet,
    alors que la cause tient en un mot.
    """
    diffusion = ctx.diffusion
    projets = ctx.projets
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
