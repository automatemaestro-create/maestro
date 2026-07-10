"""Comptabilité de coût par tâche — le grand livre d'une exécution (ticket #55).

Le journal (#8) consigne déjà chaque étape avec son usage (tokens, coût, durée) ;
cette brique le **réorganise en comptabilité** : une entrée par tâche
(`TaskCost`), l'étape de planification à part, et l'agrégat de l'exécution
(`RunCost`) — la vue « coût de l'exécution, traçable par tâche » du critère MVP
n°6 (parent #49), que l'API Control Tower exposera (#57) et sur laquelle le
plafond de dépense s'adossera (#56).

L'attribution suit la convention d'étape du journal : `planification` pour
l'orchestrateur, `<tache>` pour l'étape de la tâche elle-même, et
`<tache>:<annexe>` pour ses étapes annexes (validation humaine #48, message
inter-agents #44) — chaque annexe est rattachée à sa tâche. Chaque ligne du
journal est ainsi comptée exactement une fois : le total de la comptabilité
retombe sur `RunJournal.usage_totale`.

La comptabilité n'évalue **aucun prix** : le coût estimé est celui rapporté par
le fournisseur via la couche `ModelProvider` (#32) — la tarification par modèle
reste de son côté, jamais en dur ici. Un fournisseur qui ne rapporte pas de coût
laisse la tâche à coût « inconnu » (None, à distinguer d'un coût nul).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from maestro.telemetry.journal import RunJournal
from maestro.telemetry.usage import StepUsage

#: Étape du journal qui n'appartient à aucune tâche : la planification (#8).
ETAPE_PLANIFICATION = "planification"


@dataclass(frozen=True)
class TaskCost:
    """L'entrée « tâche » du grand livre : qui a fait quoi, pour quel usage.

    `usage` fusionne l'étape de la tâche et ses étapes annexes (validation,
    message) — celles-ci rapportent un usage nul aujourd'hui, la fusion les
    garderait comptées si elles en portaient un demain. `nom`, `agent`, `role`
    et `statut` viennent de l'étape de la tâche elle-même ; ils restent vides
    tant que seule une annexe a été consignée (tâche encore en cours).
    """

    tache_id: str
    nom: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    usage: StepUsage = StepUsage()

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'entrée en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "tache_id": self.tache_id,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class RunCost:
    """Le grand livre d'une exécution : le coût par tâche et l'agrégat du run.

    `taches` suit l'ordre de première apparition au journal — l'ordre
    d'achèvement des tâches (#7), chaque entrée restant reliée au plan par
    `tache_id` ; `planification` porte l'usage de l'orchestrateur.
    """

    run_id: str
    planification: StepUsage = StepUsage()
    taches: tuple[TaskCost, ...] = ()

    @property
    def total(self) -> StepUsage:
        """Usage agrégé de l'exécution — retombe sur `RunJournal.usage_totale`."""
        total = self.planification
        for tache in self.taches:
            total = total.fusion(tache.usage)
        return total

    @classmethod
    def depuis_journal(cls, journal: RunJournal) -> RunCost:
        """Comptabilise `journal` : chaque ligne attribuée à sa tâche, comptée une fois.

        S'appuie sur `RunJournal.records` (l'agrégat mémoire) : utilisable en
        fin d'exécution comme en cours de run (comptabilité partielle — une
        tâche dont seule une annexe est consignée apparaît sans identité ni
        statut, son usage déjà compté).
        """
        planification = StepUsage()
        entrees: dict[str, TaskCost] = {}
        for record in journal.records:
            if record.etape == ETAPE_PLANIFICATION:
                planification = planification.fusion(record.usage)
                continue
            tache_id = record.etape.split(":", 1)[0]
            entree = entrees.get(tache_id)
            if entree is None:
                entree = TaskCost(tache_id=tache_id)
            if record.etape == tache_id:
                # L'étape de la tâche elle-même : elle fait foi pour l'identité.
                entree = replace(
                    entree,
                    nom=record.nom,
                    agent=record.agent,
                    role=record.role,
                    statut=record.statut,
                    usage=entree.usage.fusion(record.usage),
                )
            else:
                entree = replace(entree, usage=entree.usage.fusion(record.usage))
            entrees[tache_id] = entree
        return cls(
            run_id=journal.run_id,
            planification=planification,
            taches=tuple(entrees.values()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet le grand livre en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "run_id": self.run_id,
            "planification": self.planification.to_dict(),
            "total": self.total.to_dict(),
            "taches": [tache.to_dict() for tache in self.taches],
        }
