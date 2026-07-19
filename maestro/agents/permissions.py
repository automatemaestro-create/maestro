"""Politique de permissions par agent et par outil (ticket #110, parent #102).

Chaque agent peut déclarer une politique **allow/deny par outil** — outils
intégrés du runtime (`Read`, `Bash`…) comme outils MCP (`mcp__<serveur>` pour
un serveur entier, `mcp__<serveur>__<outil>` pour un outil précis) — appliquée
à **l'exécution** :

- les outils intégrés refusés sont **retirés de la session** avant son
  ouverture (`PolitiqueOutils.filtre_outils` — l'agent ne les voit jamais) ;
- un serveur MCP entièrement refusé n'est **jamais monté** (ses secrets ne
  sont même pas résolus — `serveur_autorise`) ;
- tout appel qui passerait quand même (outil MCP refusé individuellement,
  outil résiduel de la session) est **refusé au vol** par le fournisseur
  (hook PreToolUse de la couche SDK) : l'agent reçoit un refus propre motivé
  et **poursuit sa tâche** — la violation est tracée (journal + fil temps
  réel Control Tower), jamais fatale au run.

Sémantique : `deny` l'emporte toujours ; `allow` vide = tout ce que le profil
expose est permis (comportement historique) ; `allow` non vide = liste
fermée — tout le reste est refusé. Une entrée vaut pour l'outil exact ou, aux
frontières `__`, pour tout ce qu'elle préfixe (`mcp__slack` couvre
`mcp__slack__send_message`).

Le dépôt suit le pattern des voisins (`maestro.agents.mcp`, `capacity`) : un
fichier JSON par agent (`core/permissions/<agent>.json`, versionné avec le
dépôt Git), **validé à la lecture** (une politique douteuse est refusée avec
sa cause, jamais appliquée à moitié) et **relu à chaud** à chaque tâche par
l'exécuteur — corriger une politique vaut pour la tâche suivante, sans
redémarrage. Pas de fichier = pas de politique = tout permis. En V1 le dépôt
passera en base sans changer ce contrat.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings

#: Nom d'agent admissible comme fichier de stockage — même verrou que les
#: dépôts voisins (`maestro.agents.mcp`, `store`) : slug sûr, jamais un chemin.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Une entrée de politique admissible : un nom d'outil intégré (`Read`, `Bash`)
#: ou un nom préfixé MCP (`mcp__slack`, `mcp__slack__send_message`) — segments
#: alphanumériques joints par `__`, jamais d'espace ni de vide.
_ENTREE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Préfixe des outils MCP dans les sessions SDK : `mcp__<serveur>__<outil>`.
_PREFIXE_MCP = "mcp__"


def _correspond(entree: str, outil: str) -> bool:
    """`entree` couvre-t-elle `outil` ? Nom exact, ou préfixe à une frontière `__`.

    `mcp__slack` couvre `mcp__slack__send_message` mais pas `mcp__slackbot__x` :
    le préfixe ne vaut qu'aux frontières de segments — jamais en plein mot.
    """
    return outil == entree or outil.startswith(entree + "__")


@dataclass(frozen=True)
class PolitiqueOutils:
    """La politique allow/deny d'un agent — la partie versionnable du contrat.

    `deny` l'emporte toujours sur `allow`. `allow` vide laisse tout passer
    (hors `deny`) ; non vide, c'est une liste fermée. Les entrées désignent un
    outil intégré (`Bash`), un serveur MCP entier (`mcp__slack`) ou un outil
    MCP précis (`mcp__slack__send_message`).
    """

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def autorise(self, outil: str) -> bool:
        """L'appel de `outil` est-il permis par cette politique ?"""
        if any(_correspond(entree, outil) for entree in self.deny):
            return False
        if not self.allow:
            return True
        return any(_correspond(entree, outil) for entree in self.allow)

    def raison_refus(self, outil: str) -> str:
        """Le motif du refus de `outil` — le message servi à l'agent et tracé."""
        if any(_correspond(entree, outil) for entree in self.deny):
            return (
                f"outil {outil!r} interdit par la politique de permissions de "
                "l'agent (liste deny). Poursuis la tâche sans cet outil."
            )
        return (
            f"outil {outil!r} hors de la politique de permissions de l'agent "
            "(liste allow fermée). Poursuis la tâche sans cet outil."
        )

    def filtre_outils(self, outils: Sequence[str]) -> tuple[str, ...]:
        """Les outils intégrés de `outils` que la politique permet de monter."""
        return tuple(outil for outil in outils if self.autorise(outil))

    def serveur_autorise(self, nom: str) -> bool:
        """Le serveur MCP `nom` mérite-t-il d'être monté sur la session ?

        Faux si le serveur est refusé **en entier** (`deny` porte
        `mcp__<nom>`), ou si une liste `allow` fermée n'en cite aucun outil —
        dans les deux cas il n'est pas monté et ses secrets ne sont jamais
        résolus. Un refus **individuel** (`deny` sur un seul de ses outils)
        laisse le serveur monté : c'est le refus au vol qui l'applique.
        """
        prefixe = f"{_PREFIXE_MCP}{nom}"
        if any(_correspond(entree, prefixe) for entree in self.deny):
            return False
        if not self.allow:
            return True
        return any(
            entree == prefixe or entree.startswith(prefixe + "__")
            for entree in self.allow
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet la politique en dict JSON-sérialisable (la forme stockée/publique)."""
        return {"allow": list(self.allow), "deny": list(self.deny)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolitiqueOutils:
        """Reconstruit une politique depuis sa forme stockée (sans la valider)."""
        return cls(
            allow=tuple(str(entree) for entree in data.get("allow", ())),
            deny=tuple(str(entree) for entree in data.get("deny", ())),
        )


class PermissionStore:
    """Dépôt des politiques de permissions par agent (`<racine>/<agent>.json`).

    Un fichier par agent : `{"allow": [...], "deny": [...]}` — le nom d'agent
    fait foi côté fichier, comme pour les autres dépôts. Relu à chaque tâche
    (application à chaud, comme les playbooks #78) : une politique corrigée
    vaut pour la tâche suivante, sans redémarrage. `lire` valide le fichier et
    lève `ValueError` avec sa cause exacte s'il est invalide — on n'applique
    jamais une politique douteuse (ni ne l'ignore en silence).
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> PermissionStore:
        """Le dépôt configuré : `MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/`."""
        settings = settings or load_settings()
        if settings.permissions_dir:
            return cls(Path(settings.permissions_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "permissions")

    def lire(self, agent: str) -> PolitiqueOutils | None:
        """La politique de `agent`, validée — None s'il n'en déclare pas (tout permis).

        Lève `ValueError` (cause exacte, agent nommé) si le fichier est
        illisible ou la politique invalide : l'appelant (exécuteur, API) la
        mue en échec propre plutôt que d'exécuter sous une politique douteuse.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return None
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"politique de permissions illisible pour l'agent {agent!r} "
                f"({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"politique de permissions invalide pour l'agent {agent!r} "
                f'({chemin.name}) : objet {{"allow": [...], "deny": [...]}} attendu.'
            )
        return PolitiqueOutils(
            allow=_liste_validee(data, "allow", agent=agent),
            deny=_liste_validee(data, "deny", agent=agent),
        )

    def agents(self) -> tuple[str, ...]:
        """Les noms des agents ayant une politique stockée, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def _chemin(self, agent: str) -> Path:
        """Le fichier de politique de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"


def _liste_validee(data: Mapping[str, Any], cle: str, *, agent: str) -> tuple[str, ...]:
    """La liste `cle` (`allow`/`deny`) du fichier, entrées validées et dédoublonnées.

    Chaque entrée doit être un nom d'outil admissible (`_ENTREE` par segment
    `__`) : une politique fautive est refusée en bloc avec sa cause — jamais
    appliquée en partie.
    """
    brut = data.get(cle, [])
    if not isinstance(brut, list):
        raise ValueError(
            f"politique de permissions invalide pour l'agent {agent!r} : "
            f"{cle} doit être une liste de noms d'outils."
        )
    entrees: list[str] = []
    for entree in brut:
        if (
            not isinstance(entree, str)
            or not entree
            or not all(_ENTREE.match(segment) for segment in entree.split("__"))
        ):
            raise ValueError(
                f"politique de permissions invalide pour l'agent {agent!r} : "
                f"entrée {cle} {entree!r} (nom d'outil attendu — ex. « Bash », "
                "« mcp__slack » ou « mcp__slack__send_message »)."
            )
        if entree not in entrees:
            entrees.append(entree)
    return tuple(entrees)
