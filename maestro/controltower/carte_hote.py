"""La carte d'un hôte de run — qui le porte, et si ce process existe encore (#1503).

Le battement (#348) répond à « l'hôte s'est-il tu ? » par l'**âge** de son dernier
signal, et le seuil d'orphelinat est fait pour un hôte *vivant mais muet* : une
tâche peut travailler 26 min sans rien publier, donc on attend 30 min avant de
conclure. Ce seuil ne sait rien d'un hôte **tué** — tous les `python.exe` du poste
arrêtés d'un coup, le 2026-10-14 (#1502) : pendant une demi-heure, l'écran disait
« En cours » d'un run que plus aucun process ne portait, et proposait une pause et
une reprise qui n'atteignaient personne.

Ce qui manquait n'était pas un seuil plus court, c'était un **fait** : *quel*
process porte ce run. La carte le dit — son pid, sa machine et sa naissance —, et
l'API, qui la relit, constate elle-même que ce process n'existe plus. Ce n'est pas
une déduction de plus sur des horodatages, c'est une observation, sur le modèle de
la carte du pilote (#213) et de l'occupant d'un atelier (#1455).

**La carte ne peut qu'avancer le verdict `orphelin`, jamais le retirer.** Le seul
cas où elle parle est celui où la mort est **certaine** (`hote_eteint`) :

- le pid ne désigne plus aucun process sur **cette** machine ;
- ou il en désigne un **autre** — né à un autre instant que celui de la carte : le
  numéro a été recyclé, l'hôte est mort.

Tout le reste se tait et laisse la règle du seuil juger, exactement comme avant :
une carte illisible, une carte d'une **autre machine** (un Redis partagé n'oblige
pas l'hôte à vivre sur le poste de l'API, et un pid n'a de sens que sur la sienne),
une naissance qu'on ne sait pas lire, un process qui tourne. On se trompe du côté
qui ne détruit rien — la même conclusion que le seuil (#348) : déclarer orphelin un
run vivant ferait proposer de le reprendre, donc de solder un travail en cours.

**Qui écrit la carte : le process qui déroule le run, et lui seul.** L'hôte détaché
(#443) et `maestro-run --publier` la posent à chaque battement (`batteur_redis`) —
« un hôte bat, et dit qui il est » ; l'API ne la pose que pour les runs qu'elle
déroule elle-même (`ServiceExecutions._derouler`, l'hôte en process), parce que son
propre cœur bat aussi les runs détachés qu'elle tient : sa carte y désignerait un
process qui n'est pas leur hôte. La carte s'efface avec le battement, au soldage
(`RegistreBattements.oublier`, `oublieur_redis`) — jamais quand l'hôte s'arrête,
pour la raison même qui garde le battement : c'est le fait qu'on veut lire.

La sonde (`sonder`) est écrite ici et non empruntée aux autres du dépôt
(`maestro.sandbox.ramassage.pid_vivant`, `maestro.lanceur.systeme`) parce que la
question n'est pas la même : elles répondent oui/non et rangent un doute du côté qui
convient à *leur* geste — un accès refusé y vaut « mort » pour l'une, « vivant »
pour l'autre. Ici le doute doit rester un doute (`SONDE_INCONNUE`), et la naissance
voyage avec la réponse, sans quoi un pid recyclé passerait pour l'hôte.
"""

from __future__ import annotations

import functools
import json
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Les trois réponses de la sonde : le process est **mort** (certain), il **tourne**
#: (mais ce n'est peut-être pas l'hôte : voir la naissance), ou on ne sait pas.
SONDE_MORT = "mort"
SONDE_VIVANT = "vivant"
SONDE_INCONNUE = "inconnue"

#: Windows : un process qui n'est pas encore sorti rend ce code de sortie.
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: `OpenProcess` échoue ainsi quand le pid n'existe plus ; tout autre échec (accès
#: refusé) dit au contraire qu'il y a quelqu'un — donc un doute, pas une mort.
_ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True, slots=True)
class CarteHote:
    """L'identité du process qui porte un run : pid, machine, naissance.

    `naissance` est l'instant de création du process tel que le système le tient —
    le `FILETIME` de `GetProcessTimes` sous Windows, le `starttime` de
    `/proc/<pid>/stat` sous Linux —, comparé **brut**, jamais converti : une
    conversion serait une seconde source de désaccord. `None` quand le système ne la
    donne pas (macOS) ; la carte vaut alors pour le seul pid, et un pid recyclé n'y
    est pas démasqué — on retombe sur le seuil, côté prudent.
    """

    pid: int
    machine: str
    naissance: int | None = None

    def vers_texte(self) -> str:
        """La forme rangée dans le registre — du JSON, lisible à l'œil dans Redis."""
        return json.dumps(
            {"pid": self.pid, "machine": self.machine, "naissance": self.naissance},
            ensure_ascii=False,
        )

    @classmethod
    def depuis_texte(cls, brut: str | None) -> CarteHote | None:
        """Relit une carte — `None` sur l'absence comme sur l'illisible, jamais une levée."""
        if not brut:
            return None
        try:
            data: Any = json.loads(brut)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        pid, machine, naissance = data.get("pid"), data.get("machine"), data.get("naissance")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        if not isinstance(machine, str) or not machine:
            return None
        if not isinstance(naissance, int) or isinstance(naissance, bool):
            naissance = None
        return cls(pid=pid, machine=machine, naissance=naissance)


@dataclass(frozen=True, slots=True)
class Sonde:
    """Ce que le système dit d'un pid : `etat` (l'une des `SONDE_*`) et sa naissance."""

    etat: str
    naissance: int | None = None


Sondeur = Callable[[int], Sonde]


def machine_courante() -> str:
    """Le nom de ce poste — celui qu'une carte écrite ici portera."""
    return socket.gethostname()


def carte_de_ce_process() -> CarteHote:
    """La carte du process qui l'appelle — jamais une levée (naissance `None` au pire)."""
    pid = os.getpid()
    try:
        naissance = sonder(pid).naissance
    except Exception:  # noqa: BLE001 - une carte sans naissance vaut mieux que pas de carte
        naissance = None
    return CarteHote(pid=pid, machine=machine_courante(), naissance=naissance)


def hote_eteint(
    brut: str | None,
    *,
    sonde: Sondeur | None = None,
    machine: str | None = None,
) -> bool:
    """L'hôte que cette carte désigne est-il **certainement** éteint ?

    `True` dans deux cas, et deux seulement : le pid ne désigne plus aucun process
    de cette machine, ou il désigne un process né à un autre instant que l'hôte —
    le numéro a été recyclé. Tout le reste rend `False`, qui ne veut **pas** dire
    « vivant » mais « la carte ne permet pas de conclure » : le verdict revient
    alors à la règle du seuil (cf. l'en-tête du module).

    Jamais une levée : la question est posée à chaque lecture de la liste des runs,
    et une sonde qui casse ne doit pas faire tomber l'écran qu'on regarde quand
    quelque chose ne va pas.
    """
    carte = CarteHote.depuis_texte(brut)
    if carte is None or carte.machine != (machine or machine_courante()):
        return False
    try:
        lu = (sonde or sonder)(carte.pid)
    except Exception:  # noqa: BLE001 - un doute, jamais une mort
        return False
    if lu.etat == SONDE_MORT:
        return True
    if lu.etat == SONDE_VIVANT and carte.naissance is not None and lu.naissance is not None:
        return lu.naissance != carte.naissance
    return False


def sonder(pid: int) -> Sonde:
    """Ce pid désigne-t-il un process de cette machine, et né quand ? — sans jamais le toucher."""
    if pid <= 0:
        return Sonde(SONDE_MORT)
    if sys.platform == "win32":
        return _sonder_windows(pid)
    return _sonder_posix(pid)


def _sonder_posix(pid: int) -> Sonde:
    """`/proc/<pid>/stat` quand il existe : l'état (un zombie est mort) et `starttime`.

    Le champ `comm` peut contenir espaces et parenthèses : on lit **après la
    dernière** `)`. `starttime` est le 22ᵉ champ de la ligne, donc le 20ᵉ après
    `comm`. Sans procfs (macOS), le signal 0 dit seulement si le pid existe.
    """
    procfs = Path("/proc/self/stat").exists()
    if procfs:
        try:
            ligne = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return Sonde(SONDE_MORT)
        except OSError:
            return Sonde(SONDE_INCONNUE)
        champs = ligne.rpartition(")")[2].split()
        if not champs:
            return Sonde(SONDE_INCONNUE)
        if champs[0] in ("Z", "X", "x"):
            return Sonde(SONDE_MORT)
        naissance = int(champs[19]) if len(champs) > 19 and champs[19].isdigit() else None
        return Sonde(SONDE_VIVANT, naissance)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return Sonde(SONDE_MORT)
    except PermissionError:
        return Sonde(SONDE_VIVANT)
    except OSError:
        return Sonde(SONDE_INCONNUE)
    return Sonde(SONDE_VIVANT)


@functools.cache
def _kernel32() -> Any:
    """`kernel32`, chargé une fois, ses signatures posées — un HANDLE a la taille d'un pointeur.

    `use_last_error` : le code d'échec d'`OpenProcess` est ce qui sépare « ce pid
    n'existe plus » d'« on n'a pas le droit de le regarder », et ctypes peut le
    perdre entre l'appel et sa lecture sans cette précaution.
    """
    import ctypes
    from ctypes import wintypes

    # Vu comme `Any` : `WinDLL` n'existe que sous Windows, et ce fichier est typé
    # aussi sous Linux (le filet joue dans un conteneur) — même parti pris que
    # `maestro.lanceur.systeme._vivant_windows`.
    module: Any = ctypes
    kernel32 = module.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _sonder_windows(pid: int) -> Sonde:
    """`OpenProcess` + `GetExitCodeProcess` + `GetProcessTimes` — `os.kill` y tuerait."""
    import ctypes
    from ctypes import wintypes

    module: Any = ctypes
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        if module.get_last_error() == _ERROR_INVALID_PARAMETER:
            return Sonde(SONDE_MORT)
        return Sonde(SONDE_INCONNUE)
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return Sonde(SONDE_INCONNUE)
        if code.value != _STILL_ACTIVE:
            return Sonde(SONDE_MORT)
        creation, sortie, noyau, usager = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(sortie),
            ctypes.byref(noyau),
            ctypes.byref(usager),
        ):
            return Sonde(SONDE_VIVANT)
        return Sonde(SONDE_VIVANT, (creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)
