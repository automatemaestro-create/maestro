"""Ce que le lanceur demande au système d'exploitation — et rien d'autre (#640).

Tout ce qui touche au poste vit ici : les ports, les process, les sondes HTTP, le
navigateur, l'horloge. Le reste du lanceur (`maestro.lanceur.lanceur`) n'appelle
jamais `subprocess`, `socket` ni `urllib` directement : il reçoit un `Systeme`.

Deux raisons, et la seconde n'est pas du confort :

1. **La suite doit pouvoir exercer le lanceur** sans ouvrir de port, sans lancer de
   process et sans attendre une seconde de plus que nécessaire — `tests/conftest.py`
   (#195) exige qu'aucun test n'ait besoin d'un backend. Un double de `Systeme` suffit.
2. **Les gestes dépendants de la plateforme sont rassemblés**, donc lisibles d'un
   seul coup d'œil. Un lanceur qui doit tourner sur un poste Windows d'utilisateur,
   sur un Mac et dans le conteneur Linux du filet n'a pas le droit de les éparpiller.

`Systeme` est une classe ordinaire dont les méthodes **sont** l'implémentation réelle :
un test n'en surcharge que ce qu'il éprouve, et ce qu'il oublie de surcharger se voit
tout de suite (un vrai appel système dans un test est une panne franche, pas un
silence).
"""

from __future__ import annotations

import os
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: Sur Windows, `os.kill(pid, 0)` ne sonde pas : il **termine** le process (CPython
#: traduit tout signal autre que Ctrl-C/Ctrl-Break en `TerminateProcess`). La vitalité
#: s'y lit donc par le code de sortie du handle, que `GetExitCodeProcess` rend à
#: `STILL_ACTIVE` tant que le process tourne.
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: `OpenProcess` échoue ainsi quand le pid n'existe plus — tout autre échec (accès
#: refusé, par exemple) dit au contraire qu'il y a bien quelqu'un.
_ERROR_INVALID_PARAMETER = 87


class Processus:
    """Un service que le lanceur vient de démarrer : son pid, et de quoi le suivre.

    `code()` rend `None` tant qu'il tourne, son code de sortie sinon. C'est ce qui
    permet à l'attente de s'arrêter **à la mort** du service plutôt qu'au bout du
    délai : un service mort ne répondra jamais, et le dire tout de suite est le
    troisième critère du ticket.

    Une classe plutôt qu'un `NamedTuple` autour du `Popen` : la suite a besoin d'un
    service qui meurt, et elle l'obtient en surchargeant `code()` — sans process.
    """

    def __init__(self, pid: int, process: subprocess.Popen[bytes] | None = None) -> None:
        self.pid = pid
        self._process = process

    def code(self) -> int | None:
        if self._process is None:
            return None
        return self._process.poll()


class Sortie:
    """Où le lanceur parle : la sortie standard, et l'erreur pour ce qui bloque.

    Une classe plutôt qu'un `print` direct, pour la raison qui vaut pour `Systeme` :
    la suite lit ce que le lanceur a dit, sans capturer un flux global.

    ⚠ **Ce que le lanceur écrit ne doit ni casser ni se changer en mojibake.** Il parle
    français, avec des accents et quelques signes (« · », « ⚠ »), et il parle à trois
    destinataires qui n'ont pas le même encodage : une console Windows héritée
    (cp1252, qui **lève** sur ces signes-là et tuerait la commande au dernier moment),
    un terminal moderne, et un tube — journal, coque, installeur. D'où deux régimes :
    sur un terminal on garde son encodage en remplaçant ce qu'il ne sait pas écrire,
    ailleurs on force l'UTF-8, qui est la règle du dépôt pour ce qui voyage (#141).
    """

    def __init__(self) -> None:
        for flux in (sys.stdout, sys.stderr):
            reconfigurer = getattr(flux, "reconfigure", None)
            if reconfigurer is None:
                continue
            try:
                if flux.isatty():
                    reconfigurer(errors="replace")
                else:
                    reconfigurer(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # flux déjà détourné par un appelant
                continue

    def dire(self, message: str = "") -> None:
        print(message)

    def alerter(self, message: str) -> None:
        print(message, file=sys.stderr)


class Systeme:
    """Le poste, vu par le lanceur. Surchargeable méthode par méthode dans la suite."""

    # ------------------------------------------------------------------ les ports

    def port_tenu(self, hote: str, port: int) -> bool:
        """Quelqu'un tient-il ce port ?

        **Deux témoins, et l'un suffit** : le port refuse qu'on s'y attache (ce qui
        est la question qu'on se pose pour démarrer un serveur), ou bien quelqu'un
        accepte une connexion dessus (ce qui reste vrai quand l'écoute est sur une
        autre adresse de la même machine). `SO_REUSEADDR` n'est **pas** posé : sous
        POSIX, il laisserait justement réussir un attachement que la présence d'un
        autre serveur devrait refuser.

        ⚠ **Sonder ne doit rien laisser derrière soi.** La connexion d'essai est donc
        fermée par `SO_LINGER` à zéro, c'est-à-dire d'un coup sec : une fermeture
        ordinaire laisserait le couple d'adresses en `TIME_WAIT` pendant une minute,
        et son port source — pris dans la plage éphémère — rendrait « occupé » un port
        voisin que personne n'utilise.
        """
        if not self.attachable(hote, port):
            return True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as prise:
            prise.settimeout(0.5)
            prise.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            try:
                prise.connect((hote, port))
            except OSError:
                return False
        return True

    def port_libre_depuis(self, hote: str, souhaite: int, essais: int = 50) -> int | None:
        """Le premier port **attachable** à partir de `souhaite`, ou `None` s'il n'y en a pas.

        En montant, et non au hasard : deux stacks lancées coup sur coup atterrissent
        alors sur des ports voisins et prévisibles, ce qui se retient et se retrouve.

        ⚠ **Le seul témoin est ici l'attachement**, là où `port_tenu` en a deux, et
        c'est la question posée qui le veut : « où puis-je écouter ? » et non « y a-t-il
        quelqu'un ? ». Le second témoin serait ici pire qu'inutile — il **ouvre une
        connexion par port sondé**, chacune consommant un port source de la plage
        éphémère ; balayer cette plage-là revenait alors à déclarer occupé, l'un après
        l'autre, les ports que la sonde précédente venait de prendre. Mesuré en
        écrivant ce module : six ports de suite « occupés » par la seule trace du
        balayage, et un `None` là où tout était libre.
        """
        for decalage in range(essais):
            port = souhaite + decalage
            if port > 65535:
                return None
            if self.attachable(hote, port):
                return port
        return None

    def attachable(self, hote: str, port: int) -> bool:
        """Peut-on s'attacher à ce port ? La question exacte qu'un serveur se pose.

        Publique et non privée : c'est l'une des deux questions que le lanceur pose au
        poste (l'autre étant « y a-t-il quelqu'un ? »), et les deux autres méthodes de
        port s'appuient dessus — un double qui la joue les joue toutes.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as prise:
            try:
                prise.bind((hote, port))
            except OSError:
                return False
        return True

    # --------------------------------------------------------------- les process

    def demarrer(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        journal: Path,
        environ: Mapping[str, str],
    ) -> Processus:
        """Lance un service, sa sortie versée dans `journal`, détaché de nous.

        **Détaché** parce que le lanceur rend la main : le service doit survivre à la
        fin de cette commande, et le terminal qu'on ferme ne doit pas l'emporter. Les
        drapeaux sont ceux, déjà éprouvés, de `maestro.controltower.hote_detache` —
        une console propre et sans fenêtre plus un groupe à soi sous Windows,
        `start_new_session` sous POSIX. Le groupe n'est pas un détail : c'est lui qui
        rend l'arrêt **complet** (voir `eteindre`).

        **Le journal repart à zéro** à chaque démarrage, et c'est une décision : la
        cause d'une mort se lit dans ses dernières lignes, et un journal qui s'empile
        ferait citer comme cause le succès du démarrage précédent (mesuré ici même).
        Un service, une vie, un journal.
        """
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("wb") as flux:
            process = subprocess.Popen(  # noqa: S603 - argv construit ici, aucun shell
                list(argv),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=flux,
                stderr=subprocess.STDOUT,
                env=dict(environ),
                **_detachement(),
            )
        return Processus(process.pid, process)

    def vivant(self, pid: int) -> bool:
        """Ce pid désigne-t-il encore un process vivant ?"""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            return _vivant_windows(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Il existe, il ne nous appartient pas — ce qui est déjà une réponse.
            return True
        except OSError:
            return False
        return True

    def ligne_de_commande(self, pid: int) -> str | None:
        """La ligne de commande de ce pid, ou `None` si elle n'est pas lisible.

        C'est le **témoin d'identité** de l'arrêt : un pid seul ment (il se recycle,
        la leçon est déjà payée par #456), et tuer l'arbre d'un inconnu est exactement
        ce que « rien n'est tué au jugé » (#213) interdit. Illisible n'est pas
        « divergent » : l'appelant s'abstient alors de conclure, et se rabat sur le
        port.
        """
        if sys.platform == "linux":
            try:
                brut = Path(f"/proc/{pid}/cmdline").read_bytes()
            except OSError:
                return None
            return brut.replace(b"\0", b" ").decode("utf-8", "replace").strip() or None
        if sys.platform == "win32":
            requete = (
                f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}"'
                " | Select-Object -ExpandProperty CommandLine"
            )
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", requete]
        else:
            argv = ["ps", "-p", str(pid), "-o", "command="]
        try:
            rendu = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
                argv,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if rendu.returncode != 0:
            return None
        return rendu.stdout.strip() or None

    def eteindre(self, pid: int) -> None:
        """Éteint ce process **et sa descendance** — jamais lui seul.

        Un front lancé par `next start` est père d'au moins un ouvrier, et l'API est
        mère des hôtes de run qu'elle n'a pas encore soldés : un `terminate()` sur le
        père laisserait travailler des enfants que plus rien ne nomme. Deux gestes,
        un par plateforme, tous deux adressés au **groupe** que `demarrer` a créé.
        """
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603 - argv fixe, aucun shell
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return

    # ------------------------------------------------------------------- le réseau

    def sonder(self, url: str, delai: float) -> bool:
        """`url` répond-elle ? Un 404 ou un 500 **répondent** : c'est un serveur vivant.

        Ce qu'on attend d'une sonde de démarrage est « quelqu'un écoute et parle
        HTTP », pas « la page est bonne » — juger le contenu ici ferait échouer le
        démarrage sur une route absente.
        """
        try:
            with urllib.request.urlopen(url, timeout=delai):  # noqa: S310 - boucle locale
                return True
        except urllib.error.HTTPError:
            return True
        except (OSError, urllib.error.URLError):
            return False

    def poster(self, url: str, delai: float) -> str | None:
        """POST sans corps, la réponse rendue telle quelle — ou `None` si personne n'a parlé."""
        requete = urllib.request.Request(url, data=b"", method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(requete, timeout=delai) as reponse:  # noqa: S310
                return str(reponse.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as erreur:
            return str(erreur.read().decode("utf-8", "replace"))
        except (OSError, urllib.error.URLError):
            return None

    def ouvrir_navigateur(self, url: str) -> bool:
        """Ouvre l'URL dans le navigateur **par défaut du poste**.

        Rien de plus : ni profil jetable, ni fenêtre isolée, ni surveillance de sa
        fermeture. Ces trois-là sont l'affaire du développement (`start.sh`) et du
        mode bureau (la coque, dont la fenêtre *est* la surface et dont la fermeture
        *est* le geste d'arrêt). Ici, l'utilisateur regarde son produit dans son
        navigateur, et l'arrête par le même geste qu'il l'a lancé.
        """
        try:
            return webbrowser.open(url)
        except Exception:  # noqa: BLE001 - un navigateur absent n'est jamais fatal
            return False

    # ------------------------------------------------------------------ le temps

    def dormir(self, secondes: float) -> None:
        time.sleep(secondes)

    def horloge(self) -> float:
        """Pour mesurer une attente : monotone, donc insensible à l'heure du poste."""
        return time.monotonic()

    def maintenant(self) -> float:
        """Pour **inscrire** une heure que relira un autre process : celle du mur.

        Une horloge monotone n'a de sens que dans le process qui la lit, et la session
        inscrite est faite pour être relue d'ailleurs — « démarrée depuis 412 s » ne
        veut rien dire pour qui vient d'ouvrir un terminal.
        """
        return time.time()


def _detachement() -> dict[str, Any]:
    """Ce qui coupe un service du cycle de vie du lanceur, par plateforme.

    Copie assumée de `maestro.controltower.hote_detache._detachement` : le besoin est
    le même (un fils qui survit à son père et reste tuable en bloc), et le geste s'y
    lit avec la mesure qui l'a établi (#469). L'importer d'ici ferait dépendre le
    lanceur du module qui lance les runs, c'est-à-dire du SDK et de tout ce qu'il
    tire — pour deux drapeaux.
    """
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        }
    return {"start_new_session": True}


def _vivant_windows(pid: int) -> bool:
    """Vitalité d'un pid sous Windows, sans jamais risquer de le tuer."""
    import ctypes

    # `windll` n'existe que sous Windows, et cette fonction est lue par le typage des
    # deux côtés (le filet joue dans un conteneur Linux) : on passe par le module vu
    # comme `Any`, plutôt que par un `type: ignore` qui serait inutile ici et
    # nécessaire là-bas — donc faux quelque part à tous les coups.
    module: Any = ctypes
    kernel32 = module.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return bool(kernel32.GetLastError() != _ERROR_INVALID_PARAMETER)
    code = ctypes.c_ulong()
    obtenu = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    if not obtenu:
        return True
    return bool(code.value == _STILL_ACTIVE)
