"""L'hôte de run détaché, éprouvé de bout en bout (#447, lot 6/6 du chantier #441).

Les cinq lots précédents ont livré sans tests, par convention ([docs/10
§5.1](../docs/10-workflow-git.md)) ; celui-ci les rattrape en un seul endroit, une
fois la bascule faite et le comportement stabilisé. Ce fichier existait déjà —
#445 y avait posé le **fail-safe** du canal humain, la seule logique du chantier
qu'on ne pouvait pas différer — et il devient ici la suite du chantier.

Ce qui est couvert, dans l'ordre où le module le construit :

① **le transport** — un ordre traverse la frontière sans rien perdre, et l'ordre
   relu est *le même* ; il est effacé dès sa lecture, parce qu'il porte l'objectif,
   c'est-à-dire du texte libre où un secret collé est plausible ;
② **le démarrage** — un hôte qui ne part pas le dit tout de suite
   (`DemarrageHoteRate`, avec son code et sa trace), un hôte lent est tenu pour
   parti, et un témoin périmé ne fait jamais dire « démarré » ;
③ **la survie** — le run ne meurt pas avec l'API. C'est *la* propriété du
   chantier, et elle se vérifie sur de **vrais process** : un process qui survit à
   un autre ne se simule pas (même parti pris que les tests d'arrêt de
   `tests/test_orchestrate.py`). Depuis #469 s'y ajoute ce que les drapeaux de
   `_detachement` achètent **de l'autre côté** : aucune fenêtre de console pour la
   descendance, et l'hôte hors de la console de son lanceur — la seconde étant la
   raison pour laquelle la première ne coûte pas l'isolation ;
④ **l'annulation** — elle traverse la frontière par le bus, jamais par un canal à
   elle : l'issue `annulee` *est* l'ordre, et un guet en panne n'emporte pas le run ;
⑤ **le canal humain** (#445) — les trois attentes tiennent sur le bus de ce
   process, et un bus refermé sans décision fait lever ou refuser, jamais approuver ;
⑥ **l'issue publiée en partant** (#446) — l'hôte dit comment il finit, et il le
   dit **exactement** comme l'hôte en process pour le même rapport : c'est le seul
   point où les deux frontières n'ont pas le droit de diverger ;
⑦ **le ramassage** — ce que l'hôte a vu mourir, rendu une fois, après un délai de
   grâce ; ce qu'il n'a pas vu ne s'invente pas.

**Aucun backend** ([docs/10 §8](../docs/10-workflow-git.md)) : ni Redis, ni
Temporal, ni réseau, ni appel modèle. Le bus est un `InMemoryEventBus` ou un
double scripté, le moteur un double, le batteur une liste. Les process réels sont
des **bouchons** qui n'importent pas une ligne de `maestro` — ils s'arment, ils
battent dans un fichier, ils meurent : c'est leur *cycle de vie* qu'on observe, pas
le run qu'ils porteraient.

Trois précautions valent d'être lues avant de toucher à cette suite :

- **ce qui doit être simultané l'est par une barrière, jamais par un `sleep`**
  (#292) — deux hôtes qui vivent « en même temps » sous la même API est une course
  que la charge de la machine tranche autrement à chaque exécution ; chaque bouchon
  s'annonce donc et attend les autres, et une barrière qui **renonce** le dit
  (#313) au lieu de laisser un relevé pris trop tôt passer pour un verdict ;
- **un relevé par process, jamais un compteur partagé** (#313) : chaque bouchon
  écrit son propre battement dans son propre fichier ;
- **les process sont tués pour de bon**, et de deux façons parce qu'il y a deux
  situations : ceux dont on tient le `Popen` par le geste du module lui-même
  (`_eteindre`), ceux dont le lanceur est mort par leur PID — c'est tout ce qui
  reste d'un hôte qui a survécu à son API, et c'est justement ce que le test
  fabrique. Chaque bouchon porte en plus une **échéance** de vie : un oubli de
  ménage coûte quelques secondes, jamais un process qui survit à la suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

import maestro
from maestro.controltower import hote_detache
from maestro.controltower.brief import (
    ArbitreBriefControlTower,
    ArbitreClarificationControlTower,
)
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.hote import DemarrageHoteRate, OrdreRun
from maestro.controltower.hote_detache import HoteRunDetache
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_TERMINEE,
    ControlTowerState,
)
from maestro.controltower.validation import ValidateurControlTower
from maestro.engine import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_TERMINEE,
    RunReport,
    TaskResult,
)
from maestro.engine.brief import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    BriefRefuse,
    DemandeBrief,
    DemandeClarification,
)
from maestro.engine.guardrails import DemandeValidation
from maestro.orchestrator.errors import OrchestratorError
from maestro.orchestrator.schema import Brief
from maestro.references import ReferenceTicket

RUN = "run-detache"

#: Plafond d'attente d'un fait observable sur le disque — un hôte qui s'arme, son
#: battement suivant, sa mort. Généreux à dessein : ce qu'on attend arrive en
#: quelques dizaines de millisecondes quand tout va bien, et l'atteindre est
#: toujours l'échec d'un test, jamais une lenteur qu'on tolère.
DELAI_OBSERVATION_S = 30.0

#: Pas de la boucle d'observation. Assez fin pour ne rien faire attendre, assez
#: large pour qu'un test ne soit pas une rafale de `stat()`.
PAS_OBSERVATION_S = 0.02

#: Échéance de vie d'un process bouchon, en secondes. C'est le dernier filet du
#: ménage : quoi qu'il arrive au test qui l'a lancé, aucun bouchon ne survit à la
#: suite. Largement au-delà de ce qu'un test lui demande de vivre.
VIE_BOUCHON_S = 60.0

#: Les fichiers que le bouchon écrit et lit dans l'atelier de son run. Ils sont
#: **passés au bouchon** (cf. `_source_bouchon`) plutôt que recopiés des deux
#: côtés : un nom qui diverge donnerait un test vert pour la mauvaise raison.
FICHIER_BATTEMENT = "battement"
FICHIER_ARRET = "arret"
FICHIER_PID = "pid"

#: Le relevé de console du bouchon (#469) : la console qu'il a **pour lui**, et la
#: liste des process qui la partagent. Écrit à l'armement, avant tout petit-fils.
FICHIER_CONSOLE = "console.json"

#: Le petit-fils du bouchon (#469) — le tenant-lieu du `claude.exe` d'une tâche :
#: son pid, et le relevé de la console dont il hérite. `hwnd` non nul = fenêtre.
FICHIER_PETIT_FILS_PID = "petitfils.pid"
FICHIER_PETIT_FILS_CONSOLE = "petitfils.json"

#: Les drapeaux **d'avant #469**, gardés ici comme **témoin fautif** et nulle part
#: ailleurs : un test qui mesure une fenêtre doit d'abord prouver qu'il sait en
#: voir une, faute de quoi son verdict vert ne distingue pas « aucune fenêtre » de
#: « aucune mesure ». Ils ne sont pas importés du module — ils n'y sont plus.
DRAPEAUX_AVANT_469 = (
    (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    if sys.platform == "win32"
    else 0
)

#: Les deux marques de la barrière : celle qu'on pose en arrivant, celle qu'on pose
#: quand on **renonce** à l'attendre. La seconde n'est pas décorative — sans elle,
#: un relevé pris avant que tout le monde soit là passerait pour un verdict (#313).
SUFFIXE_ARRIVE = ".arrive"
SUFFIXE_BARRIERE_RATEE = ".barriere-ratee"

#: La racine du dépôt d'où vient le `maestro` que cette suite importe. Elle est
#: **imposée** aux sous-process par `PYTHONPATH` : un script lancé depuis un
#: worktree importerait sinon le `maestro` du clone principal (installation
#: éditable), c'est-à-dire une autre version que celle qu'on croit éprouver.
RACINE_DEPOT = Path(maestro.__file__).resolve().parents[1]


# ------------------------------------------------------------------ le bouchon

#: Le corps du process bouchon. `# <noms>` y est remplacé par les constantes
#: ci-dessus au moment de l'écrire : un seul endroit les définit.
_CORPS_BOUCHON = '''\
"""Un hôte de run bouchon : il s'arme, il bat, il meurt — jamais de moteur (#447).

Lancé par le **vrai** `HoteRunDetache` à la place de
`maestro.controltower.hote_detache`, parce que ce qu'on observe est le cycle de vie
d'un process et non le run qu'il porterait. Il n'importe donc rien de `maestro` —
ni Redis, ni fournisseur, ni boucle d'orchestration —, ce qui est aussi ce qui rend
son démarrage assez court pour qu'une suite en lance plusieurs.

Tout est réglé par l'environnement, et son relevé est **le sien** : un fichier par
process (#313), jamais un compteur partagé qu'une écriture perdue rendrait faux au
moment précis où deux hôtes vivent ensemble.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# <noms>


def releve_console():
    """La console de CE process : sa fenêtre, et qui la partage (#469).

    `GetConsoleWindow` rend 0 quand la console n'a pas de fenêtre — c'est tout le
    correctif — comme quand il n'y en a aucune ; `GetConsoleProcessList` sépare
    les deux, et c'est lui qui dit si l'hôte partage la console de son lanceur.
    """
    if sys.platform != "win32":
        return {"hwnd": 0, "partagee_avec": [], "pid": os.getpid()}
    import ctypes

    k = ctypes.windll.kernel32
    tampon = (ctypes.c_ulong * 64)()
    combien = k.GetConsoleProcessList(tampon, 64)
    return {
        "hwnd": int(k.GetConsoleWindow()),
        "partagee_avec": [int(tampon[i]) for i in range(combien)],
        "pid": os.getpid(),
    }


atelier = Path(sys.argv[1])
run_id = atelier.name

message = os.environ.get("BOUCHON_MESSAGE", "")
if message:
    print(message, file=sys.stderr)

code = os.environ.get("BOUCHON_CODE", "")
if code:
    # Le démarrage raté : on meurt **avant** le témoin. C'est le seul cas où le
    # lanceur doit lever au lieu de tenir le process pour parti.
    raise SystemExit(int(code))

echeance = time.monotonic() + float(os.environ["BOUCHON_VIE_S"])
battement = atelier / BATTEMENT
arret = atelier / ARRET

(atelier / PID).write_text(str(os.getpid()), encoding="utf-8")
(atelier / CONSOLE).write_text(json.dumps(releve_console()), encoding="utf-8")

petit_fils = os.environ.get("BOUCHON_PETIT_FILS", "")
if petit_fils:
    # Le tenant-lieu du `claude.exe` d'une tâche (#469) : une application
    # CONSOLE, lancée exactement comme le claude-agent-sdk la lance — par
    # `anyio.open_process`, donc SANS aucun creationflags, stdin/stdout sur des
    # pipes. C'est ce lancement-là qui réclamait une fenêtre.
    enfant = subprocess.Popen(
        [sys.executable, petit_fils, str(atelier / PETIT_FILS_CONSOLE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    (atelier / PETIT_FILS_PID).write_text(str(enfant.pid), encoding="utf-8")

# Armé : c'est sur ce témoin-là, et pas sur la naissance du process, que le
# lanceur rend la main. Il vient APRÈS le petit-fils : un test qui l'attend doit
# pouvoir compter sur le fait que la descendance est là.
(atelier / TEMOIN).write_text("", encoding="utf-8")

porte = os.environ.get("BOUCHON_BARRIERE", "")
if porte:
    # La barrière (#292) : chacun s'annonce, puis attend les autres. C'est la seule
    # façon de dire « ces hôtes ont vécu **ensemble** » ; un `sleep` dirait « ils
    # ont vécu », ce que la charge de la machine tranche autrement à chaque fois.
    attendus = int(os.environ["BOUCHON_BARRIERE_N"])
    entree = Path(porte)
    (entree / (run_id + ARRIVE)).write_text("", encoding="utf-8")
    while len(list(entree.glob("*" + ARRIVE))) < attendus:
        if time.monotonic() >= echeance:
            # Renoncer se **dit** (#313) : sans cette marque, un relevé pris avant
            # que tout le monde soit là passerait pour un verdict.
            (entree / (run_id + RATEE)).write_text("", encoding="utf-8")
            break
        time.sleep(0.02)

tour = 0
while time.monotonic() < echeance and not arret.exists():
    tour += 1
    battement.write_text(str(tour), encoding="utf-8")
    time.sleep(0.02)
'''


#: Le corps du petit-fils : le tenant-lieu du `claude.exe` d'une tâche (#469).
#:
#: Il n'a qu'un travail — dire quelle console il a reçue — et il n'importe rien de
#: `maestro` : ce qu'on éprouve est une règle de Windows sur la création de
#: process, pas une ligne de notre code. Il **vit** ensuite, parce qu'une fenêtre
#: se mesure sur un process vivant et qu'un `_eteindre` doit avoir une descendance
#: à emporter ; son échéance est celle du bouchon, même filet de ménage.
_CORPS_PETIT_FILS = '''\
"""Un `claude.exe` de tâche, en tenant-lieu : il dit sa console, puis il vit."""

import ctypes
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    import ctypes

    hwnd = int(ctypes.windll.kernel32.GetConsoleWindow())
    # `IsWindowVisible` vit dans user32 et non dans kernel32 : l'appeler sur le
    # mauvais module lève, et ne lèverait QUE dans le cas où hwnd est non nul,
    # c'est-à-dire dans le seul cas que ce relevé existe pour attraper.
    visible = int(ctypes.windll.user32.IsWindowVisible(hwnd)) if hwnd else 0
else:
    # POSIX n'a pas de fenêtre à ouvrir : le petit-fils n'y sert qu'à donner une
    # descendance à `_eteindre`, et son relevé n'a que son pid d'utile.
    hwnd, visible = 0, 0

Path(sys.argv[1]).write_text(
    json.dumps({"hwnd": hwnd, "visible": visible, "pid": os.getpid()}),
    encoding="utf-8",
)
time.sleep(float(os.environ.get("BOUCHON_VIE_S", "60")))
'''


#: Le corps du lanceur : une « API » minuscule, dont on veut la **mort**.
_CORPS_LANCEUR = '''\
"""Une « API » minuscule : elle confie N runs à l'hôte détaché, puis s'arrête (#447).

Elle n'existe que pour mourir. La propriété du chantier #441 — un run survit à
l'arrêt de l'API — ne s'observe qu'entre deux process : celui qui lance et celui
qui reste. Le premier est ici, et c'est le **vrai** `HoteRunDetache` qu'il emploie.
"""

import asyncio
import sys
from pathlib import Path

from maestro.controltower import hote_detache
from maestro.controltower.hote import OrdreRun

hote_detache.MODULE_HOTE = sys.argv[1]
hote = hote_detache.HoteRunDetache(atelier=Path(sys.argv[2]))


async def confier() -> None:
    for run_id in sys.argv[3:]:
        await hote.lancer(OrdreRun(run_id=run_id, objectif="Survivre a son API"))


asyncio.run(confier())
'''


def _source_bouchon() -> str:
    """Le bouchon, avec les noms de fichiers du test injectés en tête."""
    noms = "\n".join(
        f"{nom} = {valeur!r}"
        for nom, valeur in (
            ("TEMOIN", hote_detache.FICHIER_PRET),
            ("BATTEMENT", FICHIER_BATTEMENT),
            ("ARRET", FICHIER_ARRET),
            ("PID", FICHIER_PID),
            ("ARRIVE", SUFFIXE_ARRIVE),
            ("RATEE", SUFFIXE_BARRIERE_RATEE),
            ("CONSOLE", FICHIER_CONSOLE),
            ("PETIT_FILS_PID", FICHIER_PETIT_FILS_PID),
            ("PETIT_FILS_CONSOLE", FICHIER_PETIT_FILS_CONSOLE),
        )
    )
    return _CORPS_BOUCHON.replace("# <noms>", noms)


def _achever(pid: int) -> None:
    """Tue un process dont plus personne ne tient le `Popen` — **et sa descendance**.

    Les deux gestes de `hote_detache._eteindre`, adressés à un **PID** au lieu d'un
    `Popen` : c'est tout ce qui reste d'un hôte qui a survécu à son lanceur, et
    c'est exactement la situation que le test de survie fabrique. Ce n'est pas du
    ménage de confort — un bouchon oublié tournerait pendant le reste de la suite.
    """
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - argv fixe, aucun shell
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    with suppress(OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


def _attendre(predicat, message: str) -> None:
    """Attend qu'un fait devienne vrai, au plus `DELAI_OBSERVATION_S`.

    L'attente porte sur un **fait observable** (un fichier qui apparaît, un
    compteur qui avance), jamais sur une durée : c'est la différence entre un test
    qui dit ce qui s'est passé et un test que la charge de la machine tranche.
    """
    echeance = time.monotonic() + DELAI_OBSERVATION_S
    while time.monotonic() < echeance:
        if predicat():
            return
        time.sleep(PAS_OBSERVATION_S)
    raise AssertionError(message)


def _battement(atelier: Path, run_id: str) -> int:
    """Le relevé du bouchon de `run_id` — 0 tant qu'il n'a pas battu."""
    fichier = atelier / run_id / FICHIER_BATTEMENT
    try:
        return int(fichier.read_text(encoding="utf-8") or 0)
    except (OSError, ValueError):
        return 0


def _releve(atelier: Path, run_id: str, nom: str) -> dict[str, Any]:
    """Un relevé JSON écrit par le bouchon ou son petit-fils, attendu puis relu.

    L'attente est celle du reste de la suite — un **fait observable**, jamais une
    durée —, et la lecture échoue franchement : un relevé absent n'est pas un
    relevé vide, et le confondre rendrait vert un test qui n'a rien mesuré.
    """
    fichier = atelier / run_id / nom
    _attendre(fichier.exists, f"{run_id} n'a jamais écrit son relevé {nom}.")
    return json.loads(fichier.read_text(encoding="utf-8"))


def _process_de_ma_console() -> list[int]:
    """Les pids attachés à la console de CE process — vide s'il n'en a aucune.

    C'est la liste exacte que le système consulte pour dispatcher un événement de
    console (Ctrl-C, Ctrl-Break, fermeture de la fenêtre) : y être ou non *est* la
    question, et elle se pose depuis le lanceur, jamais depuis l'hôte.
    """
    if sys.platform != "win32":
        return []
    import ctypes

    k = ctypes.windll.kernel32
    tampon = (ctypes.c_ulong * 64)()
    combien = k.GetConsoleProcessList(tampon, 64)
    return [int(tampon[i]) for i in range(combien)]


def _vivant(pid: int) -> bool:
    """Ce pid **tourne**-t-il encore ? — et un zombie ne tourne pas.

    Sous Windows, `OpenProcess` + `GetExitCodeProcess` : `os.kill(pid, 0)` y **tue**
    au lieu d'interroger, ce qui ferait de la question sa propre réponse.

    Sous POSIX, `os.kill(pid, 0)` réussit encore sur un **zombie** — un process
    mort dont personne n'a lu le code de sortie —, et c'est exactement ce qu'on
    rencontre ici : le petit-fils est le fils de l'hôte, `_eteindre` les emporte
    tous les deux, et plus personne n'est là pour le récolter. Dans le conteneur du
    filet CI le PID 1 n'adopte ni ne récolte, si bien que la dépouille reste
    visible pour toujours et qu'un test qui interroge le signal 0 conclut « il a
    survécu » d'un process que le noyau donne pour mort. D'où la lecture de
    `/proc/<pid>/stat`, où l'état `Z` tranche ; le repli sur le signal 0 couvre les
    POSIX sans `/proc` (macOS), où le cas ne se pose pas de la même façon.

    Le champ `comm` de `stat` peut contenir des espaces et des parenthèses : l'état
    se lit **après la dernière** `)`, jamais en découpant sur les espaces.
    """
    if sys.platform != "win32":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True
        etat = stat.rpartition(")")[2].split()
        return bool(etat) and etat[0] != "Z"
    import ctypes

    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


@pytest.fixture()
def bouchon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Substitue le module du fils par un bouchon, et rend la racine des ateliers.

    Trois réglages, chacun pour sa raison. `MODULE_HOTE` est remplacé — c'est le
    seul point du lanceur qui nomme ce qu'il exécute, et le remplacer laisse *tout
    le reste* du vrai code en place. `PYTHONPATH` porte la racine du dépôt **et** le
    dossier du bouchon : la première parce qu'un sous-process lancé depuis un
    worktree importerait sinon le `maestro` du clone principal, la seconde pour que
    `python -m bouchon` trouve quelque chose. L'échéance de vie, enfin, est le filet
    de ménage — aucun bouchon ne survit à la suite, même si un test échoue avant sa
    ligne de nettoyage.

    Au démontage, on **achève ce qui reste** : on demande d'abord l'arrêt (le
    bouchon le relit à chaque tour), puis on tue par PID ce qui n'est pas parti.
    """
    (tmp_path / "bouchon.py").write_text(_source_bouchon(), encoding="utf-8")
    (tmp_path / "petit_fils.py").write_text(_CORPS_PETIT_FILS, encoding="utf-8")
    racine = tmp_path / "ateliers"
    racine.mkdir()
    monkeypatch.setattr(hote_detache, "MODULE_HOTE", "bouchon")
    monkeypatch.setenv("PYTHONPATH", f"{RACINE_DEPOT}{os.pathsep}{tmp_path}")
    monkeypatch.setenv("BOUCHON_VIE_S", str(VIE_BOUCHON_S))
    try:
        yield racine
    finally:
        for atelier in sorted(racine.glob("*")):
            with suppress(OSError):
                (atelier / FICHIER_ARRET).write_text("", encoding="utf-8")
        # Le petit-fils **avant** son père : un `taskkill /T` sur l'hôte l'emporte
        # déjà, mais l'ordre inverse est précisément ce qui fabrique l'orphelin
        # (#291) le jour où l'hôte est mort avant le démontage.
        motifs = (f"*/{FICHIER_PETIT_FILS_PID}", f"*/{FICHIER_PID}")
        for motif in motifs:
            for fichier in sorted(racine.glob(motif)):
                with suppress(OSError, ValueError):
                    _achever(int(fichier.read_text(encoding="utf-8")))


# ------------------------------------------------------------------- les doubles


def brief() -> Brief:
    """Le brief minimal que les deux arbitres transportent."""
    return Brief(
        objectif="Objectif",
        perimetre=("dedans",),
        hors_perimetre=("dehors",),
        criteres_acceptation=("fait",),
        questions=("laquelle ?",),
    )


def ordre(**surcharges: Any) -> OrdreRun:
    """Un ordre de run, en mode « humain » sauf mention contraire."""
    champs: dict[str, Any] = {
        "run_id": RUN,
        "objectif": "Objectif",
        "mode_brief": MODE_BRIEF_HUMAIN,
    }
    champs.update(surcharges)
    return OrdreRun(**champs)


def resultat(task_id: str, statut: str = STATUT_TERMINEE) -> TaskResult:
    """Un `TaskResult` minimal — le rapport ne sert ici qu'à compter les issues."""
    return TaskResult(
        task_id=task_id,
        titre=f"Tâche {task_id}",
        agent="developpeur",
        role="Développeur",
        competences_requises=("python",),
        score=1,
        statut=statut,
        sortie="ok" if statut == STATUT_TERMINEE else "",
        erreur=None if statut == STATUT_TERMINEE else "boum",
    )


class BusQuiSeReferme(InMemoryEventBus):
    """Un bus dont le flux se **tarit** : personne ne tranchera jamais.

    Même double que `tests/test_brief.py`, et pour la même raison :
    `InMemoryEventBus.close()` est un no-op assumé (seul un bus à connexions a
    quelque chose à libérer), donc c'est la fin de l'itération qu'il faut jouer,
    pas la fermeture d'une ressource.
    """

    async def subscribe(self):  # type: ignore[override]
        return
        yield  # pragma: no cover - fait de `subscribe` un générateur asynchrone


class BusScripte(InMemoryEventBus):
    """Un bus qui rend une suite d'événements **connue d'avance**, puis se tarit.

    C'est ce qui rend le guet éprouvable sans course : avec un vrai bus, il
    faudrait publier *après* que l'abonnement soit effectif, c'est-à-dire trancher
    un ordonnancement au lieu de tester une lecture.
    """

    def __init__(self, evenements) -> None:
        super().__init__()
        self._scenario = tuple(evenements)
        self.ferme = False

    async def subscribe(self):  # type: ignore[override]
        for evenement in self._scenario:
            yield evenement

    async def close(self) -> None:  # type: ignore[override]
        self.ferme = True


class BusQuiRefuse(InMemoryEventBus):
    """Un bus dont l'abonnement échoue — la panne qui ne doit pas emporter le run."""

    async def subscribe(self):  # type: ignore[override]
        raise RuntimeError("redis absent")
        yield  # pragma: no cover - fait de `subscribe` un générateur asynchrone


class MoteurCapture:
    """Un faux `OrchestrationEngine` qui retient ce qu'on lui a câblé.

    Le seul moyen d'observer le câblage sans lancer de run : `_derouler` construit
    le moteur puis part, et rien de ce qu'il lui passe ne ressort autrement.
    """

    cable: dict[str, Any] = {}

    @classmethod
    def default(cls, **kwargs: Any) -> MoteurCapture:
        cls.cable = dict(kwargs)
        return cls()

    async def run(self, objectif: str, **kwargs: Any) -> str:
        cls = type(self)
        cls.cable["run"] = {"objectif": objectif, **kwargs}
        return "rapport"


class MoteurScripte:
    """Un moteur qui rend le rapport qu'on lui donne, ou lève l'exception qu'on veut.

    Fabrique **et** moteur à la fois : `_derouler` appelle `OrchestrationEngine
    .default(...)`, donc un objet portant une méthode `default` suffit, et un run
    par process est tout ce dont ce module a besoin.
    """

    def __init__(
        self, *, rapport: RunReport | None = None, erreur: Exception | None = None
    ) -> None:
        self.rapport = rapport if rapport is not None else RunReport(objectif="", resultats=())
        self.erreur = erreur
        self.cable: dict[str, Any] = {}

    def default(self, **kwargs: Any) -> MoteurScripte:
        self.cable = dict(kwargs)
        return self

    async def run(self, objectif: str, **kwargs: Any) -> RunReport:
        self.cable["run"] = {"objectif": objectif, **kwargs}
        if self.erreur is not None:
            raise self.erreur
        return self.rapport


class MoteurQuiSeFaitAnnuler(MoteurScripte):
    """Un moteur qui publie l'ordre d'annulation **depuis le run**, puis attend.

    Publier depuis le run, et non à côté, est ce qui rend la course inoffensive
    sans un seul `sleep` : quand `run` s'exécute, le guet est forcément déjà
    abonné — c'est `_derouler` qui l'a voulu, en cédant la main avant de poser le
    témoin. Publier avant serait publier dans le vide.
    """

    def __init__(self, bus: InMemoryEventBus, *, rendre_la_main: bool = False) -> None:
        super().__init__()
        self._bus = bus
        self._rendre_la_main = rendre_la_main
        self.annule = False

    async def run(self, objectif: str, **kwargs: Any) -> RunReport:
        await self._bus.publish(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=RUN,
                statut=EXECUTION_ANNULEE,
            )
        )
        if self._rendre_la_main:
            # Le run finit **sans céder la main** après avoir publié : `asyncio.wait`
            # se réveille donc sur lui, et l'ordre arrive trop tard. C'est le cas
            # « un run terminé ne s'annule pas », et il est déterministe.
            return self.rapport
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.annule = True
            raise
        raise AssertionError("un run bloqué ne finit que par son annulation")


class ProcessDouble:
    """Le strict nécessaire d'un `Popen` pour l'hôte : un code de sortie, et rien d'autre.

    L'hôte ne demande à ses process que `poll()` — c'est ce qui rend `en_vol`
    synchrone et gratuit, donc posable à chaque battement. Un double suffit dès
    lors qu'on éprouve les **registres** de l'hôte et non le détachement lui-même.
    """

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self._code: int | None = None

    def poll(self) -> int | None:
        return self._code

    def mourir(self, code: int = 0) -> None:
        self._code = code


def hote_double(
    monkeypatch: pytest.MonkeyPatch, atelier: Path, **reglages: Any
) -> tuple[HoteRunDetache, dict[str, ProcessDouble]]:
    """Un `HoteRunDetache` dont les process sont des doubles — et le registre de ceux-ci.

    `delai_demarrage_s=0` fait de chaque lancement un « vivant mais lent » : aucun
    témoin n'est posé, l'échéance tombe tout de suite, et l'hôte tient le process
    pour parti. C'est le chemin le plus court vers un hôte qui *porte* des runs.
    """
    process: dict[str, ProcessDouble] = {}

    def ouvrir(self: HoteRunDetache, atelier_du_run: Path, journal: Path) -> ProcessDouble:
        double = ProcessDouble(pid=4000 + len(process))
        process[atelier_du_run.name] = double
        return double

    monkeypatch.setattr(HoteRunDetache, "_ouvrir_process", ouvrir)
    reglages.setdefault("delai_demarrage_s", 0.0)
    return HoteRunDetache(atelier=atelier, **reglages), process


def sans_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe la **dernière** porte par laquelle un test atteindrait un vrai Redis.

    `_observer_ordres` garde une ouverture de secours pour le cas où l'appelant
    ne lui passe pas de bus — et `_derouler` lui en passe un `None` quand
    `_bus_du_run` a échoué. Sans cette doublure, ce chemin-là ouvrirait un
    `pubsub` vers `localhost:6379` : l'échec serait immédiat et le test passerait
    quand même, mais la suite aurait tenté le réseau, ce qu'elle promet de ne
    jamais faire (`tests/conftest.py`). Un bus tari est la forme fidèle de ce qui
    doit arriver : le guet n'écoute plus, et le run continue.
    """
    monkeypatch.setattr(hote_detache, "RedisEventBus", lambda *_a, **_k: BusQuiSeReferme())


def deroule(monkeypatch: pytest.MonkeyPatch, bus: Any, atelier: Path, **ordre_kw: Any):
    """Joue `_derouler` avec `bus` pour bus du process, et rend ce qui a été câblé."""
    import maestro.engine.loop as loop

    MoteurCapture.cable = {}
    sans_redis(monkeypatch)
    monkeypatch.setattr(loop, "OrchestrationEngine", MoteurCapture)
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: bus)
    asyncio.run(hote_detache._derouler(ordre(**ordre_kw), atelier))
    return MoteurCapture.cable


def joue_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    moteur: Any,
    *,
    bus: Any = None,
    **ordre_kw: Any,
) -> tuple[int, list[tuple[str, str, str]], list[str]]:
    """Joue le **process fils** en entier, et rend `(code, issues publiées, battements)`.

    Cinq doublures, chacune pour une raison qui n'est pas le confort :

    - la **publication des étapes** pose un handler sur le logger *global*
      `maestro.trace` que rien ne retire — il survivrait au test et enverrait
      chaque ligne journalisée ensuite vers un Redis absent (même famille de fuite
      que l'export Langfuse, `tests/conftest.py`) ;
    - le **batteur** bat dans une liste : le cœur, lui, est le vrai ;
    - le **soldage** est capturé au lieu d'être publié — c'est ce qu'on veut lire ;
    - le **moteur** ne résout aucun fournisseur ;
    - le **bus** est passé, ce qui évite d'ouvrir un client Redis.

    Ce qui reste est tout le reste : la relecture de l'ordre, l'armement, le cœur,
    `run_borne`, la table des issues et le code de sortie.
    """
    import maestro.engine.loop as loop
    from maestro.controltower import battement
    from maestro.engine import cli as engine_cli

    atelier = tmp_path / RUN
    atelier.mkdir(parents=True, exist_ok=True)
    (atelier / hote_detache.FICHIER_ORDRE).write_text(
        json.dumps(hote_detache.ordre_vers_dict(ordre(**ordre_kw)), ensure_ascii=False),
        encoding="utf-8",
    )
    battus: list[str] = []
    issues: list[tuple[str, str, str]] = []

    def solder(run_id: str, statut: str, detail: str = "", **_kwargs: Any) -> bool:
        issues.append((run_id, statut, detail))
        return True

    sans_redis(monkeypatch)
    monkeypatch.setattr(engine_cli, "activer_publication_evenements", lambda: None)
    monkeypatch.setattr(battement, "batteur_redis", lambda *_a, **_k: battus.append)
    monkeypatch.setattr(hote_detache, "solder_le_run", solder)
    monkeypatch.setattr(loop, "OrchestrationEngine", moteur)
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: bus)
    return hote_detache.main([str(atelier)]), issues, battus


# --- ① Le transport : un ordre traverse la frontière sans rien perdre ----------


def test_un_ordre_relu_est_le_meme_champ_pour_champ() -> None:
    """L'aller-retour est l'**identité**, et il n'y a pas de moitié à vérifier.

    La forme sérialisée est écrite d'un côté de la frontière et relue de l'autre :
    c'est le seul couplage réel du module, et le seul moyen de s'assurer qu'il
    reste d'accord avec lui-même est que la lecture n'ait rien à deviner. Le
    `ticket` y est inclus à dessein — c'est le seul champ qui n'est pas un
    scalaire, donc le seul qui puisse s'aplatir en route (#187).
    """
    complet = OrdreRun(
        run_id=RUN,
        objectif="Prototyper un mini-CRM",
        plafond_cout_usd=1.5,
        plafond_tokens=120_000,
        timeout_tache_s=90.0,
        parallelisme=3,
        ticket=ReferenceTicket(id="#42", url="https://exemple.test/issues/42"),
        projet_id="prj-7f3a",
        mode_brief=MODE_BRIEF_AUTO,
    )

    relu = hote_detache.ordre_depuis_dict(hote_detache.ordre_vers_dict(complet))

    assert relu == complet


def test_un_ordre_minimal_retombe_sur_les_defauts_du_contrat() -> None:
    """Une clé absente vaut son défaut : c'est le contrat qui le porte, pas le transport."""
    relu = hote_detache.ordre_depuis_dict({"run_id": RUN, "objectif": "Objectif"})

    assert relu == OrdreRun(run_id=RUN, objectif="Objectif")
    assert relu.mode_brief == MODE_BRIEF_HUMAIN


@pytest.mark.parametrize(
    ("data", "attendu"),
    [
        ({"objectif": "Objectif"}, "run_id"),
        ({"run_id": RUN, "objectif": "   "}, "objectif"),
    ],
)
def test_les_deux_champs_sans_defaut_possible_sont_refuses(data, attendu) -> None:
    """Un ordre sans `run_id` ni objectif ne se répare pas : il se refuse.

    Ce sont les deux seuls champs stricts, et chacun pour une raison différente —
    un `run_id` absent rattacherait les étapes du run à un autre (ou à aucun), un
    objectif vide n'a rien à orchestrer. Tout le reste a un défaut au contrat.
    """
    with pytest.raises(ValueError) as refus:
        hote_detache.ordre_depuis_dict(data)

    assert attendu in str(refus.value)


def test_un_plafond_illisible_vaut_absent_et_ne_refuse_rien() -> None:
    """Les plafonds sont relus sans être **revalidés** — ils l'ont été côté service.

    Redire le refus ici en ferait un second endroit où le message s'écrit, et
    `Guardrails` refuserait de toute façon une valeur ≤ 0. Ce qui est illisible
    retombe donc sur « aucun plafond », jamais sur une levée.
    """
    relu = hote_detache.ordre_depuis_dict(
        {
            "run_id": RUN,
            "objectif": "Objectif",
            "plafond_cout_usd": "beaucoup",
            "plafond_tokens": None,
            "parallelisme": 4.0,
        }
    )

    assert relu.plafond_cout_usd is None
    assert relu.plafond_tokens is None
    assert relu.parallelisme == 4


def test_lire_l_ordre_l_efface_du_disque(tmp_path: Path) -> None:
    """L'effacement n'est pas du ménage : l'ordre porte l'objectif.

    C'est du texte libre écrit par un humain — la matière même que `redact_secrets`
    expurge partout ailleurs. Il a été lu, il n'a plus d'usage, il n'a pas à rester
    sur le disque le temps d'un run.
    """
    fichier = tmp_path / hote_detache.FICHIER_ORDRE
    fichier.write_text(
        json.dumps(hote_detache.ordre_vers_dict(ordre(objectif="Secret : sk-42"))),
        encoding="utf-8",
    )

    relu = hote_detache.lire_ordre(tmp_path)

    assert relu.objectif == "Secret : sk-42"
    assert not fichier.exists()


# --- ② Le démarrage : ce qui part, ce qui ne part pas --------------------------


def test_un_hote_mort_aussitot_leve_avec_son_code_et_sa_cause(
    monkeypatch: pytest.MonkeyPatch, bouchon: Path
) -> None:
    """Un démarrage raté se dit **tout de suite**, avec de quoi le diagnostiquer.

    C'est ce que la veille AionUi conseillait de garder (docs/28 §7) : on peut
    rater un démarrage. Et c'est une panne du *lancement*, jamais du run — rien ne
    viendra plus de cet hôte, donc l'appelant est le seul encore là pour l'écrire.
    Le message porte le **code de sortie**, les dernières lignes du journal et le
    chemin de ce journal : c'est ce qui atterrit dans le `detail` du run, sous les
    yeux de quelqu'un.
    """
    monkeypatch.setenv("BOUCHON_CODE", "3")
    monkeypatch.setenv("BOUCHON_MESSAGE", "ModuleNotFoundError: No module named 'boum'")
    hote = HoteRunDetache(atelier=bouchon, delai_issue_s=0.0)

    with pytest.raises(DemarrageHoteRate) as echec:
        asyncio.run(hote.lancer(ordre()))

    cause = str(echec.value)
    assert RUN in cause
    assert "code 3" in cause
    assert "No module named 'boum'" in cause
    assert hote_detache.FICHIER_JOURNAL in cause
    # Ni en vol, ni **ramassable** : cette mort-là a déjà soldé le run chez
    # l'appelant, et la rendre une seconde fois le solderait deux fois.
    assert hote.en_vol(RUN) is False
    assert hote.runs_en_vol() == ()
    assert hote.ramasser() == ()


def test_un_hote_arme_rend_la_main_sur_son_temoin(bouchon: Path) -> None:
    """Le témoin ne dit pas « je suis né » mais « je suis armé » — et c'est la promesse.

    Le lanceur rend la main dessus, ce qui rend l'attente *courte* : sans lui, il
    devrait dormir un délai fixe à chaque lancement pour savoir si le process a
    tenu, et payer sur la requête HTTP de chaque run le prix du run qui rate.
    """
    hote = HoteRunDetache(atelier=bouchon)

    asyncio.run(hote.lancer(ordre()))

    assert (bouchon / RUN / hote_detache.FICHIER_PRET).exists()
    assert hote.en_vol(RUN) is True
    assert hote.runs_en_vol() == (RUN,)


def test_un_hote_vivant_mais_lent_est_tenu_pour_parti(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Au-delà du plafond, on ne tue pas ce qui n'a rien fait de mal.

    L'atteindre fait tenir pour parti un process qui ne l'est peut-être pas ; c'est
    alors le seuil d'orphelinat (trente minutes) qui éteindra le run. En face, le
    coût d'attendre plus longtemps ne pèse que sur un process **vivant** qui tarde à
    s'armer, c'est-à-dire précisément le cas où attendre est juste.
    """
    hote, process = hote_double(monkeypatch, tmp_path)

    asyncio.run(hote.lancer(ordre()))

    assert not (tmp_path / RUN / hote_detache.FICHIER_PRET).exists()
    assert hote.en_vol(RUN) is True
    assert process[RUN].poll() is None


def test_un_hote_qui_a_pose_son_temoin_puis_meurt_est_parti_quand_meme(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L'ordre des deux questions **est** le contenu de `_attendre_demarrage`.

    Le témoin d'abord : un process qui l'a posé *est* parti, même s'il meurt dans
    la seconde qui suit — cette mort-là est celle d'un run, pas d'un démarrage.
    Interroger `poll()` en premier ferait dire « n'a pas démarré » d'un hôte qui
    avait démarré, publié et battu, donc solder en `echec` un run qui a peut-être
    très bien fini.

    Les deux faits sont posés **ensemble** ici (témoin écrit, process déjà mort) :
    c'est le seul montage où l'ordre se lit sans dépendre d'une course, et donc le
    seul où l'inverser se voit à coup sûr. La dépouille, elle, reste ramassable :
    c'est le bon canal pour cette mort-là.
    """
    morts: list[str] = []

    def ouvrir(self: HoteRunDetache, atelier_du_run: Path, journal: Path) -> ProcessDouble:
        (atelier_du_run / hote_detache.FICHIER_PRET).write_text("", encoding="utf-8")
        journal.write_text("MemoryError: out of memory", encoding="utf-8")
        double = ProcessDouble()
        double.mourir(1)
        morts.append(atelier_du_run.name)
        return double

    monkeypatch.setattr(HoteRunDetache, "_ouvrir_process", ouvrir)
    hote = HoteRunDetache(atelier=tmp_path, delai_issue_s=0.0)

    asyncio.run(hote.lancer(ordre()))  # ne lève pas : le témoin fait foi

    assert morts == [RUN]
    assert hote.en_vol(RUN) is False
    (defunt,) = hote.ramasser()
    assert "MemoryError" in defunt.cause


def test_un_temoin_perime_ne_fait_jamais_dire_demarre(tmp_path: Path) -> None:
    """Un atelier déjà là est vidé de son témoin, et ce n'est pas du ménage.

    Un témoin périmé ferait dire « démarré » d'un process qui n'a pas encore ouvert
    la bouche, c'est-à-dire exactement l'inverse du contrôle qu'il sert. Le cas ne
    se présente pas avec la racine temporaire — un dossier neuf par lancement —
    mais avec une racine imposée, où le nom du dossier est celui du run.
    """
    hote = HoteRunDetache(atelier=tmp_path)
    perime = tmp_path / RUN / hote_detache.FICHIER_PRET
    perime.parent.mkdir(parents=True)
    perime.write_text("", encoding="utf-8")

    assert hote._ouvrir_atelier(RUN) == tmp_path / RUN
    assert not perime.exists()


def test_un_process_qui_ne_s_ouvre_pas_leve_en_nommant_l_interpreteur(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L'autre non-départ : le `Popen` lui-même échoue, avant tout journal.

    Il n'y a alors rien à lire — le process n'a pas existé —, donc la cause est ce
    qu'on tentait de lancer. C'est ce qui distingue « l'interpréteur est
    introuvable » de « le module a levé », deux pannes qu'un même message rendrait
    indiscernables.
    """

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("interpréteur introuvable")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    hote = HoteRunDetache(atelier=tmp_path, python="python-qui-n-existe-pas")

    with pytest.raises(DemarrageHoteRate) as echec:
        asyncio.run(hote.lancer(ordre()))

    cause = str(echec.value)
    assert "python-qui-n-existe-pas" in cause
    assert hote_detache.MODULE_HOTE in cause
    assert "OSError" in cause


@pytest.mark.parametrize(
    ("lignes", "attendu"),
    [
        ([], "journal vide"),
        (["premier", "", "  ", "dernier"], "premier | dernier"),
    ],
)
def test_la_cause_retient_les_dernieres_lignes_utiles(tmp_path: Path, lignes, attendu) -> None:
    """La **dernière** ligne d'une trace Python est celle qui nomme la panne.

    Les précédentes disent *où* ; au-delà, on ne remplit plus qu'un champ que
    personne ne lira jusqu'au bout. Le chemin du journal voyage avec, et pas par
    politesse : c'est ce qui manque quand cinq lignes ne suffisent pas.
    """
    journal = tmp_path / hote_detache.FICHIER_JOURNAL
    journal.write_text("\n".join(lignes), encoding="utf-8")

    cause = hote_detache._cause(journal)

    assert attendu in cause
    assert str(journal) in cause


def test_une_cause_trop_longue_est_tronquee_par_la_tete(tmp_path: Path) -> None:
    """Ce qui est rendu atterrit dans le `detail` d'un run : la fin est ce qui compte."""
    journal = tmp_path / hote_detache.FICHIER_JOURNAL
    journal.write_text("x" * 5_000 + "\nErreur finale", encoding="utf-8")

    cause = hote_detache._cause(journal)

    assert cause.startswith("…")
    assert "Erreur finale" in cause


def test_l_environnement_du_fils_herite_sans_ecraser_un_choix_explicite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deux réglages s'ajoutent, au service du seul fichier que quelqu'un lira.

    `PYTHONUNBUFFERED` n'est pas cosmétique : le lanceur lit le journal **dans la
    milliseconde** qui suit la mort du process, et un tampon non vidé rendrait
    « cause inconnue » sur la panne même que ce dispositif existe pour nommer.
    `PYTHONIOENCODING` évite le mojibake d'une console héritée (#141). Ni l'un ni
    l'autre n'écrase un choix explicite de l'environnement.
    """
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    monkeypatch.setenv("REDIS_URL", "redis://exemple.test:6379/0")

    env = HoteRunDetache._environnement()

    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "latin-1"
    # Hérité : c'est ce qui fait qu'un run détaché publie sur *le même* Redis sans
    # qu'on ait à lui passer une seule adresse.
    assert env["REDIS_URL"] == "redis://exemple.test:6379/0"


def test_le_module_du_fils_s_execute_par_python_m_sans_avertissement(
    tmp_path: Path,
) -> None:
    """`python -m maestro.controltower.hote_detache` est l'argv de production.

    Deux choses d'un coup, et la seconde est un piège que le module documente : le
    point d'entrée **répond** (l'usage, code 0), et il n'est **pas** réexporté par
    `maestro.controltower.__init__` — un module déjà importé par son paquet est
    ensuite exécuté une seconde fois comme `__main__`, ce que Python signale par un
    `RuntimeWarning` qui atterrirait en tête du seul fichier où l'on va chercher la
    cause d'un démarrage raté.
    """
    sortie = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
        [sys.executable, "-m", hote_detache.MODULE_HOTE, "--help"],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        cwd=str(tmp_path),
        timeout=DELAI_OBSERVATION_S,
        check=False,
    )

    trace = sortie.stderr.decode("utf-8", errors="replace")
    assert sortie.returncode == 0, trace
    assert "Usage" in trace
    assert "RuntimeWarning" not in trace


@pytest.mark.parametrize("args", [[], ["un", "deux"]])
def test_un_appel_mal_forme_rend_2_et_l_usage(capsys, args) -> None:
    """2, et non 1 : rien n'a raté du run, c'est l'appel qui n'a pas de sens."""
    assert hote_detache.main(args) == 2
    assert "Usage" in capsys.readouterr().err


def test_un_ordre_illisible_rend_2_sans_toucher_au_reste(tmp_path: Path, capsys) -> None:
    """Un atelier sans ordre lisible : le fils ne peut rien faire, et il le dit."""
    (tmp_path / hote_detache.FICHIER_ORDRE).write_text("{ pas du JSON", encoding="utf-8")

    assert hote_detache.main([str(tmp_path)]) == 2
    assert "Ordre de run illisible" in capsys.readouterr().err


# --- ③ La survie : le run ne meurt pas avec l'API ------------------------------


def test_le_fils_est_coupe_de_la_console_et_du_groupe_de_l_api() -> None:
    """Deux drapeaux et pas un — c'est ce qui distingue « détaché » de « lancé ».

    Un test qui paraît tautologique, et qui ne l'est pas : ce que ces drapeaux
    achètent ne se voit sur aucun process orphelin (ni Windows ni POSIX ne tuent un
    enfant quand son parent sort), mais **un Ctrl-C dans la console de l'API**
    emporterait le run sans `CREATE_NEW_PROCESS_GROUP`, c'est-à-dire la panne même
    que ce module supprime. Les retirer serait donc invisible partout ailleurs.

    Ils font aussi du fils un **chef de groupe**, ce dont `_eteindre` a besoin (cf.
    le repli franc, plus bas) : ce n'est pas un effet de bord, c'est la seconde
    raison de ces drapeaux. `CREATE_BREAKAWAY_FROM_JOB` n'y est **pas**, et son
    absence est un choix : il échoue en `ACCESS_DENIED` quand le job de l'appelant
    n'autorise pas l'évasion, et ferait alors rater *tous* les démarrages.

    **L'absence de `DETACHED_PROCESS` est le contrôle qui compte** (#469), et c'est
    le seul qui ne se lit pas comme une redite du code : les deux drapeaux sont
    **exclusifs** — `CREATE_NO_WINDOW` est ignoré en présence de l'autre —, si bien
    que le remettre « pour faire bonne mesure » ne l'ajouterait pas, il
    **annulerait** le correctif sans rien casser de visible ici. La fenêtre qu'il
    ramènerait ne se voit que sur un vrai petit-fils console, sous Windows, et
    c'est le test suivant qui la regarde.
    """
    detachement = hote_detache._detachement()

    if sys.platform == "win32":
        drapeaux = detachement["creationflags"]
        assert drapeaux & subprocess.CREATE_NO_WINDOW
        assert drapeaux & subprocess.CREATE_NEW_PROCESS_GROUP
        assert not drapeaux & subprocess.DETACHED_PROCESS
        assert not drapeaux & getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    else:
        assert detachement == {"start_new_session": True}


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="la fenêtre de console est une règle Windows : POSIX n'a rien à ouvrir.",
)
def test_un_petit_fils_console_n_ouvre_aucune_fenetre(
    monkeypatch: pytest.MonkeyPatch, bouchon: Path
) -> None:
    """La panne de #469, sur un vrai petit-fils console — et son témoin fautif.

    Le run lance un `claude.exe` par tâche, application **console**, par
    l'`anyio.open_process` du `claude-agent-sdk`, sans aucun `creationflags`. Sous
    `DETACHED_PROCESS`, l'hôte n'avait **aucune** console, et la règle Windows veut
    qu'un process console dont le parent n'en a pas s'en voie allouer une neuve
    **avec fenêtre** — un terminal noir par tâche en vol, vide parce que rien ne
    s'y écrit jamais. `CREATE_NO_WINDOW` donne à l'hôte une console *propre et
    invisible*, dont les petits-enfants héritent au lieu d'en réclamer une.

    Le test **joue d'abord le régime fautif** (les drapeaux d'avant #469) et exige
    d'y voir la fenêtre : sans cette moitié, un `hwnd == 0` ne distinguerait pas
    « aucune fenêtre » de « aucune mesure », et le jour où le petit-fils ne
    démarrerait plus le test resterait vert sur une question jamais posée. C'est la
    discipline que le dépôt applique déjà à ses `grep` de garde.

    Ce qu'on mesure est `GetConsoleWindow()` **dans le petit-fils**, seul endroit
    d'où la réponse est un fait : le lanceur, lui, ne peut que supposer.
    """
    monkeypatch.setenv("BOUCHON_PETIT_FILS", str(bouchon.parent / "petit_fils.py"))
    reel = hote_detache._detachement
    hote = HoteRunDetache(atelier=bouchon)

    monkeypatch.setattr(
        hote_detache, "_detachement", lambda: {"creationflags": DRAPEAUX_AVANT_469}
    )
    asyncio.run(hote.lancer(ordre(run_id=f"{RUN}-avant")))
    avant = _releve(bouchon, f"{RUN}-avant", FICHIER_PETIT_FILS_CONSOLE)

    monkeypatch.setattr(hote_detache, "_detachement", reel)
    asyncio.run(hote.lancer(ordre(run_id=f"{RUN}-apres")))
    apres = _releve(bouchon, f"{RUN}-apres", FICHIER_PETIT_FILS_CONSOLE)

    assert avant["hwnd"] != 0, (
        "le témoin fautif n'a ouvert aucune fenêtre : ce banc ne sait plus en voir, "
        "donc le verdict qui suit ne vaut rien."
    )
    assert apres["hwnd"] == 0, (
        f"le petit-fils a reçu une console à fenêtre (hwnd={apres['hwnd']}) : "
        "chaque tâche en vol rouvre un terminal vide (#469)."
    )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="les événements de console sont un mécanisme Windows.",
)
def test_l_hote_ne_partage_pas_la_console_de_son_lanceur(bouchon: Path) -> None:
    """Ce qui remplace le Ctrl-C injecté : la raison pour laquelle il n'arrive pas.

    Le critère du ticket demande que l'hôte survive au **Ctrl-C dans la console de
    l'API**. Le geste ne se synthétise pas de façon fiable — mesuré le 2026-08-25 :
    `AttachConsole` puis `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` rendent tous
    deux `TRUE` et ne réveillent personne, ni l'API visée ni l'émetteur. Un banc
    qui rend « survit » parce que son signal n'est jamais parti prouve exactement
    rien, et c'est le genre de vert qu'on préfère ne pas avoir.

    On mesure donc la propriété **à sa source**, et elle est plus large que le seul
    Ctrl-C : un événement de console — Ctrl-C, Ctrl-Break, fermeture de la fenêtre
    — n'est dispatché qu'aux process **attachés à cette console-là**.
    `GetConsoleProcessList`, lu depuis le lanceur, donne cette liste exacte. L'hôte
    n'y est pas : aucun de ces trois événements ne peut donc l'atteindre, et la
    démonstration ne dépend d'aucune course.

    C'est la propriété que `DETACHED_PROCESS` achetait en ne donnant **aucune**
    console ; une console *à soi* l'achète tout autant, et c'est ce qui rendait le
    correctif sûr avant même de le mesurer.
    """
    partagent_ma_console = _process_de_ma_console()
    if not partagent_ma_console:
        pytest.skip("ce process n'a pas de console : la question n'a pas de sens ici.")

    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    vu_de_l_hote = _releve(bouchon, RUN, FICHIER_CONSOLE)

    assert os.getpid() in partagent_ma_console, (
        "l'instrument ne sait pas lire sa propre console : le verdict qui suit "
        "serait vrai pour la mauvaise raison."
    )
    assert vu_de_l_hote["pid"] not in partagent_ma_console, (
        "l'hôte partage la console de son lanceur : un Ctrl-C, un Ctrl-Break ou "
        "la fermeture de la fenêtre l'emporterait avec l'API (#469)."
    )
    assert hote._process[RUN].pid not in partagent_ma_console
    # Sa console à lui existe — c'est ce qui distingue `CREATE_NO_WINDOW` de
    # `DETACHED_PROCESS` — et elle n'a pas de fenêtre.
    assert vu_de_l_hote["pid"] in vu_de_l_hote["partagee_avec"]
    assert vu_de_l_hote["hwnd"] == 0


def test_fermer_n_arrete_aucun_run(bouchon: Path) -> None:
    """La méthode où deux hôtes disent le contraire l'un de l'autre.

    Celui en process annule tout, faute de pouvoir survivre ; celui-ci ne fait
    rien, et ce **rien est la livraison** du chantier #441. `delai_s` est ignoré,
    comme le contrat le prévoit : il borne l'attente de celui qui a quelque chose à
    éteindre.

    Éprouvé sur un **vrai** process, et pas sur un double, parce que le double ne
    saurait pas mourir : un `_eteindre` ajouté ici passerait inaperçu sur un objet
    qui rend `poll() is None` quoi qu'on lui fasse. Ce qu'on regarde est donc le
    battement qui **avance** après la fermeture — un process encore là mais figé ne
    serait pas une survie.
    """
    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    repere = _battement(bouchon, RUN)

    asyncio.run(hote.fermer(delai_s=5.0))

    assert hote.en_vol(RUN) is True
    _attendre(
        lambda: _battement(bouchon, RUN) > repere,
        "le run s'est arrêté avec l'API : `fermer` a éteint ce qu'il devait laisser vivre.",
    )


def test_l_extinction_volontaire_emporte_ce_que_fermer_laisse_vivre(bouchon: Path) -> None:
    """Les deux arrêts, **sur le même process**, dans l'ordre où ils se distinguent (#486).

    Le test ci-dessus prouve la moitié qu'on ne défait pas ; celui-ci prouve l'autre,
    et les mettre côte à côte est le sujet : c'est **le même hôte**, au même instant,
    qui survit à l'arrêt subi et meurt de l'arrêt volontaire. Deux tests séparés
    diraient chacun la moitié, et aucun ne dirait que la distinction *tient* — un
    `fermer` qui se mettrait à tuer laisserait le second vert.

    `fermer` d'abord, parce que c'est ce que l'API subit (lifespan, `SIGTERM`,
    redémarrage qui remplace la session précédente) : le battement doit **avancer**
    après lui, un
    process encore là mais figé ne serait pas une survie. `annuler` ensuite, parce
    que c'est le verbe par lequel passe l'extinction volontaire
    (`ServiceExecutions.eteindre` → `_solder` → `_hote.annuler`) — le repli franc y
    emporte le groupe de process, ce que les deux tests de `_eteindre` plus bas
    éprouvent jusqu'à la descendance.
    """
    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    process = hote._process[RUN]
    repere = _battement(bouchon, RUN)

    # ① L'accident : l'API se retire, le run continue.
    asyncio.run(hote.fermer(delai_s=5.0))
    assert hote.en_vol(RUN) is True
    _attendre(
        lambda: _battement(bouchon, RUN) > repere,
        "le run s'est arrêté avec l'API : l'arrêt subi a emporté ce qu'il doit laisser vivre.",
    )

    # ② La décision : Maestro s'éteint, le run s'éteint avec lui.
    assert asyncio.run(hote.annuler(RUN, delai_s=0.2)) is True
    _attendre(
        lambda: process.poll() is not None,
        "le run a survécu à l'extinction volontaire : il tourne désormais sans "
        "écran pour le suivre ni bouton pour l'arrêter (#486).",
    )
    assert hote.runs_en_vol() == ()


def test_deux_runs_en_vol_survivent_a_la_mort_de_leur_lanceur(bouchon: Path) -> None:
    """La propriété du chantier, sur de **vrais** process : le run survit à son API.

    Elle ne se simule pas — vérifier qu'un process survit à un autre demande deux
    process —, et elle ne se lit pas sur un seul run : `fermer` porte sur *tout* ce
    que l'hôte tient, et deux runs lancés l'un après l'autre ne prouveraient pas
    qu'ils ont été en vol **ensemble**. D'où la barrière (#292) : chaque bouchon
    s'annonce et attend l'autre avant de battre, si bien que « les deux étaient
    vivants sous la même API » est un fait observé et non une course gagnée. Chacun
    tient son propre relevé (#313) — un compteur partagé perdrait une écriture
    exactement quand les deux écrivent en même temps.

    Ce que le test regarde ensuite est le battement qui **avance** après la mort du
    lanceur : un process encore là mais figé ne serait pas une survie.
    """
    runs = (f"{RUN}-a", f"{RUN}-b")
    porte = bouchon.parent / "barriere"
    porte.mkdir()
    lanceur = bouchon.parent / "lanceur.py"
    lanceur.write_text(_CORPS_LANCEUR, encoding="utf-8")

    api = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
        [sys.executable, str(lanceur), hote_detache.MODULE_HOTE, str(bouchon), *runs],
        capture_output=True,
        env={
            **os.environ,
            "BOUCHON_BARRIERE": str(porte),
            "BOUCHON_BARRIERE_N": str(len(runs)),
        },
        timeout=DELAI_OBSERVATION_S * 2,
        check=False,
    )

    assert api.returncode == 0, api.stderr.decode("utf-8", errors="replace")
    # Le lanceur est mort — c'est l'arrêt de l'API. La barrière, elle, a été levée :
    # les deux hôtes se sont vus vivants ensemble avant qu'il ne parte.
    assert not list(porte.glob(f"*{SUFFIXE_BARRIERE_RATEE}")), (
        "barrière non levée : le relevé qui suit ne dirait pas que les deux runs "
        "étaient en vol ensemble."
    )
    assert sorted(p.name for p in porte.glob(f"*{SUFFIXE_ARRIVE}")) == sorted(
        f"{run}{SUFFIXE_ARRIVE}" for run in runs
    )

    repere = {run: _battement(bouchon, run) for run in runs}
    for run in runs:
        _attendre(
            lambda run=run: _battement(bouchon, run) > repere[run],
            f"le run {run} a cessé de battre avec son lanceur : il n'y a pas survécu.",
        )


def test_un_run_qu_on_ne_porte_plus_ne_s_annule_pas_ici(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`False` n'est pas un échec : c'est le cas normal d'un run qu'on ne tient plus.

    Après un redémarrage de l'API, le registre est vide et l'hôte ne prétend plus
    rien porter. Le run, lui, s'arrête **quand même** : l'ordre voyage par le bus,
    qui ne dépend d'aucun process — et c'est ce qui a retiré à ce `False` sa
    conclusion d'autrefois (« rien ne l'interrompra »).
    """
    hote, _ = hote_double(monkeypatch, tmp_path)

    assert asyncio.run(hote.annuler("run-d-une-autre-api", delai_s=0.1)) is False


# --- ④ L'annulation traverse la frontière --------------------------------------


def test_le_guet_retient_l_issue_annulee_de_son_run_et_rien_d_autre() -> None:
    """L'ordre n'a pas de canal à lui : c'est l'**issue** du run, et c'est voulu.

    Deux façons de dire un même fait finissent par se séparer, l'une émise sans
    l'autre, et l'on aurait alors un run soldé qui travaille encore ; là où « ce run
    est soldé annulé » a pour conséquence *nécessaire* « ce run doit cesser de
    travailler ». Tout le reste du bus est ignoré, les issues des **autres** runs
    comprises : ce process n'en porte qu'un, et il est nommé.
    """
    bus = BusScripte(
        [
            Event(type=EVENEMENT_TACHE_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id="un-autre", statut=EXECUTION_ANNULEE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_TERMINEE),
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE),
        ]
    )

    assert asyncio.run(hote_detache._observer_ordres(RUN, bus=bus)) is True
    # Le bus est celui de l'appelant : le guet ferme son flux, jamais le bus.
    assert bus.ferme is False


@pytest.mark.parametrize("bus", [BusQuiSeReferme(), BusQuiRefuse()], ids=["tari", "en-panne"])
def test_un_guet_en_panne_dit_je_n_ecoute_plus_jamais_on_m_a_dit_stop(bus) -> None:
    """`False` sur tout ce qui n'est pas l'ordre — client impossible, flux tari, refus.

    Confondre les deux ferait tuer un run par une panne de bus, alors que le run se
    passe très bien de ce canal : le lanceur garde son repli franc. C'est pour tenir
    cette promesse d'un seul tenant que l'ouverture du bus est **dans** le `try` —
    la laisser dehors y laisserait la seule panne capable d'emporter le run.
    """
    assert asyncio.run(hote_detache._observer_ordres(RUN, bus=bus)) is False


def test_le_guet_referme_le_bus_qu_il_a_ouvert_lui_meme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'ouverture de secours reste, et ce qu'elle ouvre, elle le referme.

    Un abonnement Redis est une connexion, et la laisser derrière soi dans un
    process qui vit des heures est le genre de fuite qu'on ne remarque qu'au
    trentième run. Le bus **passé**, lui, appartient à l'appelant (test ci-dessus).
    """
    propre = BusScripte([])
    monkeypatch.setattr(hote_detache, "RedisEventBus", lambda *_a, **_k: propre)

    assert asyncio.run(hote_detache._observer_ordres(RUN)) is False
    assert propre.ferme is True


def test_l_ordre_vu_annule_la_tache_du_run_et_court_circuite_le_reste(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`Task.cancel` reste le mécanisme réel, à un aller de bus près.

    C'est la propriété que le POC avait protégée en choisissant la tâche de fond
    (#185) et que docs/28 §4.3 refuse de repayer : le process écoute, annule sa
    **propre** tâche, et `RunAnnule` court-circuite le chemin nominal — il n'y a
    pas de rapport à imprimer, pas de synthèse à rendre, et le seul geste qui reste
    est de sortir.
    """
    import maestro.engine.loop as loop

    bus = InMemoryEventBus()
    moteur = MoteurQuiSeFaitAnnuler(bus)
    monkeypatch.setattr(loop, "OrchestrationEngine", moteur)
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: bus)

    with pytest.raises(hote_detache.RunAnnule) as annulation:
        asyncio.run(hote_detache._derouler(ordre(), tmp_path))

    assert RUN in str(annulation.value)
    assert moteur.annule is True


def test_un_run_termine_ne_s_annule_pas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Le run finit d'abord : l'ordre arrivé dans la même passe est arrivé trop tard.

    Sa synthèse a plus de valeur qu'une annulation nominale, et il n'y a plus rien à
    interrompre. C'est la première des trois lectures de la course, et la seule où le
    résultat du run l'emporte sur ce que le guet a vu.
    """
    import maestro.engine.loop as loop

    bus = InMemoryEventBus()
    attendu = RunReport(objectif="Objectif", resultats=(resultat("t1"),))
    moteur = MoteurQuiSeFaitAnnuler(bus, rendre_la_main=True)
    moteur.rapport = attendu
    monkeypatch.setattr(loop, "OrchestrationEngine", moteur)
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: bus)

    assert asyncio.run(hote_detache._derouler(ordre(), tmp_path)) is attendu


def test_un_guet_qui_n_a_rien_vu_laisse_le_run_continuer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bus refermé, Redis injoignable : le run **continue**, sans son canal gracieux.

    C'est le cas où l'on tient à ne pas confondre « je n'écoute plus » avec « on m'a
    dit stop » : la première lecture coûterait un run tué par une panne de Redis.
    """
    import maestro.engine.loop as loop

    attendu = RunReport(objectif="Objectif", resultats=(resultat("t1"),))
    monkeypatch.setattr(loop, "OrchestrationEngine", MoteurScripte(rapport=attendu))
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: BusQuiSeReferme())

    assert asyncio.run(hote_detache._derouler(ordre(), tmp_path)) is attendu


def test_un_guet_annule_ou_en_erreur_est_lu_comme_n_ayant_rien_vu() -> None:
    """Lire le résultat d'une tâche est l'endroit où une promesse tenue ailleurs se paie.

    `_observer_ordres` promet de ne pas lever, mais une tâche annulée ou en
    erreur relèverait **à la lecture**, et transformerait un guet en panne du run.
    """

    async def scenario() -> tuple[bool, bool]:
        async def jamais() -> bool:
            await asyncio.Event().wait()
            return True  # pragma: no cover - la tâche est annulée avant

        async def boum() -> bool:
            raise RuntimeError("guet en panne")

        annulee = asyncio.create_task(jamais())
        annulee.cancel()
        with suppress(asyncio.CancelledError):
            await annulee
        fautive = asyncio.create_task(boum())
        with suppress(RuntimeError):
            await fautive
        return hote_detache._annulation_vue(annulee), hote_detache._annulation_vue(fautive)

    assert asyncio.run(scenario()) == (False, False)


def test_un_hote_qui_s_eteint_dans_le_delai_n_est_pas_acheve(
    monkeypatch: pytest.MonkeyPatch, bouchon: Path
) -> None:
    """Le geste gracieux **ne s'écrit pas** dans `annuler` : il est déjà parti.

    L'issue que `ServiceExecutions._solder` consigne avant d'appeler cette méthode
    est publiée sur le bus, le process la guette et déroule ses tâches proprement.
    Ce qui reste à faire ici est de **lui en laisser le temps** — borné par
    `delai_s`, jamais plus. Le fichier d'arrêt tient ici le rôle de l'ordre reçu :
    ce qu'on éprouve est la borne, pas le canal (testé plus haut).
    """
    eteints: list[Any] = []
    monkeypatch.setattr(hote_detache, "_eteindre", eteints.append)
    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    (bouchon / RUN / FICHIER_ARRET).write_text("", encoding="utf-8")

    assert asyncio.run(hote.annuler(RUN, delai_s=DELAI_OBSERVATION_S)) is True
    assert eteints == []
    assert hote.en_vol(RUN) is False


def test_un_hote_qui_s_obstine_est_eteint_pour_de_bon(bouchon: Path) -> None:
    """Passé le délai, on éteint l'hôte **et sa descendance** — le repli franc.

    Ce n'est pas l'aveu d'un geste raté : un process qui a entendu l'ordre a déjà
    annulé ses tâches quand on l'achève, si bien qu'on interrompt une fermeture de
    boucle et non du travail. Ce qu'on refuse, c'est qu'un run **soldé** continue de
    coûter parce que personne n'écoutait. Le vrai `_eteindre` est joué ici, sur un
    vrai groupe de process : c'est le seul endroit où la leçon de #291 — *tuer un
    parent avant ses enfants fabrique l'orphelin qu'on veut éviter* — a une chance
    d'être fausse sans qu'on le voie.
    """
    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    process = hote._process[RUN]

    assert asyncio.run(hote.annuler(RUN, delai_s=0.2)) is True

    _attendre(
        lambda: process.poll() is not None,
        "l'hôte obstiné a survécu à son extinction : le repli franc n'a pas eu lieu.",
    )
    assert hote.runs_en_vol() == ()


def test_eteindre_emporte_toujours_la_descendance_de_l_hote(
    monkeypatch: pytest.MonkeyPatch, bouchon: Path
) -> None:
    """La leçon de #291, éprouvée sur un vrai petit-fils — et non déduite (#469).

    Le test ci-dessus regarde mourir l'**hôte** ; celui-ci regarde mourir ce qu'il
    tenait. C'est la moitié qui compte : un hôte de run est le père d'un
    `claude.exe` par tâche en vol, et un `terminate()` sur lui seul les laisserait
    travailler pour un run déjà soldé, sans que rien ne les nomme — *tuer un parent
    avant ses enfants est ce qui fabrique l'orphelin qu'on veut éviter*.

    Il est ici parce que #469 a changé les drapeaux de `_detachement`, et que
    `_eteindre` s'adresse au **groupe** qu'ils créent : `CREATE_NEW_PROCESS_GROUP`
    reste, mais la propriété qui en dépend ne se relit pas dans un `assert` sur des
    constantes. Le pid visé est celui que le petit-fils **se voit** et non celui que
    `Popen` a rendu : sous un venv à trampoline (uv), les deux diffèrent, et viser
    le second laisserait le vrai process hors du contrôle.

    Il vaut sur les deux plateformes — `taskkill /T` d'un côté, `killpg` de
    l'autre —, donc il tourne aussi dans le conteneur du filet, où POSIX répond.
    """
    monkeypatch.setenv("BOUCHON_PETIT_FILS", str(bouchon.parent / "petit_fils.py"))
    hote = HoteRunDetache(atelier=bouchon)
    asyncio.run(hote.lancer(ordre()))
    petit_fils = _releve(bouchon, RUN, FICHIER_PETIT_FILS_CONSOLE)["pid"]
    assert _vivant(petit_fils), "le petit-fils n'a jamais vécu : rien à emporter."

    assert asyncio.run(hote.annuler(RUN, delai_s=0.2)) is True

    _attendre(
        lambda: not _vivant(petit_fils),
        f"le petit-fils {petit_fils} a survécu à l'extinction de son hôte : "
        "`_eteindre` n'emporte plus la descendance (#291).",
    )


# --- ⑤ Le canal humain : les trois attentes tiennent sur le bus du process -----


def test_les_trois_attentes_humaines_sont_cablees_sur_le_bus_du_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Brief, clarifications et action sensible reçoivent leur arbitre (#445).

    C'est le lot entier en un test : avant lui, `_derouler` construisait un moteur
    sans arbitre et des garde-fous sans validateur, si bien qu'un run détaché ne
    pouvait poser aucune des trois questions.
    """
    cable = deroule(monkeypatch, InMemoryEventBus(), tmp_path)

    assert isinstance(cable["arbitre_brief"], ArbitreBriefControlTower)
    assert isinstance(cable["arbitre_clarification"], ArbitreClarificationControlTower)
    assert isinstance(cable["guardrails"].validateur, ValidateurControlTower)


def test_le_meme_bus_sert_le_guet_et_les_trois_arbitres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un process, un bus : quatre abonnements tiennent sur une connexion.

    Le contrôle porte sur l'**identité** de l'objet et pas sur son type : trois
    fabriques `*_redis` rendraient trois bus corrects, donc trois connexions dans
    un process qui vit des heures.
    """
    bus = InMemoryEventBus()
    cable = deroule(monkeypatch, bus, tmp_path)

    assert cable["arbitre_brief"]._bus is bus
    assert cable["arbitre_clarification"]._bus is bus
    assert cable["guardrails"].validateur._bus is bus


def test_les_plafonds_voyagent_avec_l_ordre_et_le_validateur_se_branche_ici(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Les garde-fous ne voyagent pas entiers, et c'est le partage du contrat.

    Ils mêlent un réglage du **lancement** — les plafonds, qui sont des nombres —
    et un câblage de **déploiement** — le validateur, branché sur le bus de *ce*
    process. Seuls les nombres entrent dans l'ordre ; les faire voyager ensemble
    rendrait l'ordre intransportable pour un seul de ses cinq champs.
    """
    cable = deroule(
        monkeypatch,
        InMemoryEventBus(),
        tmp_path,
        plafond_cout_usd=2.0,
        plafond_tokens=1_000,
        timeout_tache_s=30.0,
        parallelisme=2,
        projet_id="prj-7f3a",
    )

    garde_fous = cable["guardrails"]
    assert (garde_fous.plafond_cout_usd, garde_fous.plafond_tokens) == (2.0, 1_000)
    assert garde_fous.timeout_s == 30.0
    assert cable["max_parallele"] == 2
    # Le prérequis commun du chantier (docs/28 §3) : sans lui, `espace_de_travail
    # (None)` retombe sur un `mkdtemp()` et le livrable n'atteint jamais le projet.
    assert cable["run"]["projet_id"] == "prj-7f3a"


def test_le_mode_du_brief_voyage_avec_l_ordre_et_l_arbitre_reste_cable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le **mode** part avec l'objectif, l'**arbitre** est branché quoi qu'il arrive.

    Aucun des trois câblages n'est conditionné au mode : le moteur ignore de
    lui-même un arbitre qu'il n'a pas à consulter, et une seconde règle ici serait
    une règle de plus à tenir d'accord avec la sienne.
    """
    cable = deroule(monkeypatch, InMemoryEventBus(), tmp_path, mode_brief=MODE_BRIEF_AUTO)

    assert cable["run"]["mode_brief"] == MODE_BRIEF_AUTO
    assert isinstance(cable["arbitre_brief"], ArbitreBriefControlTower)


def test_un_bus_referme_sans_decision_fait_lever_le_brief_et_les_clarifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le run **échoue** — il ne repart pas avec un brief que personne n'a validé.

    Éprouvé sur les arbitres tels que l'hôte les a câblés, et non sur des arbitres
    montés pour l'occasion : ce que ce lot pouvait casser, c'est le branchement.
    """
    cable = deroule(monkeypatch, BusQuiSeReferme(), tmp_path)

    with pytest.raises(RuntimeError) as decision:
        asyncio.run(
            cable["arbitre_brief"](
                DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief())
            )
        )
    assert RUN in str(decision.value)

    with pytest.raises(RuntimeError) as reponses:
        asyncio.run(
            cable["arbitre_clarification"](
                DemandeClarification(
                    run_id=RUN, objectif="Objectif", brief=brief(), tour=1, tours_max=2
                )
            )
        )
    assert RUN in str(reponses.value)


def test_un_bus_referme_sans_decision_fait_refuser_l_action_sensible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La tâche est **soldée refusée**, comme sans validateur du tout (#9).

    L'asymétrie avec le test précédent est celle du dépôt et non un oubli : le
    brief refusé arrête le run avant qu'aucune tâche n'existe, l'action sensible
    refusée n'arrête que la tâche.
    """
    cable = deroule(monkeypatch, BusQuiSeReferme(), tmp_path)
    demande = DemandeValidation(
        task_id="t1",
        titre="Déployer en production",
        description="…",
        agent="dev",
        role="Développeur",
        raison="deploi",
    )

    approuve, detail = asyncio.run(cable["guardrails"].demande_validation(demande))

    assert approuve is False
    assert "refus par défaut" in detail


def test_sans_bus_rien_n_est_cable_et_les_deux_fail_safes_prennent_la_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le seul cas neuf du lot : un bus qu'on n'a pas pu **construire**.

    On ne lui invente pas un troisième refus — les deux existants suffisent, et
    c'est ce que ce test fixe : sans arbitre le moteur refusera le mode « humain »
    avant le premier appel modèle, sans validateur les garde-fous refusent toute
    action sensible.
    """
    cable = deroule(monkeypatch, None, tmp_path)

    assert cable["arbitre_brief"] is None
    assert cable["arbitre_clarification"] is None
    assert cable["guardrails"].validateur is None

    demande = DemandeValidation(
        task_id="t1",
        titre="Supprimer la base",
        description="…",
        agent="bdd",
        role="Base de données",
        raison="supprim",
    )
    approuve, detail = asyncio.run(cable["guardrails"].demande_validation(demande))
    assert approuve is False
    assert "aucun validateur humain configuré" in detail


def test_ouvrir_le_bus_ne_leve_jamais(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_bus_du_run` rend None au lieu de lever — sinon le run mourrait du bus.

    L'ouverture vivait **dans** le `try` de `_observer_ordres`, précisément
    pour qu'aucune façon de manquer Redis ne puisse emporter le run. La sortir de
    là sans reprendre la promesse aurait rendu fatal ce qui ne l'était pas.
    """

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("redis absent")

    monkeypatch.setattr(hote_detache, "RedisEventBus", refuse)

    assert hote_detache._bus_du_run() is None


class FauxProcess:
    """Un process qui vit : ni témoin posé, ni mort — le cas « parti mais lent »."""

    pid = 4321

    def poll(self) -> int | None:
        return None


def test_lancer_ne_refuse_plus_le_brief_humain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le refus de #443 a disparu, et l'ordre part avec son mode intact.

    Il existait parce que la décision n'avait aucun canal jusqu'au process ; elle
    en a un, donc le laisser en place refuserait le mode par **défaut** des
    lancements Control Tower pour une raison qui n'est plus vraie.
    """
    monkeypatch.setattr(
        HoteRunDetache, "_ouvrir_process", lambda self, atelier, journal: FauxProcess()
    )
    hote = HoteRunDetache(atelier=tmp_path, delai_demarrage_s=0.05)

    asyncio.run(hote.lancer(ordre()))

    ecrit = json.loads((tmp_path / RUN / hote_detache.FICHIER_ORDRE).read_text("utf-8"))
    assert ecrit["mode_brief"] == MODE_BRIEF_HUMAIN
    assert hote.en_vol(RUN) is True


# --- ⑥ L'issue publiée en partant ---------------------------------------------


@pytest.mark.parametrize(
    ("statuts", "issue", "code"),
    [
        ((STATUT_TERMINEE, STATUT_TERMINEE), (EXECUTION_TERMINEE, "2/2 tâche(s) réussie(s)"), 0),
        ((STATUT_TERMINEE, STATUT_ECHEC), (EXECUTION_ECHEC, "1/2 tâche(s) réussie(s)"), 1),
        ((STATUT_ECHEC, STATUT_BLOQUEE), (EXECUTION_ECHEC, "0/2 tâche(s) réussie(s)"), 1),
    ],
    ids=["tout-reussi", "une-echouee", "une-bloquee-en-aval"],
)
def test_l_hote_publie_son_issue_en_partant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys, statuts, issue, code
) -> None:
    """Ce qui manquait pour que ce module devienne le défaut (#446).

    Sans ce geste, un run détaché **terminé normalement** finissait `orphelin` : son
    dernier battement vieillissait, faute de statut de fin, et le verdict portait
    sur son hôte (« plus personne ne veille ») jamais sur son travail. Acceptable
    tant que le détaché était opt-in ; plus du tout une fois qu'il est le chemin
    normal de tous les lancements Control Tower.
    """
    rapport = RunReport(
        objectif="Objectif",
        resultats=tuple(resultat(f"t{i}", statut) for i, statut in enumerate(statuts)),
    )

    rendu, issues, battus = joue_main(
        monkeypatch, tmp_path, MoteurScripte(rapport=rapport)
    )
    capsys.readouterr()  # la synthèse Markdown, sans intérêt ici

    assert issues == [(RUN, *issue)]
    assert rendu == code
    # L'hôte a battu avant de partir : c'est ce qui rend l'attente humaine sûre
    # (#348), et le battement n'est retiré que **par** le soldage.
    assert battus == [RUN]


def test_les_deux_hotes_racontent_la_meme_issue_du_meme_rapport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Le seul point où les deux frontières n'ont pas le droit de diverger.

    Un run doit finir de la même façon qu'il ait vécu dans l'API ou dans un process
    à côté : c'est ce que lit un écran qui ne sait pas — et n'a pas à savoir — où le
    run s'exécutait. La comparaison est faite ici sur **le même rapport**, joué des
    deux côtés, plutôt que sur deux littéraux recopiés : deux constantes d'accord
    aujourd'hui sont deux constantes qu'un seul commit sépare.

    ⚠ Le **code de sortie**, lui, a le droit de diverger et ce n'est pas une
    incohérence à corriger au passage : l'issue compte les tâches échouées *et*
    bloquées, le code de sortie garde la lecture qu'il a partout ailleurs dans le
    dépôt (`maestro-run`). Personne ne lit le code d'un process sans appelant.
    """
    rapport = RunReport(
        objectif="Objectif",
        resultats=(resultat("t1"), resultat("t2", STATUT_ECHEC)),
    )

    _, issues, _ = joue_main(monkeypatch, tmp_path, MoteurScripte(rapport=rapport))
    capsys.readouterr()

    projection = ControlTowerState()
    service = ServiceExecutions(
        InMemoryEventBus(),
        projection,
        fabrique_moteur=lambda **_kwargs: MoteurScripte(rapport=rapport),
    )
    asyncio.run(service._derouler(ordre()))
    etat = projection.execution(RUN)
    assert etat is not None
    en_process = [e for e in etat.evenements if e.type == EVENEMENT_EXECUTION_STATUT][-1]

    _, statut, detail = issues[0]
    assert (statut, detail) == (en_process.statut, en_process.detail)
    assert (statut, detail) == (EXECUTION_ECHEC, "1/2 tâche(s) réussie(s)")


def test_une_annulation_ne_republie_rien(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La seule issue qui ne part pas d'ici — elle a déjà servi d'**ordre**.

    `ServiceExecutions._solder` l'a consignée côté API *avant* de demander
    l'interruption, et la republier ferait dire deux fois un fait acquis. Le code,
    lui, est `CODE_ANNULE` : ni 0 ni 1, parce que le dépôt refuse ailleurs de
    confondre une annulation avec un échec — un journal d'hôte qui rendrait 1 ferait
    chercher une panne là où quelqu'un a simplement dit stop.
    """
    bus = InMemoryEventBus()

    code, issues, _ = joue_main(
        monkeypatch, tmp_path, MoteurQuiSeFaitAnnuler(bus), bus=bus
    )

    assert code == hote_detache.CODE_ANNULE
    assert issues == []


def test_un_brief_refuse_se_publie_en_annulee_et_non_en_echec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rien n'a raté : quelqu'un a dit non, avant qu'une seule tâche existe.

    L'asymétrie avec l'annulation est celle du dépôt : la projection a peut-être
    déjà posé le statut sur la décision (`_applique_brief_decision`), mais rien n'a
    soldé le run côté hôte, et c'est ce geste qui pose sa `fin`. Rendre `echec`
    ferait chercher une panne là où le dispositif a fonctionné, et divergerait de
    l'hôte en process sur le seul point où les deux doivent dire la même chose.
    """
    code, issues, _ = joue_main(
        monkeypatch,
        tmp_path,
        MoteurScripte(erreur=BriefRefuse("brief refusé par l'humain")),
    )

    assert code == hote_detache.CODE_ANNULE
    assert issues == [(RUN, EXECUTION_ANNULEE, "brief refusé par l'humain")]


@pytest.mark.parametrize(
    "erreur",
    [OrchestratorError("plan invalide"), RuntimeError("boum")],
    ids=["orchestration", "imprevue"],
)
def test_une_panne_se_publie_en_echec_avec_son_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys, erreur
) -> None:
    """Un hôte qui tombe le **dit** : sans issue, son run vieillirait en orphelin.

    Le `detail` porte le type de l'exception autant que son message — c'est ce
    qu'un écran affiche à quelqu'un qui découvre le run soldé une heure plus tard,
    et « boum » tout seul ne dit pas d'où ça vient.
    """
    code, issues, _ = joue_main(monkeypatch, tmp_path, MoteurScripte(erreur=erreur))
    capsys.readouterr()  # la trace, imprimée pour le journal de l'hôte

    assert code == 1
    assert issues == [(RUN, EXECUTION_ECHEC, f"{type(erreur).__name__} : {erreur}")]


# --- ⑦ Le ramassage : ce que l'hôte a vu mourir --------------------------------


def test_une_depouille_est_rendue_une_fois_avec_son_code_et_sa_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le constat qui manquait : `runs_en_vol` dit ce qui vit, jamais ce qui a cessé.

    Un hôte qui fabrique quelque chose peut voir ce quelque chose mourir sans un mot
    — process tué, machine qui s'endort —, et le run reste alors `en_cours` dans la
    projection jusqu'à ce que le seuil d'orphelinat l'y laisse pour de bon. Chaque
    mort n'est rendue **qu'une fois** : l'appelant en fait un run soldé, et la
    redire ferait réécrire l'issue d'un run à chaque tour d'horloge.
    """
    hote, process = hote_double(monkeypatch, tmp_path, delai_issue_s=0.0)
    asyncio.run(hote.lancer(ordre()))
    (tmp_path / RUN / hote_detache.FICHIER_JOURNAL).write_text(
        "Traceback…\nMemoryError: out of memory", encoding="utf-8"
    )
    process[RUN].mourir(9)

    (defunt,) = hote.ramasser()

    assert defunt.run_id == RUN
    assert "code 9" in defunt.cause
    assert "MemoryError" in defunt.cause
    assert hote.ramasser() == ()


def test_la_mort_est_vue_tout_de_suite_mais_rendue_apres_le_delai_de_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le délai de grâce est ce qui rend le constat **sûr**, et il ne retarde rien d'autre.

    Un process publie son issue *puis* sort : entre les deux il y a un aller Redis
    et la pompe de l'API, et un `poll()` tombé pile dans cette fenêtre ferait solder
    en « echec » un run qui vient d'annoncer sa réussite. Ce que le délai ne retarde
    pas, en revanche, c'est la **récolte** : le run quitte le registre des vivants
    dès qu'il est mort, sans quoi il serait à la fois « en vol » et « ramassable ».
    """
    hote, process = hote_double(monkeypatch, tmp_path, delai_issue_s=DELAI_OBSERVATION_S)
    asyncio.run(hote.lancer(ordre()))
    process[RUN].mourir(0)

    assert hote.ramasser() == ()
    assert hote.runs_en_vol() == ()
    assert hote.en_vol(RUN) is False


def test_un_run_annule_passe_par_le_ramassage_comme_les_autres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Filtrer ici demanderait à l'hôte de savoir **pourquoi** son process est mort.

    C'est exactement ce que le contrat lui épargne : il rapporte un fait, l'appelant
    le confronte à la projection. Un run annulé y trouvera un statut déjà terminal et
    n'aura rien à solder. Au passage, `annuler` sur un process déjà mort rend `False`
    — il n'est plus porté — sans que sa dépouille se perde.
    """
    hote, process = hote_double(monkeypatch, tmp_path, delai_issue_s=0.0)
    asyncio.run(hote.lancer(ordre()))
    process[RUN].mourir(hote_detache.CODE_ANNULE)

    assert asyncio.run(hote.annuler(RUN, delai_s=0.1)) is False

    (defunt,) = hote.ramasser()
    assert f"code {hote_detache.CODE_ANNULE}" in defunt.cause


def test_les_runs_en_vol_recoltent_les_morts_au_passage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le pendant de l'`add_done_callback` de l'hôte en process, appelé par le cœur.

    Un `Popen` qu'on n'attend jamais laisse un zombie sous POSIX, et `poll()` est ce
    qui le récolte. Le cœur pose un battement par run en vol à chaque période : le
    ménage se fait donc tout seul, sans tâche à lui.
    """
    hote, process = hote_double(monkeypatch, tmp_path, delai_issue_s=0.0)
    asyncio.run(hote.lancer(ordre(run_id="run-a")))
    asyncio.run(hote.lancer(ordre(run_id="run-b")))
    assert hote.runs_en_vol() == ("run-a", "run-b")

    process["run-a"].mourir(0)

    assert hote.runs_en_vol() == ("run-b",)
    assert [defunt.run_id for defunt in hote.ramasser()] == ["run-a"]
