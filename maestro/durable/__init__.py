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
  `OrchestrationEngine` (`await engine.run(objectif)`), et `create_durable_engine`.

Usage — en démo CLI (Temporal lancé, cf. infra/docker-compose.yml) :

    maestro-run --durable "<objectif>"

ou par le code :

    from maestro.durable import create_durable_engine

    engine = create_durable_engine()
    report = await engine.run("Créer une API de gestion de tâches")
"""

from __future__ import annotations

from maestro.durable.engine import DurableEngine, create_durable_engine
from maestro.durable.worker import FILE_DURABLE, construire_worker
from maestro.durable.workflow import MaestroRunWorkflow

__all__ = [
    "FILE_DURABLE",
    "DurableEngine",
    "MaestroRunWorkflow",
    "construire_worker",
    "create_durable_engine",
]
