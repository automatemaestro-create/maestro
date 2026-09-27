"""Un processus et tout ce qu'il lancera — de quoi l'arrêter d'un seul geste (#1160, #1279).

Deux lecteurs, une seule mécanique : la vérification des commandes d'un projet
(`maestro.sandbox.verification`, #1160), qui arrête une commande au bout de son
délai **avec sa descendance**, et le confinement de la session d'un agent
(`maestro.sandbox.confinement`, #1279), qui arrête à la clôture d'une tâche tout ce
que son agent a lancé. Ce module vivait dans la première ; la seconde en avait
besoin telle quelle, et deux copies d'un Job Object sont deux façons de se tromper.

## Pourquoi un conteneur de processus, et pas l'arbre des parents

La leçon de #291 et de `maestro.controltower.hote_detache` : un `npm run dev` est le
père d'un `node` qui tient un port, et tuer le seul père le laisserait tourner.

⚠ **Sous Windows, `taskkill /T` ne suffit pas** — mesuré le 2026-09-24 : les
processus que Git Bash lance (`sleep 30 & sleep 30`) échappent à l'arbre des
parents Windows, parce que l'émulation de `fork`/`exec` de MSYS en rompt la
filiation ; ils survivaient à l'arrêt et tenaient la sortie ouverte. Et même quand
la filiation tient, elle meurt avec l'intermédiaire : le `msedge` de l'incident
#1279 avait pour parent un `bash.exe` déjà mort, que plus aucun arbre ne relie à
l'agent. Le processus naît donc **suspendu**, est placé dans un **Job Object** avant
son premier instant de vie, puis relâché : tout ce qu'il lancera y naît aussi —
quel que soit l'intermédiaire, et qu'il vive ou non —, et c'est le job qu'on
termine. Le job est créé « tué à la fermeture » : si le process qui le tient
mourait avant de l'arrêter, fermer sa poignée — ce que le système fait alors pour
lui — emporterait encore l'arbre.

Sous POSIX, le **groupe** de processus dont il est le chef (`start_new_session`)
fait le gros du même travail (`killpg`). Ce qui s'en échappe (`setsid`, un démon
qui se détache) reste un **descendant** de `racine` — et sous Linux, où le lanceur
du confinement se déclare *subreaper*, les orphelins lui reviennent au lieu de
partir chez `init` : l'arbre ne perd personne en route. Les deux ensembles sont
arrêtés.

## Ce qui sort du job sans le demander : les alias d'application (Windows)

⚠ Mesuré le 2026-09-26 en écrivant #1279 : un processus du job qui lance un **alias
d'exécution d'application** — `…\\WindowsApps\\…\\python.exe`, le `python` qu'installe
le Microsoft Store, et donc celui du PATH sur bien des postes — fait naître un
processus **hors du job**, et que `TerminateJobObject` n'atteint pas : l'activation
d'un paquet est faite pour lui par le système. Aucun drapeau ne l'a demandé (le
redirecteur d'un venv lance son interpréteur avec des drapeaux nuls), et aucun
réglage du job ne l'empêche. Un `python -m http.server &` d'agent y survivait.

L'évadé garde pourtant son **parent** — le membre du job qui l'a lancé
(`ParentProcessId`, mesuré). D'où le **veilleur** (`_Veilleur`, demandé par
`veiller=True`) : le job lui signale par un port de complétion chaque naissance et
chaque mort ; il **tient une poignée** sur chaque membre — tant qu'elle est tenue,
le pid ne peut pas être recyclé, et « enfant de ce pid » ne désigne que lui —, et il
**adopte** tout enfant né hors du job dans un job à lui, tué à la fermeture, où naît
à son tour ce que l'adopté lancera. Il regarde à chaque balayage — serré pendant
l'activité du job, espacé au repos (`PERIODE_VEILLE_S`) —, avant de lâcher la
poignée d'un membre mort (l'évadé qui lui survit devient un orphelin : tant que le
pid du mort est tenu, on le sait encore rattaché), et à l'arrêt. Mesuré : sans lui,
le `python` du Store lancé en arrière-plan par `bash` survivait à l'arrêt ; avec
lui, il est adopté, nommé et arrêté.

Ce qui reste hors d'atteinte, et se dit : un évadé qui meurt **avant** d'avoir été
vu en laissant lui-même un orphelin — plus rien ne relie celui-ci à l'arbre.

## Nommer ce qu'on arrête, et ce qui résiste

Le confinement doit **dire** ce qu'il a arrêté et, surtout, ce qu'il n'a pas pu
arrêter (#1279) : `membres()` rend les processus de l'arbre avec leur nom, relevé
**avant** l'arrêt (après, il n'y a plus rien à interroger), et `survivants()` ceux
qui vivent encore une fois le délai écoulé. Un processus zombie est mort : il ne
tient plus ni port ni fichier, il attend seulement qu'on le recueille.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

#: La période du balayage du veilleur (Windows) **pendant l'activité** du job — une
#: naissance ou une mort depuis moins de `FENETRE_ACTIVITE_S` : ce qui est né hors du
#: job d'un membre encore vivant est adopté au plus tard après ce délai. Au repos, le
#: balayage s'espace (`PERIODE_REPOS_S`). Un instantané des processus du poste coûte
#: ~8 ms pour 400 processus (mesuré le 2026-09-26) : balayer sans cesse coûterait
#: une part de cœur à chaque session d'agent, pour des évadés qui naissent presque
#: tous dans les secondes qui suivent le lancement d'une commande.
PERIODE_VEILLE_S = 0.2
PERIODE_REPOS_S = 2.0
FENETRE_ACTIVITE_S = 5.0


@dataclass(frozen=True)
class ProcessusNomme:
    """Un processus de l'arbre : son nom d'image et son pid.

    `nom` est le nom de l'exécutable (`msedge.exe`, `node`, `sleep`), jamais sa
    ligne de commande : une ligne de commande porte des chemins et parfois des
    secrets, et ce qui suffit à reconnaître un processus au gestionnaire des tâches
    est son nom et son pid. Vide quand le système ne l'a pas dit.
    """

    nom: str
    pid: int

    def __str__(self) -> str:
        return f"{self.nom or 'processus'} (PID {self.pid})"

    def to_dict(self) -> dict[str, Any]:
        return {"nom": self.nom, "pid": self.pid}

    @classmethod
    def from_dict(cls, brut: Mapping[str, Any]) -> ProcessusNomme:
        return cls(nom=str(brut.get("nom") or ""), pid=int(brut.get("pid") or 0))


class Arbre:
    """Un processus et tout ce qu'il lancera — de quoi l'arrêter d'un seul geste.

    Sous POSIX, le **groupe** dont il est le chef (`start_new_session`) et les
    descendants de `racine` ; sous Windows, un **Job Object** où il naît suspendu,
    et, quand il est demandé, le **veilleur** qui adopte ce qui s'en évade (voir
    l'en-tête du module).
    """

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        job: int | None,
        *,
        racine: int | None = None,
        veilleur: Any = None,
    ) -> None:
        self.process = process
        self._job = job
        self._racine = process.pid if racine is None else racine
        self._veilleur = veilleur

    @classmethod
    def lancer(
        cls,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin: int | IO[Any] | None = subprocess.DEVNULL,
        stdout: int | IO[Any] | None = subprocess.PIPE,
        stderr: int | IO[Any] | None = subprocess.STDOUT,
        console_propre: bool = True,
        racine: int | None = None,
        veiller: bool = False,
    ) -> Arbre:
        """Lance `argv`, déjà rangé dans son arbre.

        Les flux par défaut sont ceux d'une commande dont on lit la sortie
        (`maestro.sandbox.verification`) : entrée fermée, sorties mêlées dans un
        tube. `None` laisse le processus hériter du flux de l'appelant.

        `console_propre` (Windows) fait naître le processus sans fenêtre et dans un
        groupe à lui — ce qu'il faut à une commande jouée pour vérification. Faux,
        il partage la console de l'appelant, exactement comme un processus lancé
        sans drapeau : c'est le régime du CLI d'un agent, que le SDK lançait ainsi.

        `racine` (POSIX) est le processus dont les **descendants** sont aussi
        arrêtés — par défaut le processus lancé ; le lanceur du confinement y met
        son propre pid, parce qu'il recueille les orphelins (voir l'en-tête).

        `veiller` (Windows) arme le veilleur qui adopte ce qui sort du job par un
        alias d'application (voir l'en-tête). Un veilleur que le système refuse
        laisse le job seul, qui reste l'arrêt nominal.
        """
        if sys.platform != "win32":
            process = subprocess.Popen(  # noqa: S603 - argv construit par l'appelant
                list(argv),
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=None if env is None else dict(env),
                start_new_session=True,
            )
            return cls(process, None, racine=racine)
        else:
            # Le `else` explicite : mypy n'écarte une branche de plateforme que dans
            # un `if`/`else`, jamais après un `return` — sous Linux, ces noms n'existent pas.
            drapeaux = _CREATE_SUSPENDED
            if console_propre:
                drapeaux |= subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(  # noqa: S603 - argv construit par l'appelant
                list(argv),
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=None if env is None else dict(env),
                creationflags=drapeaux,
            )
            try:
                job, veilleur = _ranger_puis_reprendre(process.pid, veiller=veiller)
            except OSError:
                # Un processus qu'on ne peut pas relâcher ne rendrait jamais la main :
                # il est arrêté tout de suite, et l'appelant en fait un verdict.
                process.kill()
                process.wait()
                raise
            return cls(process, job, racine=racine, veilleur=veilleur)

    @property
    def confine(self) -> bool:
        """L'arbre est-il tenu par un conteneur de processus (job ou groupe) ?

        Faux seulement sous Windows quand le système a refusé le job : l'arrêt
        retombe alors sur l'arbre des parents, faute de mieux.
        """
        return sys.platform != "win32" or self._job is not None

    def membres(self) -> tuple[ProcessusNomme, ...]:
        """Les processus de l'arbre qui vivent encore, avec leur nom — jamais l'appelant."""
        soi = os.getpid()
        if sys.platform == "win32":
            if self._veilleur is not None:
                pids: list[int] = self._veilleur.pids()
            elif self._job is not None:
                pids = _pids_du_job(self._job)
            else:
                pids = [self.process.pid] if self.process.poll() is None else []
            return tuple(ProcessusNomme(_nom_windows(pid), pid) for pid in pids if pid != soi)
        table = _table_posix()
        return tuple(
            ProcessusNomme(table[pid][2], pid)
            for pid in sorted(_membres_posix(table, self.process.pid, self._racine))
            if pid != soi
        )

    def arreter(self) -> None:
        """Arrête le processus **et sa descendance** — jamais lui seul (#291)."""
        if sys.platform != "win32":
            cibles = set(_membres_posix(_table_posix(), self.process.pid, self._racine))
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            for pid in cibles - {os.getpid()}:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    continue
            return
        else:
            if self._veilleur is not None:
                self._veilleur.arreter()
                return
            if self._job is not None and _terminer_job(self._job):
                return
            # Sans job (refusé par le système) : l'arbre des parents, faute de mieux.
            subprocess.run(  # noqa: S603 - argv fixe, aucun shell
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    def survivants(
        self, parmi: Iterable[ProcessusNomme], *, delai_s: float = 2.0
    ) -> tuple[ProcessusNomme, ...]:
        """Ceux de `parmi` qui vivent encore au bout de `delai_s` — la question d'après `arreter`.

        On attend parce qu'un arrêt n'est pas instantané : un processus en pleine
        écriture disque meurt quand le noyau le laisse faire. Sous Windows, un
        membre d'un job qui l'a quitté est mort ; sous POSIX, un zombie compte pour
        mort (voir l'en-tête).
        """
        restants = tuple(parmi)
        echeance = time.monotonic() + max(delai_s, 0.0)
        while True:
            restants = tuple(p for p in restants if self._vit(p.pid))
            if not restants or time.monotonic() >= echeance:
                return restants
            time.sleep(0.05)

    def _vit(self, pid: int) -> bool:
        if sys.platform == "win32":
            if self._veilleur is not None:
                return bool(self._veilleur.vit(pid))
            if self._job is not None:
                return pid in _pids_du_job(self._job)
            return _vivant_windows(pid)
        return _vivant_posix(pid)

    def fermer(self) -> None:
        """Rend les poignées et le tube de sortie — ce qui vit encore meurt avec."""
        if self.process.stdout is not None:
            self.process.stdout.close()
        if sys.platform == "win32":
            if self._veilleur is not None:
                self._veilleur.fermer()
                self._veilleur = None
            if self._job is not None:
                _fermer_poignee(self._job)
                self._job = None


# --------------------------------------------------------------------- POSIX


def _table_posix() -> dict[int, tuple[int, int, str]]:
    """`pid → (ppid, pgid, nom)` de tous les processus visibles, zombies exclus.

    `/proc` sous Linux — il existe même dans l'image minimale d'un conteneur, là
    où `ps` peut manquer —, `ps` ailleurs (macOS). Une table illisible est vide :
    l'arrêt retombe alors sur le seul groupe, qui reste le cas nominal.
    """
    racine_proc = Path("/proc")
    if racine_proc.is_dir():
        return _table_proc(racine_proc)
    try:
        sortie = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,comm="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[int, tuple[int, int, str]] = {}
    for ligne in sortie.splitlines():
        champs = ligne.split(None, 4)
        if len(champs) < 5 or champs[3].startswith("Z"):
            continue
        try:
            table[int(champs[0])] = (int(champs[1]), int(champs[2]), Path(champs[4]).name)
        except ValueError:
            continue
    return table


def _table_proc(racine_proc: Path) -> dict[int, tuple[int, int, str]]:
    table: dict[int, tuple[int, int, str]] = {}
    for entree in racine_proc.iterdir():
        if not entree.name.isdigit():
            continue
        try:
            stat = (entree / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # « pid (comm) état ppid pgrp … » : le nom peut contenir des espaces et des
        # parenthèses, d'où la découpe sur la **dernière** parenthèse fermante.
        fin_nom = stat.rfind(")")
        champs = stat[fin_nom + 2 :].split()
        if len(champs) < 3 or champs[0] == "Z":
            continue
        try:
            table[int(entree.name)] = (int(champs[1]), int(champs[2]), _nom_proc(entree, stat))
        except ValueError:
            continue
    return table


def _nom_proc(entree: Path, stat: str) -> str:
    """Le nom de l'exécutable : le premier argument de sa ligne de commande, à défaut `comm`."""
    try:
        premier = (entree / "cmdline").read_bytes().split(b"\0", 1)[0]
    except OSError:
        premier = b""
    if premier:
        return Path(premier.decode("utf-8", errors="replace")).name
    return stat[stat.find("(") + 1 : stat.rfind(")")]


def _membres_posix(table: Mapping[int, tuple[int, int, str]], chef: int, racine: int) -> set[int]:
    """Le groupe de `chef` et les descendants de `racine` (et de `chef`), à tout degré."""
    membres = {pid for pid, (_, pgid, _) in table.items() if pgid == chef}
    enfants: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        enfants.setdefault(ppid, []).append(pid)
    pile = [racine, chef]
    vus: set[int] = set()
    while pile:
        parent = pile.pop()
        if parent in vus:
            continue
        vus.add(parent)
        for enfant in enfants.get(parent, ()):
            membres.add(enfant)
            pile.append(enfant)
    if chef in table:
        membres.add(chef)
    return membres


def _vivant_posix(pid: int) -> bool:
    """Vivant, et pas zombie : un zombie ne tient plus rien, il attend d'être recueilli."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.parent.is_dir():
        try:
            texte = stat.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        champs = texte[texte.rfind(")") + 2 :].split()
        return not champs or champs[0] != "Z"
    return pid in _table_posix()


# ------------------------------------------------------------------- Windows

#: `CREATE_SUSPENDED` : le processus naît sans avoir exécuté une instruction, le
#: temps d'être rangé dans son job.
_CREATE_SUSPENDED = 0x00000004

#: `STILL_ACTIVE`, le code de sortie d'un processus qui tourne encore.
_STILL_ACTIVE = 259

#: Combien de pids la liste d'un job peut rendre d'un coup. Un navigateur headless
#: en compte une dizaine ; au-delà, la liste est tronquée — ce qui ne change rien à
#: l'arrêt (c'est le job qu'on termine), seulement à ce qui est nommé.
_PIDS_MAX = 1024

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    #: `JobObjectBasicProcessIdList`, `JobObjectAssociateCompletionPortInformation`,
    #: `JobObjectExtendedLimitInformation` et `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
    _LISTE_DES_PROCESSUS = 3
    _PORT_ASSOCIE = 7
    _INFO_LIMITES_ETENDUES = 9
    _TUER_A_LA_FERMETURE = 0x00002000

    #: Ce que le job écrit sur son port : un processus y est né, en est sorti.
    _MSG_NOUVEAU = 6
    _MSG_SORTI = 7
    _MSG_SORTI_EN_ERREUR = 8

    #: La clé que le veilleur poste pour s'arrêter — aucune clé de job ne la porte.
    _CLE_FIN = 0xFFFF

    #: `WAIT_TIMEOUT` : le port n'a rien dit pendant la période — l'heure de balayer.
    _ATTENTE_ECOULEE = 258

    #: Les droits qu'il faut sur le processus : le ranger dans un job
    #: (`PROCESS_SET_QUOTA | PROCESS_TERMINATE`), puis le relâcher
    #: (`PROCESS_SUSPEND_RESUME`).
    _DROITS = 0x0100 | 0x0001 | 0x0800

    #: `PROCESS_QUERY_LIMITED_INFORMATION` : lire le nom et le code de sortie, rien de plus.
    _DROITS_LECTURE = 0x1000

    #: Ce que le veilleur tient sur chaque processus : le lire, l'adopter, le tuer,
    #: et — c'est l'essentiel — garder son pid hors du recyclage tant qu'il le tient.
    _DROITS_TENUE = _DROITS_LECTURE | 0x0100 | 0x0001

    _TH32CS_SNAPPROCESS = 0x00000002
    _POIGNEE_INVALIDE = ctypes.c_void_p(-1).value

    class _CompteursIo(ctypes.Structure):
        _fields_ = [
            (nom, ctypes.c_ulonglong)
            for nom in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _LimitesDeBase(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _LimitesEtendues(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _LimitesDeBase),
            ("IoInfo", _CompteursIo),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _ListeDesProcessus(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * _PIDS_MAX),
        ]

    class _PortDuJob(ctypes.Structure):
        _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", wintypes.HANDLE)]

    class _EntreeProcessus(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.IsProcessInJob.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    )
    _kernel32.IsProcessInJob.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    )
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CreateIoCompletionPort.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_size_t,
        wintypes.DWORD,
    )
    _kernel32.CreateIoCompletionPort.restype = wintypes.HANDLE
    _kernel32.GetQueuedCompletionStatus.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
    )
    _kernel32.GetQueuedCompletionStatus.restype = wintypes.BOOL
    _kernel32.PostQueuedCompletionStatus.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
    )
    _kernel32.PostQueuedCompletionStatus.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_EntreeProcessus))
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_EntreeProcessus))
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    def _ranger_puis_reprendre(pid: int, *, veiller: bool = False) -> tuple[int | None, Any]:
        """Range le processus suspendu `pid` dans un job, puis le relâche — rend job et veilleur.

        Le job est `None` si le système le refuse (l'arrêt retombera alors sur
        `taskkill /T`), le veilleur aussi quand il n'est pas demandé ou pas
        possible. Lève `OSError` si le processus ne peut pas être relâché, seul cas
        où il ne rendrait jamais la main. Le veilleur est armé **avant** le premier
        instant de vie du processus : rien de ce qu'il lance ne naît sans être vu.
        """
        processus = _kernel32.OpenProcess(_DROITS, False, pid)
        if not processus:
            raise OSError(ctypes.get_last_error(), "le processus n'a pas pu être relâché")
        veilleur: _Veilleur | None = None
        try:
            job = _job_pour(processus)
            if job is not None and veiller:
                try:
                    veilleur = _Veilleur(job, pid)
                except OSError:
                    veilleur = None
            if _ntdll.NtResumeProcess(processus) != 0:
                if veilleur is not None:
                    veilleur.fermer()
                if job is not None:
                    _kernel32.CloseHandle(job)
                raise OSError("le processus n'a pas pu être relâché")
            return job, veilleur
        finally:
            _kernel32.CloseHandle(processus)

    def _job_pour(processus: int) -> int | None:
        """Un job « tué à la fermeture » où `processus` est rangé — `None` si refusé."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limites = _LimitesEtendues()
        limites.BasicLimitInformation.LimitFlags = _TUER_A_LA_FERMETURE
        pose = _kernel32.SetInformationJobObject(
            job, _INFO_LIMITES_ETENDUES, ctypes.byref(limites), ctypes.sizeof(limites)
        )
        if not pose or not _kernel32.AssignProcessToJobObject(job, processus):
            _kernel32.CloseHandle(job)
            return None
        return int(job)

    def _pids_du_job(job: int) -> list[int]:
        """Les pids des processus que le job porte encore — vide si illisible."""
        liste = _ListeDesProcessus()
        lu = _kernel32.QueryInformationJobObject(
            job, _LISTE_DES_PROCESSUS, ctypes.byref(liste), ctypes.sizeof(liste), None
        )
        if not lu:
            return []
        nombre = min(int(liste.NumberOfProcessIdsInList), _PIDS_MAX)
        return [int(liste.ProcessIdList[i]) for i in range(nombre)]

    def _nom_windows(pid: int) -> str:
        """Le nom de l'image du processus (`msedge.exe`) — vide si le système le tait."""
        processus = _kernel32.OpenProcess(_DROITS_LECTURE, False, pid)
        if not processus:
            return ""
        try:
            taille = wintypes.DWORD(1024)
            tampon = ctypes.create_unicode_buffer(taille.value)
            if not _kernel32.QueryFullProcessImageNameW(
                processus, 0, tampon, ctypes.byref(taille)
            ):
                return ""
            return Path(tampon.value).name
        finally:
            _kernel32.CloseHandle(processus)

    def _vivant_windows(pid: int) -> bool:
        """Vitalité d'un pid, par le code de sortie de sa poignée — sans jamais le tuer."""
        processus = _kernel32.OpenProcess(_DROITS_LECTURE, False, pid)
        if not processus:
            return False
        try:
            return _poignee_vivante(processus)
        finally:
            _kernel32.CloseHandle(processus)

    def _poignee_vivante(processus: int) -> bool:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(processus, ctypes.byref(code)):
            return True
        return int(code.value) == _STILL_ACTIVE

    def _naissance(processus: int) -> int | None:
        """L'instant de création du processus (FILETIME), `None` si illisible."""
        creation, sortie, noyau, usager = (ctypes.c_ulonglong() for _ in range(4))
        if not _kernel32.GetProcessTimes(
            processus,
            ctypes.byref(creation),
            ctypes.byref(sortie),
            ctypes.byref(noyau),
            ctypes.byref(usager),
        ):
            return None
        return int(creation.value)

    def _instantane() -> list[tuple[int, int]]:
        """`(pid, ppid)` de chaque processus du poste — vide si le système refuse."""
        cliche = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not cliche or cliche == _POIGNEE_INVALIDE:
            return []
        try:
            entree = _EntreeProcessus()
            entree.dwSize = ctypes.sizeof(_EntreeProcessus)
            lignes: list[tuple[int, int]] = []
            encore = _kernel32.Process32FirstW(cliche, ctypes.byref(entree))
            while encore:
                lignes.append((int(entree.th32ProcessID), int(entree.th32ParentProcessID)))
                encore = _kernel32.Process32NextW(cliche, ctypes.byref(entree))
            return lignes
        finally:
            _kernel32.CloseHandle(cliche)

    def _terminer_job(job: int) -> bool:
        """Termine tout ce que le job porte — vrai si le système l'a fait."""
        return bool(_kernel32.TerminateJobObject(job, 1))

    def _fermer_poignee(job: int) -> None:
        _kernel32.CloseHandle(job)

    class _Veilleur:
        """Suit tout ce qui naît dans les jobs de l'arbre, et adopte ce qui s'en évade.

        Voir l'en-tête du module. Trois états, sous un seul verrou :

        - `_jobs` : le job de l'arbre, puis un job par adopté — tous « tués à la
          fermeture », tous rattachés au même port de complétion ;
        - `_tenus` : `pid → poignée` de chaque processus de ces jobs encore
          vivant, ou mort mais pas encore **soldé** (ses évadés cherchés). Tenir la
          poignée, c'est interdire au système de recycler le pid : un processus
          dont le parent déclaré est un pid tenu, et né après lui, est bien son
          enfant ;
        - `_isoles` : les évadés que le système a refusé d'adopter, tués un à un
          à l'arrêt.

        Le fil de veille lit le port : une naissance est tenue, une mort est
        soldée ; entre deux messages, il balaie. Le job de l'arbre appartient à
        `Arbre` ; les jobs d'adoption et le port appartiennent au veilleur.
        """

        def __init__(self, job: int, racine: int) -> None:
            self._verrou = threading.RLock()
            self._jobs: list[int] = [job]
            self._tenus: dict[int, int] = {}
            self._isoles: dict[int, int] = {}
            self._morts: set[int] = set()
            port = _kernel32.CreateIoCompletionPort(_POIGNEE_INVALIDE, None, 0, 1)
            if not port:
                raise OSError(ctypes.get_last_error(), "port de complétion refusé")
            self._port = int(port)
            if not self._associer(job):
                _kernel32.CloseHandle(self._port)
                raise OSError(ctypes.get_last_error(), "port refusé par le job")
            self.tenir(racine)
            self._fil: threading.Thread | None = threading.Thread(
                target=self._veiller, name="maestro-veilleur", daemon=True
            )
            self._fil.start()

        def _associer(self, job: int) -> bool:
            lien = _PortDuJob(ctypes.c_void_p(len(self._jobs)), self._port)
            return bool(
                _kernel32.SetInformationJobObject(
                    job, _PORT_ASSOCIE, ctypes.byref(lien), ctypes.sizeof(lien)
                )
            )

        # ------------------------------------------------------ le fil de veille

        def _veiller(self) -> None:
            """Lit le port ; balaie à chaque échéance — serrée pendant l'activité du job.

            Un instantané du poste coûte de l'ordre de la milliseconde par centaine de
            processus : on ne balaie donc ni à chaque message, ni sans cesse. Une mort
            n'est pas soldée sur-le-champ mais au balayage suivant, avec les autres —
            sa poignée tenue garde son pid, donc ses orphelins restent reconnaissables
            d'ici là.
            """
            message = wintypes.DWORD()
            cle = ctypes.c_size_t()
            porteur = ctypes.c_void_p()
            activite = time.monotonic()
            balayage = activite + PERIODE_VEILLE_S
            while True:
                attente_ms = max(0, int((balayage - time.monotonic()) * 1000))
                recu = _kernel32.GetQueuedCompletionStatus(
                    self._port,
                    ctypes.byref(message),
                    ctypes.byref(cle),
                    ctypes.byref(porteur),
                    attente_ms,
                )
                if recu and cle.value == _CLE_FIN:
                    return
                if not recu and ctypes.get_last_error() != _ATTENTE_ECOULEE:
                    return  # port fermé sous le fil : plus rien à veiller
                try:
                    if recu:
                        activite = time.monotonic()
                        pid = int(porteur.value or 0)
                        if message.value == _MSG_NOUVEAU:
                            self.tenir(pid)
                        elif message.value in (_MSG_SORTI, _MSG_SORTI_EN_ERREUR):
                            with self._verrou:
                                self._morts.add(pid)
                    if time.monotonic() >= balayage:
                        self._balayer()
                        actif = time.monotonic() - activite < FENETRE_ACTIVITE_S
                        balayage = time.monotonic() + (
                            PERIODE_VEILLE_S if actif else PERIODE_REPOS_S
                        )
                except Exception:  # noqa: BLE001 — la veille ne tombe jamais pour un message
                    continue

        def _arreter_la_veille(self) -> None:
            fil, self._fil = self._fil, None
            if fil is None:
                return
            _kernel32.PostQueuedCompletionStatus(self._port, 0, _CLE_FIN, None)
            fil.join(timeout=5)

        # ------------------------------------------------ tenir, solder, adopter

        def tenir(self, pid: int) -> None:
            """Tient `pid` — s'il est bien dans un de nos jobs (un pid recyclé ne l'est pas)."""
            with self._verrou:
                if pid in self._tenus or pid <= 0:
                    return
                poignee = _kernel32.OpenProcess(_DROITS_TENUE, False, pid)
                if not poignee:
                    return
                if not self._dans_nos_jobs(poignee):
                    _kernel32.CloseHandle(poignee)
                    return
                self._tenus[pid] = int(poignee)

        def _dans_nos_jobs(self, poignee: int) -> bool:
            for job in self._jobs:
                dedans = wintypes.BOOL()
                if _kernel32.IsProcessInJob(poignee, job, ctypes.byref(dedans)) and dedans:
                    return True
            return False

        def _balayer(self) -> None:
            """Adopte les évadés de tous les tenus, puis lâche les morts — un seul instantané.

            Un mort n'est lâché qu'**après** : ses évadés, devenus orphelins, ne sont
            reconnaissables que tant que son pid est tenu.
            """
            with self._verrou:
                self.adopter_les_evades()
                for pid in self._morts:
                    poignee = self._tenus.pop(pid, None)
                    if poignee is not None:
                        _kernel32.CloseHandle(poignee)
                self._morts.clear()

        def adopter_les_evades(self, parents: Iterable[int] | None = None) -> int:
            """Adopte les enfants de `parents` (tous les tenus par défaut) nés hors de nos jobs.

            Rend le nombre d'adoptés. Un enfant n'est retenu que s'il est né **après**
            son parent : c'est ce qui écarte un processus plus ancien qui porterait,
            comme parent déclaré, un pid recyclé depuis. Ses propres enfants nés avant
            l'adoption sont cherchés au tour suivant.
            """
            with self._verrou:
                cherches = set(self._tenus if parents is None else parents)
                adoptes = 0
                while cherches:
                    connus = self._connus()
                    nouveaux: set[int] = set()
                    for pid, ppid in _instantane():
                        if ppid not in cherches or pid in connus or pid in nouveaux:
                            continue
                        if self._adopter(pid, ppid):
                            nouveaux.add(pid)
                    adoptes += len(nouveaux)
                    cherches = nouveaux
                return adoptes

        def _connus(self) -> set[int]:
            connus = set(self._tenus) | set(self._isoles)
            for job in self._jobs:
                connus.update(_pids_du_job(job))
            return connus

        def _adopter(self, pid: int, ppid: int) -> bool:
            parent = self._tenus.get(ppid) or self._isoles.get(ppid)
            if parent is None:
                return False
            poignee = _kernel32.OpenProcess(_DROITS_TENUE, False, pid)
            if not poignee:
                return False
            ne_parent, ne_enfant = _naissance(parent), _naissance(poignee)
            if ne_parent is None or ne_enfant is None or ne_enfant < ne_parent:
                _kernel32.CloseHandle(poignee)
                return False
            job = _job_pour(poignee)
            if job is None:
                self._isoles[pid] = int(poignee)
                return True
            self._jobs.append(job)
            self._associer(job)
            self._tenus[pid] = int(poignee)
            return True

        # --------------------------------------------------- ce que l'arbre lit

        def pids(self) -> list[int]:
            """Les pids vivants de l'arbre — évadés compris, une fois adoptés."""
            with self._verrou:
                self.adopter_les_evades()
                vus: list[int] = []
                for job in self._jobs:
                    vus.extend(p for p in _pids_du_job(job) if p not in vus)
                vus.extend(
                    pid
                    for pid, poignee in self._isoles.items()
                    if pid not in vus and _poignee_vivante(poignee)
                )
                return vus

        def vit(self, pid: int) -> bool:
            with self._verrou:
                if any(pid in _pids_du_job(job) for job in self._jobs):
                    return True
                poignee = self._isoles.get(pid) or self._tenus.get(pid)
                return poignee is not None and _poignee_vivante(poignee)

        def arreter(self) -> None:
            """Termine chaque job et chaque isolé, puis ce que leurs morts laissent orphelin.

            La veille est arrêtée d'abord : les poignées des morts restent tenues,
            si bien que leurs orphelins sont encore reconnaissables au tour suivant.
            """
            self._arreter_la_veille()
            for _ in range(5):
                with self._verrou:
                    for job in self._jobs:
                        _terminer_job(job)
                    for poignee in self._isoles.values():
                        _kernel32.TerminateProcess(poignee, 1)
                    if not self.adopter_les_evades():
                        return

        def fermer(self) -> None:
            """Rend tout — ce qui vit encore dans un job d'adoption meurt avec lui."""
            self._arreter_la_veille()
            with self._verrou:
                for poignee in [*self._tenus.values(), *self._isoles.values()]:
                    _kernel32.CloseHandle(poignee)
                self._tenus.clear()
                self._isoles.clear()
                for job in self._jobs[1:]:
                    _kernel32.CloseHandle(job)
                del self._jobs[1:]
                _kernel32.CloseHandle(self._port)
