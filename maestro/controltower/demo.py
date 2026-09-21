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
(répertoire temporaire) : la démo n'écrit rien dans `core/chat/`. Le **fil
global** en fait autant depuis #685 : son répondeur réel confie désormais le
jugement de l'intention au modèle, donc exige un fournisseur — ce que la démo
n'a pas, et n'a jamais eu à avoir.

Depuis #183, la démo est aussi le **backend de fixtures** des contrats d'API v2
(routes des Phases 5/6 : exécutions, journal requêtable, registre de
configuration, propositions de playbook globales, flux SSE d'un fil de chat) :
`create_app` reçoit un `FixturesControlTower`, et la voie front code contre ces
formes figées sans attendre le backend réel.

C'est le backend du lancement local en une commande
(`scripts/controltower/start.sh`, skill `control-tower`) ; la vraie
orchestration branchée sur Redis passe par `maestro-api` (`cli.py`).

Depuis #978, la démo sert aussi des **scénarios nommés** (`--scenario`) : le
**nominal** — tout ce qui précède, inchangé et servi sans option —, puis trois
états limites qu'elle ne savait pas montrer et où le rendu casse le plus
souvent : **vide** (aucun run, aucune tâche, aucune validation), **erreur**
(l'API répond en erreur, sa WebSocket refuse la connexion) et **charge** (des
centaines de lignes, des noms et des textes longs, un jeton sans espace). Ils
sont **demandés** : ni `/verify`, ni les tests de câblage, ni `captures.sh` ne
les rencontrent sans l'avoir voulu.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from maestro.agents.capacity import CapacityStore
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower.app import create_app
from maestro.controltower.chat import UTILISATEUR, ChatStore, MessageChat, RepondeurScripte
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_QUESTION_DEMANDE,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_DECISION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    ROLE_RUN,
    Event,
    EventBus,
    InMemoryEventBus,
    ReferenceTicket,
)
from maestro.controltower.fixtures import FixturesControlTower
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.state import EXECUTION_TERMINEE, QUESTION_EN_ATTENTE
from maestro.detail_tache import (
    ETAPE_A_FAIRE,
    ETAPE_EN_COURS,
    ETAPE_FAITE,
    LIEN_DEPOT,
    LIEN_MAQUETTE,
    LIEN_TICKET,
    EtapeTache,
    LienUtile,
    phrase_ecart_checklist,
)
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_DECISION_AUTONOME,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_QUESTION_SANS_REPONSE,
    STATUT_TERMINEE,
)
from maestro.equipe.gabarits import GABARITS
from maestro.plan_run import NoeudPlan
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

#: **L'équipe du projet de démo** (#1042) : nom du rôle → libellé, dans l'ordre.
#:
#: Le scénario nommait `developpeur`, `bdd`, `devops`… — les cinq agents que tout
#: poste recevait d'office. Ils sont devenus des **gabarits**
#: ([docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md)) : plus aucun
#: projet n'en reçoit, et un dépôt d'agents refuse même ces noms-là
#: (`NOMS_RESERVES`). La démo montre donc ce qu'un projet a vraiment après #1040 —
#: **une équipe validée**, écrite dans le projet —, et ses rôles sont ceux que
#: l'analyse proposerait pour ce mini-CRM.
#:
#: Dérivée de `maestro.equipe.gabarits.GABARITS`, jamais recopiée : un rôle
#: renommé là-bas suit ici, et la démo ne peut pas montrer une équipe que
#: l'analyse ne saurait pas proposer.
EQUIPE_DEMO: tuple[tuple[str, str], ...] = tuple(
    (gabarit.nom, gabarit.role) for gabarit in GABARITS
)

#: Les rôles de l'équipe, nommés un à un pour que le scénario les cite sans
#: littéral — un nom de rôle écrit en dur dans un événement serait un agent que
#: le projet n'a pas.
AGENT_DEV, AGENT_DONNEES, AGENT_INFRA, AGENT_INTERFACE, AGENT_TESTS = (
    nom for nom, _ in EQUIPE_DEMO
)

#: Le libellé d'un rôle de l'équipe — les événements portent les deux (#46).
ROLE_DE: dict[str, str] = dict(EQUIPE_DEMO)

#: **Le run déjà fini** (#928) — celui qu'on retrouve en arrivant, par opposition
#: à `RUN_ID`, qu'on regarde travailler.
#:
#: Distinct de `RUN_ID` pour la raison exacte qui a fait exister `RUN_CHARGE` :
#: aucune vue ne doit laisser croire que c'est le même run. Il existe parce que la
#: démo n'avait **aucun** run soldé — le scénario ne publiait que des statuts de
#: tâches, jamais d'`execution.statut` — donc aucun écran ne pouvait montrer ce
#: qu'un run rend en finissant, qui est tout l'objet du retex du 2026-09-11 (G1).
RUN_SOLDE = "demo-livre"

#: Ce que ce run avait à faire, tel que le fil l'a demandé. Il voyage sur
#: l'événement d'issue (`titre`) et c'est lui que l'annonce cite : un run se
#: reconnaît à son objectif, jamais à son identifiant.
OBJECTIF_SOLDE = "Exporter les contacts du mini-CRM en CSV, avec un rapport de qualité"

#: L'objectif qu'une **proposition en attente** soumet à l'accord (#943, #990) —
#: le `proposition` du dernier message du fil nominal. C'est lui que la carte
#: « Lancer ce run ? » affiche, éditable, et c'est lui qui partirait si l'on
#: cliquait : sans cette valeur, la carte n'a rien à montrer et ne se rend pas.
OBJECTIF_PROPOSE = (
    "Dédoublonner les contacts du mini-CRM sur l'adresse e-mail, puis relancer "
    "l'export CSV"
)

#: Les tâches de ce run, avec ce qu'elles ont coûté. Deux, et pas quatre : ce run
#: n'est pas là pour peupler un Kanban — il est là pour qu'un run **fini** existe.
_TACHES_SOLDEES: tuple[tuple[str, str, str, str, StepUsage], ...] = (
    (
        "livre-t1",
        "Extraire les contacts et écrire contacts.csv",
        AGENT_DEV,
        ROLE_DE[AGENT_DEV],
        StepUsage(
            appels=2,
            tokens_entree=6100,
            tokens_sortie=1480,
            cout_usd=0.0560,
            duree_ms=41000,
            tours=3,
            outils=("Read", "Write"),
        ),
    ),
    (
        "livre-t2",
        "Vérifier l'export et rédiger le rapport de qualité",
        AGENT_TESTS,
        ROLE_DE[AGENT_TESTS],
        StepUsage(
            appels=1,
            tokens_entree=3400,
            tokens_sortie=920,
            cout_usd=0.0310,
            duree_ms=22000,
            tours=2,
            outils=("Read",),
        ),
    ),
)

#: Cadence de la pulsation QA : assez lente pour rester lisible, assez rapide
#: pour qu'un badge « Temps réel connecté » ait quelque chose à montrer.
PERIODE_PULSATION_S = 20.0

#: Respiration entre deux statuts d'une même tâche : l'œil voit la carte bouger.
PAUSE_ENTRE_STATUTS_S = 0.8

#: **Les scénarios de la démo** (#978) — ce qu'on peut lui demander de servir.
#:
#: Le **nominal** est la démo d'avant ce lot, au bit près, et reste le défaut :
#: `captures.sh`, `/milestone-presentation` et les parcours filmés (#545) en
#: dépendent, et c'est lui que `/verify` et les tests de câblage rencontrent. Les
#: trois autres sont les **états limites** que la relecture visuelle reconnaissait
#: ne pas savoir atteindre (#932) — c'est là que le rendu casse : un état vide sans
#: explication, une erreur qui déborde, un nom de 80 caractères, une liste de 200
#: lignes.
#:
#: ⚠ Une constante par ligne, en début de ligne, entre guillemets droits : le
#: lanceur (`scripts/controltower/start.sh`) et la relecture
#: (`scripts/design/relecture-visuelle.sh`) les **lisent ici** plutôt que de les
#: recopier — une constante recopiée des deux côtés d'une frontière est ce que #830
#: a vu casser. `SCENARIOS` en fixe l'**ordre**, celui dans lequel on les annonce.
SCENARIO_NOMINAL = "nominal"
SCENARIO_VIDE = "vide"
SCENARIO_ERREUR = "erreur"
SCENARIO_CHARGE = "charge"
SCENARIOS: tuple[str, ...] = (SCENARIO_NOMINAL, SCENARIO_VIDE, SCENARIO_ERREUR, SCENARIO_CHARGE)

#: Les routes que le scénario « erreur » **laisse répondre**, et pas une de plus.
#:
#: `/api/sante` est la sonde du lanceur : `start.sh` attend qu'elle réponde avant
#: de monter l'UI, donc une santé en erreur ferait échouer le démarrage au lieu de
#: montrer l'erreur. `/api/projets` est le **verrou du shell**
#: (`apps/web/lib/etatProjetActif.tsx`) : en erreur, l'UI resterait sur le choix du
#: projet et l'on ne verrait qu'**une** erreur, celle de la porte, là où le
#: scénario existe pour montrer celle de **chaque** écran. Le prix est nommé : sur
#: l'écran « Projets », qui ne lit que cette route, le scénario ne montre rien.
ROUTES_EPARGNEES_PAR_L_ERREUR: tuple[str, ...] = ("/api/sante", "/api/projets")

#: Le code que le scénario « erreur » rend partout ailleurs : une panne serveur,
#: la plus ordinaire. L'UI la nomme telle quelle (« <chemin> a répondu 500 »).
STATUT_ERREUR_SIMULEE = 500
MESSAGE_ERREUR_SIMULEE = "Erreur simulée par le scénario « erreur » de la démo."

#: **Le plan du run** (#490), tel que la décomposition l'aurait rendu — publié une
#: fois, sur `run.plan`, juste après l'activité de planification qu'il détaille.
#:
#: Sans lui la démo rend un graphe `plan_connu: false` : des nœuds reconstruits
#: des seules tâches vues, tous au niveau 0 et **sans une arête**. Le dessin est
#: le même, ce qu'on en conclut ne l'est pas — et la vue pipeline (#491), qui est
#: précisément l'écran que cette démo doit montrer, n'y aurait rien à montrer
#: d'autre qu'une file de boîtes grises.
#:
#: La topologie reprend celle de l'exemple de docs/05 §6.11, sur les tâches du
#: scénario : le schéma d'abord, l'API et la maquette **en parallèle** derrière —
#: deux tâches sans dépendance entre elles, donc au même niveau —, la CI au bout.
#: Elle porte aussi l'**ossature de checklist** (#489), des libellés seuls : c'est
#: ce qui rend lisible une tâche qui n'a pas encore démarré.
PLAN_DEMO: tuple[NoeudPlan, ...] = (
    NoeudPlan(
        id="demo-t1",
        titre="Concevoir le schéma SQL de la table contacts",
        etapes=("Lister les entités", "Écrire la migration"),
    ),
    NoeudPlan(
        id="demo-t2",
        titre="Implémenter l'API REST des contacts (créer / lister)",
        dependances=("demo-t1",),
        etapes=(
            "Définir le contrat OpenAPI",
            "Implémenter la création",
            "Implémenter la liste paginée",
            "Tests d'intégration",
        ),
    ),
    NoeudPlan(
        id="demo-t4",
        titre="Maquette de l'écran de gestion des contacts",
        dependances=("demo-t1",),
        etapes=("Cadrer les écrans", "Poser la maquette"),
    ),
    NoeudPlan(
        id="demo-t3",
        titre="Pipeline CI et déploiement de l'API",
        dependances=("demo-t2",),
        etapes=("Écrire le workflow", "Brancher le déploiement"),
    ),
)

#: Le relevé de `demo-t1`, **entièrement coché** (#1112) : une tâche qui se solde
#: sur une checklist qui le confirme, c'est-à-dire le cas ordinaire.
#:
#: Il manquait, et c'est ce que le bouclage du 2026-09-21 a relevé : `demo-t1` ne
#: publiait aucun relevé, si bien que le graphe retombait sur l'ossature du plan
#: et affichait une tâche « Terminée » à **0/2**. La démo ne portait alors que des
#: relevés qui contredisent leur verdict, et pas un seul qui le confirme — alors
#: que porter les deux cas côte à côte est tout ce qu'on lui demande.
#:
#: Ses libellés sont ceux du plan, et un test le garde : deux listes qui divergent
#: raconteraient deux histoires de la même tâche.
CHECKLIST_DEMO_T1: tuple[EtapeTache, ...] = (
    EtapeTache(libelle="Lister les entités", etat=ETAPE_FAITE),
    EtapeTache(libelle="Écrire la migration", etat=ETAPE_FAITE),
)

#: Le relevé de `demo-t2`, **inachevé à la clôture** (#1112) : l'agent a livré
#: sans cocher ses deux dernières lignes, ce qui est le cas ordinaire que le
#: retex du 2026-09-11 a relevé (G12) et que #944 a tranché côté moteur — l'écart
#: se **dit**, il ne se comble pas.
#:
#: Nommé plutôt qu'écrit sur place parce qu'il a deux lecteurs : la tâche qui le
#: publie, et la ligne d'écart qui en dérive ses libellés. Les recopier ferait de
#: la démo le seul endroit du dépôt où l'écart et la checklist peuvent se
#: contredire.
CHECKLIST_DEMO_T2: tuple[EtapeTache, ...] = (
    EtapeTache(libelle="Définir le contrat OpenAPI", etat=ETAPE_FAITE),
    EtapeTache(libelle="Implémenter la création", etat=ETAPE_FAITE),
    EtapeTache(libelle="Implémenter la liste paginée", etat=ETAPE_EN_COURS),
    EtapeTache(libelle="Tests d'intégration", etat=ETAPE_A_FAIRE),
)


@dataclass(frozen=True)
class DecisionDemo:
    """Une décision que l'agent a tranchée seul, telle que la démo la publie (#1026).

    Les deux familles de la lecture « Décisions » d'un run, sous une seule forme
    parce qu'elles ne diffèrent que par leur **provenance** — et c'est exactement
    ce que l'écran doit rendre visible :

    - `hypothese=False` : l'agent a jugé que la question ne demandait personne, a
      tranché et l'a consigné (`tache.decision`, #1024) ;
    - `hypothese=True` : il a posé sa question, personne n'a répondu avant la
      borne, et il est reparti sur ce qu'il avait annoncé — au journal, une étape
      `<tache>:question` soldée `question_sans_reponse`, que le pont range en
      `agent.activite` (#1023).

    La démo publie donc **la forme d'arrivée** de chacune, et non un raccourci à
    elle : ce que l'écran lit ici est bit pour bit ce qu'un vrai run lui envoie,
    faute de quoi la démo validerait un rendu que la production ne produit pas.
    """

    decision: str
    raison: str
    hypothese: bool = False

    def evenement(
        self,
        tache_id: str,
        titre: str,
        agent: str,
        role: str,
        projet_id: str | None,
    ) -> Event:
        """L'événement de bus que cette décision produit, dans sa famille."""
        return Event(
            type=(EVENEMENT_AGENT_ACTIVITE if self.hypothese else EVENEMENT_TACHE_DECISION),
            run_id=RUN_ID,
            tache_id=tache_id,
            # Le `nom` de l'étape, préfixé comme le moteur le préfixe : c'est ce
            # qui oblige la liste à prendre le titre de la tâche à la projection
            # plutôt qu'à découper cette chaîne (`state.titres_du_run`).
            titre=(
                f"Question de l'agent — {titre}"
                if self.hypothese
                else f"Décision de l'agent — {titre}"
            ),
            agent=agent,
            role=role,
            statut=(
                STATUT_QUESTION_SANS_REPONSE
                if self.hypothese
                else STATUT_DECISION_AUTONOME
            ),
            detail=self.decision,
            description=self.raison,
            projet_id=projet_id,
        )


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
    gestes: Sequence[str] = (),
    decisions: Sequence[DecisionDemo] = (),
) -> None:
    """Fait avancer une tâche à travers `etapes` ; usage/coût posés sur la dernière.

    `gestes` (#837) sont les `agent.activite` que l'agent consigne **pendant**
    que la tâche est `en_cours` — un par geste, publiés juste après ce statut
    et avant le suivant. C'est ce qui donne à la tâche un **signe de vie**
    (#836) : sans eux, la carte, le nœud et le couloir d'une tâche en cours
    restent immobiles, et l'écran que ce lot est là pour montrer n'aurait rien
    à montrer. Vides par défaut : une tâche sans geste est la tâche d'avant.

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

    `decisions` (#1026) sont ce que l'agent a **tranché seul** pendant que la
    tâche est `en_cours` — publiées au même endroit que les `gestes`, et pour la
    même raison : sans elles, la lecture « Décisions » de la vue d'un run n'a
    rien à montrer, et l'écran que ce lot livre ne se voit pas. Les deux familles
    y sont, parce que les distinguer est tout l'objet de l'écran : une décision
    que l'agent a jugée sienne (#1024) et une hypothèse reprise faute de réponse
    (#1023). Vides par défaut : une tâche sans décision est la tâche d'avant.
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
        if statut != STATUT_EN_COURS:
            continue
        for geste in gestes:
            await bus.publish(
                Event(
                    type=EVENEMENT_AGENT_ACTIVITE,
                    run_id=RUN_ID,
                    tache_id=tache_id,
                    titre=titre,
                    agent=agent,
                    role=role,
                    detail=geste,
                    projet_id=projet_id,
                )
            )
            await asyncio.sleep(PAUSE_ENTRE_STATUTS_S)
        for prise in decisions:
            await bus.publish(prise.evenement(tache_id, titre, agent, role, projet_id))
            await asyncio.sleep(PAUSE_ENTRE_STATUTS_S)


async def _run_solde(bus: EventBus) -> None:
    """Publie **un run déjà fini**, tâches comprises (#928).

    Trois choses, et aucune n'est décorative :

    - **ses deux tâches**, soldées d'un coup (pas de pause entre les statuts :
      elles sont du passé, personne ne les regarde avancer) — sans elles le run
      n'aurait ni volume ni coût, et l'annonce de fin citerait un run vide ;
    - **son issue** (`execution.statut` = `terminee`), le seul événement que la
      démo ne publiait pas : c'est lui qui fait passer le run dans les vues qui
      lisent `GET /api/executions`, donc le seul qui puisse dire « c'est fini » ;
    - **son projet** : `projet_id` est ce qui rattache le run à une racine sur le
      disque, et donc ce dont l'annonce tire le chemin du livrable.
    """
    for tache_id, titre, agent, role, usage in _TACHES_SOLDEES:
        for statut in (STATUT_EN_COURS, STATUT_TERMINEE):
            dernier = statut == STATUT_TERMINEE
            await bus.publish(
                Event(
                    type=EVENEMENT_TACHE_STATUT,
                    run_id=RUN_SOLDE,
                    tache_id=tache_id,
                    titre=titre,
                    agent=agent,
                    role=role,
                    statut=statut,
                    cout_usd=usage.cout_usd if dernier else None,
                    usage=usage if dernier else None,
                    projet_id=PROJET_ID,
                )
            )
    await bus.publish(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN_SOLDE,
            titre=OBJECTIF_SOLDE,
            agent=ACTEUR_RUN,
            role=ROLE_RUN,
            statut=EXECUTION_TERMINEE,
            detail="2 tâche(s) terminée(s), 0 en échec",
            projet_id=PROJET_ID,
        )
    )


async def _scenario(bus: EventBus) -> None:
    """Publie le scénario : un état initial parlant, puis une pulsation continue.

    L'état couvre les écrans de l'UI : planification de l'orchestrateur (fil
    d'activité), tâches dans plusieurs colonnes du Kanban avec leur coût,
    message inter-agents, et une validation humaine **laissée en attente** —
    approuver/refuser depuis l'UI publie la décision sur le bus, comme en vrai.

    ⚠ Il couvre aussi, depuis #928, **un run déjà soldé** (`_run_soldé`) : sans
    lui la démo n'avait aucun run terminé, donc aucun écran ne pouvait montrer ce
    qu'un run rend en finissant. C'est la scène du retex du 2026-09-11 (G1), et
    elle se joue **avant** le run vivant : un run fini est ce qu'on retrouve en
    arrivant, pas ce qu'on regarde se faire.
    """
    await _run_solde(bus)
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
    # Deux événements pour une même étape, et c'est voulu (#490) : celui du
    # dessus dit ce que la planification a **coûté**, celui-ci ce qu'elle a
    # **décidé**. Sans lui, la vue pipeline (#491) n'aurait que des nœuds
    # reconstruits, sans une arête — le seul écran que cette démo n'a jamais su
    # montrer.
    await bus.publish(
        Event(
            type=EVENEMENT_RUN_PLAN,
            run_id=RUN_ID,
            agent="orchestrateur",
            role="Orchestrateur",
            detail="4 tâche(s), 3 enchaînement(s)",
            projet_id=PROJET_ID,
            plan=list(PLAN_DEMO),
        )
    )
    await asyncio.sleep(1)

    await _avancer_tache(
        bus,
        tache_id="demo-t1",
        titre="Concevoir le schéma SQL de la table contacts",
        agent=AGENT_DONNEES,
        role=ROLE_DE[AGENT_DONNEES],
        etapes=["assignee", "en_cours", "terminee"],
        usage=StepUsage(
            appels=2,
            tokens_entree=5320,
            tokens_sortie=1240,
            cout_usd=0.0480,
            duree_ms=38000,
            duree_attente_creneau_ms=0,
            duree_attente_atelier_ms=0,
            tours=3,
            outils=("Write", "Bash"),
        ),
        # Le cas ordinaire (#1112) : une tâche soldée dont le relevé confirme le
        # verdict. `demo-t2`, juste en dessous, garde le cas incomplet — avec la
        # ligne d'écart qu'un vrai run publie.
        checklist=CHECKLIST_DEMO_T1,
        # Une décision tranchée seul (#1024), de la famille « choix technique qui
        # ne dépasse pas le brief » : rien à demander, donc rien de suspendu.
        decisions=(
            DecisionDemo(
                decision="Clé primaire en UUID v7 plutôt qu'en entier auto-incrémenté",
                raison=(
                    "le brief prévoit une synchronisation entre deux bases ; un "
                    "compteur local y produirait des collisions, et le choix "
                    "n'engage que le schéma que cette tâche livre"
                ),
            ),
        ),
    )
    await bus.publish(
        Event(
            type=EVENEMENT_MESSAGE_INTER_AGENTS,
            run_id=RUN_ID,
            agent=AGENT_DONNEES,
            role=ROLE_DE[AGENT_DONNEES],
            detail=(
                f"→ {AGENT_DEV} : schéma prêt, la table `contacts` et sa migration "
                "sont disponibles"
            ),
        )
    )
    await asyncio.sleep(1)

    await _avancer_tache(
        bus,
        tache_id="demo-t2",
        titre="Implémenter l'API REST des contacts (créer / lister)",
        agent=AGENT_DEV,
        role=ROLE_DE[AGENT_DEV],
        etapes=["assignee", "en_cours", "terminee"],
        usage=StepUsage(
            appels=3,
            tokens_entree=9870,
            tokens_sortie=2610,
            cout_usd=0.0910,
            duree_ms=819000,
            # La tâche du retex (#989, G4) : 13 min 39 s d'horloge, dont 12 min 38 s
            # passées à attendre l'atelier du projet que `demo-t1` tenait. La démo
            # porte les deux cas côte à côte — celle-ci a attendu, `demo-t1` non —
            # pour qu'on voie du même coup d'œil ce qui s'affiche et ce qui se tait.
            duree_attente_creneau_ms=0,
            duree_attente_atelier_ms=758000,
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
        checklist=CHECKLIST_DEMO_T2,
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
        # Les **deux** familles sur la même tâche, et c'est voulu : c'est le seul
        # moyen de voir côte à côte ce que l'écran doit distinguer — une décision
        # que l'agent a jugée sienne, et une hypothèse qu'il a prise faute de
        # réponse à une question qu'il avait bel et bien posée (#1023).
        decisions=(
            DecisionDemo(
                decision="Pagination en curseur plutôt qu'en offset",
                raison=(
                    "aucune réponse après 900 s à : la liste des contacts se "
                    "pagine-t-elle en offset ou en curseur ?"
                ),
                hypothese=True,
            ),
            DecisionDemo(
                decision="Erreurs rendues au format RFC 7807 (`application/problem+json`)",
                raison=(
                    "le brief demande « les erreurs au format du projet » et le "
                    "projet n'en fixe aucun ; RFC 7807 est le seul format que le "
                    "client web sait déjà lire"
                ),
            ),
        ),
    )
    # L'écart entre le verdict et le relevé, **tel que le moteur le consigne**
    # (#944) : `demo-t2` se solde à 2/4, et un vrai run publie alors cette ligne
    # sur `agent.activite`. Sans elle, la démo montrait un compteur inachevé sous
    # un badge « Terminée » et rien nulle part pour dire laquelle des quatre
    # étapes manquait — le défaut que le bouclage du 2026-09-21 a relevé (#1112).
    # La phrase vient de `maestro.detail_tache`, jamais recopiée ici.
    await bus.publish(
        Event(
            type=EVENEMENT_AGENT_ACTIVITE,
            run_id=RUN_ID,
            tache_id="demo-t2",
            titre="Implémenter l'API REST des contacts (créer / lister)",
            agent=AGENT_DEV,
            role=ROLE_DE[AGENT_DEV],
            detail=phrase_ecart_checklist(
                [etape for etape in CHECKLIST_DEMO_T2 if etape.etat != ETAPE_FAITE],
                len(CHECKLIST_DEMO_T2),
            ),
            projet_id=PROJET_ID,
        )
    )
    await asyncio.sleep(1)

    await _avancer_tache(
        bus,
        tache_id="demo-t3",
        titre="Pipeline CI et déploiement de l'API",
        agent=AGENT_INFRA,
        role=ROLE_DE[AGENT_INFRA],
        etapes=["assignee", "en_cours"],
        # Une décision prise **pendant** que la tâche travaille encore, et dont
        # la validation en attente juste en dessous est le contraire : ce que
        # l'agent tranche seul ne passe par personne, l'acte sensible si (EF-08).
        decisions=(
            DecisionDemo(
                decision="Cache des dépendances indexé sur le lock, pas sur la branche",
                raison=(
                    "une clé par branche ferait repartir de zéro à chaque ticket ; "
                    "le réglage est interne au pipeline et se défait d'une ligne"
                ),
            ),
        ),
    )
    await bus.publish(
        Event(
            type=EVENEMENT_VALIDATION_DEMANDE,
            run_id=RUN_ID,
            tache_id="demo-t3",
            titre="Pipeline CI et déploiement de l'API",
            agent=AGENT_INFRA,
            role=ROLE_DE[AGENT_INFRA],
            description=(
                "L'agent veut pousser la configuration de déploiement vers l'environnement "
                "de démo (action sensible : écriture hors du bac à sable)."
            ),
            detail="validation requise avant déploiement",
        )
    )
    # Une **question libre** du même agent (#1023, #1025), délibérément posée sur
    # la tâche qui porte déjà une demande de validation : les deux canaux se
    # voient alors côte à côte, et c'est la meilleure façon de montrer qu'ils ne
    # sont pas le même — là on approuve un **acte** par oui/non, ici on répond du
    # **texte** à une question, et y répondre n'autorise rien (EF-08).
    #
    # La borne est **large** (30 min) là où le défaut du moteur est de quelques
    # minutes : la démo doit rendre l'état *en attente* de façon stable, y compris
    # quand on la laisse tourner le temps d'une relecture visuelle ou d'une
    # capture. L'état *échu* se voit en laissant passer l'échéance.
    await bus.publish(
        Event(
            type=EVENEMENT_QUESTION_DEMANDE,
            run_id=RUN_ID,
            tache_id="demo-t3",
            titre="Pipeline CI et déploiement de l'API",
            agent=AGENT_INFRA,
            role=ROLE_DE[AGENT_INFRA],
            statut=QUESTION_EN_ATTENTE,
            description=(
                "Le déploiement de démo doit-il tourner sur le même compose que "
                "la CI, ou sur une pile à lui ?"
            ),
            hypothese="je pars sur le même compose, plus simple à tenir à jour",
            choix=["Le même compose", "Une pile dédiée"],
            detail=(
                "sans réponse d'ici 1800 s, l'agent reprendra sur son hypothèse : "
                "je pars sur le même compose, plus simple à tenir à jour"
            ),
            echeance=(
                datetime.now(UTC) + timedelta(seconds=1800)
            ).isoformat(timespec="seconds"),
            projet_id=PROJET_ID,
            question_id="demo-t3:4f2a91c0de",
        )
    )

    await _avancer_tache(
        bus,
        tache_id="demo-t4",
        titre="Maquette de l'écran de gestion des contacts",
        agent=AGENT_INTERFACE,
        role=ROLE_DE[AGENT_INTERFACE],
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
            agent=AGENT_TESTS,
            role=ROLE_DE[AGENT_TESTS],
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
            # Deux gestes pendant `en_cours` (#837) : le seul moment de la démo
            # où une tâche **travaille** assez longtemps pour qu'on voie son
            # signe de vie compter, sur le nœud, la carte et le couloir.
            gestes=(
                "Lance la suite de santé : GET /sante, puis GET /contacts",
                "Relit les réponses — 200 partout, latence sous 50 ms",
            ),
        )
        print(f"[scenario] pulsation QA n°{n} publiée", flush=True)


# --- Scénario « charge » (#978) ----------------------------------------------------------------
#
# Des ordres de grandeur, pas des mesures : assez pour qu'une liste défile, qu'un
# graphe déborde, qu'un tableau s'allonge et qu'un fil de conversation ne tienne
# plus à l'écran. Tout est publié **d'un coup, au démarrage**, et rien ne pulse
# ensuite : une capture prise à la minute 1 et une autre à la minute 3 doivent
# montrer le même écran, sans quoi deux relectures ne se comparent pas.

#: Le run qui porte la charge. Distinct de `RUN_ID` : la charge n'est pas le
#: nominal grossi, et aucune vue ne doit laisser croire que c'est le même run.
RUN_CHARGE = "demo-charge"

#: Le volume. `CHARGE_TACHES` est le chiffre de la description de #978 (« une
#: liste de 200 lignes ») ; les autres sont réglés pour que **chaque** écran du
#: menu reçoive plus qu'il n'en affiche sans défiler.
CHARGE_TACHES = 200
CHARGE_RUNS = 24
CHARGE_VALIDATIONS = 30
CHARGE_ACTIVITES = 300
CHARGE_MESSAGES_INTER_AGENTS = 40
CHARGE_CONVERSATIONS = 30
CHARGE_MESSAGES_CHAT = 80

#: Largeur d'un niveau du plan : 200 tâches sur 20 niveaux, chacune dépendant de
#: sa voisine du niveau précédent — un graphe qui déborde dans les deux sens.
CHARGE_LARGEUR_NIVEAU = 10

#: **Les textes longs, sous leurs trois formes**, parce qu'elles ne cassent pas le
#: rendu de la même façon : un **nom** de 80 caractères (une colonne étroite, une
#: pastille, un en-tête), une **phrase** longue (un retour à la ligne, une hauteur
#: qui n'était pas prévue) et un **jeton sans espace** — identifiant, URL —, le
#: seul que le navigateur ne sait pas couper de lui-même, donc le débordement
#: horizontal type.
AGENT_LONG = "specialiste-integration-referentiels-clients-normalisation-des-adresses-postales"
ROLE_LONG = (
    "Spécialiste de l'intégration des référentiels clients et de la normalisation "
    "des adresses postales internationales"
)
JETON_LONG = (
    "mini_crm.contacts.import.normalisation_des_adresses_postales_internationales"
    ".verification_du_code_postal_et_du_pays.v2"
)
URL_LONGUE = (
    "https://gitlab.example/mini-crm/api/-/merge_requests/1287/diffs"
    "?commit_id=9f3c2a7e1b4d5c6f8a9b0c1d2e3f4a5b6c7d8e9f&view=parallel&w=1"
)
TITRE_LONG = (
    "Normaliser les adresses postales importées depuis l'ancien CRM en conservant la trace "
    "de chaque correction pour l'audit, sans bloquer l'import des fiches incomplètes"
)
TEXTE_LONG = (
    "L'import de l'ancien CRM remonte 48 213 fiches, dont 6 % portent une adresse postale "
    "incomplète ou mal découpée : numéro et voie dans le même champ, code postal absent, "
    "pays saisi en toutes lettres dans trois langues. La normalisation passe par le "
    f"référentiel `{JETON_LONG}`, et chaque correction est consignée avec la valeur "
    "d'origine pour que l'audit puisse la rejouer.\n\n"
    "Les fiches que le référentiel ne sait pas trancher ne bloquent pas l'import : elles "
    "entrent avec un drapeau « à vérifier » et une file dédiée, relue par l'équipe support. "
    f"Le détail des écarts relevés sur l'échantillon est dans {URL_LONGUE}.\n\n"
    "Hors périmètre : la déduplication des contacts (lot suivant), et la géolocalisation, "
    "qui demande un fournisseur externe et un arbitrage sur son coût."
)

#: Les agents de la charge : l'équipe du nominal, plus quelques-uns, dont **un**
#: porte le nom long — un seul suffit à élargir chaque colonne qui l'affiche. Les
#: deux derniers sont des rôles qu'une équipe peut très bien porter en plus des
#: cinq proposés : une équipe est sur mesure, elle n'a pas de taille fixe (#1042).
_AGENTS_CHARGE: tuple[tuple[str, str], ...] = (
    *EQUIPE_DEMO,
    (AGENT_LONG, ROLE_LONG),
    ("analyste", "Analyste"),
    ("securite", "Sécurité"),
)

#: La répartition des statuts : toutes les colonnes du Kanban reçoivent des cartes,
#: les terminées un peu plus — c'est ce qu'un run long accumule.
_STATUTS_CHARGE: tuple[str, ...] = (
    "assignee",
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    STATUT_TERMINEE,
    STATUT_ECHEC,
    STATUT_BLOQUEE,
)

_SUJETS_CHARGE: tuple[str, ...] = (
    "Migration du schéma des contacts",
    "Endpoint de recherche paginée",
    "Écran de fusion des doublons",
    "Export CSV des segments",
    "Journal d'audit des corrections",
    "Tests de charge de l'import",
    "Traduction des libellés d'erreur",
    "Revue des permissions par rôle",
)


def _usage_charge(i: int) -> StepUsage:
    """Un usage plausible et **varié** : des coûts tous égaux feraient un graphique plat."""
    return StepUsage(
        appels=1 + i % 4,
        tokens_entree=1800 + 173 * (i % 23),
        tokens_sortie=240 + 61 * (i % 17),
        cout_usd=round(0.004 * (1 + i % 9), 4),
        duree_ms=4000 + 1500 * (i % 11),
        tours=1 + i % 6,
        outils=("Read", "Edit", "Bash")[: 1 + i % 3],
    )


def _titre_charge(i: int) -> str:
    """Un titre sur sept est long, un sur treize porte le jeton sans espace."""
    if i % 13 == 0:
        return f"Brancher {JETON_LONG}"
    if i % 7 == 0:
        return TITRE_LONG
    return f"Lot {i:03d} — {_SUJETS_CHARGE[i % len(_SUJETS_CHARGE)]}"


async def _scenario_charge(bus: EventBus) -> None:
    """Publie la charge d'un coup : un gros run, beaucoup de petits, et tout ce qui s'accumule.

    Aucune pause et aucune pulsation (voir l'en-tête de la section). L'UI se
    connecte bien après — le lanceur attend l'API avant de monter Next —, donc
    elle trouve un état **déjà** chargé, comme un poste qui tourne depuis des
    heures, et non un flot d'événements qui la ferait recharger à chacun.
    """
    await bus.publish(
        Event(
            type=EVENEMENT_AGENT_ACTIVITE,
            run_id=RUN_CHARGE,
            agent=ACTEUR_RUN,
            role="Orchestrateur",
            detail=f"Objectif décomposé en {CHARGE_TACHES} tâches — {TITRE_LONG}",
            cout_usd=0.0840,
            usage=StepUsage(
                appels=2, tokens_entree=18400, tokens_sortie=5120, cout_usd=0.0840, duree_ms=41000
            ),
            projet_id=PROJET_ID,
        )
    )
    identifiants = [f"charge-t{i:03d}" for i in range(1, CHARGE_TACHES + 1)]
    plan = [
        NoeudPlan(
            id=identifiant,
            titre=_titre_charge(i),
            dependances=(
                (identifiants[i - 1 - CHARGE_LARGEUR_NIVEAU],) if i > CHARGE_LARGEUR_NIVEAU else ()
            ),
            etapes=("Cadrer", "Implémenter", "Vérifier"),
        )
        for i, identifiant in enumerate(identifiants, start=1)
    ]
    await bus.publish(
        Event(
            type=EVENEMENT_RUN_PLAN,
            run_id=RUN_CHARGE,
            agent=ACTEUR_RUN,
            role="Orchestrateur",
            detail=(
                f"{CHARGE_TACHES} tâche(s), "
                f"{CHARGE_TACHES - CHARGE_LARGEUR_NIVEAU} enchaînement(s)"
            ),
            projet_id=PROJET_ID,
            plan=plan,
        )
    )

    en_cours: list[tuple[str, str, str, str]] = []
    for i, identifiant in enumerate(identifiants, start=1):
        agent, role = _AGENTS_CHARGE[i % len(_AGENTS_CHARGE)]
        statut = _STATUTS_CHARGE[i % len(_STATUTS_CHARGE)]
        solde = statut in (STATUT_TERMINEE, STATUT_ECHEC)
        detaille = i % 25 == 0
        usage = _usage_charge(i) if solde else None
        titre = _titre_charge(i)
        await bus.publish(
            Event(
                type=EVENEMENT_TACHE_STATUT,
                run_id=RUN_CHARGE,
                tache_id=identifiant,
                titre=titre,
                agent=agent,
                role=role,
                statut=statut,
                cout_usd=usage.cout_usd if usage is not None else None,
                usage=usage,
                ticket=(
                    ReferenceTicket(id=f"#{1000 + i}", url=URL_LONGUE) if i % 5 == 0 else None
                ),
                projet_id=PROJET_ID,
                description=TEXTE_LONG if i % 10 == 0 else "",
                etapes=(
                    [
                        EtapeTache(
                            libelle=(TITRE_LONG if n == 3 else f"Étape {n + 1} — {JETON_LONG}"),
                            etat=(ETAPE_FAITE, ETAPE_EN_COURS, ETAPE_A_FAIRE)[min(n // 4, 2)],
                        )
                        for n in range(12)
                    ]
                    if detaille
                    else None
                ),
                liens=(
                    [
                        LienUtile(libelle=TITRE_LONG, url=URL_LONGUE, nature=LIEN_TICKET),
                        LienUtile(libelle=JETON_LONG, url=URL_LONGUE, nature=LIEN_DEPOT),
                        LienUtile(libelle="Maquette", url=URL_LONGUE, nature=LIEN_MAQUETTE),
                    ]
                    if detaille
                    else None
                ),
            )
        )
        if statut == STATUT_EN_COURS:
            en_cours.append((identifiant, titre, agent, role))

    for n in range(CHARGE_ACTIVITES):
        agent, role = _AGENTS_CHARGE[n % len(_AGENTS_CHARGE)]
        identifiant = identifiants[n % CHARGE_TACHES]
        await bus.publish(
            Event(
                type=EVENEMENT_AGENT_ACTIVITE,
                run_id=RUN_CHARGE,
                tache_id=identifiant,
                agent=agent,
                role=role,
                detail=(
                    TEXTE_LONG
                    if n % 17 == 0
                    else f"Geste {n + 1} : relit {JETON_LONG} puis relance la vérification"
                ),
                projet_id=PROJET_ID,
            )
        )

    for n in range(CHARGE_MESSAGES_INTER_AGENTS):
        agent, role = _AGENTS_CHARGE[n % len(_AGENTS_CHARGE)]
        destinataire, _ = _AGENTS_CHARGE[(n + 3) % len(_AGENTS_CHARGE)]
        await bus.publish(
            Event(
                type=EVENEMENT_MESSAGE_INTER_AGENTS,
                run_id=RUN_CHARGE,
                agent=agent,
                role=role,
                detail=f"→ {destinataire} : {TITRE_LONG if n % 2 else TEXTE_LONG}",
                projet_id=PROJET_ID,
            )
        )

    for identifiant, titre, agent, role in en_cours[:CHARGE_VALIDATIONS]:
        await bus.publish(
            Event(
                type=EVENEMENT_VALIDATION_DEMANDE,
                run_id=RUN_CHARGE,
                tache_id=identifiant,
                titre=titre,
                agent=agent,
                role=role,
                description=TEXTE_LONG,
                detail=f"validation requise avant d'écrire dans {JETON_LONG}",
            )
        )

    # Les autres runs : petits, mais nombreux — c'est la liste des runs et l'analyse
    # des coûts qui s'allongent, pas le détail de chacun.
    for r in range(1, CHARGE_RUNS):
        run_id = f"{RUN_CHARGE}-r{r:02d}"
        for t in range(3):
            agent, role = _AGENTS_CHARGE[(r + t) % len(_AGENTS_CHARGE)]
            usage = _usage_charge(r * 3 + t)
            await bus.publish(
                Event(
                    type=EVENEMENT_TACHE_STATUT,
                    run_id=run_id,
                    tache_id=f"{run_id}-t{t + 1}",
                    titre=TITRE_LONG if (r + t) % 4 == 0 else _titre_charge(r * 3 + t),
                    agent=agent,
                    role=role,
                    statut=STATUT_ECHEC if (r + t) % 9 == 0 else STATUT_TERMINEE,
                    cout_usd=usage.cout_usd,
                    usage=usage,
                    projet_id=PROJET_ID,
                )
            )
    print(f"[scenario] charge publiée : {CHARGE_TACHES} tâches, {CHARGE_RUNS} runs", flush=True)


def _depot_agents_demo() -> AgentStore:
    """Un dépôt d'agents **éphémère**, où l'équipe du projet de démo est écrite (#1042).

    La démo ne peut plus compter sur cinq agents reçus d'office : un projet naît
    sans agent, et les rôles du code sont devenus des gabarits. Elle écrit donc
    l'équipe qu'elle montre, là où une équipe validée se range — **dans le
    projet** (`_projets/<PROJET_ID>/`, #1038) —, avec le playbook du gabarit dont
    chaque rôle descend, exactement ce que `#1040` écrit après une validation.

    Éphémère comme le fil de chat et le dépôt de capacités : la démo n'écrit rien
    dans `core/agents/`. La racine du dépôt reste **vide** : c'est le niveau des
    gabarits, et y poser une fiche en ferait un gabarit, pas un membre d'équipe.

    ⚠ Ces agents ne sont lisibles que si le projet `prj-demo` est **déclaré** (les
    routes de configuration refusent un projet inconnu) — ce que fait
    `scripts/presentation/captures.sh` via `MAESTRO_PROJETS_DIR`. Sans
    déclaration, la démo reste ce qu'elle était : des agents que le flux
    d'événements fait apparaître au fil du scénario.
    """
    depot = AgentStore(Path(tempfile.mkdtemp(prefix="maestro-agents-demo-")))
    equipe = depot.pour_projet(PROJET_ID)
    for gabarit in GABARITS:
        equipe.ecrire(
            AgentDefinition(
                nom=gabarit.nom,
                role=gabarit.role,
                competences=gabarit.competences,
                playbook=gabarit.playbook_de_repli(),
            )
        )
    return depot


def _peupler_chat_nominal(store: ChatStore) -> None:
    """Met dans le fil global **la demande qui a ouvert le run soldé**, et sa réponse (#928).

    Les deux premiers messages sont la scène exacte du retex du 2026-09-11 : on
    demande quelque chose, l'orchestration répond « c'est parti », et le run
    finit ailleurs, sans que le fil en sache rien. La réponse porte `run_id`
    (#268) : c'est le rattachement persisté par lequel le fil sait de quel run il
    parle, et il existe depuis #269.

    Les deux derniers sont la **demande de cadrage en attente** (#943), et ils
    sont là pour une raison mécanique : la carte « Lancer ce run ? » n'existe à
    l'écran que si le **dernier** message du fil porte une `proposition`
    (`chat.proposition_en_attente`). Sans eux, la porte d'entrée du produit —
    donc l'écran où se posent les bornes d'un run (#990) — ne se montre dans
    aucune démo, et ni une capture ni une relecture visuelle ne peuvent
    l'atteindre. L'état nominal montre donc les deux moments du fil : ce qui a
    été lancé, et ce qui attend un accord.
    """
    ouverture = datetime.now(UTC) - timedelta(minutes=53)
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR,
            contenu=f"{OBJECTIF_SOLDE} — le fichier doit être relisible tel quel.",
            horodatage=ouverture.isoformat(timespec="seconds"),
        )
    )
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu=(
                "C'est parti : deux tâches, l'extraction puis la vérification. "
                "Je vous préviens dès que c'est fini."
            ),
            horodatage=(ouverture + timedelta(seconds=12)).isoformat(timespec="seconds"),
            run_id=RUN_SOLDE,
        )
    )
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR,
            contenu="Il faudrait aussi dédoublonner les contacts avant l'export.",
            horodatage=(ouverture + timedelta(minutes=51)).isoformat(timespec="seconds"),
        )
    )
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu=(
                "Je vous propose de dédoublonner les contacts du mini-CRM sur "
                "l'adresse e-mail, puis de relancer l'export CSV. Je lance ?"
            ),
            horodatage=(ouverture + timedelta(minutes=51, seconds=9)).isoformat(
                timespec="seconds"
            ),
            proposition=OBJECTIF_PROPOSE,
        )
    )


def _peupler_chat_charge(store: ChatStore) -> None:
    """Remplit le fil global de conversations nombreuses, et l'une d'elles de messages longs.

    Le fil se relit du **disque** (`ChatStore`), pas du bus : c'est donc ici, avant
    que l'app ne serve, qu'il se remplit. Le dépôt est le répertoire temporaire de
    la démo — rien n'atteint `core/chat/`.
    """
    maintenant = datetime.now(UTC)
    for n in range(CHARGE_CONVERSATIONS):
        # La plus récente est la dernière écrite, et c'est elle qui porte le fil
        # long : c'est celle que l'écran ouvre d'office.
        ouverture = maintenant - timedelta(hours=CHARGE_CONVERSATIONS - n)
        conversation = f"{ouverture:%Y%m%dt%H%M%S}-ch{n:04d}"
        nombre = CHARGE_MESSAGES_CHAT if n == CHARGE_CONVERSATIONS - 1 else 2
        for m in range(nombre):
            de_l_utilisateur = m % 2 == 0
            if de_l_utilisateur:
                contenu = TITRE_LONG if m % 6 == 0 else f"Où en est {JETON_LONG} ?"
            else:
                contenu = TEXTE_LONG if m % 4 == 1 else f"C'est en cours, voir {URL_LONGUE}"
            store.ajouter(
                MessageChat(
                    agent=NOM_ORCHESTRATION,
                    auteur=UTILISATEUR if de_l_utilisateur else NOM_ORCHESTRATION,
                    contenu=contenu,
                    horodatage=(ouverture + timedelta(seconds=30 * m)).isoformat(
                        timespec="seconds"
                    ),
                    conversation=conversation,
                )
            )


# --- Scénario « erreur » (#978) ----------------------------------------------------------------


class _ApiEnErreur:
    """Middleware ASGI du scénario « erreur » : l'API répond en panne, sa WebSocket refuse.

    Deux moitiés, parce que l'UI a deux canaux et qu'une panne réelle les coupe
    tous les deux : le REST rend `STATUT_ERREUR_SIMULEE` (sauf
    `ROUTES_EPARGNEES_PAR_L_ERREUR`), et toute WebSocket est **refusée avant
    l'acceptation** — uvicorn répond alors 403 à la poignée de main, et l'UI passe
    en « Reconnexion… » comme devant une API tombée.

    ⚠ Il se branche **à l'intérieur** du CORS (`_brancher_erreur`), jamais
    par-dessus : une réponse sans en-tête CORS est une réponse que le navigateur
    **cache** au code, qui ne verrait alors qu'un « Failed to fetch » — une API
    **coupée**, pas une API **en erreur**, et le message que l'écran rend ne serait
    plus celui qu'on voulait regarder.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1011})
            return
        chemin = scope.get("path", "")
        if (
            scope["type"] == "http"
            and chemin.startswith("/api/")
            and chemin not in ROUTES_EPARGNEES_PAR_L_ERREUR
        ):
            reponse = JSONResponse(
                {"detail": MESSAGE_ERREUR_SIMULEE}, status_code=STATUT_ERREUR_SIMULEE
            )
            await reponse(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _brancher_erreur(app: FastAPI) -> None:
    """Accroche `_ApiEnErreur` en **dernier** des middlewares utilisateur, donc sous le CORS.

    `add_middleware` l'aurait mis en **premier** — par-dessus le CORS, dont les
    en-têtes manqueraient alors aux réponses en erreur (voir `_ApiEnErreur`).
    La liste est lue au premier appel de l'app, donc la compléter avant que
    uvicorn ne serve suffit.
    """
    app.user_middleware.append(Middleware(_ApiEnErreur))


async def _servir(hote: str, port: int, scenario: str = SCENARIO_NOMINAL) -> int:
    """Sert l'app de démo et déroule le scénario ; bloquant jusqu'à l'arrêt (Ctrl-C).

    `scenario` (#978) choisit ce que l'app sert : le **nominal** par défaut ;
    **vide** ne publie rien ; **erreur** ne publie rien non plus et fait répondre
    l'API en panne ; **charge** remplit le fil de conversation puis publie la
    charge d'un coup.
    """
    # Import local : seul ce point d'entrée dépend du serveur uvicorn (cf. cli.py).
    import uvicorn

    bus = InMemoryEventBus()
    chat_store = ChatStore(Path(tempfile.mkdtemp(prefix="maestro-chat-demo-")))
    if scenario == SCENARIO_CHARGE:
        _peupler_chat_charge(chat_store)
    elif scenario == SCENARIO_NOMINAL:
        _peupler_chat_nominal(chat_store)
    app = create_app(
        bus=bus,
        # Chat de démo (#84) : réponses scriptées (aucun modèle ni auth) sur un
        # fil éphémère — la démo ne laisse aucune trace dans core/chat/.
        chat_store=chat_store,
        chat_repondeur=RepondeurScripte(),
        # Le fil global (#268) passe par le modèle depuis #685 : sans ce
        # répondeur scripté, le premier message de la démo résoudrait le
        # fournisseur configuré — donc demanderait une authentification que la
        # démo existe précisément pour éviter. C'est l'usage que le point
        # d'injection `orchestration_repondeur` porte depuis l'origine.
        orchestration_repondeur=RepondeurScripte(),
        # Capacités éphémères (#86) : activer/désactiver ou régler les instances
        # depuis l'UI de démo n'écrit rien dans core/capacite/.
        capacites=CapacityStore(Path(tempfile.mkdtemp(prefix="maestro-capacite-demo-"))),
        # L'équipe du projet de démo (#1042), écrite dans un dépôt éphémère : la
        # démo ne suppose plus qu'un `devops` existe, elle montre l'équipe que ce
        # projet a validée.
        agents_store=_depot_agents_demo(),
        # Contrats d'API v2 (#183) : la démo sert les routes des Phases 5/6 en
        # données factices — la voie front code contre elles sans backend réel.
        fixtures=FixturesControlTower(),
    )
    if scenario == SCENARIO_ERREUR:
        _brancher_erreur(app)
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
    print(
        f"[api] Control Tower de démo sur http://{hote}:{port} — scénario « {scenario} »",
        flush=True,
    )
    # « vide » et « erreur » ne publient rien : un état vide est l'absence
    # d'événement, et une API en panne n'a rien à montrer derrière sa panne.
    publications = {SCENARIO_NOMINAL: _scenario, SCENARIO_CHARGE: _scenario_charge}
    publier = publications.get(scenario)
    deroule = asyncio.create_task(publier(bus)) if publier is not None else None
    try:
        await serveur
    finally:
        if deroule is not None:
            deroule.cancel()
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
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default=SCENARIO_NOMINAL,
        help=(
            f"ce que la démo sert (défaut : {SCENARIO_NOMINAL}) — {SCENARIO_VIDE} : aucun run ; "
            f"{SCENARIO_ERREUR} : API en panne ; {SCENARIO_CHARGE} : listes et textes longs"
        ),
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : sert la démo jusqu'à interruption (Ctrl-C = arrêt propre)."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return asyncio.run(_servir(args.hote, args.port, args.scenario))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
