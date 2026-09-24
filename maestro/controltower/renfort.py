"""Arbitre de renfort adossé à la Control Tower (#1227, #1260).

Relie le canal de renfort du moteur (`maestro.engine.renfort`) au **fil de
l'orchestration** : quand la décomposition appelle un rôle que l'équipe du projet
n'a pas, la demande est posée dans le fil qui a lancé le run, et le run **attend
la décision** sur le bus (`renfort.decision`, publiée par
`POST /api/chat/{agent}/recrutement`).

C'est le **quatrième pendant** de `ValidateurControlTower` (#48),
d'`ArbitreBriefControlTower` (#320) et d'`ArbitreQuestionControlTower` (#1023) :
même bus, même patron d'attente (s'abonner **avant** de publier, pour qu'une
décision instantanée ne tombe pas dans le vide). Trois choses le distinguent, et
chacune décide d'une partie de ce fichier.

## 1. Deux moitiés, de part et d'autre du bus (#1260)

#1227 avait fait de l'arbitre l'**écrivain** du fil : il posait la demande par
`ServiceChat.proposer_recrutement`, puis attendait. Cela ne marchait que dans le
process de l'API — le seul qui ait un fil. Or la vraie stack confie ses runs à
l'**hôte détaché** (#446) : il ne recevait aucun arbitre, le manque était consigné
au journal, et la personne ne voyait jamais rien. Le bouclage du 2026-09-24 l'a
constaté sur le cas même de #1227, run échoué 0/3 à la clé.

Le dispositif est donc coupé en deux, au bus, comme ses trois aînés :

- **`ArbitreRenfortControlTower`** vit **avec le moteur**, dans l'un ou l'autre
  hôte. Il ne connaît que le bus : il publie `renfort.demande`, attend
  `renfort.decision`, et renonce à la borne ;
- **`RelaisRenfort`** vit **dans l'API**, seul écrivain du fil. La pompe
  d'événements lui passe chaque `renfort.demande` ; il pose la demande dans la
  conversation qui a lancé le run, et y dit l'échéance si personne n'a répondu.

**Les deux hôtes prennent le même chemin**, et c'est le remède autant que le
câblage : c'est parce que l'hôte en process avait un chemin à lui que l'absence de
l'autre est passée inaperçue — les tests de #1227 exerçaient le seul qui marchait.

Ce qui reste du choix de #1227 est l'essentiel : la demande est posée **dans le
fil**, sur la carte d'équipe de #1146 (`EquipeDansLeFil`), et le geste qui y
répond est celui de #1146 (`recrutement_en_attente`, `POST …/recrutement`). Rien
n'est doublé — la demande voyage seulement sur le bus avant d'y arriver.

## 2. La borne est tenue des deux côtés, sur la même date

Chez les trois aînés, l'attente est indéfinie et c'est l'appelant qui renonce.
Ici la borne voyage sur la demande (`DemandeRenfort.attente_s`, le même temps
humain qu'un arbitrage — `MAESTRO_ARBITRAGE_ATTENTE`), et chaque moitié la tient
pour ce qui la regarde : l'arbitre **reprend le run**, le relais **le dit dans le
fil**. Les deux lisent la même date (`echeance`, posée à la publication), si bien
qu'un seul fait se produit : passé l'échéance, le run continue avec l'équipe
actuelle, et le fil porte la phrase qui le dit.

Aucun troisième statut n'est publié pour « personne n'a répondu » : le seul
producteur de `renfort.decision` reste un geste humain (cf.
`maestro.controltower.state`).

## 3. Une panne ne condamne rien

Fail-safe hérité, dans le sens utile : ce qui est en jeu n'est pas un acte sensible
mais une **amélioration** de l'équipe. Un bus refermé, une API arrêtée (la demande
n'est alors relayée par personne), une décision qui n'arrive jamais — le plan
reste exécutable par l'équipe qu'on a, les tâches vont au rôle le plus proche
(#1260), et la boucle consigne pourquoi personne n'a été recruté
(`OrchestrationEngine._confronte_equipe`). Rien ici ne lève à la place du run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from maestro.agents.catalog import Agent
from maestro.controltower.chat import DemandeRecrutement, ServiceChat
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_RENFORT_DECISION,
    EVENEMENT_RENFORT_DEMANDE,
    ROLE_RUN,
    Event,
    EventBus,
)
from maestro.controltower.state import RENFORT_ACCORDE
from maestro.engine.renfort import DecisionRenfort, DemandeRenfort
from maestro.telemetry import redact_secrets

_LOGGER = logging.getLogger(__name__)

#: Ce que le fil dit en posant la demande. Une phrase, et elle porte les quatre
#: choses qu'il faut pour décider : ce qui a été demandé, le rôle qui manque, la
#: raison (le **plan**, pas le projet), et ce qui se passe si l'on ne répond pas.
#:
#: La dernière n'est pas une politesse : sans elle, une personne qui revient dix
#: minutes plus tard ne saurait pas si son run l'attend encore. C'est la même
#: information que l'hypothèse d'une question d'agent (#1023) — *voici ce qui se
#: passera sans vous* —, et pour la même raison.
PHRASE_RENFORT = (
    "Avant d'exécuter « {objectif} », une chose : le plan que je viens d'écrire "
    "appelle un rôle que votre équipe n'a pas. {raison}\n\n"
    "Je vous propose de le recruter, juste en dessous — relisez son playbook, "
    "ajustez ses instances, rien n'est créé sans votre validation. Si vous "
    "déclinez, ou si vous ne répondez pas d'ici {attente:g} s, le run continue "
    "avec l'équipe actuelle et ces tâches iront au rôle le plus proche."
)


def evenement_demande(demande: DemandeRenfort, *, maintenant: datetime | None = None) -> Event:
    """La demande de renfort d'un run, telle qu'elle traverse le bus (#1260).

    Elle porte **tout** ce que le relais écrira, déjà composé : la phrase
    (`detail`) et la demande que la carte du fil lira (`recrutement`). Le relais
    n'a donc rien à juger ni à recomposer — il ne connaît pas le manque, seulement
    sa forme publiée, et c'est ce qui laisse la raison montrée être *la* raison
    calculée par le moteur.

    Secrets expurgés de ce qui vient d'un humain (l'objectif), comme la question
    d'un agent (#1023) : l'événement part au journal durable. La borne est posée
    **en date** (`echeance`), la même que l'arbitre tient de son côté.
    """
    naissance = maintenant or datetime.now(UTC)
    objectif = redact_secrets(demande.objectif.strip())
    raison = demande.manque.raison()
    recrutement = DemandeRecrutement(
        objectif=objectif,
        projet_id=demande.projet_id,
        run_id=demande.run_id,
        role=demande.manque.manque.role,
        gabarit=demande.manque.manque.gabarit or "",
        raison=raison,
        taches=demande.manque.taches,
    )
    return Event(
        type=EVENEMENT_RENFORT_DEMANDE,
        run_id=demande.run_id,
        projet_id=demande.projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre=recrutement.role,
        detail=PHRASE_RENFORT.format(
            objectif=objectif, raison=raison, attente=demande.attente_s
        ),
        recrutement=recrutement.to_dict(),
        echeance=(naissance + timedelta(seconds=demande.attente_s)).isoformat(
            timespec="seconds"
        ),
        horodatage=naissance.isoformat(timespec="seconds"),
    )


class ArbitreRenfortControlTower:
    """Arbitre (#1227) du moteur : publie la demande de renfort, attend la décision.

    Il vit **avec le moteur**, dans l'hôte en process comme dans l'hôte détaché
    (#1260) — c'est pourquoi il ne connaît que le bus. S'abonne **avant** de
    publier : même une validation immédiate ne peut pas être manquée.

    L'attente est bornée par `demande.attente_s`. À l'échéance il rend
    « sans réponse » : le run continue avec l'équipe actuelle. Le dire **dans le
    fil** n'est pas son affaire — c'est celle du relais, qui tient la même date.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandeRenfort) -> DecisionRenfort:
        """Publie la demande, puis rend la décision (ou l'absence de décision)."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.run_id))
        # Laisse l'abonnement se poser avant de publier (bus mémoire : un tour de
        # boucle suffit ; sur Redis, un geste humain arrive de toute façon bien
        # après l'aller-retour du SUBSCRIBE). Même précaution qu'en #48, #320 et
        # #1023.
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_demande(demande))
            return await asyncio.wait_for(ecoute, timeout=demande.attente_s)
        except TimeoutError:
            return DecisionRenfort(
                approuve=False,
                detail=f"personne n'a répondu en {demande.attente_s:g} s",
                sans_reponse=True,
            )
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


class RelaisRenfort:
    """Le relais de l'API (#1260) : pose dans le fil la demande qu'un run a publiée.

    Le seul écrivain du fil pour ce canal, quel que soit l'hôte du run. La pompe
    d'événements l'appelle sur chaque `renfort.demande`, **après** la projection,
    et il rend la main tout de suite : le travail — écrire, attendre la décision,
    dire l'échéance — part dans une tâche, qu'il garde tant qu'elle vit
    (`asyncio.create_task` ne retient qu'une référence faible).

    La demande est posée **dans la conversation qui a lancé le run** — celle dont
    un message porte son `run_id` (#268, `conversation_du_run`) — et non dans la
    plus récente : la personne qui a ouvert une autre conversation entre-temps
    doit trouver la question là où elle a demandé le travail. Un run qu'aucune
    conversation n'a lancé (l'écran des exécutions) retombe sur la plus récente.

    `fil` et `agent` désignent *où* la demande est posée, ce qui est exactement ce
    qu'un câblage de déploiement décide. `maintenant` est l'horloge, injectable
    pour que les tests tiennent l'échéance sans attendre.
    """

    def __init__(
        self,
        bus: EventBus,
        fil: ServiceChat,
        agent: Agent,
        *,
        maintenant: Callable[[], datetime] | None = None,
    ) -> None:
        self._bus = bus
        self._fil = fil
        self._agent = agent
        self._maintenant = maintenant or (lambda: datetime.now(UTC))
        self._en_vol: set[asyncio.Task[None]] = set()

    def __call__(self, event: Event) -> None:
        """Prend en charge `event` s'il est une demande de renfort — sans faire attendre."""
        if event.type != EVENEMENT_RENFORT_DEMANDE or not event.run_id:
            return
        tache = asyncio.create_task(self.relayer(event))
        self._en_vol.add(tache)
        tache.add_done_callback(self._en_vol.discard)

    def fermer(self) -> None:
        """Abandonne les relais en vol — l'API s'arrête, le run tient sa propre borne."""
        for tache in list(self._en_vol):
            tache.cancel()

    async def relayer(self, event: Event) -> None:
        """Pose la demande dans le fil, attend la décision jusqu'à l'échéance, la dit sinon.

        Best-effort de bout en bout : un fil illisible ou une écriture refusée ne
        doivent ni lever dans la pompe ni condamner le run, qui tient sa propre
        borne et reprend avec l'équipe actuelle. La panne est tracée au journal
        technique.
        """
        demande = DemandeRecrutement.from_dict(event.recrutement or {})
        conversation: str | None = None
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, event.run_id))
        await asyncio.sleep(0)
        try:
            conversation = self._fil.conversation_du_run(self._agent.nom, event.run_id)
            await self._fil.proposer_recrutement(
                self._agent,
                contenu=event.detail,
                demande=demande,
                conversation=conversation,
            )
            await asyncio.wait_for(ecoute, timeout=self._attente(event))
        except TimeoutError:
            await self._dire_sans_reponse(demande, conversation)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — le run tient sa borne, la pompe continue
            _LOGGER.exception(
                "Demande de renfort du run %s non relayée dans le fil.", event.run_id
            )
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await ecoute

    def _attente(self, event: Event) -> float:
        """Le temps qui reste jusqu'à l'échéance de la demande — jamais négatif.

        L'échéance est celle que l'arbitre du run tient : la relire ici plutôt que
        de relire le réglage fait qu'un relais qui reçoit la demande en retard
        n'attend pas plus longtemps que le run. Une échéance illisible — un
        producteur d'avant ce lot — vaut une attente nulle : le relais pose la
        demande et dit aussitôt qu'elle est restée sans réponse, ce qui ne peut
        pas être faux, là où attendre une durée inventée pourrait l'être.
        """
        try:
            fin = datetime.fromisoformat(event.echeance)
        except ValueError:
            return 0.0
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=UTC)
        return max((fin - self._maintenant()).total_seconds(), 0.0)

    async def _dire_sans_reponse(
        self, demande: DemandeRecrutement, conversation: str | None
    ) -> None:
        """Écrit dans le fil que le run est reparti sans renfort — best-effort.

        Best-effort **et pas seulement par prudence** : à cet instant le run est
        déjà décidé (il continue), et faire échouer le relais pour un message
        qu'on n'a pas su écrire remplacerait une trace manquante par une cause
        d'échec inventée. Ce que la boucle consigne au journal, elle, ne dépend
        pas de cette écriture.

        Le message **ne repose pas la demande** (`demande=None`) : la laisser
        offrirait un bouton « Créer l'équipe » qui promettrait de faire reprendre
        un run déjà parti. Ce qu'il reste à faire — recruter pour la prochaine
        fois — se fait depuis les écrans d'agents du projet, et la phrase le dit.
        """
        with suppress(Exception):
            await self._fil.proposer_recrutement(
                self._agent,
                contenu=(
                    f"Personne n'a répondu : je n'ai recruté aucun rôle "
                    f"« {demande.role} », et le run a continué avec "
                    "l'équipe actuelle — ces tâches sont allées au rôle le plus "
                    "proche. Vous pouvez recruter ce rôle depuis les écrans "
                    "d'agents du projet, puis relancer ce travail."
                ),
                conversation=conversation,
            )


async def _premiere_decision(
    flux: AsyncIterator[Event], run_id: str
) -> DecisionRenfort:
    """Attend sur `flux` la décision visant `run_id` et la rend telle quelle.

    Ignore tout le reste du bus (statuts de tâches, validations, décisions visant
    un autre run). Le filtre est le **run**, et il suffit : un run n'a qu'une
    demande de renfort en vol, la confrontation ayant lieu une fois, après la
    décomposition (cf. `EVENEMENT_RENFORT_DECISION`).

    Si le flux se tarit sans décision (bus refermé), **lève** : la boucle en fait
    un « personne n'a répondu » consigné, jamais un échec de run.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_RENFORT_DECISION:
                continue
            if event.run_id != run_id:
                continue
            return DecisionRenfort(
                approuve=event.statut == RENFORT_ACCORDE,
                detail=event.detail,
            )
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans décision de renfort pour le run {run_id}"
    )
