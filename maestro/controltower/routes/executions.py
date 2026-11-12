"""Les routes des exécutions : lancer un run, le piloter et lire ce qu'il a produit.

Piloter, c'est annuler, suspendre, reprendre, relancer, et trancher ses arbitrages (plafond,
brief). Lire, c'est son détail, son coût, son graphe, sa frise, ses décisions et son bilan."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.brief import ACTEUR_BRIEF, ROLE_BRIEF
from maestro.controltower.decisions import decisions_du_run
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_REPONSES,
    Event,
    brief_depuis,
    titre_court,
)
from maestro.controltower.executions import (
    MOTIF_GESTE_RUN_NON_SUSPENDU,
    MOTIF_GESTE_RUN_SANS_HOTE,
    MOTIF_GESTE_RUN_SUSPENDU,
    MOTIF_RELANCE_RUN_INCONNU,
    MOTIF_RELANCE_RUN_SOLDE,
    MOTIF_RELANCE_RUN_VIVANT,
    MOTIF_RELANCE_SANS_CADRAGE,
    MOTIF_REPRISE_EN_COURS,
    MOTIF_REPRISE_ETAT_ILLISIBLE,
    RelanceRefusee,
)
from maestro.controltower.frise import frise_du_run
from maestro.controltower.gestes import GESTE_ANNULATION, GESTE_PAUSE, GESTE_REPRISE, GesteRefuse
from maestro.controltower.plafond import evenement_decision
from maestro.controltower.routes.communs import _detail_refus, _portee
from maestro.controltower.routes.contexte import Contexte, Ctx
from maestro.controltower.routes.requetes import (
    DecisionBriefRequete,
    DecisionPlafondRequete,
    LancementExecutionRequete,
    ReponsesBriefRequete,
)
from maestro.controltower.state import (
    BRIEF_APPROUVE,
    BRIEF_REFUSE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_PLAFOND,
    EXECUTION_EN_ATTENTE_REPONSES,
)
from maestro.engine.plafond import (
    GESTE_ARRETER,
    GESTE_REDUIRE,
    GESTE_RELEVER,
    DecisionPlafond,
    DemandePlafond,
)
from maestro.orchestrator.errors import BriefValidationError
from maestro.orchestrator.schema import validate_brief

router = APIRouter()


#: Le code HTTP de chaque motif de refus d'une **relance** (#349). La table vit
#: ici et non dans le service : c'est la route qui parle HTTP, le service ne
#: connaît que ses motifs. Repli à `422` pour un motif non listé — un refus qu'on
#: n'a pas su classer reste une requête que l'API n'a pas honorée, jamais un
#: succès.
_CODE_REFUS_RELANCE: dict[str, int] = {
    MOTIF_RELANCE_RUN_INCONNU: 404,
    # Les deux mêmes 409 que l'annulation, et pour la même raison : l'état du run
    # interdit le geste, sans que la requête soit malformée.
    MOTIF_RELANCE_RUN_SOLDE: 409,
    MOTIF_RELANCE_RUN_VIVANT: 409,
}



#: Le statut HTTP des refus de la pause, de la reprise et de l'annulation (#1179) —
#: ceux que leurs routes rendaient déjà, maintenant que les règles vivent dans le
#: service (`refus_du_geste`) : `404` sur un run inconnu, `409` sur tout état qui
#: interdit le geste (soldé, déjà suspendu, pas suspendu, sans hôte pour la pause).
_CODE_REFUS_GESTE: dict[str, int] = {
    MOTIF_RELANCE_RUN_INCONNU: 404,
    MOTIF_RELANCE_RUN_SOLDE: 409,
    MOTIF_GESTE_RUN_SUSPENDU: 409,
    MOTIF_GESTE_RUN_NON_SUSPENDU: 409,
    MOTIF_GESTE_RUN_SANS_HOTE: 409,
}



#: Ce que la **reprise sur le plan** peut refuser en plus, une fois passés les refus
#: du geste (#1391) : une reprise déjà en train de partir (`409`, l'état du run),
#: un état acquis qu'on n'a pas pu relire (`503`, le stockage — la requête n'y est
#: pour rien, et elle passera une fois le stockage revenu) et, par son repli sur la
#: relance, un run sans plan ni brief approuvé (`422`, comme `…/relancer`).
_CODE_REFUS_REPRISE: dict[str, int] = {
    **_CODE_REFUS_GESTE,
    **_CODE_REFUS_RELANCE,
    MOTIF_REPRISE_EN_COURS: 409,
    MOTIF_REPRISE_ETAT_ILLISIBLE: 503,
    MOTIF_RELANCE_SANS_CADRAGE: 422,
}



#: Ce que le journal du run dira de chaque geste au plafond (#1182) — qui a décidé,
#: et d'où. Le chiffre, lui, est dans la ligne que le moteur écrit.
_DETAIL_GESTE_PLAFOND = {
    GESTE_RELEVER: "plafond relevé depuis la Control Tower",
    GESTE_REDUIRE: "portée réduite depuis la Control Tower",
    GESTE_ARRETER: "run arrêté depuis la Control Tower",
}



def _detail_decision_plafond(geste: str) -> str:
    """La phrase qu'une décision au plafond porte au journal — vide pour un geste inconnu."""
    return _DETAIL_GESTE_PLAFOND.get(geste, "")



def _refus_plafond(decision: DecisionPlafond, demande: DemandePlafond) -> str:
    """Pourquoi `decision` ne se tient pas face à la question en vol — "" si elle se tient.

    Trois refus, chacun pour ce qu'il éviterait :

    - une tâche écartée que la question ne nommait pas : la personne n'a décidé
      que de ce qu'on lui a montré ;
    - une réduction qui écarte **tout** ce qui reste : c'est un arrêt, et le dire
      autrement ferait relever un plafond pour ne rien faire ;
    - un plafond, relevé ou laissé tel quel, que la dépense **atteint déjà** : le
      run retomberait dessus à sa première mesure, c'est-à-dire reposerait la même
      question aussitôt.
    """
    if not decision.reprend:
        return ""
    connues = {tache.tache_id for tache in demande.restantes}
    inconnues = [tache for tache in decision.ecartees if tache not in connues]
    if inconnues:
        return (
            f"tâche(s) à écarter inconnue(s) de la question en vol : {', '.join(inconnues)}."
        )
    if connues and set(decision.ecartees) >= connues:
        return "écarter tout ce qui reste revient à arrêter le run : choisissez « arrêter »."
    cout = (
        decision.plafond_cout_usd
        if decision.plafond_cout_usd is not None
        else demande.plafond_cout_usd
    )
    jetons = (
        decision.plafond_tokens if decision.plafond_tokens is not None else demande.plafond_tokens
    )
    if cout is not None and demande.depense_usd is not None and demande.depense_usd >= cout:
        return (
            f"un plafond de {cout:.2f} $ ne couvre pas ce qui est déjà dépensé "
            f"({demande.depense_usd:.2f} $) : le run s'y arrêterait aussitôt."
        )
    if jetons is not None and demande.depense_tokens >= jetons:
        return (
            f"un plafond de {jetons} tokens ne couvre pas ce qui est déjà dépensé "
            f"({demande.depense_tokens} tokens) : le run s'y arrêterait aussitôt."
        )
    return ""



@router.get("/api/executions")
async def executions_liste(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """Les runs connus (#185) : résumés, **récents d'abord**.

    En cours comme passés, lancés depuis la Control Tower comme publiés par
    un autre process (`maestro-run --publier`) : le suivi ne distingue pas
    leur origine — la projection est la même. C'est la source de l'écran
    *Exécutions & traces* (docs/05 §2.4). `projet` est **obligatoire**
    (#277), au contrat commun : `<id>` | `tous` | `aucun`.

    Chaque résumé porte de quoi **dresser une liste utile** sans un appel par
    ligne (#473) : état, objectif, `progression` par statut de tâche, début
    et coût cumulé.
    """
    executions = ctx.executions
    return await executions.resumes(_portee(ctx, projet))



@router.post("/api/executions", status_code=202)
async def lancer_execution(ctx: Ctx, requete: LancementExecutionRequete) -> dict[str, Any]:
    """Lance une exécution (#185) et rend son résumé, `run_id` compris, **aussitôt**.

    Le run se déroule **hors** de la requête HTTP — et, depuis #446, hors du
    process de l'API : il part chez son **hôte** (`MAESTRO_HOTE_RUN`, un
    process détaché par défaut), qui lui survit. La réponse ne dit donc ni ce
    qu'il a produit ni même qu'il ira au bout : elle dit qu'il est parti. La
    suite arrive par le flux d'événements existant — chaque étape devient un
    `tache.statut` du WebSocket et une ligne du Kanban — et se relit sur
    `GET /api/executions/{run_id}`. 422 sur un objectif vide, un garde-fou
    hors bornes (les plafonds sont des maximums : ils doivent être > 0) ou
    une **source** refusée (#317).

    Avec des `sources`, la réponse porte en plus le **rapport de lecture**
    (#316) et n'est plus instantanée : la matière est lue avant qu'elle ne
    parte, sans quoi le rapport n'aurait rien à dire (docs/05 §6.1).
    """
    executions = ctx.executions
    try:
        return await executions.lancer(
            requete.objectif,
            plafond_cout_usd=requete.plafond_cout_usd,
            plafond_tokens=requete.plafond_tokens,
            timeout_tache_s=requete.timeout_tache_s,
            parallelisme=requete.parallelisme,
            ticket=None if requete.ticket is None else requete.ticket.en_reference(),
            projet_id=requete.projet_id,
            sources=requete.sources_declarees(),
            mode_brief=requete.brief,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_detail_refus(exc)) from exc



async def _refuser_le_geste(ctx: Contexte, geste: str, run_id: str) -> None:
    """Lève le statut HTTP du refus qu'un geste recevrait — rien s'il passe (#1179).

    Les règles sont celles du service (`ServiceExecutions.refus_du_geste`), que
    le fil appelle aussi : la route n'en garde que la traduction en code, et le
    `detail` reste la phrase du refus, comme avant que les règles n'y déménagent.
    """
    executions = ctx.executions
    refus = await executions.refus_du_geste(geste, run_id)
    if refus is not None:
        raise HTTPException(
            status_code=_CODE_REFUS_GESTE.get(refus.motif, 409), detail=str(refus)
        )



@router.post("/api/executions/{run_id}/annuler")
async def annuler_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Interrompt un run en cours (#185) : rend son résumé passé à « annulée ».

    L'issue est consignée comme n'importe quel fait du run : elle apparaît
    dans la projection (statut `annulee`, `fin` posée) **et** sur le flux
    d'événements. 404 si le run est inconnu, 409 s'il est déjà soldé — un run
    terminé n'est plus interruptible, et le dire vaut mieux que faire croire
    à une annulation.
    """
    executions = ctx.executions
    await _refuser_le_geste(ctx, GESTE_ANNULATION, run_id)
    annulee = await executions.annuler(run_id)
    if annulee is None:  # pragma: no cover - le résumé vient d'être lu
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return annulee



@router.post("/api/executions/{run_id}/pause")
async def suspendre_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Suspend un run en cours (#477) : rend son résumé passé à « en pause ».

    **Aucune tâche nouvelle n'est lancée ; celles qui sont en vol vont à leur
    terme.** C'est ce qui sépare cette route d'`…/annuler`, où les tâches sont
    tuées là où elles en sont et perdent leur travail. Le run n'est pas soldé,
    il ne change même pas de statut : `en_pause` est un drapeau **à côté** du
    statut, si bien qu'un run suspendu pendant l'attente de son brief continue
    de montrer qu'il attend ce brief.

    L'ordre traverse la frontière d'exécution par le **bus**, comme
    l'annulation (#444) : l'événement consigné ici est celui que le process
    détaché guette. Il survit donc au redémarrage de l'API — il est dans le
    journal durable (#97), rejoué au démarrage suivant.

    Le run **bat toujours** pendant sa pause (#348) : un run suspendu qui
    cesserait de battre ressortirait `orphelin` au bout d'une demi-heure, et
    #349 proposerait de le relancer depuis son brief, c'est-à-dire de repayer
    le cadrage d'un run qui n'a rien perdu.

    `404` si le run est inconnu, `409` s'il est **déjà soldé** (il n'y a plus
    rien à suspendre) ou **déjà suspendu** — répondre 200 à une pause qui
    n'était pas la première ferait passer pour un geste ce qui n'en est pas un.
    """
    executions = ctx.executions
    await _refuser_le_geste(ctx, GESTE_PAUSE, run_id)
    suspendue = await executions.mettre_en_pause(run_id)
    if suspendue is None:  # pragma: no cover - le résumé vient d'être lu
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return suspendue



@router.post("/api/executions/{run_id}/reprendre")
async def reprendre_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Reprend un run **là où il en était** (#477, #1391) : son résumé remis en route.

    Deux runs se reprennent, et c'est le **même** run qui repart dans les deux :

    - **suspendu** (#477) — rien n'a bougé pendant la pause, puisque rien n'a été
      tué. La route ne reconstruit rien : elle rouvre la porte, et les tâches
      qui attendaient repartent ;
    - **interrompu** (#1391) — Maestro éteint, une borne atteinte, en pause ou
      non, ou son hôte qui s'est tu. Il reprend **sur son plan, sous le même
      identifiant** : les tâches faites gardent leur état et leur sortie, l'aval
      lit celles-ci, et seules restent à faire la tâche interrompue et celles
      jamais démarrées. Un run interrompu **sans rien d'acquis** (arrêté avant
      son plan) repart de son brief approuvé — c'est l'endroit où il en était.

    ⚠ À ne pas confondre avec `…/relancer` (#349), qui **recommence** un run
    depuis son brief approuvé : c'est un **nouveau** run, avec un nouvel
    identifiant, qui re-planifie et refait tout.

    `404` si le run est inconnu ; `409` s'il travaille ou attend quelqu'un (rien
    à reprendre), s'il est soldé par son issue ou une annulation voulue, ou si sa
    reprise est déjà en train de partir ; `503` si son état acquis n'a pas pu
    être relu — jamais un repli sur la relance, qui referait un plan existant ;
    `422` s'il n'a ni plan ni brief approuvé.
    """
    executions = ctx.executions
    await _refuser_le_geste(ctx, GESTE_REPRISE, run_id)
    try:
        reprise = await executions.reprendre(run_id)
    except GesteRefuse as refus:
        raise HTTPException(
            status_code=_CODE_REFUS_REPRISE.get(refus.motif, 409), detail=str(refus)
        ) from refus
    if reprise is None:  # pragma: no cover - le résumé vient d'être lu
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return reprise



@router.post("/api/executions/{run_id}/relancer", status_code=202)
async def relancer_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Rejoue un run interrompu **sur son brief approuvé** (#349) : le nouveau résumé.

    Un run dont l'hôte est tombé emporte un cadrage **payé et validé par un
    humain** — clarification, brief, approbation. Ce cadrage est intégralement
    conservé dans la projection : cette route le rejoue en mode `sans`, donc sans
    repasser par la clarification ni par la validation, en conservant le projet
    et le ticket du run repris.

    La réponse est celle d'un lancement (`202` + `ResumeExecution`) parce que
    c'en est un : le run relancé est un **nouveau** run, qui porte `reprise_de`
    — de qui il est la suite. Le run repris, lui, est soldé en `annulee` : rien
    n'a raté, son hôte est tombé et quelqu'un a repris la main.

    Depuis #1391 c'est le geste de **recommencer** : reprendre un run interrompu
    sur son plan, sous le même identifiant et sans refaire ce qui est fait, passe
    par `…/reprendre`.

    `404` si le run est inconnu, `409` s'il est **déjà soldé** (rien à reprendre —
    sauf un arrêt qui n'a pas jugé son travail : l'extinction, une borne
    atteinte, `CAUSES_RELANCABLES`, #1179) ou **encore vivant** (verdict de
    `vitalite`, #348 — l'interrompre d'abord si c'est bien voulu), `422` si son
    brief n'a **jamais été approuvé** : le
    relancer reviendrait à repartir de son objectif brut en silence, c'est-à-dire
    à sauter la validation qu'il attendait encore. Le refus est motivé à la
    convention du reste (`{motif, message}`, §6.1).
    """
    executions = ctx.executions
    try:
        return await executions.relancer(run_id)
    except RelanceRefusee as refus:
        raise HTTPException(
            status_code=_CODE_REFUS_RELANCE.get(refus.motif, 422),
            detail=_detail_refus(refus),
        ) from refus



@router.post("/api/executions/{run_id}/plafond")
async def decider_plafond(
    ctx: Ctx,
    run_id: str, requete: DecisionPlafondRequete
) -> dict[str, Any]:
    """Tranche un run arrêté sur son plafond de dépense (#1182) : il reprend, ou il se solde.

    **Relever** pose le nouveau plafond et reprend les tâches mises de côté là
    où elles en étaient ; **réduire** fait de même en écartant les tâches
    désignées ; **arrêter** solde le run sur ce qui est fait. Même mécanique
    que la décision sur un brief (#320) : l'état est appliqué **d'abord** (le
    REST répond déjà à jour) puis l'événement est publié — le moteur, en
    attente sur ce même bus, reprend ou s'arrête, et la pompe réapplique
    l'événement sans effet.

    404 si le run est inconnu, **409 s'il n'attend pas de décision au
    plafond** (jamais tranché deux fois, jamais un run soldé ramené en vol),
    422 si la décision ne se tient pas : geste inconnu, reprise sans nouveau
    plafond, **nouveau plafond qui ne couvre même pas ce qui est déjà
    dépensé** (le run retomberait dessus à sa première mesure — c'est reposer
    la même question), réduction qui n'écarte rien ou qui écarte une tâche que
    la question ne nommait pas.
    """
    bus = ctx.bus
    executions = ctx.executions
    state = ctx.state
    resume = executions.resume(run_id)
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
        )
    detail = state.execution(run_id)
    en_vol = detail.plafond if detail is not None else None
    if resume["statut"] != EXECUTION_EN_ATTENTE_PLAFOND or en_vol is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cette exécution n'attend pas de décision au plafond de dépense "
                f"({resume['statut']}) : {run_id}."
            ),
        )
    demande = DemandePlafond.from_dict(en_vol)
    try:
        decision = DecisionPlafond(
            geste=requete.geste,
            plafond_cout_usd=requete.plafond_cout_usd,
            plafond_tokens=requete.plafond_tokens,
            ecartees=tuple(requete.ecartees),
            detail=_detail_decision_plafond(requete.geste),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    refus = _refus_plafond(decision, demande)
    if refus:
        raise HTTPException(status_code=422, detail=refus)
    event = evenement_decision(run_id, decision, projet_id=resume["projet_id"])
    state.appliquer(event)
    await bus.publish(event)
    return await executions.resume_vivant(run_id) or resume



@router.post("/api/executions/{run_id}/brief/decision")
async def decider_brief(
    ctx: Ctx,
    run_id: str, requete: DecisionBriefRequete
) -> dict[str, Any]:
    """Tranche le brief d'un run suspendu (#320, décision D5) : il repart, ou il s'arrête.

    **Approuver** relance la décomposition, et ce qui sera décomposé est le
    brief tel qu'il vient d'être approuvé — la version corrigée si le corps en
    porte une. **Refuser** solde le run en « annulée » : rien de payant n'aura
    été engagé au-delà du brief lui-même.

    Même mécanique que la décision de validation (#48) et pour les mêmes
    raisons : l'état est appliqué **d'abord** (le REST répond déjà à jour) puis
    l'événement est publié — le moteur, en attente sur ce même bus, reprend ou
    s'arrête, et la pompe réapplique l'événement sans effet (idempotence).

    404 si le run est inconnu, **409 s'il n'attend pas de décision** (jamais
    tranché deux fois, et surtout pas un run soldé ramené en vol), 422 si le
    brief corrigé n'est pas conforme au schéma partagé — un refus qui coûte un
    aller-retour à celui qui le soumet, plutôt qu'un run qui échoue plus tard.
    """
    bus = ctx.bus
    executions = ctx.executions
    state = ctx.state
    resume = executions.resume(run_id)
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
        )
    if resume["statut"] != EXECUTION_EN_ATTENTE_BRIEF:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cette exécution n'attend pas de décision sur son brief "
                f"({resume['statut']}) : {run_id}."
            ),
        )
    corrige = None
    if requete.approuve and requete.brief is not None:
        try:
            validate_brief(requete.brief, where="brief corrigé")
        except BriefValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        corrige = brief_depuis(requete.brief)
    event = Event(
        type=EVENEMENT_BRIEF_DECISION,
        run_id=run_id,
        # Le titre court à montrer, l'objectif entier à lire (#991) : la
        # projection tient déjà celui-ci, l'événement ne fait que le porter.
        titre=titre_court(resume["objectif"]),
        description=resume["objectif"],
        agent=ACTEUR_BRIEF,
        role=ROLE_BRIEF,
        statut=BRIEF_APPROUVE if requete.approuve else BRIEF_REFUSE,
        detail=(
            "brief approuvé depuis la Control Tower"
            if requete.approuve
            else "brief refusé depuis la Control Tower : rien n'a été décomposé"
        ),
        # La **correction**, ou None quand il n'y en a pas — jamais une recopie
        # du brief proposé. « Absent » veut dire « celui d'avant tient », et
        # cette lecture-là est faite au même endroit par la projection et par le
        # moteur (`DecisionBrief.retenu`), donc énoncée une seule fois.
        brief=corrige,
        projet_id=resume["projet_id"],
    )
    state.appliquer(event)
    await bus.publish(event)
    return await executions.resume_vivant(run_id) or resume



@router.post("/api/executions/{run_id}/brief/reponses")
async def repondre_brief(
    ctx: Ctx,
    run_id: str, requete: ReponsesBriefRequete
) -> dict[str, Any]:
    """Répond aux questions de clarification d'un brief (#321) : le run repart.

    Le run avait publié ses questions et s'était suspendu (`en_attente_reponses`,
    état non terminal — il est resté annulable tout du long). Les réponses le
    relancent : il **régénère son brief entier** en les intégrant, puis repose
    des questions s'il en reste et que le plafond le permet, sinon passe en
    validation.

    Même mécanique que la décision de brief (#320) et pour les mêmes raisons :
    l'état est appliqué **d'abord** (le REST répond déjà à jour) puis l'événement
    est publié — le moteur, en attente sur ce même bus, reprend, et la pompe
    réapplique l'événement sans effet (idempotence).

    404 si le run est inconnu, **409 s'il n'attend pas de réponses** (jamais
    répondu deux fois, jamais un run soldé ramené en vol), 422 si le nombre de
    réponses ne correspond pas aux questions du brief stocké — l'appariement est
    positionnel, donc une liste décalée affecterait des réponses aux mauvaises
    questions sans que rien ne le signale.
    """
    bus = ctx.bus
    executions = ctx.executions
    state = ctx.state
    resume = executions.resume(run_id)
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail=f"exécution inconnue : {run_id} (voir GET /api/executions).",
        )
    if resume["statut"] != EXECUTION_EN_ATTENTE_REPONSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cette exécution n'attend pas de réponses sur son brief "
                f"({resume['statut']}) : {run_id}."
            ),
        )
    detail = state.execution(run_id)
    attendues = len(detail.brief.questions) if detail and detail.brief else 0
    if len(requete.reponses) != attendues:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(requete.reponses)} réponse(s) pour {attendues} question(s) : "
                "les réponses s'apparient par position aux questions du brief en "
                "attente. Répondre « » (chaîne vide) laisse une question sans "
                "réponse ; elle partira en hypothèse."
            ),
        )
    event = Event(
        type=EVENEMENT_BRIEF_REPONSES,
        run_id=run_id,
        # Même partage qu'à la décision ci-dessus (#991).
        titre=titre_court(resume["objectif"]),
        description=resume["objectif"],
        agent=ACTEUR_BRIEF,
        role=ROLE_BRIEF,
        detail=(
            f"{sum(1 for r in requete.reponses if r.strip())}/{attendues} "
            "question(s) répondue(s) depuis la Control Tower"
        ),
        # **Pas** expurgées, et c'est le même choix assumé que pour le brief
        # (cf. `evenement_demande_brief`, #320) : ces réponses ne voyagent pas
        # pour être affichées, elles voyagent pour **atteindre le moteur**, qui
        # les intègre au brief régénéré. Les masquer ici ne protégerait rien —
        # le brief qui en sort circule déjà en clair sur le même bus — mais
        # corromprait l'entrée de la régénération, et un `[REDACTED]` au milieu
        # d'une réponse produirait un brief faux sans que personne le voie.
        reponses=list(requete.reponses),
        tour=resume.get("tour_clarification", 0),
        tours_max=resume.get("tours_clarification_max", 0),
        projet_id=resume["projet_id"],
    )
    state.appliquer(event)
    await bus.publish(event)
    return await executions.resume_vivant(run_id) or resume



@router.get("/api/executions/{run_id}")
async def execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """L'état d'une exécution (#185) : son résumé, sa trace et son coût.

    Le résumé de `GET /api/executions` (objectif, statut, volume, bornes,
    **vitalité** #348, **progression** #473) enrichi de ce qu'il ne porte
    pas : le grand livre du run (#57) et sa trace événement par événement.

    La `progression` est comptée **ici**, sur la machine à états du moteur
    (docs/03 §3) : à faire, en cours, bloquées, terminées, échecs — jamais
    recomptée par le front, qui ne verrait de toute façon que les tâches
    qu'il a chargées. Les tâches ainsi comptées sont exactement celles que
    rend `GET /api/taches?projet=…&run=<run_id>`.
    """
    executions = ctx.executions
    state = ctx.state
    detail = state.execution(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return {**detail.to_dict(), **(await executions.resume_vivant(run_id) or {})}



@router.get("/api/executions/{run_id}/cout")
async def cout_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Le grand livre d'une exécution (#57) : coût par tâche et agrégat du run.

    La comptabilité du lot #55 (forme `RunCost.to_dict`) reconstruite du
    flux d'événements : `planification` (l'usage de l'orchestrateur),
    `taches` (tokens entrée/sortie, coût estimé, durée par tâche) et
    `total` (l'agrégat de l'exécution) — sans la trace événement par
    événement du détail. 404 si aucune trace reçue pour ce `run_id`.
    """
    state = ctx.state
    detail = state.execution(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return detail.cout.to_dict()



@router.get("/api/executions/{run_id}/graphe")
async def graphe_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Le **graphe** d'une exécution (#490) : nœuds, arêtes, branches parallèles.

    La troisième lecture d'un run, à côté du Kanban (« combien dans quel
    état ») et de la progression (« où en est-on ») : celle qui dit **quoi
    après quoi**. Un nœud par tâche du plan — agent, statut, checklist, coût,
    durée —, une arête par dépendance, et les nœuds rangés par `niveaux` :
    deux tâches sans dépendance entre elles y tombent au **même** niveau,
    donc se lisent comme parallèles au lieu de paraître séquentielles.

    Rien n'est à recalculer côté client : `niveau`/`rang` par nœud,
    `compartiment` (la table partagée de la progression, #473),
    `profondeur`, `largeur` et `plat` sont servis.

    Un plan **sans aucune dépendance** rend un graphe plat et le dit
    (`plat: true`, tous les nœuds au niveau 0) : c'est le cas courant, pas un
    graphe vide. `plan_connu: false` marque le cas qu'on ne peut pas
    deviner — un run qui n'a jamais publié son plan, dont les nœuds sont
    alors reconstruits de ses seules tâches vues, sans arête.

    La mise à jour **en direct** passe par le flux existant, sans second
    canal : le graphe se recompose à chaque lecture depuis la projection, et
    ce sont donc `run.plan` (le plan est connu), `tache.statut` (un nœud
    démarre, une arête s'allume), `tache.detail` (une étape se coche, #489)
    et `agent.activite` (le nœud en cours **vit**, #836) qui le font bouger.

    Le nœud `en_cours` porte un **signe de vie** (`activite` : horodatage et
    libellé court du dernier geste de son agent), `null` sur tout nœud qui
    ne travaille pas : entre deux gestes d'agent, deux lectures rendent
    deux valeurs — c'est ce qui manquait à un graphe immobile pendant toute
    la durée d'une tâche. 404 si aucune trace reçue pour ce `run_id`.
    """
    state = ctx.state
    if state.execution(run_id) is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return state.graphe(run_id).to_dict()



@router.get("/api/executions/{run_id}/frise")
async def frise_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """La **frise d'activité** d'une exécution (#355) : ce que les agents font et se disent.

    La quatrième lecture d'un run, à côté du Kanban (« combien dans quel
    état »), de la progression (« où en est-on ») et du graphe (« quoi après
    quoi ») : celle qui dit **dans quel ordre**. Les changements de statut de
    tâche et les messages inter-agents sur une même frise, triés dans le
    temps, chaque entrée portant son agent, son horodatage et son objet.

    Les entrées sont rangées en **couloirs** — un par agent que le run a
    employé, y compris muet —, et un couloir de **repli** recueille ce qui n'a
    pas d'agent résoluble : une tâche bloquée n'a jamais été routée, donc le
    moteur y consigne un tiret (`_consigne_blocage`), qui n'est pas un nom
    d'agent. Chaque entrée porte son `couloir`, et ce couloir est toujours
    dans la liste servie : aucune entrée ne se perd faute de file.

    Une tâche **bloquée**, une tâche **en attente de validation humaine** et
    une tâche **en cours** portent trois `statut` distincts — c'est le cas
    d'usage qui a motivé le ticket : une attente de décision y cesse d'être
    indiscernable d'un travail en cours. `en_attente_validation` est résolu
    depuis `validation.demande`, qui **est** l'instant où la tâche s'arrête
    sur un humain ; la file `GET /api/validations` en dit l'état courant, pas
    la seconde.

    Rien n'est créé : les trois flux fusionnés sont déjà persistés et déjà
    servis par `GET /api/journal?run_id=…`, dont chaque entrée garde ici son
    identifiant. Comme le graphe, la frise **n'a pas d'événement à elle** :
    elle se recompose à la lecture, donc la mise à jour en direct passe par
    le flux existant, sans second canal.

    Chaque couloir dont une tâche **travaille** porte un **signe de vie**
    (#836) — `activite` : horodatage et libellé court du dernier geste de
    son agent, le même que celui du nœud en cours du graphe —, `null` sur
    un couloir arrêté. C'est un attribut de l'en-tête et jamais une entrée :
    `agent.activite` reste hors de `entrees`, le tri de la frise est intact.

    Bornée à `PLAFOND_FRISE` entrées, les plus **récentes** : `total` et
    `tronquee` disent ce qui a été laissé de côté — une borne muette ferait
    passer un run d'une heure pour un run de cinq cents lignes. 404 si aucune
    trace reçue pour ce `run_id`.
    """
    journal = ctx.journal
    state = ctx.state
    if state.execution(run_id) is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return frise_du_run(
        run_id,
        journal.entrees_du_run(run_id),
        agents=state.agents_du_run(run_id),
        activites=state.signes_de_vie_du_run(run_id),
    ).to_dict()



@router.get("/api/executions/{run_id}/decisions")
async def decisions_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Ce que les agents de ce run ont tranché **seuls** (#1026, docs/05 §6.18).

    La cinquième lecture d'un run, à côté du Kanban (« combien dans quel
    état »), de la progression (« où en est-on »), du graphe (« quoi après
    quoi ») et de la frise (« dans quel ordre ») : celle qui dit **ce qui a
    été décidé sans moi, et pourquoi**. C'est la condition que le parent
    #1019 pose à l'autonomie — elle n'est acceptable que si elle se vérifie
    après coup.

    Deux familles, séparées par `origine` et jamais mêlées : une décision que
    l'agent a jugée sienne (`tranchee`, #1024 — il n'avait à demander à
    personne) et une **hypothèse prise faute de réponse** (`hypothese`,
    #1023 — il a demandé, personne n'a répondu avant la borne, il est reparti
    sur ce qu'il avait annoncé). `decision` et `raison` sortent des deux
    champs que le moteur sépare à l'écriture : cette route ne redécoupe rien.

    Rien n'est créé : les deux flux sont déjà persistés et déjà servis par
    `GET /api/journal?run_id=…`, dont chaque entrée garde ici son identifiant.
    Comme le graphe et la frise, cette liste **n'a pas d'événement à elle** —
    elle se recompose à la lecture, donc la mise à jour en direct passe par le
    flux existant, sans second canal.

    Rendue **du plus récent au plus ancien**, comme le journal du run et non
    comme la frise : les deux lectures chronologiques de la bascule vont dans
    le même sens. Bornée à `PLAFOND_DECISIONS` entrées, les plus récentes :
    `total`, `hypotheses` et `tronquee` comptent **avant** la borne et disent
    ce qui a été laissé de côté. 404 si aucune trace reçue pour ce `run_id`.
    """
    journal = ctx.journal
    state = ctx.state
    if state.execution(run_id) is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    return decisions_du_run(
        run_id,
        journal.entrees_du_run(run_id),
        taches=state.titres_du_run(run_id),
    ).to_dict()



@router.get("/api/executions/{run_id}/bilan")
async def bilan_execution(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Le **bilan sur pièces** d'un run terminé (#1284, docs/05 §6.23).

    Rendu à la fin du run par un appel au modèle, vérifié contre les pièces du
    journal du run, puis publié — donc gardé au journal durable, et relu d'ici
    après un redémarrage. Chaque constat cite ses pièces (`P7`), et chaque pièce
    les entrées du journal dont elle vient (`j-0042`, celles de
    `GET /api/journal`) ; les constats sans pièce sont rendus à part, avec la
    raison de leur écart.

    `bilan` vaut `null` tant qu'il n'y en a pas : un run en vol, un modèle qui
    n'a pas répondu, un run soldé avant ce lot. Ce n'est pas une erreur — le
    run existe, son bilan pas encore. 404 si aucune trace reçue pour ce `run_id`.
    """
    bilans = ctx.bilans
    state = ctx.state
    execution = state.execution(run_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"exécution inconnue : {run_id}")
    bilan = bilans.bilan(run_id)
    # Ce que la vue du run dit quand `bilan` est nul (#1285) : `attendu` (run en
    # vol), `en_redaction` (l'appel est parti), `absent` — avec sa `raison`
    # quand on la sait (`modele_muet`, `reponse_illisible`) ; `rendu` sinon.
    etat, raison = bilans.etat(run_id)
    return {
        "run_id": run_id,
        "bilan": bilan.to_dict() if bilan is not None else None,
        "etat": etat,
        "raison": raison,
        # Le titre de chaque tâche du run (#1285) : un constat nomme sa tâche
        # par son identifiant, la vue la nomme par son titre — servi ici avec
        # le bilan, pour ne pas dépendre du moment où la liste des tâches arrive,
        # ni d'une panne qui l'aurait vidée.
        "taches": state.titres_du_run(run_id),
        # Ce que le bilan a coûté (#1285), compté au run après sa fin : la tête
        # du run l'inclut, la vue du bilan le dit — sans quoi le coût « jusqu'à
        # sa fin » que cite le bilan contredisait la tête. `null` : inconnu.
        "cout_bilan": execution.cout.bilan.cout_usd,
    }
