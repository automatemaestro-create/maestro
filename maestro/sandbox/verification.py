"""Jouer une commande du projet **dans une copie**, bornée dans le temps (#1160).

La mécanique de « ce que Maestro écrit dans un projet est vérifié en l'exécutant ».
Le domaine — quelles commandes, quel verdict, ce qui ne se joue pas sans une
personne — vit dans `maestro.outillage.verification` ; ici vivent les trois gestes
qui touchent au système, et eux seuls : faire une copie de vérification, trouver
l'interpréteur, jouer une commande et l'arrêter à temps.

## Pourquoi une copie, et jamais la racine ni le worktree

Une commande d'outillage **écrit** : `npm ci` remplit `node_modules`, `ruff format .`
réécrit les sources, un build pose `dist/`. Jouée là où Maestro écrit l'outillage,
chacune ferait un dégât différent selon le régime (docs/24 §2.4) :

- **à la racine d'un projet non versionné**, un formateur réécrirait les fichiers de
  la personne sans qu'elle l'ait demandé, et il n'y a ni diff ni retour arrière ;
- **dans le worktree d'un projet versionné**, tout ce qu'elle produit serait commité
  sur la branche d'outillage par `_solder_la_branche` — des sources reformatées, un
  build, des caches — et proposé à la fusion comme si c'était l'outillage.

La copie tranche les deux d'un coup, et elle vérifie **la bonne chose** : ce qu'un
agent trouve en arrivant, c'est-à-dire **ce que le périmètre du projet lui laisse
voir** — ni `.git`, ni `node_modules`, ni `.env`, ni `**/secrets/**` par défaut
(`Perimetre`), ni l'atelier `.maestro/`. Sur un projet versionné c'est l'arbre frais
du worktree ; `npm test` n'y passe donc que si l'installation qu'on écrit avant lui
passe aussi. C'est la leçon des produits comparables : les étapes de mise en route
de l'agent cloud de GitHub Copilot se vérifient dans l'environnement neuf où
l'agent travaillera, pas sur le poste de celui qui les écrit.

⚠ **Aucun dossier n'est écarté par son nom.** Les dossiers que l'analyse ignore
(`maestro.outillage.detection.IGNORES_DEFAUT`) sont des indices pour ne pas
compter des fichiers, pas une vérité sur le projet : un `vendor/` commité est la
source d'un projet Go, et le sauter ferait échouer sa construction à tort. Ce qui
borne la copie est sa **taille**, et elle le dit (`CopieImpossible`).

Elle n'y suit **aucun lien symbolique** (le vecteur d'évasion de docs/24 §2.5), naît
sous la racine jetable (`maestro.emplacements.jetable`) avec le pid de ce process
dans son nom — le ramassage (`maestro.sandbox.ramassage`) l'emporte donc si le
process meurt avant de la retirer — et elle est retirée en sortie, quoi qu'il
arrive.

## Le miroir d'une racine en place, pour vérifier une tâche (#1496)

La vérification d'une tâche (#1177) rejoue ce qui dit si le travail de l'agent tient.
Sur un projet **non versionné** (#839), l'espace de l'agent **est** la racine de la
personne, et y jouer les contrôles y laissait ce qu'ils écrivent : `.pytest_cache/`,
un rapport JUnit, un fichier de la personne réécrit par `ruff check --fix`, puis
compté à l'agent. Défaire après coup ne se peut pas : rien ne distingue ce que la
vérification a écrit de ce que la personne, son éditeur ou un client de
synchronisation ont écrit pendant ce temps (docs/45 §2). La vérification joue donc
dans un **miroir** de la racine (`MiroirDeVerification`), et rien n'est écrit dans la
racine — la contrainte tient par construction, pas par un ménage.

Le miroir n'est pas la copie d'outillage, parce qu'il ne répond pas à la même
question : celle-ci reproduit ce qu'un agent trouve **en arrivant**, celui-là
l'environnement que l'agent a **construit**, où « les tests passent » veut dire
quelque chose. Il porte donc **toute** la racine, et c'est ce qui garde au verdict
son sens :

- les **dépendances**, en **vraie copie** — ni jonction, ni lien, ni lien dur vers la
  racine : une écriture à travers eux (le cache de Vite dans `node_modules/.vite`, un
  `npm install` du vérificateur) atterrirait dans la racine ;
- le **`.env`** : un service démarré par une vérification le lisait dans la racine.
  Le secret est dupliqué sous la racine jetable, dans un dossier que seul son
  propriétaire lit (`tempfile.mkdtemp`), et retiré avec le miroir ;
- l'**atelier** des tâches (`.maestro/`), qu'une commande de vérification peut nommer.

Un **lien** de la racine est recréé, jamais suivi : s'il mène dans la racine —
`node_modules/.bin/vite`, relatif, ou un lien absolu —, il mène au même endroit
**du miroir** ; s'il en sort, il mène où il menait (l'interpréteur d'un `.venv`).
Là où le poste refuse de créer un lien (Windows sans mode développeur), un lien vers
un fichier est remplacé par une copie de ce fichier ; un lien vers un dossier manque.

Le miroir naît à la **première** vérification, et se **resynchronise** avant chaque
suivante, dans un seul sens : ce que la racine a de neuf ou de changé y entre, ce que
la vérification précédente y a laissé ou réécrit en sort — chaque côté par son
empreinte (taille, date à la nanoseconde), celle de `Workspace`. La copie se paie
donc une fois par miroir, et chaque vérification suivante ne recopie que ce qui a
bougé. Mesuré le 2026-10-13 sur le poste : 20 000 fichiers de 1,2 Ko se copient en
13,1 s un à un, 5,1 s à huit fils (`FILS_DE_COPIE`), et se relèvent en 0,09 s.

Un miroir vaut pour **toutes** les tâches d'un projet, puisqu'il suit la racine (#1497) :
l'exécuteur en garde un par run et par racine (`RegistreDesMiroirs`), copié à la
première vérification du run et retiré à sa fin, au lieu d'un par tâche — 20 s par
tâche sur la racine de S12. Sans exécuteur pour le garder (le worker d'une file, qui
ne voit pas la fin des runs), l'espace de la tâche a le sien et le retire avec lui.

⚠ **Ce que le miroir ne ferme pas**, nommé plutôt que tu : un **chemin absolu** qui
ramène à la racine. Une commande qui l'écrit en toutes lettres sort du dossier, et la
portée ne la joue pas ; mais un lanceur de `.venv` (`.venv/Scripts/pytest.exe`
embarque le chemin du `python.exe` de la racine) ou une installation éditable (un
`.pth` qui nomme `src/` de la racine) font encore lire la racine, et Python peut y
écrire ses `__pycache__`. Le `python` du `.venv` du miroir, lui, est celui du miroir.

Ses bornes (`FICHIERS_MAX_MIROIR`, `OCTETS_MAX_MIROIR`) comptent les dépendances, à la
différence de celles de la copie d'outillage : la racine de S12 (une application
Next.js) en porte 24 776 fichiers et 505 Mo, que les bornes de la copie (20 000
fichiers, 512 Mo) auraient rendue « non jouée » — une régression de #1177. Au-delà,
`CopieImpossible` le dit, et le contrôle n'est pas joué : il n'est **jamais** rejoué
dans la racine.

## L'interpréteur est celui des agents : bash

Les commandes écrites dans un projet le sont dans un bloc ```` ```bash ```` et un
agent les joue par son outil shell, qui est bash — Git Bash sous Windows. Les jouer
ici par `cmd.exe` vérifierait autre chose que ce qu'on écrit. D'où
`interprete()` : sous Windows, le `bash.exe` **de Git**, retrouvé à côté de `git`
et jamais par le `PATH` — `C:\\Windows\\System32\\bash.exe` est le lanceur de WSL,
qui jouerait la commande dans une autre machine. Sans bash, rien n'est joué, et le
verdict le dit (`None`) : une commande qu'on n'a pas pu jouer n'est pas une
commande qui a échoué.

## Arrêter à temps, et arrêter tout

Une commande a un délai ; au-delà, elle est arrêtée **avec sa descendance** — la
leçon de #291 et de `maestro.controltower.hote_detache` : un `npm run dev` est le
père d'un `node` qui tient un port, et tuer le seul père le laisserait tourner pour
une vérification déjà soldée. Le délai atteint n'est pas un échec en soi : pour un
démarrage c'est même la réussite (le serveur tourne), et c'est au domaine d'en
juger (`Execution.expiree`).

⚠ **Sous Windows, `taskkill /T` ne suffit pas** — mesuré le 2026-09-24 : les
processus que Git Bash lance (`sleep 30 & sleep 30`) échappent à l'arbre des
parents Windows, parce que l'émulation de `fork`/`exec` de MSYS en rompt la
filiation ; ils survivaient à l'arrêt et tenaient la sortie ouverte. La commande
naît donc **suspendue**, est placée dans un **Job Object** avant son premier
instant de vie, puis relâchée : tout ce qu'elle lancera y naît aussi, et c'est le
job qu'on termine. Sous POSIX, le groupe de processus fait la même chose
(`start_new_session`, puis `killpg`). La mécanique vit dans `maestro.sandbox.arbre`
depuis #1279 : le confinement de la session d'un agent en a besoin telle quelle.

**On attend le processus, pas la fin de sa sortie.** Un `dotnet build` laisse un
serveur de compilation, un `gradle` son démon : ils héritent de la sortie et la
tiennent ouverte bien après que la commande a rendu la main. Attendre la fin de la
sortie les ferait passer pour « trop longs ». La sortie est donc lue par un fil à
part, le **code de retour** de la commande fait foi dès qu'elle l'a rendu, et ce
qu'elle laisse derrière elle est arrêté avec l'arbre — dans la copie, où rien ne se
perd.

⚠ **Le code de retour fait foi, jamais le texte.** La sortie est gardée pour être
**montrée** à la personne, jamais lue pour deviner une panne (#1315).

## Démarrer un service, attendre qu'il réponde, l'arrêter (#1432)

« Le serveur répond 200 » ne se constate pas d'une seule commande qui rend la main :
il faut démarrer le service, l'interroger jusqu'à ce qu'il réponde, puis l'arrêter.
Ce verbe-là appartient à Maestro (`demarrer`, docs/44), pas à la commande qu'on lui
donne. Le service naît dans son arbre comme toute commande ; une **sonde** — une
commande ordinaire qui constate une fois que le service répond — est rejouée tant
qu'il tourne, jusqu'à rendre 0 ; puis l'arbre est arrêté.

Sans ce verbe, le juge écrivait lui-même l'attente et l'arrêt. Mesuré sur le passage
du banc `20261004-202743` (S12) : un `node -e` qui lançait `npm start`, l'interrogeait
puis le tuait par `taskkill` — une ligne que l'antivirus du poste a prise pour un
cheval de Troie (`Trojan:Win32/SuspExec.SE`), si bien que Windows a refusé de créer
le processus (`[WinError 5] Accès refusé`). Le service se démarre désormais par sa
commande nue (`npm start`), et l'attente comme l'arrêt se font ici.

La sonde est jouée **une fois avant** le service : si elle tient déjà, ce qui lui
répond n'est pas lui (un port qu'un autre processus tient), et rien n'est démarré.

## Un service à la fois sur le poste (#1449)

Un port est au **poste**, pas à l'espace de travail : deux vérifications qui
démarrent chacune leur service sur le port que leurs critères nomment
(`PORT=4321 npm start`) se le disputent, quel que soit le dossier où elles jouent.
Mesuré sur le run `3a7fa26ba927` : deux pages livrées à quelques secondes d'écart,
la sonde de la seconde a trouvé le service de la première, et son critère n'a pas
été joué — la tâche a fini en échec pour un port qu'elle ne tenait pas.

D'où le **créneau** (`creneau_de_service`) : un seul service démarré par une
vérification à la fois, pour tous les process de Maestro du poste — l'hôte d'un
run, un worker, l'API —, que l'appelant tient de la sonde préalable jusqu'à la
**mort constatée** du service. Car l'arrêt attend que l'arbre soit mort, et pas
seulement qu'on l'ait demandé (`_arreter`) : le tour suivant trouve le port rendu.

Le créneau ne pose rien sur le disque. Sous POSIX, c'est un `flock` sur la racine
jetable elle-même (`maestro.emplacements.racine_jetable`) ; sous Windows, un mutex
nommé d'après elle. Le système rend l'un comme l'autre quand leur détenteur meurt :
un process tué ne laisse aucun verrou à ramasser, et ni le ménage ni le ramassage
n'ont rien à juger (#1454). Les process qui partagent un répertoire temporaire
partagent le créneau — c'est le poste, et c'est ce qui laisse une suite de tests en
prendre un à elle.

## Le port d'une vérification n'est pas celui des agents (#1487)

Le créneau sérialise les vérifications **entre elles**, pas avec les agents. Or les
agents des tâches parallèles (#1299) se vérifient eux-mêmes sur le port que nomment
leurs critères — celui où la vérification démarrait aussi son service. Mesuré sur le
run `c05bf6263236` : un agent en correction « libère son port » en tuant ce qui
l'écoute (`netstat … | taskkill //PID … //F`), et c'est le service de la
vérification voisine qui meurt. Elle lit « le service a rendu la main (code 1) »,
et la tâche vérifiée reçoit un échec qu'elle n'a pas causé. Un code de sortie ne dit
pas qui a arrêté le service (docs/44) : ce qui ne se distingue pas se **prévient**.

Le service d'une vérification reçoit donc **son** port (`port_de_verification`),
dans la variable `VARIABLE_PORT`, posée pour lui et pour sa sonde. Le vérificateur
l'écrit à la place du port d'un critère qui laisse le choisir (`PORT=$MAESTRO_PORT
npm start`) : c'est le modèle qui juge si le port est un réglage du service. Ce port
est choisi **dans le créneau**, juste avant le démarrage — deux vérifications ne
tirent jamais le même —, au hasard dans `PORTS_DE_VERIFICATION`, et constaté libre
**adresse par adresse** : sous Windows, un `bind` sur `0.0.0.0` réussit à côté d'un
serveur qui n'écoute que `127.0.0.1` (mesuré sur le poste). Un agent qui tue ce qui
écoute le port de son critère ne l'atteint plus, et ne juge plus sa page sur la
réponse d'un service qui n'est pas le sien. Les agents, eux, essaient leurs serveurs
sur le port que Maestro réserve à chacun dans une plage voisine (`maestro.sandbox.ports`,
#1491).
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from maestro.emplacements import jetable, racine_jetable
from maestro.fichiers import retirer_arbre
from maestro.sandbox.arbre import Arbre
from maestro.sandbox.ports import ESSAIS_PORT, port_libre, tirages

# Ré-exporté : le moteur de vérification en fait un contrôle « non joué » (#1487).
from maestro.sandbox.ports import PortIndisponible as PortIndisponible

#: Le préfixe d'une copie de vérification — sous le préfixe commun des espaces de
#: Maestro, donc candidate au ramassage si son process meurt avant de la retirer. Le
#: miroir d'une racine en place (#1496) naît sous le même.
PREFIXE_COPIE = "maestro-verif-"

#: Les bornes d'un miroir de vérification (#1496), **dépendances comprises**. La
#: racine de S12 (une application Next.js, mesurée le 2026-10-13) : 24 776 fichiers,
#: 505 Mo. Quatre fois cette mesure : un projet web non versionné se vérifie, une
#: vérification ne vaut pas la copie d'un disque.
FICHIERS_MAX_MIROIR = 100_000
OCTETS_MAX_MIROIR = 2 * 1024 * 1024 * 1024

#: Combien de fichiers le miroir copie de front. Mesuré sur le poste le 2026-10-13 :
#: 20 000 fichiers de 1,2 Ko en 13,1 s un à un, 5,1 s à huit, 4,8 s à seize — copier
#: un fichier, c'est surtout attendre le système, et des fils recouvrent ces attentes.
FILS_DE_COPIE = 8

#: La part de la sortie gardée, **la fin** : c'est là qu'une commande dit pourquoi
#: elle a échoué. Assez pour lire une trace ou un résumé de tests, pas assez pour
#: faire d'un manifeste un journal de compilation.
CARACTERES_SORTIE = 4000

#: Ce qui est posé dans l'environnement de la commande, et rien d'autre : `CI=1` est
#: le signal que les outils reconnaissent pour « personne ne répondra » — un
#: lanceur de tests ne passe pas en mode surveillance, un gestionnaire ne pose pas
#: de question. C'est la situation exacte d'une vérification, et celle d'un agent.
ENVIRONNEMENT_AJOUTE: dict[str, str] = {"CI": "1"}

#: Le délai laissé au fil de lecture pour finir, une fois l'arbre arrêté.
_DELAI_VIDANGE_S = 5.0

#: Ce que le fil de lecture garde en mémoire, en octets : la fin de la sortie, avec
#: assez de marge pour qu'un caractère UTF-8 coupé en tête ne compte pas.
_OCTETS_GARDES = CARACTERES_SORTIE * 8

#: L'intervalle entre deux passages de la sonde d'un service qui démarre (#1432). Un
#: passage lance un bash : sous Windows, une à deux centaines de millisecondes.
PERIODE_SONDE_S = 1.0

#: La variable où le service d'une vérification et sa sonde trouvent le port que
#: Maestro leur réserve (#1487) — posée pour eux seuls, jamais pour un agent.
VARIABLE_PORT = "MAESTRO_PORT"

#: Où Maestro tire le port d'une vérification (#1487). **Sous** les ports que le
#: système distribue de lui-même aux connexions sortantes — 32768 et au-delà sous
#: Linux, 49152 et au-delà sous Windows et macOS, où vivent aussi les plages que
#: Hyper-V se réserve — : constaté libre, le port ne peut pas être repris par une
#: connexion du service qui démarre (`npm ci` en ouvre des dizaines). **Au-dessus**
#: des ports qu'un projet choisit de lui-même (3000, 4321, 8080…) et de ceux que
#: Maestro donne aux agents pour leurs essais (`maestro.sandbox.ports.PORTS_D_ESSAI`,
#: #1491) : les deux plages ne se croisent pas.
PORTS_DE_VERIFICATION = range(20_000, 32_768)

#: Le nom du mutex qui tient le créneau des services sous Windows (#1449), suivi
#: d'une empreinte de la racine jetable. `Local\` : la session de la personne, où
#: vivent tous les process de Maestro qu'elle a lancés.
PREFIXE_CRENEAU = "Local\\maestro-creneau-service-"

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Une instance à ce module : les signatures posées ici ne touchent pas celles
    # de `maestro.sandbox.arbre`.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    #: `WAIT_OBJECT_0`, `WAIT_ABANDONED` — le détenteur est mort sans rendre le
    #: mutex, qui revient à celui qui l'attendait — et `WAIT_TIMEOUT`.
    _OBTENU = 0x00000000
    _ABANDONNE = 0x00000080
    _PAS_ENCORE = 0x00000102

    @contextmanager
    def _mutex(nom: str, abandon: threading.Event | None) -> Iterator[None]:
        """Le mutex `nom`, attendu puis tenu — par le fil qui l'attend, seul à le rendre."""
        poignee = _kernel32.CreateMutexW(None, False, nom)
        if not poignee:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            pas_ms = int(_PAS_CRENEAU_S * 1000)
            while (etat := _kernel32.WaitForSingleObject(poignee, pas_ms)) == _PAS_ENCORE:
                _si_abandon(abandon)
            if etat not in (_OBTENU, _ABANDONNE):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                _kernel32.ReleaseMutex(poignee)
        finally:
            _kernel32.CloseHandle(poignee)

else:
    import fcntl

#: Le pas auquel l'attente du créneau regarde si on l'a abandonnée.
_PAS_CRENEAU_S = 0.2


class CreneauAbandonne(Exception):
    """L'attente du créneau des services a été abandonnée — la vérification est annulée.

    Pas une `OSError` : le système n'a rien refusé, personne n'attend plus ce service.
    """


class CopieImpossible(Exception):
    """La copie de vérification n'a pas pu être faite — avec son motif.

    `motif` est un code court (`trop-de-fichiers`, `trop-lourd`, `dans-la-racine`,
    `espace-indisponible`) ; le message dit à la personne pourquoi rien n'a été joué.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class Execution:
    """Ce qu'une commande a rendu : son code, la fin de sa sortie, sa durée.

    `code` vaut `None` quand la commande n'a pas rendu la main dans son délai —
    elle a alors été arrêtée avec sa descendance (`expiree`). La sortie mêle ce
    qu'elle a écrit sur ses deux flux, dans l'ordre où elle l'a écrit.
    """

    code: int | None
    sortie: str
    duree_s: float
    expiree: bool = False


@dataclass(frozen=True)
class Demarrage:
    """Ce qu'un service démarré puis sondé a rendu (#1432).

    `repondu` : la sonde a rendu 0 pendant que le service tournait — la seule
    réussite. `deja` : la sonde tenait **avant** le démarrage, si bien que ce qui
    lui répond n'est pas le service ; rien n'a alors été démarré. `code` : le code
    du service s'il s'est arrêté de lui-même, `None` s'il tournait encore quand il
    a été arrêté. `sortie` : la fin de ce que le service a écrit. `sonde` : le
    dernier passage de la sonde.
    """

    repondu: bool
    code: int | None
    sortie: str
    sonde: Execution
    duree_s: float
    deja: bool = False


class SondeNonLancee(OSError):
    """La **sonde** d'un service n'a pas pu être lancée — et non le service lui-même.

    Même nature que l'`OSError` de `jouer` (l'interpréteur ne s'est pas lancé), même
    message ; le type dit seulement laquelle des deux commandes n'est pas partie.
    """


@contextmanager
def copie_de_verification(
    racine: Path,
    *,
    exclus: Sequence[re.Pattern[str]] = (),
    hors: tuple[str, ...] = (),
    fichiers_max: int = 20_000,
    octets_max: int = 512 * 1024 * 1024,
) -> Iterator[Path]:
    """Une copie de `racine` où jouer des commandes, retirée en sortie.

    Ce qui n'est **pas** copié : ce que `exclus` retire (le périmètre du projet,
    motifs compilés), les chemins relatifs nommés dans `hors` (l'atelier de
    Maestro), et tout lien symbolique — exactement le parcours de
    `maestro.sandbox.en_place.fichiers_du_perimetre`. Les dates de modification
    sont gardées : un outil qui compare des dates (`make`) voit l'arbre tel qu'il
    est.

    Lève `CopieImpossible` si l'arbre dépasse `fichiers_max` ou `octets_max` — une
    vérification ne vaut pas la copie d'un disque —, ou si la copie devait naître
    **dans** la racine (un `TEMP` pointé dans le projet : ce serait écrire dans le
    projet de la personne).
    """
    source = Path(racine).resolve()
    try:
        parent = jetable(PREFIXE_COPIE)
    except OSError as exc:
        raise CopieImpossible(
            "espace-indisponible",
            f"aucun répertoire temporaire n'a pu accueillir la copie de vérification : {exc}",
        ) from exc
    try:
        if _sous(parent.resolve(), source):
            raise CopieImpossible(
                "dans-la-racine",
                f"la copie de vérification naîtrait dans le projet ({parent}) — le "
                "répertoire temporaire du poste pointe dans sa racine.",
            )
        copie = parent / "projet"
        copie.mkdir()
        _copier(source, copie, tuple(exclus), hors, fichiers_max, octets_max)
        yield copie
    finally:
        retirer_arbre(parent)


@dataclass(frozen=True)
class _Entree:
    """Ce qu'un chemin de la racine est, relevé pour que le miroir le reproduise (#1496).

    `nature` vaut `dossier`, `fichier` ou `lien`. Un fichier porte son `empreinte`
    (taille, date en ns). Un lien porte ce qu'il désigne : `interieur`, le chemin
    relatif à la racine de ce qu'il désigne quand c'est dans la racine — le miroir le
    fait mener au même endroit du miroir —, sinon `exterieur`, le chemin absolu où il
    mène ; `dossier` dit s'il désigne un dossier.
    """

    nature: str
    empreinte: tuple[int, int] = (0, 0)
    interieur: str | None = None
    exterieur: str = ""
    dossier: bool = False


class MiroirDeVerification:
    """Le reflet d'une racine en place, où la vérification d'une tâche joue (#1496).

    Voir l'en-tête : il porte **toute** la racine — dépendances et `.env` compris —,
    recrée ses liens sans jamais les suivre, naît à la première `synchroniser`, suit la
    racine à chaque suivante dans un seul sens, et disparaît à `fermer`. Il n'écrit
    **rien** dans la racine : il la lit.
    """

    def __init__(
        self,
        racine: Path,
        *,
        fichiers_max: int | None = None,
        octets_max: int | None = None,
    ) -> None:
        self._racine = Path(racine)
        self._fichiers_max = fichiers_max
        self._octets_max = octets_max
        self._parent: Path | None = None
        #: Ce que le miroir a reçu de la racine, chemin par chemin : ce qu'était le
        #: chemin dans la racine, et l'empreinte de ce qu'il en a fait dans le miroir.
        #: Les deux côtés comptent — une vérification réécrit le miroir sans que la
        #: racine bouge, l'agent réécrit la racine sans que le miroir bouge.
        self._recus: dict[str, tuple[_Entree, tuple[object, ...]]] = {}

    @property
    def racine(self) -> Path:
        """La racine que ce miroir reflète."""
        return self._racine

    @property
    def chemin(self) -> Path | None:
        """Le dossier du miroir — `None` tant qu'aucune vérification ne l'a fait naître."""
        return None if self._parent is None else self._parent / "projet"

    def synchroniser(self) -> Path:
        """Rend le miroir conforme à la racine, à cet instant, et rend son chemin.

        Lève `CopieImpossible` au-delà des bornes, si le poste n'a pas la place de la
        copie, ou si le miroir naîtrait dans la racine ; rien n'est alors écrit dans
        la racine, et c'est à l'appelant de dire que le contrôle n'a pas été joué.
        """
        source = self._racine.resolve()
        attendu = _relever(
            source,
            FICHIERS_MAX_MIROIR if self._fichiers_max is None else self._fichiers_max,
            OCTETS_MAX_MIROIR if self._octets_max is None else self._octets_max,
        )
        copie = self._naitre(source)
        a_jour = self._elaguer(copie, attendu)
        # Les dossiers du miroir où l'on peut écrire : ceux que l'élagage a parcourus,
        # et ceux qu'on vient de créer. Un chemin qu'on n'a pas su effacer — un lien
        # qu'une vérification aurait posé vers la racine — n'est jamais un passage :
        # rien ne s'écrit à travers lui, ni dessous (trié, un dossier précède son contenu).
        surs = {""}
        a_copier: list[tuple[str, _Entree]] = []
        for relatif, entree in sorted(attendu.items()):
            if relatif.rpartition("/")[0] not in surs:
                continue
            if entree.nature == "dossier":
                if relatif not in a_jour:
                    try:
                        (copie / relatif).mkdir()
                    except OSError:
                        continue
                surs.add(relatif)
            elif relatif in a_jour or os.path.lexists(copie / relatif):
                continue
            elif entree.nature == "lien":
                self._lier(source, copie, relatif, entree)
            else:
                a_copier.append((relatif, entree))
        self._copier(source, copie, a_copier)
        return copie

    def fermer(self) -> None:
        """Retire le miroir — sans jamais suivre un lien vers ce qu'il désigne."""
        if self._parent is not None:
            retirer_arbre(self._parent)
        self._parent = None
        self._recus.clear()

    def _naitre(self, source: Path) -> Path:
        """Le dossier du miroir, créé à la première synchronisation, hors de la racine."""
        if self._parent is not None:
            return self._parent / "projet"
        try:
            parent = jetable(PREFIXE_COPIE)
        except OSError as exc:
            raise CopieImpossible(
                "espace-indisponible",
                "aucun répertoire temporaire n'a pu accueillir la copie de vérification : "
                f"{exc}",
            ) from exc
        if _sous(parent.resolve(), source):
            retirer_arbre(parent)
            raise CopieImpossible(
                "dans-la-racine",
                f"la copie de vérification naîtrait dans le projet ({parent}) — le "
                "répertoire temporaire du poste pointe dans sa racine.",
            )
        (parent / "projet").mkdir()
        self._parent = parent
        return parent / "projet"

    def _elaguer(self, copie: Path, attendu: Mapping[str, _Entree]) -> set[str]:
        """Retire du miroir ce qui n'est plus la racine ; rend les chemins déjà conformes.

        Un chemin que la racine n'a pas — ce qu'une vérification a laissé, ce que
        l'agent a retiré —, ou qu'elle a sous une autre nature, part. Un fichier ou un
        lien reste s'il est ce que le miroir a reçu de la racine, inchangé des deux
        côtés ; sinon il part aussi, et sera recopié. Un dossier conforme est parcouru,
        jamais un lien.
        """
        a_jour: set[str] = set()
        pile = [""]
        while pile:
            relatif_dossier = pile.pop()
            base = copie / relatif_dossier if relatif_dossier else copie
            try:
                with os.scandir(base) as entrees:
                    triees = list(entrees)
            except OSError:
                continue
            for entree in triees:
                relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
                voulu = attendu.get(relatif)
                lien = _est_un_lien(entree)
                if not lien and entree.is_dir(follow_symlinks=False):
                    if voulu is not None and voulu.nature == "dossier":
                        a_jour.add(relatif)
                        pile.append(relatif)
                    else:
                        retirer_arbre(Path(entree.path))
                    continue
                recu = self._recus.get(relatif)
                if (
                    voulu is not None
                    and voulu.nature != "dossier"
                    and recu is not None
                    and recu == (voulu, _etat_dans_le_miroir(entree, lien))
                ):
                    a_jour.add(relatif)
                    continue
                self._recus.pop(relatif, None)
                _effacer(Path(entree.path))
        return a_jour

    def _lier(self, source: Path, copie: Path, relatif: str, entree: _Entree) -> None:
        """Recrée un lien de la racine — vers le miroir s'il menait dans la racine."""
        lien = copie / relatif
        if entree.interieur is not None:
            cible = os.path.relpath(copie / entree.interieur, lien.parent)
        else:
            cible = entree.exterieur
        try:
            os.symlink(cible, lien, target_is_directory=entree.dossier)
        except OSError:
            # Le poste refuse les liens (Windows sans mode développeur) : un fichier
            # désigné est recopié à la place, un dossier désigné manque au miroir.
            designe = (
                source / entree.interieur if entree.interieur is not None else Path(cible)
            )
            if entree.dossier or not designe.is_file():
                return
            try:
                shutil.copy2(designe, lien)
            except OSError:
                return
        try:
            etat = os.stat(lien, follow_symlinks=False)
        except OSError:
            return
        if lien.is_symlink():
            self._recus[relatif] = (entree, ("lien", os.readlink(lien)))
        else:
            self._recus[relatif] = (entree, ("fichier", etat.st_size, etat.st_mtime_ns))

    def _copier(self, source: Path, copie: Path, a_copier: list[tuple[str, _Entree]]) -> None:
        """Copie de front les fichiers qui manquent au miroir, après avoir vu la place."""
        if not a_copier:
            return
        besoin = sum(entree.empreinte[0] for _, entree in a_copier)
        try:
            libre = shutil.disk_usage(copie).free
        except OSError:
            libre = besoin
        if besoin > libre:
            raise CopieImpossible(
                "espace-indisponible",
                f"la copie de vérification demande {besoin // (1024 * 1024)} Mo, et le "
                f"répertoire temporaire du poste n'en a que {libre // (1024 * 1024)} de libres.",
            )

        def un(relatif: str) -> tuple[object, ...] | None:
            cible = copie / relatif
            try:
                shutil.copy2(source / relatif, cible, follow_symlinks=False)
                etat = os.stat(cible, follow_symlinks=False)
            except OSError:  # fichier verrouillé ou illisible : absent du miroir,
                _effacer(cible)  # et pas à moitié
                return None
            return ("fichier", etat.st_size, etat.st_mtime_ns)

        with ThreadPoolExecutor(max_workers=FILS_DE_COPIE) as fils:
            etats = list(fils.map(un, (relatif for relatif, _ in a_copier)))
        for (relatif, entree), etat in zip(a_copier, etats, strict=True):
            if etat is not None:
                self._recus[relatif] = (entree, etat)


@contextmanager
def miroir_de_verification(
    racine: Path,
    *,
    fichiers_max: int | None = None,
    octets_max: int | None = None,
) -> Iterator[MiroirDeVerification]:
    """Un miroir de `racine` (#1496), retiré en sortie quoi qu'il arrive.

    Rien n'est copié tant qu'on ne le synchronise pas : un espace dont aucune
    livraison n'est vérifiée ne coûte rien.
    """
    miroir = MiroirDeVerification(racine, fichiers_max=fichiers_max, octets_max=octets_max)
    try:
        yield miroir
    finally:
        miroir.fermer()


class RegistreDesMiroirs:
    """Les miroirs qu'un exécuteur garde d'une tâche à l'autre : un par run et par racine (#1497).

    Le miroir d'une racine (#1496) se resynchronise avant chaque usage : il vaut pour
    toutes les tâches du projet, qui passent déjà une à une dans sa racine (l'atelier,
    `maestro.engine.executor`). Tenu par l'espace d'une seule tâche, il se recopiait et
    se retirait à chaque tâche — 20 s sur la racine de S12. Gardé ici, il se copie à la
    première vérification du run, se resynchronise aux suivantes, et part à la fin du run.

    Le temps d'un run est **borné par qui le tient** — `ouvrir`, puis `retirer`, que
    `LocalExecutor.tient_le_run` joue autour des tâches de la boucle. Un run qu'on n'a
    pas ouvert n'obtient aucun miroir, et l'espace de sa tâche garde le sien : c'est ce
    qui empêche un exécuteur dont personne ne voit la fin des runs — le worker d'une
    file — d'accumuler un miroir par run.

    Un miroir ne sert qu'un espace à la fois (`prete`) : prêté, il est refusé au
    suivant, qui a le sien. Une réalisation détachée par un time-out (#64), qui peut
    encore jouer, ne le partage donc jamais avec la tâche d'après ; et un retrait
    demandé pendant le prêt attend son retour. Si le process meurt avant, le miroir est
    sous la racine jetable, marqué de son pid : le ramassage l'emporte
    (`maestro.sandbox.ramassage`).

    Sûr entre fils : un retrait, qui dure le temps d'effacer un arbre, se joue hors de
    la boucle asyncio.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        #: Les runs tenus, et par combien de bornes : un run tenu deux fois garde ses
        #: miroirs jusqu'à la dernière sortie.
        self._runs: dict[str, int] = {}
        self._miroirs: dict[tuple[str, Path], MiroirDeVerification] = {}
        self._pretes: set[tuple[str, Path]] = set()

    def ouvrir(self, run_id: str) -> None:
        """Le run `run_id` commence : ses tâches partageront le miroir de chaque racine."""
        with self._verrou:
            self._runs[run_id] = self._runs.get(run_id, 0) + 1

    def retirer(self, run_id: str) -> None:
        """Le run `run_id` finit : ses miroirs partent — un miroir prêté, à son retour."""
        with self._verrou:
            restantes = self._runs.get(run_id, 0) - 1
            if restantes > 0:
                self._runs[run_id] = restantes
                return
            self._runs.pop(run_id, None)
            cles = [cle for cle in self._miroirs if cle[0] == run_id and cle not in self._pretes]
            partants = [self._miroirs.pop(cle) for cle in cles]
        for miroir in partants:
            miroir.fermer()

    @contextmanager
    def prete(self, run_id: str, racine: Path) -> Iterator[MiroirDeVerification | None]:
        """Le miroir de `racine` pour le run `run_id`, prêté le temps du bloc.

        `None` quand le run n'est pas tenu, ou que le miroir sert déjà un autre espace :
        l'espace aura alors le sien, comme avant #1497. Rien n'est copié ici — le miroir
        naît à la première vérification qui le synchronise.
        """
        cle = (run_id, Path(racine).resolve())
        with self._verrou:
            miroir: MiroirDeVerification | None = None
            if run_id in self._runs and cle not in self._pretes:
                miroir = self._miroirs.get(cle)
                if miroir is None:
                    miroir = self._miroirs[cle] = MiroirDeVerification(racine)
                self._pretes.add(cle)
        if miroir is None:
            yield None
            return
        try:
            yield miroir
        finally:
            with self._verrou:
                self._pretes.discard(cle)
                # Le run s'est fini pendant le prêt : son retrait attendait ce retour.
                orphelin = run_id not in self._runs
                if orphelin:
                    self._miroirs.pop(cle, None)
            if orphelin:
                miroir.fermer()


def _relever(source: Path, fichiers_max: int, octets_max: int) -> dict[str, _Entree]:
    """Ce que la racine porte, chemin par chemin — bornes comptées pendant le relevé.

    Rien n'est exclu par son nom (voir l'en-tête), aucun lien n'est suivi ; un dossier
    devenu illisible est sauté, jamais fatal. Un fichier spécial (tube, socket) n'est
    pas un fichier du projet.
    """
    releve: dict[str, _Entree] = {}
    fichiers = 0
    octets = 0
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        base = source / relatif_dossier if relatif_dossier else source
        try:
            with os.scandir(base) as entrees:
                triees = list(entrees)
        except OSError:
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if _est_un_lien(entree):
                lien = _entree_de_lien(entree, source)
                if lien is not None:
                    releve[relatif] = lien
                continue
            if entree.is_dir(follow_symlinks=False):
                releve[relatif] = _Entree("dossier")
                pile.append(relatif)
                continue
            if not entree.is_file(follow_symlinks=False):
                continue
            try:
                etat = entree.stat(follow_symlinks=False)
            except OSError:
                continue
            fichiers += 1
            octets += etat.st_size
            if fichiers > fichiers_max:
                raise CopieImpossible(
                    "trop-de-fichiers",
                    f"le projet compte plus de {fichiers_max} fichiers, dépendances "
                    "comprises : trop pour une copie de vérification.",
                )
            if octets > octets_max:
                raise CopieImpossible(
                    "trop-lourd",
                    f"le projet pèse plus de {octets_max // (1024 * 1024)} Mo, dépendances "
                    "comprises : trop pour une copie de vérification.",
                )
            releve[relatif] = _Entree("fichier", (etat.st_size, etat.st_mtime_ns))
    return releve


def _est_un_lien(entree: os.DirEntry[str]) -> bool:
    """Un lien symbolique, ou une jonction Windows — ce que le miroir recrée sans le suivre."""
    if entree.is_symlink():
        return True
    jonction = getattr(entree, "is_junction", None)  # Python 3.12 et au-delà
    return bool(jonction is not None and jonction())


def _entree_de_lien(entree: os.DirEntry[str], source: Path) -> _Entree | None:
    """Ce qu'un lien de la racine désigne, résolu jusqu'au bout — `None` s'il est illisible."""
    try:
        os.readlink(entree.path)
    except OSError:
        return None
    reel = Path(os.path.realpath(entree.path))
    dossier = reel.is_dir()
    if _sous(reel, source):
        return _Entree("lien", interieur=reel.relative_to(source).as_posix(), dossier=dossier)
    return _Entree("lien", exterieur=str(reel), dossier=dossier)


def _etat_dans_le_miroir(entree: os.DirEntry[str], lien: bool) -> tuple[object, ...] | None:
    """L'empreinte d'un chemin du miroir : ce que désigne un lien, taille et date d'un fichier."""
    try:
        if lien:
            return ("lien", os.readlink(entree.path))
        etat = entree.stat(follow_symlinks=False)
    except OSError:
        return None
    return ("fichier", etat.st_size, etat.st_mtime_ns)


def _effacer(chemin: Path) -> None:
    """Efface un fichier ou un lien du miroir — le lien, jamais ce qu'il désigne."""
    try:
        chemin.unlink()
        return
    except PermissionError:
        pass
    except OSError:
        return
    try:
        if chemin.is_symlink() or chemin.is_dir():
            os.rmdir(chemin)  # un lien vers un dossier, sous Windows
        else:
            os.chmod(chemin, stat.S_IWRITE)  # lecture seule, sous Windows
            chemin.unlink()
    except OSError:
        pass


@contextmanager
def dossier_de_sonde() -> Iterator[Path]:
    """Un dossier vide où demander au poste ce qu'il a, retiré en sortie (#1343).

    Pour les questions qui ne portent sur aucun projet — *ce programme est-il
    installé ?*, posée avant même que le projet existe. Même naissance qu'une copie
    de vérification (sous la racine jetable, pid dans le nom, donc ramassée si
    le process meurt avant de la retirer) : une sonde ne s'exécute jamais dans le
    répertoire courant de l'API, qui n'est le dossier de personne.

    Lève `CopieImpossible` (`espace-indisponible`) si aucun répertoire temporaire ne
    l'accueille.
    """
    try:
        dossier = jetable(PREFIXE_COPIE)
    except OSError as exc:
        raise CopieImpossible(
            "espace-indisponible",
            f"aucun répertoire temporaire n'a pu accueillir la sonde du poste : {exc}",
        ) from exc
    try:
        yield dossier
    finally:
        retirer_arbre(dossier)


@contextmanager
def creneau_de_service(
    environnement: Mapping[str, str] | None = None,
    *,
    abandon: threading.Event | None = None,
) -> Iterator[None]:
    """Le créneau des services démarrés par une vérification — attendu, puis tenu (#1449).

    Rend la main quand plus aucun autre service de vérification ne vit sur le poste,
    dans ce process ou un autre (voir l'en-tête). Sans délai d'attente : chaque
    détenteur est borné par les délais de sa vérification, et un détenteur mort rend
    le créneau. `abandon`, posé pendant l'attente, l'interrompt (`CreneauAbandonne`)
    : une vérification annulée ne démarre plus rien, et son fil ne retient pas la
    sortie du process. Lève `OSError` si le système refuse le créneau — à
    l'appelant d'en faire un verdict.
    """
    racine = racine_du_creneau(environnement)
    if sys.platform == "win32":
        with _mutex(nom_du_creneau(racine), abandon):
            yield
    else:
        racine.mkdir(parents=True, exist_ok=True)
        # Un descripteur par entrée : deux fils d'un même process s'excluent aussi,
        # `flock` tenant à la description ouverte et non au process.
        descripteur = os.open(racine, os.O_RDONLY)
        try:
            while True:
                try:
                    fcntl.flock(descripteur, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    _si_abandon(abandon)
                    time.sleep(_PAS_CRENEAU_S)
                else:
                    break
            yield
        finally:
            os.close(descripteur)  # rend le verrou


def racine_du_creneau(environnement: Mapping[str, str] | None = None) -> Path:
    """La racine dont le créneau des services est le verrou : la racine jetable du poste.

    Relue à chaque prise du créneau : c'est la couture par laquelle la suite de tests
    en prend un à elle (`tests/conftest.py`), sans attendre les runs du poste.
    """
    return racine_jetable(environnement)


def _si_abandon(abandon: threading.Event | None) -> None:
    """Lève `CreneauAbandonne` si l'attente a été abandonnée."""
    if abandon is not None and abandon.is_set():
        raise CreneauAbandonne("la vérification a été annulée pendant qu'elle attendait son tour")


def nom_du_creneau(racine: Path) -> str:
    """Le nom du mutex Windows du créneau de `racine` — une empreinte, sans séparateur."""
    chemin = os.path.normcase(os.path.abspath(racine))
    return PREFIXE_CRENEAU + hashlib.sha256(chemin.encode("utf-8")).hexdigest()[:16]


def port_de_verification(
    *, tirage: random.Random | None = None, essais: int = ESSAIS_PORT
) -> int:
    """Un port libre pour le service d'une vérification, hors de ceux des agents (#1487).

    Tiré au hasard dans `PORTS_DE_VERIFICATION`, et rendu seulement s'il est libre
    sur chacune des adresses où un serveur local écoute (`maestro.sandbox.ports`). À
    prendre **dans le créneau** (`creneau_de_service`) : rien ne le tient entre ce
    constat et le démarrage, sinon le tour. `tirage` est la couture des tests.

    Lève `PortIndisponible` si `essais` tirages n'en ont trouvé aucun — une `OSError`,
    dont l'appelant fait un contrôle « non joué ».
    """
    for port in tirages(PORTS_DE_VERIFICATION, tirage=tirage, essais=essais):
        if port_libre(port):
            return port
    raise PortIndisponible(
        f"aucun port libre pour le service de la vérification parmi {essais} tirés entre "
        f"{PORTS_DE_VERIFICATION.start} et {PORTS_DE_VERIFICATION.stop - 1}"
    )


def _copier(
    source: Path,
    copie: Path,
    exclus: tuple[re.Pattern[str], ...],
    hors: tuple[str, ...],
    fichiers_max: int,
    octets_max: int,
) -> None:
    """Recopie l'arbre, itérativement — bornes comptées **avant** d'écrire chaque fichier."""
    fichiers = 0
    octets = 0
    pile = [""]
    while pile:
        relatif_dossier = pile.pop()
        base = source / relatif_dossier if relatif_dossier else source
        try:
            with os.scandir(base) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:  # dossier devenu illisible : sauté, jamais fatal
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if (
                entree.is_symlink()
                or relatif in hors
                or any(motif.match(relatif) for motif in exclus)
            ):
                continue
            if entree.is_dir():
                (copie / relatif).mkdir(exist_ok=True)
                pile.append(relatif)
                continue
            if not entree.is_file():
                continue
            fichiers += 1
            try:
                octets += entree.stat().st_size
            except OSError:
                continue
            if fichiers > fichiers_max:
                raise CopieImpossible(
                    "trop-de-fichiers",
                    f"le projet compte plus de {fichiers_max} fichiers hors dépendances "
                    "installées : trop pour une copie de vérification.",
                )
            if octets > octets_max:
                raise CopieImpossible(
                    "trop-lourd",
                    f"le projet pèse plus de {octets_max // (1024 * 1024)} Mo hors "
                    "dépendances installées : trop pour une copie de vérification.",
                )
            try:
                shutil.copy2(entree.path, copie / relatif, follow_symlinks=False)
            except OSError:  # fichier verrouillé ou illisible : absent de la copie
                continue


def _sous(chemin: Path, racine: Path) -> bool:
    """`chemin` est-il `racine` ou quelque chose dessous ?"""
    try:
        chemin.relative_to(racine)
    except ValueError:
        return False
    return True


def interprete() -> tuple[str, ...] | None:
    """La commande qui joue un texte shell — **bash**, celui des agents — ou `None`.

    Sous Windows, le `bash.exe` de Git, retrouvé à côté de l'exécutable `git`
    (`<Git>\\cmd\\git.exe` ou `<Git>\\mingw64\\bin\\git.exe` → `<Git>\\bin\\bash.exe`),
    jamais par le `PATH` où `System32\\bash.exe` lancerait WSL. Ailleurs, le `bash`
    du `PATH`, et `sh` à défaut.
    """
    if sys.platform == "win32":
        git = shutil.which("git")
        if git is None:
            return None
        chemin = Path(git).resolve()
        for ancetre in (chemin.parent.parent, chemin.parent.parent.parent):
            bash = ancetre / "bin" / "bash.exe"
            if bash.is_file():
                return (str(bash), "-c")
        return None
    trouve = shutil.which("bash") or shutil.which("sh")
    return (trouve, "-c") if trouve else None


def jouer(
    commande: str,
    cwd: Path,
    *,
    interprete: Sequence[str],
    delai_s: float,
    caracteres_sortie: int = CARACTERES_SORTIE,
    environnement: Mapping[str, str] | None = None,
) -> Execution:
    """Joue `commande` dans `cwd` par `interprete`, et l'arrête au bout de `delai_s`.

    Entrée standard fermée (une commande qui attend une réponse la reçoit vide et
    s'arrête), sorties mêlées, environnement du process augmenté de
    `ENVIRONNEMENT_AJOUTE` puis d'`environnement` (le port d'un service, #1487). Le
    verdict est pris sur **la fin du processus**, pas sur celle de sa sortie ; puis
    tout l'arbre est arrêté — au délai, ou parce que la commande a laissé derrière
    elle un démon qui tient la sortie (voir l'en-tête).
    Lève `OSError` si l'interpréteur ne se lance pas — c'est à l'appelant d'en faire
    un verdict.
    """
    debut = time.monotonic()
    arbre = Arbre.lancer([*interprete, commande], cwd=cwd, env=_environnement(environnement))
    tampon = bytearray()
    assert arbre.process.stdout is not None
    lecteur = threading.Thread(
        target=_lire, args=(arbre.process.stdout, tampon), daemon=True
    )
    lecteur.start()
    try:
        arbre.process.wait(timeout=max(delai_s, 0.0))
        expiree = False
    except subprocess.TimeoutExpired:
        expiree = True
    try:
        _arreter(arbre)
        lecteur.join(timeout=_DELAI_VIDANGE_S)
        if not expiree and arbre.process.returncode is None:  # pragma: no cover - défensif
            arbre.process.wait(timeout=_DELAI_VIDANGE_S)
    finally:
        arbre.fermer()
    duree = time.monotonic() - debut
    return Execution(
        code=None if expiree else arbre.process.returncode,
        sortie=_fin(_texte(tampon), caracteres_sortie),
        duree_s=round(duree, 2),
        expiree=expiree,
    )


def demarrer(
    commande: str,
    cwd: Path,
    *,
    interprete: Sequence[str],
    sonde: str,
    delai_s: float,
    delai_sonde_s: float,
    periode_s: float = PERIODE_SONDE_S,
    caracteres_sortie: int = CARACTERES_SORTIE,
    environnement: Mapping[str, str] | None = None,
) -> Demarrage:
    """Démarre le service `commande`, rejoue `sonde` jusqu'à ce qu'elle tienne, puis l'arrête.

    `environnement` vaut pour le service **et** sa sonde : c'est là qu'ils trouvent
    tous deux le port que Maestro leur réserve (`VARIABLE_PORT`, #1487).

    La sonde est jouée par `jouer`, bornée par `delai_sonde_s`, toutes les
    `periode_s` ; l'ensemble est borné par `delai_s`. S'arrête au premier des trois
    faits : la sonde rend 0 pendant que le service tourne (`repondu`), le service
    rend la main de lui-même (`code`), le délai s'écoule. Le service est ensuite
    arrêté **avec sa descendance**, quoi qu'il arrive (voir l'en-tête).

    Un seul essai, sans rien attendre du poste : c'est à l'appelant de tenir le
    créneau (`creneau_de_service`) autour de l'appel, et de rejouer plus tard un
    démarrage dont la sonde tenait déjà (#1449).

    Lève `OSError` si le service ne se lance pas, `SondeNonLancee` si c'est la
    sonde — à l'appelant d'en faire un verdict.
    """
    debut = time.monotonic()

    def sonder(borne_s: float) -> Execution:
        try:
            return jouer(
                sonde,
                cwd,
                interprete=interprete,
                delai_s=borne_s,
                caracteres_sortie=caracteres_sortie,
                environnement=environnement,
            )
        except OSError as exc:
            raise SondeNonLancee(
                exc.errno, exc.strerror, exc.filename, getattr(exc, "winerror", None)
            ) from exc

    derniere = sonder(delai_sonde_s)
    if derniere.code == 0:
        return Demarrage(
            repondu=False,
            code=None,
            sortie="",
            sonde=derniere,
            duree_s=round(time.monotonic() - debut, 2),
            deja=True,
        )
    arbre = Arbre.lancer([*interprete, commande], cwd=cwd, env=_environnement(environnement))
    tampon = bytearray()
    assert arbre.process.stdout is not None
    lecteur = threading.Thread(
        target=_lire, args=(arbre.process.stdout, tampon), daemon=True
    )
    lecteur.start()
    repondu = False
    code: int | None = None
    try:
        while True:
            reste = delai_s - (time.monotonic() - debut)
            code = arbre.process.poll()
            if code is not None or reste <= 0:
                break
            derniere = sonder(min(delai_sonde_s, reste))
            # Le service doit tourner encore quand la sonde tient : un service mort
            # pendant qu'elle jouait n'est pas celui qui lui a répondu.
            code = arbre.process.poll()
            if derniere.code == 0 and code is None:
                repondu = True
                break
            reste = delai_s - (time.monotonic() - debut)
            if code is not None or reste <= 0:
                break
            try:
                code = arbre.process.wait(timeout=min(periode_s, reste))
            except subprocess.TimeoutExpired:
                code = None
            else:
                break
    finally:
        _arreter(arbre)
        lecteur.join(timeout=_DELAI_VIDANGE_S)
        arbre.fermer()
    return Demarrage(
        repondu=repondu,
        code=code,
        sortie=_fin(_texte(tampon), caracteres_sortie),
        sonde=derniere,
        duree_s=round(time.monotonic() - debut, 2),
    )


def _arreter(arbre: Arbre) -> None:
    """Arrête l'arbre **et attend sa mort** — le tour suivant trouve le port rendu (#1449).

    Un arrêt n'est pas instantané : un service qui meurt tient encore son port le
    temps que le système le lui reprenne, et celui qui démarrerait aussitôt sur le
    même port rendrait la main sur `EADDRINUSE` — un échec que l'agent n'a pas causé.
    Les membres sont relevés **avant** l'arrêt (après, il n'y a plus rien à
    interroger) ; un survivant au-delà du délai n'est attendu de personne.
    """
    membres = arbre.membres()
    arbre.arreter()
    arbre.survivants(membres, delai_s=_DELAI_VIDANGE_S)


def _environnement(ajoute: Mapping[str, str] | None) -> dict[str, str]:
    """L'environnement d'une commande jouée : celui du process, `ENVIRONNEMENT_AJOUTE`, `ajoute`."""
    return {**os.environ, **ENVIRONNEMENT_AJOUTE, **(ajoute or {})}


def _texte(tampon: bytearray) -> str:
    """Ce que le fil de lecture a gardé, en texte — fins de ligne Windows ramenées."""
    return bytes(tampon).decode("utf-8", errors="replace").replace("\r\n", "\n")


def _lire(flux: IO[bytes], tampon: bytearray) -> None:
    """Lit la sortie jusqu'à sa fin, en ne gardant que la queue (`_OCTETS_GARDES`)."""
    descripteur = flux.fileno()
    while True:
        try:
            bloc = os.read(descripteur, 65536)
        except OSError:
            return
        if not bloc:
            return
        tampon.extend(bloc)
        if len(tampon) > 2 * _OCTETS_GARDES:
            del tampon[: len(tampon) - _OCTETS_GARDES]


def _fin(texte: str, caracteres: int) -> str:
    """La fin de `texte`, bornée — `…` en tête quand le début a été coupé."""
    propre = texte.strip()
    if len(propre) <= caracteres:
        return propre
    return "…" + propre[-(caracteres - 1) :]
