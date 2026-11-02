"""L'attente d'un run à la limite d'usage, adossée à la Control Tower (#1543).

Relie l'attente du moteur (`maestro.engine.limite`) à la Control Tower : quand le
fournisseur oppose sa limite d'usage à une tâche, `AttenteLimiteControlTower` **met
le run en pause** — l'ordre de pause de #477, sur le bus, avec sa cause
(`limite_usage`) et l'heure de reprise quand le fournisseur la donne (`echeance`) —,
puis attend : l'heure venue, elle **reprend** le run d'elle-même ; d'ici là,
« Reprendre » à l'écran le reprend aussi (`ServiceExecutions.reprendre`, qui publie
l'ordre de reprise qu'elle guette). La projection en tire ce que la liste et le fil
disent du run (`pause_cause`, `reprise_a`), et le guet de l'hôte détaché ferme puis
rouvre sa porte sur les mêmes ordres : aucune tâche neuve ne part dans une fenêtre
que le fournisseur a fermée.

Sixième pendant des arbitres du bus (`ValidateurControlTower` #48, …,
`ArbitrePlafondControlTower` #1182), avec leur patron — s'abonner **avant** de
publier —, et deux différences qui décident de son code :

- **elle n'attend personne par défaut.** Une limite qui dit quand elle se lève n'a
  pas besoin d'une décision : le run repart à l'heure dite. Il n'attend une personne
  que lorsque le fournisseur n'a rien dit (un solde épuisé ne se relève pas avec le
  temps) — et aucun palier de quelques minutes n'est inventé à sa place ;
- **elle ne défait jamais la pause d'une personne.** Un run qu'on avait mis en
  pause — avant la limite, ou pendant l'attente — reste en pause au reset : la tâche
  coupée reprend (elle était en vol, une pause la laisse finir), mais rien de neuf
  ne part avant « Reprendre ».

Fail-safe hérité : si le bus se referme pendant l'attente, elle **lève** — la tâche
échoue alors sur sa limite, nommée par son type, plutôt que d'attendre un ordre qui
ne viendra pas.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime

from maestro.controltower.causes import CAUSE_LIMITE_USAGE
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_EXECUTION_STATUT,
    ROLE_RUN,
    Event,
    EventBus,
)
from maestro.controltower.state import (
    ORDRE_PAUSE,
    ORDRE_REPRISE,
    STATUTS_EXECUTION_TERMINAUX,
)
from maestro.engine.limite import LimiteDuRun, phrase_de_la_limite, phrase_de_la_reprise
from maestro.engine.pause import PorteExecution
from maestro.telemetry import redact_secrets

#: Ce qui se dit quand la limite se lève sur un run qu'une personne avait mis en
#: pause : la tâche coupée repart, le run, lui, attend toujours « Reprendre ».
DETAIL_LEVEE_SOUS_PAUSE = (
    "limite d'usage du fournisseur levée — la tâche coupée reprend dans sa session ; "
    "le run reste en pause, comme demandé, jusqu'à « Reprendre »"
)


def evenement_pause(limite: LimiteDuRun) -> Event:
    """L'ordre de pause d'un run à la limite — sa cause et l'heure de reprise portées en faits."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=limite.run_id,
        projet_id=limite.projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre="Limite d'usage du fournisseur",
        statut=ORDRE_PAUSE,
        detail=redact_secrets(phrase_de_la_limite(limite)),
        cause=CAUSE_LIMITE_USAGE,
        echeance=limite.reprise_a.isoformat() if limite.reprise_a is not None else "",
    )


def evenement_reprise(limite: LimiteDuRun) -> Event:
    """L'ordre de reprise qu'elle publie d'elle-même, la limite levée."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=limite.run_id,
        projet_id=limite.projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre="Limite d'usage du fournisseur",
        statut=ORDRE_REPRISE,
        detail=phrase_de_la_reprise(limite),
        cause=CAUSE_LIMITE_USAGE,
    )


def evenement_levee_sous_pause(limite: LimiteDuRun) -> Event:
    """La limite se lève sur un run qu'une personne a suspendu : la pause redevient la sienne."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=limite.run_id,
        projet_id=limite.projet_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        titre="Limite d'usage du fournisseur",
        statut=ORDRE_PAUSE,
        detail=DETAIL_LEVEE_SOUS_PAUSE,
    )


def _maintenant() -> datetime:
    return datetime.now(UTC)


class AttenteLimiteControlTower:
    """Attente (#1543) du moteur à la limite d'usage : pause publiée, reprise au reset ou sur geste.

    Une instance **par run** — les deux hôtes la construisent avec le moteur du run —,
    parce qu'elle retient d'une limite à l'autre deux faits que le bus seul ne redit
    pas : qu'elle tient elle-même la pause en cours, et qu'une personne en a posé une.
    `porte` est celle du run (#477) : fermée à la pause, rouverte à la reprise, pour
    que l'hôte en process ne laisse pas partir de tâche neuve dans la fenêtre fermée
    — l'hôte détaché la bascule déjà sur les ordres qu'il guette. `maintenant` est
    l'horloge, injectable.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        porte: PorteExecution | None = None,
        maintenant: Callable[[], datetime] = _maintenant,
    ) -> None:
        self._bus = bus
        self._porte = porte
        self._maintenant = maintenant
        # La pause en cours est-elle la sienne ? Posé à la publication, retiré à la
        # reprise : une attente remplacée (mieux informée, `RendezVousLimite`) la
        # laisse posée, l'attente qui la remplace la tient.
        self._tient_la_pause = False
        # Une personne a-t-elle mis le run en pause ? Vu sur le bus pendant
        # l'attente, ou lu sur la porte, fermée quand la limite tombe.
        self._pause_humaine = False

    async def __call__(self, limite: LimiteDuRun) -> None:
        """Met le run en pause, puis rend la main au reset ou sur « Reprendre »."""
        if not self._tient_la_pause:
            # Une limite neuve relit la porte : la pause d'une personne vaut pour la
            # limite qu'elle a vue tomber, pas pour celle d'après, qu'un « Reprendre »
            # a pu précéder sans qu'aucune attente ne l'entende. Une attente qui en
            # remplace une autre (`RendezVousLimite`) garde, elle, ce qui a été vu.
            self._pause_humaine = self._porte is not None and not self._porte.ouverte
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(self._guetter(flux, limite.run_id))
        # Laisse l'abonnement se poser avant de publier — même précaution qu'en #48.
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_pause(limite))
            self._tient_la_pause = True
            if self._porte is not None:
                self._porte.fermer()
            delai = self._delai(limite)
            if delai is not None:
                fini, _ = await asyncio.wait({ecoute}, timeout=delai)
                if ecoute not in fini:
                    await self._lever(limite)
                    return
            await ecoute
            self._tient_la_pause = False
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute

    def _delai(self, limite: LimiteDuRun) -> float | None:
        """Le temps qui reste avant l'heure de reprise — None : personne ne l'a dite.

        Une heure **déjà passée** n'est pas une heure de reprise : repartir aussitôt
        retomberait sur la même limite, et la session reprise, sur une nouvelle
        attente, sans fin. Le run attend alors « Reprendre », comme sans heure.
        """
        if limite.reprise_a is None:
            return None
        reste = (limite.reprise_a - self._maintenant()).total_seconds()
        return reste if reste > 0 else None

    async def _lever(self, limite: LimiteDuRun) -> None:
        """L'heure de reprise est venue : le run repart — sauf si une personne l'a suspendu."""
        self._tient_la_pause = False
        if self._pause_humaine:
            await self._bus.publish(evenement_levee_sous_pause(limite))
            return
        await self._bus.publish(evenement_reprise(limite))
        if self._porte is not None:
            self._porte.ouvrir()

    async def _guetter(self, flux: AsyncIterator[Event], run_id: str) -> None:
        """Rend la main sur l'ordre de reprise de `run_id` — retient au passage une pause humaine.

        Sa propre pause revient sur le flux (abonnement posé avant de publier) : elle
        porte la cause de la limite, et ne se lit pas comme celle d'une personne. Un
        run soldé pendant l'attente n'a plus rien à reprendre — l'attente lève.
        """
        try:
            async for event in flux:
                if event.type != EVENEMENT_EXECUTION_STATUT or event.run_id != run_id:
                    continue
                if event.statut == ORDRE_REPRISE:
                    self._pause_humaine = False
                    return
                if event.statut == ORDRE_PAUSE and event.cause != CAUSE_LIMITE_USAGE:
                    self._pause_humaine = True
                elif event.statut in STATUTS_EXECUTION_TERMINAUX:
                    raise RuntimeError(
                        f"le run {run_id} s'est soldé pendant qu'il attendait la levée de "
                        "la limite d'usage"
                    )
        finally:
            aclose = getattr(flux, "aclose", None)
            if aclose is not None:
                await aclose()
        raise RuntimeError(
            f"le bus d'événements s'est refermé pendant que le run {run_id} attendait la "
            "levée de la limite d'usage"
        )
