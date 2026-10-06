"""L'**état acquis** d'un run : ce qu'il a payé, et qu'une reprise ne refait pas (#1391).

docs/28 §12 (cadrage #701, 2026-08-28) a tranché comment reprendre un run interrompu :
non pas en le rejouant depuis son brief — la relance de #349, qui re-planifie et refait
tout —, mais **sur son plan**, à la granularité de la tâche terminée, depuis un état
tenu **hors du process** du run. Ce module en porte la forme et le contrat ; le magasin
de production vit côté Control Tower (`maestro.controltower.acquis`), qui seul sait où
un run garde ses données.

**Deux choses, et pas une** (docs/28 §12.2). Le journal durable dit qu'une tâche a
réussi, jamais ce qu'elle a produit — or sa `sortie` est précisément ce qui entre dans
le prompt de la tâche suivante. Et le plan que la projection garde (`NoeudPlan`) suffit
à dessiner un graphe, pas à reprendre une exécution : il lui manque la description (le
prompt), les compétences (le routage) et le format de sortie. L'état acquis est donc :

- le **plan exécutable** — les `Task` entières, telles que le moteur les déroule, ticket
  et projet hérités compris ;
- les **issues réussies** — un `TaskResult` par tâche terminée, sortie et usage compris.

Rien n'est inventé pour les transporter : `Task.to_dict` et `TaskResult.to_dict` sont
déjà les formes qui voyagent entre process (la file #41, le mode durable #95). L'état
acquis d'un run est une valeur JSON.

**Ce qui n'y entre pas, et pourquoi.** Une tâche en échec, bloquée ou interrompue n'est
pas acquise : elle n'a rien produit qu'une autre puisse lire, et elle sera refaite. La
tâche **en vol** au moment de l'interruption non plus — c'est la même règle que le mode
durable (`MaestroRunWorkflow.resultats_acquis`) : « elle n'a rien produit et sera
reprise ». Un run reprend donc là où il s'est arrêté **à la tâche près, pas au milieu
d'une tâche** (docs/28 §12.6) — et la tâche interrompue repart de **sa branche**, où son
travail a été porté à l'interruption (#1392, `maestro.sandbox.projet`) : ce magasin n'a
pas à le tenir, Git le tient.

**Qui écrit, qui lit.** Le moteur écrit, au fil du run : le plan une fois figé, puis
chaque issue réussie à l'instant où elle l'est — du côté du **producteur**, la leçon de
#699 (docs/28 §12.2) : un état acquis écrit par un consommateur serait troué exactement
quand on en a besoin. La Control Tower relit, décide de reprendre, et rend l'état au
moteur du run repris (`OrchestrationEngine.run(reprise=…)`). Une écriture qui échoue
**ne fait jamais échouer le run** : elle lui coûte d'être moins reprenable, ce que le
journal du process dit.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.engine.executor import TaskResult
from maestro.orchestrator.schema import Task

#: L'entrée du plan dans l'état acquis d'un run — une par run.
ENTREE_PLAN = "plan"

#: Le préfixe de l'entrée d'une issue réussie : `tache:<id>`, une par tâche acquise.
#: Une entrée **par tâche** et non une liste qu'on réécrirait : deux tâches menées de
#: front qui réussissent ensemble écrivent chacune la sienne, sans relire celle de
#: l'autre — aucune mise à jour perdue, sans verrou.
PREFIXE_TACHE = "tache:"


@dataclass(frozen=True)
class EtatAcquis:
    """Ce qu'un run a payé : son plan exécutable et ses tâches réussies (#1391).

    `plan` est dans l'ordre où le moteur l'a figé ; `resultats` ne porte que des
    issues **réussies**, rangées par identifiant de tâche. Une issue qui ne
    désigne aucune tâche du plan est ignorée à la lecture (`depuis_entrees`) :
    elle ne saurait être rendue à personne.
    """

    plan: tuple[Task, ...]
    resultats: Mapping[str, TaskResult] = field(default_factory=dict)

    @property
    def acquises(self) -> tuple[str, ...]:
        """Les tâches acquises, **dans l'ordre du plan** — celles qu'une reprise ne refait pas."""
        return tuple(t.id for t in self.plan if t.id in self.resultats)

    @property
    def a_reprendre(self) -> tuple[str, ...]:
        """Les tâches qui restent, dans l'ordre du plan — interrompue et jamais démarrées."""
        return tuple(t.id for t in self.plan if t.id not in self.resultats)

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'état en dict JSON-sérialisable (`{plan, resultats}`)."""
        return {
            "plan": [t.to_dict() for t in self.plan],
            "resultats": [self.resultats[i].to_dict() for i in self.acquises],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EtatAcquis:
        """Reconstruit l'état depuis sa forme `to_dict` — l'aller-retour de l'ordre d'un run."""
        plan = tuple(Task.from_dict(t) for t in data.get("plan") or ())
        return cls.depuis(plan, (TaskResult.from_dict(r) for r in data.get("resultats") or ()))

    @classmethod
    def depuis(cls, plan: Sequence[Task], resultats: Any) -> EtatAcquis:
        """L'état d'un plan et d'issues lues — les réussies du plan, et elles seules."""
        ids = {t.id for t in plan}
        return cls(
            plan=tuple(plan),
            resultats={r.task_id: r for r in resultats if r.ok and r.task_id in ids},
        )

    @classmethod
    def depuis_entrees(cls, entrees: Mapping[str, str]) -> EtatAcquis | None:
        """Relit l'état depuis les entrées brutes d'un magasin — **None** sans plan lisible.

        Le pendant de `entrees_du_plan` / `entree_du_resultat`, commun aux trois
        magasins : ils rangent tous des `(clé, JSON)` par run, et une seule lecture
        tient la règle. Un plan illisible ne se répare pas — sans lui il n'y a rien à
        reprendre, et le dire (`None`) vaut mieux que reprendre de travers. Une issue
        illisible, elle, est écartée seule : sa tâche sera refaite, ce qui est le prix
        juste d'une donnée qu'on ne sait plus lire.
        """
        brut = entrees.get(ENTREE_PLAN)
        if brut is None:
            return None
        try:
            plan = tuple(Task.from_dict(t) for t in json.loads(brut))
        except (ValueError, TypeError, KeyError):
            return None
        if not plan:
            return None
        resultats: list[TaskResult] = []
        for cle, valeur in entrees.items():
            if not cle.startswith(PREFIXE_TACHE):
                continue
            try:
                resultats.append(TaskResult.from_dict(json.loads(valeur)))
            except (ValueError, TypeError, KeyError):
                continue
        return cls.depuis(plan, resultats)


def entree_du_plan(tasks: Sequence[Task]) -> tuple[str, str]:
    """L'entrée `(clé, JSON)` qui range le plan d'un run."""
    return ENTREE_PLAN, json.dumps([t.to_dict() for t in tasks], ensure_ascii=False)


def entree_du_resultat(resultat: TaskResult) -> tuple[str, str]:
    """L'entrée `(clé, JSON)` qui range une issue réussie."""
    return (
        f"{PREFIXE_TACHE}{resultat.task_id}",
        json.dumps(resultat.to_dict(), ensure_ascii=False),
    )


class MagasinAcquis(ABC):
    """Où un run garde son état acquis — hors de son process (#1391, docs/28 §12.6).

    Quatre verbes, et c'est tout le contrat : le moteur **pose** le plan puis
    **acquiert** chaque issue réussie ; la Control Tower **lit** l'état d'un run
    pour le reprendre ; et un run qui n'a plus rien à reprendre est **oublié**.
    Le stockage est un détail d'implémentation — mémoire, Redis, SQLite —, comme
    pour le journal durable (#97, #639), dont ce magasin suit le support.

    ⚠ Écrire est **idempotent** et **par entrée** : poser deux fois le même plan,
    acquérir deux fois la même tâche (une QA qui renvoie un livrable à son
    producteur, #1177) remplace, ne cumule pas.
    """

    @abstractmethod
    async def poser_plan(self, run_id: str, tasks: Sequence[Task]) -> None:
        """Range le plan exécutable du run."""

    @abstractmethod
    async def acquerir(self, run_id: str, resultat: TaskResult) -> None:
        """Range une issue **réussie** du run — une issue qui ne l'est pas est ignorée."""

    @abstractmethod
    async def lire(self, run_id: str) -> EtatAcquis | None:
        """L'état acquis du run, ou None s'il n'a pas de plan rangé."""

    @abstractmethod
    async def oublier(self, run_id: str) -> None:
        """Retire l'état du run — il n'a plus rien à reprendre."""

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027),
    # comme `EventLog.close` — seul un magasin à connexion a quelque chose à libérer.
    async def close(self) -> None:  # noqa: B027
        """Libère les ressources du magasin (connexion) — sans effet par défaut."""


class MagasinAcquisMemoire(MagasinAcquis):
    """Le magasin en process : celui des tests, et d'un run qui ne survit pas à son API.

    Il ne survit à rien — c'est le transport des tests et de l'hôte en process
    d'une app qui n'a rien de durable (`create_app` nu). Il honore le contrat à
    l'identique, entrées comprises : ce que les tests y éprouvent est la règle,
    pas un raccourci. Aucun verrou : chaque geste est un accès au dictionnaire sans
    `await` au milieu, donc atomique pour la boucle qui l'appelle.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, str]] = {}

    async def poser_plan(self, run_id: str, tasks: Sequence[Task]) -> None:
        cle, valeur = entree_du_plan(tasks)
        self._runs.setdefault(run_id, {})[cle] = valeur

    async def acquerir(self, run_id: str, resultat: TaskResult) -> None:
        if not resultat.ok:
            return
        cle, valeur = entree_du_resultat(resultat)
        self._runs.setdefault(run_id, {})[cle] = valeur

    async def lire(self, run_id: str) -> EtatAcquis | None:
        return EtatAcquis.depuis_entrees(dict(self._runs.get(run_id, {})))

    async def oublier(self, run_id: str) -> None:
        self._runs.pop(run_id, None)


__all__ = [
    "ENTREE_PLAN",
    "PREFIXE_TACHE",
    "EtatAcquis",
    "MagasinAcquis",
    "MagasinAcquisMemoire",
    "entree_du_plan",
    "entree_du_resultat",
]
