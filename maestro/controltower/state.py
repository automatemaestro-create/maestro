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
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_REASSIGNATION,
    EVENEMENT_TACHE_REFERENCE,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    ReferenceTicket,
)
from maestro.engine.executor import STATUT_BLOQUEE, STATUT_ECHEC, STATUT_TERMINEE
from maestro.projets.application import DiffProjet
from maestro.references import ticket_en_dict
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

#: Statuts d'exécution **terminaux** : le run ne bouge plus, il n'est plus
#: interruptible (`POST /api/executions/{run_id}/annuler` répond alors 409).
STATUTS_EXECUTION_TERMINAUX = frozenset(
    {EXECUTION_TERMINEE, EXECUTION_ANNULEE, EXECUTION_ECHEC}
)

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
    reste du côté du grand livre du run (`EtatExecution.cout`). `ticket` porte
    la référence du ticket externe dont relève la tâche (#187, contrat #183) —
    None tant qu'aucune n'a été transportée par un événement (inconnu ≠ absent) ;
    posée par un événement de tâche, elle survit au rejeu du journal durable.
    `projet_id` porte le projet auquel la tâche appartient (#222) — None quand
    elle ne relève d'aucun projet, ce qui reste le cas courant : c'est ce champ
    que le Kanban filtre (`GET /api/taches?projet=…`).
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

    @property
    def debut(self) -> str:
        """L'horodatage du premier événement reçu pour ce run — vide si aucun.

        Pour un run lancé par l'API c'est son événement de lancement ; pour un
        run venu du journal d'un autre process, sa première étape consignée.
        """
        return self.evenements[0].horodatage if self.evenements else ""

    @property
    def nb_taches(self) -> int:
        """Le nombre de tâches distinctes vues dans le run (0 avant planification)."""
        return len(
            {
                e.tache_id
                for e in self.evenements
                if e.type == EVENEMENT_TACHE_STATUT and e.tache_id
            }
        )

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
        """
        return {
            "run_id": self.run_id,
            "objectif": self.objectif,
            "statut": self.statut,
            "nb_taches": self.nb_taches,
            "cout_usd": self.cout_usd,
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
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
        que le résumé n'a pas : le grand livre (#57) et la trace événement par
        événement — `GET /api/executions/{run_id}` sert donc l'état du run et sa
        trace d'un seul appel.
        """
        return {
            **self.resume(),
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

    def taches(self, projet: str | None = None) -> list[EtatTache]:
        """Les tâches connues, dans l'ordre de première apparition.

        `projet` (#222) restreint la vue aux tâches de ce projet — c'est le
        filtre du Kanban. Sans lui, **toutes** les tâches sortent, celles sans
        projet comprises : le champ est optionnel, ne pas filtrer reste le
        comportement d'avant ce lot. Une tâche sans `projet_id` n'apparaît dans
        aucune vue filtrée : on ne devine pas à quel projet elle appartiendrait.
        """
        taches = list(self._taches.values())
        if projet is None:
            return taches
        return [t for t in taches if t.projet_id == projet]

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

    def executions(self, projet: str | None = None) -> list[EtatExecution]:
        """Les exécutions connues, dans l'ordre de première apparition (#87).

        `projet` (#222) restreint aux runs de ce projet — même convention que
        `taches` : sans filtre, tous les runs sortent ; avec, un run sans projet
        n'y figure pas. C'est par ce paramètre que la vue coûts & analytics se
        restreint à un projet, sa matière étant ces mêmes exécutions.
        """
        executions = list(self._executions.values())
        if projet is None:
            return executions
        return [e for e in executions if e.projet_id == projet]

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
        elif event.type in {EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS}:
            self._applique_activite(event)
        elif event.type == EVENEMENT_AGENT_CAPACITE:
            self._applique_capacite(event)
        elif event.type == EVENEMENT_VALIDATION_DEMANDE:
            self._applique_validation_demande(event)
        elif event.type == EVENEMENT_VALIDATION_DECISION:
            self._applique_validation_decision(event)
        elif event.type == EVENEMENT_EXECUTION_STATUT:
            self._applique_execution_statut(event)

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
        if event.projet_id is not None:
            tache.projet_id = event.projet_id

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

    def _applique_execution_statut(self, event: Event) -> None:
        """Pose le cycle de vie d'un run (#185) : objectif, statut, heure de fin.

        L'exécution existe déjà (`appliquer` l'a créée en rattachant l'événement
        à son `run_id`) : il ne reste qu'à porter l'état **résultant**. `fin` est
        posée sur un statut terminal et retirée si le run repasse en cours — un
        événement sans `run_id` est ignoré (rien à rattacher). Idempotent :
        réappliquer le même événement (application directe par le service puis
        rediffusion par la pompe) laisse l'état inchangé.
        """
        execution = self._executions.get(event.run_id)
        if execution is None:
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
        execution.fin = (
            event.horodatage if execution.statut in STATUTS_EXECUTION_TERMINAUX else None
        )

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
            diff=event.diff,
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
