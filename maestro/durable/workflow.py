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

**Reprise sur panne** (#96) — c'est la raison d'être du mode durable : l'état du
workflow (quelles tâches ont abouti) vit sur le serveur Temporal, pas dans le
process. Un worker tué en cours de run est remplacé — le workflow **rejoue son
historique**, les activités déjà terminées rendent leur résultat *depuis
l'historique* sans être ré-exécutées : l'amont n'est jamais repayé. Deux pièces
ajoutent ce qu'il manquait pour que la reprise soit réellement automatique et
traçable :

- la requête `resultats_acquis` expose l'état acquis du run (planification et
  tâches terminées) à un process qui reprend en cours de route — c'est ce qui
  permet à `maestro.durable.engine` de reconstituer le journal, donc de
  consolider rapport et grand livre par-dessus l'interruption ;
- la politique de relance des activités ne neutralise plus tout : Temporal
  rattrape la **perte du worker** (cf. `RELANCE_PERTE_WORKER`), seul aléa que la
  relance applicative ne peut pas rattraper puisqu'elle meurt avec le process.

Les deux couches de relance composent ainsi sans se marcher dessus (exigence du
parent #92) : l'aléa **fournisseur** reste du seul ressort de la relance
applicative (#91), qui vit *dans* l'activité ; l'aléa **d'infrastructure** est du
seul ressort de Temporal. Les échecs applicatifs déterministes (plan invalide,
tâche non conforme au schéma) sont levés `non_retryable` par les activités —
aucune des deux couches ne les rejoue.
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

#: Battement de cœur attendu des activités longues (#96). C'est lui qui rend la
#: reprise **rapide** : sans battement, la mort d'un worker ne se voit qu'au
#: « start-to-close » (jusqu'à une heure pour une tâche) et le run resterait
#: figé d'autant. Les activités le tiennent via `maestro.durable.activities`.
BATTEMENT_ACTIVITE = timedelta(seconds=30)

#: Relance Temporal des activités : elle ne rattrape que ce que la relance
#: applicative (#91) ne peut pas — la **perte du worker** (process tué, battement
#: de cœur expiré). L'aléa fournisseur, lui, est relancé *dans* l'activité ; les
#: échecs applicatifs déterministes sont levés `non_retryable` par les activités,
#: donc jamais rejoués ici (#96, cf. en-tête du module).
RELANCE_PERTE_WORKER = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


@workflow.defn
class MaestroRunWorkflow:
    """Un run d'orchestration, exécuté comme workflow durable Temporal (#95).

    L'instance porte l'**état acquis** du run (planification, tâches terminées),
    reconstruit à l'identique par le rejeu de l'historique : c'est ce que la
    requête `resultats_acquis` rend consultable de l'extérieur, pour qu'un
    process qui reprend après une panne (#96) sache ce qui est déjà payé.
    """

    def __init__(self) -> None:
        self._objectif: str = ""
        self._run_id: str = ""
        self._planification: dict[str, Any] | None = None
        self._ordre: tuple[str, ...] = ()
        self._acquis: dict[str, dict[str, Any]] = {}

    @workflow.query
    def resultats_acquis(self) -> dict[str, Any]:
        """L'état déjà acquis du run : ce qui est payé et ne sera pas refait (#96).

        Même forme que l'agrégat final, mais **partielle** : `planification` est
        `None` tant que le plan n'est pas établi, et `resultats` ne porte que les
        tâches abouties, dans l'ordre du plan. Une tâche en vol au moment de
        l'interruption n'y figure pas — elle n'a rien produit et sera reprise.

        Interrogée par `maestro.durable.engine` au moment de reprendre : elle lui
        donne de quoi reconstituer le journal du run (donc consolider rapport et
        grand livre par-dessus la panne). Le rejeu de l'historique par un worker
        rebâtit cet état à l'identique, sans ré-exécuter la moindre activité :
        lire l'état acquis ne coûte rien.
        """
        return {
            "objectif": self._objectif,
            "run_id": self._run_id,
            "planification": self._planification,
            "resultats": [
                self._acquis[tache_id]
                for tache_id in self._ordre
                if tache_id in self._acquis
            ],
        }

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
        self._objectif = objectif
        self._run_id = run_id

        plan = await workflow.execute_activity(
            planifier,
            {"objectif": objectif, "run_id": run_id},
            start_to_close_timeout=TIMEOUT_PLANIFICATION,
            heartbeat_timeout=BATTEMENT_ACTIVITE,
            retry_policy=RELANCE_PERTE_WORKER,
        )
        tasks = [Task.from_dict(t) for t in plan["tasks"]]
        ordered = topological_order(tasks)
        self._planification = plan["usage"]
        self._ordre = tuple(task.id for task in ordered)

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
            resultat: dict[str, Any]
            if insatisfaites:
                # Blocage aval (#43) : la tâche n'atteint jamais l'exécution.
                resultat = await workflow.execute_activity(
                    consigner_blocage,
                    {
                        "task": task.to_dict(),
                        "dependances": insatisfaites,
                        "run_id": run_id,
                    },
                    start_to_close_timeout=TIMEOUT_ANNEXE,
                    retry_policy=RELANCE_PERTE_WORKER,
                )
            else:
                resultat = await workflow.execute_activity(
                    executer_tache,
                    {
                        "task": task.to_dict(),
                        "dependances": dependances,
                        "run_id": run_id,
                    },
                    start_to_close_timeout=TIMEOUT_TACHE,
                    heartbeat_timeout=BATTEMENT_ACTIVITE,
                    retry_policy=RELANCE_PERTE_WORKER,
                )
            # L'issue rejoint l'état acquis du run : payée, elle ne sera pas
            # refaite — c'est elle que `resultats_acquis` rendra à une reprise.
            self._acquis[task.id] = resultat
            return resultat

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
