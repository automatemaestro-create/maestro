"""Les routes des sources d'un run : leur téléversement et l'aperçu de leur lecture."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from maestro.controltower.routes.communs import _detail_refus
from maestro.controltower.routes.contexte import Ctx
from maestro.sources import SourceRefusee, apercu_sources

router = APIRouter()


@router.post("/api/sources", status_code=201)
async def televerser_sources(
    ctx: Ctx,
    fichier: Annotated[list[UploadFile], File()],
) -> dict[str, Any]:
    """Téléverse un ou plusieurs fichiers (#317) et rend leurs **identifiants de source**.

    Le champ `fichier` est répétable : un formulaire qui dépose trois
    documents fait un appel, pas trois. Les octets vont dans le dépôt de
    téléversement — hors de tout projet et hors de tout run —, et c'est le
    lancement qui les rattachera au run qui les consomme (docs/05 §6.8).

    Les plafonds d'ingestion (ENF-07) s'appliquent **pendant** la réception :
    le nombre de fichiers d'abord, sans toucher au disque, puis la taille de
    chacun et le cumul de l'appel, tranche par tranche. Un refus est motivé
    (422, `{motif, message, index}`) et **ne laisse rien** : les fichiers
    déjà acceptés dans le même appel sont retirés du dépôt, faute de quoi ils
    y resteraient sous des identifiants que personne n'a reçus.
    """
    televersements = ctx.televersements
    plafond = televersements.garde_fous.nb_max_sources
    if plafond is not None and len(fichier) > plafond:
        raise HTTPException(
            status_code=422,
            detail={
                "motif": "trop-de-sources",
                "message": (
                    f"Trop de fichiers dans un même téléversement : {len(fichier)} "
                    f"reçus, {plafond} au maximum."
                ),
            },
        )
    recus: list[dict[str, Any]] = []
    total = 0
    try:
        for index, envoi in enumerate(fichier):
            # Dans un fil : la copie des octets est du disque, pas de
            # l'attente réseau — la boucle de l'API ne doit pas la porter.
            televerse = await asyncio.to_thread(
                televersements.accueillir,
                envoi.filename or "",
                envoi.file,
                index=index,
                deja_recu=total,
            )
            total += televerse.taille
            recus.append(televerse.to_dict())
    except SourceRefusee as exc:
        for accepte in recus:
            televersements.oublier(str(accepte["id"]))
        raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
    return {"sources": recus, "total_octets": total}



@router.post("/api/sources/apercu")
async def apercu_ingestion(
    sources: Annotated[str, Form()] = "[]",
    fichier: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, Any]:
    """Ce que des sources **donneraient**, sans lancer et sans rien conserver (#319).

    Le pendant gratuit du rapport rendu par le lancement (docs/05 §6.1) :
    même résolution (#315), même extraction (#316), mêmes plafonds — et rien
    au bout. C'est ce qui rend le geste de composer un objectif **réversible
    tant qu'il est gratuit** : on voit ce qui sera lu, ignoré ou tronqué, et
    ce que ça coûtera, avant de dépenser la première tâche.

    Le corps est un **multipart** parce qu'un aperçu porte des octets et non
    des identifiants : il ne dépose rien dans le dépôt de téléversement
    (§6.8), qui n'existe que pour faire **survivre** une matière jusqu'au run
    qui la consomme. `sources` est le JSON des sources déclarées, **dans
    l'ordre de l'écran** — celui qui décide de ce qui entre quand le budget de
    tokens s'épuise — et `fichier` est répétable : le n-ième correspond à la
    n-ième source de type `fichier`.

    Toujours du texte, jamais une panne : une source illisible est une
    **ligne** du rapport (« ignoré », avec son motif). Le 422 motivé
    (`{motif, message, index}`) est réservé à ce que la résolution **refuse**
    — un type inconnu, une racine interdite, un plafond dépassé —, c'est-à-dire
    à ce qu'une correction de saisie répare.
    """
    try:
        declarations = json.loads(sources or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "motif": "sources-illisibles",
                "message": f"Sources illisibles : {exc.msg}.",
            },
        ) from exc
    envois = [(envoi.filename or "", envoi.file) for envoi in fichier or []]
    try:
        # Dans un fil : l'extraction ouvre des fichiers et peut récupérer une
        # page (#316), ce que la boucle de l'API ne doit pas porter.
        rapport = await asyncio.to_thread(
            apercu_sources, declarations, fichiers=envois
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc
    return rapport.to_dict()
