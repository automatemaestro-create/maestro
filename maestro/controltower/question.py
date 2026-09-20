"""Arbitre des questions d'agent adossé à la Control Tower (#1023).

Relie le canal de question du moteur (`maestro.engine.questions`) à la Control
Tower : quand un agent appelle `poser_une_question` pendant sa tâche,
`ArbitreQuestionControlTower` publie la question sur le bus d'événements
(`question.demande` — l'API la projette, l'écran du lot #1025 l'affiche dans le
fil) puis **attend la réponse humaine** (`question.reponse`, publiée par
`POST /api/questions/{question_id}/reponse`).

C'est le **troisième pendant** de `ValidateurControlTower` (#48) et
d'`ArbitreBriefControlTower` (#320) : même bus, même patron d'attente
(s'abonner **avant** de publier, pour qu'une réponse instantanée ne tombe pas
dans le vide), et un **canal distinct** pour une autre question. Là-bas
« exécute-t-on cette action sensible ? », dont la réponse est un booléen ; chez
le brief « décompose-t-on ce brief, et sous quelle forme ? » ; ici « voici ce que
je ne sais pas — que veux-tu ? », dont la réponse est du texte libre, **pendant**
une tâche. Détourner le validateur pour ça aurait demandé de faire voyager du
texte dans un canal fait pour un oui/non (docs/32 §5.2).

## Ce qui le distingue de ses deux aînés, et qui décide de son code

**Il n'a pas de borne à lui.** L'attente est indéfinie ici, comme chez les deux
autres ; c'est l'**exécuteur** qui renonce à `BornesArbitrage.attente_s` et fait
reprendre l'agent sur son hypothèse. La raison est le troisième critère du
ticket : la reprise doit être **écrite au journal**, et le journal est chez
l'exécuteur. Un arbitre qui bornerait ferait reprendre l'agent sans trace.

**Et il ne suspend pas le run.** `_suspend_sur_arbitrage` (#571) existe parce
qu'une tâche arrêtée sur une validation l'est *indéfiniment* — sans décision, elle
ne repart jamais. Une question n'a pas cette propriété : elle a une **issue par
défaut**, annoncée par l'agent lui-même, et il reprend son travail à la borne.
Marquer le run « en attente » le laisserait bloqué à l'écran quelques minutes
après que plus personne n'attend — c'est-à-dire refaire, en plus petit, la
promesse fausse que #571 a supprimée.

**Une question reste donc ouverte jusqu'à ce qu'on y réponde**, y compris après
que l'agent a repris. Ce n'est pas un oubli de fermeture : une réponse tardive
sert encore (`MemoireArbitrage`, #584) — le même appel rejoué la retrouve sans
nouvelle attente. Ce que l'agent a fait entre-temps, lui, est dit **au journal**,
étape `<tache>:question`.

Fail-safe hérité, dans le bon sens : si le bus se referme sans réponse, l'attente
**lève** au lieu de rendre une réponse par défaut — et l'exécuteur la traduit en
reprise sur hypothèse, jamais en échec de tâche. Une question sans réponse n'a
jamais été un motif de condamner un travail en cours.

Même bus que le reste de la Control Tower : `InMemoryEventBus` en test ou en
mono-process, `bus_durable` en production (`arbitre_question_redis`, pendant exact
de `validateur_redis` et d'`arbitre_brief_redis`) — le bus Redis qui **consigne en
publiant** (#699), faute de quoi une question posée pendant une coupure de l'API
n'atteindrait personne et ne laisserait aucune trace.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from maestro.controltower.events import (
    EVENEMENT_QUESTION_DEMANDE,
    EVENEMENT_QUESTION_REPONSE,
    Event,
    EventBus,
)
from maestro.controltower.persistence import bus_durable
from maestro.controltower.state import QUESTION_EN_ATTENTE, QUESTION_REPONDUE
from maestro.engine.questions import DemandeQuestion
from maestro.telemetry import redact_secrets


def evenement_question(demande: DemandeQuestion) -> Event:
    """Mue une `DemandeQuestion` du moteur en événement `question.demande`.

    Porte tout ce qu'il faut pour répondre : qui demande (l'agent, son rôle), à
    propos de quoi (la tâche, son titre), la **question** (`description`), les
    **choix** s'il y en a, et l'**hypothèse** — ce que l'agent fera sans réponse.

    L'hypothèse voyage dans un champ à elle *et* dans `detail`, et ce n'est pas un
    doublon : le champ est la donnée (servie telle quelle à l'agent à la borne),
    `detail` est la phrase qu'un fil affiche sans rien composer. C'est le partage
    que `validation.demande` fait déjà entre `raison` et `description`.

    **La borne voyage deux fois pour la même raison** (#1025) : en toutes lettres
    dans `detail`, et en **date** dans `echeance` — le fait avec lequel l'écran
    compare son horloge pour dire si l'agent a déjà repris. Sans elle, il lui
    faudrait lire le chiffre dans la phrase (juger du texte par un motif, ce que
    le dépôt refuse) ou recopier `BornesArbitrage.attente_s` côté navigateur (deux
    supports pour un même réglage).

    Tout ce qui vient de l'agent est **expurgé des secrets** avant de partir —
    question, choix, hypothèse : c'est du texte qu'un modèle a composé, au même
    titre que la `raison` d'un arbitrage, et une question peut très bien citer une
    ligne de commande. `question_id` et les identifiants, eux, ne le sont pas : ce
    sont des identifiants que **nous** produisons.
    """
    hypothese = redact_secrets(demande.hypothese)
    naissance = datetime.now(UTC)
    return Event(
        type=EVENEMENT_QUESTION_DEMANDE,
        run_id=demande.run_id,
        tache_id=demande.tache_id,
        titre=redact_secrets(demande.titre),
        agent=demande.agent,
        role=demande.role,
        statut=QUESTION_EN_ATTENTE,
        description=redact_secrets(demande.question),
        detail=(
            f"sans réponse d'ici {demande.attente_s:g} s, l'agent reprendra sur "
            f"son hypothèse : {hypothese}"
        ),
        projet_id=demande.projet_id,
        question_id=demande.question_id,
        hypothese=hypothese,
        choix=[redact_secrets(choix) for choix in demande.choix],
        # La borne, en **date** (#1025) — le même chiffre que `detail` dit en
        # toutes lettres, sous la forme avec laquelle un écran compare son
        # horloge. Elle est posée ici, à la publication, et non côté écran : la
        # borne est un réglage du moteur (`BornesArbitrage.attente_s`), et la
        # recopier dans le navigateur ferait deux supports pour un même réglage.
        # `horodatage` reste l'instant de la demande : les deux ne se déduisent
        # pas l'un de l'autre sans connaître ce réglage.
        echeance=(naissance + timedelta(seconds=demande.attente_s)).isoformat(
            timespec="seconds"
        ),
        horodatage=naissance.isoformat(timespec="seconds"),
    )


class ArbitreQuestionControlTower:
    """Arbitre (#1023) qui porte la question d'un agent à l'UI et attend la réponse.

    S'abonne au bus **avant** de publier la question : même une réponse immédiate
    ne peut pas être manquée. L'attente est indéfinie **ici** — c'est l'exécuteur
    qui borne (voir la docstring du module), et c'est ce qui permet à une réponse
    tardive d'arriver encore à quelqu'un.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandeQuestion) -> str:
        """Publie la question puis rend la réponse humaine, telle qu'elle a été écrite."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_reponse(flux, demande.question_id))
        # Laisse l'abonnement se poser avant de publier (bus mémoire : un tour de
        # boucle suffit ; sur Redis, la réponse humaine arrive de toute façon bien
        # après l'aller-retour du SUBSCRIBE). Même précaution qu'en #48 et #320.
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_question(demande))
            return await ecoute
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


async def _premiere_reponse(flux: AsyncIterator[Event], question_id: str) -> str:
    """Attend sur `flux` la réponse visant `question_id` et la rend telle quelle.

    Ignore tout le reste du bus (statuts de tâches, validations, réponses visant
    une autre question). Le filtre est l'**identifiant de la question** et non la
    tâche : une tâche en pose plusieurs, et une question laissée sans réponse
    reste en vol pendant que l'agent reprend — filtrer par `tache_id` ferait rendre
    à la première la réponse écrite pour la seconde.

    Le statut est vérifié plutôt que supposé : seule une réponse **répondue** rend
    la main. Aucun producteur n'en émet d'autre aujourd'hui — c'est la garde d'un
    canal dont le journal durable est rejoué, et où un événement venu d'une
    version ultérieure ne doit pas se faire passer pour une réponse humaine.

    Si le flux se tarit sans réponse (bus refermé), **lève** : l'exécuteur en fait
    une reprise sur hypothèse, consignée, jamais un échec de tâche.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_QUESTION_REPONSE:
                continue
            if event.question_id != question_id:
                continue
            if event.statut != QUESTION_REPONDUE:
                continue
            return event.detail
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans réponse à la question {question_id}"
    )


def arbitre_question_redis(url: str | None = None) -> ArbitreQuestionControlTower:
    """L'arbitre de production : questions et réponses via Redis Pub/Sub.

    Pendant exact de `validateur_redis` (#48) et d'`arbitre_brief_redis` (#320), et
    pour la même raison : c'est ce qui permet à un run lancé **hors** de l'API
    (`maestro-run --publier`) d'être questionné depuis la Control Tower comme un
    run qu'elle a lancé.

    Bus **durable** depuis #699 : une question posée pendant que l'API est arrêtée
    est consignée à la publication, donc rejouée au démarrage suivant — sans quoi
    l'écran ne montrerait aucune attente pour un agent qui, lui, attend toujours.
    """
    return ArbitreQuestionControlTower(bus_durable(url))
