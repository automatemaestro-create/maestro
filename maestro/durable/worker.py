"""Worker Temporal du mode durable : consomme workflow et activités (ticket #95).

Assemble le `temporalio.worker.Worker` qui exécute le run durable : il enregistre
le workflow (`MaestroRunWorkflow`) et ses trois activités
(`maestro.durable.activities`) sur une **file de tâches dédiée** (`FILE_DURABLE`),
contre le serveur Temporal (`TEMPORAL_ADDRESS`, cf. infra/docker-compose.yml).

Deux usages :

- **embarqué** — `maestro.durable.engine.DurableEngine` démarre un worker
  éphémère dans le process du CLI, le temps d'un run (`maestro-run --durable`) :
  les activités partagent alors le logger `maestro.trace` du process, donc les
  handlers de trace/publication (#8/#46) et la validation console (#9) opèrent
  comme en local ;
- **autonome** — `python -m maestro.durable.worker` fait tourner un worker
  persistant (déploiement réel), à l'image de `celery -A maestro.queue worker`.

La configuration d'exécution (fournisseur, garde-fous, relance) est **propre au
process** et posée via `configurer_worker` (cf. `activities`).
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from maestro.config import load_settings
from maestro.durable.activities import consigner_blocage, executer_tache, planifier
from maestro.durable.workflow import MaestroRunWorkflow

#: File de tâches Temporal dédiée au run durable (analogue de `FILE_TACHES` côté
#: Celery) : le workflow et ses activités s'y enregistrent, le client y planifie.
FILE_DURABLE = "maestro-durable"

#: Bac à sable des workflows, avec le paquet `maestro` en **passe-droit**.
#:
#: Par défaut, le bac à sable réimporte tout module touché par le code de
#: workflow dans un espace isolé, en le remplaçant par un mandataire (« proxy »)
#: qui surveille les accès non déterministes. Nos modules définissent des classes
#: qui en héritent d'autres (fournisseurs, exécuteurs) : réimportés ainsi, ils
#: sous-classent un mandataire, ce que le bac à sable ne sait pas faire
#: (« Using subclasses of proxied objects is unsupported ») — la construction du
#: worker échouait alors, quel que soit le run.
#:
#: Le passe-droit lève l'ambiguïté : le code Maestro est importé **une seule
#: fois**, normalement. Le déterminisme du workflow n'y perd rien — il ne tient
#: pas au bac à sable mais à la discipline du module `workflow` (aucun I/O, aucune
#: horloge : tout passe par des activités). Recommandation Temporal pour les
#: modules de premier rang, dont on maîtrise le contenu.
BAC_A_SABLE = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("maestro")
)


def construire_worker(client: Client, *, task_queue: str = FILE_DURABLE) -> Worker:
    """Construit le worker enregistrant le run durable sur `task_queue`.

    Le `Worker` est un gestionnaire de contexte asynchrone : `async with` le
    démarre puis l'éteint proprement — c'est ainsi que l'`engine` l'embarque le
    temps d'un run.
    """
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[MaestroRunWorkflow],
        activities=[planifier, executer_tache, consigner_blocage],
        workflow_runner=BAC_A_SABLE,
    )


async def _servir(adresse: str, task_queue: str) -> None:
    """Connexion au serveur puis service indéfini du worker (mode autonome)."""
    client = await Client.connect(adresse)
    async with construire_worker(client, task_queue=task_queue):
        print(
            f"[OK  ] Worker durable en écoute sur « {task_queue} » "
            f"(Temporal {adresse}). Ctrl-C pour arrêter."
        )
        # Sert jusqu'à interruption : un Event jamais signalé tient la coroutine.
        await asyncio.Event().wait()


def main() -> int:  # pragma: no cover - worker persistant, exige un serveur vivant
    """Point d'entrée du worker autonome : sert jusqu'à interruption.

    Sort en code 0 sur arrêt clavier, 1 si le serveur Temporal est injoignable.
    """
    settings = load_settings()
    adresse = settings.temporal_address
    try:
        asyncio.run(_servir(adresse, FILE_DURABLE))
    except KeyboardInterrupt:
        print("\n[OK  ] Worker durable arrêté.")
        return 0
    except (RPCError, RuntimeError) as exc:
        print(f"[FAIL] Temporal injoignable sur {adresse} ({exc}).")
        print("Démarrez-le : docker compose -f infra/docker-compose.yml up -d temporal")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
