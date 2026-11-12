"""Les routes du premier lancement : son état et sa fin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.routes.contexte import Ctx

router = APIRouter()


@router.get("/api/accueil")
async def accueil_etat(ctx: Ctx) -> dict[str, Any]:
    """Le premier lancement reste-t-il à faire sur ce poste ? (#642)

    `a_faire` est vrai tant qu'aucun projet n'est déclaré **et** que le
    parcours n'a jamais été terminé ; `termine_le` date sa fin (null : jamais).
    Les étapes du parcours ne sont pas ici : chacune se lit sur la route du
    réglage qu'elle pose (fournisseur par défaut, répertoire des projets,
    projets déclarés).
    """
    accueil = ctx.accueil
    return accueil.etat()



@router.post("/api/accueil/terminer")
async def accueil_terminer(ctx: Ctx) -> dict[str, Any]:
    """Note la fin du premier lancement (#642) — idempotent, la première date reste.

    L'écran l'appelle quand le projet né dans le parcours s'ouvre. 503 si le
    dépôt des réglages du poste refuse l'écriture : rien n'est alors noté, et
    un poste qui a un projet n'est de toute façon plus à son premier lancement.
    """
    accueil = ctx.accueil
    try:
        return accueil.terminer()
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"La fin du premier lancement n'a pas pu être notée : {exc}",
        ) from exc
