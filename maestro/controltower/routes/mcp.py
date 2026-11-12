"""Les routes des serveurs MCP : la bibliothèque, ses admissions, le pool et les activations.

La bibliothèque se lit avec sa provenance ; une entrée s'y admet et s'en révoque ; le pool
d'intégrations reçoit et perd ses serveurs ; chaque agent a ses activations, et ses déclarations
se migrent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.agents.mcp import IntegrationMcp, ServeurMcp, references_env
from maestro.agents.mcp_admission import MOTIF_NON_ADMISE, RefusAdmission, etat_politique
from maestro.agents.mcp_federation import oublier_memo
from maestro.agents.mcp_registry import (
    SOURCE_ADMISE,
    SOURCE_CUREE,
    SOURCE_DECOUVERTE,
    SOURCE_TOUTES,
)
from maestro.controltower.routes.communs import (
    _config,
    _exige_agent_du_catalogue,
    _integration_pool_dict,
    _pool_mcp,
    registre,
)
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import (
    ActivationsMcpRequete,
    AdmissionRequete,
    IntegrationPoolRequete,
    RevocationRequete,
)

router = APIRouter()


@router.get("/api/mcp/registre")
async def mcp_registre(ctx: Ctx, q: str = "", source: str = SOURCE_TOUTES) -> list[dict[str, Any]]:
    """La bibliothèque de serveurs MCP (#131, #271), **à trois sources** depuis #678.

    `q` filtre par nom, id, éditeur, description ou tag (recherche libre,
    insensible à la casse et aux accents ; vide → tout le registre). Chaque
    entrée est un **template** : transport, gabarit d'exécution `${VAR}`
    (jamais de secret), mode d'auth (docs/21), variables à fournir
    (`secrets`) et lien de procédure côté outil (`procedure_url`) — de quoi
    guider la configuration.

    `source` sert à n'en demander qu'une : `toutes` (défaut), `curee`,
    `admise` ou `decouverte` — une valeur inconnue est un **422**, jamais
    une liste silencieusement autre que celle demandée. Sans le paramètre,
    la réponse est celle d'avant #677 **augmentée** : curées d'abord, puis
    admises, puis découvertes (tri `_rang`), donc un appelant qui ignore le
    paramètre lit toujours la même tête de liste.

    ⚠ `curee` et `source` ne disent **pas** la même chose (#678) : le
    booléen marque l'appartenance à l'**allowlist** — c'est lui qui répond à
    « montable ? », garde-fou supply-chain, docs/19 —, la source dit d'où
    l'entrée vient. Une entrée `admise` est donc `curee: true` : un humain
    l'a fait entrer par la porte d'admission. Une entrée `decouverte` se
    lit, se cherche, porte ses signaux d'amont (`editeur`, `version`,
    `depot`, `statut`) — et ne se monte pas.
    """
    try:
        entrees = registre(ctx).rechercher(q, source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [e.to_dict() for e in entrees]



@router.get("/api/mcp/registre/provenance")
async def mcp_registre_provenance(ctx: Ctx) -> dict[str, Any]:
    """D'où vient la bibliothèque, quand elle a été revue, et par quoi chercher (#271).

    Le critère 1 de #271 demande que la provenance de la liste et sa date de
    revue soient **dites à l'écran** : un registre curé sans provenance
    affichée demande une confiance qu'il ne justifie pas. `tags` est la
    sortie du cul-de-sac du critère 2 — les pistes qu'une recherche sans
    résultat propose, plutôt que de répéter qu'elle n'a rien trouvé.

    Depuis #677 la bibliothèque a plusieurs sources, donc plusieurs
    provenances, et elles ne répondent pas à la même question : la curée se
    date par sa **revue humaine**, la découverte par le **rafraîchissement**
    du miroir et le nombre d'entrées qu'il porte. `provenances` les rend côte à côte ;
    `moissonnee` dit si le miroir a effectivement rapporté quelque chose —
    c'est ce qui fait cesser le « jamais moissonnée » du critère 5 du parent
    sans le remplacer par une promesse que personne n'a tenue.

    Depuis #678 il y a **trois** sources, donc une troisième provenance : les
    entrées **admises**, qui ne se datent ni par une revue de code ni par un
    rafraîchissement de miroir mais par le **geste** qui les a fait entrer.
    `derniere_le` est la date de la plus récente, `revoquees` ce qu'on a
    retiré, `signaux` ce que l'amont dit de tout ça depuis — l'écran a de
    quoi dire l'état de l'allowlist locale sans recharger le journal entier.

    ⚠ Les clés historiques (`resume`, `sources`, `revue_le`, `tags`,
    `total`) sont **conservées à plat et inchangées de sens** : elles parlent
    de la curation, que l'écran affiche déjà. L'ajout est purement additif —
    `total` reste le compte servi par `GET /api/mcp/registre` sans filtre, et
    `total_curees`/`total_admises`/`total_decouvertes` le détaillent.

    ⚠ `total_curees` compte le **seed seul** : une entrée admise n'y est plus,
    elle est dans `total_admises`. C'est le seul endroit du contrat où un
    chiffre a changé de portée, et la raison est la même que pour le filtre
    `source=curee` — ce qui répond à « montable ? » n'est pas la source mais
    le champ `curee` de chaque entrée.

    ⚠ La forme de `GET /api/mcp/registre` n'a **pas** bougé : elle rend
    toujours une liste nue. Emballer la liste pour y loger la provenance
    aurait cassé tous ses appelants pour une donnée qui ne change qu'à
    chaque revue — d'où une route sœur. `provenance` est un id **réservé**
    du registre (`ID_RESERVES`), donc aucune entrée ne peut être masquée par
    cette route et l'ordre de déclaration ci-dessous ne porte aucune règle.
    """
    courant = registre(ctx)
    curees = courant.lister(SOURCE_CUREE)
    admises = courant.lister(SOURCE_ADMISE)
    decouvertes = courant.lister(SOURCE_DECOUVERTE)
    journal = courant.admissions()
    return {
        **courant.provenance.to_dict(),
        "tags": list(courant.tags()),
        "total": len(courant.lister()),
        "total_curees": len(curees),
        "total_admises": len(admises),
        "total_decouvertes": len(decouvertes),
        "provenances": [
            {
                **courant.provenance.to_dict(),
                "source": SOURCE_CUREE,
                "total": len(curees),
            },
            {
                "source": SOURCE_ADMISE,
                "resume": (
                    "Entrées du registre officiel qu'un humain a fait entrer dans "
                    "l'allowlist par un geste tracé — chacune figée à la version "
                    "admise, avec sa source et qui l'a admise. Révocable ; une "
                    "nouvelle version amont ne remplace rien sans un nouveau geste."
                ),
                "total": len(admises),
                "revoquees": len(courant.admissions(revoquees=True)),
                "derniere_le": max((a.le for a in journal), default=""),
                "signaux": len(courant.signaux),
            },
            {
                **courant.provenance_decouverte.to_dict(),
                "total": len(decouvertes),
            },
        ],
    }



@router.get("/api/mcp/registre/{id}")
async def mcp_registre_entree(ctx: Ctx, id: str) -> dict[str, Any]:
    """Une entrée du registre MCP (#131) — 404 si l'id n'est d'**aucune** source.

    Sert une entrée découverte ou admise aussi bien qu'une curée (#677,
    #678) : c'est la fiche de ce que l'écran vient de lister, `source` dit
    laquelle des trois on regarde et `curee` si elle est montable. Ouvrir
    cette lecture n'ouvre aucune voie de montage — `instancier` ne passe pas
    par ici, et `POST /api/mcp/pool` continue de n'accepter que l'allowlist.
    """
    entree = registre(ctx).trouver(id)
    if entree is None:
        raise HTTPException(
            status_code=404,
            detail=f"serveur MCP inconnu du registre : {id} (voir GET /api/mcp/registre)",
        )
    return entree.to_dict()



def _impact_pool(ctx: Contexte, id: str) -> dict[str, Any]:
    """Ce qu'une révocation de `id` laisse **debout** dans le pool projet (#678).

    Le « sans le dire » du critère 2 : révoquer ne démonte rien — casser un
    run en cours pour appliquer une décision d'allowlist serait un remède
    pire que le mal —, donc la route doit dire ce qui reste monté, chez qui,
    et par quel geste le retirer. Un pool illisible n'est pas une raison de
    faire échouer la révocation : on rend l'ignorance plutôt qu'une fausse
    réponse (`erreur` non nul, `montee` faux).
    """
    mcp = ctx.mcp
    try:
        pool = mcp.pool()
    except ValueError as exc:
        return {"montee": False, "agents": [], "erreur": str(exc)}
    if not any(i.id == id for i in pool):
        return {"montee": False, "agents": [], "erreur": None}
    agents = [agent for agent in mcp.agents() if id in mcp.activations(agent)]
    return {"montee": True, "agents": sorted(agents), "erreur": None}



@router.get("/api/mcp/admissions")
async def mcp_admissions(ctx: Ctx) -> dict[str, Any]:
    """Le journal des admissions (#678) : ce qu'un humain a fait entrer dans l'allowlist.

    `admissions` porte les **actives** (ce qui est montable aujourd'hui) et
    `revoquees` ce qui l'a été puis retiré, avec qui, quand et pourquoi. Les
    deux listes plutôt qu'une à filtrer : ce ne sont pas les mêmes lectures —
    l'une répond à « qu'est-ce qu'on autorise ? », l'autre à « qu'a-t-on
    retiré, et pour quel motif ? ».

    `signaux` porte ce que l'amont dit **depuis** de ces entrées
    (dépréciation, suppression, disparition, version plus récente) : rien
    n'est jamais retiré en silence, c'est le critère 3 du ticket. `politique`
    nomme qui garde la porte — une porte dont on ignore le gardien n'en est
    pas une, et le défaut du dépôt (le geste humain suffit) doit se
    distinguer d'une politique d'entreprise branchée.

    `erreur` porte la cause si le journal est illisible : ce cas-là **retire
    de l'allowlist** tout ce qu'il autorisait, il ne se tait pas.
    """
    admissions = ctx.admissions
    courant = registre(ctx)
    try:
        journal = admissions.lister()
        erreur = None
    except ValueError as exc:
        journal, erreur = (), str(exc)
    return {
        "admissions": [a.to_dict() for a in journal if a.active],
        "revoquees": [a.to_dict() for a in journal if not a.active],
        "signaux": [s.to_dict() for s in courant.signaux],
        "politique": etat_politique(admissions.politique),
        "erreur": erreur,
    }



@router.post("/api/mcp/admissions", status_code=201)
async def admettre_entree(ctx: Ctx, requete: AdmissionRequete) -> dict[str, Any]:
    """**Admet** une entrée découverte dans l'allowlist — le geste du ticket (#678).

    C'est la porte : après elle, et seulement après elle, `POST
    /api/mcp/pool` accepte l'entrée. Elle enregistre l'entrée **traduite et
    figée** avec sa source (nom amont, version épinglée, éditeur, dépôt,
    horodatage du miroir) et le geste (qui, quand, pourquoi).

    Rend l'entrée telle que la bibliothèque la sert **après** l'admission —
    `curee: true`, `source: "admise"`, `admission` renseignée : l'appelant
    n'a pas à recharger pour savoir ce qu'il a obtenu.

    404 si l'id n'est d'aucune source (une entrée que la traduction a refusée
    n'est pas dans la bibliothèque, donc pas admissible — la porte ne fabrique
    pas ce que le gabarit ne sait pas exprimer) ; 409 si elle est déjà curée
    au seed, supprimée chez l'amont, ou refusée par la politique. Ré-admettre
    une entrée déjà admise **à la même version** est idempotent ; à une autre
    version, c'est le nouveau geste qui promeut cette version-là.
    """
    admissions = ctx.admissions
    courant = registre(ctx)
    entree = courant.trouver(requete.registre_id)
    if entree is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"serveur MCP inconnu de la bibliothèque : {requete.registre_id} — "
                "rien à admettre. Une entrée que la traduction a refusée n'y "
                "figure pas (voir GET /api/mcp/registre)."
            ),
        )
    provenance = courant.provenance_decouverte
    try:
        admission = admissions.admettre(
            entree,
            par=requete.par,
            note=requete.note,
            amont=provenance.amont,
            miroir_le=provenance.rafraichi_le,
        )
    except RefusAdmission as exc:
        raise HTTPException(status_code=409, detail=exc.cause) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Le journal vient de changer : la bibliothèque doit servir l'entrée
    # admise **à la requête suivante**, sans attendre que le mtime du
    # système de fichiers se distingue (cf. `federer_memo`).
    oublier_memo()
    admise = registre(ctx).trouver(admission.id)
    return admise.to_dict() if admise is not None else admission.to_dict()



@router.post("/api/mcp/admissions/{id}/revocation")
async def revoquer_admission(ctx: Ctx, id: str, requete: RevocationRequete) -> dict[str, Any]:
    """**Révoque** une admission (#678, critère 2) — sans rien démonter.

    ⚠ Un `POST …/revocation` et non un `DELETE …/{id}`, pour deux raisons qui
    vont dans le même sens : **rien n'est effacé** — l'admission reste au
    journal avec qui l'a révoquée, quand et pourquoi, et c'est ce qui permet
    au refus d'instanciation de la nommer —, et le geste porte un corps
    (l'auteur, le motif) qu'un `DELETE` transporte mal, certains
    intermédiaires HTTP le laissant tomber en route.

    L'entrée sort de l'allowlist : `POST /api/mcp/pool` la refuse à nouveau,
    et `instancier` **nomme** la révocation au lieu de rendre le refus d'un
    id inconnu.

    ⚠ Un serveur **déjà monté** dans le pool projet y reste, et la réponse le
    dit (`pool`) : les agents qui l'ont activée, et le geste pour le retirer
    (`DELETE /api/mcp/pool/{id}`). Le démonter d'office couperait un run en
    cours pour appliquer une décision d'allowlist ; ce qui est promis est
    « jamais sans le dire », pas « jamais sans casser ».

    404 si l'id n'a jamais été admis (une entrée curée du seed se retire en
    revue de code, pas ici), 409 si l'admission est déjà révoquée.
    """
    admissions = ctx.admissions
    try:
        admission = admissions.revoquer(id, par=requete.par, motif=requete.motif)
    except RefusAdmission as exc:
        code = 404 if exc.motif == MOTIF_NON_ADMISE else 409
        raise HTTPException(status_code=code, detail=exc.cause) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    oublier_memo()
    impact = _impact_pool(ctx, id)
    return {
        "admission": admission.to_dict(),
        "pool": impact,
        "message": (
            f"« {id} » est sortie de l'allowlist mais reste montée dans le pool "
            "projet : rien n'a été démonté. Pour la retirer aussi du pool, "
            f"DELETE /api/mcp/pool/{id}."
            if impact["montee"]
            else f"« {id} » est sortie de l'allowlist ; elle n'était pas dans le pool."
        ),
    }



@router.get("/api/mcp/pool")
async def mcp_pool(ctx: Ctx, projet: str | None = None) -> dict[str, Any]:
    """Le pool projet des intégrations MCP configurées (#133), avec l'état des secrets.

    `{integrations: [...], erreur}` : les intégrations ajoutées au pool
    depuis la bibliothèque, chacune enrichie de son mode d'auth et de
    l'état (présent/valide) de ses secrets côté coffre projet — jamais une
    valeur de secret. `erreur` porte la cause si le pool stocké est invalide.
    C'est ce que liste la section « Intégrations MCP » des Paramètres.

    Le mot « pool **projet** » devient exact avec #1038 : `?projet=<id>` rend
    celui de ce projet — celui du gabarit tant qu'il n'a pas le sien.
    """
    return _pool_mcp(ctx, _config(ctx, projet))



@router.post("/api/mcp/pool", status_code=201)
async def ajouter_integration_pool(
    ctx: Ctx,
    requete: IntegrationPoolRequete, projet: str | None = None
) -> dict[str, Any]:
    """Ajoute (ou reconfigure) une intégration du registre dans le pool projet (#133).

    Le parcours **configuration** du critère 1 : instancie l'entrée de
    l'**allowlist** `registre_id` (garde-fou supply-chain — 404 si elle n'y
    est pas, docs/19), l'inscrit au pool (remplacement si l'id y est déjà —
    reconfiguration) et pose ses secrets **une seule fois** dans le coffre
    projet chiffré, selon le mode d'auth de l'entrée (token
    statique/appairage/OAuth importé). Une valeur de secret vide est ignorée
    (le secret reste à configurer). 404 si l'id est hors allowlist, 422 si
    une valeur/échéance est invalide.

    ⚠ Le 404 **nomme le geste qui manque** depuis #678 : une entrée
    découverte y renvoie vers l'admission (`POST /api/mcp/admissions`), une
    entrée révoquée dit qui l'a retirée et quand. La phrase vient de
    `RegistreMcp.cause_non_instanciable` et n'est pas recopiée ici — c'est
    exactement celle que lèverait `instancier` deux lignes plus bas, et deux
    formulations pour un même refus finiraient par se contredire.
    """
    secrets = ctx.secrets
    cfg = _config(ctx, projet)
    courant = registre(ctx)
    entree = courant.get(requete.registre_id)
    if entree is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{courant.cause_non_instanciable(requete.registre_id)} "
                "Voir GET /api/mcp/registre."
            ),
        )
    try:
        serveur = registre(ctx).instancier(requete.registre_id, nom=requete.nom or None)
        integration = IntegrationMcp(id=requete.registre_id, serveur=serveur)
        pool = [i for i in cfg.mcp.pool() if i.id != integration.id]
        pool.append(integration)
        cfg.mcp.ecrire_pool(pool)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Les secrets, une seule fois, dans le coffre projet partagé. On ne
    # retient que les variables déclarées par l'entrée curée (les autres
    # n'ont pas de sens pour ce serveur) et on ignore les valeurs vides.
    variables = {v.cle: v for v in entree.secrets}
    for saisi in requete.secrets:
        variable = variables.get(saisi.cle)
        if variable is None or not saisi.valeur:
            continue
        try:
            secrets.enregistrer_projet(
                saisi.cle,
                saisi.valeur,
                mode_auth=entree.mode_auth,
                secret=variable.secret,
                expire_le=saisi.expire_le or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
    except ValueError:
        etats = {}
    return _integration_pool_dict(ctx, integration, etats)



@router.delete("/api/mcp/pool/{id}")
async def retirer_integration_pool(ctx: Ctx, id: str, projet: str | None = None) -> dict[str, Any]:
    """Retire une intégration du pool projet (#133) et fait le ménage derrière elle.

    Sort l'intégration du pool, **désactive** son id chez tout agent qui
    l'avait activée (sinon `McpStore.lire` casserait sur une activation
    orpheline) et **supprime** du coffre projet les secrets qu'elle était
    seule à référencer (un secret encore utilisé par une autre intégration
    du pool reste). 404 si l'id n'est pas dans le pool.

    Cadré sur un projet (#1038), le retrait **descend le pool hérité** dans
    le projet amputé de cette intégration : le gabarit n'est pas touché, et
    retirer chez soi n'enlève rien aux autres projets.
    """
    secrets = ctx.secrets
    cfg = _config(ctx, projet)
    try:
        pool = list(cfg.mcp.pool())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cible = next((i for i in pool if i.id == id), None)
    if cible is None:
        raise HTTPException(
            status_code=404,
            detail=f"intégration inconnue du pool : {id} (voir GET /api/mcp/pool).",
        )
    cfg.mcp.ecrire_pool([i for i in pool if i.id != id])
    for agent in cfg.mcp.agents():
        actives = cfg.mcp.activations(agent)
        if id in actives:
            cfg.mcp.ecrire_activations(agent, [a for a in actives if a != id])
    # Secrets : ne retirer que ceux qu'aucune intégration restante ne référence.
    references_restantes = {
        cle for i in cfg.mcp.pool() for cle in references_env(i.serveur)
    }
    for cle in references_env(cible.serveur):
        if cle not in references_restantes:
            secrets.supprimer_projet(cle)
    return {"id": id, "supprime": True}



@router.put("/api/mcp/activations/{agent}")
async def definir_activations_mcp(
    ctx: Ctx,
    agent: str, requete: ActivationsMcpRequete, projet: str | None = None
) -> dict[str, Any]:
    """Fixe les intégrations du pool **activées** pour un agent (#133) — critère 2.

    Remplacement intégral : `integrations` (des ids du pool) devient
    l'ensemble activé de l'agent, vide pour tout désactiver. C'est l'écriture
    derrière l'interrupteur par agent qui remplace l'affichage lecture seule
    des serveurs MCP. 404 si l'agent n'est pas au catalogue, 422 si un id
    n'est pas dans le pool (une activation orpheline casserait la lecture).

    Écrit **dans le projet** demandé (#1038) : activer une intégration ici
    n'en active aucune ailleurs.
    """
    cfg = _config(ctx, projet)
    _exige_agent_du_catalogue(ctx, agent, cfg)
    try:
        pool_ids = {i.id for i in cfg.mcp.pool()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    inconnues = sorted(set(requete.integrations) - pool_ids)
    if inconnues:
        raise HTTPException(
            status_code=422,
            detail=(
                f"intégration(s) absente(s) du pool : {', '.join(inconnues)} "
                "(voir GET /api/mcp/pool)."
            ),
        )
    try:
        activees = cfg.mcp.ecrire_activations(agent, requete.integrations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": agent, "integrations": list(activees)}



def _id_registre_de(ctx: Contexte, serveur: ServeurMcp) -> str | None:
    """L'entrée de l'allowlist qui décrit **exactement** ce serveur, s'il y en a une (#263).

    Ce qu'une migration gagne à la poser : l'intégration migrée entre au pool
    sous l'id de son entrée, donc avec son mode d'auth, sa procédure et ses
    variables — la même ligne que si elle avait été ajoutée depuis la
    bibliothèque. Sans elle, `figma-officiel` et `slack` y arriveraient bien
    (leur nom de serveur *est* l'id de leur entrée) mais `forge`, qui nomme le
    serveur GitHub autrement, entrerait hors bibliothèque et porterait une
    alerte pour une situation parfaitement saine.

    Le rapprochement est une **égalité stricte** sur la déclaration
    instanciée *sous le nom du serveur d'origine*, jamais une ressemblance :
    un id de registre qui décrirait autre chose donnerait à l'intégration la
    fiche et les secrets d'un autre serveur. Le nom, lui, est **conservé** —
    c'est le préfixe d'outils (`mcp__<nom>__…`) que les playbooks emploient
    déjà, et le renommer au passage changerait le comportement d'un agent au
    milieu d'une migration qui prétend ne rien changer.
    """
    for entree in registre(ctx).lister(SOURCE_TOUTES):
        if not entree.curee:
            continue  # hors allowlist : ce serait promettre ce que le pool refuse
        try:
            if entree.vers_serveur(nom=serveur.nom) == serveur:
                return entree.id
        except ValueError:
            continue
    return None



@router.post("/api/mcp/migration/{agent}")
async def migrer_declarations_mcp(
    ctx: Ctx,
    agent: str, projet: str | None = None
) -> dict[str, Any]:
    """Migre la déclaration héritée `core/mcp/{agent}.json` vers le pool projet (#263).

    L'issue qui manquait au bloc « hérités » de la fiche d'un agent : ses
    serveurs rejoignent le **pool**, activés pour lui, et le fichier part.
    Additif (`McpStore.migrer_agent`) — le pool et les activations des autres
    agents ne bougent pas —, et **mutualisant** : un serveur déjà au pool sous
    la même déclaration est repris, pas dupliqué. Chaque serveur que la
    bibliothèque décrit exactement entre sous l'**id de son entrée**, donc
    avec son mode d'auth et l'état de ses secrets.

    Aucun secret n'est demandé ni redemandé : une déclaration héritée porte
    déjà ses références `${VAR}`, qui se résolvent au montage comme avant.
    C'est ce qui fait de la migration un geste et non un formulaire.

    404 si l'agent n'est pas au catalogue, 422 s'il n'a rien à migrer ou si
    une source est invalide (rien n'est écrit dans ce cas).

    ⚠ Elle ne migre que les fichiers **du niveau demandé** (#1038) : la
    déclaration d'un projet cadré, celle du gabarit sinon. Migrer depuis un
    projet un fichier qu'il *hérite* le laisserait en place — donc
    autoritaire à la lecture —, ce qui ferait une migration invisible.
    """
    secrets = ctx.secrets
    cfg = _config(ctx, projet)
    _exige_agent_du_catalogue(ctx, agent, cfg)
    try:
        heritees = cfg.mcp.heritees(agent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ids = {
        serveur.nom: id_registre
        for serveur in heritees
        if (id_registre := _id_registre_de(ctx, serveur)) is not None
    }
    try:
        migration = cfg.mcp.migrer_agent(agent, ids=ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
    except ValueError:
        etats = {}
    return {
        "agent": migration.agent,
        "ajoutees": [_integration_pool_dict(ctx, i, etats) for i in migration.ajoutees],
        "reprises": [_integration_pool_dict(ctx, i, etats) for i in migration.reprises],
        "activations": list(migration.activations),
        "fichier_retire": migration.fichier_retire,
    }
