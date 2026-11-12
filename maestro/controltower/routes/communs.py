"""Ce que plusieurs domaines de routes partagent."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import HTTPException

from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import IntegrationMcp, references_env
from maestro.agents.mcp_federation import federer_memo
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.store import AgentDefinition
from maestro.controltower.depense_du_fil import au_compte_du_fil
from maestro.controltower.events import ACTEUR_RUN, Event
from maestro.controltower.portee import (
    PorteeProjet,
    PorteeRefusee,
    resoudre_portee,
    resoudre_projet_configuration,
)
from maestro.controltower.projets import detail_refus, statut_http
from maestro.controltower.routes.contexte import Contexte
from maestro.controltower.state import STATUTS_EXECUTION_TERMINAUX
from maestro.decision_humaine import ETENDUE_RUN
from maestro.equipe import RoleValide


class Diffusion:
    """Fan-out des événements vers les WebSockets connectées, **à leur portée**.

    Une file par connexion, alimentée par la pompe ; chaque handler WebSocket
    draine la sienne. Files non bornées : le POC diffuse peu d'événements et
    une connexion les consomme au fil de l'eau.

    Chaque connexion déclare sa **portée projet** à l'ouverture (#277) et ne
    reçoit que les événements que celle-ci retient — le tri se fait donc **à
    l'entrée de la file**, pas à l'émission : une socket cadrée sur un projet
    n'est même pas réveillée par le travail d'un autre, et aucun événement
    étranger ne peut être servi par erreur en aval. C'est la même règle que les
    vues REST (`PorteeProjet.retient`), appliquée au flux : un client qui
    recharge son état puis suit les événements voit deux fois le même périmètre.
    """

    def __init__(self) -> None:
        self._connexions: dict[asyncio.Queue[Event], PorteeProjet] = {}

    def connecter(self, portee: PorteeProjet | None = None) -> asyncio.Queue[Event]:
        """Enregistre une connexion : sa file recevra les événements de sa portée.

        `None` vaut la vue transverse, pour les appels internes (#277) ; les
        routes, elles, exigent une portée.
        """
        file: asyncio.Queue[Event] = asyncio.Queue()
        self._connexions[file] = portee if portee is not None else PorteeProjet.tous()
        return file

    def deconnecter(self, file: asyncio.Queue[Event]) -> None:
        """Retire la connexion ; ses événements en attente disparaissent avec elle."""
        self._connexions.pop(file, None)

    def diffuser(self, event: Event) -> None:
        """Pousse `event` aux connexions dont la portée le retient."""
        for file, portee in self._connexions.items():
            if portee.retient(event.projet_id):
                file.put_nowait(event)



def _detail_refus(exc: Exception) -> dict[str, Any]:
    """Le corps d'un refus de source ou de lancement : `{motif, message[, index]}`.

    `detail_refus` (#223) donne les deux premiers champs — même convention que
    les routes projets, un code stable plutôt qu'une phrase à analyser, et
    `requete-invalide` en repli pour ce qui n'a pas de motif propre (objectif
    vide, garde-fou hors bornes). L'`index` s'y ajoute quand le refus **vise une
    source** (#315) : « une source est trop grosse » sans dire laquelle
    obligerait à tout relire pour savoir quoi retirer.
    """
    detail: dict[str, Any] = dict(detail_refus(exc))
    index = getattr(exc, "index", None)
    if isinstance(index, int) and not isinstance(index, bool):
        detail["index"] = index
    return detail



def registre(ctx: Contexte) -> RegistreMcp:
    """La bibliothèque du moment : celle injectée, sinon la fédérée (mémoïsée)."""
    registre_mcp_injecte = ctx.registre_mcp_injecte
    if registre_mcp_injecte is not None:
        return registre_mcp_injecte
    return federer_memo().registre



async def creer_equipe_du_projet(
    ctx: Contexte,
    projet_id: str, roles: Sequence[RoleValide], proposition_id: str = ""
) -> dict[str, Any]:
    """Crée l'équipe validée — la voie de #1040, que la route et le fil partagent.

    Une seule écriture d'équipe, et ses deux conséquences ensemble : les
    fichiers du projet (`ServiceEquipe.creer`, hors de la boucle d'événements),
    puis la projection, où les agents deviennent cibles de réassignation
    comme ceux du `POST /api/catalogue`.
    """
    equipe = ctx.equipe
    state = ctx.state
    rapport = await asyncio.to_thread(
        equipe.creer, projet_id, roles, proposition_id=proposition_id
    )
    for agent in rapport["agents"]:
        state.ajouter_agent(agent["nom"], agent["role"])
    return rapport



def au_compte_du_projet(
    ctx: Contexte,
    id_projet: str | None, titre: str
) -> AbstractAsyncContextManager[None]:
    """Ce qu'une route dépense pour `id_projet`, au grand livre du fil (#1534).

    Les cartes du fil appellent aussi des routes à elles — l'équipe proposée et
    corrigée, l'analyse, le questionnaire et la génération de l'outillage — : ce
    sont des appels modèle pour un projet nommé dans le chemin, hors de tout run.

    Les gestes d'atelier aussi (#1538) : la génération d'un agent du catalogue,
    la révision et la réécriture d'un playbook. C'est Maestro qui travaille pour
    un projet, hors de tout run, donc le même poste. Le même générateur écrit
    déjà les playbooks de l'équipe proposée sous ce poste, et un poste à part
    rangerait le même travail en deux endroits selon l'écran d'où il part. Leur
    projet vient de `?projet=`, et `None` (le niveau des gabarits) range la
    dépense hors projet, toujours comptée dans la vue transverse.
    """
    bus = ctx.bus
    return au_compte_du_fil(bus, titre=titre, agent=ACTEUR_RUN, projet=lambda: id_projet)



def _portee(ctx: Contexte, projet: str | None) -> PorteeProjet:
    """La **portée projet** d'une lecture (#277), ou un refus motivé.

    Le contrat unique des vues qui agrègent — Kanban, exécutions, coûts,
    validations, journal, flux temps réel : `?projet=<id>` pour un projet,
    `?projet=tous` pour la vue transverse, `?projet=aucun` pour les travaux
    hors projet. **Omis, le paramètre est refusé** (422 `projet-requis`) :
    une lecture sans périmètre serait un mélange silencieux de tous les
    projets, et un refus motivé se diagnostique là où une liste vide se
    confondrait avec un projet sans activité. Un identifiant inconnu du
    dépôt sort en 404 `projet-inconnu`, par la même porte que les refus de
    `ServiceProjets` (#223) — motif, message, code.
    """
    projets = ctx.projets
    try:
        return resoudre_portee(projet, projet_connu=projets)
    except PorteeRefusee as exc:
        raise _refus_projet(exc) from exc



def _config(ctx: Contexte, projet: str | None) -> ConfigurationAgents:
    """La configuration d'agent cadrée par le projet demandé (#1038), ou un refus motivé.

    Le pendant d'`_portee` pour ce qu'un agent **est** — définition,
    playbook, autorisations, serveurs MCP, capacité — plutôt que pour ce
    qu'il fait. Deux différences, et chacune est une décision (voir
    `resoudre_projet_configuration`) : `tous`/`aucun` sont refusés, une
    configuration appartenant à un projet réel ; et `?projet=` **omis** n'est
    pas refusé : il rend le niveau des **gabarits**, un niveau à part entière
    et celui d'un appel qui ne nomme aucun projet.
    """
    gabarits = ctx.gabarits
    projets = ctx.projets
    try:
        projet_id = resoudre_projet_configuration(projet, projet_connu=projets)
    except PorteeRefusee as exc:
        raise _refus_projet(exc) from exc
    return gabarits.pour_projet(projet_id)



def _integration_pool_dict(
    ctx: Contexte,
    integration: IntegrationMcp, etats: dict[str, Any]
) -> dict[str, Any]:
    """Une intégration du pool + l'état de ses secrets côté coffre projet (#133).

    Enrichit `IntegrationMcp.to_dict` (id + déclaration à secrets masqués) de
    ce dont l'UI a besoin pour dire *où en est* l'intégration : son
    `mode_auth` et sa `procedure_url` (repris de l'entrée curée quand l'id y
    correspond), et pour **chaque** variable `${VAR}` requise, si son secret
    est **présent** dans le coffre projet et **valide** (un token OAuth
    expiré ne l'est plus). Aucune valeur de secret n'est jamais réémise.

    Elle porte aussi **ce qui a changé sous elle** (#678) : `admission`
    (par quel geste elle est montable), `signaux` (ce que l'amont en dit
    depuis), et `alerte` quand elle n'est plus dans l'allowlist — révoquée,
    ou disparue de la bibliothèque. C'est la moitié « sans le dire » du
    critère 2 : le serveur reste monté, mais l'écran qui le liste ne peut
    pas l'afficher comme si de rien n'était. `curee: false` ne suffit pas à
    le dire : il ne dit pas *pourquoi*, et une intégration montée hier qui
    devient « non curée » sans un mot se lit comme un bug.
    """
    courant = registre(ctx)
    entree = courant.get(integration.id)
    requis = (
        {v.cle: v for v in entree.secrets}
        if entree is not None
        else {cle: None for cle in references_env(integration.serveur)}
    )
    secrets_etat: list[dict[str, Any]] = []
    for cle in requis:
        variable = requis[cle]
        etat = etats.get(cle)
        secrets_etat.append(
            {
                "cle": cle,
                "description": variable.description if variable is not None else "",
                "secret": variable.secret if variable is not None else True,
                "present": etat is not None,
                "valide": etat["valide"] if etat is not None else False,
                "ephemere": etat["ephemere"] if etat is not None else False,
                "expire_le": etat["expire_le"] if etat is not None else None,
            }
        )
    revocation = courant.revocation_de(integration.id)
    if entree is not None:
        alerte = ""
    elif revocation is not None:
        alerte = (
            f"admission révoquée le {revocation.revoquee_le or '?'} par "
            f"{revocation.revoquee_par or '?'}"
            f"{f' — {revocation.motif}' if revocation.motif else ''} : ce serveur "
            "reste monté mais n'est plus dans l'allowlist. Le retirer du pool : "
            f"DELETE /api/mcp/pool/{integration.id}."
        )
    else:
        # ⚠ Trois causes, pas deux (#263) : une intégration **migrée** d'une
        # déclaration héritée que la bibliothèque ne reconnaît pas arrive ici
        # par un chemin parfaitement normal. Ne nommer que les deux premières
        # ferait lire « quelque chose a disparu » là où rien n'a bougé.
        alerte = (
            "ce serveur est monté mais ne figure pas dans la bibliothèque : "
            "il a pu être retiré du seed, son entrée admise devenir illisible, "
            "ou provenir d'une déclaration de fichier migrée qu'aucune entrée "
            "ne décrit. Il reste monté tel qu'il a été configuré."
        )
    return {
        **integration.to_dict(),
        "mode_auth": entree.mode_auth if entree is not None else None,
        "procedure_url": entree.procedure_url if entree is not None else "",
        "curee": entree is not None,
        "source": entree.source if entree is not None else None,
        "admission": (
            entree.admission.trace()
            if entree is not None and entree.admission is not None
            else revocation.trace()
            if revocation is not None
            else None
        ),
        "signaux": [s.to_dict() for s in courant.signaux_de(integration.id)],
        "alerte": alerte,
        "secrets": secrets_etat,
    }



def _pool_mcp(ctx: Contexte, cfg: ConfigurationAgents | None = None) -> dict[str, Any]:
    """Le pool projet des intégrations MCP + l'état de leurs secrets (#133).

    `{integrations: [...], erreur}` : chaque intégration est enrichie de
    l'état de ses secrets (coffre projet). `erreur` porte la cause si le
    pool stocké est invalide — même contrat de visibilité que `mcp_erreur`
    (la misconfiguration s'affiche sans casser la fiche ni le listing).

    `cfg` est la configuration cadrée sur le projet demandé (#1038) ; son
    absence désigne le niveau des **gabarits**, ici comme dans les autres
    fabricants de fiche — c'est un niveau, pas un cas particulier.
    """
    gabarits = ctx.gabarits
    secrets = ctx.secrets
    cfg = cfg if cfg is not None else gabarits
    try:
        integrations = cfg.mcp.pool()
    except ValueError as exc:
        return {"integrations": [], "erreur": str(exc)}
    try:
        etats = {e.cle: e.to_dict() for e in secrets.etat_projet()}
    except ValueError:
        # Coffre projet illisible : on sert le pool sans état de secret
        # plutôt que de masquer les intégrations déjà configurées.
        etats = {}
    return {
        "integrations": [_integration_pool_dict(ctx, i, etats) for i in integrations],
        "erreur": None,
    }



def _accords_en_vigueur(ctx: Contexte, nom: str, cfg: ConfigurationAgents) -> list[dict[str, Any]]:
    """Les accords étendus de `nom` qui couvrent encore quelque chose (#1185).

    Un accord de projet vaut tant qu'on ne l'a pas retiré ; un accord de run,
    tant que son run n'est pas soldé — un run inconnu de la projection reste
    servi, on ne sait pas dire qu'il est fini. Un dépôt illisible rend une
    liste vide : l'agent est alors redemandé à chaque appel, le sens sûr.
    """
    state = ctx.state
    try:
        accords = cfg.permissions.accords().lire(nom)
    except ValueError:
        return []
    en_vigueur = []
    for accord in accords:
        if accord.etendue == ETENDUE_RUN:
            execution = state.execution(accord.run_id)
            if execution is not None and execution.statut in STATUTS_EXECUTION_TERMINAUX:
                continue
        en_vigueur.append(accord.to_dict())
    return en_vigueur



def _personnalise_ou_none(
    ctx: Contexte,
    nom: str, cfg: ConfigurationAgents | None = None
) -> AgentDefinition | None:
    """La définition persistée de `nom`, ou None (nom hors slug compris — jamais levé)."""
    gabarits = ctx.gabarits
    cfg = cfg if cfg is not None else gabarits
    try:
        return cfg.agents.lire(nom)
    except ValueError:
        return None


def _refus_projet(exc: Exception, *, explorateur: bool = False) -> HTTPException:
    """Un refus du service des projets en `HTTPException` — motif compris.

    Le corps est `{motif, message}` et non une phrase libre : l'écran
    Projets doit pouvoir dire *pourquoi* une racine est refusée (docs/05
    §2.7), ce qu'un texte à analyser ne lui permettrait pas. Le code vient
    du motif (`statut_http`), avec 422 en repli — **jamais un 500** : une
    racine hors périmètre est une réponse, pas une panne (critère #223).
    """
    return HTTPException(
        status_code=statut_http(exc, explorateur=explorateur),
        detail=detail_refus(exc),
    )



def _exige_agent_du_catalogue(
    ctx: Contexte,
    nom: str, cfg: ConfigurationAgents | None = None
) -> Agent:
    """La fiche catalogue de `nom` (défaut ou personnalisé), ou l'erreur 404.

    Le chat ne s'adresse qu'aux agents du catalogue effectif — celui que
    les moteurs chargent : un nom inconnu n'ouvre pas de fil orphelin.
    Cadré sur un projet (#1038), « du catalogue » veut dire de **son**
    catalogue : un agent d'un autre projet y est inconnu, ce qui est
    exactement ce qu'on veut d'une route de configuration.

    Au niveau **gabarit**, les gabarits de rôle en font partie (#1042) : ce
    niveau est celui qu'on consulte et qu'on règle, et leurs serveurs MCP,
    autorisations et fils de chat s'y adressent. Dans un projet, non — il
    n'a que son équipe.
    """
    gabarits = ctx.gabarits
    niveau = cfg if cfg is not None else gabarits
    defaut = (
        None
        if niveau.projet_id is not None
        else next((a for a in GABARITS_DU_CODE if a.nom == nom), None)
    )
    if defaut is not None:
        return defaut
    definition = _personnalise_ou_none(ctx, nom, cfg)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"agent inconnu : {nom} (voir GET /api/catalogue)",
        )
    return definition.to_agent()
