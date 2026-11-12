"""Les routes du parc d'agents : son état et la capacité de chacun."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.agents.capacity import CapaciteAgent
from maestro.agents.configuration import ConfigurationAgents
from maestro.controltower.events import EVENEMENT_AGENT_CAPACITE, Event
from maestro.controltower.routes.communs import _config
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import CapaciteRequete
from maestro.controltower.state import CAPACITE_ACTIVE, CAPACITE_DESACTIVE, EtatAgent

router = APIRouter()


def _parc(ctx: Contexte, cfg: ConfigurationAgents) -> list[EtatAgent]:
    """Le parc que `cfg` désigne : l'équipe d'un projet, ou la projection entière.

    Le pendant d'`_config` pour la vue des agents, **écrit une fois** (#1101)
    parce que deux routes le lisent et qu'elles doivent le lire pareil :
    `GET /api/agents` et `POST /api/agents/{nom}/capacite`, qui décide de son
    404. Les avoir laissées diverger est précisément ce qui a produit le
    symptôme du ticket — une équipe qu'un écran ne montrait plus et dont la
    capacité ne se réglait donc plus.

    Cadré sur un projet, il part du **disque** (`state.equipe`) : c'est là
    que vit l'équipe validée, quand la projection, elle, repart vide à chaque
    redémarrage. Sans projet, c'est le niveau des gabarits, et le parc rendu
    est celui de la projection — ce que cette route servait avant #1038.
    """
    state = ctx.state
    if cfg.projet_id is None:
        return state.agents()
    return state.equipe(cfg.catalogue(), cfg.capacites.lister())



@router.get("/api/agents")
async def agents(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """L'état des agents : libre/occupé, tâche courante, compteurs, coût cumulé.

    `projet` cadre le **parc** sur l'équipe de ce projet (#1038, renverse la
    première ligne du tableau « ce qui reste global » de docs/05 §2.0) : la
    vue rend les agents de son catalogue, jamais ceux d'un autre projet.

    Ce qui est cadré est l'**appartenance**, pas les compteurs : ceux-ci
    restent ceux que la projection a vus, et ils ne se recalculent pas par
    projet — c'est la vue des tâches (`/api/taches?projet=`) qui répond à
    « qu'a fait cet agent ici ». Le **routage** sur l'équipe d'un projet,
    lui, est le lot #1041.

    ⚠ Cadrée sur un projet, la vue est **dérivée du disque** depuis #1101 :
    son catalogue et ses capacités, la projection n'y apportant que
    l'activité. Elle filtrait auparavant la projection, qui repart vide à
    chaque redémarrage de l'API (le catalogue de la racine est vide depuis
    #1042) : l'équipe validée d'un projet disparaissait de l'écran au
    redémarrage, et portait une instance là où son projet en avait rangé
    deux. Le routage, lui, lisait déjà la bonne — c'est bien la vue qui
    mentait.

    Un projet qu'on vient de créer rend une liste **vide** (#1042) : il n'a
    aucun agent tant que son équipe n'a pas été validée, et une vue vide le
    dit mieux que cinq rôles que personne n'a recrutés. Même phrase qu'avant
    #1101, et pour une raison de plus : un catalogue vide n'a rien à rendre.

    Omis, la vue rend le parc entier, comme avant ce lot.
    """
    return [a.to_dict() for a in _parc(ctx, _config(ctx, projet))]



@router.post("/api/agents/{nom}/capacite")
async def regler_capacite(
    ctx: Ctx,
    nom: str, requete: CapaciteRequete, projet: str | None = None
) -> dict[str, Any]:
    """Règle la capacité d'un agent (#86, EF-21) : actif/désactivé, instances.

    Persiste le réglage dans le dépôt partagé — celui que moteur et workers
    relisent à chaque tâche : un agent désactivé ne reçoit plus de tâches,
    le plafond d'instances borne ses exécutions simultanées. Applique
    l'événement `agent.capacite` à l'état (le REST répond déjà à jour) puis
    le publie sur le bus — les fiches agents des clients WebSocket suivent
    en temps réel, et la pompe le réapplique sans effet (idempotence).
    404 si l'agent est inconnu, 422 si la requête ne règle rien ou si le
    plafond d'instances est invalide (< 1).

    Le réglage est écrit **dans le projet** demandé (#1038) : le même agent
    peut avoir trois instances ici et une ailleurs — c'est le « nombre
    d'instances rangé par projet » du critère.

    L'agent est cherché dans le **parc du projet demandé** (`_parc`, #1101),
    le même que `GET /api/agents` rend : réglable est exactement ce qui est
    montré. Le chercher dans la seule projection revenait à 404 sur toute
    l'équipe d'un projet au redémarrage de l'API — l'écran la listait déjà
    (depuis ce lot), et aucun de ses curseurs n'aurait répondu.
    """
    bus = ctx.bus
    state = ctx.state
    cfg = _config(ctx, projet)
    fiche = next((a for a in _parc(ctx, cfg) if a.nom == nom), None)
    if fiche is None:
        raise HTTPException(
            status_code=404, detail=f"agent inconnu : {nom} (voir GET /api/agents)"
        )
    if requete.actif is None and requete.instances is None:
        raise HTTPException(
            status_code=422,
            detail="rien à régler : renseigner `actif` et/ou `instances`.",
        )
    courante = cfg.capacites.lire(nom)
    try:
        capacite = cfg.capacites.ecrire(
            CapaciteAgent(
                nom=nom,
                actif=courante.actif if requete.actif is None else requete.actif,
                instances=(
                    courante.instances
                    if requete.instances is None
                    else requete.instances
                ),
                # Des instances posées ici sont un **choix** de la personne, et le
                # run le garde au lieu d'en dériver un de son plan (#1299) — une
                # seule instance comprise. Régler seulement `actif` ne fixe rien.
                instances_fixees=(
                    courante.fixe_ses_instances if requete.instances is None else True
                ),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event = Event(
        type=EVENEMENT_AGENT_CAPACITE,
        agent=nom,
        role=fiche.role,
        statut=CAPACITE_ACTIVE if capacite.actif else CAPACITE_DESACTIVE,
        instances=capacite.instances,
        detail="capacité réglée depuis la Control Tower",
    )
    state.appliquer(event)
    await bus.publish(event)
    # La réponse porte le réglage **qu'on vient d'écrire**, et pas celui que
    # la projection tient : cadrée sur un projet, la fiche est une copie dont
    # `actif`/`instances` viennent du dépôt du projet, que l'événement appliqué
    # ci-dessus ne touche pas (#1101). Au niveau des gabarits, `avec_capacite`
    # rend la même chose que la fiche partagée — `_applique_capacite` vient
    # d'y poser les deux mêmes valeurs.
    return fiche.avec_capacite(capacite).to_dict()
