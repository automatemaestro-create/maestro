"""La route du journal requêtable des événements."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.journal import (
    ORDRE_DESC,
    ORDRES_JOURNAL,
    TAILLE_PAGE_DEFAUT,
    TAILLE_PAGE_MAX,
    TRI_JOURNAL_HORODATAGE,
    TRIS_JOURNAL,
)
from maestro.controltower.routes.communs import _portee
from maestro.controltower.routes.contexte import Ctx

router = APIRouter()


@router.get("/api/journal")
async def journal_requetable(
    ctx: Ctx,
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
    ids: str | None = None,
) -> dict[str, Any]:
    """Le journal requêtable : filtres (agent / type / run / projet / période), tri, pagination.

    Servi sur l'historique des événements consignés par la pompe et rejoués
    au démarrage (`ServiceJournal`, journal durable #97, #478).

    `depuis`/`jusqua` sont des horodatages ISO-8601 (bornes incluses).
    `ids` (#1285) est une liste d'identifiants d'entrée séparés par des
    virgules (`j-0042,j-0055`) : les entrées qu'une pièce du bilan d'un run
    cite, que la vue du run ouvre — où qu'elles soient dans le journal.
    `projet` est **obligatoire** (#277) et suit le contrat commun :
    `<id>` | `tous` | `aucun` — 422 `projet-requis` s'il manque, 404
    `projet-inconnu` sur un identifiant non déclaré. 422 aussi sur un
    `tri`/`ordre` inconnu, une `page` < 1 ou une `taille` hors [1, {max}].
    """
    journal = ctx.journal
    portee = _portee(ctx, projet)
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
    return journal.page(
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
        ids=(
            [i.strip() for i in ids.split(",") if i.strip()] if ids is not None else None
        ),
    )
