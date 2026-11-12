"""Les routes des tâches : leur liste, leur réassignation et leur référence de ticket."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.events import (
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_REFERENCE,
    Event,
)
from maestro.controltower.portee import PorteeRefusee, PorteeRun, resoudre_portee_run
from maestro.controltower.routes.communs import _portee, _refus_projet
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import ReassignationRequete, ReferenceTicketRequete

router = APIRouter()


def _portee_run(ctx: Contexte, run: str | None) -> PorteeRun:
    """La **portée run** d'une lecture (#473), ou un refus motivé.

    Le second périmètre, qui **s'ajoute** au premier sans le remplacer :
    `?run=<run_id>` restreint aux tâches que ce run a portées, `?projet=`
    restant obligatoire à côté. Omis, il ne restreint rien — c'est un
    paramètre facultatif, et son absence y est une lecture normale, non le
    mélange silencieux que `projet` refuse (#277). Un run dont la
    projection n'a aucune trace sort en 404 `run-inconnu`, par la porte de
    `projet-inconnu` et pour la même raison : une liste vide se lirait « ce
    run n'a rien fait ».
    """
    state = ctx.state
    try:
        return resoudre_portee_run(
            run, run_connu=lambda run_id: state.execution(run_id) is not None
        )
    except PorteeRefusee as exc:
        raise _refus_projet(exc) from exc



@router.get("/api/taches")
async def taches(
    ctx: Ctx, projet: str | None = None, run: str | None = None
) -> list[dict[str, Any]]:
    """Les tâches connues : statut, agent, coût détaillé (#57) — la source du Kanban.

    `projet` est **obligatoire** (#277) : `<id>` cadre le Kanban sur un
    projet, `tous` rend la vue transverse, `aucun` les tâches hors projet.
    Omis, 422 `projet-requis` ; inconnu, 404 `projet-inconnu`. Une tâche
    sans projet n'apparaît dans la vue d'aucun projet — on ne devine pas à
    quel projet elle appartiendrait.

    `run` est **facultatif** et s'**ajoute** au précédent (#473) : il
    restreint aux tâches que ce run a portées, sans dispenser de dire sur
    quel projet on lit — un run appartient à un projet, les deux filtres se
    composent donc au lieu de se relayer. C'est la source du Kanban **d'un
    run**, et elle compte exactement les tâches que sa `progression` répartit
    (`GET /api/executions/{run_id}`). 404 `run-inconnu` sur un run dont la
    projection n'a aucune trace.
    """
    state = ctx.state
    return [t.to_dict() for t in state.taches(_portee(ctx, projet), _portee_run(ctx, run))]



@router.post("/api/taches/{tache_id}/reassigner")
async def reassigner(ctx: Ctx, tache_id: str, requete: ReassignationRequete) -> dict[str, Any]:
    """Réassigne manuellement une tâche à un agent (glisser-déposer du Kanban).

    Applique l'événement à l'état (le REST répond déjà à jour) puis le
    publie sur le bus — les clients WebSocket voient la réassignation, et
    la pompe la réapplique sans effet (idempotence). 404 si la tâche est
    inconnue, 422 si l'agent ne l'est pas — ou s'il est **désactivé** (#86 :
    un agent désactivé ne reçoit plus de tâches, réassignation comprise).
    """
    bus = ctx.bus
    state = ctx.state
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



@router.post("/api/taches/{tache_id}/reference")
async def poser_reference(
    ctx: Ctx,
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
    bus = ctx.bus
    state = ctx.state
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
