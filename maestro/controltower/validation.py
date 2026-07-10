"""Validateur human-in-the-loop adossé à la Control Tower (ticket #48).

Relie le garde-fou de validation humaine du moteur (#9,
`maestro.engine.guardrails`) à l'UI de la Control Tower (#46/#47) : quand une
tâche est classée sensible, `ValidateurControlTower` publie la demande sur le
bus d'événements (`validation.demande` — l'API la projette, l'UI l'affiche
avec le contexte : agent, tâche, action demandée, justification) puis **attend
la décision humaine** (`validation.decision`, publiée par
`POST /api/validations/{tache_id}/decision`). La tâche reste en pause tant que
personne n'a tranché — pas de time-out silencieux : le time-out par tâche du
moteur ne court pas pendant cette attente (cf. `LocalExecutor._realise_gardee`).

Le contrat est celui du `Validateur` des garde-fous : approbation → le moteur
reprend la tâche ; refus → il l'annule proprement avant toute exécution ; dans
les deux cas la décision est consignée au journal (#8, étape
`<tâche>:validation`). Fail-safe hérité : si le bus est en panne (Redis
injoignable…), l'exception remonte aux garde-fous qui **refusent** la demande —
jamais d'action sensible sans accord explicite.

Même bus que le reste de la Control Tower : `InMemoryEventBus` en test ou en
mono-process, `RedisEventBus` en production (`validateur_redis`, pendant
moteur du `publieur_redis` du pont télémétrie).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from maestro.controltower.events import (
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    EventBus,
    RedisEventBus,
)
from maestro.controltower.state import VALIDATION_APPROUVEE, VALIDATION_EN_ATTENTE
from maestro.engine.guardrails import DemandeValidation
from maestro.telemetry import redact_secrets


def evenement_demande(demande: DemandeValidation) -> Event:
    """Mue une `DemandeValidation` du moteur en événement `validation.demande`.

    Porte tout ce que l'UI montre pour trancher : la tâche (id, titre), l'agent
    qui l'exécuterait, la `raison` de la classification (dans `detail`) et
    l'action demandée (`description`). Expurgé des secrets avant publication —
    même filet que le journal (#8) : ce qui part sur le bus est montrable.
    """
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=demande.task_id,
        titre=redact_secrets(demande.titre),
        agent=demande.agent,
        role=demande.role,
        statut=VALIDATION_EN_ATTENTE,
        detail=redact_secrets(demande.raison),
        description=redact_secrets(demande.description),
    )


class ValidateurControlTower:
    """Validateur (#9) qui soumet la demande à l'UI et attend la décision humaine.

    S'abonne au bus **avant** de publier la demande : même une décision
    immédiate ne peut pas être manquée. L'attente est indéfinie — la pause
    d'une tâche sensible ne se résout que par une décision humaine (ou l'arrêt
    du run), jamais par un time-out silencieux.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandeValidation) -> bool:
        """Publie la demande puis rend la décision humaine (True = approuvée)."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.task_id))
        # Laisse l'abonnement se poser avant de publier (bus mémoire : un tour
        # de boucle suffit ; sur Redis, la décision humaine arrive de toute
        # façon bien après l'aller-retour du SUBSCRIBE).
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_demande(demande))
            return await ecoute
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


async def _premiere_decision(flux: AsyncIterator[Event], tache_id: str) -> bool:
    """Attend sur `flux` la décision visant `tache_id` ; True si approuvée.

    Ignore tout le reste du bus (statuts de tâches, autres validations…). Si le
    flux se tarit sans décision (bus refermé), lève — les garde-fous muent
    l'exception en refus (fail-safe), pas en fausse décision humaine.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_VALIDATION_DECISION:
                continue
            if event.tache_id != tache_id:
                continue
            return event.statut == VALIDATION_APPROUVEE
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans décision pour la tâche {tache_id}"
    )


def validateur_redis(url: str | None = None) -> ValidateurControlTower:
    """Le validateur de production : demandes et décisions via Redis Pub/Sub.

    Pendant moteur du `publieur_redis` du pont télémétrie : même instance Redis
    (celle du docker-compose par défaut), même canal `maestro.evenements` que
    consomme l'API (`maestro-api`). La connexion est paresseuse (ouverte à la
    première demande).
    """
    return ValidateurControlTower(RedisEventBus(url))
