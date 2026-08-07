"""Control Tower de démonstration : l'app réelle sur bus mémoire + scénario factice (ticket #65).

Sert l'API Control Tower **réelle** (`create_app` — mêmes endpoints REST et
WebSocket que la production) sur un `InMemoryEventBus`, sans Redis ni Docker,
et publie depuis le même process un **scénario d'événements factices** : de quoi
regarder l'UI (`apps/web`) vivre — cartes Kanban dans chaque colonne, coûts par
tâche (#57), message inter-agents, une demande de validation humaine (#48)
laissée en attente (le panneau reste cliquable), puis une pulsation QA
périodique qui montre le flux temps réel.

Le chat utilisateur ↔ agent (#84) répond en **scripté** (`RepondeurScripte` :
aucun modèle appelé, aucune authentification requise) sur un fil **éphémère**
(répertoire temporaire) : la démo n'écrit rien dans `core/chat/`.

Depuis #183, la démo est aussi le **backend de fixtures** des contrats d'API v2
(routes des Phases 5/6 : exécutions, journal requêtable, registre de
configuration, propositions de playbook globales, flux SSE d'un fil de chat) :
`create_app` reçoit un `FixturesControlTower`, et la voie front code contre ces
formes figées sans attendre le backend réel.

C'est le backend du lancement local en une commande
(`scripts/controltower/start.sh`, skill `control-tower`) ; la vraie
orchestration branchée sur Redis passe par `maestro-api` (`cli.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from maestro.agents.capacity import CapacityStore
from maestro.controltower.app import create_app
from maestro.controltower.chat import ChatStore, RepondeurScripte
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    EventBus,
    InMemoryEventBus,
    ReferenceTicket,
)
from maestro.controltower.fixtures import FixturesControlTower
from maestro.detail_tache import (
    ETAPE_A_FAIRE,
    ETAPE_EN_COURS,
    ETAPE_FAITE,
    LIEN_DEPOT,
    LIEN_MAQUETTE,
    LIEN_TICKET,
    EtapeTache,
    LienUtile,
)
from maestro.telemetry.usage import StepUsage

#: Écoute par défaut : locale, même défaut que `maestro-api` — et que l'UI
#: (`apps/web/lib/api.ts` retombe sur http://localhost:8000).
HOTE_DEFAUT = "127.0.0.1"
PORT_DEFAUT = 8000

#: L'exécution factice à laquelle tout le scénario est rattaché.
RUN_ID = "demo-live"

#: Le projet dans lequel ce run travaille (#222) — le même que les fixtures du
#: journal, pour qu'un `?projet=prj-demo` rende une vue cohérente d'un écran à
#: l'autre. Le scénario laisse à dessein une tâche **hors projet** (`demo-t4`) :
#: un travail sans projet reste normal, et c'est ce qui rend le filtre visible.
PROJET_ID = "prj-demo"

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
    ticket: ReferenceTicket | None = None,
    projet_id: str | None = PROJET_ID,
    description: str = "",
    checklist: Sequence[EtapeTache] = (),
    liens: Sequence[LienUtile] = (),
) -> None:
    """Fait avancer une tâche à travers `etapes` ; usage/coût posés sur la dernière.

    `ticket` (#183) rattache la tâche à un ticket externe : la référence voyage
    avec chaque événement de statut — de quoi montrer le lien sur la carte du
    Kanban (`GET /api/taches`) comme le ferait un vrai run parti d'un ticket.

    `projet_id` (#222) rattache la tâche à un **projet** et voyage de la même
    façon : c'est ce que filtrent `GET /api/taches?projet=…` et la vue coûts.
    None pour une tâche hors projet — le comportement d'avant ce lot.

    `description`, `checklist` et `liens` (#246) sont le **détail** de la tâche,
    celui qu'ouvre le panneau du Kanban (#251). Vides par défaut : une tâche de
    démo sans détail montre exactement la carte d'avant ce lot, ce qui est le
    seul moyen de voir les deux comportements côte à côte. `checklist` est
    nommée ainsi pour ne pas se confondre avec `etapes`, qui reste la suite des
    **statuts** traversés.
    """
    # `None` (et non `[]`) quand la tâche n'a rien à détailler : le détail voyage
    # avec chaque statut comme le ticket, et une liste vide effacerait à chaque
    # événement ce que le précédent a posé (#246).
    checklist_portee = list(checklist) or None
    liens_portes = list(liens) or None
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
                ticket=ticket,
                projet_id=projet_id,
                description=description,
                etapes=checklist_portee,
                liens=liens_portes,
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
            # La planification est une dépense du projet au même titre que les
            # tâches (#222) : sans ce champ, le total filtré par projet serait
            # inférieur à la somme des runs de ce projet.
            projet_id=PROJET_ID,
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
        # Référence de ticket externe (#183/#187) : la carte porte un lien vers le
        # ticket dont elle relève — servi tel quel par `GET /api/taches`.
        ticket=ReferenceTicket(
            id="#42", url="https://gitlab.example/maestro/-/issues/42"
        ),
        # Le détail (#246) : c'est la seule tâche de la démo qui en porte, pour
        # qu'on voie côte à côte une carte qui s'ouvre et une carte qui non.
        description=(
            "Exposer les contacts en REST : `POST /contacts` pour créer, "
            "`GET /contacts` pour lister (pagination, tri par nom). Validation "
            "des champs obligatoires côté API, erreurs au format du projet."
        ),
        checklist=(
            EtapeTache(libelle="Définir le contrat OpenAPI", etat=ETAPE_FAITE),
            EtapeTache(libelle="Implémenter la création", etat=ETAPE_FAITE),
            EtapeTache(libelle="Implémenter la liste paginée", etat=ETAPE_EN_COURS),
            EtapeTache(libelle="Tests d'intégration", etat=ETAPE_A_FAIRE),
        ),
        liens=(
            LienUtile(
                libelle="Maquette de l'écran contacts",
                url="https://figma.example/file/contacts",
                nature=LIEN_MAQUETTE,
            ),
            LienUtile(
                libelle="#42 — API REST des contacts",
                url="https://gitlab.example/maestro/-/issues/42",
                nature=LIEN_TICKET,
            ),
            LienUtile(
                libelle="mini-crm/api",
                url="https://gitlab.example/mini-crm/api",
                nature=LIEN_DEPOT,
            ),
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
        # Hors projet à dessein (#222) : le Kanban filtré sur `prj-demo` ne
        # doit pas la montrer — un travail sans projet reste du travail.
        projet_id=None,
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
    app = create_app(
        bus=bus,
        # Chat de démo (#84) : réponses scriptées (aucun modèle ni auth) sur un
        # fil éphémère — la démo ne laisse aucune trace dans core/chat/.
        chat_store=ChatStore(Path(tempfile.mkdtemp(prefix="maestro-chat-demo-"))),
        chat_repondeur=RepondeurScripte(),
        # Capacités éphémères (#86) : activer/désactiver ou régler les instances
        # depuis l'UI de démo n'écrit rien dans core/capacite/.
        capacites=CapacityStore(Path(tempfile.mkdtemp(prefix="maestro-capacite-demo-"))),
        # Contrats d'API v2 (#183) : la démo sert les routes des Phases 5/6 en
        # données factices — la voie front code contre elles sans backend réel.
        fixtures=FixturesControlTower(),
    )
    config = uvicorn.Config(app, host=hote, port=port, log_level="warning")
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
