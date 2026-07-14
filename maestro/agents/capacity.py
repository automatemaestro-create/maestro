"""Capacité des agents : activer/désactiver et plafond d'instances (ticket #86, EF-21).

Matérialise les champs `actif` et `instances_max` de l'entité AGENT (docs/03 §2) :
la **capacité** d'un agent — reçoit-il des tâches, et combien en même temps — se
règle depuis la Control Tower et a un **effet réel** sur l'exécution :

- un agent **désactivé** est écarté des candidats du routage
  (`maestro.router.Router.route`, paramètre `exclus`) : il ne reçoit plus de
  tâches, ni par auto-assignation ni par réassignation manuelle ;
- le nombre d'**instances** plafonne ses exécutions simultanées : la
  `JaugeInstances` retient une tâche routée vers un agent au complet jusqu'à ce
  qu'un créneau se libère.

Le réglage est **persisté hors du code** (`CapacityStore`, un fichier JSON par
agent — même pattern que `maestro.agents.store`) et relu **à chaud** à chaque
tâche, comme les playbooks (#78) : un réglage posé depuis l'UI vaut pour la
tâche suivante, sans redémarrage. Au POC le dépôt est sur fichiers
(`core/capacite/`, ou `MAESTRO_CAPACITE_DIR`) ; en V1 il passera en base
(champs `actif`/`instances_max` de la table AGENT) sans changer ce contrat.

Limite POC assumée : la jauge borne les exécutions simultanées **par process**
(par boucle asyncio) — exacte pour le moteur en process, elle ne coordonne pas
encore plusieurs workers entre eux (la coordination distribuée viendra avec la
persistance partagée, EF-16).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from maestro.config import Settings, load_settings

#: Plafond d'instances par défaut : un agent = une exécution à la fois (docs/09 :
#: on *augmente* les instances pour absorber la charge).
INSTANCES_DEFAUT = 1

#: Nom d'agent admissible comme fichier de stockage — même verrou que
#: `maestro.agents.store` : slug sûr, sans séparateur ni point.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class CapaciteAgent:
    """Le réglage de capacité d'un agent : reçoit-il des tâches, et combien à la fois.

    Miroir des champs `actif`/`instances_max` de l'entité AGENT (docs/03).
    `modifie_le` est posé par le dépôt à l'écriture (ISO 8601, UTC) — vide pour
    une capacité jamais réglée (les défauts du code).
    """

    nom: str
    actif: bool = True
    instances: int = INSTANCES_DEFAUT
    modifie_le: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet la capacité en dict JSON-sérialisable (le fichier stocké)."""
        return {
            "nom": self.nom,
            "actif": self.actif,
            "instances": self.instances,
            "modifie_le": self.modifie_le,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapaciteAgent:
        """Reconstruit une capacité depuis sa forme `to_dict` (le fichier stocké)."""
        return cls(
            nom=data["nom"],
            actif=data.get("actif", True),
            instances=data.get("instances", INSTANCES_DEFAUT),
            modifie_le=data.get("modifie_le", ""),
        )


class CapacityStore:
    """Dépôt des capacités d'agents, sur fichiers (`<racine>/<nom>.json`).

    Un fichier par agent, écrit atomiquement ; un agent sans fichier a la
    capacité **par défaut** (actif, une instance) — `lire` ne rend jamais None.
    Un seul écrivain à la fois au POC (l'API Control Tower) : le dépôt ne porte
    pas de verrou de concurrence. Les lecteurs (moteur, workers) relisent à
    chaque tâche — l'application est à chaud, comme les playbooks (#78).
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> CapacityStore:
        """Le dépôt configuré : `MAESTRO_CAPACITE_DIR`, sinon `core/capacite/` du dépôt."""
        settings = settings or load_settings()
        if settings.capacite_dir:
            return cls(Path(settings.capacite_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "capacite")

    def lire(self, nom: str) -> CapaciteAgent:
        """La capacité de l'agent `nom` — celle par défaut s'il n'a jamais été réglé."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return CapaciteAgent(nom=nom)
        capacite = CapaciteAgent.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier, comme pour les définitions d'agents.
        return replace(capacite, nom=nom)

    def lister(self) -> tuple[CapaciteAgent, ...]:
        """Les capacités **réglées** (stockées), par nom — les autres sont aux défauts."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            self.lire(chemin.stem)
            for chemin in sorted(self._racine.glob("*.json"))
            if _NOM_AGENT.match(chemin.stem)
        )

    def ecrire(self, capacite: CapaciteAgent) -> CapaciteAgent:
        """Persiste `capacite` (remplacement intégral) et la renvoie datée.

        Écriture atomique (fichier temporaire puis renommage). Lève `ValueError`
        si le réglage est invalide (nom hors slug, instances < 1) — le dépôt ne
        stocke jamais une capacité inapplicable.
        """
        if capacite.instances < 1:
            raise ValueError(
                f"instances doit être ≥ 1 (reçu : {capacite.instances}) — pour ne plus "
                "recevoir de tâches, désactiver l'agent."
            )
        chemin = self._chemin(capacite.nom)
        propre = replace(capacite, modifie_le=_maintenant())
        self._racine.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def supprimer(self, nom: str) -> bool:
        """Retire le réglage de `nom` (retour aux défauts) ; False s'il n'y était pas."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def inactifs(self) -> frozenset[str]:
        """Les noms des agents désactivés — les exclus du routage, relus à chaque tâche."""
        return frozenset(c.nom for c in self.lister() if not c.actif)

    def _chemin(self, nom: str) -> Path:
        """Le fichier de stockage de `nom`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(nom):
            raise ValueError(f"nom d'agent invalide : {nom!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{nom}.json"


class JaugeInstances:
    """Borne les exécutions simultanées d'un agent à son plafond d'instances.

    `creneau(nom, plafond)` s'utilise en contexte : l'entrée attend qu'un
    créneau se libère si l'agent est au complet, la sortie le rend et réveille
    les tâches en attente. `plafond` est un rappel (et pas un entier) pour que
    chaque prise — réveils compris — relise le réglage courant : une capacité
    augmentée depuis l'UI s'applique au prochain créneau disputé, sans
    redémarrage.

    La comptabilité est **par boucle asyncio** : exacte pour le moteur en
    process (toutes les tâches d'un run partagent la boucle), inerte côté
    worker Celery (une boucle par message — la coordination inter-process
    viendra avec la persistance partagée, cf. docstring du module).
    """

    def __init__(self) -> None:
        self._boucles: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[asyncio.Condition, dict[str, int]]
        ] = WeakKeyDictionary()

    @asynccontextmanager
    async def creneau(self, nom: str, plafond: Callable[[], int]) -> AsyncIterator[None]:
        """Occupe un créneau d'exécution de l'agent `nom` le temps du bloc."""
        condition, en_vol = self._etat_de_la_boucle()
        async with condition:
            while en_vol.get(nom, 0) >= max(1, plafond()):
                await condition.wait()
            en_vol[nom] = en_vol.get(nom, 0) + 1
        try:
            yield
        finally:
            async with condition:
                en_vol[nom] -= 1
                condition.notify_all()

    def _etat_de_la_boucle(self) -> tuple[asyncio.Condition, dict[str, int]]:
        """La condition et les compteurs de la boucle courante (créés au premier usage).

        Une `asyncio.Condition` ne s'attend que depuis sa boucle de création :
        l'état est donc tenu **par boucle** (clé faible — l'état d'une boucle
        disparaît avec elle). Un exécuteur partagé entre boucles (worker à
        threads) compte ainsi chaque boucle séparément, sans jamais se bloquer
        sur la condition d'une autre.
        """
        boucle = asyncio.get_running_loop()
        etat = self._boucles.get(boucle)
        if etat is None:
            etat = (asyncio.Condition(), {})
            self._boucles[boucle] = etat
        return etat


def _maintenant() -> str:
    """L'horodatage d'écriture d'un réglage (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
