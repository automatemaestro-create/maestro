"""Le veilleur n'adopte jamais un évadé qui n'a pas fini de naître (#1367).

Le 2026-09-27, tous les python du poste sont morts deux fois à la même seconde. La
cause, établie sur le poste le 2026-09-28 (voir l'en-tête de `maestro.sandbox.arbre`) :
le veilleur rangeait dans un job un python du Store **pendant** l'activation de son
paquet ; l'activation échouait, et le système détruisait le conteneur AppX que
partagent tous les python du Store du poste.

Ce qui est éprouvé ici, sur de vrais processus Windows — un `ping.exe` membre du job,
un second `ping.exe` né **suspendu** hors du job, l'état où l'activation laisse un
évadé :

- le lecteur d'état (`_suspendus`) voit un processus suspendu, puis relâché ;
- un évadé suspendu n'est pas adopté, et l'est dès qu'il est relâché ; un suspendu
  plus ancien que son parent déclaré (pid recyclé) n'est pas même retenu ;
- son parent mort reste tenu tant qu'il est en activation — sans quoi il ne serait
  plus reconnaissable au tour suivant ;
- l'arrêt l'attend, puis l'arrête une fois né ;
- celui qui ne naît pas n'est jamais touché tant que son créateur vit — le tuer
  pendant l'activation détruit aussi le conteneur — ; son créateur mort et
  l'échéance passée, c'est un reste, achevé à l'arrêt comme pendant la veille.

La filiation seule est simulée — la ligne `(évadé, membre)` ajoutée à l'instantané du
poste : faire naître un vrai évadé suspendu exigerait l'alias du Store, c'est-à-dire
le geste qui tue tous les python du poste. Le reste est le vrai chemin : le job, le
port, le fil de veille, l'adoption et l'arrêt.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from maestro.sandbox import arbre as module_arbre
from maestro.sandbox.arbre import Arbre

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="l'activation des paquets et le veilleur n'existent que sous Windows",
)

#: `CREATE_SUSPENDED` : l'état d'un évadé que l'activation de son paquet n'a pas fini
#: de faire naître.
SUSPENDU = 0x00000004

#: Un exécutable natif, sans paquet ni alias : il reste dans le job qui le porte.
PING = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE")
DORMIR = [PING, "-n", "60", "127.0.0.1"]


def _relacher(process: subprocess.Popen[bytes]) -> None:
    handle = ctypes.c_void_p(int(process._handle))  # type: ignore[attr-defined]
    assert ctypes.WinDLL("ntdll").NtResumeProcess(handle) == 0


def _dans_nos_jobs(veilleur, pid: int) -> bool:
    return any(pid in module_arbre._pids_du_job(job) for job in veilleur._jobs)


def _attendre(condition, delai_s: float = 5.0) -> bool:
    echeance = time.monotonic() + delai_s
    while not condition():
        if time.monotonic() >= echeance:
            return False
        time.sleep(0.05)
    return True


@pytest.fixture
def arbre() -> Iterator[Arbre]:
    """Un membre du job, veillé — celui dont l'évadé se dira l'enfant."""
    lance = Arbre.lancer(DORMIR, veiller=True)
    assert lance._veilleur is not None, "le système a refusé le veilleur"
    yield lance
    lance.arreter()
    lance.fermer()


@pytest.fixture
def evade(arbre: Arbre, monkeypatch) -> Iterator[subprocess.Popen[bytes]]:
    """Un processus né suspendu hors du job, que l'instantané dit enfant du membre.

    Né **après** le membre : le veilleur, qui écarte un enfant plus ancien que son
    parent (pid recyclé), le reconnaît bien comme sien.
    """
    process = subprocess.Popen(  # noqa: S603 - argv fixe
        DORMIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUSPENDU | subprocess.CREATE_NO_WINDOW,
    )
    parent = arbre.process.pid
    instantane = module_arbre._instantane
    monkeypatch.setattr(
        module_arbre,
        "_instantane",
        lambda: [(pid, parent if pid == process.pid else ppid) for pid, ppid in instantane()],
    )
    yield process
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def test_un_processus_ne_suspendu_se_lit_suspendu_puis_relache():
    process = subprocess.Popen(  # noqa: S603 - argv fixe
        DORMIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUSPENDU | subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert module_arbre._suspendus([process.pid, os.getpid()]) == {process.pid}
        _relacher(process)
        assert _attendre(lambda: not module_arbre._suspendus([process.pid]))
    finally:
        process.kill()
        process.wait(timeout=10)
    # Un pid mort n'est pas suspendu : il n'est plus dans l'instantané.
    assert module_arbre._suspendus([process.pid]) == set()
    assert module_arbre._suspendus([]) == set()


def test_un_evade_suspendu_n_est_pas_adopte_puis_l_est_une_fois_relache(arbre, evade):
    veilleur = arbre._veilleur

    # Le conhost du membre, évadé vivant, peut être adopté à ce tour ; pas l'évadé suspendu.
    veilleur.adopter_les_evades()
    # Trois balayages du fil de veille : aucun ne l'a rangé dans un job.
    time.sleep(3 * module_arbre.PERIODE_VEILLE_S)
    assert not _dans_nos_jobs(veilleur, evade.pid)
    assert veilleur._en_activation.get(evade.pid) == arbre.process.pid
    # Pas encore adopté, mais de l'arbre : il est nommé.
    assert evade.pid in {p.pid for p in arbre.membres()}

    _relacher(evade)

    assert _attendre(lambda: _dans_nos_jobs(veilleur, evade.pid)), "jamais adopté une fois relâché"
    # Le relevé est échangé à la fin de la passe qui l'adopte.
    assert _attendre(lambda: evade.pid not in veilleur._en_activation)


def test_un_suspendu_plus_ancien_que_son_parent_n_est_pas_retenu(monkeypatch):
    """Un pid recyclé : le suspendu né avant le membre n'est pas son enfant."""
    ancien = subprocess.Popen(  # noqa: S603 - argv fixe
        DORMIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUSPENDU | subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(0.05)
    lance = Arbre.lancer(DORMIR, veiller=True)
    try:
        parent = lance.process.pid
        instantane = module_arbre._instantane
        monkeypatch.setattr(
            module_arbre,
            "_instantane",
            lambda: [(pid, parent if pid == ancien.pid else ppid) for pid, ppid in instantane()],
        )
        veilleur = lance._veilleur

        # Le conhost du membre, évadé légitime et vivant, peut être adopté ; pas l'ancien.
        veilleur.adopter_les_evades()
        assert ancien.pid not in veilleur._en_activation
        assert not _dans_nos_jobs(veilleur, ancien.pid)
        assert ancien.pid not in {p.pid for p in lance.membres()}
    finally:
        lance.arreter()
        lance.fermer()
        ancien.kill()
        ancien.wait(timeout=10)


def test_un_etat_illisible_compte_pour_suspendu(arbre, evade, monkeypatch):
    """Dans le doute, on attend : adopter à tort peut tuer tous les python du poste."""
    # Illisible **avant** d'être relâché : le fil de veille ne l'a jamais vu né.
    monkeypatch.setattr(module_arbre, "_suspendus", lambda pids: None)
    _relacher(evade)

    arbre._veilleur.adopter_les_evades()
    time.sleep(3 * module_arbre.PERIODE_VEILLE_S)
    assert not _dans_nos_jobs(arbre._veilleur, evade.pid)
    assert evade.pid in arbre._veilleur._en_activation


def test_le_parent_mort_reste_tenu_tant_que_son_enfant_est_en_activation(arbre, evade):
    veilleur = arbre._veilleur
    membre = arbre.process.pid
    assert _attendre(lambda: evade.pid in veilleur._en_activation)

    arbre.process.kill()
    arbre.process.wait(timeout=10)
    # La mort est lue sur le port puis soldée au balayage suivant — sauf ici.
    time.sleep(3 * module_arbre.PERIODE_VEILLE_S)
    assert membre in veilleur._tenus, "le parent lâché : son enfant ne serait plus reconnu"

    _relacher(evade)

    assert _attendre(lambda: _dans_nos_jobs(veilleur, evade.pid)), "l'orphelin n'a pas été adopté"
    assert _attendre(lambda: membre not in veilleur._tenus), "le parent n'a jamais été lâché"


def test_l_arret_attend_l_evade_en_activation_puis_l_arrete(arbre, evade):
    assert _attendre(lambda: evade.pid in arbre._veilleur._en_activation)
    relache = threading.Timer(0.5, _relacher, args=(evade,))
    relache.start()

    debut = time.monotonic()
    arbre.arreter()
    duree = time.monotonic() - debut
    relache.join()

    assert evade.wait(timeout=10) is not None
    assert duree < module_arbre.ATTENTE_ACTIVATION_S


def test_tant_que_son_createur_vit_l_evade_suspendu_n_est_jamais_touche(arbre, evade, monkeypatch):
    """L'échéance passée, son créateur peut encore le relâcher : ni rangé, ni tué."""
    monkeypatch.setattr(module_arbre, "ATTENTE_ACTIVATION_S", 0.3)
    assert _attendre(lambda: evade.pid in arbre._veilleur._en_activation)

    # Bien au-delà de l'échéance : plusieurs balayages du fil de veille.
    time.sleep(1.0)

    assert evade.poll() is None, "un évadé suspendu dont le créateur vit a été tué"
    assert module_arbre._suspendus([evade.pid]) == {evade.pid}
    assert not _dans_nos_jobs(arbre._veilleur, evade.pid)
    assert evade.pid in {p.pid for p in arbre.membres()}


def test_l_arret_acheve_l_evade_que_son_createur_mort_ne_relachera_plus(
    arbre, evade, monkeypatch
):
    """Le reste d'un `bash` tué avant de relâcher son enfant ne survit pas à la tâche (#1279)."""
    monkeypatch.setattr(module_arbre, "ATTENTE_ACTIVATION_S", 0.5)
    assert _attendre(lambda: evade.pid in arbre._veilleur._en_activation)
    membres = arbre.membres()
    assert evade.pid in {p.pid for p in membres}

    arbre.arreter()

    assert evade.wait(timeout=10) is not None
    assert arbre.survivants(membres, delai_s=0.5) == ()


def test_un_evade_ne_trop_recemment_n_est_pas_acheve_meme_vu_depuis_longtemps(
    arbre, evade, monkeypatch
):
    """Un pid recyclé entre deux regards hérite de l'ancienneté vue : la naissance tranche."""
    monkeypatch.setattr(module_arbre, "ATTENTE_ACTIVATION_S", 3.0)
    veilleur = arbre._veilleur
    assert _attendre(lambda: evade.pid in veilleur._en_activation)
    with veilleur._verrou:
        veilleur._attente_depuis[evade.pid] = time.monotonic() - 100.0
    arbre.process.kill()
    arbre.process.wait(timeout=10)

    with veilleur._verrou:
        assert veilleur._achever_les_abandonnes() == 0
    time.sleep(3 * module_arbre.PERIODE_VEILLE_S)
    assert evade.poll() is None, "un évadé né il y a moins de l'échéance a été tué"


def test_la_veille_acheve_l_abandonne_puis_lache_son_createur(arbre, evade, monkeypatch):
    monkeypatch.setattr(module_arbre, "ATTENTE_ACTIVATION_S", 0.3)
    veilleur = arbre._veilleur
    membre = arbre.process.pid
    assert _attendre(lambda: evade.pid in veilleur._en_activation)

    arbre.process.kill()
    arbre.process.wait(timeout=10)

    assert _attendre(lambda: evade.poll() is not None), "l'abandonné n'a jamais été achevé"
    assert _attendre(lambda: membre not in veilleur._tenus), "son créateur n'a jamais été lâché"
