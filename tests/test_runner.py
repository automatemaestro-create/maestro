"""Tests du runner à l'arrêt borné (ticket #64) : `maestro.engine.runner.run_borne`.

À la fermeture, `asyncio.run` attend sans limite l'extinction des tâches encore en
vol — une réalisation détachée par le time-out ferme (#64) l'y suspendrait
indéfiniment, et le rapport calculé ne serait jamais rendu. `run_borne` doit se
comporter comme `asyncio.run` sur une exécution saine (résultat rendu, exception
propagée) et rendre la main malgré une tâche zombie qui avale son annulation.
"""

import asyncio
import gc
from time import perf_counter

import pytest

from maestro.engine.runner import run_borne


async def _zombie() -> None:
    """Tâche qui avale toute annulation — le sous-processus suspendu du #64."""
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            continue


def test_run_borne_rend_le_resultat_comme_asyncio_run():
    async def principal():
        await asyncio.sleep(0)
        return 42

    assert run_borne(principal()) == 42


def test_run_borne_propage_l_exception_du_programme():
    async def principal():
        raise RuntimeError("boum")

    with pytest.raises(RuntimeError, match="boum"):
        run_borne(principal())


def test_run_borne_rend_la_main_malgre_une_tache_zombie():
    async def principal():
        asyncio.get_running_loop().create_task(_zombie())
        await asyncio.sleep(0.01)  # la zombie démarre et s'installe dans son attente
        return "rapport"

    debut = perf_counter()
    assert run_borne(principal(), grace_s=0.05) == "rapport"
    assert perf_counter() - debut < 2  # la fermeture est bornée, jamais suspendue
    # Finalise la zombie abandonnée ICI : retenue par un cycle, elle n'est
    # détruite qu'au GC cyclique — sans ce collect, son « Task was destroyed
    # but it is pending! » (logger asyncio) fuirait dans le caplog d'un test
    # ultérieur au gré du timing du GC.
    gc.collect()
