"""Moteur d'orchestration : la boucle objectif → tâches → agents → agrégat (ticket #6).

Assemble les briques du POC en une **boucle d'orchestration** :

1. l'orchestrateur (#3) découpe l'objectif en tâches validées ;
2. le routeur (#6) assigne chaque tâche à l'agent le plus compétent ;
3. le moteur exécute les tâches **dans l'ordre des dépendances** (tri topologique),
   chaque agent produisant son livrable via la couche fournisseur (`ModelProvider`)
   et recevant les résultats des tâches dont il dépend (tableau noir léger) ;
4. les résultats sont **agrégés** en un `RunReport` (rapport structuré déterministe).

Le moteur ne dépend que de `ModelProvider` : il reste **agnostique du fournisseur**.
`OrchestrationEngine.default` câble le Claude du POC, comme `Orchestrator.default`.

La boucle est **résiliente** : un échec de routage ou d'exécution est consigné dans
le résultat de la tâche (`statut = "echec"`) et n'interrompt pas les autres — le
rapport agrège les réussites comme les échecs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.config import Settings, load_settings
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task, topological_order
from maestro.providers.base import ModelProvider
from maestro.router.router import RoutingError, assign

#: Statuts terminaux d'une tâche, alignés sur la machine à états (docs/03 §3).
STATUT_TERMINEE = "terminee"
STATUT_ECHEC = "echec"


@dataclass(frozen=True)
class TaskResult:
    """Issue de l'exécution d'une tâche par l'agent qui lui a été assigné.

    Miroir léger de l'entité `RUN` (docs/03) pour le POC : qui a fait quoi, avec quel
    statut et quel livrable. `sortie` porte le livrable si `terminee`, `erreur` la
    cause si `echec` (auquel cas `sortie` est vide).
    """

    task_id: str
    titre: str
    agent: str
    role: str
    competences_requises: tuple[str, ...]
    score: int
    statut: str
    sortie: str
    erreur: str | None = None

    @property
    def ok(self) -> bool:
        """La tâche s'est-elle terminée avec succès ?"""
        return self.statut == STATUT_TERMINEE

    def to_dict(self) -> dict[str, Any]:
        """Réémet le résultat en dict JSON-sérialisable."""
        return {
            "task_id": self.task_id,
            "titre": self.titre,
            "agent": self.agent,
            "role": self.role,
            "competences_requises": list(self.competences_requises),
            "score": self.score,
            "statut": self.statut,
            "sortie": self.sortie,
            "erreur": self.erreur,
        }


@dataclass(frozen=True)
class RunReport:
    """Agrégat déterministe d'une exécution : l'objectif et le résultat de chaque tâche.

    `resultats` est dans l'**ordre d'exécution** (topologique). Le rapport n'appelle
    aucun modèle : il assemble ce que la boucle a collecté (choix « rapport structuré »
    du ticket #6).
    """

    objectif: str
    resultats: tuple[TaskResult, ...]

    @property
    def reussies(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches terminées avec succès."""
        return tuple(r for r in self.resultats if r.ok)

    @property
    def echouees(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches en échec (routage ou exécution)."""
        return tuple(r for r in self.resultats if not r.ok)

    def synthese(self) -> str:
        """Rend l'agrégat en Markdown : récap chiffré puis livrable par tâche."""
        lignes = [
            f"# Synthèse — {self.objectif}",
            "",
            f"{len(self.reussies)}/{len(self.resultats)} tâche(s) réussie(s).",
            "",
        ]
        for r in self.resultats:
            # Marqueurs en texte (pas d'emoji) : portables sur une console Windows
            # héritée (cp1252) comme en UTF-8, sans faire planter l'affichage.
            etat = "[terminée]" if r.ok else "[échec]"
            competences = ", ".join(r.competences_requises)
            lignes.append(f"## {etat} {r.titre}")
            lignes.append(f"- Agent : {r.role} (`{r.agent}`) — compétences : {competences}")
            if r.ok:
                lignes.extend(["", r.sortie, ""])
            else:
                lignes.extend([f"- Échec : {r.erreur}", ""])
        return "\n".join(lignes).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Réémet le rapport en dict JSON-sérialisable."""
        return {
            "objectif": self.objectif,
            "reussies": len(self.reussies),
            "total": len(self.resultats),
            "resultats": [r.to_dict() for r in self.resultats],
        }


class OrchestrationEngine:
    """Boucle d'orchestration : objectif → plan → assignation → exécution → agrégat."""

    def __init__(
        self,
        provider: ModelProvider,
        orchestrator: Orchestrator,
        *,
        agents: Sequence[Agent] = DEFAULT_AGENTS,
    ) -> None:
        self._provider = provider
        self._orchestrator = orchestrator
        self._agents = tuple(agents)

    @classmethod
    def default(cls, settings: Settings | None = None) -> OrchestrationEngine:
        """Moteur par défaut du POC : planification et exécution via Claude (config).

        Importe le fournisseur ici (et non en tête de module) pour ne pas lier le
        moteur agnostique à un fournisseur concret : seul ce raccourci connaît Claude.
        """
        from maestro.providers.claude import ClaudeProvider

        settings = settings or load_settings()
        provider = ClaudeProvider.from_settings(settings)
        orchestrator = Orchestrator(provider, model=settings.anthropic_model)
        return cls(provider, orchestrator)

    async def run(self, objective: str) -> RunReport:
        """Exécute la boucle complète pour `objective` et renvoie l'agrégat.

        Lève `ValueError` si l'objectif est vide et propage les erreurs de
        **planification** (`PlanParsingError`, `TaskValidationError`) : sans plan
        valide, il n'y a rien à orchestrer. En revanche, les échecs *par tâche*
        (routage, exécution) sont consignés, pas propagés.
        """
        tasks = await self._orchestrator.plan(objective)
        done: dict[str, TaskResult] = {}
        resultats: list[TaskResult] = []
        for task in topological_order(tasks):
            result = await self._execute(task, done)
            done[task.id] = result
            resultats.append(result)
        return RunReport(objectif=objective, resultats=tuple(resultats))

    async def _execute(self, task: Task, done: dict[str, TaskResult]) -> TaskResult:
        """Assigne puis exécute une tâche ; renvoie son `TaskResult` (jamais d'exception)."""
        try:
            assignment = assign(task, self._agents)
        except RoutingError as exc:
            return _echec(task, agent="—", role="non assigné", score=0, erreur=str(exc))

        agent = assignment.agent
        prompt = _build_task_prompt(task, [done[dep] for dep in task.dependances if dep in done])
        try:
            sortie = await self._provider.generate(
                prompt, model=agent.modele, system_prompt=agent.prompt_systeme
            )
        except Exception as exc:  # exécution: on consigne l'échec sans casser la boucle
            return _echec(
                task, agent=agent.nom, role=agent.role, score=assignment.score, erreur=str(exc)
            )

        sortie = sortie.strip()
        if not sortie:
            return _echec(
                task,
                agent=agent.nom,
                role=agent.role,
                score=assignment.score,
                erreur="réponse vide de l'agent.",
            )
        return TaskResult(
            task_id=task.id,
            titre=task.titre,
            agent=agent.nom,
            role=agent.role,
            competences_requises=task.competences_requises,
            score=assignment.score,
            statut=STATUT_TERMINEE,
            sortie=sortie,
        )


def _echec(task: Task, *, agent: str, role: str, score: int, erreur: str) -> TaskResult:
    """Construit un `TaskResult` en échec pour `task` (sortie vide, cause consignée)."""
    return TaskResult(
        task_id=task.id,
        titre=task.titre,
        agent=agent,
        role=role,
        competences_requises=task.competences_requises,
        score=score,
        statut=STATUT_ECHEC,
        sortie="",
        erreur=erreur,
    )


def _build_task_prompt(task: Task, dependances: Sequence[TaskResult]) -> str:
    """Compose le message confié à l'agent : la tâche + les livrables de ses dépendances.

    Les résultats des dépendances forment le « tableau noir » : l'agent voit ce que
    les tâches prérequises ont produit, pour enchaîner de façon cohérente.
    """
    lignes = [
        f"Tâche : {task.titre}",
        "",
        "Description :",
        task.description,
        "",
        f"Format de sortie attendu : {task.format_sortie}",
    ]
    if dependances:
        lignes += ["", "Résultats des tâches dont celle-ci dépend :"]
        for dep in dependances:
            livrable = dep.sortie or "(aucune sortie — tâche en échec)"
            lignes += ["", f"— [{dep.titre}] (agent {dep.role}) :", livrable]
    lignes += ["", "Produis maintenant le livrable demandé."]
    return "\n".join(lignes)
