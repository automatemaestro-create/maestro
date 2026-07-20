"""Activités Temporal du mode durable — tout l'I/O du run (ticket #95).

Le mode durable fait d'un run un **workflow** Temporal (`maestro.durable.workflow`)
et de chaque tâche une **activité**. Le workflow doit rester **déterministe** :
appels modèle, horloge et journalisation vivent donc ici, dans les activités,
jamais dans le code du workflow. Trois activités, toutes consignant au
`RunJournal` (#8) à l'identique de la boucle en process — c'est ce qui garde la
Control Tower alimentée sans changement (événements temps réel #46, grand livre
#55) : là où l'exécution locale ou un worker de file consigne déjà, l'activité
consigne pareil.

- `planifier` — déroule l'orchestrateur (#3) et consigne l'étape `planification` ;
- `executer_tache` — exécute une tâche via le **même chemin que la boucle locale**
  (`maestro.engine.LocalExecutor`) : routage, garde-fous (#9 — plafond, time-out,
  validation humaine), runtime outillé ou appel texte, mesure d'usage (#8). Les
  garde-fous passent donc bien « par les activités » du workflow ;
- `consigner_blocage` — mue en `TaskResult` bloqué (#43) une tâche à l'aval d'un
  échec, décision prise dans le workflow mais **consignée ici** (le journal est
  de l'I/O).

Configuration **propre au process** du worker (fournisseur, garde-fous, relance),
posée par `configurer_worker` avant la consommation — même modèle que
`maestro.queue.worker` : le validateur humain (#9) n'étant pas sérialisable, il ne
peut voyager en argument d'activité ; il vit dans le process du worker (embarqué
dans celui du CLI pour la démo, cf. `maestro.durable.engine`).

Relance : la relance applicative (#91, ENF-06) reste **la seule couche qui
relance** — la politique de retry Temporal des activités est neutralisée côté
workflow (`maximum_attempts=1`). Les deux mécanismes composent ainsi proprement,
sans double relance (note technique du #95).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter
from typing import Any

from temporalio import activity

from maestro.agents import default_runtimes
from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.secrets import SecretStore
from maestro.agents.store import AgentStore, catalogue
from maestro.config import load_settings
from maestro.engine.executor import (
    STATUT_ECHEC,
    STATUT_TERMINEE,
    LocalExecutor,
    TaskResult,
    _ecoule_ms,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import _consigne_blocage
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task, validate_task
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, collect_usage

#: Fabrique du fournisseur du worker : appelée une fois par process, à la
#: construction paresseuse de l'exécuteur (comme `maestro.queue.worker`).
ProviderFactory = Callable[[], ModelProvider]


def _fabrique_configuree() -> ModelProvider:
    """Fournisseur par défaut du worker : celui que désigne la config (#69).

    Importé ici (et non en tête de module) pour garder le worker agnostique du
    fournisseur — le choix vit dans la config (`MAESTRO_PROVIDER`), comme pour
    `OrchestrationEngine.default`.
    """
    from maestro.providers.factory import provider_from_settings

    return provider_from_settings(load_settings())


_provider_factory: ProviderFactory = _fabrique_configuree
_guardrails: Guardrails | None = None
#: Relance automatique des échecs transitoires (#91, ENF-06) — armée par défaut,
#: comme sur `OrchestrationEngine.default()`. C'est la **seule** couche de relance
#: (le retry Temporal des activités est neutralisé, cf. workflow).
_relance: PolitiqueRelance = RELANCE_DEFAUT
_executor: LocalExecutor | None = None


def configurer_worker(
    *,
    provider_factory: ProviderFactory | None = None,
    guardrails: Guardrails | None = None,
    relance: PolitiqueRelance | None = None,
) -> None:
    """Configure l'exécution de CE process worker (fournisseur, garde-fous, relance).

    À appeler avant la consommation (démarrage du worker, fixture de test, ou
    `maestro.durable.engine.DurableEngine.run` pour le worker embarqué). Ne touche
    que le process courant. L'exécuteur courant est invalidé — reconstruit au
    prochain message. `relance` (#91) remplace la politique par défaut ; la
    neutraliser se fait via `PolitiqueRelance(max_tentatives=1)` (None : inchangée).
    """
    global _provider_factory, _guardrails, _relance, _executor
    if provider_factory is not None:
        _provider_factory = provider_factory
    if guardrails is not None:
        _guardrails = guardrails
    if relance is not None:
        _relance = relance
    _executor = None


def reinitialiser_worker() -> None:
    """Rétablit la configuration par défaut du worker (fabrique configurée, garde-fous défaut)."""
    global _provider_factory, _guardrails, _relance, _executor
    _provider_factory = _fabrique_configuree
    _guardrails = None
    _relance = RELANCE_DEFAUT
    _executor = None


def _executeur() -> LocalExecutor:
    """L'exécuteur du process worker, construit paresseusement au premier message.

    Câblé comme celui de la boucle locale et des workers de file (#41) : catalogue
    effectif (#72), runtimes outillés, playbooks (#78), capacité (#86), MCP (#104),
    secrets (#109) et permissions (#110) relus **à chaud** dans les dépôts de la
    config à chaque tâche — une édition publiée depuis la Control Tower vaut pour
    le message suivant, sans redémarrer le worker. Les garde-fous (#9) et la
    relance (#91) sont ceux posés par `configurer_worker`.
    """
    global _executor
    if _executor is None:
        provider = _provider_factory()
        settings = load_settings()
        _executor = LocalExecutor(
            provider,
            agents=catalogue(AgentStore.default(settings), settings.model),
            runtimes=default_runtimes(provider, model=settings.model),
            guardrails=_guardrails,
            playbooks=PlaybookStore.default(settings),
            capacites=CapacityStore.default(settings),
            mcp=McpStore.default(settings),
            secrets=SecretStore.default(settings),
            permissions=PermissionStore.default(settings),
            relance=_relance,
        )
    return _executor


@activity.defn
async def planifier(payload: dict[str, Any]) -> dict[str, Any]:
    """Active la planification : découpe l'objectif en tâches et consigne l'étape.

    Miroir de `OrchestrationEngine._plan` côté activité : l'orchestrateur (#3)
    tourne sur le fournisseur configuré du worker, l'usage est mesuré (#8) et
    l'étape `planification` consignée au journal (donc au fil temps réel de la
    Control Tower, #46). Une erreur de planification est consignée puis **levée** —
    sans plan, il n'y a rien à orchestrer : l'activité échoue et le workflow avec
    (l'orchestrateur côté client la remonte, cf. `maestro.durable.engine`).
    """
    objectif: str = payload["objectif"]
    journal = RunJournal(run_id=payload.get("run_id"))
    debut = perf_counter()
    orchestrator = _orchestrateur()
    with collect_usage() as recolte:
        try:
            tasks = await orchestrator.plan(objectif)
        except Exception as exc:
            journal.consigne(
                etape="planification",
                nom="Planification de l'objectif",
                agent="orchestrateur",
                role="Orchestrateur",
                statut=STATUT_ECHEC,
                entree=objectif,
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
        entree=objectif,
        sortie=f"{len(tasks)} tâche(s) planifiée(s)",
        usage=usage,
    )
    return {"tasks": [t.to_dict() for t in tasks], "usage": usage.to_dict()}


@activity.defn
async def executer_tache(payload: dict[str, Any]) -> dict[str, Any]:
    """Active l'exécution d'une tâche : routage, garde-fous, livrable, journal.

    Le contrat est celui de tout `TaskExecutor` : ne lève jamais pour un échec
    métier (routage, garde-fou, agent en erreur) — l'échec est un `TaskResult` au
    statut `echec`, consigné. Seul un payload non conforme au schéma partagé lève
    (tâche refusée avant toute exécution). Le tableau noir (résultats des
    dépendances) voyage **avec** le message : aucune mémoire partagée entre
    activités. L'usage porté par le résultat est le vrai coût mesuré par
    `LocalExecutor` (#8), consigné au même journal que tout le reste (#46/#55).
    """
    validate_task(payload["task"])
    task = Task.from_dict(payload["task"])
    dependances = [TaskResult.from_dict(d) for d in payload.get("dependances", ())]
    journal = RunJournal(run_id=payload.get("run_id"))
    result = await _executeur().execute(task, dependances, journal)
    # Identité de l'exécutant, rapportée sur le résultat (`TaskResult.worker`) —
    # ici la file d'activités Temporal qui a porté la tâche (critère MVP n°2).
    result = replace(result, worker=f"temporal/{activity.info().task_queue}")
    return result.to_dict()


@activity.defn
async def consigner_blocage(payload: dict[str, Any]) -> dict[str, Any]:
    """Active la consignation d'un blocage aval (#43) : décidé au workflow, tracé ici.

    Le workflow constate qu'une dépendance a échoué (ou est elle-même bloquée) et
    délègue à cette activité la construction **et** la consignation du `TaskResult`
    bloqué — car consigner au journal est de l'I/O, interdit au workflow
    déterministe. Réutilise `maestro.engine.loop._consigne_blocage` : même message
    d'erreur, mêmes champs de journal que la boucle en process, donc même
    événement côté Control Tower. Aucun agent n'est sollicité : usage nul.
    """
    task = Task.from_dict(payload["task"])
    insatisfaites = [TaskResult.from_dict(d) for d in payload.get("dependances", ())]
    journal = RunJournal(run_id=payload.get("run_id"))
    result = _consigne_blocage(task, insatisfaites, journal)
    return result.to_dict()


def _orchestrateur() -> Orchestrator:
    """Construit l'orchestrateur de la planification sur le fournisseur du worker.

    Reconstruit à chaque run (la planification est unique par run — pas de
    surcoût à le garder en cache) : le fournisseur vient de la fabrique du process
    (`configurer_worker`), le modèle de la config (`MAESTRO_MODEL`, #69) — même
    couplage config↔fabrique que l'exécuteur.
    """
    from maestro.providers.factory import default_model

    return Orchestrator(_provider_factory(), model=default_model(load_settings()))


__all__ = [
    "ProviderFactory",
    "configurer_worker",
    "consigner_blocage",
    "executer_tache",
    "planifier",
    "reinitialiser_worker",
]
