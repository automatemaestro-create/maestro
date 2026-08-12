"""Arbitre du brief adossé à la Control Tower (#320, décision D5).

Relie l'arrêt sur brief du moteur (`maestro.engine.brief`) à l'UI de la Control
Tower : quand un run tourne en mode « humain », `ArbitreBriefControlTower` publie
le brief proposé sur le bus d'événements (`brief.demande` — l'API le projette, le
run passe en `en_attente_brief`, l'écran de validation #322 l'affiche avec
l'objectif d'origine) puis **attend la décision humaine** (`brief.decision`,
publiée par `POST /api/executions/{run_id}/brief/decision`). Le run reste suspendu
tant que personne n'a tranché — pas de time-out silencieux, et **aucune tâche
créée** d'ici là.

C'est le **pendant exact de `ValidateurControlTower`** (#48), au même patron
d'attente et sur le même bus, mais sur un **canal distinct** et pour une autre
question : là-bas « exécute-t-on cette action sensible ? », dont la réponse est un
booléen ; ici « décompose-t-on ce brief, et sous quelle forme ? », dont la réponse
transporte un **brief corrigé**. Détourner le validateur pour ça aurait demandé de
faire voyager un brief dans un canal fait pour un oui/non — c'est la note technique
du ticket, et c'est pour cela que les deux mécanismes se ressemblent sans se
confondre.

Fail-safe hérité, dans le bon sens : si le bus se referme sans décision, l'attente
**lève** au lieu de rendre une approbation par défaut. Le run échoue alors sans
avoir rien décomposé — la moitié gratuite du run (le brief) protège la moitié
payante (les tâches).

Même bus que le reste de la Control Tower : `InMemoryEventBus` en test ou en
mono-process, `RedisEventBus` en production (`arbitre_brief_redis`, pendant exact
de `validateur_redis`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_DEMANDE,
    Event,
    EventBus,
    RedisEventBus,
)
from maestro.controltower.state import BRIEF_APPROUVE, EXECUTION_EN_ATTENTE_BRIEF
from maestro.engine.brief import MODE_BRIEF_HUMAIN, DecisionBrief, DemandeBrief
from maestro.telemetry import redact_secrets

#: L'acteur au nom duquel le cadrage est consigné — celui de l'étape de brief du
#: journal (#318) et de la planification (#8) : c'est le Chef de projet qui rédige.
#: Public parce que la **décision** est émise ailleurs (la route de l'API) et doit
#: porter le même acteur que la demande : deux orthographes rendraient la trace d'un
#: même cadrage illisible.
ACTEUR_BRIEF = "orchestrateur"
ROLE_BRIEF = "Orchestrateur"


def evenement_demande_brief(demande: DemandeBrief) -> Event:
    """Mue une `DemandeBrief` du moteur en événement `brief.demande`.

    Porte ce qu'il faut pour trancher : le run visé, l'**objectif d'origine** (dans
    `titre`) et le brief proposé. L'objectif est expurgé des secrets avant de
    partir, comme partout ailleurs sur le bus (#115).

    Le **brief, lui, ne l'est pas**, et c'est un choix assumé plutôt qu'un oubli.
    Deux raisons, dans cet ordre : c'est le texte que l'humain doit lire
    **exactement** pour l'approuver — approuver une version masquée d'un texte qui
    s'exécutera dans sa version d'origine viderait l'approbation de son sens ; et
    c'est le même objet qui **revient** par `brief.decision` pour devenir l'entrée
    de la décomposition, si bien qu'un masquage à l'aller corromprait ce qui est
    décomposé au retour. Le secret qu'un opérateur colle dans un objectif est déjà
    couvert là où il entre (l'objectif du lancement voyage expurgé) ; l'ajouter ici
    coûterait la fidélité de l'unique chose que ce lot sert à faire relire.
    """
    return Event(
        type=EVENEMENT_BRIEF_DEMANDE,
        run_id=demande.run_id,
        titre=redact_secrets(demande.objectif),
        agent=ACTEUR_BRIEF,
        role=ROLE_BRIEF,
        statut=EXECUTION_EN_ATTENTE_BRIEF,
        detail="brief soumis à validation humaine avant décomposition",
        brief=demande.brief,
        mode_brief=MODE_BRIEF_HUMAIN,
    )


class ArbitreBriefControlTower:
    """Arbitre (#320) qui soumet le brief à l'UI et attend la décision humaine.

    S'abonne au bus **avant** de publier la demande : même une décision immédiate
    ne peut pas être manquée. L'attente est indéfinie — un run suspendu sur son
    brief ne se résout que par une décision humaine (ou l'arrêt du run), jamais par
    un time-out silencieux.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandeBrief) -> DecisionBrief:
        """Publie le brief puis rend la décision humaine sur ce run."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.run_id))
        # Laisse l'abonnement se poser avant de publier (bus mémoire : un tour de
        # boucle suffit ; sur Redis, la décision humaine arrive de toute façon bien
        # après l'aller-retour du SUBSCRIBE). Même précaution qu'en #48.
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_demande_brief(demande))
            return await ecoute
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


async def _premiere_decision(flux: AsyncIterator[Event], run_id: str) -> DecisionBrief:
    """Attend sur `flux` la décision visant `run_id` et la rend telle quelle.

    Ignore tout le reste du bus (statuts de tâches, validations d'actions
    sensibles, décisions visant un autre run). Le brief porté par l'événement est
    le **brief retenu** — corrigé par l'humain, ou celui qui a été proposé : c'est
    l'API qui tranche cette question au moment de publier, pour que le moteur n'ait
    pas à rejouer la règle de son côté.

    Si le flux se tarit sans décision (bus refermé), **lève** : le moteur remonte
    l'erreur en échec de run, sans avoir décomposé. Jamais de fausse approbation.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_BRIEF_DECISION:
                continue
            if event.run_id != run_id:
                continue
            return DecisionBrief(
                approuve=event.statut == BRIEF_APPROUVE,
                brief=event.brief,
                detail=event.detail,
            )
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans décision sur le brief du run {run_id}"
    )


def arbitre_brief_redis(url: str | None = None) -> ArbitreBriefControlTower:
    """L'arbitre de production : demandes et décisions via Redis Pub/Sub.

    Pendant exact de `validateur_redis` (#48) : même instance Redis (celle du
    docker-compose par défaut), même canal `maestro.evenements` que consomme l'API
    (`maestro-api`). La connexion est paresseuse (ouverte à la première demande).
    C'est ce qui permet à un run **hors API** (`maestro-run --publier --brief
    humain`) d'être arbitré depuis la Control Tower comme un run qu'elle a lancé.
    """
    return ArbitreBriefControlTower(RedisEventBus(url))
