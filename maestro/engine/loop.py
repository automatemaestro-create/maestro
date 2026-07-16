"""Moteur d'orchestration : la boucle objectif → tâches → agents → agrégat (ticket #6).

Assemble les briques du POC en une **boucle d'orchestration** :

1. l'orchestrateur (#3) découpe l'objectif en tâches validées ;
2. le routeur (#6) assigne chaque tâche à l'agent le plus compétent ;
3. le moteur exécute les tâches en respectant les dépendances, **en parallèle dès
   qu'elles sont indépendantes** (#7) : chaque tâche démarre sitôt ses dépendances
   résolues, chaque agent produisant son livrable via la couche fournisseur
   (`ModelProvider`) et recevant les résultats des tâches dont il dépend (tableau
   noir léger) ;
4. les résultats sont **agrégés** en un `RunReport` (rapport structuré déterministe).

L'exécution d'une tâche (routage, garde-fous #9, production du livrable — runtime
outillé #35 ou texte —, mesure d'usage #8) vit dans `maestro.engine.executor` : la
boucle la **délègue** à un `TaskExecutor` injectable. Par défaut c'est le
`LocalExecutor` (en process, comportement historique) ; la file de tâches (#41,
`maestro.queue.CeleryExecutor`) s'injecte à sa place pour distribuer les tâches à
des **workers séparés** via Celery + Redis — la boucle (dépendances, parallélisme,
agrégation, journal) ne change pas.

L'exécution parallèle (#7) n'introduit aucun état partagé entre tâches : chaque
exécution ne reçoit que les résultats de **ses** dépendances, la mesure d'usage (#8)
est isolée par le contexte (`contextvars`, copié par tâche asyncio — pas de fuite
entre agents), et chaque exécution outillée ouvre son propre espace de travail
jetable (`maestro.sandbox`). Le rassemblement reste déterministe : les résultats du
rapport suivent l'ordre topologique du plan, pas l'ordre d'achèvement.
`max_parallele` plafonne au besoin le nombre d'exécutions simultanées (illimité par
défaut — les plans du POC sont petits).

Le moteur ne dépend que de `ModelProvider` : il reste **agnostique du fournisseur**.
`OrchestrationEngine.default` résout fournisseur et modèle depuis la config
(`MAESTRO_PROVIDER`/`MAESTRO_MODEL`, #69), comme `Orchestrator.default`.

La boucle est **résiliente** : un échec de routage ou d'exécution est consigné dans
le résultat de la tâche (`statut = "echec"`) et n'interrompt pas les tâches
indépendantes. En revanche, les tâches **aval** d'un échec sont **bloquées** (#43) :
statut explicite `bloquee`, jamais transmises à l'exécuteur (donc jamais mises en
file), blocage propagé en cascade — pas d'exécution orpheline. Le rapport agrège
réussites, échecs et blocages.

Chaque étape (planification comprise) est **journalisée et mesurée** (#8) : durée
horloge chronométrée, tokens/coût/outils récoltés auprès du fournisseur via
`maestro.telemetry.collect_usage`, le tout consigné dans un `RunJournal` (une ligne
JSON par étape) et porté par les `TaskResult` — le coût par tâche est visible dans
la synthèse comme dans le rapport structuré, et traçable par le `run_id`.

Avec une **messagerie inter-agents** injectée (#44, `mailbox=`), le relais entre
tâches dépendantes devient un **handoff observable** (critère MVP n°7) : l'agent
qui termine une tâche à dépendants **annonce** l'issue par message (diffusion,
`maestro.messaging`), et chaque tâche aval n'est transmise à l'exécuteur qu'à
**réception** du message de ses dépendances. L'échange est journalisé (#8) et
visible dans le flux d'événements de la Control Tower (#46). Sans messagerie
(défaut), la synchronisation reste purement en process — comportement historique.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.capacity import CapacityStore
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.runtime import AgentRuntime
from maestro.agents.store import AgentStore, catalogue
from maestro.config import Settings, load_settings
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskExecutor,
    TaskResult,
    _ecoule_ms,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.messaging.handoff import HandoffRelais
from maestro.messaging.mailbox import Mailbox
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task, topological_order
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage, collect_usage

__all__ = [
    "STATUT_BLOQUEE",
    "STATUT_ECHEC",
    "STATUT_TERMINEE",
    "OrchestrationEngine",
    "RunReport",
    "TaskResult",
]


@dataclass(frozen=True)
class RunReport:
    """Agrégat déterministe d'une exécution : l'objectif et le résultat de chaque tâche.

    `resultats` est dans l'**ordre topologique du plan** — un ordre déterministe,
    indépendant de l'ordre d'achèvement des tâches exécutées en parallèle (#7). Le
    rapport n'appelle aucun modèle : il assemble ce que la boucle a collecté (choix
    « rapport structuré » du ticket #6).

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
        return tuple(r for r in self.resultats if r.statut == STATUT_ECHEC)

    @property
    def bloquees(self) -> tuple[TaskResult, ...]:
        """Sous-ensemble des tâches bloquées par une dépendance en échec (#43).

        Jamais exécutées ni mises en file : leur `erreur` cite les dépendances non
        satisfaites. Toujours vide si `echouees` l'est (un blocage a forcément un
        échec en amont).
        """
        return tuple(r for r in self.resultats if r.statut == STATUT_BLOQUEE)

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
            if r.ok:
                etat = "[terminée]"
            elif r.statut == STATUT_BLOQUEE:
                etat = "[bloquée]"
            else:
                etat = "[échec]"
            competences = ", ".join(r.competences_requises)
            lignes.append(f"## {etat} {r.titre}")
            lignes.append(f"- Agent : {r.role} (`{r.agent}`) — compétences : {competences}")
            if r.worker:
                lignes.append(f"- Worker : `{r.worker}`")
            if r.playbook_version is not None:
                lignes.append(f"- Playbook : v{r.playbook_version}")
            lignes.append(f"- Usage : {r.usage.resume_court()}")
            if r.ok:
                lignes.extend(["", r.sortie, ""])
                if r.fichiers:
                    lignes.append(f"Fichiers produits ({len(r.fichiers)}) :")
                    lignes.extend(f"- `{f.chemin}`" for f in r.fichiers)
                    lignes.append("")
            elif r.statut == STATUT_BLOQUEE:
                lignes.extend([f"- Bloquée : {r.erreur}", ""])
            else:
                lignes.extend([f"- Échec : {r.erreur}", ""])
        return "\n".join(lignes).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Réémet le rapport en dict JSON-sérialisable."""
        return {
            "objectif": self.objectif,
            "run_id": self.run_id,
            "reussies": len(self.reussies),
            "bloquees": len(self.bloquees),
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
        max_parallele: int | None = None,
        guardrails: Guardrails | None = None,
        executor: TaskExecutor | None = None,
        mailbox: Mailbox | None = None,
        playbooks: PlaybookStore | None = None,
        capacites: CapacityStore | None = None,
        relance: PolitiqueRelance | None = None,
    ) -> None:
        if max_parallele is not None and max_parallele < 1:
            raise ValueError(f"max_parallele doit être ≥ 1 (reçu : {max_parallele}).")
        self._orchestrator = orchestrator
        # Plafond d'exécutions simultanées (#7) — None : illimité. Utile pour ménager
        # les limites de débit d'un fournisseur sur un plan très large.
        self._max_parallele = max_parallele
        # Messagerie inter-agents (#44) — None : pas de handoff par message, la
        # synchronisation des dépendances reste purement en process.
        self._mailbox = mailbox
        # Frontière d'exécution (#41) : en process par défaut ; un exécuteur injecté
        # (ex. `maestro.queue.CeleryExecutor`) distribue les tâches à des workers.
        # `playbooks` (#78) et `capacites` (#86) : les dépôts que l'exécuteur local
        # relit à chaque tâche — l'application à chaud ; ignorés si un exécuteur est
        # injecté (en distribué, chaque worker câble les siens — `relance` (#91)
        # comprise, cf. maestro.queue.worker.configurer_worker).
        self._executor = (
            executor
            if executor is not None
            else LocalExecutor(
                provider,
                agents=agents,
                runtimes=runtimes,
                guardrails=guardrails,
                playbooks=playbooks,
                capacites=capacites,
                relance=relance,
            )
        )

    @classmethod
    def default(
        cls,
        settings: Settings | None = None,
        *,
        guardrails: Guardrails | None = None,
        mailbox: Mailbox | None = None,
        relance: PolitiqueRelance | None = RELANCE_DEFAUT,
        max_parallele: int | None = None,
    ) -> OrchestrationEngine:
        """Moteur par défaut : fournisseur et modèle issus de la config (#69).

        Importe la fabrique ici (et non en tête de module) pour ne pas lier le
        moteur agnostique à un fournisseur concret : le choix vit dans la config
        (`MAESTRO_PROVIDER`). `MAESTRO_MODEL`, s'il est renseigné, bascule d'un même
        geste l'orchestrateur, le catalogue d'exécutants et les runtimes outillés.

        Les prompts système des exécutants sont chargés depuis le **stockage
        versionné des playbooks** (#76) et appliqués **à chaud** (#78) : le dépôt
        est passé à l'exécuteur, qui relit la version courante à chaque tâche —
        une édition publiée depuis la Control Tower vaut pour l'exécution
        suivante, sans reconstruire le moteur ni redémarrer le process. Un agent
        jamais édité garde son prompt du code.

        Le catalogue d'exécutants est le catalogue **effectif** (#72) : les
        agents par défaut plus les agents personnalisés persistés
        (`MAESTRO_AGENTS_DIR`, sinon `core/agents/`), chargés ici, à la
        construction du moteur — un agent créé ensuite vaut pour les moteurs
        construits après lui. Sans runtime outillé, un agent personnalisé
        produit son livrable par le chemin texte, cadré par son playbook.

        Le **contrôle de capacité** (#86, EF-21) est branché sur le dépôt
        configuré (`MAESTRO_CAPACITE_DIR`, sinon `core/capacite/`), relu à
        chaud à chaque tâche : un agent désactivé depuis la Control Tower ne
        reçoit plus de tâches, et ses exécutions simultanées sont bornées à
        son plafond d'instances.

        La **relance automatique** (#91, ENF-06) est **armée par défaut**
        (`PolitiqueRelance()` : 3 tentatives, backoff exponentiel) : sur ce
        moteur — celui des vrais runs —, un aléa fournisseur transitoire ne
        condamne plus l'exécution. `relance=None` la désactive ;
        `relance=PolitiqueRelance(...)` l'ajuste.

        `max_parallele` (#100) pose le **plafond global** du run — le plafond
        transverse, prioritaire sur la capacité par agent (#86) qui s'applique
        en dessous : quel que soit le nombre d'instances accordé à un agent,
        jamais plus de `max_parallele` tâches en vol toutes files confondues.
        None (défaut) : illimité, comportement historique.
        """
        from maestro.providers.factory import default_model, provider_from_settings

        settings = settings or load_settings()
        provider = provider_from_settings(settings)
        orchestrator = Orchestrator(provider, model=default_model(settings))
        return cls(
            provider,
            orchestrator,
            agents=catalogue(AgentStore.default(settings), settings.model),
            runtimes=default_runtimes(provider, model=settings.model),
            guardrails=guardrails,
            mailbox=mailbox,
            playbooks=PlaybookStore.default(settings),
            capacites=CapacityStore.default(settings),
            relance=relance,
            max_parallele=max_parallele,
        )

    async def run(self, objective: str, *, journal: RunJournal | None = None) -> RunReport:
        """Exécute la boucle complète pour `objective` et renvoie l'agrégat.

        Lève `ValueError` si l'objectif est vide et propage les erreurs de
        **planification** (`PlanParsingError`, `TaskValidationError`) : sans plan
        valide, il n'y a rien à orchestrer. En revanche, les échecs *par tâche*
        (routage, exécution) sont consignés, pas propagés.

        Les tâches **indépendantes s'exécutent en parallèle** (#7) : chacune reçoit
        sa propre tâche asyncio et n'est transmise à l'exécuteur (donc mise en file,
        en mode distribué) que lorsque **toutes** ses dépendances sont terminées
        avec succès (#43). Une dépendance en échec (ou elle-même bloquée) **bloque**
        la tâche : statut explicite `bloquee`, consigné au journal, sans aucune
        exécution ni mise en file — le blocage se propage ainsi en cascade sur tout
        l'aval. Les résultats sont rassemblés dans l'ordre topologique du plan,
        déterministe quel que soit l'ordre d'achèvement.

        Chaque étape est consignée dans `journal` (#8) — un `RunJournal` neuf par
        défaut ; en injecter un permet d'inspecter les traces ou de fixer le
        `run_id`. Le rapport porte ce `run_id` et l'usage par tâche. Les traces des
        tâches parallèles y apparaissent dans l'ordre d'achèvement.

        Avec une messagerie injectée (#44), le passage de relais est **observable** :
        l'issue de chaque tâche à dépendants est annoncée par message (handoff ou
        notification, journalisé), et la tâche aval attend ce message avant de
        démarrer — la synchronisation en process (#43) reste le filet de sécurité
        (une annonce perdue ne suspend pas l'exécution au-delà du time-out).
        """
        journal = journal if journal is not None else RunJournal()
        plan_usage, tasks = await self._plan(objective, journal)
        ordered = topological_order(tasks)
        dependants = _dependants_directs(ordered)
        # Boîte de diffusion ouverte avant toute exécution (pub/sub sans rejeu :
        # aucune annonce ne peut être manquée) — None sans messagerie (#44).
        relais = (
            await HandoffRelais.ouvrir(self._mailbox, journal)
            if self._mailbox is not None
            else None
        )
        # Sémaphore créé ici (et pas au constructeur) : lié à la boucle asyncio de
        # cette exécution, il ne survit pas d'un `run` à l'autre.
        semaphore = (
            asyncio.Semaphore(self._max_parallele) if self._max_parallele else None
        )
        en_vol: dict[str, asyncio.Task[TaskResult]] = {}

        async def _des_que_prete(task: Task) -> TaskResult:
            # Attend ses seules dépendances : chaque exécution ne voit que le
            # tableau noir qui la concerne, aucun état partagé entre tâches. Le
            # sémaphore n'est pris qu'une fois les dépendances résolues, pour ne
            # pas occuper un créneau à attendre (ni s'interbloquer).
            dependances = [await en_vol[dep] for dep in task.dependances]
            if relais is not None:
                # Handoff (#44) : la tâche ne démarre qu'une fois le message de
                # chacune de ses dépendances relevé dans la boîte aux lettres.
                for dep in task.dependances:
                    await relais.attend(dep)
            insatisfaites = [dep for dep in dependances if not dep.ok]
            if insatisfaites:
                # Blocage aval (#43) : la tâche n'atteint jamais l'exécuteur — ni
                # exécution ni mise en file — et le blocage cascade sur l'aval.
                result = _consigne_blocage(task, insatisfaites, journal)
            elif semaphore is None:
                result = await self._executor.execute(task, dependances, journal)
            else:
                async with semaphore:
                    result = await self._executor.execute(task, dependances, journal)
            if relais is not None and dependants[task.id]:
                # L'agent qui termine annonce l'issue à l'aval (handoff ou
                # notification) — publication journalisée, résiliente.
                await relais.annonce(task, result, dependants[task.id])
            return result

        try:
            # Créées dans l'ordre topologique, les tâches asyncio des dépendances
            # existent toujours avant celles qui les attendent. `execute` ne levant
            # jamais, le TaskGroup ne se déclenche que sur un bug interne.
            async with asyncio.TaskGroup() as tg:
                for task in ordered:
                    en_vol[task.id] = tg.create_task(_des_que_prete(task))
        finally:
            if relais is not None:
                await relais.fermer()

        return RunReport(
            objectif=objective,
            resultats=tuple(en_vol[task.id].result() for task in ordered),
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


def _dependants_directs(tasks: Sequence[Task]) -> dict[str, list[str]]:
    """Inverse le graphe de dépendances : pour chaque tâche, qui dépend d'elle.

    C'est le carnet d'adresses du handoff (#44) : une tâche sans dépendant n'a
    personne à qui passer la main (aucune annonce), une tâche à dépendants
    annonce son issue pour les débloquer. Les ids sont dans l'ordre du plan.
    """
    dependants: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dep in task.dependances:
            dependants[dep].append(task.id)
    return dependants


def _consigne_blocage(
    task: Task, insatisfaites: Sequence[TaskResult], journal: RunJournal
) -> TaskResult:
    """Construit et consigne le résultat `bloquee` d'une tâche à l'aval d'un échec (#43).

    `insatisfaites` porte les résultats non réussis des dépendances de `task`
    (échec direct, ou blocage hérité en cascade) : l'erreur les cite pour rendre la
    cause traçable dans le rapport comme au journal. Aucun agent n'a été sollicité
    ni aucun message mis en file — l'usage est nul.
    """
    causes = ", ".join(f"{dep.task_id} ({dep.statut})" for dep in insatisfaites)
    result = TaskResult(
        task_id=task.id,
        titre=task.titre,
        agent="—",
        role="non exécutée",
        competences_requises=task.competences_requises,
        score=0,
        statut=STATUT_BLOQUEE,
        sortie="",
        erreur=(
            f"dépendance(s) non satisfaite(s) : {causes} — tâche bloquée, "
            "jamais exécutée ni mise en file."
        ),
    )
    journal.consigne(
        etape=task.id,
        nom=task.titre,
        agent=result.agent,
        role=result.role,
        statut=result.statut,
        entree=task.description,
        sortie="",
        erreur=result.erreur,
        usage=StepUsage(),
    )
    return result
