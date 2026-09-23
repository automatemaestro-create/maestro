"""Arbitre de renfort adossé à la Control Tower (#1227).

Relie le canal de renfort du moteur (`maestro.engine.renfort`) au **fil de
l'orchestration** : quand la décomposition appelle un rôle que l'équipe du projet
n'a pas, `ArbitreRenfortControlTower` pose la demande dans le fil
(`ServiceChat.proposer_recrutement`) puis **attend la décision** sur le bus
(`renfort.decision`, publiée par `POST /api/chat/{agent}/recrutement`).

C'est le **quatrième pendant** de `ValidateurControlTower` (#48),
d'`ArbitreBriefControlTower` (#320) et d'`ArbitreQuestionControlTower` (#1023) :
même bus, même patron d'attente (s'abonner **avant** de publier, pour qu'une
décision instantanée ne tombe pas dans le vide). Trois choses l'en distinguent, et
chacune décide d'une partie de ce fichier.

## 1. La demande n'est pas publiée, elle est écrite dans le fil

Les trois aînés publient leur demande sur le bus, où la projection la range dans
une file que l'API sert et qu'un écran affiche. Ici il n'y avait rien à construire :
#1146 a déjà posé la carte d'équipe au pied du fil (`EquipeDansLeFil`), déjà le
geste qui la valide (`POST …/recrutement`), déjà la règle qui dit si une demande
tient encore (`recrutement_en_attente`). Le renfort **réutilise** tout cela au lieu
de le doubler — c'est ce que le ticket demande explicitement —, et le prix de cette
réutilisation est que la demande voyage par le fil et non par le bus. Ce qui reste
sur le bus est la seule chose que le fil ne sait pas faire : **prévenir le run**.

Conséquence assumée : cet arbitre connaît un `ServiceChat`, là où les trois autres
ne connaissent qu'un `EventBus`. Il n'en pilote rien — un verbe, qui écrit un
message — et c'est le prix d'une carte partagée plutôt que d'une seconde carte à
tenir d'accord avec la première.

## 2. Il tient la borne lui-même

Chez les trois aînés, l'attente est indéfinie et c'est l'appelant qui renonce
(l'exécuteur pour une question, personne pour un brief). Ici la borne est **ici**,
parce que sans réponse il faut le **dire dans le fil** — c'est le second critère du
ticket — et que l'arbitre est le seul à y avoir accès. Une boucle qui bornerait
l'annulerait au milieu de son attente, donc avant qu'il ait écrit quoi que ce soit.

La borne voyage sur la demande (`DemandeRenfort.attente_s`) : c'est le même temps
humain qu'un arbitrage ou qu'une question d'agent, et il n'a qu'un réglage
(`MAESTRO_ARBITRAGE_ATTENTE`).

## 3. Une panne ne condamne rien

Fail-safe hérité, dans le sens utile : ce qui est en jeu n'est pas un acte sensible
mais une **amélioration** de l'équipe. Un fil illisible, un bus refermé, une
décision qui n'arrive jamais — le plan reste exécutable par l'équipe qu'on a, et la
boucle consigne pourquoi personne n'a été recruté (`OrchestrationEngine
._confronte_equipe`). Rien ici ne lève à la place du run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from maestro.agents.catalog import Agent
from maestro.controltower.chat import DemandeRecrutement, ServiceChat
from maestro.controltower.events import (
    EVENEMENT_RENFORT_DECISION,
    Event,
    EventBus,
)
from maestro.controltower.state import RENFORT_ACCORDE
from maestro.engine.renfort import DecisionRenfort, DemandeRenfort

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


class ArbitreRenfortControlTower:
    """Arbitre (#1227) qui porte la demande de renfort au fil et attend la décision.

    S'abonne au bus **avant** d'écrire dans le fil : même une validation
    immédiate ne peut pas être manquée. L'attente est bornée par
    `demande.attente_s` — et à l'échéance elle est **dite dans le fil**, jamais
    seulement au journal du run : la personne qui revient doit lire, là où la
    demande a été posée, que le run est reparti sans elle.

    `fil` est le service du fil de l'orchestration et `agent` sa fiche : les deux
    ensemble désignent *où* la demande est posée, ce qui est exactement ce qu'un
    câblage de déploiement décide.
    """

    def __init__(self, bus: EventBus, fil: ServiceChat, agent: Agent) -> None:
        self._bus = bus
        self._fil = fil
        self._agent = agent

    async def __call__(self, demande: DemandeRenfort) -> DecisionRenfort:
        """Pose la demande dans le fil, puis rend la décision (ou l'absence de décision)."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.run_id))
        # Laisse l'abonnement se poser avant d'écrire (bus mémoire : un tour de
        # boucle suffit ; sur Redis, un geste humain arrive de toute façon bien
        # après l'aller-retour du SUBSCRIBE). Même précaution qu'en #48, #320 et
        # #1023.
        await asyncio.sleep(0)
        try:
            await self._fil.proposer_recrutement(
                self._agent,
                contenu=_phrase(demande),
                demande=_demande_de_fil(demande),
            )
            decision = await asyncio.wait_for(ecoute, timeout=demande.attente_s)
        except TimeoutError:
            await self._dire_sans_reponse(demande)
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
        return decision

    async def _dire_sans_reponse(self, demande: DemandeRenfort) -> None:
        """Écrit dans le fil que le run est reparti sans renfort — best-effort.

        Best-effort **et pas seulement par prudence** : à cet instant le run est
        déjà décidé (il continue), et faire échouer l'arbitre pour un message
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
                    f"« {demande.manque.manque.role} », et le run a continué avec "
                    "l'équipe actuelle — ces tâches sont allées au rôle le plus "
                    "proche. Vous pouvez recruter ce rôle depuis les écrans "
                    "d'agents du projet, puis relancer ce travail."
                ),
            )


def _phrase(demande: DemandeRenfort) -> str:
    """La phrase posée dans le fil avec la demande — objectif, raison, borne."""
    return PHRASE_RENFORT.format(
        objectif=demande.objectif.strip(),
        raison=demande.manque.raison(),
        attente=demande.attente_s,
    )


def _demande_de_fil(demande: DemandeRenfort) -> DemandeRecrutement:
    """La demande du moteur dans la forme que le fil et sa carte connaissent.

    `run_id` n'est pas décoratif : c'est lui qui dit à la carte comme au geste
    qu'un run attend (`DemandeRecrutement.pendant_un_run`), donc qu'il n'y a rien
    à reproposer une fois l'équipe créée. `objectif` voyage quand même — c'est ce
    qui donne son sens au rôle proposé —, mais il ne sera pas relancé.
    """
    return DemandeRecrutement(
        objectif=demande.objectif,
        projet_id=demande.projet_id,
        run_id=demande.run_id,
        role=demande.manque.manque.role,
        gabarit=demande.manque.manque.gabarit or "",
        raison=demande.manque.raison(),
        taches=demande.manque.taches,
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
