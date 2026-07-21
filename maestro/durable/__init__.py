"""Mode durable — le moteur d'exécution sur Temporal (ticket #95).

Fait basculer un run du process courant vers un **workflow Temporal** durable, en
mode **opt-in** : un run = un workflow, une tâche = une activité. Le moteur en
process (`maestro.engine`) reste le défaut et ne change pas ; ce module en est le
pendant durable, calqué sur `maestro.queue` (l'exécution distribuée par file).

Quatre pièces :

- `workflow` — `MaestroRunWorkflow`, le run **déterministe** (planification,
  dépendances, ordonnancement) qui délègue tout l'I/O à des activités ;
- `activities` — `planifier` / `executer_tache` / `consigner_blocage`, l'I/O du
  run (appels modèle, garde-fous #9, journal #8) ; `configurer_worker` pose la
  config du process worker ;
- `worker` — le `Worker` Temporal (`construire_worker`) et son mode autonome ;
- `engine` — `DurableEngine`, côté orchestrateur, même interface que
  `OrchestrationEngine` (`await engine.run(objectif)`), plus la **reprise sur
  panne** (`await engine.reprendre(run_id)`, #96), et `create_durable_engine`.

Usage — en démo CLI (Temporal lancé, cf. infra/docker-compose.yml) :

    maestro-run --durable "<objectif>"

ou par le code :

    from maestro.durable import create_durable_engine

    engine = create_durable_engine()
    report = await engine.run("Créer une API de gestion de tâches")

**Reprise sur panne** (#96) — le run survit au process qui le suit. Si celui-ci
disparaît en cours de route, on le rattache par son `run_id` (celui du journal,
de la trace et de la Control Tower) :

    maestro-run --reprendre <run_id>

Les tâches déjà réussies ne sont pas ré-exécutées — leur résultat vient de
l'historique Temporal : l'amont n'est pas repayé. Si c'est le **worker** qui
tombe, il n'y a rien à faire : le run reprend de lui-même dès qu'un worker
revient sur la file (`python -m maestro.durable.worker`).
"""

from __future__ import annotations

from maestro.durable.engine import (
    DurableEngine,
    create_durable_engine,
    identifiant_workflow,
)
from maestro.durable.worker import FILE_DURABLE, construire_worker
from maestro.durable.workflow import MaestroRunWorkflow

__all__ = [
    "FILE_DURABLE",
    "DurableEngine",
    "MaestroRunWorkflow",
    "construire_worker",
    "create_durable_engine",
    "identifiant_workflow",
]
