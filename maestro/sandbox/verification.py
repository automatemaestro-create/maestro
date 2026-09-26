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
sous `racine_des_espaces()` avec le pid de ce process dans son nom — le ramassage
(`maestro.sandbox.ramassage`) l'emporte donc si le process meurt avant de la
retirer — et elle est retirée en sortie, quoi qu'il arrive.

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
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from maestro.fichiers import retirer_arbre
from maestro.sandbox.arbre import Arbre
from maestro.sandbox.ramassage import marquer, racine_des_espaces

#: Le préfixe d'une copie de vérification — sous le préfixe commun des espaces de
#: Maestro, donc candidate au ramassage si son process meurt avant de la retirer.
PREFIXE_COPIE = "maestro-verif-"

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
        parent = Path(tempfile.mkdtemp(prefix=marquer(PREFIXE_COPIE), dir=racine_des_espaces()))
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
) -> Execution:
    """Joue `commande` dans `cwd` par `interprete`, et l'arrête au bout de `delai_s`.

    Entrée standard fermée (une commande qui attend une réponse la reçoit vide et
    s'arrête), sorties mêlées, environnement du process augmenté de
    `ENVIRONNEMENT_AJOUTE`. Le verdict est pris sur **la fin du processus**, pas
    sur celle de sa sortie ; puis tout l'arbre est arrêté — au délai, ou parce que
    la commande a laissé derrière elle un démon qui tient la sortie (voir l'en-tête).
    Lève `OSError` si l'interpréteur ne se lance pas — c'est à l'appelant d'en faire
    un verdict.
    """
    debut = time.monotonic()
    arbre = Arbre.lancer(
        [*interprete, commande], cwd=cwd, env={**os.environ, **ENVIRONNEMENT_AJOUTE}
    )
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
        arbre.arreter()
        lecteur.join(timeout=_DELAI_VIDANGE_S)
        if not expiree and arbre.process.returncode is None:  # pragma: no cover - défensif
            arbre.process.wait(timeout=_DELAI_VIDANGE_S)
    finally:
        arbre.fermer()
    duree = time.monotonic() - debut
    texte = bytes(tampon).decode("utf-8", errors="replace").replace("\r\n", "\n")
    return Execution(
        code=None if expiree else arbre.process.returncode,
        sortie=_fin(texte, caracteres_sortie),
        duree_s=round(duree, 2),
        expiree=expiree,
    )


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
