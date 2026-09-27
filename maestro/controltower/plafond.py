"""Arbitre du plafond de dépense adossé à la Control Tower (#1182).

Relie le canal du moteur (`maestro.engine.plafond`) à la Control Tower : quand un
run atteint son plafond de dépense, `ArbitrePlafondControlTower` publie la
question sur le bus (`plafond.demande` — la projection passe le run « en attente
du plafond » et en garde les faits, le fil la pose sur sa carte) puis **attend la
décision** (`plafond.decision`, publiée par `POST /api/executions/{run_id}/plafond`).

C'est le **cinquième pendant** de `ValidateurControlTower` (#48),
d'`ArbitreBriefControlTower` (#320), d'`ArbitreQuestionControlTower` (#1023) et
d'`ArbitreRenfortControlTower` (#1227) : même bus, même patron d'attente
(s'abonner **avant** de publier, pour qu'une décision instantanée ne tombe pas dans
le vide). Ce qui le distingue, et décide de son code :

- **aucune borne.** Sans réponse, le run attend — comme une validation (#571), et
  pour la raison inverse d'un renfort : l'issue par défaut d'un renfort (« on
  continue avec l'équipe qu'on a ») ne coûte rien de plus, celle d'un plafond
  dépenserait ce que la personne n'a pas accordé, ou jetterait ce qu'elle a payé.
  Aucune des deux ne se décide à sa place. Le run reste annulable pendant ce temps ;
- **aucun relais dans le fil.** La question n'est pas une phrase écrite dans la
  conversation, c'est une **carte** au pied du fil, tirée de la projection — comme
  la question d'un agent (#1025). Les chiffres qu'elle montre sont ceux que le
  moteur a mesurés, jamais une rédaction ;
- **la décision est typée.** Relever porte un montant, réduire des tâches : ce
  n'est ni un booléen (validation) ni du texte libre (question), et l'endpoint la
  valide contre la demande en vol avant de la publier.

Fail-safe hérité : si le bus se referme sans décision, l'attente **lève** — la
boucle solde alors le run comme avant ce lot, en disant que la question n'a pas pu
être posée. Elle n'invente jamais une réponse.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_PLAFOND_DECISION,
    EVENEMENT_PLAFOND_DEMANDE,
    ROLE_RUN,
    Event,
    EventBus,
)
from maestro.engine.plafond import DecisionPlafond, DemandePlafond
from maestro.telemetry import redact_secrets

#: Le statut que porte la demande : la question est en vol.
PLAFOND_EN_ATTENTE = "en_attente"


def phrase_de_la_demande(demande: DemandePlafond) -> str:
    """La ligne que le journal durable et la frise gardent de la demande — le constat."""
    reste = len(demande.restantes)
    return (
        f"plafond de dépense atteint — {reste} tâche(s) restent à faire ; le run "
        "attend une décision : relever, réduire ou arrêter"
    )


def evenement_demande(demande: DemandePlafond) -> Event:
    """La demande au plafond d'un run, telle qu'elle traverse le bus.

    `plafond` porte les faits (`DemandePlafond.to_dict`) ; l'objectif y est
    expurgé des secrets, comme partout où un texte humain part au journal durable.
    Les titres de tâches, eux, sont ceux que le journal consigne déjà en clair.
    """
    faits = demande.to_dict()
    faits["objectif"] = redact_secrets(demande.objectif)
    return Event(
        type=EVENEMENT_PLAFOND_DEMANDE,
        run_id=demande.run_id,
        projet_id=demande.projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre="Plafond de dépense atteint",
        statut=PLAFOND_EN_ATTENTE,
        detail=phrase_de_la_demande(demande),
        plafond=faits,
    )


def evenement_decision(
    run_id: str, decision: DecisionPlafond, *, projet_id: str | None = None
) -> Event:
    """La décision au plafond, telle que l'endpoint la publie — le geste en `statut`."""
    return Event(
        type=EVENEMENT_PLAFOND_DECISION,
        run_id=run_id,
        projet_id=projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre="Décision au plafond de dépense",
        statut=decision.geste,
        detail=redact_secrets(decision.detail),
        plafond=decision.to_dict(),
    )


class ArbitrePlafondControlTower:
    """Arbitre (#1182) du moteur : publie la question au plafond, attend la décision.

    Il vit **avec le moteur**, dans l'hôte en process comme dans l'hôte détaché —
    c'est pourquoi il ne connaît que le bus. S'abonne **avant** de publier : même
    une décision immédiate ne peut pas être manquée. Aucune borne (voir l'en-tête).
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandePlafond) -> DecisionPlafond:
        """Publie la demande, puis rend la décision humaine."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.run_id))
        # Laisse l'abonnement se poser avant de publier — même précaution qu'en
        # #48, #320, #1023 et #1227.
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_demande(demande))
            return await ecoute
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


async def _premiere_decision(flux: AsyncIterator[Event], run_id: str) -> DecisionPlafond:
    """Attend sur `flux` la décision visant `run_id` et la rend.

    Le filtre est le **run**, et il suffit : un run n'a qu'une question au plafond
    en vol. Une décision illisible (charge absente ou invalide — un producteur
    d'une autre version) est ignorée plutôt que prise pour un geste : l'attente
    continue, et le run reste annulable.

    Si le flux se tarit sans décision (bus refermé), **lève** : la boucle en fait
    un arrêt dit, jamais une réponse inventée.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_PLAFOND_DECISION or event.run_id != run_id:
                continue
            try:
                return DecisionPlafond.from_dict(event.plafond or {})
            except ValueError:
                continue
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans décision au plafond pour le run {run_id}"
    )
