"""Les routes des projets : leur déclaration, leur racine et les dossiers autorisés.

S'y ajoutent le sélecteur de dossier, le répertoire des projets, l'explorateur et le
versionnement d'un projet."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maestro.config import load_settings
from maestro.controltower import selecteur
from maestro.controltower.projets import ProjetInconnu
from maestro.controltower.routes.communs import _refus_projet
from maestro.controltower.routes.contexte import Ctx
from maestro.controltower.routes.requetes import (
    DossierAutoriseRequete,
    ProjetRequete,
    RacineRequete,
    RepertoireRequete,
    SelecteurRequete,
)
from maestro.projets import RacineRefusee, VersionnementRefuse, canonique, valider_racine

router = APIRouter()


def _motifs(valeur: list[str] | None) -> tuple[str, ...] | None:
    """Les motifs de périmètre d'une requête — None laissant les défauts du modèle."""
    return None if valeur is None else tuple(valeur)



def _poste(requete: Request) -> bool:
    """La requête vient-elle du poste ? — le discriminant « poste » vs « serveur ».

    Lu sur le **client TCP**, jamais sur un en-tête : `X-Forwarded-For` et
    consorts sont posés par le client, donc un `127.0.0.1` déclaré ouvrirait
    une fenêtre sur la machine du serveur depuis n'importe où.
    """
    return selecteur.hote_du_poste(requete.client.host if requete.client else None)



# Enregistrées **avant** `/api/projets/{id_projet}` : « explorateur » et
# « selecteur » sont des identifiants de projet valides au regard du slug
# `ID_PROJET`, la capture les avalerait donc si elle passait la première
# (même piège que `/api/playbooks/lexique` dans `routes/playbooks.py`).
@router.get("/api/projets/selecteur")
async def selecteur_disponible(requete: Request) -> dict[str, Any]:
    """Le sélecteur de dossier **natif du poste** est-il ouvrable ici — et sinon, pourquoi ?

    C'est la réponse que l'écran lit **avant** d'afficher quoi que ce soit :
    un bouton quand `disponible`, la phrase de `message` sinon (#278). Jamais
    un bouton mort, jamais un silence — l'explorateur servi par l'API reste
    dans les deux cas la voie complète.

    Toujours **200** : une indisponibilité est une réponse, pas une erreur.
    Trois motifs — `selecteur-desactive` (`MAESTRO_SELECTEUR_NATIF=0`),
    `selecteur-hors-poste` (backend joint depuis le réseau : le dialogue
    s'ouvrirait sur le serveur, devant personne) et `selecteur-sans-outil`
    (ni PowerShell, ni osascript, ni zenity/kdialog, ou pas de session
    graphique).
    """
    return selecteur.disponibilite(
        poste=_poste(requete),
        reglage=load_settings().selecteur_natif,
    )



@router.post("/api/projets/selecteur")
async def ouvrir_selecteur(
    requete: Request,
    corps: SelecteurRequete | None = None,
) -> dict[str, Any]:
    """Ouvre le dialogue de dossier de l'OS et rend le chemin choisi (#278).

    Le pas d'après l'explorateur : un navigateur ne livre jamais de chemin
    absolu, mais le backend **tourne sur le poste** et peut ouvrir le
    dialogue natif. `depart` (facultatif) est le dossier d'ouverture.

    Le chemin rendu est **confronté aux frontières d'EF-38** avant de
    revenir : `chemin` porte le dossier canonicalisé et `racine_valide` dit
    s'il est déclarable tel quel — un dossier explorable mais non déclarable
    (une racine de disque, le dossier utilisateur nu) revient avec son
    `refus` motivé plutôt qu'en 4xx, l'écran ayant besoin de le montrer dans
    le formulaire, pas de le traiter en panne.

    **Annuler n'est pas une erreur** : fermer la fenêtre rend
    `{"annule": true}` en 200. Les vrais empêchements sont des 403 motivés
    (`selecteur-hors-poste`, `selecteur-desactive`, `selecteur-sans-outil`,
    `selecteur-en-cours`, `selecteur-expire`).
    """
    etat = selecteur.disponibilite(
        poste=_poste(requete),
        reglage=load_settings().selecteur_natif,
    )
    if not etat["disponible"]:
        raise HTTPException(
            status_code=403,
            detail={"motif": etat["motif"], "message": etat["message"]},
        )
    try:
        chemin = await asyncio.to_thread(
            selecteur.choisir_dossier,
            depart=(corps.depart if corps else None),
        )
    except selecteur.SelecteurRefuse as exc:
        raise HTTPException(
            status_code=403,
            detail={"motif": exc.motif, "message": str(exc)},
        ) from exc
    if chemin is None:
        return {"annule": True, "chemin": None, "racine_valide": False, "refus": None}
    return {"annule": False, **_verdict_racine(chemin)}



@router.post("/api/projets/racine")
async def verdict_racine(corps: RacineRequete) -> dict[str, Any]:
    """Ce qu'EF-38 dit d'un chemin proposé comme racine (#938) — **la porte unique**.

    Le pendant de `POST /api/projets/selecteur` pour les chemins que le
    backend n'a pas ouverts lui-même : celui que le dialogue de la **fenêtre**
    a rendu, celui d'un **dossier déposé** sur l'écran de déclaration. Les
    deux arrivent avec un chemin réel que le navigateur, lui, ne sait pas
    fabriquer — et ils doivent passer par la **même** porte que les autres
    voies, faute de quoi il y aurait deux formules de validation à tenir
    d'accord et c'est la garde qui perdrait.

    Même corps que la route du sélecteur, `annule` en moins : `chemin` porte
    le dossier canonicalisé, `racine_valide` dit s'il est déclarable tel
    quel, et un dossier lisible mais non déclarable (une racine de disque, le
    dossier utilisateur nu) revient avec son `refus` motivé plutôt qu'en 4xx
    — l'écran a besoin de le **montrer**, pas de le traiter en panne.

    Toujours **200** : c'est un verdict, pas une exécution. Rien n'est ouvert,
    rien n'est écrit, et refuser fait partie des réponses.
    """
    return _verdict_racine(corps.chemin)



def _verdict_racine(chemin: str) -> dict[str, Any]:
    """Le chemin choisi, canonicalisé, et ce qu'EF-38 en dit — sans jamais lever.

    Deux questions distinctes, et c'est tout l'intérêt de les séparer : le
    dossier est-il **lisible** (canonicalisable), et est-il **déclarable**
    comme racine de projet ? Un `D:/` choisi au dialogue répond oui à la
    première et non à la seconde ; l'écran doit pouvoir le dire au lieu de
    laisser l'utilisateur découvrir le refus à la soumission.
    """
    try:
        resolu = valider_racine(chemin)
    except RacineRefusee as exc:
        try:
            lisible = canonique(chemin).as_posix()
        except OSError:  # pragma: no cover - chemin illisible pour l'OS
            lisible = chemin
        return {
            "chemin": lisible,
            "racine_valide": False,
            "refus": {"motif": exc.motif, "message": str(exc)},
        }
    return {"chemin": resolu.as_posix(), "racine_valide": True, "refus": None}



@router.get("/api/projets/repertoire")
async def repertoire_des_projets(ctx: Ctx) -> dict[str, Any]:
    """Le **répertoire des projets** : où naît un projet neuf (#1022).

    Ce que le formulaire lit pour **remplir d'office** le dossier parent
    d'un projet neuf, et ce que les Paramètres affichent. Rend `chemin`,
    `par_defaut` (aucun réglage posé — c'est `~/Maestro`), `existe`, `cree`
    et `refus`.

    ⚠ **Cette lecture crée le dossier s'il manque**, et le dit (`cree`).
    C'est la décision de #1022 : « créé à la première utilisation », la
    première utilisation étant la première fois qu'on demande *où naît un
    projet neuf*. Rendre un chemin qui n'existe pas serait pire — le bouton
    « Changer de dossier… » s'ouvre dessus, et l'explorateur le refuserait
    (`dossier-absent`). La création est idempotente et porte sur un dossier
    que Maestro propose ; elle n'est jamais silencieuse.

    Toujours **200** : un répertoire devenu indéclarable (disque débranché,
    dossier devenu fichier) revient avec son `refus` motivé plutôt qu'en
    4xx — l'écran doit le **montrer** et laisser parcourir ailleurs, pas
    traiter la page en panne.
    """
    projets = ctx.projets
    return projets.repertoire(creer=True)



@router.put("/api/projets/repertoire")
async def regler_repertoire_des_projets(ctx: Ctx, corps: RepertoireRequete) -> dict[str, Any]:
    """Pose le répertoire des projets — `chemin: null` revient au défaut (#1022).

    Le dossier est **validé et créé** avant d'être stocké : un réglage posé
    est toujours un dossier déclarable, jamais une intention qui échouerait
    plus tard et ailleurs. Comme partout, le chemin **ne se tape pas** côté
    écran (#225) — il vient de l'explorateur ou du dialogue du poste ; la
    route, elle, valide ce qu'on lui donne.

    422/403/404 motivés quand la racine est refusée (`valider_racine`,
    EF-38) — et dans ce cas **rien n'est écrit** : le réglage précédent
    reste en place.
    """
    projets = ctx.projets
    try:
        return projets.regler_repertoire(corps.chemin)
    except ValueError as exc:
        raise _refus_projet(exc) from exc



@router.get("/api/projets/dossiers-autorises")
async def dossiers_autorises(ctx: Ctx) -> dict[str, Any]:
    """Les dossiers autorisés depuis l'écran (#1610), chacun avec son verdict du moment.

    Ce que *Paramètres › Projets* liste pour qu'on les retire. Une
    autorisation devenue invalide (disque débranché) revient avec son
    `refus` : elle ne sert plus de frontière, et l'écran le dit.
    """
    projets = ctx.projets
    return projets.dossiers_autorises()



@router.post("/api/projets/dossiers-autorises")
async def autoriser_dossier(ctx: Ctx, corps: DossierAutoriseRequete) -> dict[str, Any]:
    """Autorise un dossier sur ce poste : l'explorateur l'ouvre dès l'appel suivant (#1610).

    Un réglage **du poste**, qui **s'ajoute** aux dossiers explorables
    (`MAESTRO_EXPLORATEUR_RACINES` ou le défaut) sans les remplacer, et sans
    redémarrage. Le dossier passe par `valider_racine` (EF-38) : 422/403/404
    motivés quand il ne pourrait pas être la racine d'un projet, et rien
    n'est écrit. Les zones interdites restent refusées sous un dossier
    autorisé.
    """
    projets = ctx.projets
    try:
        return projets.autoriser_dossier(corps.chemin)
    except ValueError as exc:
        raise _refus_projet(exc) from exc



@router.delete("/api/projets/dossiers-autorises")
async def retirer_dossier_autorise(ctx: Ctx, chemin: str) -> dict[str, Any]:
    """Retire une autorisation (#1610) ; 404 `autorisation-inconnue` si rien ne correspond."""
    projets = ctx.projets
    try:
        return projets.retirer_dossier(chemin)
    except ValueError as exc:
        raise _refus_projet(exc) from exc



@router.get("/api/projets/explorateur")
async def explorer_dossiers(ctx: Ctx, chemin: str | None = None) -> dict[str, Any]:
    """Énumère les **dossiers** de `chemin`, ou les racines explorables sans `chemin`.

    La brique sans laquelle l'écran Projets ne peut pas exister : un
    navigateur ne livre jamais de chemin absolu, c'est donc le backend — qui
    tourne déjà sur le poste — qui énumère (docs/05 §2.7). Chaque entrée
    porte son marqueur « dépôt Git » et l'identifiant du projet qui l'a déjà
    déclarée. `parent` est `null` quand remonter sortirait des racines.

    Ce n'est **pas** un « lis n'importe quel chemin » : hors des racines
    explorables (dossier utilisateur par défaut, `MAESTRO_EXPLORATEUR_RACINES`,
    et les racines des projets déclarés) ou dans une zone sensible (`.ssh`,
    `AppData`, dossiers système, dépôt de Maestro), la route **refuse avec
    son motif** — 403 — au lieu de rendre une liste vide. 404 sur un dossier
    absent, 422 sur un chemin relatif ou un fichier.
    """
    projets = ctx.projets
    try:
        return projets.explorer(chemin)
    except ValueError as exc:
        raise _refus_projet(exc, explorateur=True) from exc



@router.get("/api/projets")
async def projets_liste(ctx: Ctx) -> list[dict[str, Any]]:
    """Les projets déclarés (#223), dans l'ordre des identifiants.

    Forme du fichier stocké (docs/24 §2.3) : racine canonicalisée, origine,
    `vcs` détecté (`null` si le projet n'est pas versionné) et périmètre. Un
    fichier du dépôt illisible est **sauté** plutôt que de rendre la page
    entière inutilisable ; `GET /api/projets/{id}` l'explique.
    """
    projets = ctx.projets
    return projets.lister()



@router.get("/api/projets/{id_projet}")
async def projet_detail(ctx: Ctx, id_projet: str) -> dict[str, Any]:
    """Un projet déclaré. 404 s'il est inconnu, 422 si son fichier est illisible."""
    projets = ctx.projets
    try:
        return projets.detail(id_projet)
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets", status_code=201)
async def creer_projet(ctx: Ctx, requete: ProjetRequete) -> dict[str, Any]:
    """Déclare un projet : racine validée (EF-38), VCS détecté, identifiant engendré.

    `origine="nouveau"` crée le dossier s'il manque ; `existant` exige qu'il
    soit là. 422 avec son **motif** si la racine est refusée par la
    validation d'EF-38 (racine de disque, dossier utilisateur nu, `.ssh`,
    `AppData`, dossier système, dépôt de Maestro, chemin relatif, dossier
    absent) ou déjà déclarée par un autre projet — jamais un 500.
    """
    projets = ctx.projets
    try:
        return projets.creer(
            requete.nom,
            requete.racine,
            origine=requete.origine,
            inclus=_motifs(requete.inclus),
            exclus=_motifs(requete.exclus),
        )
    except ValueError as exc:
        raise _refus_projet(exc) from exc



@router.put("/api/projets/{id_projet}")
async def modifier_projet(ctx: Ctx, id_projet: str, requete: ProjetRequete) -> dict[str, Any]:
    """Remplace la déclaration d'un projet — l'intégrale, pas un diff (cf. `/api/catalogue`).

    Le `vcs` est **re-détecté** sur la racine servie et `cree_le` est
    préservé. 404 si le projet est inconnu, 422 motivé si la nouvelle racine
    est refusée ou déjà déclarée ailleurs.
    """
    projets = ctx.projets
    try:
        return projets.remplacer(
            id_projet,
            requete.nom,
            requete.racine,
            origine=requete.origine,
            inclus=_motifs(requete.inclus),
            exclus=_motifs(requete.exclus),
        )
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.delete("/api/projets/{id_projet}")
async def supprimer_projet(ctx: Ctx, id_projet: str) -> dict[str, Any]:
    """Oublie un projet. Ne touche **jamais** au dossier sur le disque (#221).

    Supprimer une déclaration n'est pas supprimer le travail de
    l'utilisateur : seul le fichier du dépôt part. 404 si le projet est
    inconnu.
    """
    projets = ctx.projets
    try:
        return projets.supprimer(id_projet)
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/versionner")
async def versionner_projet(ctx: Ctx, id_projet: str) -> dict[str, Any]:
    """Met un projet non versionné sous Git et rend sa fiche relue (#855).

    Le geste qui fait passer un projet du régime « écriture en place » (#839)
    au régime « worktree + fusion sous accord » (#705/#706, docs/24 §2.4) :
    `git init` puis un premier commit de toute la racine — `git add -A`, donc
    le `.gitignore` du projet respecté — pour que la branche de base résolve.
    Rien n'est réimplémenté ici : la route appelle le verbe de #704
    (`ProjetStore.versionner`) et traduit ses refus.

    **Sans corps**, à dessein : le `vcs` n'est jamais un champ de requête
    (EF-38 — constaté sur le disque, jamais déclaré), et c'est ce qui
    distingue ce geste d'un `PUT` élargi. Un projet **déjà versionné** rend
    sa fiche telle quelle (aucune commande, `modifie_le` intact). 404 si le
    projet est inconnu ; un refus porte son **motif** — `depot-englobant`
    (409, la racine est dans un autre dépôt), `commit-refuse` (422, un
    `pre-commit` de l'utilisateur a dit non — la racine est dans l'état
    d'avant), `git-indisponible` (503, le poste n'a pas Git), ou une racine
    devenue inadmissible (`dossier-absent`…) — jamais un 500.

    Joué **hors de la boucle d'événements** : le premier commit indexe tout ce
    que la racine porte, jusqu'à cinq minutes sur un projet lourd, et une
    route qui bloquerait la boucle figerait les flux SSE des autres écrans.
    """
    projets = ctx.projets
    try:
        return await asyncio.to_thread(projets.versionner, id_projet)
    except (ValueError, ProjetInconnu, VersionnementRefuse) as exc:
        raise _refus_projet(exc) from exc
