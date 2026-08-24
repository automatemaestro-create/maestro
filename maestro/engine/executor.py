"""Exécution d'une tâche assignée — la brique que la boucle délègue (tickets #6, #41).

Isole de la boucle d'orchestration (`maestro.engine.loop`) tout ce qui concerne
l'exécution d'**une** tâche : routage vers l'agent compétent, garde-fous (#9),
production du livrable (runtime outillé ou appel texte), mesure d'usage (#8) et
consignation au journal. La boucle, elle, ne garde que l'ordonnancement (plan,
dépendances, parallélisme) et l'agrégation.

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
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from maestro.agents import default_runtimes
from maestro.agents.capacity import CapacityStore, JaugeInstances
from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.mcp import McpStore, ServeurMcp
from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.agents.playbooks import PlaybookStore, PlaybookVersion
from maestro.agents.runtime import AgentRuntime
from maestro.agents.secrets import SecretStore
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.engine.retry import PolitiqueRelance, est_transitoire
from maestro.orchestrator.schema import Task
from maestro.projets.modele import Projet
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider, UnsupportedCapability, stderr_de
from maestro.router.classifier import TaskClassifier
from maestro.router.router import Router
from maestro.sandbox import ProducedFile
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
    ) -> None:
        self._provider = provider
        # Dépôt des projets (#224, EF-36) : quand une tâche porte un `projet_id`
        # (#222), le projet est relu ici — à chaud, comme les autres dépôts — et
        # l'espace de travail en est **dérivé** (worktree Git sur une branche
        # `maestro/<tâche>`, ou copie du périmètre) au lieu d'un répertoire vide.
        # None, ou tâche sans `projet_id` : le `mkdtemp()` historique.
        self._projets = projets
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
        """
        debut = perf_counter()
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
                        async with self._creneau_capacite(decision.agent.nom):
                            result = await self._realise_gardee(
                                decision.agent,
                                task,
                                entree,
                                decision.score,
                                journal,
                                playbook,
                                serveurs_mcp,
                                politique,
                            )
                        if playbook is not None:
                            result = replace(result, playbook_version=playbook.version)
        result = replace(result, usage=recolte.total.avec_duree(_ecoule_ms(debut)))
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
    ) -> TaskResult:
        """Réalise la tâche sous garde-fous (#9) : validation humaine, puis time-out.

        Une tâche sensible non approuvée est stoppée **avant** toute exécution.
        Le time-out ne court que sur la réalisation elle-même : l'attente d'une
        décision humaine n'y est pas comptée. Le plafond de dépense, lui, est armé
        plus haut (sur le collecteur d'usage de `execute`) — son dépassement
        remonte en exception du fournisseur, muée ici en échec comme les autres.

        Le time-out est une **échéance ferme** (#64) : la réalisation court dans sa
        propre tâche asyncio et l'attente est bornée par `asyncio.wait`, qui rend
        la main à l'échéance sans rien exiger de la tâche — là où `asyncio.timeout`
        restait suspendu quand le transport du SDK avalait l'annulation. À
        l'échéance, l'échec est consigné immédiatement ; l'annulation de la
        réalisation n'est qu'un vœu (`_annule_ou_detache`), jamais une attente.
        """
        refus = await self._valide_si_sensible(agent, task, score, journal)
        if refus is not None:
            return refus
        timeout_s = self._guardrails.timeout_s
        if timeout_s is None:
            return await self._realise(
                agent, task, description, score, playbook, serveurs_mcp, politique, journal
            )
        realisation = asyncio.create_task(
            self._realise(
                agent, task, description, score, playbook, serveurs_mcp, politique, journal
            ),
            name=f"maestro-realisation:{task.id}",
        )
        try:
            fini, _ = await asyncio.wait({realisation}, timeout=timeout_s)
        except asyncio.CancelledError:
            # Annulation externe (arrêt de l'exécution) : relayée à la réalisation.
            realisation.cancel()
            raise
        if realisation in fini:
            return realisation.result()
        issue = await _annule_ou_detache(realisation)
        return _echec(
            task,
            agent=agent.nom,
            role=agent.role,
            score=score,
            erreur=f"time-out : la tâche a dépassé {timeout_s:g} s — {issue}.",
        )

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
        )
        approuve, detail = await self._guardrails.demande_validation(demande)
        journal.consigne(
            etape=f"{task.id}:validation",
            nom=f"Validation humaine — {task.titre}",
            agent=agent.nom,
            role=agent.role,
            statut="approuve" if approuve else "refuse",
            entree=raison,
            sortie=detail,
            usage=StepUsage(),
            # Le projet (#222) est porté par **toutes** les étapes de la tâche,
            # annexes comprises : c'est un critère de filtre, et une étape qui ne
            # le porterait pas disparaîtrait des vues restreintes à ce projet.
            projet_id=task.projet_id,
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
        """
        relance = self._relance
        max_tentatives = relance.max_tentatives if relance is not None else 1
        tentative = 1
        while True:
            self._consigne_debut(task, agent, tentative, max_tentatives, journal)
            try:
                sortie, fichiers = await self._produce(
                    agent, task, description, playbook, serveurs_mcp, politique, journal
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
    ) -> None:
        """Trace un refus d'outil au journal (#110) — donc au fil temps réel de la Control Tower.

        Étape dédiée `<task.id>:refus-outil` (même modèle que `:validation` et
        `:relance`), que le pont (`maestro.controltower.bridge`) mue en
        activité d'agent : la violation est visible au moment où elle se
        produit — l'agent, lui, a reçu le motif et poursuit sa tâche. `entree`
        porte l'outil demandé, `sortie` le motif du refus. Usage nul : le coût
        de la tâche est porté par son étape finale.
        """
        journal.consigne(
            etape=f"{task.id}{SUFFIXE_ETAPE_REFUS}",
            nom=f"Outil refusé — {task.titre}",
            agent=agent.nom,
            role=agent.role,
            statut=STATUT_REFUS_OUTIL,
            entree=outil,
            sortie=raison,
            usage=StepUsage(),
            projet_id=task.projet_id,
        )

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

    async def _produce(
        self,
        agent: Agent,
        task: Task,
        description: str,
        playbook: PlaybookVersion | None,
        serveurs_mcp: tuple[ServeurMcp, ...] = (),
        politique: PolitiqueOutils | None = None,
        journal: RunJournal | None = None,
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

        L'**activité** (#479) suit exactement ce chemin-là, et n'équipe donc que
        le chemin outillé : c'est celui qui dure. Le fournisseur publie à débit
        borné ce que l'agent fait, chaque salve est consignée au `journal`
        (étape `:activite`), et la tâche cesse d'être muette entre son début et
        son issue. Le repli texte (`generate`) n'en émet aucune — un appel texte
        n'a pas d'étapes à raconter, et il ne dure pas.

        Le **projet** de la tâche (#224) n'équipe lui aussi que le chemin
        outillé : c'est de lui qu'est dérivé l'espace de travail (worktree ou
        copie). Le chemin texte ne produit aucun fichier — il n'a pas d'espace
        de travail du tout.
        """
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
                            task, agent, outil, raison, journal
                        )
                    ),
                    on_activite=(
                        None
                        if journal is None
                        else lambda texte: self._consigne_activite(
                            task, agent, texte, journal
                        )
                    ),
                    projet=self._projet(task),
                    tache_id=task.id,
                )
                return outcome.resume, outcome.fichiers
            except UnsupportedCapability:
                pass  # fournisseur texte-seul : repli sur le livrable texte
        sortie = await self._provider.generate(
            _build_task_prompt(description, task.format_sortie),
            model=agent.modele,
            system_prompt=playbook.contenu if playbook is not None else agent.prompt_systeme,
        )
        return sortie, ()


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
    """
    lignes = [
        f"Tâche : {task.titre}",
        "",
        "Description :",
        task.description,
    ]
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
