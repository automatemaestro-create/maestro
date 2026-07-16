"""Projection d'état de la Control Tower — tâches, agents, exécutions (ticket #46).

`ControlTowerState` matérialise l'**état courant** de l'orchestration à partir
du flux d'événements (`maestro.controltower.events`) : c'est la source des
endpoints REST (liste des tâches, état des agents, détail d'une exécution),
pendant que le WebSocket rediffuse le flux brut. Même modèle que l'UI
(docs/05) : un client charge l'état via le REST puis suit les événements.

La projection est **en mémoire** (POC Phase 0) : elle vit avec le process de
l'API et se reconstruit en rejouant les événements reçus depuis son démarrage.
La persistance PostgreSQL (entités TASK/RUN/AGENT de docs/03) viendra s'y
substituer sans changer le contrat des endpoints.

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
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
)
from maestro.engine.executor import STATUT_BLOQUEE, STATUT_ECHEC, STATUT_TERMINEE
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

#: Statuts de tâche *terminaux* (machine à états docs/03 §3) : l'agent redevient
#: libre et les compteurs de la fiche agent s'incrémentent.
_STATUTS_TERMINAUX = frozenset({STATUT_TERMINEE, STATUT_ECHEC, STATUT_BLOQUEE})

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
    reste du côté du grand livre du run (`EtatExecution.cout`).
    """

    id: str
    titre: str = ""
    statut: str = ""
    agent: str = ""
    role: str = ""
    run_id: str = ""
    cout_usd: float | None = None
    usage: StepUsage | None = None
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
    """

    tache_id: str
    titre: str = ""
    description: str = ""
    agent: str = ""
    role: str = ""
    raison: str = ""
    statut: str = VALIDATION_EN_ATTENTE
    decision: str = ""
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
            "horodatage": self.horodatage,
        }


@dataclass
class EtatExecution:
    """Le détail d'une exécution : les événements d'un `run_id`, dans l'ordre reçu.

    Pendant API du `RunJournal` (#8) : la trace consultable d'un run — étapes,
    statuts, coûts — reliée aux tâches par `tache_id`. `cout_usd` agrège les
    coûts rapportés par les événements du run ; `cout` en est la vue
    comptable (#57) : le grand livre du run, coût par tâche et agrégat.
    """

    run_id: str
    evenements: list[Event] = field(default_factory=list)

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
        """Réémet l'exécution en dict JSON-sérialisable (la forme du REST)."""
        return {
            "run_id": self.run_id,
            "cout_usd": self.cout_usd,
            "cout": self.cout.to_dict(),
            "evenements": [e.to_dict() for e in self.evenements],
        }


class ControlTowerState:
    """L'état courant de l'orchestration, reconstruit du flux d'événements.

    Quatre vues, chacune derrière un endpoint REST : `taches` (Kanban),
    `agents` (fiches et charge), `execution(run_id)` (détail d'un run),
    `validations` (demandes de validation humaine, #48).
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

    def taches(self) -> list[EtatTache]:
        """Les tâches connues, dans l'ordre de première apparition."""
        return list(self._taches.values())

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

    def executions(self) -> list[EtatExecution]:
        """Les exécutions connues, dans l'ordre de première apparition (#87)."""
        return list(self._executions.values())

    def validations(self) -> list[EtatValidation]:
        """Les demandes de validation humaine, dans l'ordre de première apparition."""
        return list(self._validations.values())

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
            self._executions.setdefault(
                event.run_id, EtatExecution(run_id=event.run_id)
            ).evenements.append(event)
        if event.type == EVENEMENT_TACHE_STATUT:
            self._applique_statut_tache(event)
        elif event.type == EVENEMENT_TACHE_REASSIGNATION:
            self._applique_reassignation(event)
        elif event.type in {EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS}:
            self._applique_activite(event)
        elif event.type == EVENEMENT_AGENT_CAPACITE:
            self._applique_capacite(event)
        elif event.type == EVENEMENT_VALIDATION_DEMANDE:
            self._applique_validation_demande(event)
        elif event.type == EVENEMENT_VALIDATION_DECISION:
            self._applique_validation_decision(event)

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

        if event.agent in _AGENTS_NON_EXECUTANTS:
            return
        agent = self._agents.setdefault(
            event.agent, EtatAgent(nom=event.agent, role=event.role)
        )
        agent.derniere_activite = event.horodatage or agent.derniere_activite
        if event.statut in _STATUTS_TERMINAUX:
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
        if tache.statut not in _STATUTS_TERMINAUX:
            agent.commence(event.tache_id)

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

    def _applique_validation_demande(self, event: Event) -> None:
        """Enregistre une demande de validation humaine (#48) — en attente de décision.

        Une nouvelle demande sur la même tâche (autre run, re-tentative) remplace
        la précédente : le moteur attend toujours la décision de la **dernière**
        demande publiée, l'historique complet reste dans le journal (#8).
        """
        self._validations[event.tache_id] = EtatValidation(
            tache_id=event.tache_id,
            titre=event.titre,
            description=event.description,
            agent=event.agent,
            role=event.role,
            raison=event.detail,
            horodatage=event.horodatage,
        )

    def _applique_validation_decision(self, event: Event) -> None:
        """Tranche une demande de validation (#48) : approuvée ou refusée.

        Idempotent : réappliquer la même décision (application directe par
        l'endpoint puis rediffusion par la pompe) laisse l'état inchangé. Une
        décision sur une demande inconnue est ignorée — la projection est un
        miroir, pas une source de vérité.
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
