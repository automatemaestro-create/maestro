"""Control Tower de démonstration : l'app réelle sur bus mémoire + scénario factice (ticket #65).

Sert l'API Control Tower **réelle** (`create_app` — mêmes endpoints REST et
WebSocket que la production) sur un `InMemoryEventBus`, sans Redis ni Docker,
et publie depuis le même process un **scénario d'événements factices** : de quoi
regarder l'UI (`apps/web`) vivre — cartes Kanban dans chaque colonne, coûts par
tâche (#57), message inter-agents, une demande de validation humaine (#48)
laissée en attente (le panneau reste cliquable), puis une pulsation QA
périodique qui montre le flux temps réel.

C'est le backend du lancement local en une commande
(`scripts/controltower/start.sh`, skill `control-tower`) ; la vraie
orchestration branchée sur Redis passe par `maestro-api` (`cli.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
from collections.abc import Sequence

from maestro.controltower.app import create_app
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    EventBus,
    InMemoryEventBus,
)
from maestro.telemetry.usage import StepUsage

#: Écoute par défaut : locale, même défaut que `maestro-api` — et que l'UI
#: (`apps/web/lib/api.ts` retombe sur http://localhost:8000).
HOTE_DEFAUT = "127.0.0.1"
PORT_DEFAUT = 8000

#: L'exécution factice à laquelle tout le scénario est rattaché.
RUN_ID = "demo-live"

#: Cadence de la pulsation QA : assez lente pour rester lisible, assez rapide
#: pour qu'un badge « Temps réel connecté » ait quelque chose à montrer.
PERIODE_PULSATION_S = 20.0

#: Respiration entre deux statuts d'une même tâche : l'œil voit la carte bouger.
PAUSE_ENTRE_STATUTS_S = 0.8


async def _avancer_tache(
    bus: EventBus,
    *,
    tache_id: str,
    titre: str,
    agent: str,
    role: str,
    etapes: Sequence[str],
    usage: StepUsage | None = None,
) -> None:
    """Fait avancer une tâche à travers `etapes` ; usage/coût posés sur la dernière."""
    for i, statut in enumerate(etapes):
        dernier = i == len(etapes) - 1
        await bus.publish(
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id=RUN_ID,
                tache_id=tache_id,
                titre=titre,
                agent=agent,
                role=role,
                statut=statut,
                cout_usd=usage.cout_usd if usage is not None and dernier else None,
                usage=usage if dernier else None,
            )
        )
        await asyncio.sleep(PAUSE_ENTRE_STATUTS_S)


async def _scenario(bus: EventBus) -> None:
    """Publie le scénario : un état initial parlant, puis une pulsation continue.

    L'état couvre les écrans de l'UI : planification de l'orchestrateur (fil
    d'activité), tâches dans plusieurs colonnes du Kanban avec leur coût,
    message inter-agents, et une validation humaine **laissée en attente** —
    approuver/refuser depuis l'UI publie la décision sur le bus, comme en vrai.
    """
    await bus.publish(
        Event(
            type=EVENEMENT_AGENT_ACTIVITE,
            run_id=RUN_ID,
            agent="orchestrateur",
            role="Orchestrateur",
            detail="Objectif décomposé en 4 tâches (mini-CRM : schéma, API, CI, maquette)",
            cout_usd=0.0210,
            usage=StepUsage(
                appels=1, tokens_entree=2140, tokens_sortie=380, cout_usd=0.0210, duree_ms=4200
            ),
        )
    )
    await asyncio.sleep(1)

    await _avancer_tache(
        bus,
        tache_id="demo-t1",
        titre="Concevoir le schéma SQL de la table contacts",
        agent="bdd",
        role="Base de données",
        etapes=["assignee", "en_cours", "terminee"],
        usage=StepUsage(
            appels=2,
            tokens_entree=5320,
            tokens_sortie=1240,
            cout_usd=0.0480,
            duree_ms=38000,
            tours=3,
            outils=("Write", "Bash"),
        ),
    )
    await bus.publish(
        Event(
            type=EVENEMENT_MESSAGE_INTER_AGENTS,
            run_id=RUN_ID,
            agent="bdd",
            role="Base de données",
            detail=(
                "→ developpeur : schéma prêt, la table `contacts` et sa migration "
                "sont disponibles"
            ),
        )
    )
    await asyncio.sleep(1)

    await _avancer_tache(
        bus,
        tache_id="demo-t2",
        titre="Implémenter l'API REST des contacts (créer / lister)",
        agent="developpeur",
        role="Développeur",
        etapes=["assignee", "en_cours", "terminee"],
        usage=StepUsage(
            appels=3,
            tokens_entree=9870,
            tokens_sortie=2610,
            cout_usd=0.0910,
            duree_ms=61000,
            tours=5,
            outils=("Write", "Edit", "Bash"),
        ),
    )

    await _avancer_tache(
        bus,
        tache_id="demo-t3",
        titre="Pipeline CI et déploiement de l'API",
        agent="devops",
        role="DevOps",
        etapes=["assignee", "en_cours"],
    )
    await bus.publish(
        Event(
            type=EVENEMENT_VALIDATION_DEMANDE,
            run_id=RUN_ID,
            tache_id="demo-t3",
            titre="Pipeline CI et déploiement de l'API",
            agent="devops",
            role="DevOps",
            description=(
                "L'agent veut pousser la configuration de déploiement vers l'environnement "
                "de démo (action sensible : écriture hors du bac à sable)."
            ),
            detail="validation requise avant déploiement",
        )
    )

    await _avancer_tache(
        bus,
        tache_id="demo-t4",
        titre="Maquette de l'écran de gestion des contacts",
        agent="designer",
        role="Designer",
        etapes=["assignee"],
    )

    for n in itertools.count(1):
        await asyncio.sleep(PERIODE_PULSATION_S)
        await _avancer_tache(
            bus,
            tache_id="demo-qa",
            titre="Vérification de santé de l'API (QA)",
            agent="qa",
            role="QA / Testeur",
            etapes=["assignee", "en_cours", "terminee"],
            usage=StepUsage(
                appels=1,
                tokens_entree=1180,
                tokens_sortie=210,
                cout_usd=0.0065,
                duree_ms=9000,
                tours=1,
                outils=("Bash",),
            ),
        )
        print(f"[scenario] pulsation QA n°{n} publiée", flush=True)


async def _servir(hote: str, port: int) -> int:
    """Sert l'app de démo et déroule le scénario ; bloquant jusqu'à l'arrêt (Ctrl-C)."""
    # Import local : seul ce point d'entrée dépend du serveur uvicorn (cf. cli.py).
    import uvicorn

    bus = InMemoryEventBus()
    config = uvicorn.Config(create_app(bus=bus), host=hote, port=port, log_level="warning")
    server = uvicorn.Server(config)
    serveur = asyncio.create_task(server.serve())
    # La pompe de l'app s'abonne au démarrage (lifespan) : publier avant, c'est
    # publier dans le vide — on attend que le serveur soit réellement démarré.
    while not server.started:
        if serveur.done():
            await serveur  # relève l'erreur de démarrage (port occupé…)
            return 1
        await asyncio.sleep(0.1)
    print(f"[api] Control Tower de démo sur http://{hote}:{port}", flush=True)
    scenario = asyncio.create_task(_scenario(bus))
    try:
        await serveur
    finally:
        scenario.cancel()
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Analyse les arguments (sortie 2 si l'appel est mal formé)."""
    parser = argparse.ArgumentParser(
        prog="maestro-controltower-demo",
        description=(
            "Control Tower de démonstration : l'API réelle sur bus mémoire, alimentée par un "
            "scénario d'événements factices — de quoi regarder l'UI sans Redis ni orchestration."
        ),
    )
    parser.add_argument(
        "--hote", default=HOTE_DEFAUT, help=f"adresse d'écoute (défaut : {HOTE_DEFAUT})"
    )
    parser.add_argument(
        "--port", type=int, default=PORT_DEFAUT, help=f"port d'écoute (défaut : {PORT_DEFAUT})"
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : sert la démo jusqu'à interruption (Ctrl-C = arrêt propre)."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return asyncio.run(_servir(args.hote, args.port))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
