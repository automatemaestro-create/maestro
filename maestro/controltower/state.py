"""Projection d'état de la Control Tower — tâches, agents, exécutions (ticket #46).

`ControlTowerState` matérialise l'**état courant** de l'orchestration à partir
du flux d'événements (`maestro.controltower.events`) : c'est la source des
endpoints REST (liste des tâches, état des agents, détail d'une exécution),
pendant que le WebSocket rediffuse le flux brut. Même modèle que l'UI
(docs/05) : un client charge l'état via le REST puis suit les événements.

La projection est **en mémoire** : elle vit avec le process de l'API et se
reconstruit en rejouant les événements. Sa durabilité vient du **journal**
(`maestro.controltower.persistence`, #97) : la pompe y consigne chaque
événement et le lifespan le rejoue au démarrage — l'état (exécutions, grands
livres, analytics, tâches, agents, validations) survit ainsi au redémarrage de
l'API, sans que cette classe ait à connaître le stockage. La persistance
PostgreSQL (entités TASK/RUN/AGENT de docs/03) viendra ensuite substituer un
stockage requêtable au rejeu intégral, sans changer le contrat des endpoints.

Toutes les mutations passent par `appliquer(event)`, appelé depuis la boucle
asyncio de l'API (un seul écrivain, pas de verrou nécessaire). L'application
d'un même événement de **réassignation** est idempotente — l'endpoint Kanban
l'applique immédiatement (cohérence du REST) *et* le publie sur le bus (d'où
une seconde application via la pompe de diffusion, sans effet).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from maestro.agents.capacity import INSTANCES_DEFAUT, CapaciteAgent
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_AGENT_CAPACITE,
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_DEMANDE,
    EVENEMENT_BRIEF_QUESTIONS,
    EVENEMENT_BRIEF_REPONSES,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_DETAIL,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_REFERENCE,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Brief,
    Event,
    ReferenceTicket,
)
from maestro.controltower.graphe import EtatNoeud, GrapheRun, graphe_du_run
from maestro.controltower.portee import PorteeProjet, PorteeRun
from maestro.controltower.progression import Progression, progression_des_statuts
from maestro.detail_tache import (
    EtapeTache,
    LienUtile,
    etapes_en_liste,
    liens_en_liste,
)
from maestro.engine.executor import STATUT_BLOQUEE, STATUT_ECHEC, STATUT_TERMINEE
from maestro.plan_run import NoeudPlan
from maestro.projets.application import DiffProjet
from maestro.references import ticket_en_dict
from maestro.sources.modele import Source, sources_en_liste
from maestro.telemetry.costs import RunCost, TaskCost
from maestro.telemetry.usage import StepUsage

#: Statuts d'agent exposés par l'API (docs/05 §2.1 : libre / occupé / désactivé…).
AGENT_LIBRE = "libre"
AGENT_OCCUPE = "occupe"

#: États portés par un événement `agent.capacite` (#86) : l'agent résultant est
#: activé (il reçoit des tâches) ou désactivé (il n'en reçoit plus).
CAPACITE_ACTIVE = "active"
CAPACITE_DESACTIVE = "desactive"

#: Statuts d'une demande de validation humaine (#48), alignés sur l'entité
#: APPROVAL (docs/03) : en attente de décision, puis approuvée ou refusée.
VALIDATION_EN_ATTENTE = "en_attente"
VALIDATION_APPROUVEE = "approuvee"
VALIDATION_REFUSEE = "refusee"

#: Statuts d'une **exécution** (#185, contrat #183) : en vol, menée à terme,
#: interrompue par un humain, ou soldée en échec (au moins une tâche échouée, ou
#: une planification impossible). Portés par l'événement `execution.statut` ;
#: `fin` reste None tant que le run est en cours.
EXECUTION_EN_COURS = "en_cours"
EXECUTION_TERMINEE = "terminee"
EXECUTION_ANNULEE = "annulee"
EXECUTION_ECHEC = "echec"

#: Le run **s'est arrêté sur son brief** et attend une décision humaine (#320,
#: décision D5) : aucune tâche n'est créée tant que rien n'est tranché. État
#: **non terminal** — le run est en vol, simplement suspendu —, ce qui le laisse
#: annulable comme n'importe quel run en cours : quelqu'un qui ne veut plus de ce
#: run n'a pas à l'approuver d'abord pour pouvoir l'arrêter.
EXECUTION_EN_ATTENTE_BRIEF = "en_attente_brief"

#: Le run **a posé les questions** de son brief et attend les réponses (#321), en
#: amont de la validation ci-dessus. État non terminal pour la même raison, et elle
#: est ici la troisième exigence du ticket : une attente de réponses peut durer, et
#: un run qu'on ne pourrait plus arrêter pendant ce temps serait indiscernable d'un
#: run planté. Distinct d'`en_attente_brief` parce que ce n'est pas la même attente —
#: on n'y attend pas une décision mais des réponses, et l'UI n'y présente pas le même
#: écran ; les confondre ferait proposer « approuver/refuser » à quelqu'un à qui on
#: pose des questions.
EXECUTION_EN_ATTENTE_REPONSES = "en_attente_reponses"

#: Le run **s'est arrêté sur un arbitrage de tâche** (#571) : une action sensible
#: attend qu'un humain l'approuve ou la refuse (#48), et rien de ce run n'avance
#: d'ici là. Troisième exemplaire du motif ci-dessus, et non un cas particulier —
#: même statut d'attente, même `attente_depuis`, même non-terminalité (le run
#: reste annulable pendant qu'il attend).
#:
#: Il vient tard parce qu'il manquait le chaînon : une demande de validation porte
#: sa **tâche**, jamais son run, si bien que la projection n'avait pas de quoi
#: savoir *quel* run attendait — #570 l'a posé sur la demande. Sans lui, un run
#: bloqué et un run qui travaille rendaient exactement la même réponse : statut
#: `en_cours`, 0 tâche, coût figé, cœur battant (#568, treize minutes de blocage
#: muet).
EXECUTION_EN_ATTENTE_ARBITRAGE = "en_attente_arbitrage"

#: Les trois états où le run est **suspendu sur un humain** (#320, #321, #571) :
#: en vol, mais rien ne bougera sans un geste. Rassemblés parce que plusieurs
#: endroits ont besoin de la question « ce run attend-il quelqu'un ? » —
#: l'ancienneté de l'attente se pose et se lève sur cet ensemble, et une vue qui
#: veut lister ce qui bloque n'a pas à connaître les trois noms.
STATUTS_EXECUTION_EN_ATTENTE = frozenset(
    {
        EXECUTION_EN_ATTENTE_BRIEF,
        EXECUTION_EN_ATTENTE_REPONSES,
        EXECUTION_EN_ATTENTE_ARBITRAGE,
    }
)

#: Statuts d'exécution **terminaux** : le run ne bouge plus, il n'est plus
#: interruptible (`POST /api/executions/{run_id}/annuler` répond alors 409).
STATUTS_EXECUTION_TERMINAUX = frozenset(
    {EXECUTION_TERMINEE, EXECUTION_ANNULEE, EXECUTION_ECHEC}
)

#: Les deux **ordres de pause** d'un run (#477), portés par `execution.statut` —
#: le canal de l'annulation (#444), et surtout pas un second transport : le guet
#: du process détaché est déjà branché là, et un run qui vit des heures n'a pas à
#: ouvrir une connexion de plus pour un fait qui passe par celle-ci.
#:
#: ⚠ Ce ne sont pas des **statuts**, et c'est la décision de ce lot. Une pause
#: n'est pas une étape du run : elle se superpose à ce qu'il fait. Un run peut
#: être suspendu *et* en attente de son brief (#320) — les deux sont vrais en même
#: temps, et écraser l'un par l'autre ferait perdre celui qui reste. C'est aussi
#: la panne qu'un statut `en_pause` aurait fabriquée : une pause demandée pendant
#: le cadrage aurait été effacée par la demande de brief qui suit, laissant une
#: porte fermée que plus rien à l'écran ne permettait de rouvrir. `appliquer` les
#: traite donc à part — ils posent `EtatExecution.en_pause` et **ne touchent ni au
#: statut ni à la fin**.
ORDRE_PAUSE = "pause"
ORDRE_REPRISE = "reprise"
ORDRES_PAUSE = frozenset({ORDRE_PAUSE, ORDRE_REPRISE})

#: Issues d'une décision sur un brief (#320). Volontairement **le vocabulaire des
#: validations** ci-dessus — approuver ou refuser se dit pareil : ce qui distingue
#: les deux mécanismes est le canal (`brief.*` vs `validation.*`) et ce qui y
#: voyage (un brief corrigé vs un booléen), pas le nom de l'issue. En inventer un
#: second obligerait chaque lecteur du flux à connaître deux tables pour un même
#: fait.
BRIEF_APPROUVE = VALIDATION_APPROUVEE
BRIEF_REFUSE = VALIDATION_REFUSEE

#: Statuts de tâche *terminaux* (machine à états docs/03 §3) : l'agent redevient
#: libre et les compteurs de la fiche agent s'incrémentent.
#:
#: **Public** depuis #466, pendant exact de `STATUTS_EXECUTION_TERMINAUX` : solder
#: un run demande de savoir lesquelles de ses tâches sont encore en vol, et cette
#: question ne peut pas avoir deux réponses. Une seconde liste, fût-elle recopiée
#: juste, se serait séparée de celle-ci au premier statut ajouté — et le premier
#: symptôme aurait été un agent libéré ici mais compté occupé là.
STATUTS_TACHE_TERMINAUX = frozenset({STATUT_TERMINEE, STATUT_ECHEC, STATUT_BLOQUEE})

#: Valeurs d'`Event.agent` qui ne désignent pas un exécutant réel (tâche bloquée
#: jamais exécutée : « — », routage sans élu…) : rien à mettre à jour côté agents.
_AGENTS_NON_EXECUTANTS = frozenset({"", "—"})


@dataclass
class EtatTache:
    """La ligne « tâche » de la projection : de quoi peupler une carte Kanban.

    Miroir léger de l'entité TASK + le coût de son dernier run (docs/05 §2.2 :
    titre, agent assigné, statut, coût). `cout_usd` reste None tant qu'aucune
    télémétrie n'a rapporté de coût (inconnu ≠ nul) ; `usage` en est la mesure
    détaillée (tokens entrée/sortie, coût, durée — #57), posée par le dernier
    passage de la tâche qui en a rapporté une. La ventilation par exécution
    reste du côté du grand livre du run (`EtatExecution.cout`). `ticket` porte
    la référence du ticket externe dont relève la tâche (#187, contrat #183) —
    None tant qu'aucune n'a été transportée par un événement (inconnu ≠ absent) ;
    posée par un événement de tâche, elle survit au rejeu du journal durable.
    `projet_id` porte le projet auquel la tâche appartient (#222) — None quand
    elle ne relève d'aucun projet, ce qui reste le cas courant : c'est ce champ
    que le Kanban filtre (`GET /api/taches?projet=…`). `description`, `etapes` et
    `liens` (#246, contrat #183) portent de quoi **comprendre** la tâche sans
    quitter l'écran — ce qu'elle demande, où elle en est, ce qu'il faut ouvrir
    pour la traiter. Vides tant qu'aucun événement n'en a transporté (inconnu ≠
    absent), et le panneau de détail (#251) ne s'ouvre alors pas : une tâche sans
    détail rend exactement la carte d'avant ce lot.
    """

    id: str
    titre: str = ""
    statut: str = ""
    agent: str = ""
    role: str = ""
    run_id: str = ""
    cout_usd: float | None = None
    usage: StepUsage | None = None
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None
    description: str = ""
    etapes: list[EtapeTache] = field(default_factory=list)
    liens: list[LienUtile] = field(default_factory=list)
    horodatage: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet la tâche en dict JSON-sérialisable (la forme du REST)."""
        return {
            "id": self.id,
            "titre": self.titre,
            "statut": self.statut,
            "agent": self.agent,
            "role": self.role,
            "run_id": self.run_id,
            "cout_usd": self.cout_usd,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "ticket": self.ticket.to_dict() if self.ticket is not None else None,
            "projet_id": self.projet_id,
            # `null` pour une description absente, `[]` pour des listes vides
            # (#246) : le client distingue ainsi « rien à montrer » d'une clé
            # qu'il ne connaîtrait pas, et le panneau de détail reste fermé.
            "description": self.description or None,
            "etapes": etapes_en_liste(self.etapes),
            "liens": liens_en_liste(self.liens),
            "horodatage": self.horodatage,
        }


@dataclass
class EtatAgent:
    """La ligne « agent » de la projection : statut, charge, compteurs et capacité.

    Alimente l'écran Agents (docs/05 §2.3) : occupé/libre, tâches en cours,
    tâches traitées, coût cumulé, dernière activité. Les agents du catalogue
    (`maestro.agents.catalog`) sont présents dès le démarrage, statut libre ;
    un acteur hors catalogue (l'orchestrateur) apparaît à sa première activité.
    `actif` et `instances` (#86, EF-21) reflètent le contrôle de capacité :
    un agent désactivé ne reçoit plus de tâches, `instances` plafonne ses
    exécutions simultanées — le `statut` reste, lui, l'activité (libre/occupé).

    En multi-instances (#100), un agent porte **plusieurs tâches à la fois** :
    `taches_en_cours` les liste dans l'ordre de démarrage — c'est la charge de
    l'écran Agents (docs/05 §2.1) — et l'agent ne redevient libre qu'à l'issue
    de la **dernière** ; `tache_courante` (la plus récemment démarrée encore en
    vol) reste exposée pour les clients mono-instance.
    """

    nom: str
    role: str = ""
    taches_en_cours: list[str] = field(default_factory=list)
    taches_terminees: int = 0
    taches_echouees: int = 0
    cout_usd: float | None = None
    derniere_activite: str = ""
    actif: bool = True
    instances: int = INSTANCES_DEFAUT

    @property
    def statut(self) -> str:
        """L'activité de l'agent : occupé dès qu'une tâche est en vol, libre sinon."""
        return AGENT_OCCUPE if self.taches_en_cours else AGENT_LIBRE

    @property
    def tache_courante(self) -> str:
        """La tâche la plus récemment démarrée encore en vol — vide si l'agent est libre."""
        return self.taches_en_cours[-1] if self.taches_en_cours else ""

    def commence(self, tache_id: str) -> None:
        """Enregistre `tache_id` parmi les tâches en vol (idempotent — un seul créneau)."""
        if tache_id and tache_id not in self.taches_en_cours:
            self.taches_en_cours.append(tache_id)

    def termine(self, tache_id: str) -> None:
        """Libère le créneau de `tache_id` — les autres instances restent en vol."""
        if tache_id in self.taches_en_cours:
            self.taches_en_cours.remove(tache_id)

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'agent en dict JSON-sérialisable (la forme du REST)."""
        return {
            "nom": self.nom,
            "role": self.role,
            "statut": self.statut,
            "tache_courante": self.tache_courante,
            "taches_en_cours": list(self.taches_en_cours),
            "taches_terminees": self.taches_terminees,
            "taches_echouees": self.taches_echouees,
            "cout_usd": self.cout_usd,
            "derniere_activite": self.derniere_activite,
            "actif": self.actif,
            "instances": self.instances,
        }


@dataclass
class EtatValidation:
    """Une demande de validation humaine (#48) : le contexte pour trancher, puis la décision.

    Miroir de la `DemandeValidation` du moteur (docs/03, entité APPROVAL) : la
    tâche mise en pause (id, titre, description — l'action que l'agent
    réaliserait), l'agent qui l'exécuterait et la `raison` de la classification
    sensible. `statut` suit `VALIDATION_*` ; `decision` porte le détail humain
    de l'issue (« approuvée depuis la Control Tower »…) une fois tranchée.

    `diff` (#227, EF-37) n'est renseigné que pour une demande d'**application
    dans le projet** : les fichiers que l'accord écrirait et la branche qu'il
    fusionnerait. C'est ce que l'UI affiche sous la question — « appliquer ces
    modifications ? » sans les montrer ne serait pas une validation, mais une
    signature à l'aveugle.

    `projet_id` (#277) est le projet de la **tâche** mise en pause, hérité de
    l'événement de demande : c'est ce qui rend le panneau des validations
    filtrable comme le Kanban. `None` quand la tâche ne relève d'aucun projet.

    `run_id` (#570) est le run que cette demande **retient**. Il répond à la
    question que le panneau ne pouvait pas poser — « quel run attend ? » — et
    c'est par lui que le run saura dire qu'il attend (#571). Vide quand la
    demande ne relève d'aucun run.

    `outil` et `arguments` (#581) portent l'**acte** qui a déclenché la demande,
    déjà expurgé par `evenement_demande`. Ce sont eux que l'écran d'arbitrage
    montre quand ils sont là — et le titre de la tâche seulement sinon : depuis
    que le déclencheur est l'acte (#573), afficher « Rédiger le README » au-dessus
    d'un `rm -rf` serait la même perte de sens que le défaut qu'on répare, dans
    l'autre sens. `outil` vide et `arguments` à None pour une demande qui n'en
    porte pas — validation de tâche, application d'un diff —, qui reste alors
    rendue exactement comme avant ce lot.

    `decideur` (#586) dit **qui doit trancher** cet acte : `auto`,
    `orchestrateur` ou `humain`. Vide pour une demande qui n'en porte pas, où
    l'écran retrouve exactement le rendu d'avant ce lot. Il ne remplace pas
    `statut` et ne s'en déduit pas : celui-là dit *où en est* la demande
    (en attente, approuvée, refusée), celui-ci *de qui* la réponse doit venir —
    une demande en attente d'orchestrateur et une demande en attente d'une
    personne portent le même statut et n'appellent pas le même geste.
    """

    tache_id: str
    titre: str = ""
    description: str = ""
    agent: str = ""
    role: str = ""
    raison: str = ""
    statut: str = VALIDATION_EN_ATTENTE
    decision: str = ""
    diff: DiffProjet | None = None
    projet_id: str | None = None
    run_id: str = ""
    outil: str = ""
    arguments: dict[str, str] | None = None
    decideur: str = ""
    horodatage: str = ""

    @property
    def en_attente(self) -> bool:
        """La demande attend-elle encore une décision humaine ?"""
        return self.statut == VALIDATION_EN_ATTENTE

    def to_dict(self) -> dict[str, Any]:
        """Réémet la demande en dict JSON-sérialisable (la forme du REST)."""
        return {
            "tache_id": self.tache_id,
            "titre": self.titre,
            "description": self.description,
            "agent": self.agent,
            "role": self.role,
            "raison": self.raison,
            "statut": self.statut,
            "decision": self.decision,
            "diff": self.diff.to_dict() if self.diff is not None else None,
            "projet_id": self.projet_id,
            "run_id": self.run_id,
            "outil": self.outil,
            "arguments": dict(self.arguments) if self.arguments is not None else None,
            "decideur": self.decideur,
            "horodatage": self.horodatage,
        }


@dataclass
class EtatExecution:
    """Le détail d'une exécution : les événements d'un `run_id`, dans l'ordre reçu.

    Pendant API du `RunJournal` (#8) : la trace consultable d'un run — étapes,
    statuts, coûts — reliée aux tâches par `tache_id`. `cout_usd` agrège les
    coûts rapportés par les événements du run ; `cout` en est la vue
    comptable (#57) : le grand livre du run, coût par tâche et agrégat.

    `objectif`, `statut` et `fin` portent le **cycle de vie du run** (#185) : ils
    sont posés par les événements `execution.statut` que publie le pilotage par
    l'API. Un run lancé **hors** de l'API (`maestro-run --publier`) n'en émet
    aucun : il apparaît quand même dans la projection (ses étapes portent son
    `run_id`), objectif inconnu et statut « en cours » — l'API n'a aucun signal
    de sa fin. Ces trois champs se reconstruisent au rejeu du journal durable
    (#97) comme le reste de la projection.
    """

    run_id: str
    evenements: list[Event] = field(default_factory=list)
    objectif: str = ""
    statut: str = EXECUTION_EN_COURS
    fin: str | None = None
    # Le ticket dont part le run (#187), posé par l'événement de lancement et
    # reconstruit au rejeu comme le reste — c'est ce qui l'a fait passer du
    # registre en mémoire du service de pilotage (#185) à la projection.
    ticket: ReferenceTicket | None = None
    # Le projet dans lequel le run travaille (#222), posé par le même événement
    # de lancement et hérité par ses tâches — None quand le run ne relève
    # d'aucun projet (le comportement d'avant ce lot).
    projet_id: str | None = None
    # La matière d'entrée de l'objectif (#315, EF-39), résolue et plafonnée au
    # lancement puis portée par son événement : c'est ce qui la fait survivre au
    # rejeu du journal durable. Vide pour un objectif purement textuel.
    sources: tuple[Source, ...] = ()
    # Le régime du brief de ce run (#320), posé par l'événement de lancement —
    # vide pour un run publié hors de l'API, qui n'en émet aucun.
    mode_brief: str = ""
    # Le brief soumis à validation, puis celui qui a été **retenu** (corrections
    # humaines comprises) : posé par `brief.demande` puis remplacé par
    # `brief.decision`. None tant que le run n'est pas passé par l'étape — c'est ce
    # que lit l'écran de validation (#322) pour savoir quoi présenter.
    brief: Brief | None = None
    # Depuis quand ce run attend un geste humain (#321) — l'horodatage de
    # l'événement qui l'a suspendu, None dès qu'il repart ou qu'il est soldé. C'est
    # l'**ancienneté** de la troisième exigence : sans elle, une attente est
    # indiscernable d'un run planté, et savoir *depuis quand* est ce qui permet d'en
    # juger. Posée pour les **trois** attentes (questions, décision sur le brief,
    # arbitrage de tâche #571) : c'est une seule question, elle mérite une seule
    # réponse.
    attente_depuis: str | None = None
    # Le rang de l'aller-retour de clarification en cours et le plafond annoncé
    # (#321) — 0 tant que le run n'en a joué aucun. C'est l'annonce du plafond, telle
    # que l'UI la rend : « tour 1 sur 2 ».
    tour_clarification: int = 0
    tours_clarification_max: int = 0
    # Le brief de ce run a-t-il été **approuvé par un humain** (#349) ? Posé par
    # `brief.decision` sur approbation, jamais retiré ensuite. C'est un fait distinct
    # de « `brief` est renseigné » : dès `brief.demande`, le champ ci-dessus porte le
    # brief *proposé*, si bien qu'un run mort **pendant** l'attente en a un sans que
    # personne ne l'ait validé. La relance (#349) a besoin de la différence — c'est
    # exactement ce qu'elle rejoue, et rejouer un brief non approuvé reviendrait à
    # sauter la validation que le run attendait encore.
    brief_approuve: bool = False
    # Le run dont celui-ci est la suite (#349) — vide pour un run qui ne reprend
    # personne, c'est-à-dire l'immense majorité. Posé par l'événement de lancement,
    # jamais retiré : c'est le pendant du fichier `reprise-de` d'un run
    # d'orchestration (#204), et il se lit dans le même sens (« ceci est la suite de
    # cela »), jamais dans l'autre.
    reprise_de: str = ""
    # **Pourquoi** ce run s'est arrêté (#479) — l'un des codes de
    # `maestro.controltower.causes`, vide tant qu'il n'y a rien à dire. Posé par
    # l'événement d'issue et **retiré** si le run repart : un run relancé après
    # un plafond de coût ne doit pas continuer d'afficher la cause de sa mort
    # précédente, qui est précisément ce qu'on vient de corriger.
    cause: str = ""
    # Ce run est-il **suspendu** (#477) ? Posé et retiré par les deux ordres de
    # pause, remis à False par tout statut terminal. Un drapeau **à côté** du
    # statut et non dedans, parce que la pause ne dit pas où en est le run mais
    # qu'on a arrêté de lui donner du travail : les deux faits coexistent, et un
    # run suspendu pendant l'attente de son brief doit continuer de montrer qu'il
    # attend ce brief — c'est ce qu'on regarde pour décider de le reprendre.
    en_pause: bool = False
    # Le **graphe du plan** (#490), posé une fois par `run.plan` et jamais
    # retiré : nœuds, arêtes, ossatures de checklist, tels que la décomposition
    # les a écrits. Vide pour un run qui n'en a pas publié — moteur antérieur à
    # ce lot, journal durable rejoué d'avant, ou planification jamais aboutie —,
    # et c'est ce vide qui vaut `plan_connu: false` au graphe servi. Il vit
    # **ici** et non sur les tâches, pour la raison qui a fait exister
    # `taches_vues` : un identifiant de tâche est un slug partagé dès que deux
    # runs décomposent le même objectif, donc une relance (#349) volerait ses
    # arêtes au run qu'elle reprend.
    plan: list[NoeudPlan] = field(default_factory=list)

    @property
    def debut(self) -> str:
        """L'horodatage du premier événement reçu pour ce run — vide si aucun.

        Pour un run lancé par l'API c'est son événement de lancement ; pour un
        run venu du journal d'un autre process, sa première étape consignée.
        """
        return self.evenements[0].horodatage if self.evenements else ""

    @property
    def taches_vues(self) -> frozenset[str]:
        """Les tâches que **ce run** a portées — son identité, lue dans ses événements.

        C'est la réponse à « quelles sont les tâches de ce run ? » (#473), et
        elle se lit ici plutôt que sur le `run_id` des tâches de la projection,
        qui ne porte que le **dernier** run les ayant touchées. Un identifiant de
        tâche est un slug engendré depuis son contenu, donc partagé dès que deux
        runs décomposent le même objectif — c'est le cas nominal d'une relance
        (#349), qui rejoue le brief approuvé. Lu là-bas, un run se ferait voler
        ses tâches par son propre successeur ; lu ici, son histoire ne change
        jamais rétroactivement.
        """
        return frozenset(
            e.tache_id
            for e in self.evenements
            if e.type == EVENEMENT_TACHE_STATUT and e.tache_id
        )

    @property
    def nb_taches(self) -> int:
        """Le nombre de tâches distinctes vues dans le run (0 avant planification)."""
        return len(self.taches_vues)

    def resume(self) -> dict[str, Any]:
        """Le **résumé** du run (`ResumeExecution` du contrat #183), sans sa trace.

        La forme que servent `GET /api/executions` et le lancement (#185) :
        identité, objectif, statut, volume, coût et bornes temporelles. `ticket`
        est le ticket externe dont part le run (#187) — lu **ici**, dans la
        projection, depuis l'événement de lancement : la référence voyage
        désormais sur le bus, donc elle survit au redémarrage de l'API comme le
        reste du résumé, là où le service de pilotage la tenait en mémoire.
        `projet_id` (#222) vient du même événement et suit le même chemin : il
        dit dans quel projet le run travaille, `null` s'il n'en relève d'aucun.
        `sources` (#315) aussi : ce que l'objectif embarquait, déjà résolu — une
        **liste vide** pour un objectif purement textuel, donc la vue d'avant ce
        lot pour tout ce qui n'en déclare pas.

        `mode_brief` (#320) est le **régime du brief** annoncé au lancement, et
        `statut` peut valoir `en_attente_brief` : le run s'est arrêté sur son brief
        et attend une décision. Le **brief lui-même** n'est pas ici mais dans le
        détail (`to_dict`), à dessein : `GET /api/executions` rend N résumés, et y
        embarquer sept sections de texte par run alourdirait la liste pour un
        contenu que seul l'écran d'un run donné regarde.
        """
        return {
            "run_id": self.run_id,
            "objectif": self.objectif,
            "statut": self.statut,
            "nb_taches": self.nb_taches,
            "cout_usd": self.cout_usd,
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
            "sources": sources_en_liste(self.sources),
            "mode_brief": self.mode_brief,
            # L'attente rendue **visible** (#321) : depuis quand, quel tour, sur
            # combien. Dans le **résumé** et non dans le seul détail, contrairement au
            # brief lui-même : c'est la liste des runs qui doit montrer lequel est
            # bloqué sur un humain, et trois scalaires n'ont pas le poids des sept
            # sections d'un brief. Les questions, elles, restent dans le détail — on
            # ne peut pas y répondre depuis une liste.
            "attente_depuis": self.attente_depuis,
            "tour_clarification": self.tour_clarification,
            "tours_clarification_max": self.tours_clarification_max,
            # Les deux faces de la relance (#349), dans le **résumé** et non dans le
            # seul détail, pour la même raison que l'attente ci-dessus : ce sont deux
            # scalaires, et c'est la **liste** des runs qui doit montrer lequel a du
            # cadrage à rejouer et lequel est déjà la suite d'un autre. Le brief
            # lui-même reste dans le détail — savoir qu'il a été approuvé ne demande
            # pas de le lire.
            "brief_approuve": self.brief_approuve,
            "reprise_de": self.reprise_de,
            # La pause (#477), dans le résumé pour la raison qui a mis l'attente
            # ici : c'est la **liste** des runs qui doit montrer lequel est
            # suspendu, et un booléen ne pèse rien. Il vient en plus du statut et
            # ne le remplace pas — un run suspendu reste `en_cours`, ou
            # `en_attente_brief`, ou ce qu'il était.
            "en_pause": self.en_pause,
            # La cause d'arrêt (#479) dans le **résumé**, et c'est le critère du
            # ticket : « dans la liste comme dans sa vue ». Un run en échec dont
            # il faut ouvrir la page pour savoir s'il a manqué de budget ou
            # d'hôte laisse la liste dire « Échec » à dix runs qui ne se
            # réparent pas de la même façon. Un code court n'y pèse rien, à la
            # différence du brief resté dans le détail.
            "cause": self.cause,
            "debut": self.debut,
            "fin": self.fin,
        }

    @property
    def cout_usd(self) -> float | None:
        """Coût cumulé rapporté par les événements du run (None si aucun)."""
        couts = [e.cout_usd for e in self.evenements if e.cout_usd is not None]
        return sum(couts) if couts else None

    @property
    def cout(self) -> RunCost:
        """Le grand livre du run (#57) : coût par tâche et agrégat, depuis les événements.

        Même convention d'attribution que `RunCost.depuis_journal` (#55),
        transposée au flux d'événements — le bus ne transporte pas le journal :
        l'activité sans tâche est la planification (l'orchestrateur), un
        `tache.statut` fait foi pour l'identité de sa tâche, activités et
        messages rattachés à une tâche (validation #48, message #44) fusionnent
        leur usage dans la sienne. Un événement qui ne rapporte qu'un
        `cout_usd` (producteur minimaliste) est compté pour ce seul coût.
        """
        planification = StepUsage()
        entrees: dict[str, TaskCost] = {}
        for event in self.evenements:
            if event.tache_id and (event.ticket is not None or event.projet_id is not None):
                # Ni le ticket externe (#187) ni le projet (#222) n'ont de coût :
                # ils se posent même sur un événement sans usage — dont le
                # `tache.reference`, qui n'en porte jamais — donc avant le filtre
                # ci-dessous. Un champ absent n'efface pas celui déjà en place.
                entree = entrees.get(event.tache_id) or TaskCost(tache_id=event.tache_id)
                entrees[event.tache_id] = replace(
                    entree,
                    ticket=event.ticket if event.ticket is not None else entree.ticket,
                    projet_id=(
                        event.projet_id if event.projet_id is not None else entree.projet_id
                    ),
                )
            usage = event.usage
            if usage is None and event.cout_usd is not None:
                usage = StepUsage(cout_usd=event.cout_usd)
            if usage is None:
                continue
            if event.type == EVENEMENT_TACHE_STATUT and event.tache_id:
                entree = entrees.get(event.tache_id) or TaskCost(tache_id=event.tache_id)
                entrees[event.tache_id] = replace(
                    entree,
                    nom=event.titre,
                    agent=event.agent,
                    role=event.role,
                    statut=event.statut,
                    usage=entree.usage.fusion(usage),
                )
            elif event.type == EVENEMENT_AGENT_ACTIVITE and not event.tache_id:
                planification = planification.fusion(usage)
            elif (
                event.type in {EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS}
                and event.tache_id
            ):
                entree = entrees.get(event.tache_id) or TaskCost(tache_id=event.tache_id)
                entrees[event.tache_id] = replace(
                    entree, usage=entree.usage.fusion(usage)
                )
        return RunCost(
            run_id=self.run_id,
            planification=planification,
            taches=tuple(entrees.values()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'exécution en dict JSON-sérialisable (la forme du REST).

        Le **résumé** du run (#185 : objectif, statut, volume, bornes) plus ce
        que le résumé n'a pas : le **brief** soumis ou retenu (#320, `null` si le
        run n'est pas passé par l'étape), le grand livre (#57) et la trace
        événement par événement — `GET /api/executions/{run_id}` sert donc l'état
        du run et sa trace d'un seul appel, et c'est là que l'écran de validation
        (#322) lit ce qu'il donne à relire.
        """
        return {
            **self.resume(),
            "brief": self.brief.to_dict() if self.brief is not None else None,
            "cout": self.cout.to_dict(),
            "evenements": [e.to_dict() for e in self.evenements],
        }


class ControlTowerState:
    """L'état courant de l'orchestration, reconstruit du flux d'événements.

    Quatre vues, chacune derrière un endpoint REST : `taches` (Kanban),
    `agents` (fiches et charge), `execution(run_id)` (détail d'un run),
    `validations` (demandes de validation humaine, #48). Plus deux **lectures
    dérivées** d'un run, qui ne stockent rien et se recomposent à chaque appel :
    `progression(run_id)` (#473) et `graphe(run_id)` (#490).
    """

    def __init__(
        self,
        agents: Sequence[Agent] = DEFAULT_AGENTS,
        capacites: Sequence[CapaciteAgent] = (),
    ) -> None:
        self._taches: dict[str, EtatTache] = {}
        self._agents: dict[str, EtatAgent] = {
            a.nom: EtatAgent(nom=a.nom, role=a.role) for a in agents
        }
        # Capacités persistées (#86) : les réglages déjà stockés (agents
        # désactivés, plafonds d'instances) sont visibles dès le démarrage,
        # comme le catalogue. Un réglage orphelin (agent disparu) est ignoré.
        for capacite in capacites:
            fiche = self._agents.get(capacite.nom)
            if fiche is not None:
                fiche.actif = capacite.actif
                fiche.instances = capacite.instances
        self._executions: dict[str, EtatExecution] = {}
        self._validations: dict[str, EtatValidation] = {}

    # ------------------------------------------------------------------ lecture

    def taches(
        self, portee: PorteeProjet | None = None, run: PorteeRun | None = None
    ) -> list[EtatTache]:
        """Les tâches connues, dans l'ordre de première apparition.

        `portee` (#277) est le périmètre de la lecture — c'est le filtre du
        Kanban, et c'est **le même objet** que consultent les exécutions, les
        validations, les analytics et la diffusion temps réel : la règle
        « appartient au projet demandé » n'est écrite qu'une fois
        (`PorteeProjet.retient`). Une tâche sans `projet_id` n'apparaît dans
        aucune vue de projet — on ne devine pas à quel projet elle
        appartiendrait —, elle n'apparaît que sous `tous` ou `aucun`.

        `run` (#473) est le périmètre **run**, et il s'**ajoute** au premier au
        lieu de le remplacer : un run appartient à un projet, les deux filtres se
        composent donc, et une portée de run sans portée de projet reste une
        question légitime. Les tâches retenues sont celles que le run a
        lui-même portées (`EtatExecution.taches_vues`), jamais celles dont le
        `run_id` le désigne — voir `PorteeRun` pour la différence, qui n'en est
        une que sur une relance, c'est-à-dire précisément quand elle compte.

        `None` reste, des deux côtés, la vue **transverse** : la projection est
        une structure de données, pas une API, et rien n'y justifie de refuser
        une question qui n'a pas dit son périmètre. Le refus (« rien plutôt
        qu'un mélange ») est le rôle des routes, qui ont un client à qui
        répondre.
        """
        taches = list(self._taches.values())
        if portee is not None:
            taches = [t for t in taches if portee.retient(t.projet_id)]
        if run is not None and not run.transverse:
            vues = self.taches_du_run(str(run.run_id))
            taches = [t for t in taches if run.retient(t.id, vues)]
        return taches

    def taches_du_run(self, run_id: str) -> frozenset[str]:
        """Les tâches que le run `run_id` a portées — vide s'il est inconnu (#473)."""
        execution = self._executions.get(run_id)
        return frozenset() if execution is None else execution.taches_vues

    def progression(self, run_id: str) -> Progression:
        """Où en est le run `run_id` : ses tâches réparties par compartiment (#473).

        Deux sources, et chacune est celle qui fait autorité sur sa moitié de la
        question. **Quelles** tâches : les événements du run
        (`EtatExecution.taches_vues`), si bien que `progression.total` vaut son
        `nb_taches` par construction — un résumé qui se contredirait sur le
        nombre de ses propres tâches serait pire qu'un résumé sans progression.
        **Dans quel état** : le statut que la projection tient pour chacune,
        c'est-à-dire celui de sa carte. Le run n'en garde donc pas une copie
        figée, et c'est voulu : `GET /api/taches?run=<id>` rend exactement ces
        cartes-là, si bien que la barre de progression et le Kanban d'un même
        écran comptent la même chose. Une tâche reprise avec succès par une
        relance se lit « terminée » des deux côtés plutôt que « échouée » d'un
        côté et « terminée » de l'autre.

        Un run inconnu rend une progression **vide** plutôt qu'une erreur : la
        projection répond à ce qu'elle sait, le refus motivé est le rôle des
        routes (`resoudre_portee_run`).
        """
        vues = self.taches_du_run(run_id)
        return progression_des_statuts(
            # Une tâche que la projection ne connaîtrait pas compte pour
            # `autres` plutôt que de disparaître du décompte : elle est déjà
            # comptée dans `nb_taches`, et l'écarter ici ferait mentir l'égalité
            # que la docstring promet — par soustraction, donc sans rien à voir.
            # Le cas n'existe pas aujourd'hui (tout `tache.statut` crée sa
            # carte) ; c'est bien pour ça qu'il doit rester impossible à
            # constater autrement.
            tache.statut if (tache := self._taches.get(tache_id)) is not None else ""
            for tache_id in vues
        )

    def graphe(self, run_id: str) -> GrapheRun:
        """Le **graphe** du run `run_id` : nœuds, arêtes, branches parallèles (#490).

        La jointure des deux moitiés, chacune lue là où elle fait autorité. Les
        **nœuds et les arêtes** viennent du plan que le run a publié
        (`EtatExecution.plan`), figé au moment de la décomposition ; l'**état** de
        chaque nœud vient de la carte que la projection tient de sa tâche —
        exactement celle que rend `GET /api/taches?run=<id>`, si bien que le
        graphe, le Kanban et la barre de progression d'un même écran ne peuvent
        pas se contredire.

        Un run **sans plan publié** ne rend pas un graphe vide : ses nœuds sont
        reconstruits de ses tâches vues, dans leur ordre d'apparition, sans
        aucune arête — et le graphe le **dit** (`plan_connu: false`). C'est le
        seul cas où les nœuds ne viennent pas du plan, et il fallait qu'il se
        distingue d'un plan réellement sans dépendance : les deux se dessinent
        pareil, on n'a pas le droit d'en conclure la même chose.

        Un run inconnu rend un graphe **vide** plutôt qu'une erreur, comme
        `progression` : la projection répond à ce qu'elle sait, le refus motivé
        est le rôle des routes.
        """
        execution = self._executions.get(run_id)
        plan = list(execution.plan) if execution is not None else []
        plan_connu = bool(plan)
        if not plan_connu:
            # Repli : les tâches que le run a portées, dans leur ordre de
            # première apparition — celui de `taches()`, et non celui de
            # `taches_vues`, qui est un `frozenset` et rendrait un graphe dont
            # l'ordre changerait d'un appel à l'autre.
            vues = self.taches_du_run(run_id)
            plan = [
                NoeudPlan(id=tache.id, titre=tache.titre)
                for tache in self._taches.values()
                if tache.id in vues
            ]
        return graphe_du_run(
            run_id,
            plan,
            {
                noeud.id: self._etat_noeud(noeud.id)
                for noeud in plan
                if noeud.id in self._taches
            },
            plan_connu=plan_connu,
        )

    def _etat_noeud(self, tache_id: str) -> EtatNoeud:
        """Ce que le graphe (#490) a besoin de savoir d'une tâche connue, et rien de plus.

        Le pendant exact de l'extraction des statuts pour la progression
        (#473) : la projection décide de quelles tâches elle parle et de leur
        état, le module feuille ne décide que du dessin. La **durée** se lit dans
        `usage`, où le moteur pose la durée horloge de la tâche (relances
        comprises) ; `None` tant que rien n'a été mesuré — inconnu n'est pas
        zéro, et une boîte annoncée à « 0 ms » se lirait comme instantanée.
        """
        tache = self._taches[tache_id]
        return EtatNoeud(
            statut=tache.statut,
            agent=tache.agent,
            role=tache.role,
            cout_usd=tache.cout_usd,
            duree_ms=tache.usage.duree_ms if tache.usage is not None else None,
            etapes=tuple(tache.etapes),
        )

    def tache(self, tache_id: str) -> EtatTache | None:
        """La tâche `tache_id`, ou None si inconnue de la projection."""
        return self._taches.get(tache_id)

    def agents(self) -> list[EtatAgent]:
        """Les agents connus : le catalogue, puis les acteurs apparus au fil des événements."""
        return list(self._agents.values())

    def agent(self, nom: str) -> EtatAgent | None:
        """L'agent `nom`, ou None s'il est inconnu (catalogue et événements confondus)."""
        return self._agents.get(nom)

    def execution(self, run_id: str) -> EtatExecution | None:
        """Le détail de l'exécution `run_id`, ou None si aucune trace reçue."""
        return self._executions.get(run_id)

    def executions(self, portee: PorteeProjet | None = None) -> list[EtatExecution]:
        """Les exécutions connues, dans l'ordre de première apparition (#87).

        Même convention que `taches` (#277) : la portée décide, un run sans
        projet ne figure dans aucune vue de projet, et `None` rend tout. C'est
        par ce paramètre que la vue coûts & analytics se restreint, sa matière
        étant ces mêmes exécutions.
        """
        executions = list(self._executions.values())
        if portee is None:
            return executions
        return [e for e in executions if portee.retient(e.projet_id)]

    def validations(self, portee: PorteeProjet | None = None) -> list[EtatValidation]:
        """Les demandes de validation humaine, dans l'ordre de première apparition.

        Filtrables par projet depuis #277, comme les tâches dont elles sont la
        mise en pause : une validation appartient au projet de sa tâche. Le
        panneau des validations d'une Control Tower cadrée sur un projet ne doit
        pas demander d'arbitrer une action qui se déroule ailleurs.
        """
        validations = list(self._validations.values())
        if portee is None:
            return validations
        return [v for v in validations if portee.retient(v.projet_id)]

    def validation(self, tache_id: str) -> EtatValidation | None:
        """La demande de validation de la tâche `tache_id`, ou None si aucune."""
        return self._validations.get(tache_id)

    # ----------------------------------------------------------------- écriture

    def ajouter_agent(self, nom: str, role: str) -> None:
        """Inscrit un agent dans la vue, fiche libre — l'acte de création du #72.

        Un agent personnalisé créé via l'API apparaît ainsi immédiatement dans
        `GET /api/agents` et devient cible de réassignation manuelle, sans
        attendre un redémarrage. Idempotent : une fiche existante garde ses
        compteurs, seul son rôle est rafraîchi.
        """
        fiche = self._agents.setdefault(nom, EtatAgent(nom=nom, role=role))
        fiche.role = role or fiche.role

    def retirer_agent(self, nom: str) -> None:
        """Retire la fiche d'un agent supprimé du catalogue (#72).

        La projection reste événementielle : une activité ultérieure portée par
        ce nom (trace d'un moteur encore câblé sur l'ancien catalogue) referait
        apparaître la fiche — la suppression n'efface pas l'histoire du flux.
        """
        self._agents.pop(nom, None)

    def appliquer(self, event: Event) -> None:
        """Projette `event` sur l'état : tâches, agents et trace d'exécution.

        Un type inconnu est tracé dans l'exécution (s'il porte un `run_id`)
        mais ne touche ni tâches ni agents — le flux peut s'enrichir sans
        casser la projection.
        """
        if event.run_id:
            execution = self._executions.setdefault(
                event.run_id, EtatExecution(run_id=event.run_id)
            )
            execution.evenements.append(event)
            if event.projet_id is not None and execution.projet_id is None:
                # Le projet du run (#222) est en principe posé par son événement
                # de lancement — mais un run publié hors de l'API
                # (`maestro-run --publier`, #87) n'en émet aucun : son
                # appartenance ne peut alors venir que de ses étapes, dont
                # chacune la porte. Le premier vu fait foi ; le lancement, quand
                # il existe, l'a déjà posée avant toute étape.
                execution.projet_id = event.projet_id
        if event.type == EVENEMENT_TACHE_STATUT:
            self._applique_statut_tache(event)
        elif event.type == EVENEMENT_TACHE_REASSIGNATION:
            self._applique_reassignation(event)
        elif event.type == EVENEMENT_TACHE_REFERENCE:
            self._applique_reference(event)
        elif event.type == EVENEMENT_TACHE_DETAIL:
            self._applique_detail(event)
        elif event.type in {EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS}:
            self._applique_activite(event)
        elif event.type == EVENEMENT_AGENT_CAPACITE:
            self._applique_capacite(event)
        elif event.type == EVENEMENT_VALIDATION_DEMANDE:
            self._applique_validation_demande(event)
        elif event.type == EVENEMENT_VALIDATION_DECISION:
            self._applique_validation_decision(event)
        elif event.type == EVENEMENT_RUN_PLAN:
            self._applique_run_plan(event)
        elif event.type == EVENEMENT_EXECUTION_STATUT:
            self._applique_execution_statut(event)
        elif event.type == EVENEMENT_BRIEF_DEMANDE:
            self._applique_brief_demande(event)
        elif event.type == EVENEMENT_BRIEF_DECISION:
            self._applique_brief_decision(event)
        elif event.type == EVENEMENT_BRIEF_QUESTIONS:
            self._applique_brief_questions(event)
        elif event.type == EVENEMENT_BRIEF_REPONSES:
            self._applique_brief_reponses(event)

    def _applique_statut_tache(self, event: Event) -> None:
        """Met à jour la tâche visée et la fiche de l'agent qui l'a portée."""
        tache = self._taches.setdefault(event.tache_id, EtatTache(id=event.tache_id))
        tache.statut = event.statut or tache.statut
        tache.titre = event.titre or tache.titre
        tache.agent = event.agent or tache.agent
        tache.role = event.role or tache.role
        tache.run_id = event.run_id or tache.run_id
        tache.horodatage = event.horodatage or tache.horodatage
        if event.cout_usd is not None:
            tache.cout_usd = event.cout_usd
        if event.usage is not None:
            tache.usage = event.usage
        if event.ticket is not None:
            tache.ticket = event.ticket
        self._pose_detail(tache, event)
        if event.projet_id is not None:
            tache.projet_id = event.projet_id
            # Une validation déjà déposée pour cette tâche (#277) suit son
            # appartenance : la demande peut précéder l'événement qui apprend le
            # projet, et une validation orpheline sortirait alors de la vue de
            # son propre projet.
            demande = self._validations.get(event.tache_id)
            if demande is not None and demande.projet_id is None:
                demande.projet_id = event.projet_id

        if event.agent in _AGENTS_NON_EXECUTANTS:
            return
        agent = self._agents.setdefault(
            event.agent, EtatAgent(nom=event.agent, role=event.role)
        )
        agent.derniere_activite = event.horodatage or agent.derniere_activite
        if event.statut in STATUTS_TACHE_TERMINAUX:
            # Multi-instances (#100) : seule l'instance de CETTE tâche se libère —
            # l'agent reste occupé tant qu'une autre de ses tâches est en vol.
            agent.termine(event.tache_id)
            if event.statut == STATUT_TERMINEE:
                agent.taches_terminees += 1
            elif event.statut == STATUT_ECHEC:
                agent.taches_echouees += 1
            if event.cout_usd is not None:
                agent.cout_usd = (agent.cout_usd or 0.0) + event.cout_usd
        else:
            # Statut non terminal (assignee, en_cours…) : l'agent est au travail.
            agent.commence(event.tache_id)

    def _applique_reassignation(self, event: Event) -> None:
        """Réassigne la tâche à l'agent visé (acte manuel du Kanban, EF-11/EF-20).

        Idempotent : réappliquer le même événement (application directe par
        l'endpoint puis rediffusion par la pompe) laisse l'état inchangé.
        """
        tache = self._taches.get(event.tache_id)
        if tache is None:
            return  # tâche inconnue : rien à réassigner (l'endpoint a déjà renvoyé 404)
        origine = tache.agent
        tache.agent = event.agent
        tache.role = event.role or tache.role
        tache.statut = event.statut or "assignee"
        tache.horodatage = event.horodatage or tache.horodatage

        # Répercute la réassignation sur les fiches agents (#52) : l'agent
        # d'origine rend le créneau de cette tâche (ses autres instances restent
        # en vol, #100), le nouvel agent la porte tant qu'elle n'est pas terminale.
        if origine != event.agent and origine not in _AGENTS_NON_EXECUTANTS:
            ancien = self._agents.get(origine)
            if ancien is not None:
                ancien.termine(event.tache_id)
        if event.agent in _AGENTS_NON_EXECUTANTS:
            return
        agent = self._agents.setdefault(
            event.agent, EtatAgent(nom=event.agent, role=event.role)
        )
        agent.derniere_activite = event.horodatage or agent.derniere_activite
        if tache.statut not in STATUTS_TACHE_TERMINAUX:
            agent.commence(event.tache_id)

    def _applique_reference(self, event: Event) -> None:
        """Rattache une tâche à son ticket externe (#187) — et **rien d'autre**.

        Le seul événement de tâche qui ne touche ni statut, ni agent, ni coût :
        un agent qui découvre en cours de route le ticket dont relève sa tâche
        (via le serveur MCP de son outil, #104) ne doit pas la faire bouger d'une
        colonne du Kanban en le disant. La tâche est **créée** si elle est encore
        inconnue — l'événement peut précéder la première étape consignée, et le
        ticket attend alors sa carte. Idempotent (poser le même ticket deux fois
        laisse l'état inchangé) ; un événement sans ticket lisible ne retire pas
        celui en place : on ne débranche pas un lien par accident.
        """
        if not event.tache_id or event.ticket is None:
            return
        tache = self._taches.setdefault(event.tache_id, EtatTache(id=event.tache_id))
        tache.ticket = event.ticket
        if event.projet_id is not None:
            # L'appartenance au projet (#222) voyage sur tous les événements de
            # tâche, celui-ci compris : la poser ici évite qu'une tâche connue
            # par ce seul chemin arrive sans projet dans les vues filtrées.
            tache.projet_id = event.projet_id
        tache.run_id = event.run_id or tache.run_id

    def _applique_detail(self, event: Event) -> None:
        """Renseigne le détail d'une tâche (#246) — et **rien d'autre**.

        Le pendant de `_applique_reference` pour ce qui se lit dans le panneau
        de détail (#251) : un agent qui découvre en cours de route une étape à
        cocher ou une maquette à ouvrir le dit sans faire bouger sa tâche d'une
        colonne du Kanban. La tâche est **créée** si elle est encore inconnue —
        l'événement peut précéder la première étape consignée.
        """
        if not event.tache_id:
            return
        tache = self._taches.setdefault(event.tache_id, EtatTache(id=event.tache_id))
        self._pose_detail(tache, event)
        if event.projet_id is not None:
            # Même raison qu'en `_applique_reference` : l'appartenance au projet
            # (#222) voyage sur tous les événements de tâche, celui-ci compris.
            tache.projet_id = event.projet_id
        tache.run_id = event.run_id or tache.run_id

    @staticmethod
    def _pose_detail(tache: EtatTache, event: Event) -> None:
        """Reporte sur la tâche le détail que l'événement **apprend**, et lui seul.

        Trois champs, une seule règle — celle du ticket externe (#187) : ce qui
        n'est pas renseigné ne **retire** pas ce qui est en place. Une
        description vide, `etapes`/`liens` à None (l'événement n'en parle pas)
        laissent la tâche inchangée, si bien que le flot ordinaire des
        `tache.statut` — qui ne porte aucun détail — ne vide pas un panneau
        renseigné par un `tache.detail` précédent. Poser deux fois le même
        détail est donc sans effet, et c'est ce qui rend le rejeu du journal
        durable (#97) idempotent.

        Une liste **présente mais vide** est, elle, une information : elle dit
        « plus aucune étape », et efface. C'est tout l'intérêt de distinguer
        `None` de `[]` sur l'événement.
        """
        if event.description:
            tache.description = event.description
        if event.etapes is not None:
            tache.etapes = list(event.etapes)
        if event.liens is not None:
            tache.liens = list(event.liens)

    def _applique_activite(self, event: Event) -> None:
        """Trace l'activité d'un acteur (planification, validation, message A2A)."""
        if event.agent in _AGENTS_NON_EXECUTANTS:
            return
        agent = self._agents.setdefault(
            event.agent, EtatAgent(nom=event.agent, role=event.role)
        )
        agent.derniere_activite = event.horodatage or agent.derniere_activite

    def _applique_capacite(self, event: Event) -> None:
        """Règle la capacité d'un agent (#86) : activé/désactivé, plafond d'instances.

        L'événement porte l'état **résultant** (statut `active`/`desactive`,
        `instances`) : le réappliquer (application directe par l'endpoint puis
        rediffusion par la pompe) laisse l'état inchangé — idempotence, comme
        la réassignation. Un champ absent ne touche pas la valeur en place.
        """
        if event.agent in _AGENTS_NON_EXECUTANTS:
            return
        agent = self._agents.setdefault(
            event.agent, EtatAgent(nom=event.agent, role=event.role)
        )
        if event.statut == CAPACITE_ACTIVE:
            agent.actif = True
        elif event.statut == CAPACITE_DESACTIVE:
            agent.actif = False
        if event.instances is not None:
            agent.instances = event.instances

    def _applique_run_plan(self, event: Event) -> None:
        """Pose le **graphe du plan** d'un run (#490) — et **rien d'autre**.

        Le pendant, pour le run, de ce que `_applique_reference` et
        `_applique_detail` font pour une tâche : l'événement n'apprend qu'une
        chose, il ne touche donc qu'à elle. Il ne crée aucune tâche, ne fait
        bouger aucune colonne du Kanban et ne change pas `nb_taches` — le plan
        annonce ce qui *sera* fait, les tâches d'un run restent celles que ses
        événements ont réellement portées (`taches_vues`), et confondre les deux
        ferait diverger `progression.total` de `nb_taches` pendant tout le run.

        Un plan **vide** ne remplace rien : le pont ne publie l'événement que sur
        un plan non vide, et si un producteur minimaliste en envoyait un, il
        n'apprendrait rien. Idempotent, donc rejouable : le journal durable (#97)
        reconstruit le graphe à l'identique au redémarrage de l'API.
        """
        execution = self._executions.get(event.run_id)
        if execution is None or not event.plan:
            return
        execution.plan = list(event.plan)

    def _applique_execution_statut(self, event: Event) -> None:
        """Pose le cycle de vie d'un run (#185) : objectif, statut, heure de fin.

        L'exécution existe déjà (`appliquer` l'a créée en rattachant l'événement
        à son `run_id`) : il ne reste qu'à porter l'état **résultant**. `fin` est
        posée sur un statut terminal et retirée si le run repasse en cours — un
        événement sans `run_id` est ignoré (rien à rattacher). Idempotent :
        réappliquer le même événement (application directe par le service puis
        rediffusion par la pompe) laisse l'état inchangé.

        Les deux **ordres de pause** (#477) empruntent le même événement et sortent
        **avant** tout le reste : ils ne disent pas où en est le run, seulement
        qu'on le suspend ou qu'on le reprend. Les laisser traverser la suite
        écraserait son statut par un mot qui n'en est pas un — et effacerait sa
        `fin`, son attente, tout ce qu'il portait par ailleurs.
        """
        execution = self._executions.get(event.run_id)
        if execution is None:
            return
        if event.statut in ORDRES_PAUSE:
            execution.en_pause = event.statut == ORDRE_PAUSE
            return
        execution.objectif = event.titre or execution.objectif
        execution.statut = event.statut or execution.statut
        if event.ticket is not None:
            # Le ticket dont part le run (#187) : posé par le lancement, jamais
            # retiré par les événements de fin, qui ne le portent pas.
            execution.ticket = event.ticket
        if event.projet_id is not None:
            # Le projet du run (#222) : même règle, posé au lancement et jamais
            # retiré par un événement qui ne le porte pas.
            execution.projet_id = event.projet_id
        if event.sources is not None:
            # Les sources de l'objectif (#315) : même règle encore. L'issue d'un
            # run n'en porte aucune, et n'a aucune raison d'effacer ce que son
            # lancement a déclaré.
            execution.sources = tuple(event.sources)
        if event.mode_brief:
            # Le régime du brief (#320) : même règle une fois de plus — annoncé par
            # le lancement, jamais retiré par l'issue, qui ne le porte pas.
            execution.mode_brief = event.mode_brief
        if event.reprise_de:
            # Le run repris (#349) : même règle, une dernière fois. Seul le lancement
            # d'une relance en porte un ; l'issue du run, qui n'en sait rien, ne doit
            # pas effacer de qui il était la suite.
            execution.reprise_de = event.reprise_de
        # La cause d'arrêt (#479) suit **exactement** le régime de `fin`, et pour
        # la même raison : elle ne vaut que d'un run soldé. Elle est donc posée
        # avec le statut terminal et **effacée** si le run repart — un run relancé
        # (#349) qui continuerait d'afficher le plafond qui l'avait tué la
        # première fois ferait chercher une panne qu'on vient de réparer.
        #
        # ⚠ Ce n'est **pas** la règle des champs ci-dessus (`ticket`, `projet_id`,
        # `sources`, `mode_brief`, `reprise_de`), qui sont posés au lancement et
        # que l'issue ne doit jamais effacer. Ici c'est l'inverse : seule l'issue
        # en parle, et un run qui n'est plus soldé n'a plus de cause.
        execution.cause = (
            event.cause if execution.statut in STATUTS_EXECUTION_TERMINAUX else ""
        )
        execution.fin = (
            event.horodatage if execution.statut in STATUTS_EXECUTION_TERMINAUX else None
        )
        if execution.statut in STATUTS_EXECUTION_TERMINAUX:
            # Un run soldé n'est plus suspendu (#477) : il n'y a plus rien à
            # reprendre, et laisser le drapeau derrière soi ferait proposer
            # « Reprendre » sur un run annulé pendant sa pause — le cas exact,
            # puisque `en_pause` n'empêche pas l'annulation.
            execution.en_pause = False
        # Le run n'attend plus dès qu'il n'est plus dans un état d'attente (#321) —
        # au premier chef l'**annulation en pleine attente**, qui est le cas que la
        # troisième exigence du ticket protège. Laisser l'ancienneté derrière soi
        # ferait afficher « en attente depuis 3 h » sur un run arrêté depuis.
        if execution.statut not in STATUTS_EXECUTION_EN_ATTENTE:
            execution.attente_depuis = None

    def _applique_brief_demande(self, event: Event) -> None:
        """Le run s'arrête sur son brief (#320) : statut suspendu, brief consultable.

        C'est **ici** que `en_attente_brief` est posé, et non par un
        `execution.statut` que le service émettrait en parallèle : la demande est
        déjà l'événement qui dit tout (quel run, quel brief), et la suspension est
        un fait du moteur, pas une décision du pilotage. Un run publié hors de l'API
        (`maestro-run --publier --brief humain`) obtient ainsi le même état que s'il
        avait été lancé depuis la Control Tower, sans rien émettre de plus.

        Un événement sans `run_id` est ignoré : il n'y a rien à suspendre.
        """
        execution = self._executions.get(event.run_id)
        if execution is None:
            return
        execution.statut = EXECUTION_EN_ATTENTE_BRIEF
        execution.fin = None
        execution.attente_depuis = event.horodatage
        if event.brief is not None:
            execution.brief = event.brief
        if event.mode_brief:
            execution.mode_brief = event.mode_brief

    def _applique_brief_decision(self, event: Event) -> None:
        """Tranche le brief d'un run (#320) : il repart, ou il s'arrête là.

        Sur **approbation** le run reprend (`en_cours`) et le brief projeté devient
        celui qui a été retenu — corrections humaines comprises : c'est lui qui part
        en décomposition, et l'état doit montrer ce qui a réellement été décomposé.
        Sur **refus** le run est `annulee` et sa fin est posée : le pilotage par
        l'API consignera la même issue en récupérant `BriefRefuse` (idempotent), et
        un run publié par un autre process n'a que cet événement-là pour le dire.

        Une décision sur un run qui n'attend pas (déjà tranché, déjà soldé) est
        **ignorée** plutôt qu'appliquée : jamais deux décisions, et surtout jamais
        de run terminé ramené en vol par une décision arrivée en retard. L'API le
        refuse déjà en 409 ; la projection ne s'en remet pas à elle, le même
        événement pouvant venir du bus (`maestro-run --publier`).
        """
        execution = self._executions.get(event.run_id)
        if execution is None or execution.statut != EXECUTION_EN_ATTENTE_BRIEF:
            return
        if event.brief is not None:
            execution.brief = event.brief
        execution.attente_depuis = None
        if event.statut == BRIEF_REFUSE:
            execution.statut = EXECUTION_ANNULEE
            execution.fin = event.horodatage
        else:
            execution.statut = EXECUTION_EN_COURS
            execution.fin = None
            # Le cadrage a été **payé et validé** (#349) : c'est ce fait-là, et non la
            # présence d'un brief, qui rend un run rejouable. Posé ici et nulle part
            # ailleurs — l'approbation est le seul moment où quelqu'un a dit oui — et
            # jamais retiré : un run approuvé puis mort reste un run approuvé, c'est
            # même toute la matière que la relance vient chercher.
            execution.brief_approuve = True

    def _applique_brief_questions(self, event: Event) -> None:
        """Le run pose les questions de son brief (#321) et attend les réponses.

        Pendant exact de `_applique_brief_demande`, sur l'autre attente : statut
        suspendu, brief consultable — ce sont **ses** questions qu'on affiche, jamais
        une copie —, ancienneté posée, et le tour annoncé avec son plafond.

        Le brief projeté est **remplacé** à chaque tour, à dessein : il a été
        régénéré entre-temps, donc les questions du tour précédent n'existent plus.
        C'est ce qui rend sûr l'appariement par position des réponses (`Clarification`)
        — l'UI répond toujours au brief que la projection montre.

        Un événement sans `run_id` connu est ignoré : il n'y a rien à suspendre.
        """
        execution = self._executions.get(event.run_id)
        if execution is None:
            return
        execution.statut = EXECUTION_EN_ATTENTE_REPONSES
        execution.fin = None
        execution.attente_depuis = event.horodatage
        execution.tour_clarification = event.tour
        execution.tours_clarification_max = event.tours_max
        if event.brief is not None:
            execution.brief = event.brief

    def _applique_brief_reponses(self, event: Event) -> None:
        """Les réponses sont arrivées (#321) : le run repart rédiger son brief.

        Il retourne en `en_cours` — et non vers une décision : ce qui suit est une
        **régénération du brief**, pas une décomposition. C'est la différence de
        nature qui a valu à ce canal d'exister à côté de `brief.decision`, et elle se
        lit ici : le run peut très bien revenir poser d'autres questions au tour
        suivant.

        `tour_clarification` est **gardé**, pas remis à zéro : c'est le compteur
        d'allers-retours joués par ce run, et il continue de dire ce qu'il a coûté
        une fois l'attente finie.

        Des réponses adressées à un run qui n'attend pas sont **ignorées** — jamais
        deux fois répondu, jamais un run soldé ramené en vol. L'API le refuse déjà en
        409 ; la projection ne s'en remet pas à elle, le même événement pouvant venir
        du bus (`maestro-run --publier`).
        """
        execution = self._executions.get(event.run_id)
        if execution is None or execution.statut != EXECUTION_EN_ATTENTE_REPONSES:
            return
        execution.statut = EXECUTION_EN_COURS
        execution.fin = None
        execution.attente_depuis = None

    def _applique_validation_demande(self, event: Event) -> None:
        """Enregistre une demande de validation humaine (#48) — en attente de décision.

        Une nouvelle demande sur la même tâche (autre run, re-tentative) remplace
        la précédente : le moteur attend toujours la décision de la **dernière**
        demande publiée, l'historique complet reste dans le journal (#8).

        Le **projet** (#277) se lit sur l'événement, ou à défaut sur la tâche déjà
        projetée. Depuis #570 c'est la **première** branche qui sert : la demande
        porte son projet, parce que la seconde ne pouvait pas tenir. Elle supposait
        que la tâche serait projetée avant la demande, or une validation qui garde
        le démarrage de sa propre tâche est publiée **avant** que cette tâche
        n'existe — le repli était en aval de ce qu'il devait réparer, et le cas
        qu'il ratait était le cas nominal (#568 : trois demandes sur trois, aucune
        rattachée, treize minutes de blocage muet).

        Le repli **reste** : un producteur qui ne porte rien — journal durable
        antérieur, double minimaliste d'un test, moteur tiers — retombe dessus, et
        `_applique_statut_tache` recolle encore l'appartenance quand la tâche
        l'apprend. Il n'est plus la source, il est le filet.
        """
        connue = self._taches.get(event.tache_id)
        projet_id = event.projet_id
        if projet_id is None and connue is not None:
            projet_id = connue.projet_id
        self._validations[event.tache_id] = EtatValidation(
            tache_id=event.tache_id,
            titre=event.titre,
            description=event.description,
            agent=event.agent,
            role=event.role,
            raison=event.detail,
            diff=event.diff,
            projet_id=projet_id,
            run_id=event.run_id,
            # L'acte (#581) est repris tel quel : il a été borné et expurgé à la
            # publication, et la projection est un miroir — elle ne rejuge rien.
            outil=event.outil,
            arguments=event.arguments,
            # Qui doit trancher (#586) — repris tel quel pour la même raison :
            # c'est un cran que nous avons posé, la projection n'a rien à en
            # rejuger.
            decideur=event.decideur,
            horodatage=event.horodatage,
        )
        self._suspend_sur_arbitrage(event.run_id, event.horodatage)

    def _applique_validation_decision(self, event: Event) -> None:
        """Tranche une demande de validation (#48) : approuvée ou refusée.

        Idempotent : réappliquer la même décision (application directe par
        l'endpoint puis rediffusion par la pompe) laisse l'état inchangé. Une
        décision sur une demande inconnue est ignorée — la projection est un
        miroir, pas une source de vérité.

        Le **run** que la demande retenait repart ici (#571), et son identité se
        lit sur la demande projetée plutôt que sur l'événement de décision : c'est
        `validation.demande` qui porte le run (#570), et la décision peut venir
        d'ailleurs — l'endpoint, une rediffusion, un journal durable rejoué. Une
        seule source, celle qui a suspendu.
        """
        demande = self._validations.get(event.tache_id)
        if demande is None:
            return
        demande.statut = (
            VALIDATION_APPROUVEE if event.statut == VALIDATION_APPROUVEE
            else VALIDATION_REFUSEE
        )
        demande.decision = event.detail or demande.decision
        demande.horodatage = event.horodatage or demande.horodatage
        self._libere_de_arbitrage(demande.run_id)

    def _suspend_sur_arbitrage(self, run_id: str, horodatage: str) -> None:
        """Le run s'arrête sur un arbitrage de tâche (#571) : statut et ancienneté.

        Pendant exact de `_applique_brief_demande` sur la troisième attente, et
        c'est tout le ticket : le motif existait deux fois et fonctionnait bien,
        il manquait ce troisième exemplaire. Sans lui, un run arrêté sur une
        action sensible restait `en_cours` — indiscernable d'un run qui travaille,
        y compris pour la vitalité, dont le cœur bat normalement (#568).

        Trois abstentions, chacune pour sa raison :

        - **sans run connu**, rien à suspendre — une demande venue d'un producteur
          qui ne porte pas son run (moteur antérieur à #570, double d'un test) est
          projetée comme avant, simplement sans rendre le run bavard ;
        - **sur un run soldé**, on ne remet rien en vol : une demande arrivée après
          coup — rediffusion, journal rejoué dans le désordre — ferait repartir un
          run annulé, et c'est la garde que `_applique_brief_decision` pose déjà
          sur l'autre bout de la chaîne ;
        - **sur un run qui attend déjà**, l'ancienneté ne se **réécrit pas**. Un run
          peut porter plusieurs demandes en vol (#568 : trois tâches sensibles sur
          trois), et « depuis quand attend-il ? » se répond depuis la **première** :
          la repousser à chaque nouvelle demande rajeunirait indéfiniment une
          attente qui dure, ce qui est exactement l'information qu'on vient poser.
        """
        execution = self._executions.get(run_id) if run_id else None
        if execution is None or execution.statut in STATUTS_EXECUTION_TERMINAUX:
            return
        if execution.statut == EXECUTION_EN_ATTENTE_ARBITRAGE:
            return
        execution.statut = EXECUTION_EN_ATTENTE_ARBITRAGE
        execution.fin = None
        execution.attente_depuis = horodatage

    def _libere_de_arbitrage(self, run_id: str) -> None:
        """Le run repart une fois l'arbitrage rendu (#571) — approbation **ou** refus.

        Les deux issues lèvent l'attente, et c'est le critère : un refus rend la
        main au moteur aussi sûrement qu'une approbation (il annule la tâche
        proprement), donc le run cesse d'attendre dans les deux cas. Ne garder que
        l'approbation laisserait un run refusé « en attente d'arbitrage » pour
        toujours, avec un compteur d'ancienneté qui court.

        Il repart en `en_cours`, et c'est bien **le statut qu'il avait** : une
        demande de validation ne naît que d'une tâche en vol, donc d'un run que son
        brief a déjà libéré — les deux autres attentes précèdent toute tâche. Rien
        n'est mémorisé pour un état qui ne peut pas se produire.

        Deux gardes :

        - la décision d'un run qui **n'attendait pas** ne le touche pas — run
          annulé pendant l'attente (son statut terminal a déjà effacé l'ancienneté,
          cf. `_applique_execution_statut`), ou décision rejouée une seconde fois ;
        - une **autre demande encore en attente** sur le même run le laisse
          suspendu. C'est la moitié qui manque à une lecture littérale de « les
          retire à la décision » : trancher la première de trois demandes rendrait
          au run un statut « en cours » qu'il ne mérite pas, et referait la promesse
          fausse que ce ticket supprime.
        """
        execution = self._executions.get(run_id) if run_id else None
        if execution is None or execution.statut != EXECUTION_EN_ATTENTE_ARBITRAGE:
            return
        if any(
            demande.run_id == run_id and demande.en_attente
            for demande in self._validations.values()
        ):
            return
        execution.statut = EXECUTION_EN_COURS
        execution.fin = None
        execution.attente_depuis = None
