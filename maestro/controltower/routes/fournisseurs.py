"""Les routes des fournisseurs de modèle : leur catalogue et le fournisseur par défaut.

Le fournisseur par défaut se lit, s'éprouve, s'enregistre et s'efface, ses clés comprises."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.fournisseur_defaut import ChoixRefuse, EpreuveEchouee
from maestro.controltower.fournisseurs import catalogue as catalogue_fournisseurs
from maestro.controltower.routes.contexte import Ctx
from maestro.controltower.routes.requetes import FournisseurDefautRequete

router = APIRouter()


@router.get("/api/fournisseurs")
async def fournisseurs(ctx: Ctx) -> dict[str, Any]:
    """Le catalogue des fournisseurs (#253 + #487) : le registre, éclairé par le poste.

    **Une route, deux moitiés qui ne se confondent pas.** *Supporté par
    Maestro* vient du **registre des fournisseurs**
    (`maestro.providers.registry.catalogue_fournisseurs`, #253) et de rien
    d'autre : chaque fiche porte le **nom** à écrire dans `fournisseur`, ses
    **modèles** annoncés — nom, libellé, et les **niveaux d'effort** admis sur
    chacun, liste vide quand le fournisseur n'expose pas ce réglage — et
    `modeles_libres`, qui dit qu'un nom hors gamme reste recevable (le cas
    d'`openai`, qui fédère des endpoints aux nommages hétéroclites : gamme
    vide **et** libre, c'est-à-dire « saisis le nom »). Inscrire un
    fournisseur au registre suffit à le faire apparaître ici, donc à l'écran,
    sans toucher à cette fonction ni au front.

    *Présent ici* vient de la **sonde** (#487) : ce qui arme le fournisseur
    sur cette machine — un CLI sur le `PATH`, un serveur local qui répond,
    une clé dans l'environnement —, avec `modeles_ici`, les modèles que la
    sonde a **vus**, à ne jamais confondre avec la gamme annoncée. Un outil
    trouvé que Maestro ne sait pas piloter sort dans `hors_registre` :
    montré, jamais proposé.

    Les deux sont **lecture seule** et **sans effet de bord** : rien n'est
    démarré, installé ni écrit, aucun binaire n'est exécuté, aucune clé n'est
    validée (la sonde ne joint que la boucle locale ; le registre ne joint
    rien du tout). Ce que la sonde ne peut pas savoir remonte dans
    `incertitudes` et dans le champ homonyme de chaque constat, plutôt que
    d'être deviné. Un poste nu rend le registre entier, des colonnes de poste
    vides et un 200.
    """
    sonde_poste = ctx.sonde_poste
    return catalogue_fournisseurs(await sonde_poste.rapport())



@router.get("/api/fournisseurs/defaut")
async def fournisseur_par_defaut(ctx: Ctx) -> dict[str, Any]:
    """Le fournisseur par défaut du poste (#1609) : le choix, l'effectif, ce qui l'impose.

    `choix` est ce qui a été enregistré dans les Paramètres (null : rien) ;
    `effectif` ce que l'exécution résout **maintenant** — fournisseur, accès,
    modèle imposé, modèle par défaut —, qui diffère du choix quand une
    variable de l'environnement l'impose ; `environnement` les variables qui
    l'imposent (la valeur, jamais pour une clé) ; `cles` les clés que le
    coffre du poste garde, par nom.
    """
    fournisseur_defaut = ctx.fournisseur_defaut
    return fournisseur_defaut.etat()



@router.post("/api/fournisseurs/epreuve")
async def eprouver_fournisseur(ctx: Ctx, corps: FournisseurDefautRequete) -> dict[str, Any]:
    """Éprouve un choix avant de l'enregistrer (#1609) : un tour de modèle, et son verdict.

    Toujours **200** quand l'épreuve a eu lieu, réussie ou non : le verdict
    est la réponse (`epreuve.ok`, `epreuve.motif`, lu dans les champs typés
    de la réponse du fournisseur). 422 quand le choix ne se tient pas
    (fournisseur ou accès inconnu, modèle ou clé manquant).
    """
    fournisseur_defaut = ctx.fournisseur_defaut
    try:
        return await fournisseur_defaut.eprouver(corps.demande())
    except ChoixRefuse as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@router.put("/api/fournisseurs/defaut")
async def enregistrer_fournisseur(ctx: Ctx, corps: FournisseurDefautRequete) -> dict[str, Any]:
    """Enregistre le fournisseur par défaut du poste — seulement un choix éprouvé (#1609).

    Pris sans redémarrage : la configuration le relit à chaque résolution.
    409 avec le verdict quand l'épreuve échoue, et **rien n'est écrit**, ni
    le choix ni la clé ; 422 quand le choix ne se tient pas.
    """
    fournisseur_defaut = ctx.fournisseur_defaut
    try:
        return await fournisseur_defaut.enregistrer(corps.demande())
    except EpreuveEchouee as exc:
        raise HTTPException(
            status_code=409, detail={"epreuve": exc.epreuve.to_dict()}
        ) from exc
    except ChoixRefuse as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@router.delete("/api/fournisseurs/defaut")
async def effacer_fournisseur(ctx: Ctx) -> dict[str, Any]:
    """Retire le choix du poste : l'environnement seul décide de nouveau (#1609)."""
    fournisseur_defaut = ctx.fournisseur_defaut
    return fournisseur_defaut.effacer()



@router.delete("/api/fournisseurs/defaut/cles/{variable}")
async def retirer_cle_fournisseur(ctx: Ctx, variable: str) -> dict[str, Any]:
    """Retire une clé du coffre du poste (#1609) — 422 si elle n'est la clé d'aucun accès."""
    fournisseur_defaut = ctx.fournisseur_defaut
    try:
        return fournisseur_defaut.retirer_cle(variable)
    except ChoixRefuse as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
