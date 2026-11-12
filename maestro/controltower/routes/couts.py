"""Les routes de la dépense : la vue analytique des coûts et la dépense du fil hors des runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.analytics import PAS_DEMANDABLES, PAS_HEURE, agrege_couts
from maestro.controltower.routes.communs import _portee
from maestro.controltower.routes.contexte import Ctx

router = APIRouter()


@router.get("/api/analytics/couts")
async def analytics_couts(
    ctx: Ctx,
    depuis: str | None = None, pas: str = PAS_HEURE, projet: str | None = None
) -> dict[str, Any]:
    """La vue coûts & analytics (#87) : agrégats transverses et série temporelle.

    Recalculée des exécutions projetées, avec la même convention
    d'attribution que le grand livre d'un run (#57) : coût agrégé par
    tâche, par agent (planification comprise) et par exécution, total, et
    série temporelle du coût en seaux de `pas` (minute/heure/jour), ou
    `auto` : le pas se **déduit alors de l'étendue** couverte (#991), ce que
    l'appelant ne peut pas faire sur une fenêtre sans borne. La réponse rend
    toujours le pas **retenu**, jamais `auto`.
    `depuis` (ISO-8601, réputé UTC sans fuseau) restreint la fenêtre — la
    période sélectionnable de l'UI. `projet` est **obligatoire** (#277) et
    restreint la dépense : seuls les événements que la portée retient
    comptent, planification comprise — un travail sans projet n'entre dans
    le total d'aucun projet. La réponse rappelle la `portee` servie, un
    total ne se lisant pas sans savoir de quoi il est le total. 422 sur un
    `pas` ou un `depuis` invalide.
    """
    state = ctx.state
    portee = _portee(ctx, projet)
    if pas not in PAS_DEMANDABLES:
        raise HTTPException(
            status_code=422,
            detail=f"pas invalide : {pas} (attendus : {', '.join(PAS_DEMANDABLES)})",
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
    # run peut en mélanger) : la liste des exécutions lui arrive entière, et
    # ce que le fil a dépensé hors de tout run avec elle (#1534).
    return agrege_couts(
        state.executions(),
        fil=state.depenses_du_fil(),
        depuis=borne,
        pas=pas,
        portee=portee,
    ).to_dict()



@router.get("/api/fil/depense")
async def depense_du_fil(ctx: Ctx, projet: str | None = None) -> dict[str, Any]:
    """Ce que le fil a dépensé pour la portée (#1534) — le poste `fil` de la vue, seul.

    Le shell additionne les grands livres des runs pour dire le coût cumulé
    d'un projet (barre supérieure, tuile « Dépense ») ; sans le fil, il disait
    2,70 $ à côté des 3,83 $ de Coûts & analytics pour le même projet. Cette
    lecture lui donne la part qui manque **par le même calcul** que la vue
    (`agrege_couts`, sans exécutions), à chaque rechargement et sans payer la
    série ni les tables. `fil` vaut `null` quand rien n'a été mesuré.
    `projet` est obligatoire, comme pour toute lecture qui agrège (#277).
    """
    state = ctx.state
    portee = _portee(ctx, projet)
    vue = agrege_couts((), fil=state.depenses_du_fil(), portee=portee)
    return {
        "projet": vue.projet,
        "portee": vue.portee,
        "fil": None if vue.fil is None else vue.fil.to_dict(),
    }
