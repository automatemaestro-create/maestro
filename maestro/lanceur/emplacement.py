"""Où le produit se trouve, vu du lanceur (#640) : sa racine, son front, son Node.

Le lanceur doit fonctionner dans **deux mondes** sans les distinguer par un drapeau :
le clone de développement (`apps/web/`, `.tools/node/`) et l'installation que #641
posera (`web/`, un runtime embarqué). Chaque résolution est donc une **liste de
candidats dans un ordre annoncé**, dont la première réponse gagne — et dont l'échec
nomme ce qui manque plutôt que de retomber sur une supposition.

Trois variables d'environnement passent devant tout, pour l'installeur comme pour un
poste exotique. Elles ne sont pas un mode caché : ce qu'elles font, le diagnostic le
dit (`python -m maestro.lanceur --diagnostic`).

- `MAESTRO_RACINE_PRODUIT` — la racine du produit (celle qui porte le front et
  `.maestro/`) ;
- `MAESTRO_FRONT` — le dossier du front à servir, quand il n'est pas sous la racine ;
- `MAESTRO_NODE` — l'exécutable Node qui servira ce front.

⚠ **Le front doit être construit.** Ce module ne connaît que deux formes servables :
le **serveur autonome** qu'un `next build` en sortie `standalone` produit (la forme
qu'une installation embarquera, sans `node_modules` à traîner) et un **build
ordinaire** servi par le `next` du dossier. Le serveur de développement n'en est pas
une, et c'est la décision du lot : `next dev` est la chaîne de développement, pas le
produit.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from maestro.espace import racine_de_la_copie

#: Les noms de dossier où un front se trouve, dans l'ordre : l'installation d'abord
#: (c'est elle que le lot prépare), le clone ensuite.
DOSSIERS_FRONT = ("web", "apps/web")

#: Où vont les journaux et l'état du lanceur, sous la racine du produit. `.maestro/`
#: est gitignoré dans un clone, et c'est la règle du dépôt pour ce qu'on invite à lire
#: (docs/10 §8.5) : un chemin **relatif**, lisible par une session autonome.
DOSSIER_LANCEUR = Path(".maestro") / "lanceur"

#: Le runtime Node d'une installation, relativement à la racine du produit.
NODE_EMBARQUE = Path("runtime") / "node"


@dataclass(frozen=True)
class Front:
    """Le front tel qu'on saura le servir — ou le motif pour lequel on ne saura pas."""

    #: Le dossier du front, quand il a été trouvé.
    dossier: Path | None
    #: `standalone` (serveur autonome), `build` (servi par `next start`), `absent`.
    forme: str
    #: Le fichier à jouer avec Node — serveur autonome, ou binaire `next`.
    cible: Path | None
    #: Ce qui manque, dit avec le geste qui le pose. Vide quand la forme est servable.
    manque: str = ""

    @property
    def servable(self) -> bool:
        return self.forme in {"standalone", "build"}


@dataclass(frozen=True)
class Emplacement:
    """Tout ce que le lanceur a besoin de savoir du disque, résolu une fois."""

    racine: Path
    front: Front
    node: str | None
    #: Ce qui manque côté Node, dit avec son geste. Vide quand `node` est résolu.
    manque_node: str = ""

    @property
    def journaux(self) -> Path:
        return self.racine / DOSSIER_LANCEUR

    @property
    def session(self) -> Path:
        return self.journaux / "session.json"

    def journal(self, service: str) -> Path:
        return self.journaux / f"{service}.log"

    def relatif(self, chemin: Path) -> str:
        """Le chemin dit **relativement à la racine** quand il en descend.

        C'est ce qui rend lisible « voir .maestro/lanceur/api.log » dans une session
        autonome, qui n'a personne pour approuver la lecture d'un chemin absolu hors
        de son répertoire de travail (docs/10 §11).
        """
        try:
            return chemin.resolve().relative_to(self.racine.resolve()).as_posix()
        except ValueError:
            return str(chemin)


def racine_produit(environ: dict[str, str], depart: Path | None = None) -> Path:
    """La racine du produit : `MAESTRO_RACINE_PRODUIT`, sinon celle qui porte un front.

    Le point de départ est **la copie dont le code tourne** (`racine_de_la_copie`,
    #1164) — le même repère que les dépôts de `core/`, pour que deux copies de travail
    ne se marchent pas dessus. On remonte de là tant qu'aucun front n'est en vue, ce
    qui couvre l'installation où le paquet Python vit sous un runtime embarqué.
    """
    impose = environ.get("MAESTRO_RACINE_PRODUIT", "").strip()
    if impose:
        return Path(impose).expanduser()
    candidate = (depart or racine_de_la_copie()).resolve()
    for niveau in (candidate, *candidate.parents):
        if _dossier_front(niveau) is not None:
            return niveau
    return candidate


def resoudre_front(racine: Path, environ: dict[str, str]) -> Front:
    """Le front servable sous cette racine, ou le motif de son absence."""
    impose = environ.get("MAESTRO_FRONT", "").strip()
    dossier = Path(impose).expanduser() if impose else _dossier_front(racine)
    if dossier is None or not dossier.is_dir():
        vise = str(dossier) if dossier is not None else " ou ".join(DOSSIERS_FRONT)
        return Front(
            dossier=None,
            forme="absent",
            cible=None,
            manque=(
                f"aucun front sous {racine} ({vise}) — "
                "désigner le sien par MAESTRO_FRONT"
            ),
        )

    autonome = _serveur_autonome(dossier)
    if autonome is not None:
        return Front(dossier=dossier, forme="standalone", cible=autonome)

    binaire = dossier / "node_modules" / "next" / "dist" / "bin" / "next"
    construit = (dossier / ".next" / "BUILD_ID").is_file()
    if construit and binaire.is_file():
        return Front(dossier=dossier, forme="build", cible=binaire)
    if construit:
        return Front(
            dossier=dossier,
            forme="absent",
            cible=None,
            manque=(
                f"front construit sous {dossier} mais sans serveur pour le servir "
                "(ni sortie « standalone », ni node_modules/next)"
            ),
        )
    return Front(
        dossier=dossier,
        forme="absent",
        cible=None,
        manque=(
            f"front non construit sous {dossier} — le construire "
            "(npm run build dans ce dossier), ou en installer un déjà construit"
        ),
    )


def resoudre_node(racine: Path, environ: dict[str, str]) -> tuple[str | None, str]:
    """L'exécutable Node qui servira le front, et le motif quand il n'y en a pas.

    Dans l'ordre : celui qu'on impose, celui qu'une installation embarque, celui que
    le clone provisionne (`.node-version` → `.tools/node/`, la résolution de
    `scripts/controltower/desktop.sh` et de `setup.sh`), celui du `PATH`.
    """
    impose = environ.get("MAESTRO_NODE", "").strip()
    if impose:
        if Path(impose).is_file() or shutil.which(impose):
            return impose, ""
        return None, f"MAESTRO_NODE introuvable : {impose}"

    for candidat in _candidats_node(racine):
        if candidat.is_file():
            return str(candidat), ""

    trouve = shutil.which("node")
    if trouve:
        return trouve, ""
    # ⚠ Ce refus s'adresse à qui **utilise** Maestro, pas à qui le développe : il ne
    # nomme donc aucune commande du dépôt (le constat G6 du retex du 2026-09-11, gardé
    # par `tests/test_registre_de_langue.py`). Celui qui travaille dans un clone a son
    # propre lanceur, et c'est lui qui connaît `setup.sh`.
    return None, (
        "Node introuvable — installer Node, ou désigner son exécutable "
        "par la variable MAESTRO_NODE"
    )


def emplacement(environ: dict[str, str], depart: Path | None = None) -> Emplacement:
    """Les trois résolutions en une, pour l'appelant qui n'en veut qu'une."""
    racine = racine_produit(environ, depart)
    node, manque_node = resoudre_node(racine, environ)
    return Emplacement(
        racine=racine,
        front=resoudre_front(racine, environ),
        node=node,
        manque_node=manque_node,
    )


def _dossier_front(racine: Path) -> Path | None:
    for nom in DOSSIERS_FRONT:
        candidat = racine.joinpath(*nom.split("/"))
        if candidat.is_dir():
            return candidat
    return None


def _serveur_autonome(dossier: Path) -> Path | None:
    """Le `server.js` d'une sortie « standalone », où que Next l'ait posé.

    Trois emplacements, parce que Next reproduit l'arborescence du dépôt sous
    `.next/standalone/` : à la racine du front pour une installation qui n'a gardé
    que lui, à la racine de la sortie pour un projet seul, et sous son chemin
    d'origine pour un dépôt à plusieurs paquets (`apps/web/server.js`).
    """
    direct = dossier / "server.js"
    if direct.is_file():
        return direct
    autonome = dossier / ".next" / "standalone"
    if not autonome.is_dir():
        return None
    racine_sortie = autonome / "server.js"
    if racine_sortie.is_file():
        return racine_sortie
    for trouve in sorted(autonome.glob("*/*/server.js")) + sorted(autonome.glob("*/server.js")):
        if trouve.is_file():
            return trouve
    return None


def _candidats_node(racine: Path) -> tuple[Path, ...]:
    suffixe = ".exe" if sys.platform == "win32" else ""
    candidats = [racine / NODE_EMBARQUE.with_name(f"node{suffixe}")]
    epingle = _version_node(racine)
    if epingle:
        outils = racine / ".tools" / "node" / f"v{epingle}"
        candidats.append(outils / f"node{suffixe}" if suffixe else outils / "bin" / "node")
    return tuple(candidats)


def _version_node(racine: Path) -> str:
    try:
        brut = (racine / ".node-version").read_text(encoding="utf-8")
    except OSError:
        return ""
    return brut.strip().lstrip("v")
