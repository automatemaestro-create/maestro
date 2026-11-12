"""Les routes des arbitrages humains : validations des tâches sensibles, questions des agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.reglements import (
    MOTIF_ATTENTE_INCONNUE,
    MOTIF_ATTENTE_REGLEE,
    MOTIF_ETENDUE_REFUSEE,
    MOTIF_REPONSE_VIDE,
    ReglementRefuse,
)
from maestro.controltower.routes.communs import _portee
from maestro.controltower.routes.contexte import Ctx
from maestro.controltower.routes.requetes import DecisionRequete, ReponseQuestionRequete

router = APIRouter()


#: Le statut HTTP des refus d'une réponse à une question et d'une décision sur une
#: validation (#1183). Les règles vivent dans le service des attentes
#: (`ServiceAttentes`), et les routes n'en gardent que la traduction : `404` sur une
#: attente inconnue, `409` sur une attente déjà réglée, `422` sur une réponse vide.
_CODE_REFUS_REGLEMENT: dict[str, int] = {
    MOTIF_ATTENTE_INCONNUE: 404,
    MOTIF_ATTENTE_REGLEE: 409,
    MOTIF_REPONSE_VIDE: 422,
    MOTIF_ETENDUE_REFUSEE: 422,
}



@router.get("/api/validations")
async def validations(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """Les demandes de validation humaine (#48) : contexte, statut, décision.

    `projet` est **obligatoire** (#277), au contrat commun
    (`<id>` | `tous` | `aucun`) : une validation appartient au projet de sa
    tâche, et une Control Tower cadrée sur un projet n'a pas à faire
    arbitrer une action qui se déroule ailleurs.
    """
    state = ctx.state
    return [v.to_dict() for v in state.validations(_portee(ctx, projet))]



@router.post("/api/validations/{tache_id}/decision")
async def decider(ctx: Ctx, tache_id: str, requete: DecisionRequete) -> dict[str, Any]:
    """Tranche une demande de validation (#48) : la décision part vers le moteur.

    Applique la décision à l'état (le REST répond déjà à jour) puis la
    publie sur le bus — le moteur, en attente sur ce même bus, reprend la
    tâche (approbation) ou l'annule proprement (refus), et la pompe
    réapplique l'événement sans effet (idempotence). 404 si aucune demande
    pour cette tâche, 409 si elle est déjà tranchée (jamais deux décisions).

    Le `motif` d'un refus (#272) voyage dans le `detail` de l'événement : c'est
    le champ que la projection recopie dans `decision`, donc celui que l'UI
    relit et que le journal durable conserve. Il voyage **aussi** dans `motif`
    (#1185), le fait que le moteur rend à l'agent comme consigne — lire la
    consigne dans la phrase de `detail` serait juger du texte par un motif.

    `etendue` (#1185) étend une approbation à l'outil pour la suite du run ou du
    projet : l'accord s'écrit dans les permissions de l'agent avant que la
    décision parte. `422` si la demande ne le permet pas (pas d'acte, pas de run,
    pas de projet, étendue inconnue).

    Les règles vivent dans le service des attentes (#1183,
    `ServiceAttentes.trancher`), que le fil de l'orchestrateur appelle aussi :
    la route n'en garde que la traduction en statut HTTP.
    """
    attentes = ctx.attentes
    try:
        demande = await attentes.trancher(
            tache_id,
            approuve=requete.approuve,
            motif=requete.motif,
            etendue=requete.etendue,
        )
    except ReglementRefuse as refus:
        raise HTTPException(
            status_code=_CODE_REFUS_REGLEMENT.get(refus.motif, 409), detail=str(refus)
        ) from refus
    return demande.to_dict()



@router.get("/api/questions")
async def questions(ctx: Ctx, projet: str | None = None) -> list[dict[str, Any]]:
    """Les questions posées par les agents (#1023) : contexte, hypothèse, réponse.

    `projet` est **obligatoire** (#277), au contrat commun
    (`<id>` | `tous` | `aucun`) : une question appartient au projet de la
    tâche qui la pose, et une Control Tower cadrée sur un projet n'a pas à
    faire répondre pour un travail qui se déroule ailleurs.

    Une question **reste servie tant que personne n'y a répondu**, y compris
    après que l'agent a repris sur son hypothèse : une réponse tardive sert
    encore (`MemoireArbitrage`, #584 — le même appel rejoué la retrouvera).
    Ce que l'agent a fait entre-temps se lit au journal du run, étape
    `<tache>:question`.
    """
    state = ctx.state
    return [q.to_dict() for q in state.questions(_portee(ctx, projet))]



@router.post("/api/questions/{question_id}/reponse")
async def repondre_question(
    ctx: Ctx,
    question_id: str, requete: ReponseQuestionRequete
) -> dict[str, Any]:
    """Répond à la question d'un agent (#1023) : la réponse part vers le moteur.

    Même patron que `POST /api/validations/{tache_id}/decision` : la réponse
    est appliquée à l'état (le REST répond déjà à jour) puis publiée sur le
    bus — l'agent, suspendu sur ce même bus, la reçoit et reprend. La pompe
    réapplique l'événement sans effet (idempotence).

    `404` si aucune question ne porte cet identifiant ; `409` si elle a déjà
    reçu une réponse — jamais deux fois répondu, l'agent n'ayant lu que la
    première. `422` si la réponse est vide : ce serait dire à l'agent qu'on
    lui a répondu sans rien lui apprendre, c'est-à-dire pire que le silence.

    ⚠ **Répondre n'approuve rien.** Le texte écrit ici n'autorise aucun acte :
    un outil classé `ask` reste refusé sans canal d'arbitrage, et la file des
    validations est le seul endroit où un acte se tranche (EF-08, docs/32 §5).

    Une réponse **tardive** est acceptée, et ce n'est pas une tolérance : la
    question est restée posée précisément parce qu'elle sert encore. L'agent
    a peut-être déjà repris sur son hypothèse — le journal du run le dit —, et
    la réponse le rattrapera au prochain appel identique.

    La réponse voyage dans `detail`, et nulle part ailleurs : c'est le champ que
    la projection recopie et que le journal durable conserve. Les règles vivent
    dans le service des attentes (#1183, `ServiceAttentes.repondre`), que le fil
    de l'orchestrateur appelle aussi : la route n'en garde que la traduction en
    statut HTTP.
    """
    attentes = ctx.attentes
    try:
        question = await attentes.repondre(question_id, requete.reponse)
    except ReglementRefuse as refus:
        raise HTTPException(
            status_code=_CODE_REFUS_REGLEMENT.get(refus.motif, 409), detail=str(refus)
        ) from refus
    return question.to_dict()
