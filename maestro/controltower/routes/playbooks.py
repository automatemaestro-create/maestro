"""Les routes des playbooks : leur lecture, leur écriture et leurs versions.

Un playbook reçoit aussi des propositions d'amélioration, qui s'appliquent ou se rejettent, une
rédaction par le modèle et une restauration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.agents.catalog import Agent
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.lexique_playbook import lexique_dict
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookDefaut
from maestro.controltower.auto_amelioration import RevisionIndisponible, echecs_du_run
from maestro.controltower.routes.communs import _config, _personnalise_ou_none, au_compte_du_projet
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import (
    PlaybookEcritureRequete,
    PlaybookPropositionRequete,
    PlaybookRedactionRequete,
    PlaybookRestaurationRequete,
)

router = APIRouter()


def _playbook_origine(
    ctx: Contexte,
    agent: str, cfg: ConfigurationAgents | None = None
) -> PlaybookDefaut | None:
    """Le playbook **d'origine** de `agent` — code ou personnalisé —, None si inconnu.

    Deux origines, un seul contrat (#259) : pour un agent du code, le
    document Markdown livré (`PLAYBOOK_DEFAUTS`, #295) ; pour un agent
    personnalisé, le `playbook` de sa définition (#72), celui qu'on a saisi
    en le créant. Dans les deux cas c'est le contenu qui vaut **tant que
    rien n'a été publié** dans le stockage versionné.

    Cette symétrie n'est pas une extension du moteur, elle suit ce qu'il fait :
    `LocalExecutor._playbook_courant` lit `PlaybookStore.lire(agent)` sans
    regarder d'où vient l'agent, et retombe sur son `prompt_systeme` sinon.
    Une version publiée pour un agent personnalisé s'applique donc comme une
    autre, et l'onglet Playbook est le chemin d'écriture de son playbook (#259).
    """
    defaut = PLAYBOOK_DEFAUTS.get(agent)
    if defaut is not None:
        return defaut
    definition = _personnalise_ou_none(ctx, agent, cfg)
    if definition is None:
        return None
    return PlaybookDefaut(
        agent=agent, role=definition.role, contenu=definition.playbook
    )



def _exige_playbook_connu(
    ctx: Contexte,
    agent: str, cfg: ConfigurationAgents | None = None
) -> PlaybookDefaut:
    """404 si `agent` n'est pas un agent du catalogue — et son origine sinon.

    L'API n'édite que les playbooks des agents du catalogue, du code comme
    personnalisés : pas de création de playbook orphelin par une simple
    faute de frappe dans l'URL. Cadré sur un projet (#1038), « du catalogue »
    veut dire de **son** catalogue : un agent d'un autre projet y est inconnu.
    """
    origine = _playbook_origine(ctx, agent, cfg)
    if origine is None:
        raise HTTPException(
            status_code=404,
            detail=f"playbook inconnu : {agent} (voir GET /api/playbooks)",
        )
    return origine



def _fiche_playbook(
    ctx: Contexte,
    agent: str, *, avec_contenu: bool, cfg: ConfigurationAgents | None = None
) -> dict[str, Any]:
    """La fiche du playbook d'un agent : version courante et provenance.

    `version` 0 et `source` « defaut » tant que le playbook n'a jamais été
    édité : le contenu effectif est alors celui d'origine (#76, repli) — le
    prompt du code, ou la définition pour un agent personnalisé (#259).
    `provenance` est celle de la version courante (« humain » — une proposition
    n'est jamais courante, #111), None quand le contenu vient de l'origine.

    Cadré sur un projet (#1038), c'est le playbook **de ce projet** — celui
    du gabarit tant qu'il n'a rien publié par-dessus.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    origine = _playbook_origine(ctx, agent, cfg)
    courant = cfg.playbooks.lire(agent)
    fiche: dict[str, Any] = {
        "agent": agent,
        "role": origine.role if origine else agent,
        "version": courant.version if courant else 0,
        "nb_versions": len(cfg.playbooks.numeros(agent)),
        "source": "stockage" if courant else "defaut",
        "provenance": courant.provenance if courant else None,
        "cree_le": courant.cree_le if courant else None,
        "projet": cfg.projet_id,
    }
    if avec_contenu:
        fiche["contenu"] = (
            courant.contenu if courant else (origine.contenu if origine else "")
        )
    return fiche



@router.get("/api/playbooks")
async def playbooks_liste(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """Les playbooks des agents (#76) : version courante et provenance de chacun.

    Les agents du code d'abord, puis les personnalisés — l'ordre du
    catalogue. Ces derniers y figurent (#259) : leur playbook s'édite et se
    versionne comme les autres, l'onglet Playbook étant son chemin
    d'écriture unique.

    `projet` cadre la lecture (#1038) : les agents **de ce projet** et leurs
    playbooks. Omis, ce sont les gabarits.
    """
    cfg = _config(ctx, projet)
    return [
        _fiche_playbook(ctx, agent, avec_contenu=False, cfg=cfg)
        for agent in (*PLAYBOOK_DEFAUTS, *(d.nom for d in cfg.agents.lister()))
    ]



@router.get("/api/playbooks/lexique")
async def lexique_playbook() -> dict[str, list[dict[str, object]]]:
    """Structures et tournures récurrentes des playbooks du dépôt (#261).

    Ce que l'éditeur propose en cours de frappe. **Dérivé** des documents livrés
    avec le paquet (`maestro.agents.lexique_playbook`), jamais recopié côté front :
    renommer une section dans `_socle.md` change ce qui est proposé, sans toucher
    une ligne de TypeScript.

    Déclarée **avant** `/api/playbooks/{agent}`, sans quoi « lexique » serait pris
    pour un nom d'agent et rendrait un 404.
    """
    return lexique_dict()



@router.get("/api/playbooks/{agent}")
async def playbook_courant(ctx: Ctx, agent: str, projet: str | None = None) -> dict[str, Any]:
    """Le playbook courant d'un agent : le contenu effectivement chargé par le moteur."""
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    return _fiche_playbook(ctx, agent, avec_contenu=True, cfg=cfg)



@router.put("/api/playbooks/{agent}")
async def ecrire_playbook(
    ctx: Ctx,
    agent: str, requete: PlaybookEcritureRequete, projet: str | None = None
) -> dict[str, Any]:
    """Publie une nouvelle version du playbook (le contenu intégral, pas un diff).

    La version créée devient la courante : elle sera chargée par les moteurs
    construits ensuite (l'application à chaud d'un moteur déjà en vie est le
    lot #78). 422 si le contenu est vide.

    Écrit **dans le projet** demandé (#1038) : publier pour un projet ne
    change rien au gabarit ni aux autres projets. Le numéro poursuit celui
    du gabarit tant que le projet n'avait rien publié.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    try:
        version = cfg.playbooks.ecrire(agent, requete.contenu)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return version.to_dict()



@router.get("/api/playbooks/{agent}/versions")
async def versions_playbook(
    ctx: Ctx,
    agent: str, projet: str | None = None
) -> list[dict[str, Any]]:
    """L'historique consultable (EF-25) : les versions, de la première à la courante.

    Métadonnées seules — le contenu d'une version passée se lit sur
    `GET /api/playbooks/{agent}/versions/{version}`. Cadré sur un projet
    (#1038), c'est l'historique hérité du gabarit tant qu'il n'a rien publié,
    le sien ensuite.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    return [v.to_dict(avec_contenu=False) for v in cfg.playbooks.versions(agent)]



@router.get("/api/playbooks/{agent}/versions/{version}")
async def version_playbook(
    ctx: Ctx,
    agent: str, version: int, projet: str | None = None
) -> dict[str, Any]:
    """Une version passée du playbook, contenu compris. 404 si elle n'existe pas."""
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    lue = cfg.playbooks.lire(agent, version)
    if lue is None:
        raise HTTPException(
            status_code=404, detail=f"version inconnue : {agent} v{version}"
        )
    return lue.to_dict()



@router.get("/api/playbooks/{agent}/propositions")
async def propositions_playbook(
    ctx: Ctx,
    agent: str, projet: str | None = None
) -> list[dict[str, Any]]:
    """Les propositions d'auto-amélioration en brouillon (#111), listées à part des versions.

    Métadonnées + justification (sans le contenu) — une proposition n'est jamais la
    version courante et le moteur ne la charge pas tant qu'elle n'est pas appliquée.

    ⚠ Les propositions ne s'héritent **pas** du gabarit (#1038) : un
    brouillon est un geste en attente, et l'appliquer écrit une version — on
    l'applique là où il a été déposé.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    return [p.to_dict(avec_contenu=False) for p in cfg.playbooks.propositions(agent)]



@router.get("/api/playbooks/{agent}/propositions/{numero}")
async def proposition_playbook(
    ctx: Ctx,
    agent: str, numero: int, projet: str | None = None
) -> dict[str, Any]:
    """Une proposition en brouillon, contenu compris — de quoi la relire avant d'agir.

    404 si elle n'existe pas (jamais créée, ou déjà appliquée/rejetée).
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    lue = cfg.playbooks.lire_proposition(agent, numero)
    if lue is None:
        raise HTTPException(
            status_code=404, detail=f"proposition inconnue : {agent} p{numero}"
        )
    return lue.to_dict()



@router.post("/api/playbooks/{agent}/propositions/{numero}/appliquer")
async def appliquer_proposition_playbook(
    ctx: Ctx,
    agent: str, numero: int, projet: str | None = None
) -> dict[str, Any]:
    """L'action humaine qui adopte une proposition (#140) : elle devient la courante.

    Le contenu candidat rejoint l'historique comme version ordinaire — donc chargée
    à chaud par le moteur dès la tâche suivante (#78) — et sort des brouillons.
    Renvoie la version publiée. 404 si la proposition n'existe pas.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    try:
        version = cfg.playbooks.appliquer_proposition(agent, numero)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return version.to_dict()



@router.post("/api/playbooks/{agent}/propositions/{numero}/rejeter")
async def rejeter_proposition_playbook(
    ctx: Ctx,
    agent: str, numero: int, projet: str | None = None
) -> dict[str, Any]:
    """Écarte une proposition (#140) : elle disparaît, la version courante ne bouge pas.

    Renvoie la proposition rejetée (contenu compris — l'appelant garde une trace de
    ce qu'il vient d'écarter). 404 si elle n'existe pas.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    try:
        rejetee = cfg.playbooks.rejeter_proposition(agent, numero)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return rejetee.to_dict()



def _agent_du_catalogue(ctx: Contexte, nom: str, cfg: ConfigurationAgents | None = None) -> Agent:
    """La fiche catalogue de `nom` (modèle, prompt du code) — un rôle du code au pire.

    Sert à l'analyse d'auto-amélioration, qui a besoin du modèle de l'agent. Le
    catalogue effectif est interrogé **d'abord** — c'est lui qui porte les
    agents du projet —, les **gabarits** restant le repli qui garantit une
    fiche aux rôles du code, dont le playbook reste éditable au niveau gabarit
    même si aucun projet ne les reçoit d'office (#1042).
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    for agent in (*cfg.catalogue(), *cfg.gabarits_du_code()):
        if agent.nom == nom:
            return agent
    # Injoignable en pratique : `_exige_playbook_connu` a déjà garanti un agent connu.
    raise HTTPException(  # pragma: no cover - garanti connu en amont
        status_code=404, detail=f"agent inconnu : {nom}"
    )



@router.post("/api/playbooks/{agent}/propositions")
async def proposer_playbook(
    ctx: Ctx,
    agent: str, requete: PlaybookPropositionRequete, projet: str | None = None
) -> dict[str, Any]:
    """Analyse **à la demande** les échecs d'un run → proposition de révision (#139).

    L'analyse relit les échecs consignés du run `run_id` imputables à `agent`, confie
    à la couche fournisseur la rédaction d'une version révisée du playbook, et
    l'enregistre en **brouillon** (provenance « proposition », #138) — jamais la version
    courante, jamais appliquée sans action humaine (lot UI #140). Renvoie la proposition
    créée (métadonnées + justification + contenu). 404 si le run est inconnu, 422 s'il
    n'a aucun échec pour cet agent, 502 si la génération échoue.

    La proposition est déposée **dans le projet** demandé (#1038) — là où
    elle sera relue, et où l'appliquer publiera une version.
    """
    analyseur = ctx.analyseur
    state = ctx.state
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    execution = state.execution(requete.run_id)
    if execution is None:
        raise HTTPException(
            status_code=404, detail=f"exécution inconnue : {requete.run_id}"
        )
    echecs = echecs_du_run(execution, agent)
    if not echecs:
        raise HTTPException(
            status_code=422,
            detail=(
                f"aucun échec consigné pour l'agent {agent} dans le run "
                f"{requete.run_id} : rien à proposer."
            ),
        )
    try:
        # Au grand livre du fil, et non du run qu'elle lit (#1538) : demandée
        # après lui, d'un geste, elle ne réécrit pas le compte d'un run clos.
        async with au_compte_du_projet(ctx, cfg.projet_id, "révision de playbook proposée"):
            proposition = await analyseur.proposer_revision(
                _agent_du_catalogue(ctx, agent, cfg), requete.run_id, echecs, store=cfg.playbooks
            )
    except RevisionIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return proposition.to_dict()



@router.post("/api/playbooks/{agent}/redaction")
async def rediger_playbook(
    ctx: Ctx,
    agent: str, requete: PlaybookRedactionRequete, projet: str | None = None
) -> dict[str, Any]:
    """Réécrit le **brouillon en cours** d'un playbook, sans rien enregistrer (#261).

    Le pendant « au clavier » de la proposition d'après-run : même cadre, même
    fournisseur, mais la matière est le texte que l'utilisateur a sous les yeux et
    le résultat lui revient — l'éditeur l'affiche en différentiel, l'applique à son
    brouillon s'il le veut, et la publication reste un geste séparé
    (`PUT /api/playbooks/{agent}`).

    **Aucune écriture**, en succès comme en échec : ni version, ni proposition en
    brouillon. 404 si l'agent n'a pas de playbook, 422 si le brouillon est vide ou
    hors bornes, 502 si la génération échoue — dans tous les cas le texte de
    l'utilisateur est intact, il n'a jamais quitté son écran.
    """
    redacteur_playbook = ctx.redacteur_playbook
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    try:
        async with au_compte_du_projet(ctx, cfg.projet_id, "playbook réécrit"):
            redaction = await redacteur_playbook.proposer_redaction(
                _agent_du_catalogue(ctx, agent, cfg), requete.contenu, requete.consigne
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RevisionIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return redaction.to_dict()



@router.post("/api/playbooks/{agent}/restaurer")
async def restaurer_playbook(
    ctx: Ctx,
    agent: str, requete: PlaybookRestaurationRequete, projet: str | None = None
) -> dict[str, Any]:
    """Retour arrière (EF-25) : republie une version passée comme nouvelle courante.

    L'historique reste linéaire — rien n'est supprimé, la restauration crée
    une version de plus. 404 si la version demandée n'existe pas. Cadré sur
    un projet (#1038), restaurer une version **héritée** la republie dans le
    projet : l'historique y bifurque plutôt que de toucher au gabarit.
    """
    cfg = _config(ctx, projet)
    _exige_playbook_connu(ctx, agent, cfg)
    try:
        version = cfg.playbooks.restaurer(agent, requete.version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return version.to_dict()
