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
from maestro.agents.playbooks import PlaybookStore, PlaybookVersion
from maestro.agents.runtime import AgentRuntime
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider, UnsupportedCapability
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
    ) -> None:
        self._provider = provider
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
            PlafondDepense(journal, self._guardrails.plafond_cout_usd)
            if self._guardrails.plafond_cout_usd is not None
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
                    # Application à chaud (#78) : le playbook courant est résolu
                    # ici, à chaque tâche — jamais retenu entre deux exécutions.
                    playbook = self._playbook_courant(decision.agent.nom)
                    # Contrôle de capacité (#86) : l'agent au complet retient la
                    # tâche jusqu'à la libération d'un créneau d'instance.
                    async with self._creneau_capacite(decision.agent.nom):
                        result = await self._realise_gardee(
                            decision.agent, task, entree, decision.score, journal, playbook
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

    async def _realise_gardee(
        self,
        agent: Agent,
        task: Task,
        description: str,
        score: int,
        journal: RunJournal,
        playbook: PlaybookVersion | None,
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
            return await self._realise(agent, task, description, score, playbook)
        realisation = asyncio.create_task(
            self._realise(agent, task, description, score, playbook),
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
    ) -> TaskResult:
        """Produit le livrable de `task` et le mue en `TaskResult` (échec consigné, jamais levé)."""
        try:
            sortie, fichiers = await self._produce(agent, task, description, playbook)
        except Exception as exc:  # exécution: on consigne l'échec sans casser la boucle
            return _echec(task, agent=agent.nom, role=agent.role, score=score, erreur=str(exc))

        sortie = sortie.strip()
        if not sortie and not fichiers:
            return _echec(
                task,
                agent=agent.nom,
                role=agent.role,
                score=score,
                erreur="réponse vide de l'agent.",
            )
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

    async def _produce(
        self, agent: Agent, task: Task, description: str, playbook: PlaybookVersion | None
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
        """
        runtime = self._runtimes.get(agent.nom)
        if runtime is not None:
            try:
                outcome = await runtime.execute(
                    description,
                    format_sortie=task.format_sortie,
                    system_prompt=playbook.contenu if playbook is not None else None,
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
