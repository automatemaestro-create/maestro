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

from maestro.appartenance import projet_id_valide
from maestro.projets.application import DiffProjet
from maestro.references import ReferenceTicket as ReferenceTicket  # ré-export explicite
from maestro.telemetry.usage import StepUsage

# `ReferenceTicket` (#187, contrat #183) est **défini** dans `maestro.references`,
# module feuille, et seulement ré-exporté ici : le journal (#8) le porte aussi, et
# `controltower` important déjà `telemetry`, le garder dans ce module aurait fermé
# un cycle d'imports. Les appelants historiques — `from
# maestro.controltower.events import ReferenceTicket` — restent servis tels quels.

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
EVENEMENT_AGENT_ACTIVITE = "agent.activite"
EVENEMENT_AGENT_CAPACITE = "agent.capacite"
EVENEMENT_MESSAGE_INTER_AGENTS = "message.inter_agents"
EVENEMENT_VALIDATION_DEMANDE = "validation.demande"
EVENEMENT_VALIDATION_DECISION = "validation.decision"
EVENEMENT_CHAT_MESSAGE = "chat.message"
EVENEMENT_PLAYBOOK_PROPOSITION = "playbook.proposition"
EVENEMENT_EXECUTION_STATUT = "execution.statut"

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
    (#86, entité AGENT `instances_max`) — None ailleurs. `ticket` porte la
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
    ceci ? », et None partout ailleurs.
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
    diff: DiffProjet | None = None
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
            "diff": self.diff.to_dict() if self.diff is not None else None,
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
            diff=DiffProjet.from_dict(diff_brut) if isinstance(diff_brut, Mapping) else None,
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
