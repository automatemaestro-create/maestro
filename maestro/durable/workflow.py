"""Le workflow durable : un run = un workflow Temporal (ticket #95).

`MaestroRunWorkflow` **est** l'exécution d'un run, portée par Temporal : son état
(quelles tâches ont abouti) est persisté par le serveur, socle de la reprise sur
panne (#96). Le workflow **orchestre** — planification, résolution des
dépendances, ordonnancement, blocage aval (#43) — mais délègue **tout l'I/O** à
des activités (`maestro.durable.activities`) : appels modèle, horloge et
journalisation vivent hors du workflow, condition du **déterminisme** que Temporal
exige du code de workflow (une même séquence d'événements doit rejouer à
l'identique).

La boucle reproduit celle en process (`maestro.engine.loop.OrchestrationEngine`) :

1. la tâche `planifier` découpe l'objectif (un run = un plan) ;
2. le workflow ordonne les tâches (tri topologique déterministe) et lance chacune
   dès que **ses** dépendances sont résolues — les tâches indépendantes partent
   donc en parallèle (autant d'activités concurrentes) ;
3. une dépendance en échec **bloque** la tâche : elle n'atteint jamais l'activité
   d'exécution, son blocage est consigné (activité `consigner_blocage`) et se
   propage en cascade ;
4. les résultats sont rassemblés dans l'ordre topologique du plan (déterministe,
   indépendant de l'ordre d'achèvement).

Retry Temporal **neutralisé** (`maximum_attempts=1`) : la relance des échecs
transitoires (#91) est portée par la couche applicative dans l'activité
d'exécution — une seule couche relance, elles composent proprement (note
technique du #95).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

# Le code métier importé ici est **déterministe** (formes de données et tri
# topologique pur) ou n'est référencé qu'au titre d'activité : on le laisse
# traverser le bac à sable du workflow sans réimport (recommandation Temporal).
with workflow.unsafe.imports_passed_through():
    from maestro.durable.activities import (
        consigner_blocage,
        executer_tache,
        planifier,
    )
    from maestro.engine.executor import STATUT_TERMINEE
    from maestro.orchestrator.schema import Task, topological_order

#: Délais « start-to-close » des activités. Larges pour une tâche d'agent (appels
#: modèle et relances comprises) ; courts pour les étapes sans modèle.
TIMEOUT_PLANIFICATION = timedelta(minutes=30)
TIMEOUT_TACHE = timedelta(hours=1)
TIMEOUT_ANNEXE = timedelta(seconds=30)

#: Retry Temporal désactivé : la relance applicative (#91) est la seule couche.
SANS_RELANCE_TEMPORAL = RetryPolicy(maximum_attempts=1)


@workflow.defn
class MaestroRunWorkflow:
    """Un run d'orchestration, exécuté comme workflow durable Temporal (#95)."""

    @workflow.run
    async def run(self, entree: dict[str, Any]) -> dict[str, Any]:
        """Déroule le run et renvoie l'agrégat sérialisé (reconstruit en `RunReport`).

        `entree` porte l'objectif, le `run_id` (relie les journaux des activités à
        l'exécution) et les plafonds armés — repris tels quels dans l'agrégat pour
        que le rapport dise quel contrôle de dépense a tenu (#113). Le résultat est
        un dict JSON-sérialisable : `maestro.durable.engine` en refait un
        `RunReport`.
        """
        objectif: str = entree["objectif"]
        run_id: str = entree["run_id"]

        plan = await workflow.execute_activity(
            planifier,
            {"objectif": objectif, "run_id": run_id},
            start_to_close_timeout=TIMEOUT_PLANIFICATION,
            retry_policy=SANS_RELANCE_TEMPORAL,
        )
        tasks = [Task.from_dict(t) for t in plan["tasks"]]
        ordered = topological_order(tasks)

        # Une activité asyncio par tâche, créées dans l'ordre topologique : les
        # dépendances existent donc toujours avant celles qui les attendent (même
        # invariant que la boucle en process). Temporal fournit une boucle asyncio
        # déterministe : `ensure_future`/`gather` y rejouent à l'identique.
        en_vol: dict[str, asyncio.Task[dict[str, Any]]] = {}

        async def _des_que_prete(task: Task) -> dict[str, Any]:
            dependances = [await en_vol[dep] for dep in task.dependances]
            insatisfaites = [
                dep for dep in dependances if dep["statut"] != STATUT_TERMINEE
            ]
            if insatisfaites:
                # Blocage aval (#43) : la tâche n'atteint jamais l'exécution.
                return await workflow.execute_activity(
                    consigner_blocage,
                    {
                        "task": task.to_dict(),
                        "dependances": insatisfaites,
                        "run_id": run_id,
                    },
                    start_to_close_timeout=TIMEOUT_ANNEXE,
                    retry_policy=SANS_RELANCE_TEMPORAL,
                )
            return await workflow.execute_activity(
                executer_tache,
                {
                    "task": task.to_dict(),
                    "dependances": dependances,
                    "run_id": run_id,
                },
                start_to_close_timeout=TIMEOUT_TACHE,
                retry_policy=SANS_RELANCE_TEMPORAL,
            )

        for task in ordered:
            en_vol[task.id] = asyncio.ensure_future(_des_que_prete(task))
        await asyncio.gather(*en_vol.values())

        # Rassemblement déterministe : l'ordre du plan, pas l'ordre d'achèvement.
        return {
            "objectif": objectif,
            "run_id": run_id,
            "resultats": [en_vol[task.id].result() for task in ordered],
            "planification": plan["usage"],
            "plafond_cout_usd": entree.get("plafond_cout_usd"),
            "plafond_tokens": entree.get("plafond_tokens"),
        }


__all__ = ["MaestroRunWorkflow"]
