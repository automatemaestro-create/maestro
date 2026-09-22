"""Les **données d'une stack** : l'espace Redis et les dépôts de fichiers qu'elle voit (#1164).

Une stack réelle — l'API, et les hôtes détachés qu'elle lance — lit et écrit deux
sortes de données :

- **Redis**, rangé dans l'espace de la stack (`maestro.espace`) : le journal
  qu'elle rejoue au démarrage, les battements, la file, les canaux — ou, en
  **mode local** (`MAESTRO_PERSISTANCE=sqlite`, #639), le fichier SQLite qui
  tient ce journal à lui seul, sans aucun service ;
- **des dépôts de fichiers**, un par sorte de donnée (`DEPOTS`) : les fils de
  conversation, les projets déclarés, les téléversements, et les réglages d'agent
  que chaque projet range sous `_projets/<id>/` (`maestro.agents.rangement`).

Ce module les nomme ensemble, pour deux usages.

**L'annonce du démarrage** (`annonce`) : `scripts/controltower/start.sh` la rend
par le préflight `maestro-api --verifier-redis`, avant de toucher à quoi que ce
soit. Elle dit quel espace la stack voit, d'où il vient, et où vivent fils et
projets — relatifs à la copie quand ils y sont, **signalés** quand une variable
les a déplacés ailleurs : deux copies réglées sur le même dossier le partagent,
et une annonce qui le tairait mentirait sur la séparation.

**Le jeu de données du banc** (`donnees_du_banc`) : l'état qu'un passage du banc
des scénarios (#1148) a laissé se rouvre sur une stack réelle **sans toucher aux
données de la copie** — celles d'un worktree en cours de travail, ou celles du
poste dans le clone principal. Il a donc son propre espace (`<espace>.banc`) et
ses propres dépôts, sous `.maestro/banc/` de la copie. Une API démarrée par
`maestro-api --etat-banc` s'y place en posant `environnement()` sur son process —
ses hôtes détachés en héritent, comme de tout le reste de son environnement.
Le reste du geste (sauver, rouvrir, rejouer) est dans `maestro.scenarios.etat`.

⚠ **Les variables ne sont pas recopiées en shell.** `start.sh` ne connaît aucun
nom de dépôt ni de variable : il passe `--etat-banc`, et Python résout. Les noms
de variable du tableau `DEPOTS` sont confrontés aux lecteurs réels par
`tests/test_donnees_stack.py` — poser la variable doit déplacer le dépôt que
l'API ouvre, sans quoi l'entrée désignerait autre chose que ce qu'elle dit.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.config import Settings, load_settings
from maestro.controltower.chat import ChatStore
from maestro.controltower.persistence import (
    SUPPORT_SQLITE,
    chemin_sqlite,
    support_persistance,
)
from maestro.espace import VARIABLE_ESPACE, Espace, espace_courant, racine_de_la_copie
from maestro.projets.store import ProjetStore
from maestro.sources.resolution import racine_ingestion

#: Le suffixe de l'espace du banc — celui d'une copie, suivi de `.banc`.
SUFFIXE_BANC = ".banc"

#: Où vivent les dépôts du banc, relativement à la copie : sous `.maestro/`, que le
#: dépôt ignore et où va ce qu'on invite à relire (docs/10 §8.5).
DOSSIER_BANC = Path(".maestro") / "banc"

#: L'origine annoncée d'un espace de banc.
ORIGINE_BANC = "état du banc de cette copie"


@dataclass(frozen=True)
class Depot:
    """Un dépôt de fichiers d'une stack : son nom, sa variable, sa racine résolue.

    `etat` distingue l'**état d'exécution** — ce qu'une stack neuve n'a pas encore
    (fils, projets, téléversements) — de la **configuration**, dont seuls les
    rangements `_projets/<id>/` appartiennent à des projets : un banc qui repart à
    vide garde la configuration de sa copie et n'en retire que ces rangements.
    """

    nom: str
    libelle: str
    variable: str
    etat: bool
    _racine: Callable[[Settings], Path]

    def racine(self, settings: Settings) -> Path:
        """La racine que l'API ouvrira pour ce dépôt, avec ces réglages."""
        return self._racine(settings)


#: Les dépôts de fichiers d'une stack, résolus par les **mêmes** `default()` que
#: l'API. Restent hors du tableau, parce qu'ils sont au poste et non à une stack :
#: le coffre des secrets, le miroir du registre MCP et les réglages du poste.
DEPOTS: tuple[Depot, ...] = (
    Depot("chat", "fils", "MAESTRO_CHAT_DIR", True, lambda s: ChatStore.default(s).racine),
    Depot(
        "projets", "projets", "MAESTRO_PROJETS_DIR", True, lambda s: ProjetStore.default(s).racine
    ),
    Depot("ingestion", "téléversements", "MAESTRO_INGESTION_DIR", True, racine_ingestion),
    Depot("agents", "agents", "MAESTRO_AGENTS_DIR", False, lambda s: AgentStore.default(s).racine),
    Depot(
        "surcharges",
        "surcharges",
        "MAESTRO_SURCHARGES_DIR",
        False,
        lambda s: SurchargeStore.default(s).racine,
    ),
    Depot(
        "playbooks",
        "playbooks",
        "MAESTRO_PLAYBOOKS_DIR",
        False,
        lambda s: PlaybookStore.default(s).racine,
    ),
    Depot(
        "permissions",
        "autorisations",
        "MAESTRO_PERMISSIONS_DIR",
        False,
        lambda s: PermissionStore.default(s).racine,
    ),
    Depot("mcp", "serveurs MCP", "MAESTRO_MCP_DIR", False, lambda s: McpStore.default(s).racine),
    Depot(
        "capacite",
        "capacités",
        "MAESTRO_CAPACITE_DIR",
        False,
        lambda s: CapacityStore.default(s).racine,
    ),
)

#: Ce que l'annonce montre : les données que le critère de #1164 nomme — les runs
#: (Redis), les fils et les projets. Le reste suit la même règle sans l'encombrer.
DEPOTS_ANNONCES = ("chat", "projets")


@dataclass(frozen=True)
class Donnees:
    """Ce qu'une stack voit : son espace Redis et la racine de chacun de ses dépôts."""

    espace: Espace
    racines: Mapping[str, Path]
    #: Les dépôts qu'une variable a déplacés — ceux qu'une autre copie peut partager.
    regles: frozenset[str] = frozenset()

    @property
    def banc(self) -> bool:
        """Vrai pour le jeu de données du banc."""
        return self.espace.nom.endswith(SUFFIXE_BANC)

    def environnement(self) -> dict[str, str]:
        """Ce qu'on pose sur un process pour qu'il voie **ces** données-là."""
        env = {VARIABLE_ESPACE: self.espace.nom}
        env.update({d.variable: str(self.racines[d.nom]) for d in DEPOTS})
        return env


def donnees_de_la_stack(
    settings: Settings | None = None, environnement: Mapping[str, str] | None = None
) -> Donnees:
    """Les données que verrait une API démarrée ici, avec cet environnement.

    Sur le banc (`--etat-banc`), les variables posées sont celles du banc : elles
    ne déplacent rien, et l'espace se présente comme celui du banc de la copie.
    """
    env = os.environ if environnement is None else environnement
    settings = settings or load_settings()
    espace = espace_courant(env)
    racines = {d.nom: d.racine(settings) for d in DEPOTS}
    attendues: Mapping[str, Path] = {}
    if espace.nom.endswith(SUFFIXE_BANC):
        banc = donnees_du_banc(environnement=env)
        espace, attendues = banc.espace, banc.racines
    return Donnees(
        espace=espace,
        racines=racines,
        regles=frozenset(
            d.nom
            for d in DEPOTS
            if (env.get(d.variable) or "").strip() and racines[d.nom] != attendues.get(d.nom)
        ),
    )


def donnees_du_banc(
    racine: Path | None = None, environnement: Mapping[str, str] | None = None
) -> Donnees:
    """Le jeu de données du banc de cette copie — un espace et des dépôts à lui.

    Idempotent : dans un process déjà placé sur le banc (une API lancée par
    `--etat-banc`, ou ce qu'elle lance), il rend ce même jeu et pas `<x>.banc.banc`.
    """
    espace = espace_courant(environnement)
    nom = espace.nom if espace.nom.endswith(SUFFIXE_BANC) else f"{espace.nom}{SUFFIXE_BANC}"
    base = (racine or racine_de_la_copie()) / DOSSIER_BANC
    return Donnees(
        espace=Espace(nom, ORIGINE_BANC),
        racines={d.nom: base / d.nom for d in DEPOTS},
    )


def poser_sur_le_process(donnees: Donnees) -> None:
    """Place le process courant (et tout ce qu'il lancera) sur ces données."""
    os.environ.update(donnees.environnement())


def _lisible(chemin: Path, copie: Path) -> str:
    """Un chemin relatif à la copie quand il y est — l'annonce se lit d'un coup d'œil."""
    try:
        return chemin.resolve().relative_to(copie.resolve()).as_posix()
    except ValueError:
        return str(chemin)


def annonce(
    donnees: Donnees, *, copie: Path | None = None, settings: Settings | None = None
) -> list[str]:
    """Les lignes qui disent, au démarrage, ce que cette stack voit — et ce qu'elle ne voit pas.

    La ligne `runs` nomme le **support** du journal durable (#639) : les clés
    Redis de l'espace, ou le fichier local quand `MAESTRO_PERSISTANCE=sqlite`.
    Une annonce qui parlerait de Redis à une stack qui n'en ouvre pas dirait
    faux sur la seule chose qu'elle existe pour dire — où vivent les données.
    """
    copie = copie or racine_de_la_copie()
    settings = settings or load_settings()
    espace = donnees.espace
    if support_persistance(settings) == SUPPORT_SQLITE:
        runs = f"SQLite, fichier « {chemin_sqlite(settings)} » — aucun service à lancer"
    elif espace.commun:
        runs = "Redis, noms sans préfixe (l'historique du poste) — aucun worktree ne les lit"
    else:
        runs = f"Redis, noms « {espace.prefixe}maestro.* » — aucune autre copie ne les lit"
    lignes = [
        f"Données de cette stack — espace « {espace.nom} » ({espace.origine}) :",
        f"  runs     {runs}",
    ]
    for depot in DEPOTS:
        if depot.nom not in DEPOTS_ANNONCES:
            continue
        chemin = _lisible(donnees.racines[depot.nom], copie)
        if depot.nom in donnees.regles:
            chemin += (
                f"  (déplacé par {depot.variable} : partagé avec toute copie réglée sur ce dossier)"
            )
        lignes.append(f"  {depot.libelle.ljust(8)} {chemin}")
    return lignes
