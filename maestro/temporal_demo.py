"""Démo Temporal hello-world : workflow + worker contre le Temporal local (ticket #94).

Exécuter avec :  python -m maestro.temporal_demo   (ou : maestro-temporal-demo)

Pré-requis : le serveur Temporal de développement tourne (voir infra/README.md) :
    docker compose -f infra/docker-compose.yml up -d temporal

Le script démarre un worker éphémère sur la file `maestro-demo`, lance le workflow
`SaluerWorkflow` (qui délègue à l'activité `saluer`) et imprime le résultat.
L'exécution est ensuite visible dans l'UI web (http://localhost:8233). Sort en
code 0 si le workflow aboutit, 1 si le serveur est injoignable.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.worker import Worker

# Adresse gRPC du serveur, surchargée par TEMPORAL_ADDRESS (cf. .env.example).
ADRESSE_DEFAUT = "localhost:7233"
FILE_DEMO = "maestro-demo"


@activity.defn
async def saluer(nom: str) -> str:
    """Activité de démonstration : compose la salutation."""
    return f"Bonjour, {nom} !"


@workflow.defn
class SaluerWorkflow:
    """Workflow de démonstration : délègue la salutation à l'activité `saluer`."""

    @workflow.run
    async def run(self, nom: str) -> str:
        return await workflow.execute_activity(
            saluer, nom, start_to_close_timeout=timedelta(seconds=10)
        )


async def _executer(adresse: str) -> str:
    client = await Client.connect(adresse)
    # Worker éphémère : il ne vit que le temps de la démo, sur sa propre file.
    async with Worker(
        client, task_queue=FILE_DEMO, workflows=[SaluerWorkflow], activities=[saluer]
    ):
        return await client.execute_workflow(
            SaluerWorkflow.run,
            "Maestro",
            id="maestro-temporal-hello",
            task_queue=FILE_DEMO,
        )


def main() -> int:
    adresse = os.environ.get("TEMPORAL_ADDRESS") or ADRESSE_DEFAUT
    try:
        resultat = asyncio.run(_executer(adresse))
    # Serveur injoignable : le SDK lève RPCError, mais son bridge Rust peut aussi
    # remonter l'échec de connexion en RuntimeError (« Failed client connect »).
    except (RPCError, RuntimeError) as exc:
        print(f"[FAIL] Temporal injoignable sur {adresse} ({exc}).")
        print("Démarrez-le d'abord : docker compose -f infra/docker-compose.yml up -d temporal")
        return 1
    print(f"[OK  ] Workflow terminé — résultat : {resultat}")
    print("UI web : http://localhost:8233 (exécution « maestro-temporal-hello »)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
