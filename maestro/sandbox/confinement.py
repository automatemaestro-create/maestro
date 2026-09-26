"""Ce qu'un agent lance pendant sa tâche ne survit pas à sa tâche (#1279).

## L'incident

Run `3fe501fc0878`, 2026-09-24. Pour vérifier sa maquette, l'agent `interface` lance
depuis son `Bash`, en arrière-plan, un Edge headless qui expose le protocole de
débogage (`--remote-debugging-port=9333`). La tâche échoue, le run se termine — et
le navigateur tourne toujours, rattaché à un `bash.exe` déjà mort : son port reste
ouvert à tout processus du poste, son profil vit dans `%TEMP%`. Rien, dans la
chaîne, ne rangeait derrière l'agent : le SDK ferme **son** sous-processus (le
CLI), et ce que le CLI a lancé en arrière-plan n'est l'enfant de personne.

## La règle

La **session** d'un agent — le CLI et tout ce qu'il lance, à tout degré, quel que
soit l'intermédiaire — vit dans un seul arbre (`maestro.sandbox.arbre` : un Job
Object sous Windows, le groupe et les descendants sous POSIX), et cet arbre est
arrêté à la **clôture** de la session : succès, échec, relance (chaque tentative est
une session) ou annulation. Ce qui ne peut pas être arrêté est **nommé**, avec son
pid, au journal du run.

Ce n'est pas une bride (docs/41) : pendant sa tâche, l'agent lance ce qu'il veut —
un serveur, un navigateur, une surveillance de fichiers — sous la politique qui est
la sienne. Ce qui change est seulement qu'un processus ne survit plus à la tâche qui
l'a fait naître : c'est un garde-fou de sûreté, au même titre que la frontière
d'écriture.

## La couture : un lanceur en `cli_path`

Le SDK lance le CLI lui-même et n'expose ni son processus ni son pid ; lire ses
internes pour les retrouver serait dépendre du comportement d'un outil tiers
(docs/44). La seule couture publique est `cli_path` — celle du mode isolé (docs/17
§2). Le fournisseur y pointe le **lanceur** `maestro-confinement` (`main`), qui :

1. lance la commande nommée par `ENV_COMMANDE` — le vrai CLI — avec les arguments
   que le SDK lui destinait, **ses flux hérités tels quels** (le protocole SDK ↔ CLI
   ne traverse aucune copie), dans un arbre créé pour elle ;
2. attend qu'elle rende la main, et relaie son code de sortie ;
3. arrête l'arbre, relève ce qui y vivait encore et ce qui a résisté, et l'écrit
   dans `ENV_RELEVE` ; le fournisseur le lit une fois la session fermée
   (`SessionConfinee.solder`).

Vu du SDK, rien n'a changé : mêmes flux, même code de sortie. Les deux variables
sont retirées de l'environnement du CLI : l'agent n'a pas à les voir.

Trois façons pour une session de finir, et aucune ne laisse l'arbre derrière elle :

- **le CLI rend la main** — fin d'entrée après son résultat, échec, annulation qui
  lui ferme l'entrée : le lanceur arrête l'arbre et écrit son relevé ;
- **le SDK termine le lanceur**, faute que le CLI sorte dans son délai de grâce :
  sous POSIX le signal est intercepté et mène au même arrêt ; sous Windows il n'y a
  pas de signal (`TerminateProcess`), mais le job est « tué à la fermeture » — le
  système ferme la poignée du lanceur et le job emporte tout l'arbre. Rien n'est
  écrit alors, et le fournisseur ne dit rien qu'il ne sait pas ;
- **l'hôte de Maestro meurt** : l'entrée du CLI se brise, il rend la main — premier cas.

## Ce qui n'est pas confiné, et le dit

- **Le mode isolé** (docs/17) n'en a pas besoin : le conteneur jetable est déjà la
  frontière, et tout ce que le CLI y lance meurt avec lui.
- **Un lanceur absent** — le paquet n'a pas été réinstallé depuis ce ticket — ou un
  **CLI introuvable** : la session tourne comme avant, et le journal du run dit
  qu'elle n'était pas confinée (`ReleveConfinement.non_confinee`), plutôt que de
  laisser croire que rien n'a survécu.

Le chemin texte (`generate`) n'expose aucun outil : il ne lance rien, il n'a rien à
confiner.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

from maestro.fichiers import retirer_arbre
from maestro.sandbox.arbre import Arbre, ProcessusNomme
from maestro.sandbox.ramassage import marquer, racine_des_espaces

#: La commande que le lanceur confine — le vrai CLI —, en **liste JSON** : un chemin
#: d'installation porte volontiers des espaces, et une liste ne se redécoupe pas.
ENV_COMMANDE = "MAESTRO_CONFINEMENT_COMMANDE"

#: Où le lanceur écrit son relevé. Absente : il confine quand même, sans rendre compte.
ENV_RELEVE = "MAESTRO_CONFINEMENT_RELEVE"

#: Le nom du lanceur (point d'entrée console déclaré dans `pyproject.toml`).
NOM_LANCEUR = "maestro-confinement"

#: Le préfixe du dossier où naît un relevé — sous le préfixe commun des espaces,
#: marqué du pid de l'hôte : le ramassage (#992) l'emporte si l'hôte meurt avant.
PREFIXE_RELEVE = "maestro-confinement-"

#: Le délai laissé à l'arbre pour mourir une fois l'arrêt donné. Court : le lanceur
#: travaille dans le délai de grâce que le SDK accorde à la fermeture (5 s), et un
#: arrêt forcé est immédiat — ce qui résiste au-delà ne mourra pas en attendant.
DELAI_ARRET_S = 2.0

#: Combien de processus arrêtés une phrase nomme avant de résumer le reste. Ceux
#: qui ont **résisté** sont toujours tous nommés : c'est le critère du ticket.
NOMMES_MAX = 10

#: `PR_SET_CHILD_SUBREAPER` (Linux) : les orphelins de l'arbre reviennent au lanceur.
_PR_SET_CHILD_SUBREAPER = 36

#: La tranche d'attente de la session (voir `_attendre`).
_TRANCHE_ATTENTE_S = 0.5

#: Les ordres d'arrêt que le lanceur intercepte — ceux que la plateforme connaît :
#: le `terminate()` du SDK sous POSIX, un Ctrl-C ou un Ctrl-Break d'une console.
_ORDRES_D_ARRET = ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK")


@dataclass(frozen=True)
class ReleveConfinement:
    """Ce qu'une session a laissé derrière elle à sa clôture, et ce qu'il en est advenu.

    `arretes` : les processus qui vivaient encore quand la session s'est fermée, et
    que le confinement a arrêtés. `survivants` : ceux qui ont **résisté** à l'arrêt
    — nommés avec leur pid, c'est ce que le journal doit dire (#1279). La session
    elle-même (le CLI) n'est jamais comptée parmi les premiers : ce n'est pas un
    reste, c'est elle.

    `non_confinee` : pourquoi la session a tourné **sans** confinement — vide quand
    elle l'était. Ce n'est pas un échec de la tâche ; c'est ce qui empêche de lire
    un relevé vide comme « rien n'a survécu ».
    """

    arretes: tuple[ProcessusNomme, ...] = ()
    survivants: tuple[ProcessusNomme, ...] = ()
    non_confinee: str = ""

    @property
    def vide(self) -> bool:
        """Rien à dire : la session était confinée et n'a rien laissé."""
        return not (self.arretes or self.survivants or self.non_confinee)

    def phrase(self) -> str:
        """Ce que le journal du run en dit — une phrase entière, vide s'il n'y a rien à dire."""
        if self.non_confinee:
            return (
                f"Session de l'agent non confinée : {self.non_confinee}. Ce qu'elle a "
                "lancé en arrière-plan peut survivre à la tâche."
            )
        morceaux: list[str] = []
        if self.survivants:
            n = len(self.survivants)
            morceaux.append(
                f"{n} {_accord(n, 'processus lancé', 'processus lancés')} pendant la tâche "
                f"{_accord(n, 'n’a', 'n’ont')} pas pu être "
                f"{_accord(n, 'arrêté', 'arrêtés')} à sa clôture : "
                f"{_liste(self.survivants, len(self.survivants))}."
            )
            if self.arretes:
                m = len(self.arretes)
                morceaux.append(
                    f"{_accord(m, 'Un autre l’a été', f'{m} autres l’ont été')} : "
                    f"{_liste(self.arretes, NOMMES_MAX)}."
                )
        elif self.arretes:
            m = len(self.arretes)
            morceaux.append(
                f"{m} {_accord(m, 'processus lancé', 'processus lancés')} pendant la tâche "
                f"{_accord(m, 'lui survivait', 'lui survivaient')} — "
                f"{_accord(m, 'arrêté', 'arrêtés')} à sa clôture : "
                f"{_liste(self.arretes, NOMMES_MAX)}."
            )
        return " ".join(morceaux)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arretes": [p.to_dict() for p in self.arretes],
            "survivants": [p.to_dict() for p in self.survivants],
            "non_confinee": self.non_confinee,
        }

    @classmethod
    def from_dict(cls, brut: Mapping[str, Any]) -> ReleveConfinement:
        def processus(cle: str) -> tuple[ProcessusNomme, ...]:
            valeurs = brut.get(cle) or []
            return tuple(ProcessusNomme.from_dict(v) for v in valeurs if isinstance(v, Mapping))

        return cls(
            arretes=processus("arretes"),
            survivants=processus("survivants"),
            non_confinee=str(brut.get("non_confinee") or ""),
        )


def _accord(n: int, singulier: str, pluriel: str) -> str:
    return singulier if n == 1 else pluriel


def _liste(processus: Sequence[ProcessusNomme], borne: int) -> str:
    nommes = ", ".join(str(p) for p in processus[:borne])
    reste = len(processus) - borne
    return nommes if reste <= 0 else f"{nommes} et {reste} {_accord(reste, 'autre', 'autres')}"


# ------------------------------------------------------------ côté fournisseur


@dataclass
class SessionConfinee:
    """Ce qu'il faut au fournisseur pour lancer une session confinée — puis son relevé.

    `lanceur` est la valeur de `cli_path` (`None` : la session n'est pas confinée,
    le SDK lance son CLI comme avant) ; `env` les variables à poser sur le
    sous-processus. `solder()`, appelé une fois la session **fermée**, rend le
    relevé et retire le dossier où il est né.
    """

    lanceur: Path | None
    env: dict[str, str] = field(default_factory=dict)
    non_confinee: str = ""
    _dossier: Path | None = None

    @classmethod
    def preparer(cls, commande: Sequence[str] | None) -> SessionConfinee:
        """Prépare la session de `commande` (le CLI), ou dit pourquoi elle ne sera pas confinée."""
        if not commande:
            return cls(None, non_confinee="le CLI de la session est introuvable")
        lanceur = chemin_lanceur()
        if lanceur is None:
            return cls(
                None,
                non_confinee=(
                    f"le lanceur {NOM_LANCEUR!r} est introuvable — l'installation de "
                    "Maestro est incomplète et doit être refaite"
                ),
            )
        env = {ENV_COMMANDE: json.dumps(list(commande))}
        dossier: Path | None
        try:
            dossier = Path(
                tempfile.mkdtemp(prefix=marquer(PREFIXE_RELEVE), dir=racine_des_espaces())
            )
        except OSError:
            # Sans dossier, le lanceur confine quand même — il ne rend simplement
            # pas compte : l'arrêt n'a jamais dépendu du relevé.
            dossier = None
        else:
            env[ENV_RELEVE] = str(dossier / "releve.json")
        return cls(lanceur, env, _dossier=dossier)

    def solder(self) -> ReleveConfinement:
        """Le relevé de la session fermée — vide si le lanceur n'a rien eu à dire ou rien écrit."""
        if self.non_confinee:
            return ReleveConfinement(non_confinee=self.non_confinee)
        if self._dossier is None:
            return ReleveConfinement()
        try:
            brut = json.loads((self._dossier / "releve.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            brut = {}
        finally:
            retirer_arbre(self._dossier)
            self._dossier = None
        if not isinstance(brut, Mapping):
            return ReleveConfinement()
        return ReleveConfinement.from_dict(brut)


def chemin_lanceur() -> Path | None:
    """L'exécutable du lanceur, installé à côté de l'interpréteur — ou sur le PATH.

    `None` quand il n'est nulle part : un clone qui n'a pas réinstallé le paquet
    depuis #1279. La session tourne alors sans confinement, et le dit.
    """
    nom = NOM_LANCEUR + (".exe" if sys.platform == "win32" else "")
    candidat = Path(sys.executable).with_name(nom)
    if candidat.is_file():
        return candidat
    trouve = shutil.which(NOM_LANCEUR)
    return Path(trouve) if trouve else None


# --------------------------------------------------------------- le lanceur


class _Interruption(Exception):
    """Le lanceur a reçu l'ordre de s'arrêter pendant que la session tournait."""

    def __init__(self, signal_recu: int) -> None:
        super().__init__(signal_recu)
        self.signal_recu = signal_recu


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de `maestro-confinement` : lance le CLI confiné, rend son code.

    `argv` (défaut : `sys.argv[1:]`) porte les arguments que le SDK destinait au
    CLI. Sans `ENV_COMMANDE` lisible, rien n'est lancé et le code est 2, avec
    l'explication sur stderr — le SDK la relaie dans son diagnostic d'échec. C'est
    aussi ce que reçoit sa vérification de version (`<cli_path> -v`), qu'il joue
    sans l'environnement de la session et dont il ignore l'échec.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Ce que le lanceur écrit part dans le tube de stderr que le SDK décode en
    # UTF-8 : sous Windows, l'encodage du poste en ferait du mojibake (#141).
    reconfigurer = getattr(sys.stderr, "reconfigure", None)
    if reconfigurer is not None:
        try:
            reconfigurer(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    environ = dict(os.environ)
    brut = environ.pop(ENV_COMMANDE, "")
    chemin_releve = environ.pop(ENV_RELEVE, "")
    try:
        commande = _commande_depuis(brut)
    except ValueError as exc:
        print(f"{NOM_LANCEUR} : {exc}", file=sys.stderr)
        return 2
    _recueillir_les_orphelins()
    try:
        arbre = Arbre.lancer(
            [*commande, *arguments],
            env=environ,
            stdin=_flux(0),
            stdout=_flux(1),
            stderr=_flux(2),
            console_propre=False,
            racine=os.getpid(),
            veiller=True,
        )
    except OSError as exc:
        print(f"{NOM_LANCEUR} : la session n'a pas pu être lancée : {exc}", file=sys.stderr)
        return 127
    code = _attendre(arbre)
    # Un second ordre d'arrêt ne doit pas interrompre l'arrêt lui-même : c'est lui
    # qu'il demandait.
    _poser_les_gestionnaires(signal.SIG_IGN)
    releve = _arreter(arbre)
    _recueillir_les_morts()
    if chemin_releve:
        _ecrire(releve, Path(chemin_releve))
    return code


def _commande_depuis(brut: str) -> list[str]:
    if not brut.strip():
        raise ValueError(
            f"{ENV_COMMANDE} absente — le lanceur est invoqué par le fournisseur, "
            "qui lui nomme la commande à confiner, pas directement."
        )
    try:
        commande = json.loads(brut)
    except ValueError as exc:
        raise ValueError(f"{ENV_COMMANDE} illisible (liste JSON attendue) : {exc}") from exc
    if not isinstance(commande, list) or not commande or not all(
        isinstance(morceau, str) and morceau for morceau in commande
    ):
        raise ValueError(f"{ENV_COMMANDE} doit être une liste JSON de chaînes non vide.")
    return commande


def _flux(descripteur: int) -> int:
    """Le flux hérité s'il est ouvert, sinon le néant — jamais un descripteur mort."""
    try:
        os.fstat(descripteur)
    except OSError:
        return subprocess.DEVNULL
    return descripteur


def _recueillir_les_orphelins() -> None:
    """Linux : le lanceur devient *subreaper* — un orphelin de l'arbre lui revient.

    Sans lui, un processus qui se détache (`setsid`, double fork) part chez `init`
    et quitte la descendance du lanceur. Best-effort : ailleurs, ou refusé, le
    groupe de processus reste l'arrêt nominal.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (OSError, AttributeError):
        return


def _attendre(arbre: Arbre) -> int:
    """Attend que la session rende la main — ou qu'on ordonne au lanceur de s'arrêter.

    Un ordre d'arrêt (le `terminate()` du SDK sous POSIX, un Ctrl-C) interrompt
    l'attente : l'arbre est alors arrêté session comprise, et le code est celui
    d'un processus tué par ce signal.
    """

    def interrompre(signal_recu: int, _frame: FrameType | None) -> None:
        raise _Interruption(signal_recu)

    _poser_les_gestionnaires(interrompre)
    try:
        # Par tranches : sous Windows, une attente sans échéance ne laisse jamais
        # la main aux gestionnaires de signaux. La sortie de la session, elle, est
        # vue aussitôt — l'échéance ne borne que l'attente d'un signal.
        while True:
            try:
                return arbre.process.wait(timeout=_TRANCHE_ATTENTE_S)
            except subprocess.TimeoutExpired:
                continue
    except _Interruption as interruption:
        return 128 + interruption.signal_recu


def _poser_les_gestionnaires(gestionnaire: Any) -> None:
    """Pose `gestionnaire` sur chaque ordre d'arrêt que la plateforme connaît."""
    for nom in _ORDRES_D_ARRET:
        numero = getattr(signal, nom, None)
        if numero is None:
            continue
        try:
            signal.signal(numero, gestionnaire)
        except (OSError, ValueError):
            continue


def _arreter(arbre: Arbre) -> ReleveConfinement:
    """Arrête tout l'arbre, relève ce qui y vivait et ce qui a résisté.

    Les membres sont relevés **avant** l'arrêt — après, il n'y a plus rien à
    interroger —, et la session elle-même (le CLI) n'est comptée parmi les
    arrêtés que si elle a résisté : ce n'est pas un reste, c'est elle.
    """
    # Lu avant `fermer()`, qui rend le job : c'est l'arbre tel qu'il a tourné.
    confine = arbre.confine
    membres = arbre.membres()
    arbre.arreter()
    survivants = arbre.survivants(membres, delai_s=DELAI_ARRET_S)
    try:
        arbre.process.wait(timeout=DELAI_ARRET_S)
    except subprocess.TimeoutExpired:
        pass
    arbre.fermer()
    session = arbre.process.pid
    resistants = {p.pid for p in survivants}
    return ReleveConfinement(
        arretes=tuple(p for p in membres if p.pid not in resistants and p.pid != session),
        survivants=survivants,
        non_confinee=(
            ""
            if confine
            else "le système a refusé le conteneur de processus (Job Object) ; l'arrêt est "
            "passé par l'arbre des parents, que les processus détachés quittent"
        ),
    )


def _recueillir_les_morts() -> None:
    """POSIX : recueille les enfants morts — le lanceur a adopté les orphelins de l'arbre."""
    if sys.platform == "win32":
        return
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _ecrire(releve: ReleveConfinement, chemin: Path) -> None:
    """Écrit le relevé d'un seul geste — un lecteur ne voit jamais un fichier à moitié écrit."""
    provisoire = chemin.with_name(chemin.name + ".partiel")
    try:
        provisoire.write_text(json.dumps(releve.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(provisoire, chemin)
    except OSError as exc:
        print(f"{NOM_LANCEUR} : relevé non écrit ({exc})", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
