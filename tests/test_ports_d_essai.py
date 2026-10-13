"""Chaque agent reçoit son port d'essai, et deux agents simultanés jamais le même (#1491).

Le cadre de l'agent lui disait d'essayer son serveur sur « un port libre que tu
choisis » : chacun tirait le sien de tête, et trois tâches parallèles sur quatre sont
tombées sur 47931 (run `3e565990fca7`). Maestro en réserve désormais un à chaque
exécution (`maestro.sandbox.ports.port_d_essai`) et le nomme sous « La machine ».

Les réservations se jouent pour de vrai — des sockets liés sur la boucle locale, rien
de doublé : c'est le poste qui dit si deux réservations du même numéro coexistent, et
si un serveur de l'agent tient à côté de la sienne.
"""

from __future__ import annotations

import asyncio
import functools
import os
import random
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.machine import TITRE_MACHINE, Machine
from maestro.providers.base import ModelProvider
from maestro.sandbox import ports
from maestro.sandbox.ports import (
    PORTS_D_ESSAI,
    PortIndisponible,
    port_d_essai,
    port_libre,
)
from maestro.sandbox.verification import PORTS_DE_VERIFICATION

MACHINE = Machine(systeme="Linux 6.8 (x86_64)", temporaire="/tmp", shell="/usr/bin/bash")

#: La ligne du port d'essai dans le message de la tâche.
_LIGNE_PORT = re.compile(r"Ton port d'essai\*\* : `(\d+)`")


class _Tirage:
    """Propose d'abord les ports donnés — en boucle si `ensuite` est faux —, puis au hasard."""

    def __init__(self, proposes: list[int], *, ensuite: bool = True) -> None:
        self.proposes = list(proposes)
        self.ensuite = ensuite

    def randrange(self, debut: int, fin: int) -> int:
        if self.proposes:
            port = self.proposes.pop(0)
            if not self.ensuite:
                self.proposes.append(port)
            return port
        return random.randrange(debut, fin)


def _un_port_d_essai_libre() -> int:
    """Un port de la plage d'essai que personne n'écoute ni ne réserve, à l'instant."""
    with port_d_essai() as port:
        return port


def _ecouter(port: int, hote: str = "127.0.0.1") -> socket.socket:
    """Un serveur TCP qui écoute `port` — ce que l'agent lance pour essayer sa page."""
    prise = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    prise.bind((hote, port))
    prise.listen()
    return prise


def _port_du_message(message: str) -> int | None:
    trouve = _LIGNE_PORT.search(message)
    return int(trouve.group(1)) if trouve else None


# --------------------------------------------------------------------------- #
# La réservation                                                               #
# --------------------------------------------------------------------------- #


def test_deux_reservations_simultanees_ne_rendent_jamais_le_meme_port() -> None:
    """Le critère du ticket : le même numéro tiré deux fois n'est donné qu'une fois."""
    vise = _un_port_d_essai_libre()
    tirage = _Tirage([vise, vise])

    with port_d_essai(tirage=tirage) as premier, port_d_essai(tirage=tirage) as second:
        assert premier == vise
        assert second != vise
        assert second in PORTS_D_ESSAI


def test_un_port_reserve_par_un_autre_process_n_est_pas_donne() -> None:
    """Les tâches d'un run peuvent jouer dans des process différents (un worker d'une
    file) : la réservation tient d'un process à l'autre, et se rend quand il meurt."""
    depot = Path(__file__).resolve().parents[1]
    vise = _un_port_d_essai_libre()
    tenant = subprocess.Popen(  # noqa: S603 - argv fixe
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from maestro.sandbox.ports import port_d_essai\n"
            "class Fixe:\n"
            f"    def randrange(self, debut, fin): return {vise}\n"
            "with port_d_essai(tirage=Fixe(), essais=1) as port:\n"
            "    print(port, flush=True)\n"
            "    sys.stdin.read()\n",
        ],
        cwd=depot,
        env={**os.environ, "PYTHONPATH": str(depot)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert tenant.stdout is not None and tenant.stdin is not None
        assert tenant.stdout.readline().strip() == str(vise)
        with pytest.raises(PortIndisponible):
            with port_d_essai(tirage=_Tirage([vise], ensuite=False), essais=3):
                pass
    finally:
        assert tenant.stdin is not None
        tenant.stdin.close()
        tenant.wait(timeout=30)

    with port_d_essai(tirage=_Tirage([vise], ensuite=False), essais=1) as rendu:
        assert rendu == vise


def test_la_reservation_ne_gene_pas_le_serveur_que_l_agent_lance() -> None:
    """Le port est donné pour qu'un serveur l'écoute : la réservation ne doit pas l'en
    empêcher, sur la boucle locale comme sur toutes les adresses."""
    with port_d_essai() as port:
        for hote in ("127.0.0.1", "0.0.0.0"):  # noqa: S104 - un serveur qui écoute partout
            with _ecouter(port, hote):
                pass


def test_la_reservation_est_rendue_a_la_sortie_meme_en_echec() -> None:
    vise = _un_port_d_essai_libre()

    with pytest.raises(RuntimeError):
        with port_d_essai(tirage=_Tirage([vise], ensuite=False), essais=1):
            raise RuntimeError("la tâche a échoué")

    with port_d_essai(tirage=_Tirage([vise], ensuite=False), essais=1) as port:
        assert port == vise


def test_un_port_qu_un_serveur_ecoute_deja_n_est_jamais_donne() -> None:
    """Un serveur d'une tâche finie, ou d'un autre programme : le port n'est pas libre."""
    tenu = _un_port_d_essai_libre()
    with _ecouter(tenu):
        assert not port_libre(tenu)
        with port_d_essai(tirage=_Tirage([tenu])) as port:
            assert port != tenu


def test_un_poste_sans_port_d_essai_libre_le_dit() -> None:
    tenu = _un_port_d_essai_libre()
    with _ecouter(tenu), pytest.raises(PortIndisponible):
        with port_d_essai(tirage=_Tirage([tenu], ensuite=False), essais=4):
            pass


def test_la_plage_d_essai_ne_croise_pas_celle_des_verifications() -> None:
    """Un port d'essai ne tombe jamais sur le service d'une vérification (#1487)."""
    assert not set(PORTS_D_ESSAI) & set(PORTS_DE_VERIFICATION)


# --------------------------------------------------------------------------- #
# La section « La machine »                                                     #
# --------------------------------------------------------------------------- #


def test_la_section_nomme_le_port_d_essai_en_dernier() -> None:
    section = MACHINE.consigne(port_d_essai=14823)

    assert section.startswith(MACHINE.consigne())
    assert section.splitlines()[-1].startswith("- **Ton port d'essai** : `14823`")
    # Une ressource donnée, pas une borne : la ligne dit ce qu'un autre port ne garantit pas.
    assert "ne le donne à aucune autre" in section
    assert "n'est réservé à personne" in section


def test_sans_port_la_section_est_celle_d_avant() -> None:
    assert "port d'essai" not in MACHINE.consigne()


# --------------------------------------------------------------------------- #
# Le runtime de l'agent                                                         #
# --------------------------------------------------------------------------- #


class _Temoin(ModelProvider):
    """Fournisseur factice : retient chaque message, et ce que le poste dit du port nommé.

    `ensemble` sessions se retrouvent avant de rendre la main : elles sont vivantes en
    même temps, comme les tâches parallèles d'un run.
    """

    name = "temoin"

    def __init__(self, *, sur_le_poste: bool = True, ensemble: int = 1) -> None:
        self.prompts: list[str] = []
        self.tenus_pendant: list[bool] = []
        self._sur_le_poste = sur_le_poste
        self._ensemble = ensemble
        self._arrivees = 0
        self._tous: asyncio.Event | None = None

    @property
    def commandes_sur_le_poste(self) -> bool:
        return self._sur_le_poste

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, workspace, tools, **kwargs: Any) -> str:
        if self._tous is None:
            self._tous = asyncio.Event()
        self.prompts.append(prompt)
        port = _port_du_message(prompt)
        if port is not None:
            # Pendant la session, le port est tenu : une autre réservation échoue.
            self.tenus_pendant.append(ports._reserver(port) is None)
        self._arrivees += 1
        if self._arrivees >= self._ensemble:
            self._tous.set()
        await asyncio.wait_for(self._tous.wait(), timeout=10)
        return "Fait."


def test_deux_agents_simultanes_recoivent_chacun_leur_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le critère du ticket, bout à bout : deux tâches parallèles, vivantes ensemble, à
    qui le tirage propose le même numéro — chacune reçoit le sien, nommé dans son
    message, tenu pendant sa session, rendu après."""
    vise = _un_port_d_essai_libre()
    tirage = _Tirage([vise, vise])
    monkeypatch.setattr(
        "maestro.agents.runtime.port_d_essai", functools.partial(port_d_essai, tirage=tirage)
    )
    provider = _Temoin(ensemble=2)
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)

    async def deux_taches() -> None:
        await asyncio.gather(
            runtime.execute("Servir la page des recettes", machine=MACHINE),
            runtime.execute("Servir la page de contact", machine=MACHINE),
        )

    asyncio.run(deux_taches())

    recus = [_port_du_message(prompt) for prompt in provider.prompts]
    assert None not in recus
    assert len(set(recus)) == 2, recus
    assert vise in recus
    assert provider.tenus_pendant == [True, True]
    # La ligne est dans la section « La machine », pas ailleurs.
    for prompt in provider.prompts:
        assert prompt.index(TITRE_MACHINE) < prompt.index("Ton port d'essai")
    # Rendus à la fin de la tâche.
    for port in recus:
        assert port is not None
        with port_d_essai(tirage=_Tirage([port], ensuite=False), essais=1) as rendu:
            assert rendu == port


def test_un_agent_qui_travaille_ailleurs_ne_recoit_aucun_port_du_poste() -> None:
    """Le conteneur du mode isolé : un port de ce poste ne lui servirait à rien."""
    provider = _Temoin(sur_le_poste=False)

    asyncio.run(AgentRuntime(provider, DEVELOPER_PROFILE).execute("t", machine=MACHINE))

    assert "port d'essai" not in provider.prompts[0]


def test_sans_port_libre_la_tache_part_sans_ligne(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une tâche ne s'arrête pas faute de port d'essai : le cadre lui dit d'en choisir un."""

    def aucun(**_: Any) -> Any:
        raise PortIndisponible("aucun port d'essai libre")

    monkeypatch.setattr("maestro.agents.runtime.port_d_essai", aucun)
    provider = _Temoin()

    asyncio.run(AgentRuntime(provider, DEVELOPER_PROFILE).execute("t", machine=MACHINE))

    assert MACHINE.consigne() in provider.prompts[0]
    assert "port d'essai" not in provider.prompts[0]
