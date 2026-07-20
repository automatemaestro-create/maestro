"""Côté orchestrateur du mode durable : lance le workflow, rend le rapport (#95).

`DurableEngine` présente **la même interface** que `OrchestrationEngine`
(`await engine.run(objectif, journal=...) -> RunReport`) : la démo CLI
(`maestro-run --durable`) et tout appelant l'utilisent sans rien changer. Sous le
capot, au lieu de dérouler la boucle en process, il :

1. configure le process worker (garde-fous, relance) — le validateur humain (#9)
   vit dans le process, non sérialisable, donc câblé ici et lu par les activités ;
2. connecte le client Temporal (`TEMPORAL_ADDRESS`) et **embarque un worker**
   éphémère (`maestro.durable.worker`) le temps du run — les activités partagent
   alors le logger `maestro.trace` du process, d'où trace/publication (#8/#46) et
   validation console opérants comme en local ;
3. exécute `MaestroRunWorkflow` (un run = un workflow) et reconstruit le
   `RunReport` à partir de l'agrégat sérialisé qu'il renvoie.

Le `journal` passé sert de porteur du `run_id` (continuité des traces) : en mode
durable, les étapes sont consignées par les **activités** (chacune son
`RunJournal` sur le même `run_id`), pas par ce journal — le fil d'événements et le
grand livre de la Control Tower s'alimentent donc depuis les activités, à
l'identique du mode en process (critère #95).

Une erreur de **planification** (plan invalide) fait échouer le workflow ; elle
est retraduite en `OrchestratorError` pour que la CLI la présente comme en local
(sans plan, rien à orchestrer).
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client, WorkflowFailureError

from maestro.config import Settings, load_settings
from maestro.durable.worker import FILE_DURABLE, construire_worker
from maestro.durable.workflow import MaestroRunWorkflow
from maestro.engine.executor import TaskResult
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import RunReport
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import RunJournal, StepUsage


class DurableEngine:
    """Moteur durable : un run devient un workflow Temporal (#95).

    S'utilise comme `OrchestrationEngine` — `await engine.run(objectif)`. `adresse`
    est le serveur Temporal (défaut : config `TEMPORAL_ADDRESS`) ; `guardrails`
    (#9) et `relance` (#91) sont posés sur le process worker embarqué avant le run.
    Un `relance=None` **désactive** la relance applicative (traduit en une seule
    tentative), là où la couche Temporal ne relance jamais.
    """

    def __init__(
        self,
        *,
        adresse: str | None = None,
        task_queue: str = FILE_DURABLE,
        guardrails: Guardrails | None = None,
        relance: PolitiqueRelance | None = RELANCE_DEFAUT,
    ) -> None:
        self._adresse = adresse or load_settings().temporal_address
        self._task_queue = task_queue
        self._guardrails = guardrails if guardrails is not None else Guardrails()
        # None ⇒ relance applicative désactivée : une seule tentative (Temporal ne
        # relance pas non plus). Sinon la politique demandée pilote la relance (#91).
        self._relance = relance if relance is not None else PolitiqueRelance(max_tentatives=1)

    async def run(self, objective: str, *, journal: RunJournal | None = None) -> RunReport:
        """Exécute le run via Temporal et renvoie l'agrégat (`RunReport`).

        `journal` fixe le `run_id` (continuité des traces) ; ses étapes, en mode
        durable, sont produites par les activités. Lève `OrchestratorError` si la
        planification échoue (workflow en échec retraduit).
        """
        # Import local : la couche activités (fournisseur, SDK) reste hors du
        # chemin d'import du module — comme la file Celery côté queue.
        from maestro.durable.activities import configurer_worker

        journal = journal if journal is not None else RunJournal()
        run_id = journal.run_id
        configurer_worker(guardrails=self._guardrails, relance=self._relance)

        client = await Client.connect(self._adresse)
        entree = {
            "objectif": objective,
            "run_id": run_id,
            "plafond_cout_usd": self._guardrails.plafond_cout_usd,
            "plafond_tokens": self._guardrails.plafond_tokens,
        }
        async with construire_worker(client, task_queue=self._task_queue):
            try:
                agregat = await client.execute_workflow(
                    MaestroRunWorkflow.run,
                    entree,
                    id=f"maestro-run-{run_id}",
                    task_queue=self._task_queue,
                )
            except WorkflowFailureError as exc:
                # Échec de planification (plan invalide) : sans plan, rien à
                # orchestrer — retraduit comme la boucle en process le propage.
                raise OrchestratorError(_cause_lisible(exc)) from exc
        return _rapport_depuis_agregat(agregat)


def create_durable_engine(
    settings: Settings | None = None,
    *,
    guardrails: Guardrails | None = None,
    relance: PolitiqueRelance | None = RELANCE_DEFAUT,
) -> DurableEngine:
    """Moteur durable branché sur la config : le pendant de `OrchestrationEngine.default`.

    Le fournisseur (planification et exécution) reste désigné par la config
    (`MAESTRO_PROVIDER`, #69), résolu **côté worker** par les activités ; l'adresse
    Temporal vient de `TEMPORAL_ADDRESS`.
    """
    settings = settings or load_settings()
    return DurableEngine(
        adresse=settings.temporal_address, guardrails=guardrails, relance=relance
    )


def _rapport_depuis_agregat(agregat: dict[str, Any]) -> RunReport:
    """Reconstruit le `RunReport` depuis l'agrégat sérialisé du workflow."""
    resultats_brut = agregat.get("resultats", [])
    resultats = resultats_brut if isinstance(resultats_brut, list) else []
    planification = agregat.get("planification", {})
    return RunReport(
        objectif=str(agregat.get("objectif", "")),
        resultats=tuple(TaskResult.from_dict(r) for r in resultats),
        run_id=str(agregat.get("run_id", "")),
        planification=StepUsage.from_dict(
            planification if isinstance(planification, dict) else {}
        ),
        plafond_cout_usd=_opt_float(agregat.get("plafond_cout_usd")),
        plafond_tokens=_opt_int(agregat.get("plafond_tokens")),
    )


def _cause_lisible(exc: WorkflowFailureError) -> str:
    """Extrait le message racine d'un échec de workflow (chaîne de causes Temporal)."""
    cause: BaseException | None = exc
    message = str(exc)
    while cause is not None:
        message = str(cause) or message
        cause = cause.__cause__
    return message


def _opt_float(valeur: object) -> float | None:
    """Coule une valeur d'agrégat en `float | None` (plafond de coût)."""
    return float(valeur) if isinstance(valeur, int | float) else None


def _opt_int(valeur: object) -> int | None:
    """Coule une valeur d'agrégat en `int | None` (plafond de tokens)."""
    return int(valeur) if isinstance(valeur, int) else None


__all__ = ["DurableEngine", "create_durable_engine"]
