"""Les routes des permissions d'un agent : sa politique et ses accords."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.routes.communs import (
    _accords_en_vigueur,
    _config,
    _exige_agent_du_catalogue,
)
from maestro.controltower.routes.contexte import Ctx
from maestro.controltower.routes.requetes import PolitiquePermissionsRequete

router = APIRouter()


@router.put("/api/permissions/{agent}")
async def definir_permissions(
    ctx: Ctx,
    agent: str, requete: PolitiquePermissionsRequete, projet: str | None = None
) -> dict[str, Any]:
    """Écrit la politique allow/ask/deny d'un agent (#262) — remplacement intégral.

    L'écriture derrière la fiche, là où la section était en lecture seule
    depuis #110 : régler ce qu'un agent a le droit d'appeler ne demande plus
    d'éditer `core/permissions/<agent>.json` à la main puis de relancer. La
    politique écrite vaut pour la **tâche suivante** — l'exécuteur la relit à
    chaud, comme les playbooks.

    404 si l'agent n'est pas au catalogue (une politique orpheline ne serait
    jamais appliquée), 422 sur une entrée mal formée, **avec la cause exacte
    du dépôt** : c'est ce motif que l'écran affiche, et il nomme la liste et
    l'entrée en faute.

    ⚠ Elle ne lit **pas** la politique en place avant de la remplacer, et
    c'est ce qui permet de réparer depuis l'écran un fichier que `lire`
    refuse — un `GET` préalable échouerait précisément là où le geste est
    nécessaire.

    Écrit **dans le projet** demandé (#1038) : régler les autorisations d'un
    agent ici ne touche ni au gabarit ni aux autres projets. C'est tout le
    point du jalon — un agent partagé porterait les autorisations de l'un
    chez l'autre.
    """
    cfg = _config(ctx, projet)
    _exige_agent_du_catalogue(ctx, agent, cfg)
    try:
        politique = cfg.permissions.ecrire(agent, requete.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": agent, "permissions": politique.to_dict()}



@router.delete("/api/permissions/{agent}/accords/{accord_id}")
async def retirer_accord(
    ctx: Ctx,
    agent: str, accord_id: str, projet: str | None = None
) -> dict[str, Any]:
    """Retire un accord étendu d'un agent (#1185) — le prochain appel redemande.

    L'accord a été donné en approuvant une demande « pour la suite du run » ou
    « du projet » ; le retirer rend l'outil à l'arbitrage **dès l'appel
    suivant**, même au milieu d'une tâche, parce que le moteur le relit à chaque
    appel arbitré. Rien d'autre ne bouge : la politique de l'agent reste celle
    qu'elle était.

    Dans le **projet** demandé, comme les autres routes de la fiche (#1038).
    `404` si l'agent n'est pas au catalogue ou si aucun accord ne porte cet
    identifiant — y compris déjà retiré : il n'y a rien à redire. Rend les
    accords qui restent en vigueur, pour que l'écran n'ait rien à recomposer.
    """
    cfg = _config(ctx, projet)
    _exige_agent_du_catalogue(ctx, agent, cfg)
    try:
        retire = cfg.permissions.accords().retirer(agent, accord_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if retire is None:
        raise HTTPException(
            status_code=404,
            detail=f"aucun accord {accord_id!r} pour l'agent {agent} — déjà retiré ?",
        )
    return {
        "agent": agent,
        "retire": retire.to_dict(),
        "accords": _accords_en_vigueur(ctx, agent, cfg),
    }
