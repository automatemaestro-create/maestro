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

**Reprise sur panne** (#96) — `reprendre(run_id)` rattache un process neuf à un
run interrompu au lieu d'en relancer un. C'est possible parce que l'identifiant
de workflow est **dérivé du `run_id`** (`identifiant_workflow`) : le `run_id`
qu'affichent journal, trace et Control Tower suffit à retrouver le run côté
Temporal. Le process qui reprend embarque un worker, et :

1. **interroge l'état acquis** du workflow (requête `resultats_acquis`) — ce qui
   est déjà payé : planification et tâches abouties ;
2. **consigne la reprise** avec sa raison et ce récapitulatif (étape `reprise`,
   donc trace et fil temps réel), puis attend l'issue du run.

Les tâches déjà réussies ne sont **jamais** ré-exécutées : leur résultat vient de
l'historique Temporal, pas d'un nouvel appel modèle — l'amont n'est pas repayé.
Seule une tâche en vol au moment de l'interruption reprend (elle n'avait rien
produit) ; c'est la relance Temporal sur perte du worker qui la redonne au worker
suivant (cf. `maestro.durable.workflow`).

**Consolidation avant/après reprise** — elle passe par l'agrégat, pas par le
journal du process qui reprend : celui-ci n'a pas vu l'avant-panne (les étapes
acquises ont été consignées par le process disparu) et ne verra pas non plus les
tâches d'après, chaque activité tenant son propre journal sur le même `run_id`.
Le rapport rendu ici porte donc tout le run, et `RunReport.grand_livre` en donne
la vue comptable consolidée — coûts, tokens, durées et statuts par tâche, de part
et d'autre de l'interruption. Côté Control Tower, le fil temps réel consolide de
lui-même : les étapes d'avant y avaient été publiées à leur heure, celles d'après
le sont à la reprise (la survie de cet état au redémarrage de l'API est l'objet
du #97).

`run()` est idempotent par le même mécanisme : rejouer un `run_id` déjà en vol
s'y **rattache** au lieu d'ouvrir un second run (`USE_EXISTING`).
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from maestro.config import Settings, load_settings
from maestro.durable.worker import FILE_DURABLE, construire_worker
from maestro.durable.workflow import MaestroRunWorkflow
from maestro.engine.executor import STATUT_TERMINEE, TaskResult
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import RunReport
from maestro.engine.retry import RELANCE_DEFAUT, PolitiqueRelance
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import RunJournal, StepUsage
from maestro.telemetry.costs import ETAPE_REPRISE

#: Préfixe de l'identifiant de workflow d'un run. Dérivé du `run_id` — donc
#: **retrouvable** : c'est ce qui permet de reprendre un run interrompu à partir
#: du seul identifiant que journal et Control Tower affichent déjà (#96).
PREFIXE_WORKFLOW = "maestro-run-"


def identifiant_workflow(run_id: str) -> str:
    """L'identifiant Temporal du run `run_id` (un run = un workflow, #95)."""
    return f"{PREFIXE_WORKFLOW}{run_id}"


class DurableEngine:
    """Moteur durable : un run devient un workflow Temporal (#95).

    S'utilise comme `OrchestrationEngine` — `await engine.run(objectif)` — et
    ajoute la reprise d'un run interrompu, `await engine.reprendre(run_id)`
    (#96). `adresse` est le serveur Temporal (défaut : config
    `TEMPORAL_ADDRESS`) ; `guardrails` (#9) et `relance` (#91) sont posés sur le
    process worker embarqué avant le run. Un `relance=None` **désactive** la
    relance applicative (traduit en une seule tentative) ; la relance Temporal,
    elle, ne couvre que la perte du worker et n'est pas concernée par ce réglage.
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
            # `USE_EXISTING` : relancer un `run_id` déjà en vol s'y rattache au
            # lieu d'ouvrir un second run — le démarrage est idempotent, et le
            # run reste unique quoi qu'il arrive au process appelant (#96).
            handle = await client.start_workflow(
                MaestroRunWorkflow.run,
                entree,
                id=identifiant_workflow(run_id),
                task_queue=self._task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            return await self._issue(handle)

    async def reprendre(
        self,
        run_id: str,
        *,
        journal: RunJournal | None = None,
        raison: str = "",
    ) -> RunReport:
        """Rattache ce process au run `run_id` interrompu et rend son rapport (#96).

        À utiliser quand le process qui suivait un run durable a disparu (CLI
        tuée, API redémarrée) alors que le run, lui, vit toujours côté Temporal.
        Ne relance rien : le workflow est retrouvé par son identifiant, son état
        acquis réintégré au journal, la reprise consignée avec sa `raison`, puis
        l'issue attendue. Les tâches déjà réussies ne sont pas ré-exécutées —
        leur résultat vient de l'historique.

        Un run **déjà terminé** se reprend aussi : son rapport est simplement
        rendu (cas du process tué après la dernière tâche, avant l'affichage).
        Lève `OrchestratorError` si aucun run durable ne porte cet identifiant.
        """
        from maestro.durable.activities import configurer_worker

        journal = journal if journal is not None else RunJournal(run_id=run_id)
        configurer_worker(guardrails=self._guardrails, relance=self._relance)

        client = await Client.connect(self._adresse)
        handle: WorkflowHandle[Any, Any] = client.get_workflow_handle(
            identifiant_workflow(run_id)
        )
        try:
            description = await handle.describe()
        except RPCError as exc:
            raise OrchestratorError(
                f"aucun run durable connu de Temporal sous l'identifiant « {run_id} » "
                f"({self._adresse}) — vérifiez le run_id, ou lancez un nouveau run."
            ) from exc

        async with construire_worker(client, task_queue=self._task_queue):
            # La requête est servie par un worker qui rejoue l'historique : le
            # worker embarqué doit donc tourner avant de la poser.
            acquis = await handle.query(MaestroRunWorkflow.resultats_acquis)
            _consigne_reprise(
                journal,
                raison=raison or _raison_par_defaut(description.status),
                acquis=acquis,
            )
            return await self._issue(handle)

    async def _issue(self, handle: WorkflowHandle[Any, Any]) -> RunReport:
        """Attend l'issue du workflow et en fait un `RunReport`.

        Partagé par le démarrage et la reprise : dans les deux cas l'agrégat
        rendu par le workflow porte **tout** le run — les tâches d'avant
        l'interruption comme celles d'après, puisqu'il est assemblé depuis
        l'historique (c'est la consolidation du rapport, #96).
        """
        try:
            agregat = await handle.result()
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


def _consigne_reprise(
    journal: RunJournal, *, raison: str, acquis: dict[str, Any]
) -> None:
    """Consigne l'étape `reprise` : le run repart, pourquoi, et ce qui est déjà payé (#96).

    C'est la **césure visible** du run : elle part sur la trace et sur le fil
    temps réel de la Control Tower, où elle dit à l'opérateur pourquoi le run
    repart et ce qu'il ne repaiera pas. Le récapitulatif est calculé depuis
    l'état acquis du workflow (planification et tâches abouties), pas depuis le
    journal de ce process — qui, lui, n'a rien vu de l'avant-panne.

    Usage nul : reprendre ne sollicite aucun modèle. Le grand livre ignore donc
    cette étape (`ETAPE_REPRISE`) et le total du run reste celui du travail réel.
    """
    recupere = StepUsage()
    etapes = 0
    planification = acquis.get("planification")
    if isinstance(planification, dict):
        recupere = recupere.fusion(StepUsage.from_dict(planification))
        etapes += 1
    resultats = acquis.get("resultats", [])
    for brut in resultats if isinstance(resultats, list) else []:
        recupere = recupere.fusion(TaskResult.from_dict(brut).usage)
        etapes += 1
    journal.consigne(
        etape=ETAPE_REPRISE,
        nom="Reprise du run interrompu",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=STATUT_TERMINEE,
        entree=raison,
        sortie=(
            f"{etapes} étape(s) déjà acquise(s), non ré-exécutées — "
            f"usage récupéré : {recupere.resume_court()}"
        ),
        usage=StepUsage(),
    )


def _raison_par_defaut(statut: Any) -> str:
    """Décrit la raison de la reprise d'après l'état du run côté Temporal.

    Sert quand l'appelant n'en fournit pas : plutôt qu'un « reprise » nu, le
    journal dit *ce qui s'est passé* — le run vivait encore sans personne pour
    le suivre, ou il s'était achevé sans que son rapport soit rendu.
    """
    nom = getattr(statut, "name", str(statut))
    if nom == "RUNNING":
        return (
            "run toujours en vol côté Temporal : le process qui le suivait a été "
            "interrompu, l'exécution reprend là où elle en était."
        )
    if nom == "COMPLETED":
        return (
            "run déjà achevé côté Temporal : seul le process qui le suivait avait "
            "été interrompu, son rapport est rendu sans rien ré-exécuter."
        )
    return f"run repris dans l'état « {nom} » côté Temporal."


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
