"""Politique de permissions par agent et par outil (ticket #110, parent #102).

Chaque agent peut déclarer une politique **allow/ask/deny par outil** — outils
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

Entre « laisser passer » et « refuser », un **troisième cran** (#580, parent
#573) : `ask` — l'appel est suspendu le temps qu'une personne tranche. La
priorité est `deny` > `ask` > `allow`, et `PolitiqueOutils.decide` en est le
verbe : il rend le verdict (`Verdict.PASSE` / `ARBITRAGE` / `REFUS`) avec son
motif. Deux conséquences à ne pas défaire — un outil classé `ask` n'est **pas**
interdit (`autorise` le dit permis, `filtre_outils`/`serveur_autorise`
continuent de le monter : un outil retiré de la session n'atteindrait jamais
l'arbitrage), et `ask` l'emporte sur `allow`, donc un outil cité en `ask` est
arbitré **même** quand une liste `allow` fermée ne le cite pas — sinon le cran
du milieu serait lettre morte dès qu'une politique ferme sa liste. À ce lot
c'est un **contrat** : le verdict existe, aucun appelant ne le consulte encore
(le hook `PreToolUse` reste inchangé — lot #583).

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
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


def _cite_serveur(entrees: Iterable[str], prefixe: str) -> bool:
    """Une entrée cite-t-elle le serveur `prefixe` — en entier, ou un seul de ses outils ?

    Le sens inverse de `_correspond` : `mcp__slack__send_message` cite le
    serveur `mcp__slack`, alors que le serveur ne « correspond » pas à l'outil.
    C'est la question que pose le montage d'un serveur MCP, pas l'appel.
    """
    return any(
        entree == prefixe or entree.startswith(prefixe + "__") for entree in entrees
    )


class Verdict(StrEnum):
    """Ce qu'une politique dit d'un appel d'outil — trois crans, plus deux (#580).

    `PASSE` laisse l'appel au flux normal, `ARBITRAGE` le suspend le temps
    qu'une personne tranche, `REFUS` l'écarte. Valeurs en clair : elles
    voyagent telles quelles en JSON (journal, API, fil temps réel).
    """

    PASSE = "passe"
    ARBITRAGE = "arbitrage"
    REFUS = "refus"


@dataclass(frozen=True)
class DecisionOutil:
    """Le verdict rendu pour un outil, et le motif qui l'explique.

    Le motif est le texte lisible servi à l'agent (refus) ou à la personne qui
    arbitre — il nomme l'outil et la liste en cause, comme `raison_refus`
    aujourd'hui. Il est **vide sur `PASSE`** : rien ne s'oppose à l'appel, il
    n'y a rien à en dire.
    """

    verdict: Verdict
    motif: str = ""


def _motif_deny(outil: str) -> str:
    """Le motif d'un refus par la liste `deny` — servi à l'agent et tracé."""
    return (
        f"outil {outil!r} interdit par la politique de permissions de "
        "l'agent (liste deny). Poursuis la tâche sans cet outil."
    )


def _motif_allow(outil: str) -> str:
    """Le motif d'un refus par une liste `allow` fermée — servi à l'agent et tracé."""
    return (
        f"outil {outil!r} hors de la politique de permissions de l'agent "
        "(liste allow fermée). Poursuis la tâche sans cet outil."
    )


def _motif_ask(outil: str) -> str:
    """Le motif d'une mise en arbitrage — lu par la personne qui tranche.

    Il nomme l'**acte** (l'outil appelé) et jamais le titre de la tâche : c'est
    tout l'objet du parent #573, où un mot du livrable déclenchait l'arbitrage.
    """
    return (
        f"outil {outil!r} soumis à arbitrage humain par la politique de "
        "permissions de l'agent (liste ask)."
    )


@dataclass(frozen=True)
class PolitiqueOutils:
    """La politique allow/ask/deny d'un agent — la partie versionnable du contrat.

    Priorité explicite : `deny` l'emporte sur `ask`, qui l'emporte sur
    `allow`. `allow` vide laisse tout passer (hors `deny`/`ask`) ; non vide,
    c'est une liste fermée — mais `ask` la déborde, un outil cité en `ask`
    étant arbitré et non refusé. Les entrées des trois listes ont la même
    forme : un outil intégré (`Bash`), un serveur MCP entier (`mcp__slack`) ou
    un outil MCP précis (`mcp__slack__send_message`).
    """

    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def decide(self, outil: str) -> DecisionOutil:
        """Le verdict de la politique sur `outil`, motif compris.

        L'ordre des trois tests **est** la priorité annoncée (`deny` > `ask` >
        `allow`) : chacun tranche avant que le suivant ne soit posé. Deux
        conséquences voulues — un outil à la fois en `deny` et en `ask` est
        refusé (le cran le plus fermé gagne), et un outil en `ask` absent
        d'une liste `allow` fermée est **arbitré** plutôt que refusé, sans
        quoi fermer sa liste `allow` suffirait à rendre `ask` lettre morte.
        """
        if any(_correspond(entree, outil) for entree in self.deny):
            return DecisionOutil(Verdict.REFUS, _motif_deny(outil))
        if any(_correspond(entree, outil) for entree in self.ask):
            return DecisionOutil(Verdict.ARBITRAGE, _motif_ask(outil))
        if not self.allow or any(_correspond(entree, outil) for entree in self.allow):
            return DecisionOutil(Verdict.PASSE)
        return DecisionOutil(Verdict.REFUS, _motif_allow(outil))

    def autorise(self, outil: str) -> bool:
        """L'appel de `outil` est-il permis par cette politique ?

        Contrat inchangé : la question est « **non interdit** », pas « laissé
        passer sans question ». Un outil classé `ask` y répond donc oui — il
        doit être monté sur la session pour que l'arbitrage puisse avoir lieu
        au vol ; un outil retiré avant l'ouverture n'atteindrait jamais le
        point de contrôle qui doit le suspendre.
        """
        return self.decide(outil).verdict is not Verdict.REFUS

    def raison_refus(self, outil: str) -> str:
        """Le motif du refus de `outil` — le message servi à l'agent et tracé.

        N'est appelé que sur un outil refusé, et ne connaît donc que les deux
        motifs de refus : `decide` est le verbe qui distingue les trois crans.
        """
        if any(_correspond(entree, outil) for entree in self.deny):
            return _motif_deny(outil)
        return _motif_allow(outil)

    def filtre_outils(self, outils: Sequence[str]) -> tuple[str, ...]:
        """Les outils intégrés de `outils` que la politique permet de monter."""
        return tuple(outil for outil in outils if self.autorise(outil))

    def serveur_autorise(self, nom: str) -> bool:
        """Le serveur MCP `nom` mérite-t-il d'être monté sur la session ?

        Faux si le serveur est refusé **en entier** (`deny` porte
        `mcp__<nom>`), ou si une liste `allow` fermée n'en cite aucun outil —
        dans les deux cas il n'est pas monté et ses secrets ne sont jamais
        résolus. Un refus **individuel** (`deny` sur un seul de ses outils)
        laisse le serveur monté : c'est le refus au vol qui l'applique. Une
        entrée `ask` le monte de même, et déborde la liste `allow` fermée
        comme dans `decide` : un serveur qu'on n'a pas monté n'a aucun appel à
        soumettre à l'arbitrage.
        """
        prefixe = f"{_PREFIXE_MCP}{nom}"
        if any(_correspond(entree, prefixe) for entree in self.deny):
            return False
        if _cite_serveur(self.ask, prefixe):
            return True
        if not self.allow:
            return True
        return _cite_serveur(self.allow, prefixe)

    def to_dict(self) -> dict[str, Any]:
        """Réémet la politique en dict JSON-sérialisable (la forme stockée/publique)."""
        return {
            "allow": list(self.allow),
            "ask": list(self.ask),
            "deny": list(self.deny),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolitiqueOutils:
        """Reconstruit une politique depuis sa forme stockée (sans la valider).

        `ask` absent vaut liste vide : une politique écrite avant #580 se
        relit à l'identique, donc sous le régime d'hier.
        """
        return cls(
            allow=tuple(str(entree) for entree in data.get("allow", ())),
            ask=tuple(str(entree) for entree in data.get("ask", ())),
            deny=tuple(str(entree) for entree in data.get("deny", ())),
        )


class PermissionStore:
    """Dépôt des politiques de permissions par agent (`<racine>/<agent>.json`).

    Un fichier par agent : `{"allow": [...], "ask": [...], "deny": [...]}` — le
    nom d'agent fait foi côté fichier, comme pour les autres dépôts. Une liste
    absente vaut vide, `ask` comprise : un fichier écrit avant #580 se relit
    tel quel. Relu à chaque tâche
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
                f'({chemin.name}) : objet {{"allow": [...], "ask": [...], '
                '"deny": [...]} attendu.'
            )
        return PolitiqueOutils(
            allow=_liste_validee(data, "allow", agent=agent),
            ask=_liste_validee(data, "ask", agent=agent),
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
    """La liste `cle` (`allow`/`ask`/`deny`) du fichier, entrées validées et dédoublonnées.

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
