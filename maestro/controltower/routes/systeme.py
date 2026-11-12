"""Les routes du service lui-même : sa vitalité, ce que l'installation fait tourner, son arrêt."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from maestro.controltower.routes.contexte import Ctx
from maestro.espace import espace_courant

router = APIRouter()


@router.get("/api/sante")
async def sante(ctx: Ctx) -> dict[str, Any]:
    """Vitalité du service, l'**espace** qu'il sert (#1164) et son **magasin** (#1206).

    L'espace dit quelles données cette API voit — celles de sa copie de
    travail, ou le jeu du banc. Un exercice le lit avant de sauver son état
    (`maestro.scenarios.exercice sauver`) : sauver les données d'une autre stack
    que celle où il a joué ferait rouvrir un état qui n'est pas le sien.

    Le **statut** ne dit « ok » que si le magasin répond et que l'historique
    a été relu ; sinon `"degrade"`, et `magasin` porte le motif et le geste.
    La réponse reste un `200` : la purge, le lanceur, `start.sh` et les passages
    (exercice, comparatif) lisent ici qu'**un process sert ce port**, et c'est vrai.
    Ce qui a cessé est le « ok » menteur d'une API qui a perdu son magasin.
    """
    magasin = ctx.magasin
    etat = await magasin.etat()
    return {
        "statut": "ok" if etat.disponible else "degrade",
        "espace": espace_courant().nom,
        "magasin": etat.en_json(),
    }



@router.get("/api/installation")
async def installation_courante(ctx: Ctx) -> dict[str, Any]:
    """Ce que cette installation fait tourner (#1622) : le produit et son CLI `claude`.

    `forme` dit si le produit est une installation ou un clone du dépôt, sur le
    réglage qui range aussi sa configuration (`MAESTRO_INSTALLATION`, #1633) ;
    `produit.version` celle que l'assemblage a écrite (dans un clone, celle de
    `apps/desktop/package.json`), ou `null` avec son `releve`. `cli_claude` est
    le CLI que le produit lance : sa `provenance` (`paquet` — celui que le SDK
    embarque —, `poste` — un `claude` du `PATH`, hors de l'installation —, ou
    `absent`), son `chemin` et la `version` que le binaire a dite (`null` :
    inconnue, jamais devinée). Hors de la boucle : lire la version lance le CLI.
    """
    installation = ctx.installation
    etat = await asyncio.to_thread(installation.lire)
    return etat.en_json()



@router.post("/api/extinction")
async def eteindre(ctx: Ctx) -> dict[str, Any]:
    """**Maestro s'éteint** : ses runs en vol sont soldés avec lui (#486).

    La porte de l'arrêt **volontaire**, et la seule. `scripts/controltower/
    start.sh` la pousse avant de libérer les ports, depuis ses **deux** gestes
    d'arrêt — `--stop`, et la **fermeture de la fenêtre** du navigateur par le
    chien de garde #149 (#700) —, et la fermeture de l'enveloppe le fera le jour
    où elle existera. Chaque hôte détaché est éteint avec sa **descendance**, son
    run consigné `annulee` avec la cause `extinction` — donc jamais laissé
    `en_cours` —, et ses tâches soldées avec lui.

    ⚠ **Ce n'est pas l'arrêt de l'API.** Relancer après une modification (le
    démarrage remplace la session précédente), planter, recevoir un `SIGTERM` :
    ceux-là passent par le `lifespan`, où l'hôte détaché ne touche à rien — c'est
    la propriété de #441 et elle n'est pas défaite. La distinction ne se déduit
    d'aucun signal reçu ici : elle **descend** de l'appelant, seul à savoir qu'il
    arrête exprès (docs/28 §11). Fermer la fenêtre a changé de camp le
    2026-08-28 parce que c'est un geste d'**arrêt** — pas parce que la
    distinction aurait faibli (docs/28 §11.2).

    Rend les résumés des runs soldés — vide quand rien ne tournait, ce qui est
    le cas courant. `200` dans les deux cas : éteindre une Control Tower au
    repos n'est pas une erreur, et un code d'échec ferait chercher une panne là
    où il n'y avait simplement rien à éteindre. Ce que le run **redevient** est
    décrit par `POST …/relancer`, qui accepte un run soldé de la sorte.
    """
    executions = ctx.executions
    soldes = await executions.eteindre()
    return {"runs": soldes, "nb": len(soldes)}
