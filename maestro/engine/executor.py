"""Exécution d'une tâche assignée — la brique que la boucle délègue (tickets #6, #41).

Isole de la boucle d'orchestration (`maestro.engine.loop`) tout ce qui concerne
l'exécution d'**une** tâche : routage vers l'agent compétent, garde-fous (#9),
production du livrable (runtime outillé ou appel texte), mesure d'usage (#8),
**fusion du travail dans le projet** (#705) et consignation au journal. La
boucle, elle, ne garde que l'ordonnancement (plan, dépendances, parallélisme) et
l'agrégation.

C'est ici que « solder une tâche » a lieu, donc ici que la branche
`maestro/<tâche>` rejoint la branche de travail du projet dès que la tâche
réussit (`_fusionne_dans_le_projet`) : le geste était écrit depuis #227 et
n'avait aucun appelant en production. Ce module ne le réimplémente pas — il le
**demande**, au seul instant où le verdict est connu et où la tâche suivante
n'a pas encore monté son worktree.

Cette frontière est **injectable** (`TaskExecutor`) : c'est elle qui permet de
remplacer l'exécution en process (`LocalExecutor`, comportement historique) par une
exécution **distribuée** via la file de tâches (ticket #41,
`maestro.queue.CeleryExecutor`) sans toucher à la boucle. Le contrat est identique
des deux côtés : `execute` ne lève jamais — un échec (routage, garde-fou,
exécution, transport) devient un `TaskResult` en échec, consigné au journal.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.capacity import CapacityStore, JaugeInstances
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import McpStore, ServeurMcp
from maestro.agents.permissions import (
    DecisionOutil,
    PermissionStore,
    PolitiqueOutils,
    Verdict,
)
from maestro.agents.playbooks import PlaybookStore, PlaybookVersion
from maestro.agents.runtime import AgentRuntime
from maestro.agents.secrets import SecretStore
from maestro.decideur import DECIDEUR_DEFAUT
from maestro.deliberation import (
    CreditArbitrage,
    Deliberation,
    MemoireArbitrage,
    cle_acte,
)
from maestro.detail_tache import EtapeTache, SuiviChecklist, consigne_detail
from maestro.engine.guardrails import (
    ORIGINE_AGENT,
    ORIGINE_POLITIQUE,
    DemandeValidation,
    Guardrails,
)
from maestro.engine.retry import PolitiqueRelance, est_transitoire
from maestro.messaging.mailbox import (
    MESSAGE_NOTIFICATION,
    AgentMessage,
    Mailbox,
    consigne_message,
)
from maestro.orchestrator.schema import Task
from maestro.projets.application import ApplicationRefusee, appliquer, diff_du_travail
from maestro.projets.modele import Projet
from maestro.projets.racine import RacineRefusee
from maestro.projets.store import ProjetStore
from maestro.providers.arbitrage import Arbitre, ArbitreActe
from maestro.providers.base import ModelProvider, UnsupportedCapability, stderr_de
from maestro.providers.courrier import Courrier
from maestro.router.classifier import TaskClassifier
from maestro.router.router import Router
from maestro.sandbox import ProducedFile, branche_de_tache
from maestro.telemetry import (
    PlafondDepense,
    PlafondDepenseDepasse,
    RunJournal,
    StepUsage,
    collect_usage,
)

#: Statuts terminaux d'une tâche, alignés sur la machine à états (docs/03 §3).
#: `bloquee` (#43) : la tâche n'a jamais été exécutée ni mise en file, une de ses
#: dépendances ayant échoué (ou été bloquée en cascade).
STATUT_TERMINEE = "terminee"
STATUT_ECHEC = "echec"
STATUT_BLOQUEE = "bloquee"

#: Statut *non terminal* d'une tâche en train de s'exécuter (docs/03 §3) — celui
#: que porte l'événement de début (#98) : la colonne « En cours » du Kanban.
STATUT_EN_COURS = "en_cours"

#: Suffixe des étapes de relance au journal (#91) : `<task.id>:relance`, une par
#: relance déclenchée — le pont Control Tower les mue en activités d'agent.
SUFFIXE_ETAPE_RELANCE = ":relance"

#: Suffixe des étapes de début d'exécution au journal (#98) : `<task.id>:debut`,
#: une par tentative — le pont Control Tower les mue en statuts `en_cours`.
SUFFIXE_ETAPE_DEBUT = ":debut"

#: Suffixe des étapes de refus d'outil au journal (#110) : `<task.id>:refus-outil`,
#: une par violation de la politique allow/deny — le pont Control Tower les mue
#: en activités d'agent. Le refus est propre : la tâche poursuit son cours.
SUFFIXE_ETAPE_REFUS = ":refus-outil"

#: Statut des étapes de refus d'outil (#110) — aligné sur le vocabulaire des
#: étapes annexes (`:validation` porte approuve/refuse) mais distinct : ici
#: c'est un appel d'outil qui est refusé, pas la tâche.
STATUT_REFUS_OUTIL = "refus_outil"

#: Statut des étapes d'**arbitrage** d'outil (#583) — même étape `:refus-outil`,
#: statut à part, et c'est la seule chose qui les sépare à l'écran.
#:
#: Il fallait le distinguer parce que le fil rend `refus_outil` par « <agent>
#: s'est vu refuser un outil », phrase **fausse** sur les deux tiers des issues
#: d'un arbitrage : un appel approuvé par une personne n'a pas été refusé, et un
#: appel écarté faute de décision ne l'a pas été non plus — sa demande est encore
#: en attente. Le motif, lui, dit laquelle des trois issues c'est
#: (`maestro.providers.arbitrage`), et c'est le `detail` que le fil affiche.
#:
#: Le **suffixe d'étape ne change pas** (`:refus-outil`) : le pont range déjà ces
#: étapes en activité d'agent sans les faire changer de colonne, et lui en donner
#: un second à connaître ferait deux vocabulaires à tenir d'accord pour une
#: distinction que le statut porte très bien.
STATUT_ARBITRAGE_OUTIL = "arbitrage_outil"

#: Suffixe des étapes de validation humaine au journal (#9) : `<task.id>:validation`.
#: Constante depuis #582, où ce suffixe a gagné un **second producteur** —
#: l'arbitrage demandé par l'agent lui-même : deux endroits qui composent le même
#: nom d'étape sont deux endroits à tenir d'accord, et le pont les reconnaît par
#: ce suffixe (`maestro.controltower.bridge`).
SUFFIXE_ETAPE_VALIDATION = ":validation"

#: Statuts d'une étape `:validation` — le vocabulaire de l'entité APPROVAL
#: (docs/03), commun aux deux producteurs : la classification d'une tâche
#: sensible et la demande de l'agent (#582) se lisent au même endroit avec les
#: mêmes mots. Ce qui les distingue est la **provenance** de la demande
#: (`maestro.engine.guardrails.ORIGINE_*`), jamais son issue.
STATUT_VALIDATION_APPROUVE = "approuve"
STATUT_VALIDATION_REFUSE = "refuse"

#: Suffixe des étapes d'activité au journal (#479) : `<task.id>:activite`, une
#: par salve publiée par le fournisseur pendant que la tâche tourne — le pont
#: Control Tower les mue en activités d'agent, comme `:relance` et
#: `:refus-outil`.
#:
#: C'est la **seule** étape du journal qui soit émise *pendant* une tâche plutôt
#: qu'à un de ses jalons : `:debut` ouvre, l'étape nue solde, et entre les deux
#: il n'y avait rien, quelle que soit la durée. Elle emprunte le canal existant
#: (journal → pont #46 → bus) et n'en ouvre aucun second : ce qui manquait
#: n'était pas un transport, c'était quelque chose à transporter.
SUFFIXE_ETAPE_ACTIVITE = ":activite"

#: Statut des étapes d'activité (#479) — un mot à lui, et c'est délibéré.
#:
#: Réutiliser `en_cours` était tentant (la tâche *est* en cours) mais rendait la
#: ligne fausse à l'écran : le fil habille un `en_cours` d'activité en « dev —
#: <titre de la tâche> en cours », c'est-à-dire qu'il redit ce que la carte du
#: Kanban montre déjà et **tait la salve**, seule information que la ligne
#: apporte. Un statut à part permet au fil de rendre le geste lui-même, ce que
#: le critère « l'écran montre l'activité en cours pendant qu'elle dure »
#: demande — sans quoi on aurait ajouté du trafic sans lever le silence.
#:
#: Il ne déplace aucune carte : le pont range ces étapes en `agent.activite`, que
#: la projection n'utilise que pour rafraîchir la dernière activité de l'agent
#: (`_applique_activite`) — jamais le statut d'une tâche.
STATUT_ACTIVITE = "activite"

#: Suffixe des étapes de **blocage déclaré par l'agent** (#719) :
#: `<task.id>:blocage`, une par appel de `signaler_blocage`
#: (`maestro.providers.blocage`) — le pont Control Tower les mue en événements
#: `tache.blocage`.
#:
#: ⚠ À ne pas confondre avec le blocage **hérité** de #43
#: (`maestro.engine.loop._consigne_blocage`), qui porte `STATUT_BLOQUEE` sur une
#: tâche que rien n'a jamais exécutée parce qu'une dépendance a échoué. Les deux
#: mots se ressemblent et disent le contraire l'un de l'autre : là-bas la tâche
#: est morte avant de commencer, ici **l'agent travaille et parle**.
#:
#: C'est pourquoi c'est une étape annexe et jamais un statut de tâche : docs/31
#: §3.4 **refuse** à un agent le droit de changer son propre statut, précisément
#: parce qu'un « bloquée » posé par lui condamnerait tout son aval par la cascade
#: de #43 alors qu'il peut encore aboutir. Il déclare ce qu'il **subit**, il ne
#: décide pas de son sort.
SUFFIXE_ETAPE_BLOCAGE = ":blocage"

#: Statut des étapes de blocage déclaré (#719) — un mot à lui, et il ne pouvait
#: pas être `bloquee`.
#:
#: `bloquee` est le statut de tâche de docs/03 §3, celui de la cascade de #43 :
#: le porter ici ferait lire « cette tâche est morte » là où la vérité est « son
#: agent bute et le dit, en travaillant encore ». La frise le rend tel quel
#: (`maestro.controltower.frise`), et une ligne qui annoncerait un abandon au
#: moment précis où quelqu'un demande de l'aide serait pire que le silence
#: qu'elle remplace.
#:
#: Il ne déplace aucune carte : le pont range ces étapes sous `tache.blocage`,
#: que la projection n'utilise que pour rafraîchir la dernière activité de
#: l'agent — jamais le statut d'une tâche.
STATUT_BLOCAGE_SIGNALE = "blocage_signale"

#: Suffixe des étapes de fusion dans le projet (#705) : `<task.id>:fusion`, une
#: par tâche soldée en succès sur un projet **versionné** — le pont Control Tower
#: les mue en activités d'agent, comme `:relance` et `:refus-outil`.
#:
#: Étape annexe et non statut de tâche, à dessein : ce qui arrive au projet de
#: l'utilisateur ne décide pas de l'issue de la tâche. Une fusion refusée ne
#: transforme pas en échec un travail qui a réussi (critère de #705) — elle se
#: **dit**, à l'endroit exact où le reste de la vie de la tâche se dit déjà.
SUFFIXE_ETAPE_FUSION = ":fusion"

#: Statuts d'une étape `:fusion` (#705) — les trois issues, et il en faut trois.
#:
#: `fusion_faite` : la branche `maestro/<tâche>` est dans la branche de travail,
#: le projet a avancé. `fusion_refusee` : Git ou le périmètre s'y est opposé — le
#: projet est **intact** et la branche conserve le travail, la phrase dit
#: laquelle des deux causes. `fusion_sans_objet` : la tâche a réussi sans rien
#: apporter au projet.
#:
#: Ce troisième-là est le moins évident et le plus important : le taire serait
#: reproduire exactement le défaut mesuré par #568 — un run vert dont la racine
#: reste vide, sans que rien nulle part ne le dise. « Rien n'est arrivé dans le
#: projet » est une information, pas une abstention nominale.
STATUT_FUSION_FAITE = "fusion_faite"
STATUT_FUSION_SANS_OBJET = "fusion_sans_objet"
STATUT_FUSION_REFUSEE = "fusion_refusee"

#: Les issues que #705 laissait **muettes**, et que #839 fait parler — parce que
#: « consignée dans les trois cas » était vrai de la fusion et faux de ses
#: abstentions : elles sortaient **avant** la consigne, si bien qu'un projet non
#: versionné ne produisait aucune étape `:fusion` et qu'un run de 8,80 $ pouvait
#: se solder vert sur une racine vide sans qu'une ligne le dise (mesure du
#: 2026-08-30 sur `cc2d8e447f83`, commentaire de #703).
#:
#: `fusion_non_tentee` : la tâche a échoué sur un projet versionné — rien n'est
#: fusionné, la branche conserve le travail commité au démontage. Distinct de
#: `fusion_sans_objet` (une tâche réussie qui n'apporte rien) : « rien à
#: fusionner » sur une tâche en échec enverrait chercher un diff vide là où c'est
#: la tâche qui est tombée. `ecriture_en_place` : projet non versionné, l'agent a
#: travaillé **dans la racine** — la phrase nomme ce qui y est. `ecriture_sans_objet` :
#: même régime, la tâche a réussi **sans rien y déposer** — la ligne qui répond
#: quand un run vert laisse le projet vide. `projet_introuvable` : la tâche
#: nomme un projet que le dépôt ne connaît plus — la tâche a travaillé dans un
#: `mkdtemp()` et rien n'a atteint aucune racine, ce qu'il faut dire plutôt que
#: laisser croire à un projet rempli.
STATUT_FUSION_NON_TENTEE = "fusion_non_tentee"
STATUT_ECRITURE_EN_PLACE = "ecriture_en_place"
STATUT_ECRITURE_SANS_OBJET = "ecriture_sans_objet"
STATUT_PROJET_INTROUVABLE = "projet_introuvable"

#: Nombre de chemins cités dans la phrase d'une écriture en place, au-delà duquel
#: on compte au lieu de lister — la ligne du journal est lue à l'écran, pas grepée.
_CHEMINS_CITES_MAX = 8

#: Délai de grâce accordé à l'annulation d'une réalisation en dépassement (#64) :
#: le temps, dans le cas nominal, que le SDK ferme son sous-processus. Au-delà,
#: la tâche est détachée — le time-out ne dépend jamais de sa coopération.
_GRACE_ANNULATION_S: float = 5.0


@dataclass(frozen=True)
class TaskResult:
    """Issue de l'exécution d'une tâche par l'agent qui lui a été assigné.

    Miroir léger de l'entité `RUN` (docs/03) pour le POC : qui a fait quoi, avec quel
    statut et quel livrable. `sortie` porte le livrable si `terminee`, `erreur` la
    cause si `echec` (auquel cas `sortie` est vide). `fichiers` porte les fichiers
    produits quand la tâche est passée par un runtime outillé (#35) — vide pour un
    livrable texte. `usage` porte le coût de la tâche (#8) : tokens, coût, durée,
    outils — durée horloge toujours mesurée, le reste selon ce que le fournisseur
    rapporte. `worker` identifie le worker qui a exécuté la tâche quand elle est
    passée par la file (#41) — vide en exécution locale. `playbook_version` (#78)
    est la version du playbook stocké avec laquelle l'agent a exécuté — None si
    l'agent a exécuté avec son prompt du code (playbook jamais édité, ou pas de
    dépôt câblé).
    """

    task_id: str
    titre: str
    agent: str
    role: str
    competences_requises: tuple[str, ...]
    score: int
    statut: str
    sortie: str
    erreur: str | None = None
    fichiers: tuple[ProducedFile, ...] = ()
    usage: StepUsage = StepUsage()
    worker: str = ""
    playbook_version: int | None = None

    @property
    def ok(self) -> bool:
        """La tâche s'est-elle terminée avec succès ?"""
        return self.statut == STATUT_TERMINEE

    def to_dict(self) -> dict[str, Any]:
        """Réémet le résultat en dict JSON-sérialisable."""
        return {
            "task_id": self.task_id,
            "titre": self.titre,
            "agent": self.agent,
            "role": self.role,
            "competences_requises": list(self.competences_requises),
            "score": self.score,
            "statut": self.statut,
            "sortie": self.sortie,
            "erreur": self.erreur,
            "fichiers": [f.to_dict() for f in self.fichiers],
            "usage": self.usage.to_dict(),
            "worker": self.worker,
            "playbook_version": self.playbook_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskResult:
        """Reconstruit un résultat depuis sa forme `to_dict` (aller-retour JSON, #41).

        C'est le chemin de **remontée des résultats** de la file de tâches : le
        worker renvoie `to_dict()`, l'orchestrateur reconstruit le `TaskResult`.
        """
        return cls(
            task_id=data["task_id"],
            titre=data["titre"],
            agent=data["agent"],
            role=data["role"],
            competences_requises=tuple(data.get("competences_requises", ())),
            score=data.get("score", 0),
            statut=data["statut"],
            sortie=data.get("sortie", ""),
            erreur=data.get("erreur"),
            fichiers=tuple(
                ProducedFile.from_dict(f) for f in data.get("fichiers", ())
            ),
            usage=StepUsage.from_dict(data.get("usage", {})),
            worker=data.get("worker", ""),
            playbook_version=data.get("playbook_version"),
        )


class TaskExecutor(ABC):
    """Frontière d'exécution d'une tâche assignable — le point d'injection du #41.

    La boucle d'orchestration ne connaît que ce contrat : elle confie une tâche
    (et les résultats de ses dépendances — le tableau noir) à l'exécuteur, qui
    rend un `TaskResult` **sans jamais lever** et consigne l'étape au `journal`.
    `LocalExecutor` exécute dans le process (comportement historique) ;
    `maestro.queue.CeleryExecutor` pousse la tâche dans la file Celery + Redis et
    attend le résultat d'un worker distant.
    """

    @abstractmethod
    async def execute(
        self, task: Task, dependances: Sequence[TaskResult], journal: RunJournal
    ) -> TaskResult:
        """Exécute `task` et renvoie son issue (échec consigné, jamais levé)."""
        raise NotImplementedError


class LocalExecutor(TaskExecutor):
    """Exécution en process : routage, garde-fous, production du livrable.

    C'est l'exécution historique de la boucle (#6/#35/#8/#9), extraite telle
    quelle : elle ne dépend que de `ModelProvider` et reste agnostique du
    fournisseur.
    """

    def __init__(
        self,
        provider: ModelProvider,
        *,
        agents: Sequence[Agent] = DEFAULT_AGENTS,
        runtimes: Mapping[str, AgentRuntime] | None = None,
        guardrails: Guardrails | None = None,
        router: Router | None = None,
        playbooks: PlaybookStore | None = None,
        capacites: CapacityStore | None = None,
        mcp: McpStore | None = None,
        secrets: SecretStore | None = None,
        permissions: PermissionStore | None = None,
        relance: PolitiqueRelance | None = None,
        projets: ProjetStore | None = None,
        mailbox: Mailbox | None = None,
    ) -> None:
        self._provider = provider
        # Messagerie inter-agents (#44) vue de l'exécuteur (#720) : la boîte sur
        # laquelle **notifier** le mot qu'un agent adresse à un pair. None — le
        # cas courant, un run se lance sans `--messagerie` — n'éteint pas le
        # verbe : le journal est la livraison, le pub/sub n'est que la
        # notification (docs/31 §3.2), donc un run sans transport écrit la trace
        # et n'a personne à prévenir. C'est exactement le régime du pair absent,
        # qui est le cas nominal et non le cas dégradé.
        self._mailbox = mailbox
        # Dépôt des projets (#224, EF-36) : quand une tâche porte un `projet_id`
        # (#222), le projet est relu ici — à chaud, comme les autres dépôts — et
        # l'espace de travail en est **dérivé** (worktree Git sur une branche
        # `maestro/<tâche>`, ou copie du périmètre) au lieu d'un répertoire vide.
        # None, ou tâche sans `projet_id` : le `mkdtemp()` historique.
        self._projets = projets
        # Un verrou par projet (#705) : les tâches d'un même run tournent de front
        # (le DAG de `maestro.engine.loop` lance tout ce qui n'attend personne),
        # mais leurs fusions visent une **seule** racine, que Git exige propre et
        # posée sur la branche de travail. Sans sérialisation, deux fusions
        # simultanées se refuseraient mutuellement en `racine-occupee` — un refus
        # qui ne dit rien du travail et perdrait une tâche sur deux.
        # Portée assumée : ce process. Les exécuteurs distribués (`CeleryExecutor`,
        # activité Temporal) montent un `LocalExecutor` par worker, donc par
        # process — un verrou de dépôt serait un autre sujet, et l'inventer ici
        # donnerait l'illusion de le traiter.
        self._verrous_projet: dict[str, asyncio.Lock] = {}
        self._boucle_verrous: asyncio.AbstractEventLoop | None = None
        # Serveurs MCP par agent (#104) : les déclarations sont relues à chaud
        # dans ce dépôt à chaque tâche — comme les playbooks (#78) — et montées
        # par la couche SDK sur les exécutions outillées de l'agent. None :
        # aucun serveur (comportement historique).
        self._mcp = mcp
        # Politiques de permissions par agent (#110) : allow/deny par outil (et
        # par serveur MCP), relues à chaud dans ce dépôt à chaque tâche et
        # appliquées à l'exécution — outils refusés retirés de la session,
        # serveurs MCP refusés jamais montés, le reste refusé au vol et tracé
        # (`:refus-outil`) sans condamner le run. None : aucune politique
        # (comportement historique).
        self._permissions = permissions
        # Coffre des secrets par agent (#109) : quand il est câblé ET provisionné,
        # les références ${VAR} des déclarations MCP se résolvent dans le coffre
        # de l'agent seulement — un agent ne voit que ses propres secrets. None,
        # ou coffre non provisionné : résolution dans l'environnement du process
        # (comportement historique #104).
        self._secrets = secrets
        # Relance automatique (#91, ENF-06) : les échecs transitoires de la
        # réalisation (aléa fournisseur — crash du sous-processus SDK, erreur
        # immédiate) sont relancés selon cette politique, avec backoff. None :
        # aucune relance (comportement historique). Les échecs non transitoires
        # (plafonds, refus de validation, time-out) ne sont jamais relancés.
        self._relance = relance
        # Application à chaud des playbooks (#78) : la version courante est relue
        # dans ce dépôt à chaque tâche — une édition publiée via la Control Tower
        # vaut pour l'exécution suivante, sans reconstruire l'exécuteur ni
        # redémarrer le process. None : prompts figés au câblage (agents/runtimes).
        self._playbooks = playbooks
        # Contrôle de capacité (#86, EF-21) : agents désactivés écartés du routage
        # et exécutions simultanées bornées au plafond d'instances, réglages relus
        # à chaud dans ce dépôt à chaque tâche. None : capacité illimitée
        # (comportement historique — tests et câblages sans Control Tower).
        self._capacites = capacites
        self._jauge = JaugeInstances()
        # Routage combiné (#42) : règles de compétences + classifieur léger adossé
        # au même fournisseur. Un routeur injecté remplace `agents` pour le routage.
        self._router = (
            router
            if router is not None
            else Router(tuple(agents), classifier=TaskClassifier(provider))
        )
        # Garde-fous (#9) : plafond de dépense par exécution (#56), time-out et
        # validation par tâche. Le défaut laisse plafond et time-out inactifs
        # mais garde la détection d'actions sensibles (refusées sans validateur).
        self._guardrails = guardrails if guardrails is not None else Guardrails()
        # Runtimes outillés, indexés par nom d'agent du catalogue. Par défaut, ceux
        # du POC (`developpeur`, `bdd`) adossés au même fournisseur que le moteur.
        self._runtimes = (
            dict(runtimes) if runtimes is not None else default_runtimes(provider)
        )

    async def execute(
        self, task: Task, dependances: Sequence[TaskResult], journal: RunJournal
    ) -> TaskResult:
        """Assigne, exécute et consigne une tâche ; renvoie son `TaskResult` (jamais d'exception).

        `dependances` porte les résultats (déjà acquis) des tâches dont celle-ci
        dépend — sa seule vue sur le reste de l'exécution, y compris en parallèle.
        La durée horloge est chronométrée autour de l'étape complète ; tokens, coût
        et outils sont récoltés auprès du fournisseur (`collect_usage`) quand il les
        signale — le collecteur vit dans le contexte de la tâche asyncio courante,
        donc sans fuite entre exécutions simultanées. Le tout est porté par le
        résultat et consigné au journal.

        Le collecteur est armé du **plafond de dépense** (#9), adossé à la
        comptabilité par tâche de l'exécution (#56) : la dépense confrontée au
        plafond est celle du run entier (étapes déjà consignées au `journal` +
        étape en cours), pas celle de la seule tâche — et une exécution au budget
        déjà épuisé ne démarre plus aucune tâche.

        La **délibération** (#584) naît ici et meurt avec la tâche : un crédit,
        qui tient le temps passé suspendu à une décision humaine, et une mémoire,
        qui tient les décisions rendues sur ses actes. Ici et pas plus bas, parce
        que les deux doivent survivre aux **relances** (#91) — une tâche relancée
        reprend sur la décision qu'elle avait obtenue, et une relance ne rend pas
        à la tâche le délai qu'elle a déjà consommé. Ici et pas plus haut, parce
        que ni l'un ni l'autre n'a de sens en dehors d'une tâche : deux tâches
        exécutées de front n'attendent pas la même personne sur le même acte.
        """
        debut = perf_counter()
        deliberation = Deliberation()
        entree = task.description
        # Un contrôle par exécution (#56) : il ne compte rien lui-même, il relit
        # le grand livre du `journal` à chaque mesure — planification et tâches
        # achevées comptent autant que la tâche courante.
        plafond = (
            PlafondDepense(
                journal,
                self._guardrails.plafond_cout_usd,
                plafond_tokens=self._guardrails.plafond_tokens,
            )
            if (
                self._guardrails.plafond_cout_usd is not None
                or self._guardrails.plafond_tokens is not None
            )
            else None
        )
        with collect_usage(plafond=plafond) as recolte:
            refus = _refus_plafond_creve(task, plafond)
            if refus is not None:
                result = refus
            else:
                decision = await self._router.route(task, exclus=self._desactives())
                if decision.agent is None:
                    # Repli explicite (#42) : tâche marquée « à assigner » plutôt que
                    # mal routée — l'assignation revient à un humain.
                    result = _echec(
                        task, agent="—", role="à assigner", score=decision.score,
                        erreur=decision.raison,
                    )
                else:
                    entree = _build_task_description(task, dependances)
                    # Application à chaud (#78, #104, #110) : playbook courant,
                    # serveurs MCP déclarés et politique de permissions sont
                    # résolus ici, à chaque tâche — jamais retenus entre deux
                    # exécutions. Une déclaration MCP ou une politique invalide
                    # (validées à la lecture) est un échec propre, avant toute
                    # exécution.
                    playbook = self._playbook_courant(decision.agent.nom)
                    try:
                        serveurs_mcp = self._serveurs_mcp(decision.agent.nom)
                        politique = self._politique_permissions(decision.agent.nom)
                    except ValueError as exc:
                        result = _echec(
                            task,
                            agent=decision.agent.nom,
                            role=decision.agent.role,
                            score=decision.score,
                            erreur=str(exc),
                        )
                    else:
                        # Contrôle de capacité (#86) : l'agent au complet retient
                        # la tâche jusqu'à la libération d'un créneau d'instance.
                        # Puis l'atelier du projet (#839) : une seule tâche à la
                        # fois dans la racine d'un projet non versionné — pris
                        # **après** le créneau et jamais avant, l'ordre inverse
                        # pouvant s'interbloquer (une tâche tenant l'atelier en
                        # attendant un créneau qu'une autre tient en attendant
                        # l'atelier). Hors de l'échéance de `_realise_gardee` :
                        # attendre son tour n'est pas travailler.
                        async with (
                            self._creneau_capacite(decision.agent.nom),
                            self._atelier_projet(task),
                        ):
                            result = await self._realise_gardee(
                                decision.agent,
                                task,
                                entree,
                                decision.score,
                                journal,
                                playbook,
                                serveurs_mcp,
                                politique,
                                deliberation,
                            )
                        if playbook is not None:
                            result = replace(result, playbook_version=playbook.version)
        # La part d'arbitrage voyage avec la durée horloge (#584) : c'est la même
        # mesure, décomposée. Elle est posée sur **cette** étape et sur aucune
        # autre — l'étape finale de la tâche est la seule qui porte sa durée, donc
        # la seule où « dont tant d'arbitrage » veuille dire quelque chose.
        result = replace(
            result,
            usage=recolte.total.avec_duree(
                _ecoule_ms(debut), arbitrage_ms=deliberation.credit.ecoule_ms()
            ),
        )
        # Le dernier mètre (#705) : la tâche est soldée, son worktree démonté et
        # sa branche écrite — c'est le seul instant où « dès qu'elle est soldée »
        # veut dire quelque chose. **Avant** l'étape terminale et non après : la
        # tâche n'est annoncée terminée qu'une fois le geste tenté, et la boucle
        # ne libère les tâches qui en dépendent qu'au retour de `execute`, donc
        # leur worktree part d'une branche de base qui porte déjà ce travail.
        await self._fusionne_dans_le_projet(task, result, journal)
        journal.consigne(
            etape=task.id,
            nom=task.titre,
            agent=result.agent,
            role=result.role,
            statut=result.statut,
            entree=entree,
            sortie=result.sortie,
            erreur=result.erreur,
            usage=result.usage,
            playbook_version=result.playbook_version,
            ticket=task.ticket,
            projet_id=task.projet_id,
            # La description que le plan porte déjà (#246) : le moteur ne fait
            # que la transporter, le journal étant le seul chemin par lequel
            # elle atteint le panneau de détail de la Control Tower (#251).
            # Étapes et liens ne viennent pas d'ici — ils se consignent en cours
            # d'exécution (`detail_tache.consigne_detail`), le plan n'en portant
            # aucun (`packages/shared/schemas/task.schema.json`).
            description=task.description,
        )
        return result

    def _desactives(self) -> frozenset[str]:
        """Les agents désactivés (#86), relus dans le dépôt à chaque tâche.

        C'est la moitié « ne reçoit plus de tâches » du contrôle de capacité :
        le routage les écarte des candidats — la tâche va au meilleur agent
        restant, ou en repli « à assigner ». Vide sans dépôt câblé.
        """
        if self._capacites is None:
            return frozenset()
        return self._capacites.inactifs()

    def _creneau_capacite(self, nom: str) -> AbstractAsyncContextManager[None]:
        """Un créneau d'exécution de l'agent `nom`, borné à son plafond d'instances (#86).

        Le plafond est relu dans le dépôt à chaque prise (application à chaud,
        comme les playbooks) : ajuster les instances depuis la Control Tower
        vaut pour la prochaine tâche disputée. Sans dépôt câblé, aucun plafond —
        comportement historique.
        """
        capacites = self._capacites
        if capacites is None:
            return nullcontext()
        return self._jauge.creneau(nom, lambda: capacites.lire(nom).instances)

    def _atelier_projet(self, task: Task) -> AbstractAsyncContextManager[None]:
        """L'atelier de `task` : la racine d'un projet non versionné, une tâche à la fois (#839).

        C'est le régime de concurrence du projet non versionné, **écrit** parce que
        le ticket l'exige : **sérialisation par projet**. Les trois options —
        sous-dossier par tâche, verrou, sérialisation — ont été pesées ainsi : un
        sous-dossier par tâche salirait le projet de l'utilisateur de dossiers
        nommés d'après des identifiants de tâche et empêcherait une tâche de
        partir du travail de la précédente (le défaut B2 de #568, recréé) ; un
        verrou par fichier ne protège pas d'un `rm`, d'un `mv` ni d'un
        `npm install` qui réécrit un arbre. Reste la sérialisation : c'est la
        réponse **exacte** à l'objection de D2 — « cinq agents en parallèle dans
        un même arbre » — sans en changer le support, et un projet non versionné
        est le plus souvent neuf et petit, donc la perte de parallélisme y coûte
        peu. Le worktree d'un projet **versionné** garde tout son parallélisme :
        les arbres sont séparés par construction, la fusion (#705) est le seul
        geste sérialisé, et c'est le **même verrou** (`_verrou_projet`) qui sert
        ici — les deux usages ne se rencontrent jamais, un projet étant l'un ou
        l'autre.

        Portée à connaître : le verrou est celui **de ce processus**. Deux runs
        lancés séparément sur le même projet non versionné (deux hôtes détachés,
        `maestro.controltower.hote_detache`) ne sont pas sérialisés entre eux —
        un verrou de fichier dans la racine salirait le projet, hors de la racine
        il faudrait l'indexer par poste ; ni l'un ni l'autre n'a été jugé
        nécessaire tant que la Control Tower lance un run à la fois par projet.

        `nullcontext()` — donc aucun effet — pour une tâche sans projet, un dépôt
        non câblé, un projet introuvable ou un projet versionné.
        """
        projet = self._projet(task)
        if projet is None or projet.versionne:
            return nullcontext()
        return self._verrou_projet(projet.id)

    def _serveurs_mcp(self, agent: str) -> tuple[ServeurMcp, ...]:
        """Les serveurs MCP déclarés pour `agent`, relus à chaque tâche (#104).

        Même application **à chaud** que les playbooks : une déclaration ajoutée
        ou corrigée dans le dépôt vaut pour la tâche suivante, sans redémarrage.
        Propage le `ValueError` de la validation à la lecture — l'appelant le
        mue en échec de tâche consigné. Vide sans dépôt câblé, ou pour un agent
        sans déclaration — comportement d'origine.
        """
        if self._mcp is None:
            return ()
        return self._mcp.lire(agent)

    def _politique_permissions(self, agent: str) -> PolitiqueOutils | None:
        """La politique allow/deny de `agent`, relue à chaque tâche (#110).

        Même application **à chaud** que les playbooks et les déclarations
        MCP : une politique ajoutée ou corrigée dans le dépôt vaut pour la
        tâche suivante, sans redémarrage. Propage le `ValueError` de la
        validation à la lecture — l'appelant le mue en échec de tâche
        consigné. None sans dépôt câblé, ou pour un agent sans politique —
        tout permis, comportement d'origine.
        """
        if self._permissions is None:
            return None
        return self._permissions.lire(agent)

    def _playbook_courant(self, agent: str) -> PlaybookVersion | None:
        """La version courante du playbook stocké de `agent`, relue à chaque tâche (#78).

        C'est la relecture par tâche qui rend l'édition applicable **à chaud** :
        aucun état de playbook ne vit dans l'exécuteur, la version publiée la plus
        récente au moment où la tâche démarre est celle qui exécute. None sans
        dépôt câblé, ou pour un agent jamais édité — l'exécution garde alors les
        prompts du code (catalogue et runtimes), comportement d'origine.
        """
        if self._playbooks is None:
            return None
        return self._playbooks.lire(agent)

    def _projet(self, task: Task) -> Projet | None:
        """Le projet dans lequel `task` travaille, relu à chaque tâche (#224, EF-36).

        Même relecture **à chaud** que les playbooks : le périmètre ou la branche
        de base corrigés depuis la Control Tower valent pour la tâche suivante.

        None dans trois cas, tous ramenés au **`mkdtemp()` d'avant** plutôt qu'à
        un échec : tâche sans `projet_id` (le critère explicite de #224), dépôt
        non câblé (tests et câblages sans Control Tower), et projet référencé mais
        absent du dépôt — un `projet_id` orphelin (projet oublié entre la
        planification et l'exécution) ne condamne pas la tâche, il la ramène au
        comportement qu'elle avait avant ce lot.
        """
        if task.projet_id is None or self._projets is None:
            return None
        return self._projets.lire(task.projet_id)

    async def _fusionne_dans_le_projet(
        self, task: Task, result: TaskResult, journal: RunJournal
    ) -> None:
        """Fusionne la branche de la tâche dans le projet dès qu'elle est soldée (#705, D2).

        Le geste manquant du parent #703 : `maestro.projets.application` savait
        déjà fusionner, `maestro.sandbox.projet` gardait déjà la branche — il n'y
        avait **aucun appelant en production** (défaut B1 de #568). Le voici, et
        c'est tout ce que ce lot ajoute : le calcul, le contrôle de périmètre et
        l'écriture restent là où ils vivent.

        **Et depuis #839, aucune issue n'est muette.** #705 tenait trois abstentions
        pour « sans rien à raconter » et sortait avant la consigne — or l'une des
        trois était exactement le défaut que #568 avait mesuré : un projet non
        versionné ne produisait **aucune** étape `:fusion`, et un run de 46 min
        pouvait se solder vert sur une racine vide sans qu'une ligne le dise. La
        seule abstention qui reste silencieuse est la tâche **sans projet** — il
        n'y a pas de racine dont parler, et une ligne « aucun projet » sur chaque
        tâche de chaque run sans projet serait du bruit sur tous les écrans. Tout
        le reste se **dit**, par une étape `:fusion` dont le statut nomme le cas :

        - **projet introuvable** — la tâche nomme un projet que le dépôt ne
          connaît plus ; elle a travaillé dans un `mkdtemp()` (règle de
          `_projet`) et rien n'a atteint aucune racine (`projet_introuvable`) ;
        - **projet non versionné** — l'agent a travaillé **dans la racine**
          (`maestro.sandbox.en_place`) : la phrase nomme ce qui y a été écrit
          (`ecriture_en_place`), ou dit que la tâche a réussi sans rien y déposer
          (`ecriture_sans_objet`) — c'est **cette** ligne qui manquait. Sur une
          tâche en échec, ce qui a été écrit avant la chute reste dans la racine
          et n'est pas recensé (le livrable d'un échec est vide) : la ligne le
          dit sans compter ;
        - **tâche en échec** sur un projet versionné — rien n'est fusionné et la
          branche reste, intacte, avec le travail que `_solder_la_branche` vient
          d'y commiter (`fusion_non_tentee`). C'est le critère de #705, et le
          seul sens possible : fusionner ce qu'on vient de déclarer raté écrirait
          l'échec dans le projet ;
        - **tâche réussie** sur un projet versionné — la fusion, comme avant.

        Le projet est **relu** ici plutôt que retenu de `_produce` : même
        application à chaud que les playbooks, et un projet supprimé entre-temps
        vaut « introuvable », pas échec.
        """
        if task.projet_id is None or self._projets is None:
            return
        projet = self._projet(task)
        if projet is None:
            self._consigne_fusion(
                task,
                result,
                STATUT_PROJET_INTROUVABLE,
                entree=f"projet {task.projet_id}",
                detail=(
                    f"projet {task.projet_id} introuvable dans le dépôt : la tâche a "
                    "travaillé dans un espace jetable et rien n'a atteint aucune racine."
                ),
                journal=journal,
            )
            return
        if not projet.versionne:
            statut, detail = _ecriture_en_place(projet, result)
            self._consigne_fusion(
                task,
                result,
                statut,
                entree=f"écriture en place dans {projet.racine}",
                detail=detail,
                journal=journal,
            )
            return
        branche = branche_de_tache(task.id)
        entree = f"fusion de {branche}"
        if not result.ok:
            self._consigne_fusion(
                task,
                result,
                STATUT_FUSION_NON_TENTEE,
                entree=entree,
                detail=(
                    f"tâche en échec : rien n'est fusionné — le projet est intact et "
                    f"{branche} conserve le travail commité au démontage."
                ),
                journal=journal,
            )
            return
        # Le travail est du sous-processus Git, pas de l'attente réseau : sans
        # `to_thread` il bloquerait la boucle, donc les tâches qui tournent de
        # front — et ce lot existe précisément pour qu'un run avance.
        async with self._verrou_projet(projet.id):
            statut, detail = await asyncio.to_thread(_fusion_du_travail, projet, branche)
        self._consigne_fusion(task, result, statut, entree=entree, detail=detail, journal=journal)

    def _verrou_projet(self, projet_id: str) -> asyncio.Lock:
        """Le verrou de fusion de `projet_id` — un seul merge à la fois par projet (#705).

        Les verrous sont indexés par projet **et remis à neuf quand la boucle
        d'événements change** : un `asyncio.Lock` se lie à la première boucle qui
        l'attend et refuse la suivante, or rien n'interdit de réutiliser un
        exécuteur d'un `asyncio.run` à l'autre. Repartir de zéro est sans risque —
        deux boucles distinctes n'ont, par construction, aucune fusion en vol à se
        disputer.
        """
        boucle = asyncio.get_running_loop()
        if self._boucle_verrous is not boucle:
            self._boucle_verrous = boucle
            self._verrous_projet = {}
        return self._verrous_projet.setdefault(projet_id, asyncio.Lock())

    def _consigne_fusion(
        self,
        task: Task,
        result: TaskResult,
        statut: str,
        *,
        entree: str,
        detail: str,
        journal: RunJournal,
    ) -> None:
        """Trace ce qui est arrivé au projet au journal (#705, #839) — donc au fil temps réel.

        Étape dédiée `<task.id>:fusion` (même modèle que `:relance` et
        `:refus-outil`), que le pont (`maestro.controltower.bridge`) mue en
        activité d'agent : ce qui est arrivé au projet se lit au moment où ça se
        produit, **sans faire changer la tâche de colonne**. `entree` porte le
        geste tenté (la branche et sa cible, ou la racine écrite en place),
        `sortie` ce qu'il en est advenu. Le suffixe reste `:fusion` pour une
        écriture en place : c'est le nom que le pont et l'écran connaissent, et
        la question posée est la même — *qu'est-ce qui est arrivé dans le
        projet ?* —, seul le statut dit par quel chemin.

        Consignée dans **tous** les cas, « rien » compris : c'est la seule ligne
        qui réponde à cette question, et la taire quand la réponse est « rien »
        rendrait invisible exactement le défaut que #568 a mesuré — un run vert
        sur une racine vide.

        Usage nul : le coût de la tâche est porté par son étape finale, et ni
        une fusion ni un recensement ne dépensent de modèle.
        """
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_FUSION}",
            nom=f"Projet — {task.titre}",
            agent=result.agent,
            role=result.role,
            statut=statut,
            entree=entree,
            sortie=detail,
            usage=StepUsage(),
            ticket=task.ticket,
            projet_id=task.projet_id,
        )

    async def _realise_gardee(
        self,
        agent: Agent,
        task: Task,
        description: str,
        score: int,
        journal: RunJournal,
        playbook: PlaybookVersion | None,
        serveurs_mcp: tuple[ServeurMcp, ...] = (),
        politique: PolitiqueOutils | None = None,
        deliberation: Deliberation | None = None,
    ) -> TaskResult:
        """Réalise la tâche sous garde-fous (#9) : validation humaine, puis time-out.

        Une tâche sensible non approuvée est stoppée **avant** toute exécution.
        Le plafond de dépense, lui, est armé plus haut (sur le collecteur d'usage
        de `execute`) — son dépassement remonte en exception du fournisseur, muée
        ici en échec comme les autres.

        Le time-out est une **échéance ferme** (#64) : la réalisation court dans sa
        propre tâche asyncio et l'attente est bornée par `asyncio.wait`, qui rend
        la main à l'échéance sans rien exiger de la tâche — là où `asyncio.timeout`
        restait suspendu quand le transport du SDK avalait l'annulation. À
        l'échéance, l'échec est consigné immédiatement ; l'annulation de la
        réalisation n'est qu'un vœu (`_annule_ou_detache`), jamais une attente.

        ⚠ **L'attente d'une décision humaine n'y est jamais comptée**, et depuis
        #584 ce n'est plus une affaire de placement. Cette fonction validait avant
        d'armer le délai, ce qui suffisait tant que le seul arbitrage possible
        portait sur le *texte* de la tâche. Le chantier #573 a déplacé le
        déclencheur sur l'**acte** : l'arbitrage vit maintenant dans l'appel
        d'outil, donc au cœur de la réalisation, donc dans l'échéance — et un
        `timeout_s` posé tuait une tâche en pleine question à l'opérateur.

        L'échéance est donc **repoussée du temps passé à délibérer**
        (`maestro.deliberation`), sur trois principes qui ne se déduisent pas les
        uns des autres :

        - elle se **recalcule** à chaque réveil au lieu de se poser une fois :
          le crédit n'existe pas encore au moment d'armer, il se gagne pendant ;
        - une échéance atteinte **pendant** une délibération ne conclut à rien —
          on attend le retour au repos, sinon la tâche mourrait entre l'instant où
          la question part et celui où le crédit la couvre ;
        - le crédit **déjà acquis avant l'armement** (la validation ci-dessus, qui
          n'a jamais couru sur le délai) ne compte pas : seul le delta est rendu.
          Le poser rendrait à la tâche du temps qu'elle n'a pas payé.
        """
        deliberation = deliberation if deliberation is not None else Deliberation()
        credit = deliberation.credit
        # La validation d'une tâche sensible (#9) est du temps d'arbitrage comme
        # un autre — le journal doit le dire (#584) —, mais elle précède
        # l'armement : le délai ne courait pas encore, il n'y a rien à lui rendre.
        # D'où la mesure ici et la remise à zéro du compteur au moment d'armer.
        with credit.attente():
            refus = await self._valide_si_sensible(agent, task, score, journal)
        if refus is not None:
            return refus
        timeout_s = self._guardrails.timeout_s
        if timeout_s is None:
            return await self._realise(
                agent, task, description, score, playbook, serveurs_mcp, politique,
                journal, deliberation,
            )
        realisation = asyncio.create_task(
            self._realise(
                agent, task, description, score, playbook, serveurs_mcp, politique,
                journal, deliberation,
            ),
            name=f"maestro-realisation:{task.id}",
        )
        acquis = credit.ecoule()
        debut = perf_counter()
        arbitre_ms = 0
        try:
            while True:
                if credit.en_attente():
                    # Quelqu'un délibère : l'échéance est suspendue, et il n'y a
                    # rien à recalculer avant qu'il ait tranché — attendre le
                    # `restant` d'ici là ferait tourner cette boucle pour rien,
                    # d'autant plus vite qu'il est court.
                    if await self._attend_repos(realisation, credit):
                        return realisation.result()
                    continue
                arbitre_ms = int((credit.ecoule() - acquis) * 1000)
                restant = timeout_s + (credit.ecoule() - acquis) - (perf_counter() - debut)
                if restant <= 0:
                    break
                fini, _ = await asyncio.wait({realisation}, timeout=restant)
                if realisation in fini:
                    return realisation.result()
        except asyncio.CancelledError:
            # Annulation externe (arrêt de l'exécution) : relayée à la réalisation.
            realisation.cancel()
            raise
        issue = await _annule_ou_detache(realisation)
        return _echec(
            task,
            agent=agent.nom,
            role=agent.role,
            score=score,
            erreur=(
                f"time-out : la tâche a dépassé {timeout_s:g} s"
                f"{_hors_arbitrage(arbitre_ms)} — {issue}."
            ),
        )

    @staticmethod
    async def _attend_repos(
        realisation: asyncio.Task[TaskResult], credit: CreditArbitrage
    ) -> bool:
        """Attend la fin de la réalisation ou celle de la délibération en cours (#584).

        Rend `True` si c'est la **réalisation** qui a fini — la seule question que
        l'appelant se pose ici : dans l'autre cas il n'a rien à conclure, il a
        juste une échéance à recalculer.

        Le guetteur du repos est **annulé dans un `finally`** : la boucle repasse
        ici à chaque délibération, et un guetteur oublié par tour laisserait
        derrière une tâche asyncio par arbitrage.
        """
        repos: asyncio.Task[None] = asyncio.ensure_future(credit.repos())
        try:
            fini, _ = await asyncio.wait(
                {realisation, repos}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            repos.cancel()
        return realisation in fini

    async def _valide_si_sensible(
        self, agent: Agent, task: Task, score: int, journal: RunJournal
    ) -> TaskResult | None:
        """Déclenche la validation humaine si la tâche est sensible (#9).

        Renvoie None si la tâche peut s'exécuter (anodine, ou approuvée) ; sinon le
        `TaskResult` d'échec de la tâche stoppée. La demande et la décision sont
        consignées au journal (étape dédiée `<task.id>:validation`, statuts alignés
        sur l'entité APPROVAL de docs/03), que la décision soit oui ou non.
        """
        raison = self._guardrails.raison_sensible(task)
        if raison is None:
            return None
        demande = DemandeValidation(
            task_id=task.id,
            titre=task.titre,
            description=task.description,
            agent=agent.nom,
            role=agent.role,
            raison=raison,
            # D'où vient la demande (#570) — même règle que la décision consignée
            # plus bas, et pour la même raison : ce sont des critères de filtre, et
            # ce qui ne les porte pas disparaît des vues. La demande est le cas où
            # ça coûte le plus cher : elle **précède** le premier `tache.statut` de
            # sa tâche, donc rien en aval ne peut recoller l'appartenance à sa place.
            run_id=journal.run_id,
            projet_id=task.projet_id,
            # Qui a demandé (#582) : ici, nous — c'est la classification du
            # moteur. Posée explicitement plutôt que laissée au défaut du champ :
            # ce chemin-ci *est* celui qui donne son sens à `ORIGINE_POLITIQUE`.
            origine=ORIGINE_POLITIQUE,
        )
        approuve, detail = await self._guardrails.demande_validation(demande)
        self._consigne_validation(
            task,
            agent,
            nom=f"Validation humaine — {task.titre}",
            raison=raison,
            approuve=approuve,
            detail=detail,
            journal=journal,
        )
        if approuve:
            return None
        return _echec(
            task,
            agent=agent.nom,
            role=agent.role,
            score=score,
            erreur=f"action sensible ({raison}) : {detail} — tâche stoppée avant exécution.",
        )

    def _arbitre(self, task: Task, agent: Agent, journal: RunJournal) -> Arbitre:
        """Le canal par lequel l'agent demande lui-même l'arbitrage (#582).

        Rend au fournisseur un `Arbitre` — une raison en entrée, `(approuvée ?,
        détail)` en sortie — qui **n'invente aucun garde-fou** : la demande part
        au `Guardrails` de l'exécuteur, celui-là même qui traite une tâche
        classée sensible. Le fail-safe est donc littéralement inchangé, parce que
        c'est le même code qui répond : sans validateur configuré, la demande de
        l'agent est refusée, comme n'importe quelle autre.

        Ce qui change est la **provenance** (`ORIGINE_AGENT`), et elle voyage sur
        les deux chemins que quelqu'un lira : le champ de la demande, qui atteint
        l'écran par le validateur (#48), et l'étape consignée ici. Sans elle, une
        déclaration d'agent se lirait comme une classification — or elles n'ont
        pas la même valeur : la nôtre tient quand l'agent se trompe ou se fait
        manipuler, la sienne ne prouve que ce qu'il a bien voulu dire.

        La décision **n'est pas mêlée** à celle de la tâche : la demande d'un
        agent porte sur une action qu'il s'apprête à commettre *dans* sa tâche,
        déjà autorisée à s'exécuter. Un refus lui est rendu et il poursuit sans
        l'action — jamais un `TaskResult` en échec, ce qui reviendrait à
        condamner une tâche pour la prudence de celui qui la mène.
        """

        async def arbitre(raison: str) -> tuple[bool, str]:
            demande = DemandeValidation(
                task_id=task.id,
                titre=task.titre,
                description=task.description,
                agent=agent.nom,
                role=agent.role,
                # La raison **est** l'action que l'agent décrit : c'est elle que
                # l'humain lit pour trancher (le validateur la rend en `detail`,
                # cf. `maestro.controltower.validation.evenement_demande`). Elle
                # est préfixée pour que la provenance survive aussi là où seul le
                # texte voyage — le champ `origine` reste la source.
                raison=f"arbitrage demandé par l'agent {agent.nom} : {raison}",
                run_id=journal.run_id,
                projet_id=task.projet_id,
                origine=ORIGINE_AGENT,
            )
            approuve, detail = await self._guardrails.demande_validation(demande)
            self._consigne_validation(
                task,
                agent,
                nom=f"Arbitrage demandé par l'agent — {task.titre}",
                raison=demande.raison,
                approuve=approuve,
                detail=detail,
                journal=journal,
            )
            return approuve, detail

        return arbitre

    def _courrier(self, task: Task, agent: Agent, journal: RunJournal) -> Courrier:
        """Le canal par lequel un agent écrit un mot à un pair (#720).

        Rend au fournisseur un `Courrier` — un destinataire, un message, rien en
        retour — qui fait **les deux gestes**, dans cet ordre :

        1. `consigne_message` : une étape `<task.id>:message` au journal du run,
           que le pont (#46) mue en `message.inter_agents` et que la frise (#355)
           reçoit sans travail de son côté ;
        2. `mailbox.publish` : la notification en direct, **best-effort**.

        ⚠ **L'ordre est inversé par rapport au handoff, et c'est le contenu de la
        décision.** `HandoffRelais.annonce` publie *puis* consigne, et abandonne
        tout — trace comprise — si la publication échoue : la trace y est
        conditionnée à la notification. Ici c'est l'inverse, parce que la réserve
        de docs/31 §3.2 le renverse : *le journal est la livraison, le pub/sub
        n'est que la notification*. Écrire d'abord donne au passage une propriété
        qu'on ne rattraperait pas autrement — un pair qui reçoit la notification
        est certain que la trace existe déjà.

        **Le pair absent est le cas nominal, pas le cas d'erreur**, et il ne
        produit même pas d'exception : le transport est un pub/sub éphémère, donc
        publier dans une boîte que personne n'écoute *réussit* et le message
        disparaît. Sans transport du tout (`mailbox=None`, le cas courant), il
        n'y a personne à prévenir et la trace est écrite tout de même. Un
        transport en panne, enfin, est **avalé** ici et non remonté : la
        promesse — l'écriture — est déjà tenue, et la seule chose que l'agent
        ferait d'un échec de notification serait de réessayer, c'est-à-dire de
        dupliquer la trace sur un canal qui n'a toujours pas de lecteur.

        Ce que l'agent **ne fournit pas** est ce dont il ne répond pas :
        l'expéditeur (`agent.nom`), la tâche (`task.id`) et le run
        (`journal.run_id`) sont fermés ici, comme dans `_arbitre`. Un agent qui
        les écrirait pourrait signer d'un autre nom, ou rattacher son mot à la
        tâche d'un tiers.

        Le type est `notification` et non `requete` : ce verbe **n'attend pas de
        réponse** — un canal « question » dont la réponse serait du texte est le
        sujet de #647, pas celui-ci —, et une `requete` promettrait une paire que
        rien ici ne referme. `payload` reste vide : il n'y a aucune charge utile
        structurée à porter, et `consigne_message` ne le lit pas.

        Rien de tout ceci ne touche au graphe du plan (docs/31 §5) : une étape de
        journal s'ajoute, aucune tâche n'est créée, aucun statut posé, personne
        n'est réassigné.
        """

        async def courrier(destinataire: str, message: str) -> None:
            mot = AgentMessage(
                type=MESSAGE_NOTIFICATION,
                de_agent=agent.nom,
                a_agent=destinataire,
                tache_id=task.id,
                run_id=journal.run_id,
                objet=message,
            )
            # La livraison d'abord — si celle-ci lève, l'agent doit l'apprendre
            # (le fournisseur lui sert `courrier.CANAL_EN_ERREUR`) : c'est la seule
            # promesse de ce verbe, et la seule dont l'échec change quelque chose.
            consigne_message(journal, mot, role=agent.role, projet_id=task.projet_id)
            if self._mailbox is None:
                return
            try:
                await self._mailbox.publish(mot)
            except Exception:  # noqa: BLE001 — la notification n'échoue ni l'appel ni la tâche
                return

        return courrier

    def _consigne_validation(
        self,
        task: Task,
        agent: Agent,
        *,
        nom: str,
        raison: str,
        approuve: bool,
        detail: str,
        journal: RunJournal,
    ) -> None:
        """Trace une décision de validation au journal (#9) — les deux provenances.

        Étape dédiée `<task.id>:validation`, statuts alignés sur l'entité
        APPROVAL de docs/03, que la décision soit oui ou non. Depuis #582 ce
        n'est plus la classification qui en est le seul producteur : l'arbitrage
        demandé par l'agent passe **par ici aussi**, et c'est voulu — le critère
        demande qu'il soit consigné « comme les autres ». Ce qui l'en distingue
        est le `nom` de l'étape, seul champ du journal qui puisse porter la
        provenance sans qu'un consommateur ait à la déduire d'une tournure de
        phrase.

        `entree` porte la raison, `sortie` le détail de la décision. Usage nul :
        délibérer ne dépense pas — le coût de la tâche est porté par son étape
        finale.
        """
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_VALIDATION}",
            nom=nom,
            agent=agent.nom,
            role=agent.role,
            statut=(
                STATUT_VALIDATION_APPROUVE if approuve else STATUT_VALIDATION_REFUSE
            ),
            entree=raison,
            sortie=detail,
            usage=StepUsage(),
            # Le projet (#222) est porté par **toutes** les étapes de la tâche,
            # annexes comprises : c'est un critère de filtre, et une étape qui ne
            # le porterait pas disparaîtrait des vues restreintes à ce projet.
            projet_id=task.projet_id,
        )

    async def _realise(
        self,
        agent: Agent,
        task: Task,
        description: str,
        score: int,
        playbook: PlaybookVersion | None,
        serveurs_mcp: tuple[ServeurMcp, ...],
        politique: PolitiqueOutils | None,
        journal: RunJournal,
        deliberation: Deliberation,
    ) -> TaskResult:
        """Produit le livrable de `task` et le mue en `TaskResult` (échec consigné, jamais levé).

        Chaque tentative s'ouvre sur une étape de **début** au journal (#98,
        `<task.id>:debut`) : la Control Tower voit la tâche passer « en cours »
        (agent, heure de début) dès qu'elle démarre — et redémarrer à chaque
        relance. Un échec **transitoire** de la production (aléa fournisseur : erreur
        immédiate, crash du sous-processus SDK, réponse vide) est **relancé**
        selon la politique (#91, ENF-06) — jusqu'à `max_tentatives` exécutions,
        backoff entre deux. Chaque relance est consignée au journal (étape
        `<task.id>:relance`, raison portée), donc visible au fil temps réel de
        la Control Tower ; le coût de **toutes** les tentatives est agrégé par
        le collecteur de `execute` et porté par l'étape finale de la tâche. Un
        échec **non transitoire** (`est_transitoire` : plafond de coût, plafond
        de tours, capacité absente) sort immédiatement — jamais relancé. Sous
        time-out (#64), l'échéance ferme borne la boucle entière, relances et
        attentes comprises : à l'échéance, aucune nouvelle tentative.

        Depuis #346, un échec dit **pourquoi** : le fournisseur accroche à son
        exception ce que le CLI a écrit sur stderr (`stderr_de`), et cette matière
        suit la cause jusqu'à l'étape `:relance` **et** jusqu'à l'échec final — donc
        jusqu'à l'événement d'activité de la Control Tower. Avant, un CLI qui
        mourait ne laissait que « Check stderr output for details » sur un flux que
        personne n'écoutait : la tâche était relancée, rééchouait, et l'incident se
        soldait sans qu'on sache s'il s'agissait d'une limite d'usage, d'un
        dépassement de contexte ou d'un plantage.

        La **checklist** de la tâche (#489) est tenue ici, et non dans la boucle
        de tentative : un `SuiviChecklist` par exécution, monté sur l'ossature que
        le plan déclare (`task.etapes`), posé une fois avant la première tentative
        et complété par les relevés de l'agent. Le placer plus bas le remettrait à
        neuf à chaque relance, c'est-à-dire ferait reculer l'avancement au moment
        précis où l'on a le plus besoin de savoir ce qui était déjà acquis.

        La **délibération** (#584) traverse cette boucle sans lui appartenir : elle
        vient de `execute`, pour la raison exacte qui fait tenir le `SuiviChecklist`
        ici plutôt qu'en dessous — une relance ne doit pas remettre à neuf ce qui
        était déjà acquis, et une décision humaine obtenue à la tentative 1 est ce
        qu'il y a de plus coûteux à redemander. C'est ce qui donne son sens à
        « la tâche relancée reprend sur elle ».
        """
        relance = self._relance
        max_tentatives = relance.max_tentatives if relance is not None else 1
        tentative = 1
        suivi = SuiviChecklist(task.etapes)
        # L'ossature part avant la première tentative : c'est ce qui donne à lire
        # la tâche pendant qu'elle démarre, là où l'agent n'a encore rien dit.
        # Muet quand le plan n'en déclare aucune (règle de #246).
        if not suivi.vide:
            self._consigne_etapes(task, agent, suivi.etapes(), journal)
        while True:
            self._consigne_debut(task, agent, tentative, max_tentatives, journal)
            try:
                sortie, fichiers = await self._produce(
                    agent, task, description, playbook, serveurs_mcp, politique, journal,
                    suivi, deliberation,
                )
            except Exception as exc:  # exécution: on consigne l'échec sans casser la boucle
                cause = str(exc)
                # #346 : ce que le CLI du fournisseur a écrit sur stderr voyage
                # accroché à l'exception — c'est la seule matière qui dise *pourquoi*
                # un sous-processus est mort. Elle suit l'échec jusqu'au journal.
                stderr_cli = stderr_de(exc)
                if not est_transitoire(exc):
                    return _echec(
                        task,
                        agent=agent.nom,
                        role=agent.role,
                        score=score,
                        erreur=_avec_stderr_cli(cause, stderr_cli),
                    )
            else:
                sortie = sortie.strip()
                if sortie or fichiers:
                    return TaskResult(
                        task_id=task.id,
                        titre=task.titre,
                        agent=agent.nom,
                        role=agent.role,
                        competences_requises=task.competences_requises,
                        score=score,
                        statut=STATUT_TERMINEE,
                        sortie=sortie,
                        fichiers=fichiers,
                    )
                # Le CLI a rendu la main sans rien produire : aucune exception, donc
                # aucun stderr accroché — l'échec n'a pas d'autre cause à donner.
                cause = "réponse vide de l'agent."
                stderr_cli = None
            if relance is None or tentative >= max_tentatives:
                return _echec(
                    task,
                    agent=agent.nom,
                    role=agent.role,
                    score=score,
                    erreur=_avec_stderr_cli(
                        cause if tentative == 1 else (
                            f"{cause} — échec transitoire persistant après "
                            f"{tentative} tentatives (relances épuisées)."
                        ),
                        stderr_cli,
                    ),
                )
            attente_s = relance.attente_s(tentative)
            self._consigne_relance(
                task, agent, tentative, max_tentatives, cause, stderr_cli, attente_s, journal
            )
            await asyncio.sleep(attente_s)
            tentative += 1

    def _consigne_debut(
        self,
        task: Task,
        agent: Agent,
        tentative: int,
        max_tentatives: int,
        journal: RunJournal,
    ) -> None:
        """Trace le début d'exécution au journal (#98) — donc au fil temps réel de la Control Tower.

        Étape dédiée `<task.id>:debut` (même modèle que `:validation` et
        `:relance`), que le pont (`maestro.controltower.bridge`) mue en statut
        de tâche `en_cours` : le Kanban voit la tâche démarrer (agent, heure de
        début) avant son issue. Rejouée à chaque tentative de relance (#91) —
        la carte « En cours » se rafraîchit au redémarrage. Usage nul : rien
        n'entre au grand livre avant l'issue de la tâche.
        """
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_DEBUT}",
            nom=task.titre,
            agent=agent.nom,
            role=agent.role,
            statut=STATUT_EN_COURS,
            entree="",
            sortie=(
                "démarrage de la tâche"
                if tentative == 1
                else f"redémarrage de la tâche (tentative {tentative}/{max_tentatives})"
            ),
            usage=StepUsage(),
            ticket=task.ticket,
            projet_id=task.projet_id,
            # Dès le démarrage (#246) : la carte s'ouvre pendant que la tâche
            # tourne, pas seulement une fois qu'elle est finie.
            description=task.description,
        )

    def _consigne_relance(
        self,
        task: Task,
        agent: Agent,
        tentative: int,
        max_tentatives: int,
        cause: str,
        stderr_cli: str | None,
        attente_s: float,
        journal: RunJournal,
    ) -> None:
        """Trace une relance au journal (#91) — donc au fil temps réel de la Control Tower.

        Étape dédiée `<task.id>:relance` (même modèle que `:validation`), que le
        pont (`maestro.controltower.bridge`) mue en activité d'agent. `entree`
        porte la **raison** (l'échec transitoire constaté), `sortie` le geste (la
        relance qui vient). Usage nul : le coût réel de toutes les tentatives est
        porté par l'étape finale de la tâche — pas de double compte au grand livre.

        `stderr_cli` (#346) est ce que le CLI du fournisseur a écrit avant de
        mourir — ou la mention explicite qu'il s'est tu. Il est **collé en fin**
        des deux champs, après le geste : le pont ne rend que `sortie` en `detail`,
        et une raison de plusieurs lignes coupée en deux par un « — relance dans
        2 s » se lit mal. C'est la matière qui manquait au diagnostic : sans elle,
        l'événement d'activité ne portait que « Check stderr output for details ».
        """
        geste = (
            f"échec transitoire (tentative {tentative}/{max_tentatives}) : {cause} "
            f"— relance dans {attente_s:g} s."
        )
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_RELANCE}",
            nom=f"Relance — {task.titre}",
            agent=agent.nom,
            role=agent.role,
            statut="relance",
            entree=_avec_stderr_cli(cause, stderr_cli),
            sortie=_avec_stderr_cli(geste, stderr_cli),
            usage=StepUsage(),
            projet_id=task.projet_id,
        )

    def _consigne_refus(
        self,
        task: Task,
        agent: Agent,
        outil: str,
        raison: str,
        journal: RunJournal,
        politique: PolitiqueOutils | None = None,
    ) -> None:
        """Trace une issue de politique d'outil (#110, #583) — donc au fil temps réel.

        Étape dédiée `<task.id>:refus-outil` (même modèle que `:validation` et
        `:relance`), que le pont (`maestro.controltower.bridge`) mue en
        activité d'agent : ce qui s'est joué sur l'appel est visible au moment
        où ça se produit — l'agent, lui, a reçu le motif et poursuit sa tâche.
        `entree` porte l'outil demandé, `sortie` le motif. Usage nul : le coût
        de la tâche est porté par son étape finale.

        Depuis #583 ce canal porte **deux natures** d'issue, et il faut les
        séparer parce que l'écran les rendrait autrement toutes les deux comme
        un refus : un appel écarté par la politique (`refus_outil`, le cas de
        #110) et l'issue d'un **arbitrage humain** (`arbitrage_outil`), qui vaut
        aussi bien pour un appel approuvé que refusé ou encore en attente.

        Laquelle des deux, c'est la **politique** qui le dit — `decide(outil)`,
        le verbe de #580 — et pas le texte du motif. Un `startswith` sur le motif
        aurait fait de la mise en forme la source de vérité de la
        classification : le jour où l'on change une phrase, la ligne change de
        nature en silence. La politique, elle, rend le même verdict au moment de
        consigner qu'au moment où le hook l'a rendu.

        Le **nom** de l'étape porte en plus le décideur (#586) — « Outil arbitré
        (orchestrateur) », « (humain) », « (auto) » —, et il le tient de la même
        source, pour la même raison. C'est le critère du ticket : *qui a tranché
        se lit, il ne se déduit pas*. Il vient là plutôt qu'ailleurs parce que
        c'est le seul champ du journal qui puisse porter une provenance sans
        qu'un consommateur ait à la deviner d'une tournure de phrase — c'est
        déjà lui qui distingue les deux producteurs d'une étape `:validation`
        (#582).
        """
        decision = (
            politique.decide(outil) if politique is not None else DecisionOutil(Verdict.REFUS)
        )
        arbitrage = decision.verdict is Verdict.ARBITRAGE
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_REFUS}",
            nom=(
                f"Outil arbitré ({decision.decideur}) — {task.titre}"
                if arbitrage
                else f"Outil refusé — {task.titre}"
            ),
            agent=agent.nom,
            role=agent.role,
            statut=STATUT_ARBITRAGE_OUTIL if arbitrage else STATUT_REFUS_OUTIL,
            entree=outil,
            sortie=raison,
            usage=StepUsage(),
            projet_id=task.projet_id,
        )

    def _arbitre_acte(
        self,
        task: Task,
        agent: Agent,
        journal: RunJournal | None,
        memoire: MemoireArbitrage | None = None,
        politique: PolitiqueOutils | None = None,
    ) -> ArbitreActe:
        """Le canal d'arbitrage **sur l'acte** confié au fournisseur (#583).

        C'est ici que l'arbitrage **sur l'acte** rejoint le garde-fou qui existe
        depuis #9 : on compose une `DemandeValidation` de plus — comme la
        validation d'une tâche (#48) ou l'application d'un diff (#227) — et on la
        soumet au **même** validateur. Le chantier #573 n'invente donc pas de
        mécanisme de décision humaine, il branche un déclencheur de plus sur
        celui qui existe, et hérite de son fail-safe : pas de validateur ⇒ refus,
        validateur en panne ⇒ refus (EF-08, ENF-04).

        Ce que la demande porte de neuf est l'**acte** : `outil` et `arguments`
        (#581) plutôt que le seul titre de la tâche. C'est tout l'objet du parent
        — « Rédiger le README » n'aide personne à trancher un `rm -rf`. La
        `raison`, elle, est le motif rendu par la politique : il nomme l'outil et
        la liste qui l'a mis en arbitrage.

        Le fournisseur reste seul à borner l'attente : il répond avant l'échéance
        de son propre runtime, et ce n'est pas quelque chose que l'exécuteur peut
        garantir à sa place (`maestro.providers.arbitrage`).

        ⚠ **Ce n'est pas `_arbitre`** (#582), qui relaie la demande d'un agent
        ayant levé la main. Deux différences qui se voient dans le code : la
        provenance est `ORIGINE_POLITIQUE` — c'est **nous** qui avons classé
        l'outil, l'agent n'a rien déclaré —, et l'issue ne se consigne pas ici.

        Elle part par `on_refus` (étape `:refus-outil`, statut
        `arbitrage_outil`) et non par une étape `:validation`, pour une raison
        qui n'est pas un oubli : le hook a **trois** issues, dont « toujours en
        attente », qui n'est l'issue d'aucune validation — la personne n'a pas
        tranché. Les ranger sous `approuve`/`refuse` obligerait la troisième à se
        déguiser en l'une des deux.

        `memoire` (#584) est ce qui fait que la troisième issue n'est plus
        définitive. La demande passe désormais par elle, indexée sur l'**acte** et
        non sur la tâche (`maestro.deliberation.cle_acte`), et cela répond à deux
        questions d'un coup : un appel rejoué sur le même acte retrouve la
        décision arrivée après que le hook a cessé d'attendre, et deux appels
        simultanés sur le même acte partagent une seule demande au lieu d'en
        empiler deux devant l'opérateur.

        L'acte et non la tâche, parce que c'est ce qu'une personne tranche
        réellement : approuver `rm build/` n'approuve pas `rm /`, et indexer par
        `tache_id` — ce que fait la projection de la Control Tower — ferait
        hériter le second de la décision rendue sur le premier. C'est la seule
        façon que « retrouver une décision » ne devienne pas « en réutiliser une
        autre ».

        Sans mémoire, chaque demande repart de zéro : le comportement exact de
        #583, celui qu'ont les appelants qui ne composent pas de délibération.

        `politique` (#586) est ce qui permet de dire **qui tranche**. Le cran
        n'est pas transporté depuis le hook mais **redemandé** à la politique,
        exactement comme `_consigne_refus_outil` lui redemande son verdict
        plutôt que de le lire dans un texte : c'est la même source, elle rend la
        même réponse, et un argument de plus dans `ArbitreActe` serait un
        contrat à faire évoluer chez tous les fournisseurs pour une valeur déjà
        disponible ici. Sans politique, aucun outil n'est classé `ask` — il n'y
        a rien à arbitrer, et ce canal n'est pas câblé.
        """
        memoire = memoire if memoire is not None else MemoireArbitrage()

        async def arbitre(
            outil: str, arguments: dict[str, str], motif: str
        ) -> tuple[bool, str]:
            demande = DemandeValidation(
                task_id=task.id,
                titre=task.titre,
                description=task.description,
                agent=agent.nom,
                role=agent.role,
                raison=motif,
                # D'où vient la demande (#570) : sans run ni projet, elle sort du
                # journal du run et de toutes les vues, qui sont cadrées dessus.
                run_id="" if journal is None else journal.run_id,
                projet_id=task.projet_id,
                outil=outil,
                arguments=arguments,
                # Qui a demandé (#582) : nous. Un outil classé `ask` est une règle
                # à nous, pas un aveu de l'agent — et c'est ce qui fait tenir le
                # garde-fou quand l'agent se trompe ou se fait manipuler.
                origine=ORIGINE_POLITIQUE,
                # Qui doit trancher (#586) : le cran posé dans la politique. Sans
                # politique, `humain` — le défaut du champ, qui escalade au lieu
                # de s'auto-approuver ; ce chemin n'est de toute façon pas câblé
                # dans ce cas.
                decideur=(
                    DECIDEUR_DEFAUT
                    if politique is None
                    else (politique.decideur(outil) or DECIDEUR_DEFAUT)
                ),
            )
            return await memoire.tranche(
                cle_acte(outil, arguments),
                lambda: self._guardrails.demande_validation(demande),
            )

        return arbitre

    def _consigne_activite(
        self,
        task: Task,
        agent: Agent,
        texte: str,
        journal: RunJournal,
    ) -> None:
        """Trace une salve d'activité au journal (#479) — donc au fil temps réel.

        Étape dédiée `<task.id>:activite` (même modèle que `:relance` et
        `:refus-outil`), que le pont (`maestro.controltower.bridge`) mue en
        activité d'agent. `sortie` porte ce que le fournisseur a composé — un
        geste, ou une salve qui annonce son regroupement. Usage nul : le coût de
        la tâche est porté par son étape finale, et une salve n'est pas un appel
        modèle de plus.

        C'est ce qui supprime le silence d'une tâche longue. Le reste du
        dispositif était déjà là : `:debut` disait qu'elle démarrait, l'étape nue
        qu'elle avait fini, et le Kanban ne bougeait pas entre les deux parce que
        rien ne lui parlait — pas parce qu'il ne savait pas écouter.

        `texte` vide n'est pas consigné : une salve sans contenu ne dit rien, et
        une ligne vide au fil d'activité se lirait comme une panne d'affichage.
        """
        if not texte.strip():
            return
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_ACTIVITE}",
            nom=task.titre,
            agent=agent.nom,
            role=agent.role,
            statut=STATUT_ACTIVITE,
            entree="",
            sortie=texte,
            usage=StepUsage(),
            projet_id=task.projet_id,
        )

    def _consigne_blocage_signale(
        self,
        task: Task,
        agent: Agent,
        raison: str,
        journal: RunJournal,
    ) -> None:
        """Trace le blocage qu'un agent **déclare** (#719) — donc au fil temps réel.

        Étape dédiée `<task.id>:blocage` (même modèle que `:activite` et
        `:refus-outil`), que le pont (`maestro.controltower.bridge`) mue en
        événement `tache.blocage`. `sortie` porte la raison telle que l'agent l'a
        écrite : c'est **le seul signal qu'aucune règle de détection ne saura
        produire**. Une règle sait dire « bloquée depuis 40 minutes » ; elle ne
        saura jamais dire « le dépôt de recette refuse mes identifiants ».

        Usage nul, et c'est un critère du ticket : déclarer ne dépense rien, donc
        rien n'entre au grand livre — le pont écarte de lui-même la mesure de ces
        étapes, comme il le fait déjà pour `:ticket` et `:detail`.

        La tâche ne **change pas de colonne** au passage (cf.
        `SUFFIXE_ETAPE_BLOCAGE`) : un agent qui bute n'est pas une tâche bloquée,
        et c'est tout ce qui sépare ce verbe de celui que docs/31 §3.4 refuse.

        `raison` vide n'est pas consignée — mais on ne devrait pas l'y voir : le
        fournisseur l'a déjà écartée et l'a dit à l'agent
        (`maestro.providers.blocage.RAISON_MANQUANTE`). Le contrôle est ici quand
        même parce que ce chemin a **deux entrées** — l'outil MCP, et un appelant
        direct — et qu'une ligne de frise vide se lirait comme une panne
        d'affichage (règle de `_consigne_activite`).
        """
        if not raison.strip():
            return
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_BLOCAGE}",
            nom=f"Blocage signalé — {task.titre}",
            agent=agent.nom,
            role=agent.role,
            statut=STATUT_BLOCAGE_SIGNALE,
            entree="",
            sortie=raison,
            usage=StepUsage(),
            projet_id=task.projet_id,
        )

    def _consigne_etapes(
        self,
        task: Task,
        agent: Agent,
        etapes: Sequence[EtapeTache],
        journal: RunJournal,
    ) -> None:
        """Pose la checklist de la tâche au journal (#489) — par `consigne_detail` (#246).

        Le **chemin existant**, et pas un second transport : `consigne_detail`
        écrit une étape `<task.id>:detail`, le pont (#46) la mue en événement
        `tache.detail`, la projection la pose sur la carte et le panneau de détail
        (#251) la rend. Toute cette plomberie était posée depuis #246 et attendait
        un appelant — c'est celui-ci.

        La tâche ne **change pas de colonne** au passage : `tache.detail` ne porte
        que le détail, jamais un statut. Une checklist qui se coche est un
        avancement *dans* la tâche, pas une tâche qui avance.

        Rien n'entre au grand livre : poser une case à cocher ne dépense pas, et
        le pont écarte de lui-même l'usage des étapes de détail.
        """
        consigne_detail(
            journal,
            task.id,
            etapes=[etape.to_dict() for etape in etapes],
            agent=agent.nom,
            role=agent.role,
        )

    def _on_etapes(
        self,
        task: Task,
        agent: Agent,
        suivi: SuiviChecklist,
        journal: RunJournal,
    ) -> Callable[[Sequence[EtapeTache]], None]:
        """Le canal par lequel un relevé de l'agent devient une checklist consignée (#489).

        Réconcilie d'abord (`SuiviChecklist.rapporte` : l'ossature supplantée au
        premier relevé, puis la fusion monotone), et ne consigne que si la
        checklist a **changé** — un agent rappelle volontiers sa liste à
        l'identique, et republier à chaque fois coûterait une ligne de journal et
        un événement de bus pour rien.
        """

        def signale(relevees: Sequence[EtapeTache]) -> None:
            etapes = suivi.rapporte(relevees)
            if etapes is not None:
                self._consigne_etapes(task, agent, etapes, journal)

        return signale

    async def _produce(
        self,
        agent: Agent,
        task: Task,
        description: str,
        playbook: PlaybookVersion | None,
        serveurs_mcp: tuple[ServeurMcp, ...] = (),
        politique: PolitiqueOutils | None = None,
        journal: RunJournal | None = None,
        suivi: SuiviChecklist | None = None,
        deliberation: Deliberation | None = None,
    ) -> tuple[str, tuple[ProducedFile, ...]]:
        """Produit le livrable de `task` : runtime outillé si le rôle en a un, sinon texte.

        `description` est la tâche déjà enrichie du tableau noir (résultats des
        dépendances). Un rôle outillé (#35) l'exécute dans un espace isolé et renvoie
        aussi ses fichiers. Si le fournisseur ne sait pas exécuter d'agent outillé
        (`UnsupportedCapability`), le rôle retombe sur son livrable texte via
        `generate()` — même chemin que les rôles sans runtime.

        `playbook` est la version courante du playbook stocké (#78) : son contenu
        remplace le prompt système sur les **deux** chemins — surcharge ponctuelle
        du runtime outillé, prompt de l'appel texte. None : prompts du code.

        `serveurs_mcp` (#104) n'équipe que le chemin **outillé** : le chemin
        texte n'expose aucun outil (c'est son contrat), MCP compris — un agent
        sans runtime outillé, ou un repli texte-seul, exécute sans ses serveurs
        (comportement documenté, docs/04 §6). Leurs références `${VAR}` se
        résolvent dans l'environnement scopé de l'agent (#109) : son coffre
        seul quand un `SecretStore` provisionné est câblé — relu ici, à chaque
        tâche, comme le reste ; un coffre invalide est un échec propre.

        `politique` (#110) ne s'applique de même qu'au chemin **outillé** (le
        chemin texte n'expose aucun outil) : le runtime retire les outils
        refusés du montage, écarte les serveurs MCP refusés, et le fournisseur
        refuse au vol le reste — chaque violation est consignée au `journal`
        (étape `:refus-outil`), donc visible au fil temps réel, sans jamais
        condamner la tâche.

        Son **troisième cran** (#580) part d'ici aussi (#583) : un outil classé
        `ask` est suspendu par le hook du fournisseur, et `_arbitre_acte` porte
        la demande jusqu'au validateur configuré. L'issue — approuvée, refusée,
        ou encore en attente à l'expiration de l'attente du fournisseur — revient
        par `on_refus`, donc au même journal et au même fil, sous un statut à
        elle. C'est le déplacement du déclencheur voulu par le parent #573 : ce
        qu'on fait trancher est l'**acte**, pas le texte de la tâche.

        L'**activité** (#479) suit exactement ce chemin-là, et n'équipe donc que
        le chemin outillé : c'est celui qui dure. Le fournisseur publie à débit
        borné ce que l'agent fait, chaque salve est consignée au `journal`
        (étape `:activite`), et la tâche cesse d'être muette entre son début et
        son issue. Le repli texte (`generate`) n'en émet aucune — un appel texte
        n'a pas d'étapes à raconter, et il ne dure pas.

        La **checklist** (#489) suit le même chemin et pour la même raison : un
        appel texte n'a pas de liste de travail à tenir. L'ossature du plan, elle,
        a déjà été posée par l'appelant — donc une tâche traitée en repli texte
        garde la checklist que le plan annonçait, sans jamais la voir se cocher.
        C'est exact et c'est dit : personne n'a rapporté d'avancement.

        L'**arbitrage demandé par l'agent** (#582) n'équipe lui aussi que le
        chemin outillé, et la raison est plus forte que pour les trois autres :
        un appel texte n'*agit* pas — il n'a aucune action irréversible à
        soumettre, et rien à faire d'une approbation. Le canal ne perd donc rien
        à s'arrêter là où l'agent cesse d'avoir des outils. Sans `journal`, pas
        de canal non plus : une décision qu'on ne pourrait pas consigner serait
        une décision prise nulle part.

        Le **mot à un pair** (#720) suit ce chemin-là aussi : il est servi comme
        un outil, donc il n'existe que là où il y en a. Sans `journal`, pas de
        canal — et ici c'est plus qu'une trace manquante : le journal *est* la
        livraison, un verbe qui n'écrirait nulle part ne tiendrait plus rien de
        ce que sa description promet. La **messagerie**, elle, n'est pas
        requise : `mailbox=None` — le cas courant — laisse le verbe consigner et
        n'a personne à notifier, ce qui est exactement le régime du pair absent
        (docs/31 §3.2).

        Le **projet** de la tâche (#224) n'équipe lui aussi que le chemin
        outillé : c'est de lui qu'est dérivé l'espace de travail (worktree ou
        copie). Le chemin texte ne produit aucun fichier — il n'a pas d'espace
        de travail du tout.

        L'**effort** (#253) est le seul réglage de cette liste à équiper les
        **deux** chemins, et c'est normal : il ne parle ni d'outils, ni d'espace,
        ni de canal — il parle du modèle, comme `agent.modele`, et il voyage donc
        exactement là où celui-ci voyage. Il est relu sur la fiche de l'agent à
        chaque tâche, comme le playbook, plutôt que figé au câblage du runtime.
        Ce qu'un fournisseur n'admet pas ne lui est jamais transmis
        (`ModelProvider.effort_admis`) : ni ici, ni dans le runtime.
        """
        deliberation = deliberation if deliberation is not None else Deliberation()
        runtime = self._runtimes.get(agent.nom)
        if runtime is not None:
            try:
                outcome = await runtime.execute(
                    description,
                    format_sortie=task.format_sortie,
                    system_prompt=playbook.contenu if playbook is not None else None,
                    mcp_serveurs=serveurs_mcp,
                    environ=(
                        self._secrets.environ(agent.nom) if self._secrets is not None else None
                    ),
                    politique=politique,
                    on_refus=(
                        None
                        if journal is None
                        else lambda outil, raison: self._consigne_refus(
                            task, agent, outil, raison, journal, politique
                        )
                    ),
                    # L'arbitrage ne dépend pas du journal (#583) : c'est la
                    # **décision** qui garde l'acte, et un run sans journal doit
                    # la demander comme les autres — seule sa trace manquerait.
                    # Sans politique en revanche, aucun outil n'est classé `ask` :
                    # il n'y a rien à arbitrer, et le hook n'existe même pas.
                    on_arbitrage_acte=(
                        None
                        if politique is None
                        else self._arbitre_acte(
                            task, agent, journal, deliberation.memoire, politique
                        )
                    ),
                    on_activite=(
                        None
                        if journal is None
                        else lambda texte: self._consigne_activite(
                            task, agent, texte, journal
                        )
                    ),
                    on_etapes=(
                        None
                        if journal is None or suivi is None
                        else self._on_etapes(task, agent, suivi, journal)
                    ),
                    on_arbitrage=(
                        None if journal is None else self._arbitre(task, agent, journal)
                    ),
                    # Sans journal, pas de canal (#719) : ce verbe ne fait
                    # **que** consigner — à la différence de l'arbitrage
                    # au-dessus, dont la décision garde l'acte même sans trace.
                    # L'exposer sans journal servirait à l'agent un outil qui
                    # n'aboutit nulle part, ce que `_outils_maestro` évite en ne
                    # le montant pas du tout.
                    on_blocage=(
                        None
                        if journal is None
                        else lambda raison: self._consigne_blocage_signale(
                            task, agent, raison, journal
                        )
                    ),
                    # Le crédit descend, la mémoire non (#584) : le fournisseur
                    # mesure une attente, il n'a pas à connaître les demandes.
                    credit_arbitrage=deliberation.credit,
                    # Le journal suffit, la messagerie n'est pas requise (#720) :
                    # c'est lui qui livre, le transport ne fait que notifier.
                    # Sans journal en revanche, il n'y aurait nulle part où
                    # écrire — et un verbe qui ne consigne rien ne tient plus
                    # aucune des promesses de sa description.
                    on_courrier=(
                        None if journal is None else self._courrier(task, agent, journal)
                    ),
                    projet=self._projet(task),
                    tache_id=task.id,
                    effort=agent.effort,
                )
                return outcome.resume, outcome.fichiers
            except UnsupportedCapability:
                pass  # fournisseur texte-seul : repli sur le livrable texte
        # Le mot-clé ne part que s'il a quelque chose à dire (#253) : sur un
        # fournisseur qui n'annonce aucun effort — le cas de tout adaptateur
        # texte-seul — l'appel est au bit près celui d'avant ce lot.
        reglage = self._provider.effort_admis(agent.modele, agent.effort)
        sortie = await self._provider.generate(
            _build_task_prompt(description, task.format_sortie),
            model=agent.modele,
            system_prompt=playbook.contenu if playbook is not None else agent.prompt_systeme,
            **({"effort": reglage} if reglage else {}),
        )
        return sortie, ()


def _fusion_du_travail(projet: Projet, branche: str) -> tuple[str, str]:
    """Fusionne `branche` dans la branche de travail de `projet` ; rend statut et phrase (#705).

    Synchrone et hors classe : c'est du sous-processus Git de bout en bout, joué
    dans un thread par l'appelant. Elle **n'invente rien** — `diff_du_travail`
    calcule, `appliquer` contrôle le périmètre (`verifier_perimetre`, EF-38) puis
    fusionne. Le nom de branche vient de `branche_de_tache` et jamais d'ici : deux
    orthographes de la convention finiraient par fusionner la branche d'une autre
    tâche.

    Le diff se lit **sur la branche** (`espace=None`) : le worktree est démonté
    depuis longtemps quand le verdict de la tâche est connu, et ce que l'agent
    avait laissé non commité y a été porté par `_solder_la_branche`.

    Ne lève **jamais** : `TaskExecutor.execute` ne lève jamais non plus (contrat
    cardinal du module), et une tâche qui vient de réussir n'a pas à échouer parce
    que le projet de quelqu'un est occupé. Les deux refus motivés du socle
    (`ApplicationRefusee`, `RacineRefusee`) portent leur cause ; le filet large
    derrière eux n'est pas de la méfiance envers ces deux-là mais la tenue du
    contrat — un `OSError` remonté d'un sous-processus ferait de ce geste annexe
    la seule chose capable de casser `execute`.
    """
    try:
        diff = diff_du_travail(projet, branche=branche)
        appliquer(projet, diff)
    except (ApplicationRefusee, RacineRefusee) as refus:
        return (
            STATUT_FUSION_REFUSEE,
            f"fusion refusée ({refus.motif}) — le projet est intact et {branche} "
            f"conserve le travail : {refus}",
        )
    except Exception as exc:  # cf. le docstring : `execute` ne lève jamais
        return (
            STATUT_FUSION_REFUSEE,
            f"fusion abandonnée — le projet est intact et {branche} conserve le "
            f"travail : {exc}",
        )
    if diff.vide:
        return (
            STATUT_FUSION_SANS_OBJET,
            f"rien à fusionner : {branche} n'apporte aucun changement à {diff.base}.",
        )
    return STATUT_FUSION_FAITE, diff.resume()


def _ecriture_en_place(projet: Projet, result: TaskResult) -> tuple[str, str]:
    """Ce qui est arrivé à la racine d'un projet **non versionné** ; rend statut et phrase (#839).

    Rien n'est calculé sur le disque : le livrable de la tâche (`result.fichiers`,
    le recensement de `EspaceEnPlace.produced_files`) **est** la réponse — ce qui
    a changé dans la racine depuis que l'agent y est entré, exclusions et liens
    écartés. Une tâche en **échec** n'a pas de livrable (le résultat d'un échec
    est vide par contrat) : ce qu'elle a écrit avant de tomber est dans la
    racine, et la phrase le dit sans prétendre le compter.
    """
    racine = projet.racine
    if not result.ok:
        return (
            STATUT_ECRITURE_EN_PLACE,
            f"tâche en échec : ce que l'agent a écrit avant d'échouer est resté dans "
            f"{racine} — rien n'en est retiré, et rien n'est recensé.",
        )
    chemins = [fichier.chemin for fichier in result.fichiers]
    if not chemins:
        return (
            STATUT_ECRITURE_SANS_OBJET,
            f"rien n'a été écrit dans {racine} : la tâche a réussi sans y déposer un fichier.",
        )
    cites = ", ".join(chemins[:_CHEMINS_CITES_MAX])
    if len(chemins) > _CHEMINS_CITES_MAX:
        cites += f" … (+{len(chemins) - _CHEMINS_CITES_MAX})"
    return (
        STATUT_ECRITURE_EN_PLACE,
        f"{len(chemins)} fichier(s) écrit(s) dans {racine} : {cites}",
    )


def _hors_arbitrage(arbitrage_ms: int) -> str:
    """Nuance un dépassement de délai du temps d'arbitrage qui lui a été rendu (#584).

    Muet quand il n'y en a pas eu, c'est-à-dire dans l'immense majorité des cas :
    le message d'un time-out ordinaire ne change pas d'un caractère. Quand il y en
    a eu, en revanche, il faut le dire — « la tâche a dépassé 600 s » sur une tâche
    qui en a passé 240 suspendue à une question enverrait chercher une lenteur
    d'exécution là où l'échéance a déjà été repoussée d'autant.
    """
    if arbitrage_ms <= 0:
        return ""
    return f" hors les {arbitrage_ms / 1000:.1f} s rendues à l'arbitrage humain"


async def _annule_ou_detache(realisation: asyncio.Task[TaskResult]) -> str:
    """Éteint une réalisation en dépassement sans jamais s'y suspendre (#64).

    Tente l'annulation coopérative pendant un court délai de grâce — le cas
    nominal, où le SDK ferme son sous-processus et la tâche s'éteint. Si
    l'annulation reste suspendue (transport qui l'avale, attente de terminaison
    du sous-processus), la tâche est **détachée** : son issue tardive est absorbée
    (`_absorbe_issue_tardive`) et plus rien n'en dépend — elle sera abandonnée à
    la fermeture de la boucle (`maestro.engine.runner`). Renvoie le détail à
    consigner dans l'erreur de time-out.
    """
    realisation.cancel()
    _, suspendues = await asyncio.wait({realisation}, timeout=_GRACE_ANNULATION_S)
    if not suspendues:
        return "exécution stoppée"
    realisation.add_done_callback(_absorbe_issue_tardive)
    return "réalisation détachée (annulation restée suspendue)"


def _absorbe_issue_tardive(realisation: asyncio.Task[TaskResult]) -> None:
    """Absorbe l'issue d'une réalisation détachée (#64) : son échec est déjà consigné.

    Sans cette relève, une exception tardive de la tâche zombie serait signalée
    par asyncio (« exception was never retrieved ») alors qu'elle n'apprend rien :
    le time-out a déjà été consigné au journal et l'aval bloqué.
    """
    if not realisation.cancelled():
        realisation.exception()


def _refus_plafond_creve(task: Task, plafond: PlafondDepense | None) -> TaskResult | None:
    """Le refus de `task` si le budget de l'exécution est déjà épuisé (#56), sinon None.

    Consulté à l'entrée de l'exécution : une exécution déjà au-delà de son plafond
    ne démarre plus aucune tâche — le routage lui-même serait de la dépense. La
    tâche est stoppée avant tout appel modèle, échec consigné avec la même cause
    qu'un dépassement en cours de tâche.
    """
    if plafond is None:
        return None
    try:
        plafond.verifie(StepUsage())
    except PlafondDepenseDepasse as exc:
        return _echec(task, agent="—", role="non exécutée", score=0, erreur=str(exc))
    return None


def _avec_stderr_cli(cause: str, stderr_cli: str | None) -> str:
    """Colle à `cause` ce que le CLI du fournisseur a écrit sur stderr (#346).

    `stderr_cli` vaut `None` quand personne n'a écouté — un fournisseur sans CLI,
    ou un échec qui ne vient pas d'un sous-processus : la cause repart alors telle
    quelle, sans ligne inutile. Quand il vaut quelque chose, c'est **soit** les
    dernières lignes du CLI, **soit** la mention explicite qu'il n'en a produit
    aucune : « pas de stderr » et « stderr jamais capturé » se ressemblaient à la
    lecture, et un seul des deux se répare.
    """
    return f"{cause}\n{stderr_cli}" if stderr_cli else cause


def _echec(task: Task, *, agent: str, role: str, score: int, erreur: str) -> TaskResult:
    """Construit un `TaskResult` en échec pour `task` (sortie vide, cause consignée)."""
    return TaskResult(
        task_id=task.id,
        titre=task.titre,
        agent=agent,
        role=role,
        competences_requises=task.competences_requises,
        score=score,
        statut=STATUT_ECHEC,
        sortie="",
        erreur=erreur,
    )


def _build_task_description(task: Task, dependances: Sequence[TaskResult]) -> str:
    """Décrit la tâche et les livrables de ses dépendances (le « tableau noir »).

    Les résultats des dépendances forment le tableau noir : l'agent voit ce que les
    tâches prérequises ont produit, pour enchaîner de façon cohérente. C'est la
    matière commune aux deux chemins d'exécution — runtime outillé et appel texte.

    L'**ossature de la checklist** (#489) y figure quand le plan en déclare une,
    et c'est ce qui rend la moitié « complétée par l'agent » de l'arbitrage
    possible : sans elle sous les yeux, l'agent ouvre sa liste de travail sur ce
    qu'il imagine, et ce que l'orchestrateur avait annoncé disparaît de l'écran
    au premier relevé. Elle est donnée comme une **proposition** et non comme une
    consigne — un agent qui découvre en travaillant a raison contre un plan écrit
    à l'aveugle, et c'est précisément pourquoi son relevé la supplante
    (`maestro.detail_tache.SuiviChecklist`).
    """
    lignes = [
        f"Tâche : {task.titre}",
        "",
        "Description :",
        task.description,
    ]
    if task.etapes:
        lignes += [
            "",
            "Étapes prévues au plan (proposition — ta liste de travail fait foi, "
            "reprends-les, corrige-les ou remplace-les selon ce que la tâche exige) :",
        ]
        lignes += [f"- {etape}" for etape in task.etapes]
    if dependances:
        lignes += ["", "Résultats des tâches dont celle-ci dépend :"]
        for dep in dependances:
            livrable = dep.sortie or "(aucune sortie — tâche en échec)"
            lignes += ["", f"— [{dep.titre}] (agent {dep.role}) :", livrable]
    return "\n".join(lignes)


def _build_task_prompt(description: str, format_sortie: str) -> str:
    """Compose le message d'un livrable *texte* : la description + le format attendu.

    Le chemin outillé n'en a pas besoin : le runtime encadre lui-même la description
    avec les consignes de son rôle (cf. `maestro.agents.runtime._build_prompt`).
    """
    lignes = [
        description,
        "",
        f"Format de sortie attendu : {format_sortie}",
        "",
        "Produis maintenant le livrable demandé.",
    ]
    return "\n".join(lignes)


def _ecoule_ms(debut: float) -> int:
    """Millisecondes écoulées depuis `debut` (un repère `perf_counter`)."""
    return int((perf_counter() - debut) * 1000)
