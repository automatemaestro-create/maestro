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

Chaque étape (planification comprise) est **journalisée et mesurée** (#8) : durée
horloge chronométrée ici, tokens/coût/outils récoltés auprès du fournisseur via
`maestro.telemetry.collect_usage`, le tout consigné dans un `RunJournal` (une ligne
JSON par étape) et porté par les `TaskResult` — le coût par tâche est visible dans
la synthèse comme dans le rapport structuré, et traçable par le `run_id`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
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
from maestro.telemetry import RunJournal, StepUsage, collect_usage

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
    livrable texte. `usage` porte le coût de la tâche (#8) : tokens, coût, durée,
    outils — durée horloge toujours mesurée, le reste selon ce que le fournisseur
    rapporte.
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
    usage: StepUsage = StepUsage()

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
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class RunReport:
    """Agrégat déterministe d'une exécution : l'objectif et le résultat de chaque tâche.

    `resultats` est dans l'**ordre d'exécution** (topologique). Le rapport n'appelle
    aucun modèle : il assemble ce que la boucle a collecté (choix « rapport structuré »
    du ticket #6).

    `run_id` relie le rapport aux lignes du journal d'exécution (#8) ;
    `planification` porte l'usage de l'étape de planification, qui s'ajoute à celui
    des tâches dans `usage_totale`.
    """

    objectif: str
    resultats: tuple[TaskResult, ...]
    run_id: str = ""
    planification: StepUsage = StepUsage()

    @property
    def reussies(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches terminées avec succès."""
        return tuple(r for r in self.resultats if r.ok)

    @property
    def echouees(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches en échec (routage ou exécution)."""
        return tuple(r for r in self.resultats if not r.ok)

    @property
    def usage_totale(self) -> StepUsage:
        """Usage agrégé de l'exécution : la planification plus toutes les tâches."""
        total = self.planification
        for r in self.resultats:
            total = total.fusion(r.usage)
        return total

    def synthese(self) -> str:
        """Rend l'agrégat en Markdown : récap chiffré puis livrable par tâche."""
        lignes = [
            f"# Synthèse — {self.objectif}",
            "",
            f"{len(self.reussies)}/{len(self.resultats)} tâche(s) réussie(s).",
            f"Usage total (planification incluse) : {self.usage_totale.resume_court()}",
            "",
        ]
        for r in self.resultats:
            # Marqueurs en texte (pas d'emoji) : portables sur une console Windows
            # héritée (cp1252) comme en UTF-8, sans faire planter l'affichage.
            etat = "[terminée]" if r.ok else "[échec]"
            competences = ", ".join(r.competences_requises)
            lignes.append(f"## {etat} {r.titre}")
            lignes.append(f"- Agent : {r.role} (`{r.agent}`) — compétences : {competences}")
            lignes.append(f"- Usage : {r.usage.resume_court()}")
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
            "run_id": self.run_id,
            "reussies": len(self.reussies),
            "total": len(self.resultats),
            "planification": self.planification.to_dict(),
            "usage_totale": self.usage_totale.to_dict(),
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

    async def run(self, objective: str, *, journal: RunJournal | None = None) -> RunReport:
        """Exécute la boucle complète pour `objective` et renvoie l'agrégat.

        Lève `ValueError` si l'objectif est vide et propage les erreurs de
        **planification** (`PlanParsingError`, `TaskValidationError`) : sans plan
        valide, il n'y a rien à orchestrer. En revanche, les échecs *par tâche*
        (routage, exécution) sont consignés, pas propagés.

        Chaque étape est consignée dans `journal` (#8) — un `RunJournal` neuf par
        défaut ; en injecter un permet d'inspecter les traces ou de fixer le
        `run_id`. Le rapport porte ce `run_id` et l'usage par tâche.
        """
        journal = journal if journal is not None else RunJournal()
        plan_usage, tasks = await self._plan(objective, journal)
        done: dict[str, TaskResult] = {}
        resultats: list[TaskResult] = []
        for task in topological_order(tasks):
            result = await self._execute(task, done, journal)
            done[task.id] = result
            resultats.append(result)
        return RunReport(
            objectif=objective,
            resultats=tuple(resultats),
            run_id=journal.run_id,
            planification=plan_usage,
        )

    async def _plan(self, objective: str, journal: RunJournal) -> tuple[StepUsage, list[Task]]:
        """Planifie l'objectif en consignant l'étape (usage et issue) dans le journal.

        Les erreurs de planification sont propagées (sans plan, rien à orchestrer)
        mais consignées d'abord : l'échec reste traçable dans le journal.
        """
        debut = perf_counter()
        with collect_usage() as recolte:
            try:
                tasks = await self._orchestrator.plan(objective)
            except Exception as exc:
                journal.consigne(
                    etape="planification",
                    nom="Planification de l'objectif",
                    agent="orchestrateur",
                    role="Orchestrateur",
                    statut=STATUT_ECHEC,
                    entree=objective,
                    sortie="",
                    erreur=str(exc),
                    usage=recolte.total.avec_duree(_ecoule_ms(debut)),
                )
                raise
        usage = recolte.total.avec_duree(_ecoule_ms(debut))
        journal.consigne(
            etape="planification",
            nom="Planification de l'objectif",
            agent="orchestrateur",
            role="Orchestrateur",
            statut=STATUT_TERMINEE,
            entree=objective,
            sortie=f"{len(tasks)} tâche(s) planifiée(s)",
            usage=usage,
        )
        return usage, tasks

    async def _execute(
        self, task: Task, done: dict[str, TaskResult], journal: RunJournal
    ) -> TaskResult:
        """Assigne, exécute et consigne une tâche ; renvoie son `TaskResult` (jamais d'exception).

        La durée horloge est chronométrée autour de l'étape complète ; tokens, coût
        et outils sont récoltés auprès du fournisseur (`collect_usage`) quand il les
        signale. Le tout est porté par le résultat et consigné au journal.
        """
        debut = perf_counter()
        entree = task.description
        with collect_usage() as recolte:
            try:
                assignment = assign(task, self._agents)
            except RoutingError as exc:
                result = _echec(task, agent="—", role="non assigné", score=0, erreur=str(exc))
            else:
                dependances = [done[dep] for dep in task.dependances if dep in done]
                entree = _build_task_description(task, dependances)
                result = await self._realise(assignment.agent, task, entree, assignment.score)
        result = replace(result, usage=recolte.total.avec_duree(_ecoule_ms(debut)))
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=result.agent,
            role=result.role,
            statut=result.statut,
            entree=entree,
            sortie=result.sortie,
            erreur=result.erreur,
            usage=result.usage,
        )
        return result

    async def _realise(
        self, agent: Agent, task: Task, description: str, score: int
    ) -> TaskResult:
        """Produit le livrable de `task` et le mue en `TaskResult` (échec consigné, jamais levé)."""
        try:
            sortie, fichiers = await self._produce(agent, task, description)
        except Exception as exc:  # exécution: on consigne l'échec sans casser la boucle
            return _echec(task, agent=agent.nom, role=agent.role, score=score, erreur=str(exc))

        sortie = sortie.strip()
        if not sortie and not fichiers:
            return _echec(
                task,
                agent=agent.nom,
                role=agent.role,
                score=score,
                erreur="réponse vide de l'agent.",
            )
        return TaskResult(
            task_id=task.id,
            titre=task.titre,
            agent=agent.nom,
            role=agent.role,
            competences_requises=task.competences_requises,
            score=score,
            statut=STATUT_TERMINEE,
            sortie=sortie,
            fichiers=fichiers,
        )

    async def _produce(
        self, agent: Agent, task: Task, description: str
    ) -> tuple[str, tuple[ProducedFile, ...]]:
        """Produit le livrable de `task` : runtime outillé si le rôle en a un, sinon texte.

        `description` est la tâche déjà enrichie du tableau noir (résultats des
        dépendances). Un rôle outillé (#35) l'exécute dans un espace isolé et renvoie
        aussi ses fichiers. Si le fournisseur ne sait pas exécuter d'agent outillé
        (`UnsupportedCapability`), le rôle retombe sur son livrable texte via
        `generate()` — même chemin que les rôles sans runtime.
        """
        runtime = self._runtimes.get(agent.nom)
        if runtime is not None:
            try:
                outcome = await runtime.execute(description, format_sortie=task.format_sortie)
                return outcome.resume, outcome.fichiers
            except UnsupportedCapability:
                pass  # fournisseur texte-seul : repli sur le livrable texte
        sortie = await self._provider.generate(
            _build_task_prompt(description, task.format_sortie),
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


def _build_task_prompt(description: str, format_sortie: str) -> str:
    """Compose le message d'un livrable *texte* : la description + le format attendu.

    Le chemin outillé n'en a pas besoin : le runtime encadre lui-même la description
    avec les consignes de son rôle (cf. `maestro.agents.runtime._build_prompt`).
    """
    lignes = [
        description,
        "",
        f"Format de sortie attendu : {format_sortie}",
        "",
        "Produis maintenant le livrable demandé.",
    ]
    return "\n".join(lignes)


def _ecoule_ms(debut: float) -> int:
    """Millisecondes écoulées depuis `debut` (un repère `perf_counter`)."""
    return int((perf_counter() - debut) * 1000)
