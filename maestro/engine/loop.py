"""Moteur d'orchestration : la boucle objectif → tâches → agents → agrégat (ticket #6).

Assemble les briques du POC en une **boucle d'orchestration** :

1. l'orchestrateur (#3) découpe l'objectif en tâches validées ;
2. le routeur (#6) assigne chaque tâche à l'agent le plus compétent ;
3. le moteur exécute les tâches **dans l'ordre des dépendances** (tri topologique),
   chaque agent produisant son livrable via la couche fournisseur (`ModelProvider`)
   et recevant les résultats des tâches dont il dépend (tableau noir léger) ;
4. les résultats sont **agrégés** en un `RunReport` (rapport structuré déterministe).

Les rôles disposant d'un **runtime outillé** (#35 : `developpeur`, `bdd` — cf.
`maestro.agents.default_runtimes`) exécutent leur tâche via ce runtime, dans un espace
de travail isolé : leur `TaskResult` porte alors aussi les **fichiers produits**. Les
autres rôles livrent leur texte via `generate()`, comme avant. Si le fournisseur ne
sait pas exécuter d'agent outillé (`UnsupportedCapability`), le rôle **retombe sur le
livrable texte** plutôt que d'échouer — la boucle reste utilisable avec un fournisseur
texte-seul.

Le moteur ne dépend que de `ModelProvider` : il reste **agnostique du fournisseur**.
`OrchestrationEngine.default` câble le Claude du POC, comme `Orchestrator.default`.

La boucle est **résiliente** : un échec de routage ou d'exécution est consigné dans
le résultat de la tâche (`statut = "echec"`) et n'interrompt pas les autres — le
rapport agrège les réussites comme les échecs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.runtime import AgentRuntime
from maestro.config import Settings, load_settings
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task, topological_order
from maestro.providers.base import ModelProvider, UnsupportedCapability
from maestro.router.router import RoutingError, assign
from maestro.sandbox import ProducedFile

#: Statuts terminaux d'une tâche, alignés sur la machine à états (docs/03 §3).
STATUT_TERMINEE = "terminee"
STATUT_ECHEC = "echec"


@dataclass(frozen=True)
class TaskResult:
    """Issue de l'exécution d'une tâche par l'agent qui lui a été assigné.

    Miroir léger de l'entité `RUN` (docs/03) pour le POC : qui a fait quoi, avec quel
    statut et quel livrable. `sortie` porte le livrable si `terminee`, `erreur` la
    cause si `echec` (auquel cas `sortie` est vide). `fichiers` porte les fichiers
    produits quand la tâche est passée par un runtime outillé (#35) — vide pour un
    livrable texte.
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
    fichiers: tuple[ProducedFile, ...] = ()

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
            "fichiers": [f.to_dict() for f in self.fichiers],
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
                if r.fichiers:
                    lignes.append(f"Fichiers produits ({len(r.fichiers)}) :")
                    lignes.extend(f"- `{f.chemin}`" for f in r.fichiers)
                    lignes.append("")
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
        runtimes: Mapping[str, AgentRuntime] | None = None,
    ) -> None:
        self._provider = provider
        self._orchestrator = orchestrator
        self._agents = tuple(agents)
        # Runtimes outillés, indexés par nom d'agent du catalogue. Par défaut, ceux
        # du POC (`developpeur`, `bdd`) adossés au même fournisseur que le moteur.
        self._runtimes = (
            dict(runtimes) if runtimes is not None else default_runtimes(provider)
        )

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
        dependances = [done[dep] for dep in task.dependances if dep in done]
        try:
            sortie, fichiers = await self._produce(agent, task, dependances)
        except Exception as exc:  # exécution: on consigne l'échec sans casser la boucle
            return _echec(
                task, agent=agent.nom, role=agent.role, score=assignment.score, erreur=str(exc)
            )

        sortie = sortie.strip()
        if not sortie and not fichiers:
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
            fichiers=fichiers,
        )

    async def _produce(
        self, agent: Agent, task: Task, dependances: Sequence[TaskResult]
    ) -> tuple[str, tuple[ProducedFile, ...]]:
        """Produit le livrable de `task` : runtime outillé si le rôle en a un, sinon texte.

        Un rôle outillé (#35) exécute la tâche dans un espace isolé et renvoie aussi
        ses fichiers. Si le fournisseur ne sait pas exécuter d'agent outillé
        (`UnsupportedCapability`), le rôle retombe sur son livrable texte via
        `generate()` — même chemin que les rôles sans runtime.
        """
        runtime = self._runtimes.get(agent.nom)
        if runtime is not None:
            try:
                outcome = await runtime.execute(
                    _build_task_description(task, dependances),
                    format_sortie=task.format_sortie,
                )
                return outcome.resume, outcome.fichiers
            except UnsupportedCapability:
                pass  # fournisseur texte-seul : repli sur le livrable texte
        sortie = await self._provider.generate(
            _build_task_prompt(task, dependances),
            model=agent.modele,
            system_prompt=agent.prompt_systeme,
        )
        return sortie, ()


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


def _build_task_description(task: Task, dependances: Sequence[TaskResult]) -> str:
    """Décrit la tâche et les livrables de ses dépendances (le « tableau noir »).

    Les résultats des dépendances forment le tableau noir : l'agent voit ce que les
    tâches prérequises ont produit, pour enchaîner de façon cohérente. C'est la
    matière commune aux deux chemins d'exécution — runtime outillé et appel texte.
    """
    lignes = [
        f"Tâche : {task.titre}",
        "",
        "Description :",
        task.description,
    ]
    if dependances:
        lignes += ["", "Résultats des tâches dont celle-ci dépend :"]
        for dep in dependances:
            livrable = dep.sortie or "(aucune sortie — tâche en échec)"
            lignes += ["", f"— [{dep.titre}] (agent {dep.role}) :", livrable]
    return "\n".join(lignes)


def _build_task_prompt(task: Task, dependances: Sequence[TaskResult]) -> str:
    """Compose le message d'un livrable *texte* : la description + le format attendu.

    Le chemin outillé n'en a pas besoin : le runtime encadre lui-même la description
    avec les consignes de son rôle (cf. `maestro.agents.runtime._build_prompt`).
    """
    lignes = [
        _build_task_description(task, dependances),
        "",
        f"Format de sortie attendu : {task.format_sortie}",
        "",
        "Produis maintenant le livrable demandé.",
    ]
    return "\n".join(lignes)
