"""Les routes du catalogue d'agents : leurs fiches, leur cycle de vie et leurs réglages.

Le cycle de vie : création, génération, modification et suppression. Les réglages : la surcharge
d'un agent et son annulation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.agents import DEFAULT_TOOLS, TOOLED_PROFILES
from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.permissions import entree_valide
from maestro.agents.store import (
    AGENT_SOURCE_DEFAUT,
    AGENT_SOURCE_PERSONNALISE,
    AGENT_SOURCE_SURCHARGE,
    NOMS_DU_CODE,
    NOMS_RESERVES,
    AgentDefinition,
    SurchargeAgent,
)
from maestro.controltower.generation_agent import GenerationIndisponible
from maestro.controltower.routes.communs import (
    _accords_en_vigueur,
    _config,
    _personnalise_ou_none,
    _pool_mcp,
    au_compte_du_projet,
)
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import (
    AgentCreationRequete,
    AgentGenerationRequete,
    AgentModificationRequete,
    SurchargeRequete,
)
from maestro.providers.arbitrage import OUTIL_ARBITRAGE
from maestro.providers.blocage import OUTIL_BLOCAGE
from maestro.providers.checklist import OUTIL_CHECKLIST
from maestro.providers.courrier import OUTIL_COURRIER
from maestro.providers.decision import OUTIL_DECISION
from maestro.providers.question import OUTIL_QUESTION

router = APIRouter()


def _volet_mcp(ctx: Contexte, nom: str, cfg: ConfigurationAgents | None = None) -> dict[str, Any]:
    """Le volet « serveurs MCP » d'une fiche catalogue (#104, #133).

    `mcp_serveurs` porte les serveurs **effectifs** montés pour l'agent
    (déclaration héritée `<agent>.json` composée avec le pool activé, forme
    publique à secrets masqués) ; `mcp_erreur` porte la cause exacte si une
    source est invalide. `mcp_pool`/`mcp_pool_erreur` exposent le **pool
    projet** (les intégrations configurables, avec l'état de leurs secrets)
    et `mcp_activations` les ids **activés** pour cet agent — de quoi
    remplacer l'affichage lecture seule par des interrupteurs par agent (#133).

    `mcp_herites` (#263) porte les serveurs de la **seule** déclaration
    héritée. Il est servi parce que l'écran ne peut pas le déduire :
    retrancher des serveurs montés ceux des intégrations activées se fait par
    leur **nom**, et une intégration renommée à l'ajout au pool fait alors
    réapparaître l'héritée comme un serveur de plus. C'est aussi ce sur quoi
    la fiche décide de proposer la migration — donc une liste qu'on lit, pas
    qu'on devine.

    `cfg` cadre le volet sur un projet (#1038) : le pool, les activations et
    la déclaration héritée sont alors ceux de ce projet — ceux du gabarit
    tant qu'il n'a rien posé par-dessus.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    try:
        serveurs = cfg.mcp.lire(nom)
        volet_serveurs: dict[str, Any] = {
            "mcp_serveurs": [s.to_dict() for s in serveurs],
            "mcp_herites": [s.to_dict() for s in cfg.mcp.heritees(nom)],
            "mcp_erreur": None,
        }
    except ValueError as exc:
        volet_serveurs = {
            "mcp_serveurs": [],
            "mcp_herites": [],
            "mcp_erreur": str(exc),
        }
    pool = _pool_mcp(ctx, cfg)
    try:
        activations = list(cfg.mcp.activations(nom))
    except ValueError:
        activations = []
    return {
        **volet_serveurs,
        "mcp_pool": pool["integrations"],
        "mcp_pool_erreur": pool["erreur"],
        "mcp_activations": activations,
    }



def _outils_exposes(
    ctx: Contexte,
    nom: str, cfg: ConfigurationAgents | None = None
) -> list[dict[str, str]]:
    """Ce que l'agent `nom` peut réellement appeler — de quoi **suggérer** (#262).

    Trois origines, et aucune n'est écrite en dur ici : les outils
    **intégrés** de son profil de rôle (`RoleProfile.outils` — le cadre que
    le code déclare pour ce rôle, sinon `DEFAULT_TOOLS`, ceux que le cadre
    générique d'une fiche sert depuis #1037 : tout agent du catalogue est
    outillé, seules ses consignes de métier lui sont propres), les verbes du serveur
    in-process **maestro** (arbitrage, blocage, courrier, décision
    consignée, question, checklist — leurs constantes existent précisément
    pour qu'une politique les désigne, #805, #1023, #1291) et les
    **serveurs MCP** effectivement montés pour lui, cités en entier
    (`mcp__<serveur>`, qui couvre tous leurs outils).

    Une suggestion n'est jamais une contrainte : la saisie reste libre — un
    outil MCP précis (`mcp__slack__send_message`) se désigne à la frappe,
    et l'API ne juge que la **forme** de l'entrée. On écarte en revanche les
    noms qu'elle refuserait (`entree_valide`) : proposer une entrée
    impossible à écrire serait pire que de n'en proposer aucune.

    Les serveurs MCP se lisent au mieux : une source invalide a déjà sa
    cause dans `mcp_erreur`, et une liste de suggestions n'est pas l'endroit
    où la redire. Ils se lisent aussi **dans le projet demandé** (#1038) :
    suggérer un serveur qu'un autre projet a monté n'aiderait personne.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    profil = next((p for p in TOOLED_PROFILES if p.nom == nom), None)
    outils: list[dict[str, str]] = [
        {"nom": outil, "origine": "integre", "libelle": "outil intégré du profil"}
        for outil in (profil.outils if profil is not None else DEFAULT_TOOLS)
    ]
    outils += [
        {"nom": OUTIL_ARBITRAGE, "origine": "maestro", "libelle": "demander un arbitrage"},
        {"nom": OUTIL_BLOCAGE, "origine": "maestro", "libelle": "signaler un blocage"},
        {"nom": OUTIL_COURRIER, "origine": "maestro", "libelle": "écrire à un pair"},
        {
            "nom": OUTIL_DECISION,
            "origine": "maestro",
            "libelle": "consigner une décision tranchée seul",
        },
        {"nom": OUTIL_QUESTION, "origine": "maestro", "libelle": "poser une question"},
        {"nom": OUTIL_CHECKLIST, "origine": "maestro", "libelle": "tenir sa checklist"},
    ]
    try:
        serveurs = cfg.mcp.lire(nom)
    except ValueError:
        serveurs = ()
    for serveur in serveurs:
        entree = f"mcp__{serveur.nom}"
        if entree_valide(entree):
            outils.append(
                {
                    "nom": entree,
                    "origine": "mcp",
                    "libelle": f"serveur MCP {serveur.nom} (tous ses outils)",
                }
            )
    return outils



def _volet_permissions(
    ctx: Contexte,
    nom: str, cfg: ConfigurationAgents | None = None
) -> dict[str, Any]:
    """Le volet « permissions » d'une fiche catalogue (#110), **écrivable** depuis #262.

    `permissions` porte la politique allow/ask/deny effective (celle que le
    moteur applique à l'exécution — None : aucune politique, tout ce que
    le profil expose est permis) ; `permissions_erreur` porte la cause
    exacte si la politique stockée est invalide — même contrat de
    visibilité que `mcp_erreur`. Les **trois** listes sont servies depuis
    #580, `ask` comprise et vide par défaut : une politique écrite avant ce
    lot se relit sous le régime d'hier.

    `permissions_outils` (#262) est ce que l'agent peut réellement appeler :
    de quoi **suggérer** des entrées à qui règle la politique, plutôt que de
    lui faire retrouver les noms exacts ailleurs. Servi **avec la fiche**, et
    pas par une route à lui : c'est la même question, posée du même écran, au
    même moment — un second aller n'apprendrait rien de plus.

    `cfg` cadre la politique sur un projet (#1038) : celle qu'il a posée,
    sinon celle du gabarit — l'absence d'une politique vaut « tout permis »,
    et c'est le repli le moins discutable des six dépôts.

    `permissions_accords` (#1185) : les **accords étendus** de l'agent à ce
    niveau — « ne plus demander pour cet outil » —, servis avec la fiche
    parce qu'ils prolongent la politique et se relisent au même endroit. Un
    accord de run dont le run est soldé n'est plus servi : il ne couvre plus
    rien, et le montrer ferait lire un laissez-passer qui n'existe plus.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    outils = _outils_exposes(ctx, nom, cfg)
    accords = _accords_en_vigueur(ctx, nom, cfg)
    try:
        politique = cfg.permissions.lire(nom)
    except ValueError as exc:
        return {
            "permissions": None,
            "permissions_erreur": str(exc),
            "permissions_outils": outils,
            "permissions_accords": accords,
        }
    return {
        "permissions": politique.to_dict() if politique is not None else None,
        "permissions_erreur": None,
        "permissions_outils": outils,
        "permissions_accords": accords,
    }



def _fiche_defaut(
    ctx: Contexte,
    agent: Agent, *, avec_playbook: bool, cfg: ConfigurationAgents | None = None
) -> dict[str, Any]:
    """La fiche catalogue d'un agent du code — surchargé ou non (#259).

    La définition vit dans le code (`maestro.agents.catalog`) : rôle,
    compétences et playbook n'en bougent pas ici (le playbook s'édite par le
    stockage versionné, `/api/playbooks`). Ce qui bouge, ce sont les **trois
    réglages de modèle**, qu'une surcharge peut recouvrir sans dupliquer
    l'agent — d'où le troisième état de `source` : « defaut » tant que rien
    n'est posé, « defaut_surcharge » dès qu'un réglage l'est.

    Les valeurs servies sont donc les **effectives** (surcharge d'abord,
    code ensuite), et trois clés disent d'où elles viennent : `herite` nomme
    les réglages qui restent au code — de quoi les marquer comme tels
    plutôt que de le faire deviner —, `reglages_du_code` donne ce que le
    code dit de chacun, y compris quand il est recouvert (c'est ce que
    « revenir au défaut » rendrait), et `modifie_le` porte la date de la
    surcharge, ou None si l'agent n'en a pas. `cree_le` reste None : un
    agent du code n'est pas créé, il est là.

    Les deux formes de fiche portent les **mêmes champs**, ici comme depuis
    #253 : un client n'a jamais à deviner ses clés d'après la `source`.

    `cfg` cadre les volets sur un projet (#1038) — surcharge, serveurs MCP
    et autorisations viennent alors de lui.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    surcharge = cfg.surcharges.lire(agent.nom)
    fiche: dict[str, Any] = {
        "nom": agent.nom,
        "role": agent.role,
        "competences": sorted(agent.competences),
        "modele": surcharge.modele or agent.modele,
        "fournisseur": surcharge.fournisseur,
        "effort": surcharge.effort if surcharge.effort is not None else agent.effort,
        "source": AGENT_SOURCE_DEFAUT if surcharge.vide else AGENT_SOURCE_SURCHARGE,
        "herite": list(surcharge.herite()),
        "reglages_du_code": {
            "fournisseur": None,
            "modele": agent.modele,
            "effort": agent.effort,
        },
        "cree_le": None,
        "modifie_le": surcharge.modifie_le or None,
        "projet": cfg.projet_id,
        **_volet_mcp(ctx, agent.nom, cfg),
        **_volet_permissions(ctx, agent.nom, cfg),
    }
    if avec_playbook:
        fiche["playbook"] = agent.prompt_systeme
    return fiche



def _fiche_personnalise(
    ctx: Contexte,
    definition: AgentDefinition,
    *,
    avec_playbook: bool,
    cfg: ConfigurationAgents | None = None,
) -> dict[str, Any]:
    """La fiche catalogue d'un agent personnalisé : sa définition persistée (#72).

    `herite` est **vide** et `reglages_du_code` **null** : un agent
    personnalisé ne tient rien du code, sa définition est son réglage. Les
    deux clés sont servies quand même — voir `_fiche_defaut` : une fiche
    dont les champs dépendraient de la `source` obligerait chaque client à
    les deviner.

    `projet` (#1038) dit à quel projet la fiche appartient — None pour un
    **gabarit**, c'est-à-dire une fiche rangée hors de tout projet. La clé
    est servie sur les deux formes, par la même règle que les deux
    précédentes.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    fiche = definition.to_dict(avec_playbook=avec_playbook)
    fiche["source"] = AGENT_SOURCE_PERSONNALISE
    fiche["herite"] = []
    fiche["reglages_du_code"] = None
    fiche["projet"] = cfg.projet_id
    fiche.update(_volet_mcp(ctx, definition.nom, cfg))
    fiche.update(_volet_permissions(ctx, definition.nom, cfg))
    return fiche



def _exige_personnalise(
    ctx: Contexte,
    nom: str, cfg: ConfigurationAgents | None = None
) -> AgentDefinition:
    """La définition personnalisée de `nom`, ou l'erreur HTTP qui explique son absence.

    403 sur un agent par défaut (défini par le code : ni modifiable ni
    supprimable ici — son playbook s'édite via `/api/playbooks`), 404 sur
    un nom inconnu du dépôt.
    """
    if nom in NOMS_RESERVES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"agent par défaut ou acteur système : {nom} — défini par le "
                "code, ni modifiable ni supprimable (playbook éditable via "
                "PUT /api/playbooks/{agent})."
            ),
        )
    definition = _personnalise_ou_none(ctx, nom, cfg)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"agent personnalisé inconnu : {nom} (voir GET /api/catalogue)",
        )
    return definition



def _exige_du_code(ctx: Contexte, nom: str, cfg: ConfigurationAgents | None = None) -> Agent:
    """Le **gabarit de rôle** `nom`, ou l'erreur HTTP qui explique son absence (#259).

    Le **symétrique** d'`_exige_personnalise`, et ses refus se répondent :
    403 sur un agent personnalisé — sa définition *est* son réglage et
    s'édite par `PUT /api/catalogue/{nom}` ; le surcharger ouvrirait un
    second chemin d'écriture vers les trois mêmes valeurs, le doublon même
    que #259 supprime côté playbook. 403 aussi sur `orchestrateur` et
    `assistance`, qui n'ont pas de fiche au catalogue. 404 sur un inconnu.

    Depuis #1042 ce qu'il rend est un **gabarit** et non un agent, et il ne se
    règle donc qu'au niveau gabarit : demandé sur un projet il sort en 404, au
    même titre que sa fiche et pour la même raison — le projet ne l'a pas, et
    un réglage écrit là ne recouvrirait aucun agent du projet.
    """
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    if cfg.projet_id is None:
        # Le gabarit **nu** : c'est `_fiche_defaut` qui pose la surcharge
        # par-dessus, et la lui donner déjà recouverte ferait afficher
        # l'ancien réglage juste après l'avoir remplacé.
        agent = next((a for a in GABARITS_DU_CODE if a.nom == nom), None)
        if agent is not None:
            return agent
    elif nom in NOMS_DU_CODE:
        raise HTTPException(
            status_code=404,
            detail=(
                f"gabarit de rôle : {nom} — un gabarit se règle hors projet "
                "(sans ?projet=) ; un projet règle les agents de son équipe."
            ),
        )
    if _personnalise_ou_none(ctx, nom, cfg) is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                f"agent personnalisé : {nom} — ses réglages font partie de sa "
                "définition et se modifient via PUT /api/catalogue/{nom} ; "
                "seul un agent du code se surcharge."
            ),
        )
    if nom in NOMS_RESERVES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"acteur système : {nom} — hors catalogue, il n'a pas de "
                "réglage de modèle à surcharger."
            ),
        )
    raise HTTPException(
        status_code=404,
        detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
    )



@router.get("/api/catalogue")
async def catalogue_liste(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """Le catalogue d'agents (#72), dans l'ordre que les moteurs chargent.

    Métadonnées seules (le playbook d'une fiche se lit sur
    `GET /api/catalogue/{nom}`).

    `projet` cadre la lecture (#1038) : **l'équipe de ce projet**, jamais
    celle d'un autre — et rien d'autre. Un projet qu'on vient de créer ou
    d'importer rend donc une liste **vide** (#1042) : il n'a aucun agent tant
    que son équipe n'a pas été proposée et validée (#1039, #1040). C'est le
    critère, et c'est ici qu'il se lit.

    Omis, la lecture est celle du niveau **gabarit** : les gabarits de rôle du
    code, puis les fiches rangées hors de tout projet. Ce niveau se consulte
    et se règle — l'analyse d'équipe y puise —, il ne travaille pas.
    """
    cfg = _config(ctx, projet)
    fiches = [
        _fiche_personnalise(ctx, d, avec_playbook=False, cfg=cfg)
        for d in cfg.agents.lister()
    ]
    if cfg.projet_id is not None:
        return fiches
    return [
        _fiche_defaut(ctx, a, avec_playbook=False, cfg=cfg)
        for a in GABARITS_DU_CODE
    ] + fiches



@router.get("/api/catalogue/{nom}")
async def catalogue_fiche(ctx: Ctx, nom: str, projet: str | None = None) -> dict[str, Any]:
    """La définition complète d'un agent du catalogue, playbook compris.

    Symétrique de la liste (#1042) : un **gabarit** de rôle ne se lit qu'au
    niveau gabarit. Demandé sur un projet, il sort en 404 — le projet ne l'a
    pas, et le servir ferait dire à la fiche le contraire de la liste.
    """
    cfg = _config(ctx, projet)
    defaut = (
        None
        if cfg.projet_id is not None
        else next((a for a in GABARITS_DU_CODE if a.nom == nom), None)
    )
    if defaut is not None:
        return _fiche_defaut(ctx, defaut, avec_playbook=True, cfg=cfg)
    definition = _personnalise_ou_none(ctx, nom, cfg)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
        )
    return _fiche_personnalise(ctx, definition, avec_playbook=True, cfg=cfg)



@router.post("/api/catalogue", status_code=201)
async def creer_agent(
    ctx: Ctx,
    requete: AgentCreationRequete, projet: str | None = None
) -> dict[str, Any]:
    """Crée un agent personnalisé (#72) : définition persistée hors du code.

    L'agent entre immédiatement dans la vue `GET /api/agents` (cible de
    réassignation manuelle) ; il est routable et exécutable par les moteurs
    et workers construits ensuite, qui chargent le catalogue effectif à
    leur démarrage. 409 si le nom est déjà pris (agent par défaut, acteur
    système ou personnalisé existant), 422 si la définition est invalide.

    L'agent naît **dans le projet** demandé (#1038) : c'est son équipe, et un
    homonyme peut exister dans un autre projet sans conflit. Le nom reste
    unique **dans ce projet-là**, et les noms du code restent réservés
    partout — ils désignent le même rôle d'un projet à l'autre.
    """
    state = ctx.state
    cfg = _config(ctx, projet)
    if (
        requete.nom in NOMS_RESERVES
        or _personnalise_ou_none(ctx, requete.nom, cfg) is not None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"nom déjà pris : {requete.nom} — modification d'un agent "
                "personnalisé via PUT /api/catalogue/{nom}."
            ),
        )
    try:
        definition = cfg.agents.ecrire(
            AgentDefinition(
                nom=requete.nom,
                role=requete.role,
                competences=tuple(requete.competences),
                playbook=requete.playbook,
                modele=requete.modele,
                fournisseur=requete.fournisseur,
                effort=requete.effort,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state.ajouter_agent(definition.nom, definition.role)
    return _fiche_personnalise(ctx, definition, avec_playbook=True, cfg=cfg)



@router.post("/api/catalogue/generation")
async def generer_definition_agent(
    ctx: Ctx,
    requete: AgentGenerationRequete, projet: str | None = None
) -> dict[str, Any]:
    """Propose une définition d'agent à partir d'une intention (#257) — sans rien créer.

    Le modèle rend rôle, compétences, playbook et — confrontés au registre
    (#253) — un fournisseur et un modèle. La réponse est un **brouillon** : elle
    n'écrit rien, n'ajoute aucun agent au catalogue et ne réserve aucun nom. La
    création reste le `POST /api/catalogue` ordinaire, à partir des champs que
    l'utilisateur a relus (et modifiés) dans le formulaire.

    Le `nom` proposé est libre **au moment de la réponse** : les noms réservés
    et les agents personnalisés existants en sont écartés. Ce n'est pas une
    réservation — un homonyme créé entre-temps rendra le 409 habituel.

    422 si l'intention est vide ou plus longue qu'une phrase, 502 si le modèle
    est injoignable, muet, ou répond hors contrat : dans les trois cas rien
    n'est écrit et l'appel se rejoue sans conséquence.
    """
    generateur_agent = ctx.generateur_agent
    cfg = _config(ctx, projet)
    pris = set(NOMS_RESERVES) | {d.nom for d in cfg.agents.lister()}
    try:
        async with au_compte_du_projet(ctx, cfg.projet_id, "définition d'agent générée"):
            proposition = await generateur_agent.proposer(requete.intention, noms_pris=pris)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GenerationIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return proposition.to_dict()



@router.put("/api/catalogue/{nom}")
async def modifier_agent(
    ctx: Ctx,
    nom: str, requete: AgentModificationRequete, projet: str | None = None
) -> dict[str, Any]:
    """Remplace la définition d'un agent personnalisé (l'intégrale, pas un diff).

    La définition modifiée vaut pour les moteurs construits ensuite. 403
    sur un agent par défaut (défini par le code), 404 si l'agent n'existe
    pas, 422 si la nouvelle définition est invalide — 404 aussi sur l'agent
    d'un **autre** projet, qui est inconnu d'ici (#1038).
    """
    state = ctx.state
    cfg = _config(ctx, projet)
    _exige_personnalise(ctx, nom, cfg)
    try:
        definition = cfg.agents.ecrire(
            AgentDefinition(
                nom=nom,
                role=requete.role,
                competences=tuple(requete.competences),
                playbook=requete.playbook,
                modele=requete.modele,
                fournisseur=requete.fournisseur,
                effort=requete.effort,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state.ajouter_agent(definition.nom, definition.role)
    return _fiche_personnalise(ctx, definition, avec_playbook=True, cfg=cfg)



@router.delete("/api/catalogue/{nom}")
async def supprimer_agent(ctx: Ctx, nom: str, projet: str | None = None) -> dict[str, Any]:
    """Supprime un agent personnalisé du catalogue.

    Sa fiche quitte la vue `GET /api/agents` ; les moteurs construits
    ensuite ne le chargent plus. Son réglage de capacité (#86) part avec
    lui — un homonyme recréé plus tard repartira des défauts. 403 sur un
    agent par défaut, 404 s'il n'existe pas.

    Le retrait ne touche que **ce projet-là** (#1038) : un homonyme d'un
    autre projet, et le gabarit du même nom, restent.
    """
    state = ctx.state
    cfg = _config(ctx, projet)
    _exige_personnalise(ctx, nom, cfg)
    cfg.agents.supprimer(nom)
    cfg.capacites.supprimer(nom)
    cfg.surcharges.supprimer(nom)
    state.retirer_agent(nom)
    return {"nom": nom, "supprime": True}



@router.put("/api/catalogue/{nom}/reglages")
async def surcharger_agent(
    ctx: Ctx,
    nom: str, requete: SurchargeRequete, projet: str | None = None
) -> dict[str, Any]:
    """Surcharge les réglages de modèle d'un agent du code (#259), sans le dupliquer.

    Le troisième état du catalogue : l'agent reste celui du code — rôle,
    compétences et playbook continuent d'en venir et d'en suivre les
    évolutions —, seuls `fournisseur`, `modele` et `effort` sont recouverts.
    Ce qui n'est pas envoyé retourne au code et se relit dans `herite`.
    Poser les trois à `null` **annule** la surcharge, comme `DELETE`.

    `modele` et `effort` valent pour les moteurs construits ensuite ;
    `fournisseur` reste déclaratif au POC. 403 sur un agent personnalisé
    (ses réglages s'éditent par `PUT /api/catalogue/{nom}`) ou un acteur
    système, 404 sur un nom inconnu.

    La surcharge est posée **dans le projet** demandé (#1038) : le même
    agent du code peut tourner sur deux modèles dans deux projets.
    """
    cfg = _config(ctx, projet)
    agent = _exige_du_code(ctx, nom, cfg)
    cfg.surcharges.ecrire(
        SurchargeAgent(
            nom=nom,
            fournisseur=requete.fournisseur,
            modele=requete.modele,
            effort=requete.effort,
        )
    )
    return _fiche_defaut(ctx, agent, avec_playbook=True, cfg=cfg)



@router.delete("/api/catalogue/{nom}/reglages")
async def annuler_surcharge(ctx: Ctx, nom: str, projet: str | None = None) -> dict[str, Any]:
    """Annule la surcharge d'un agent du code : retour à ses réglages du code (#259).

    ⚠ **Annule, ne supprime pas** — et c'est tout le sujet du critère 3 de
    #259 : l'agent reste au catalogue, il redevient simplement celui du
    code. La suppression, elle, reste réservée aux agents personnalisés
    (`DELETE /api/catalogue/{nom}`, qui refuse un agent du code en 403).
    Deux verbes voisins pour deux gestes que rien ne doit confondre.

    Idempotent : annuler une surcharge absente rend la même fiche, sans
    erreur — il n'y a rien à signaler à qui demande un état déjà atteint.
    403/404 comme `PUT`.

    Cadré sur un projet (#1038), « retour aux réglages du code » veut dire
    retour au **gabarit** — c'est-à-dire au code tant que rien n'y est
    surchargé, ce qui est l'invariant du #259 dans le nouveau rangement.
    """
    cfg = _config(ctx, projet)
    agent = _exige_du_code(ctx, nom, cfg)
    cfg.surcharges.supprimer(nom)
    return _fiche_defaut(ctx, agent, avec_playbook=True, cfg=cfg)
