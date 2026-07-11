"""Exécution asyncio à l'arrêt borné — le pendant « fermeture » du time-out ferme (#64).

À la fermeture de la boucle, `asyncio.run` annule les tâches encore en vol puis
**attend leur extinction sans limite**. Or le garde-fou de time-out
(`maestro.engine.executor`) peut précisément laisser derrière lui une réalisation
**détachée** dont l'annulation reste suspendue (transport du SDK qui l'avale) : le
rapport serait alors calculé… et jamais rendu, la fermeture ne rendant pas la main
— c'est l'aléa du #64, déplacé d'un cran.

`run_borne` reproduit `asyncio.run` en bornant chaque étape de la fermeture par le
même délai de grâce : annulation des tâches restantes, fermeture des générateurs
asynchrones (celle du `query()` du SDK peut être le point suspendu lui-même),
arrêt de l'exécuteur par défaut. Au-delà, la boucle est fermée quoi qu'il arrive :
une tâche zombie est abandonnée (asyncio le signale en avertissement) plutôt que
de suspendre le process. Sur une exécution saine, le comportement est celui
d'`asyncio.run` — rien ne reste en vol, la fermeture est immédiate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")

#: Délai accordé, à la fermeture, aux tâches annulées et aux générateurs pour
#: s'éteindre proprement — au-delà, ils sont abandonnés.
GRACE_ARRET_S: float = 5.0


def run_borne(coro: Coroutine[Any, Any, _T], *, grace_s: float = GRACE_ARRET_S) -> _T:
    """Exécute `coro` comme `asyncio.run`, mais avec une fermeture de boucle bornée.

    Le résultat (ou l'exception) de `coro` est rendu tel quel ; seule la phase de
    fermeture diffère : aucune tâche restante ne peut la suspendre plus de
    `grace_s` secondes.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            _ferme_borne(loop, grace_s)
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _ferme_borne(loop: asyncio.AbstractEventLoop, grace_s: float) -> None:
    """Rejoue la fermeture d'`asyncio.run` en attendant chaque étape au plus `grace_s`.

    `asyncio.wait` (jamais `wait_for`) : à l'échéance il abandonne l'attente sans
    annuler ni attendre quoi que ce soit de plus — la garantie recherchée.
    """
    restantes = asyncio.all_tasks(loop)
    for tache in restantes:
        tache.cancel()
    if restantes:
        loop.run_until_complete(asyncio.wait(restantes, timeout=grace_s))
    _attend_borne(loop, loop.shutdown_asyncgens(), grace_s)
    _attend_borne(loop, loop.shutdown_default_executor(), grace_s)


def _attend_borne(
    loop: asyncio.AbstractEventLoop, etape: Coroutine[Any, Any, None], grace_s: float
) -> None:
    """Déroule une étape de fermeture au plus `grace_s` secondes, puis l'abandonne."""
    tache = loop.create_task(etape)
    loop.run_until_complete(asyncio.wait({tache}, timeout=grace_s))
