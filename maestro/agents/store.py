"""Catalogue dynamique d'agents : définitions persistées hors du code (ticket #72).

Matérialise la moitié « configuration » d'EF-03 (création d'agents personnalisés,
parent #70) : la définition d'un agent — nom, rôle, playbook, compétences,
fournisseur/modèle/effort — devient un document **persisté hors du code**, créé et
modifié depuis l'API Control Tower (`maestro.controltower.app`, endpoints
`/api/catalogue`). Ce que ces trois réglages de modèle peuvent valoir se lit, lui,
sur `GET /api/fournisseurs` (#253) — le dépôt stocke un choix, il ne dit pas
l'offre.
Au POC le dépôt est sur fichiers (`core/agents/<nom>.json`) ; en V1 il passera en
base (table AGENT, docs/03) sans changer ce contrat.

Le catalogue **effectif** d'une exécution est l'assemblage `catalogue()` : les
agents par défaut du code (`DEFAULT_AGENTS`, inchangés) suivis des agents
personnalisés du dépôt — l'ordre préserve le départage déterministe du routeur
(les rôles du code restent prioritaires à score égal). Un dépôt vide rend le
catalogue par défaut à l'identique, ce qui rend ce lot mergeable seul.

Le chargement se fait **au câblage** (construction du moteur, premier message d'un
worker, démarrage de l'API) : un agent créé est routable et exécutable par les
moteurs construits ensuite — sans runtime outillé, il produit son livrable par le
chemin texte (`LocalExecutor._produce`), cadré par son playbook et son modèle.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.catalog import (
    DEFAULT_AGENTS,
    MODELE_EXECUTANT_DEFAUT,
    Agent,
    agents_pour,
)
from maestro.config import Settings, load_settings

#: Nom d'agent admissible comme fichier de stockage : slug sûr, sans séparateur ni
#: point — verrouille toute traversée de chemin depuis un nom venu de l'API.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Noms réservés : les agents par défaut du code (un agent personnalisé ne peut pas
#: les masquer) et les acteurs système de la Control Tower — l'orchestrateur, et
#: l'assistant du canal d'aide (#123, `maestro.controltower.assistance.NOM_ASSISTANCE` :
#: la chaîne est répétée ici plutôt qu'importée, la couche agents ne dépendant pas de
#: la Control Tower). Sans cette réserve, un agent personnalisé homonyme partagerait le
#: fil `core/chat/assistance.jsonl` de l'assistant et serait masqué par lui au chat.
NOMS_RESERVES: frozenset[str] = frozenset(
    {agent.nom for agent in DEFAULT_AGENTS} | {"orchestrateur", "assistance"}
)


@dataclass(frozen=True)
class AgentDefinition:
    """La définition persistée d'un agent personnalisé — l'entité AGENT (docs/03).

    `playbook` porte les instructions du rôle (le prompt système d'exécution,
    docs/04 §1) ; `modele` le modèle conseillé (None : le modèle par défaut des
    exécutants) ; `fournisseur` est **déclaratif au POC** — le moteur exécute sur
    un fournisseur unique (`MAESTRO_PROVIDER`), le champ prépare l'exécution
    multi-fournisseurs sans la promettre. `cree_le`/`modifie_le` sont posés par
    le dépôt à l'écriture (ISO 8601, UTC).

    `effort` (#253) est le troisième réglage de modèle, à côté de `fournisseur`
    et `modele` — et lui, contrairement au fournisseur, **atteint l'exécution**
    (`Agent.effort`, puis la frontière). Il est stocké **tel qu'il a été posé**,
    sans confrontation à ce que le fournisseur admet : le dépôt ne connaît aucun
    fournisseur, et un catalogue qui bouge (modèle changé, gamme élargie) ne doit
    pas rendre invalide une définition écrite hier. Le tri se fait à
    l'exécution, où il est **ignoré sans erreur** s'il n'a plus cours.
    """

    nom: str
    role: str
    competences: tuple[str, ...]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None
    effort: str | None = None
    cree_le: str = ""
    modifie_le: str = ""

    def to_agent(self, modele_impose: str | None = None) -> Agent:
        """La définition muée en `Agent` du catalogue, routable et exécutable.

        `modele_impose` (#69, `MAESTRO_MODEL`) prime sur le modèle de la
        définition — même bascule globale que pour les agents par défaut. Elle ne
        touche **pas** à l'effort (#253) : `MAESTRO_MODEL` bascule le modèle, et
        un effort n'est pas un modèle — l'écraser au passage retirerait en silence
        un réglage que personne n'a demandé de retirer.
        """
        return Agent(
            nom=self.nom,
            role=self.role,
            competences=frozenset(self.competences),
            modele=modele_impose or self.modele or MODELE_EXECUTANT_DEFAUT,
            prompt_systeme=self.playbook,
            effort=self.effort,
        )

    def to_dict(self, *, avec_playbook: bool = True) -> dict[str, Any]:
        """Réémet la définition en dict JSON-sérialisable (métadonnées seules si demandé)."""
        fiche: dict[str, Any] = {
            "nom": self.nom,
            "role": self.role,
            "competences": list(self.competences),
            "modele": self.modele,
            "fournisseur": self.fournisseur,
            "effort": self.effort,
            "cree_le": self.cree_le,
            "modifie_le": self.modifie_le,
        }
        if avec_playbook:
            fiche["playbook"] = self.playbook
        return fiche

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        """Reconstruit une définition depuis sa forme `to_dict` (le fichier stocké).

        `effort` est lu en `get` comme les autres réglages optionnels : les
        fichiers écrits avant #253 n'en portent pas, et se relisent sans
        migration ni valeur par défaut inventée.
        """
        return cls(
            nom=data["nom"],
            role=data["role"],
            competences=tuple(data.get("competences", ())),
            playbook=data.get("playbook", ""),
            modele=data.get("modele"),
            fournisseur=data.get("fournisseur"),
            effort=data.get("effort"),
            cree_le=data.get("cree_le", ""),
            modifie_le=data.get("modifie_le", ""),
        )


class AgentStore:
    """Dépôt des définitions d'agents personnalisés, sur fichiers (`<racine>/<nom>.json`).

    Un fichier par agent, écrit atomiquement ; `ecrire` crée ou remplace la
    définition (la date de création survit au remplacement), `supprimer` la
    retire. Un seul écrivain à la fois au POC (l'API Control Tower) : le dépôt
    ne porte pas de verrou de concurrence.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> AgentStore:
        """Le dépôt configuré : `MAESTRO_AGENTS_DIR`, sinon `core/agents/` du dépôt."""
        settings = settings or load_settings()
        if settings.agents_dir:
            return cls(Path(settings.agents_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "agents")

    def noms(self) -> tuple[str, ...]:
        """Les noms des agents personnalisés stockés, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def lister(self) -> tuple[AgentDefinition, ...]:
        """Les définitions stockées, dans l'ordre des noms — déterministe pour le routage."""
        return tuple(
            definition
            for nom in self.noms()
            if (definition := self.lire(nom)) is not None
        )

    def lire(self, nom: str) -> AgentDefinition | None:
        """La définition de l'agent `nom`, ou None s'il n'est pas dans le dépôt."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return None
        definition = AgentDefinition.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier : un contenu recopié sous un autre nom
        # reste adressé (et donc routé) par le nom du fichier.
        return replace(definition, nom=nom)

    def ecrire(self, definition: AgentDefinition) -> AgentDefinition:
        """Persiste `definition` (création ou remplacement intégral) et la renvoie datée.

        Écriture atomique (fichier temporaire puis renommage) : une définition
        n'apparaît dans le dépôt que complète. Lève `ValueError` si la définition
        est invalide (nom hors slug ou réservé, rôle/playbook vides, aucune
        compétence) — le dépôt ne stocke jamais un agent inexécutable.
        """
        propre = _valide(definition)
        existante = self.lire(propre.nom)
        maintenant = _maintenant()
        propre = replace(
            propre,
            cree_le=existante.cree_le if existante is not None else maintenant,
            modifie_le=maintenant,
        )
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._chemin(propre.nom)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def supprimer(self, nom: str) -> bool:
        """Retire l'agent `nom` du dépôt ; False s'il n'y était pas (rien à faire)."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def _chemin(self, nom: str) -> Path:
        """Le fichier de stockage de `nom`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(nom):
            raise ValueError(f"nom d'agent invalide : {nom!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{nom}.json"


def catalogue(store: AgentStore | None = None, modele: str | None = None) -> tuple[Agent, ...]:
    """Le catalogue effectif : les agents par défaut, puis les personnalisés du dépôt.

    C'est le point de chargement « au démarrage » du #72 : les moteurs
    (`OrchestrationEngine.default`), les workers (`maestro.queue.worker`) et
    l'état Control Tower assemblent leur catalogue ici. Les agents par défaut
    gardent la tête (leur ordre départage les ex æquo de routage) ; les
    personnalisés suivent, par nom. `modele` (#69, `MAESTRO_MODEL`) bascule
    l'ensemble sur un modèle unique, définitions personnalisées comprises.
    """
    store = store if store is not None else AgentStore.default()
    return agents_pour(modele) + tuple(
        definition.to_agent(modele) for definition in store.lister()
    )


def _valide(definition: AgentDefinition) -> AgentDefinition:
    """La définition normalisée (compétences épurées), ou `ValueError` si invalide.

    L'`effort` (#253) est **normalisé, jamais refusé** : épuré, et ramené à `None`
    s'il ne reste rien — une chaîne vide et « pas de réglage » ne doivent pas
    coexister dans le dépôt, sans quoi l'UI aurait deux façons de dire la même
    absence. Le refuser, en revanche, exigerait de connaître ici le fournisseur et
    sa gamme du jour ; c'est la frontière qui tranche, et elle ignore.
    """
    if not _NOM_AGENT.match(definition.nom):
        raise ValueError(
            f"nom d'agent invalide : {definition.nom!r} (slug [a-z0-9_-] attendu)."
        )
    if definition.nom in NOMS_RESERVES:
        raise ValueError(
            f"nom d'agent réservé : {definition.nom!r} (agent par défaut ou acteur système)."
        )
    if not definition.role.strip():
        raise ValueError(f"rôle vide pour l'agent {definition.nom!r}.")
    if not definition.playbook.strip():
        raise ValueError(f"playbook vide pour l'agent {definition.nom!r}.")
    competences = tuple(
        dict.fromkeys(c.strip() for c in definition.competences if c.strip())
    )
    if not competences:
        raise ValueError(
            f"aucune compétence pour l'agent {definition.nom!r} : il ne serait "
            "jamais retenu par les règles de routage."
        )
    effort = (definition.effort or "").strip() or None
    return replace(
        definition,
        role=definition.role.strip(),
        competences=competences,
        effort=effort,
    )


def _maintenant() -> str:
    """L'horodatage d'écriture d'une définition (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
