"""Événements temps réel de la Control Tower — modèle et bus de diffusion (ticket #46).

L'API Control Tower expose l'état de l'orchestration et le **pousse en temps
réel** aux clients (UI). Le vecteur commun est l'`Event` : un fait daté et
JSON-sérialisable — changement de statut d'une tâche, activité d'un agent,
message inter-agents, réassignation manuelle.

Les événements circulent sur un **bus** (`EventBus`), avec deux implémentations
au même contrat :

- `InMemoryEventBus` : file(s) asyncio en process — le levier des tests d'API
  (aucun Redis ni réseau requis) et d'un déploiement mono-process ;
- `RedisEventBus` : **Redis Pub/Sub** (canal `CANAL_EVENEMENTS`) — le chemin de
  production (docs/02 §4), sur l'instance Redis mutualisée avec la file de
  tâches (#41, infra/docker-compose.yml). Producteurs (télémétrie via
  `maestro.controltower.bridge`, workers) et consommateurs (l'API) peuvent
  alors vivre dans des process distincts.

Le bus est volontairement **éphémère** (pub/sub, pas de rejeu) : un client qui
se connecte voit l'état courant via le REST (projection `state.py`) puis les
événements suivants via le WebSocket — même modèle que l'UI (docs/05 §4).
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from maestro.acte import arguments_depuis
from maestro.appartenance import projet_id_valide
from maestro.detail_tache import EtapeTache as EtapeTache  # ré-export explicite
from maestro.detail_tache import LienUtile as LienUtile  # ré-export explicite
from maestro.detail_tache import etapes_depuis, liens_depuis
from maestro.orchestrator.schema import Brief as Brief  # ré-export explicite
from maestro.plan_run import NoeudPlan as NoeudPlan  # ré-export explicite
from maestro.plan_run import noeuds_depuis
from maestro.projets.application import DiffProjet
from maestro.references import ReferenceTicket as ReferenceTicket  # ré-export explicite
from maestro.sources.modele import Source as Source  # ré-export explicite
from maestro.sources.modele import sources_depuis
from maestro.telemetry.usage import StepUsage

# `ReferenceTicket` (#187, contrat #183) est **défini** dans `maestro.references`,
# module feuille, et seulement ré-exporté ici : le journal (#8) le porte aussi, et
# `controltower` important déjà `telemetry`, le garder dans ce module aurait fermé
# un cycle d'imports. Les appelants historiques — `from
# maestro.controltower.events import ReferenceTicket` — restent servis tels quels.
# `EtapeTache` et `LienUtile` (#246) vivent dans `maestro.detail_tache` pour la
# même raison, et sont ré-exportés de la même façon. `Source` (#315) suit les
# trois : définie dans `maestro.sources.modele`, feuille, elle voyage du
# lancement à la projection en traversant les mêmes couches. `Brief` (#318) est le
# cinquième, à ceci près qu'il vient de `maestro.orchestrator.schema` : c'est
# l'orchestrateur qui le produit, et il n'y a aucune raison d'en tenir un second
# modèle ici — il voyage du moteur (#320) à l'écran de validation (#322) tel quel.
# `NoeudPlan` (#490) est le sixième, et il suit `EtapeTache` au mot près :
# défini dans `maestro.plan_run`, feuille, parce que le journal le transporte
# (`StepRecord.plan`) et que la projection le garde (`EtatExecution.plan`).

#: Types d'événements diffusés (docs/05 §2.1 : flux d'activité temps réel).
#: `tache.statut` suit la machine à états de docs/03 §3 ; `tache.reassignation`
#: est l'acte manuel du Kanban (EF-11/EF-20) ; `agent.activite` couvre la
#: planification et les validations humaines ; `message.inter_agents` porte la
#: messagerie A2A (entité AGENT_MESSAGE, docs/03 §2) ; `validation.demande` et
#: `validation.decision` portent le human-in-the-loop (#48, entité APPROVAL de
#: docs/03) — le moteur demande, la Control Tower rend la décision humaine ;
#: `chat.message` porte le chat utilisateur ↔ agent (#84) : `agent` désigne le
#: fil, `statut` l'auteur (« utilisateur »/« agent »), `detail` le contenu ;
#: `agent.capacite` porte le contrôle de capacité (#86, EF-21) : `statut` l'état
#: résultant (« active »/« desactive ») et `instances` le plafond résultant ;
#: `playbook.proposition` porte l'apparition d'une proposition d'auto-amélioration
#: (#183, voie Phase 5/6) : `agent` le fil concerné, `role` son rôle, `statut` le
#: numéro de brouillon (chaîne) et `detail` la justification — un signal **global**
#: (sans `run_id`) que l'UI badge et pousse en notification (cadrage #182, item 9).
#: `execution.statut` porte le **cycle de vie d'un run** piloté par l'API (#185) :
#: `statut` l'état résultant (« en_cours » au lancement, « terminee »/« echec » à
#: l'issue, « annulee » sur interruption humaine), `titre` l'objectif et `detail`
#: la raison — c'est le seul signal qui dise à la projection qu'un run a commencé
#: ou fini, les étapes du journal ne parlant que de tâches.
EVENEMENT_TACHE_STATUT = "tache.statut"
EVENEMENT_TACHE_REASSIGNATION = "tache.reassignation"
#: `tache.reference` (#187) rattache une tâche à son **ticket externe** : seul
#: `ticket` y est renseigné, tout le reste étant inchangé — c'est le seul
#: événement de tâche qui ne touche ni statut, ni agent, ni coût, pour qu'un
#: agent puisse nommer le ticket dont relève sa tâche en cours d'exécution sans
#: la faire changer de colonne au Kanban.
EVENEMENT_TACHE_REFERENCE = "tache.reference"
#: `tache.detail` (#246) porte ce qu'il faut pour **comprendre** une tâche sans
#: quitter l'écran : sa `description`, ses `etapes` et ses `liens` utiles. Même
#: forme que `tache.reference` et pour la même raison — un agent qui découvre en
#: cours de route une étape ou une maquette à ouvrir doit pouvoir le dire sans
#: faire changer sa tâche de colonne au Kanban : ni statut, ni agent, ni coût.
EVENEMENT_TACHE_DETAIL = "tache.detail"
#: `tache.blocage` (#719) porte le blocage qu'un **agent déclare** en cours de
#: tâche : `detail` en donne la raison, telle qu'il l'a écrite. Même forme que
#: `tache.reference` et `tache.detail`, et pour la même raison — ni statut, ni
#: agent, ni coût ne changent : un agent qui bute n'est pas une tâche bloquée, et
#: docs/31 §3.4 lui refuse explicitement le droit de changer son propre statut,
#: qui condamnerait tout son aval par la cascade de #43 alors qu'il travaille
#: encore.
#:
#: ⚠ Il lui fallait un type à lui plutôt que de rejoindre `agent.activite`, et
#: c'est la frise (#355) qui l'impose : elle écarte `agent.activite` à dessein
#: — le **bruit de fond** d'un run —, si bien qu'un blocage rangé là serait
#: consigné puis invisible, exactement l'inverse de ce que ce verbe existe pour
#: faire. L'y verser en bloc noierait à l'inverse les signaux que #355 demande de
#: distinguer. Un type distinct est la seule voie qui montre le blocage **sans**
#: défaire ce tri.
EVENEMENT_TACHE_BLOCAGE = "tache.blocage"
#: `tache.usage` (#835) porte ce qu'une tâche **en cours** a consommé jusqu'ici :
#: `usage` en est le cumul (tokens, tours, et le coût si le fournisseur l'a déjà
#: tarifé), `cout_usd` le raccourci scalaire. Même forme que `tache.reference`,
#: `tache.detail` et `tache.blocage`, et pour la même raison — ni statut, ni
#: agent ne changent : une tâche qui dépense ne change pas de colonne.
#:
#: ⚠ C'est le seul événement dont l'usage est un **cumul** et non une part, et
#: c'est ce qui lui vaut un type à lui : les trois lecteurs comptables du flux
#: (`EtatExecution.cout`, `analytics.agrege_couts`, et le `cout_usd` d'un run)
#: additionnent l'usage des `tache.statut`, `agent.activite` et messages — y
#: ranger un cumul compterait chaque tour autant de fois qu'il a été relevé. Un
#: type distinct les laisse hors du grand livre par construction, et la
#: projection en fait ce qu'il est : le **coût partiel** de la tâche, que son
#: `tache.statut` final remplace.
EVENEMENT_TACHE_USAGE = "tache.usage"
EVENEMENT_AGENT_ACTIVITE = "agent.activite"
EVENEMENT_AGENT_CAPACITE = "agent.capacite"
EVENEMENT_MESSAGE_INTER_AGENTS = "message.inter_agents"
EVENEMENT_VALIDATION_DEMANDE = "validation.demande"
EVENEMENT_VALIDATION_DECISION = "validation.decision"
EVENEMENT_CHAT_MESSAGE = "chat.message"
EVENEMENT_PLAYBOOK_PROPOSITION = "playbook.proposition"
EVENEMENT_EXECUTION_STATUT = "execution.statut"

#: L'acteur au nom duquel le **cycle de vie d'un run** est consigné — le même que
#: celui de l'étape de planification du journal (#8). Il vit ici, avec le type
#: d'événement qu'il accompagne, parce que depuis #446 il n'a plus un seul
#: écrivain : `ServiceExecutions._consigne` l'émet côté API, et un **hôte** l'émet
#: en partant (`maestro.controltower.bridge.solder_le_run`), qu'il soit détaché ou
#: `maestro-run --publier`. Deux constantes recopiées seraient deux acteurs
#: différents sur le même écran le jour où l'une des deux change.
ACTEUR_RUN = "orchestrateur"
ROLE_RUN = "Orchestrateur"
#: `brief.demande` et `brief.decision` (#320) portent la **validation humaine du
#: brief** avant décomposition (décision D5) : le moteur soumet, la Control Tower
#: rend la décision. Canal **distinct** de `validation.*` à dessein — celui-là
#: transporte un booléen sur une action sensible (#48), celui-ci un **brief
#: corrigé** sur un run entier —, mais même bus et même patron d'attente. Le run
#: qu'ils visent est dans `run_id` (jamais `tache_id` : aucune tâche n'existe
#: encore, c'est tout le sujet), `brief` porte le brief proposé puis celui qui a
#: été retenu, et `statut` l'issue (« approuvee »/« refusee », le vocabulaire des
#: validations, cf. `maestro.controltower.state`).
EVENEMENT_BRIEF_DEMANDE = "brief.demande"
EVENEMENT_BRIEF_DECISION = "brief.decision"
#: `brief.questions` et `brief.reponses` (#321) portent les **allers-retours de
#: clarification**, en amont de la validation ci-dessus : le run publie les questions
#: que le brief a laissées ouvertes et attend, l'humain répond, le brief est régénéré.
#: Troisième canal `brief.*` et non un détournement du deuxième, pour une raison de
#: nature : `brief.decision` clôt le cadrage (une fois, par oui ou non), ceux-ci le
#: **poursuivent** (jusqu'au plafond, avec du texte libre). Les confondre ferait d'un
#: run en cours de clarification un run tranché, donc repartirait en décomposition
#: avec un brief encore troué. `brief` porte le brief **dont** on pose les questions
#: (jamais une copie de la liste : le brief est régénéré en entier à chaque tour, deux
#: sources se périmeraient), `reponses` les réponses appariées **par position**, et
#: `tour`/`tours_max` l'annonce du plafond — ce qui permet à celui qui répond de
#: savoir s'il lui reste un tour.
EVENEMENT_BRIEF_QUESTIONS = "brief.questions"
EVENEMENT_BRIEF_REPONSES = "brief.reponses"

#: `run.plan` (#490) porte le **graphe du run** — un nœud par tâche, ses
#: dépendances, son ossature de checklist —, publié **une fois**, à l'instant où
#: la décomposition rend son plan. Il ne dit rien de l'état : ni agent, ni
#: statut, ni coût, ni durée, aucun de ces faits n'existant encore (cf.
#: `maestro.plan_run`). Le run visé est dans `run_id` et jamais dans `tache_id` —
#: l'événement porte sur le run entier, comme `execution.statut`.
#:
#: ⚠ Il **double** la ligne de journal de la planification, il ne la remplace
#: pas : la même étape produit toujours son `agent.activite` (c'est par lui que
#: l'usage du cadrage entre au grand livre, #57, et qu'il s'affiche au fil
#: d'activité). Deux événements pour une ligne, parce que ce sont deux faits :
#: ce que la planification a **coûté**, et ce qu'elle a **décidé**. Les fondre
#: aurait fait dépendre le graphe d'un type d'événement dont la projection ne
#: touche, à dessein, ni aux tâches ni aux runs.
EVENEMENT_RUN_PLAN = "run.plan"

#: Canal Redis Pub/Sub des événements — sur l'instance mutualisée avec la file
#: de tâches (#41), d'où un canal nommé plutôt que le canal par défaut.
CANAL_EVENEMENTS = "maestro.evenements"

#: URL Redis par défaut : l'instance locale du docker-compose (infra/README.md).
#: Doublonne volontairement `maestro.queue.celery_app.REDIS_URL_DEFAUT` pour ne
#: pas faire dépendre la Control Tower de Celery.
REDIS_URL_DEFAUT = "redis://localhost:6379/0"


def _horodatage() -> str:
    """Horodatage UTC ISO-8601, même précision que le journal (#8)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def brief_depuis(donnees: Any) -> Brief | None:
    """Relit un brief venu du flux (#320) — None si rien d'exploitable.

    **Relecture, jamais nouvelle saisie** : même règle que pour les sources (#315).
    Un brief qui circule sur le bus a déjà été validé contre le schéma partagé, à sa
    production (#318) comme à sa correction humaine (`POST …/brief/decision`) ; le
    revalider ici rendrait illisible un run passé dès que le schéma bougerait, et
    ferait échouer le **rejeu du journal durable** (#97) sur un run ancien plutôt
    que de l'afficher tel qu'il était.

    D'où la seule exigence retenue : que les quatre champs requis soient là. Un
    payload amputé retombe sur None — l'événement dit alors qu'il n'apprend rien du
    brief, ce que la projection sait déjà traiter (elle n'efface pas ce qu'un
    événement précédent a posé).
    """
    if not isinstance(donnees, Mapping):
        return None
    requis = ("objectif", "perimetre", "hors_perimetre", "criteres_acceptation")
    if any(cle not in donnees for cle in requis):
        return None
    return Brief.from_dict(donnees)


def reponses_depuis(donnees: Any) -> list[str]:
    """Relit des réponses de clarification venues du flux (#321) — jamais None.

    Même régime que `brief_depuis` : **relecture, pas nouvelle saisie**. Ce qui n'est
    pas une chaîne est écarté plutôt que de faire échouer la relecture d'un run
    passé ; une liste absente ou illisible retombe sur `[]`, que l'appariement par
    position traite comme « aucune réponse » — donc en hypothèses, jamais en
    questions reposées.
    """
    if not isinstance(donnees, list):
        return []
    return [entree for entree in donnees if isinstance(entree, str)]


def _entier_positif(valeur: Any) -> int:
    """Relit un compteur venu du flux — 0 pour tout ce qui n'est pas un entier ≥ 0.

    `bool` est exclu à dessein : c'est un `int` en Python, et un `True` relu en `1`
    ferait passer un booléen égaré pour un premier tour de clarification.
    """
    if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
        return 0
    return valeur


@dataclass(frozen=True)
class Event:
    """Un fait daté de l'orchestration, prêt à voyager en JSON.

    `type` est l'un des `EVENEMENT_*` ; les autres champs sont renseignés selon
    le type (une activité d'agent n'a pas forcément de `tache_id`). `usage`
    porte la mesure complète de l'étape (tokens entrée/sortie, coût, durée —
    la matière de la comptabilité par tâche, #57) quand la télémétrie (#8) l'a
    rapportée ; `cout_usd` en reste le raccourci scalaire — None sinon
    (inconnu, à distinguer d'un coût nul). `detail` est un texte libre déjà
    expurgé des secrets par le journal amont (`redact_secrets`). `description`
    porte le contexte long d'une demande de validation (#48 : l'action que
    l'agent réaliserait, telle que décrite par la tâche) — vide ailleurs.
    `instances` porte le plafond d'instances résultant d'un réglage de capacité
    (#86, entité AGENT `instances_max`) — None ailleurs. Sur un événement de
    **tâche**, `description` porte celle de la tâche (#246) : le même champ que
    pour une validation, et le même sens — ce que le travail demande, en long.
    `etapes` et `liens` (#246) l'accompagnent : les lignes de checklist de la
    tâche et les liens utiles à ouvrir pour la traiter. Tous trois sont **None
    ou vides** quand l'événement n'en apprend rien, ce qui est le comportement
    d'avant ce lot — un consommateur qui ignore ces clés n'est pas cassé, et une
    tâche sans détail rend exactement la carte d'avant. `ticket` porte la
    référence du ticket externe dont relève la tâche (#187, contrat #183) — None
    quand aucune n'est connue ; il voyage avec les événements de tâche pour que
    l'UI l'affiche et qu'il survive au rejeu du journal durable. `projet_id`
    porte le **projet** auquel le travail appartient (#222, entité PROJECT de
    #221) — None quand le travail ne relève d'aucun projet, ce qui est le
    comportement d'avant ce lot : un consommateur qui ignore la clé n'est pas
    cassé, et les vues qui filtrent par projet n'ont rien à voir. Il voyage sur
    les événements de tâche comme sur ceux du cycle de vie d'un run, et survit
    donc au rejeu du journal durable (#97) comme le reste. `diff` porte les
    **modifications qu'une application dans le projet écrirait** (#227, EF-37) —
    fichiers touchés, lignes ajoutées/supprimées, branche à fusionner : la pièce
    jointe d'une demande de validation dont la question est « applique-t-on
    ceci ? », et None partout ailleurs. `sources` porte la **matière d'entrée**
    d'un objectif (#315, EF-39) — fichiers, dossier de références, URL, déjà
    résolus et plafonnés au lancement : c'est par lui qu'elles rejoignent la
    projection, donc qu'elles survivent au rejeu du journal durable. None
    partout ailleurs, y compris sur le lancement d'un objectif **sans** source :
    l'événement est alors identique, au bit près, à celui d'avant ce lot.
    """

    type: str
    run_id: str = ""
    tache_id: str = ""
    titre: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    detail: str = ""
    description: str = ""
    cout_usd: float | None = None
    usage: StepUsage | None = None
    instances: int | None = None
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None
    # None (et non `[]`) quand l'événement n'apprend rien : c'est ce qui permet à
    # la projection de distinguer « pas d'information » de « plus aucune étape »
    # et de ne pas effacer ce qu'un événement précédent a posé (#246).
    etapes: list[EtapeTache] | None = None
    liens: list[LienUtile] | None = None
    diff: DiffProjet | None = None
    # None (et non `[]`) pour la même raison qu'`etapes`/`liens` : seul le
    # lancement en porte, et un événement de fin ne doit pas effacer les sources
    # posées au départ (#315).
    sources: list[Source] | None = None
    # Le brief soumis puis retenu (#320) — None (et non un brief vide) partout
    # ailleurs, pour la même raison qu'`etapes`/`liens`/`sources` : la projection
    # distingue « cet événement n'apprend rien du brief » de « le brief est vide »,
    # et l'issue d'un run n'a pas à effacer ce que sa demande a posé.
    brief: Brief | None = None
    # Le régime du brief du run (#320 : « sans »/« auto »/« humain »), porté par
    # l'événement de **lancement** — c'est par lui qu'il rejoint la projection, donc
    # `ResumeExecution`, donc l'annonce du mode dans le résumé du run. Vide ailleurs.
    mode_brief: str = ""
    # Les réponses humaines aux questions du brief (#321), appariées **par position**
    # aux questions du brief soumis. None (et non `[]`) partout ailleurs, pour la même
    # raison que les champs ci-dessus : la projection distingue « cet événement
    # n'apprend rien des réponses » de « aucune réponse n'a été donnée ».
    reponses: list[str] | None = None
    # Le rang de l'aller-retour et le plafond annoncé (#321) — 0 partout ailleurs.
    # Portés par `brief.questions`, c'est par eux que l'annonce du plafond atteint
    # l'UI : « tour 1 sur 2 » dit à celui qui répond ce qui lui reste.
    tour: int = 0
    tours_max: int = 0
    # Le run **dont celui-ci est la suite** (#349), porté par l'événement de
    # lancement d'une relance et vide partout ailleurs. Chaîne vide plutôt que
    # `None`, contrairement aux champs ci-dessus : il n'y a rien à ne pas effacer —
    # aucun événement ultérieur n'en parle, et « ce run ne reprend personne » est un
    # fait, pas une absence d'information. Même relation que le fichier `reprise-de`
    # entre deux runs d'orchestration (#204), et même parti pris : le run repris
    # n'est jamais réécrit pour désigner son successeur, c'est le **nouveau** qui
    # dit de qui il est la suite.
    reprise_de: str = ""
    # **Pourquoi** un run s'est arrêté (#479), porté par son événement d'issue et
    # vide partout ailleurs : l'un des codes de `maestro.controltower.causes`, ou
    # la chaîne vide quand aucun ne s'applique. Chaîne vide plutôt que `None`,
    # pour la même raison que `reprise_de` : il n'y a rien à ne pas effacer — un
    # seul événement en parle, et « aucune cause reconnue » est un fait, pas une
    # absence d'information. Il vient **en plus** de `detail`, jamais à sa place :
    # le code dit de quoi il s'agit, le détail ce qui s'est passé.
    cause: str = ""
    # Le **graphe du plan** (#490), porté par le seul `run.plan` : nœuds, arêtes
    # et ossatures de checklist, tels que la décomposition les a écrits. None
    # (et non `[]`) partout ailleurs, pour la raison qui vaut déjà
    # d'`etapes`/`liens`/`sources` : la projection distingue « cet événement
    # n'apprend rien du plan » de « le plan est vide », et rien de ce qui suit la
    # planification ne doit effacer le graphe qu'elle a posé.
    plan: list[NoeudPlan] | None = None
    # L'**acte** qui a déclenché une demande d'arbitrage (#581) : l'outil appelé
    # et ce qu'on lui passe, portés par le seul `validation.demande` et vides
    # partout ailleurs. `outil` en chaîne vide plutôt qu'en None, pour la raison
    # de `reprise_de`/`cause` : un seul événement en parle, et « cette demande ne
    # porte pas d'acte » est un fait — celui d'une validation de tâche (#48) ou
    # d'une application de diff (#227) — et non une absence d'information.
    # `arguments` reste None, lui, parce qu'il porte une **charge** : None dit
    # qu'il n'y a rien à afficher, `{}` dirait qu'un outil n'a aucun paramètre,
    # et aucun consommateur n'a à faire la différence.
    outil: str = ""
    arguments: dict[str, str] | None = None
    # **Qui tranche** l'acte soumis (#586) : `auto` ou `humain`
    # (`maestro.decideur` — un troisième cran, `orchestrateur`, a été retiré par
    # #715, mais des événements déjà émis le portent : ce champ est de la donnée
    # durable, `str` et non `Decideur`, et il n'a donc pas rétréci avec
    # l'énumération), porté par le seul `validation.demande` et vide
    # partout ailleurs. Chaîne vide plutôt que `humain`, pour la raison
    # de `outil`/`cause`/`reprise_de` : un seul événement en parle, et « cet
    # événement ne dit rien d'un décideur » n'est pas « cet acte revient à une
    # personne » — le défaut du **champ métier** vit dans `DemandeValidation`,
    # pas dans le transport, où il ferait annoncer une attente humaine à chaque
    # statut de tâche.
    decideur: str = ""
    horodatage: str = field(default_factory=_horodatage)

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'événement en dict JSON-sérialisable (la forme du WebSocket)."""
        return {
            "type": self.type,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
            "titre": self.titre,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "detail": self.detail,
            "description": self.description,
            "cout_usd": self.cout_usd,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "instances": self.instances,
            "ticket": self.ticket.to_dict() if self.ticket is not None else None,
            "projet_id": self.projet_id,
            "etapes": (
                [etape.to_dict() for etape in self.etapes] if self.etapes is not None else None
            ),
            "liens": ([lien.to_dict() for lien in self.liens] if self.liens is not None else None),
            "diff": self.diff.to_dict() if self.diff is not None else None,
            "sources": (
                [source.to_dict() for source in self.sources]
                if self.sources is not None
                else None
            ),
            "brief": self.brief.to_dict() if self.brief is not None else None,
            "mode_brief": self.mode_brief,
            "reponses": list(self.reponses) if self.reponses is not None else None,
            "tour": self.tour,
            "tours_max": self.tours_max,
            "reprise_de": self.reprise_de,
            "cause": self.cause,
            "plan": (
                [noeud.to_dict() for noeud in self.plan] if self.plan is not None else None
            ),
            "outil": self.outil,
            "arguments": dict(self.arguments) if self.arguments is not None else None,
            "decideur": self.decideur,
            "horodatage": self.horodatage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        """Reconstruit un événement depuis sa forme `to_dict` (aller-retour JSON).

        Les clés absentes retombent sur les défauts : un producteur minimaliste
        (seul `type` est requis) reste lisible.
        """
        usage_brut = data.get("usage")
        ticket_brut = data.get("ticket")
        diff_brut = data.get("diff")
        return cls(
            type=data["type"],
            run_id=data.get("run_id", ""),
            tache_id=data.get("tache_id", ""),
            titre=data.get("titre", ""),
            agent=data.get("agent", ""),
            role=data.get("role", ""),
            statut=data.get("statut", ""),
            detail=data.get("detail", ""),
            description=data.get("description", ""),
            cout_usd=data.get("cout_usd"),
            usage=StepUsage.from_dict(usage_brut) if isinstance(usage_brut, Mapping) else None,
            instances=data.get("instances"),
            ticket=(
                ReferenceTicket.from_dict(ticket_brut)
                if isinstance(ticket_brut, Mapping)
                else None
            ),
            # Un `projet_id` reçu du bus est traité comme tout ce qui vient de
            # l'extérieur (#222) : normalisé, et écarté s'il n'est pas un
            # identifiant de projet — il sert de nom de fichier au dépôt (#221).
            projet_id=projet_id_valide(data.get("projet_id")),
            # Absentes → None (l'événement n'en dit rien) ; présentes → la liste
            # normalisée, éventuellement vide si rien n'y était lisible (#246).
            etapes=(etapes_depuis(data["etapes"]) if data.get("etapes") is not None else None),
            liens=(liens_depuis(data["liens"]) if data.get("liens") is not None else None),
            diff=DiffProjet.from_dict(diff_brut) if isinstance(diff_brut, Mapping) else None,
            # Relecture, jamais nouvelle saisie (#315) : les sources ont été
            # résolues et plafonnées au lancement ; les rejuger ici rendrait
            # illisible un run passé dès qu'un plafond serait resserré.
            sources=(
                sources_depuis(data["sources"]) if data.get("sources") is not None else None
            ),
            # Même régime que les sources (#320) : relecture tolérante, jamais
            # revalidation — cf. `brief_depuis`.
            brief=brief_depuis(data.get("brief")),
            mode_brief=data.get("mode_brief", ""),
            # Même régime que les autres listes (#321) : absente → None (l'événement
            # n'en dit rien) ; présente → les seules entrées textuelles, les autres
            # écartées. Relecture tolérante, comme le brief lui-même.
            reponses=(
                reponses_depuis(data["reponses"]) if data.get("reponses") is not None else None
            ),
            tour=_entier_positif(data.get("tour")),
            tours_max=_entier_positif(data.get("tours_max")),
            # Relu en texte, sans vérifier que le run désigné existe (#349) : c'est
            # une **référence historique**, pas une clé étrangère. Le run repris peut
            # avoir été purgé du journal ; le nouveau doit continuer de dire de qui
            # il est la suite, comme `reprise-de` côté orchestration (#204).
            reprise_de=str(data.get("reprise_de") or ""),
            # Relu en texte et **non vérifié** contre `CAUSES` (#479), au même
            # titre que le brief ou les sources : un run passé doit rester
            # lisible si le vocabulaire des causes s'enrichit, et le rejeu du
            # journal durable (#97) ne doit pas trébucher sur un code qu'une
            # version ultérieure a introduit. L'écran, lui, sait déjà ne rien
            # afficher d'un code qu'il ne connaît pas.
            cause=str(data.get("cause") or ""),
            # Même régime que les autres listes (#490) : absente → None
            # (l'événement n'en dit rien) ; présente → les seuls nœuds lisibles.
            # **Relecture, jamais revalidation** — le plan a été validé contre
            # `task.schema.json` à sa production, et le rejuger ici rendrait
            # illisible le graphe d'un run passé dès que le schéma bougerait.
            plan=(noeuds_depuis(data["plan"]) if data.get("plan") is not None else None),
            # Même régime que les listes ci-dessus (#581) : absents → None
            # (l'événement n'apprend rien de l'acte) ; présents → les seules
            # entrées lisibles, chaque valeur bornée. **Relecture, jamais
            # revalidation** : les arguments ont été bornés et expurgés à la
            # publication, et les rejuger ici rendrait illisible l'arbitrage d'un
            # run passé le jour où la borne bougerait.
            outil=str(data.get("outil") or ""),
            arguments=(
                arguments_depuis(data["arguments"])
                if data.get("arguments") is not None
                else None
            ),
            # Même régime que `cause` (#586) : la valeur brute passe telle quelle,
            # sans être rejugée. Un cran émis par une version plus récente doit
            # arriver jusqu'à l'écran, qui sait ne pas afficher ce qu'il ne
            # connaît pas ; le repli sûr (`decideur_depuis`) est appliqué là où
            # une **décision** se prend, jamais sur un transport.
            decideur=str(data.get("decideur") or ""),
            horodatage=data.get("horodatage", ""),
        )

    def to_json(self) -> str:
        """Sérialise l'événement en JSON compact (la forme du canal Redis)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, brut: str | bytes) -> Event:
        """Désérialise un événement du canal Redis (`bytes` tels que reçus)."""
        texte = brut.decode("utf-8") if isinstance(brut, bytes) else brut
        data = json.loads(texte)
        if not isinstance(data, dict):
            raise ValueError(f"événement JSON attendu (objet), reçu : {texte!r}")
        return cls.from_dict(data)


class EventBus(ABC):
    """Bus de diffusion des événements — le point d'injection de l'API (#46).

    `publish` pousse un événement à tous les abonnés ; `subscribe` rend un
    itérateur asynchrone des événements **à venir** (pas de rejeu — le pub/sub
    est éphémère, l'état courant s'obtient par le REST). Plusieurs abonnés
    voient chacun tous les événements.
    """

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publie `event` à tous les abonnés du bus."""
        raise NotImplementedError

    @abstractmethod
    def subscribe(self) -> AsyncIterator[Event]:
        """S'abonne au bus : itérateur asynchrone des événements à venir."""
        raise NotImplementedError

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027) —
    # seul un bus à connexions (Redis) a quelque chose à libérer.
    async def close(self) -> None:  # noqa: B027
        """Libère les ressources du bus (connexions) — no-op par défaut."""


class InMemoryEventBus(EventBus):
    """Bus en mémoire : une file asyncio par abonné, aucun réseau.

    C'est le bus des **tests d'API** (REST + WebSocket, critère CI du ticket)
    et d'un déploiement mono-process où producteurs et API partagent la même
    boucle asyncio. Les files sont non bornées : le POC diffuse peu
    d'événements et un abonné (WebSocket) les consomme au fil de l'eau.
    """

    def __init__(self) -> None:
        self._abonnes: list[asyncio.Queue[Event]] = []

    async def publish(self, event: Event) -> None:
        # Copie défensive : un abonné peut se désabonner pendant la boucle.
        for file in list(self._abonnes):
            file.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[Event]:
        file: asyncio.Queue[Event] = asyncio.Queue()
        self._abonnes.append(file)
        try:
            while True:
                yield await file.get()
        finally:
            # Exécuté à la fermeture du générateur (fin de boucle, annulation) :
            # l'abonné disparaît, ses événements en attente avec lui.
            self._abonnes.remove(file)


class RedisEventBus(EventBus):
    """Bus adossé à Redis Pub/Sub — le chemin de production multi-process.

    Publie et consomme sur `canal` (JSON compact, `Event.to_json`). La
    dépendance `redis` est déjà tirée par `celery[redis]` (#41) ; l'instance
    visée est celle du docker-compose (mutualisée avec la file de tâches).
    La connexion est paresseuse : construite ici, ouverte au premier appel.
    """

    def __init__(self, url: str | None = None, *, canal: str = CANAL_EVENEMENTS) -> None:
        # Import local : seule la branche Redis dépend du client (l'API testée
        # sur bus mémoire n'en a pas besoin).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)
        self._canal = canal

    async def publish(self, event: Event) -> None:
        await self._client.publish(self._canal, event.to_json())

    async def subscribe(self) -> AsyncIterator[Event]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self._canal)
        try:
            async for message in pubsub.listen():
                # `listen` intercale des messages de contrôle (subscribe…) :
                # seuls les messages de données portent un événement.
                if message.get("type") != "message":
                    continue
                yield Event.from_json(message["data"])
        finally:
            await pubsub.unsubscribe(self._canal)
            await pubsub.aclose()

    async def close(self) -> None:
        await self._client.aclose()
