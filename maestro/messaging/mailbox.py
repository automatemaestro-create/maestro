"""Boîtes aux lettres inter-agents — messages et transport Pub/Sub (ticket #44).

Chaque agent dispose d'une **boîte aux lettres** (docs/01 §3.3, docs/08) : un
canal de messagerie directe qui permet à un agent d'en notifier un autre —
passer un relais (*handoff*), poser une question, diffuser une annonce — sans
repasser par l'orchestrateur (EF-32). Le vecteur est l'`AgentMessage`, miroir
léger de l'entité AGENT_MESSAGE (docs/03 §2) : expéditeur, destinataire
(vide = diffusion à toutes les boîtes), type (`handoff`, `requete`, `reponse`,
`notification`), tâche de contexte et charge utile JSON.

Le transport (`Mailbox`) suit le même schéma que le bus d'événements de la
Control Tower (#46) — deux implémentations au même contrat :

- `InMemoryMailbox` : files asyncio en process — le levier des tests et d'un
  déploiement mono-process ;
- `RedisMailbox` : **Redis Pub/Sub** — le chemin de production, sur l'instance
  mutualisée avec la file de tâches (#41) et le bus d'événements (#46). Un
  canal par agent (`maestro.boite.<agent>`) plus un canal de diffusion
  (`maestro.diffusion`).

À la différence du bus d'événements, `subscribe` renvoie un **abonnement déjà
posé** (`MailboxSubscription`) plutôt qu'un générateur : le pub/sub étant
éphémère (pas de rejeu), le consommateur doit avoir la garantie que sa boîte
est ouverte *avant* que l'expéditeur publie — c'est ce que le retour de
`subscribe` atteste.

Chaque échange est **journalisé** dans la télémétrie (#8) via
`consigne_message` : la ligne de journal (étape `<tache>:message`) est ensuite
convertie par le pont Control Tower (#46, `maestro.controltower.bridge`) en
événement `message.inter_agents`, visible dans le flux temps réel (EF-34).
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from maestro.telemetry import RunJournal, StepUsage

#: Types de message inter-agents (entité AGENT_MESSAGE, docs/03 §2) : le
#: `handoff` passe le relais, la `notification` informe (échec, annonce), la
#: paire `requete`/`reponse` porte un échange question-réponse.
MESSAGE_HANDOFF = "handoff"
MESSAGE_REQUETE = "requete"
MESSAGE_REPONSE = "reponse"
MESSAGE_NOTIFICATION = "notification"

#: Statuts d'un message (docs/03 §2). Le POC publie en `envoye` ; `lu` et
#: `traite` attendent la persistance (PostgreSQL) pour un accusé durable.
STATUT_ENVOYE = "envoye"
STATUT_LU = "lu"
STATUT_TRAITE = "traite"

#: Destinataire « diffusion » : le message part dans **toutes** les boîtes
#: (`to_agent` null de l'entité AGENT_MESSAGE — broadcast).
DIFFUSION = ""

#: Canaux Redis Pub/Sub : une boîte par agent, plus le canal de diffusion —
#: préfixes distincts pour qu'aucun nom d'agent n'entre en collision avec la
#: diffusion. Instance mutualisée (#41, #46), d'où des canaux nommés.
CANAL_BOITE_PREFIXE = "maestro.boite."
CANAL_DIFFUSION = "maestro.diffusion"

#: URL Redis par défaut : l'instance locale du docker-compose (infra/README.md).
#: Doublonne volontairement celles de la file (#41) et du bus (#46) pour ne pas
#: créer de dépendance entre les trois briques.
REDIS_URL_DEFAUT = "redis://localhost:6379/0"

#: Suffixe de l'étape de journal qui consigne un message inter-agents — la
#: convention lue par le pont Control Tower (`maestro.controltower.bridge`).
SUFFIXE_ETAPE_MESSAGE = ":message"


def _horodatage() -> str:
    """Horodatage UTC ISO-8601, même précision que le journal (#8)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def canal_boite(agent: str) -> str:
    """Le canal Redis de la boîte aux lettres de `agent`."""
    return f"{CANAL_BOITE_PREFIXE}{agent}"


@dataclass(frozen=True)
class AgentMessage:
    """Un message inter-agents, prêt à voyager en JSON (entité AGENT_MESSAGE).

    `de_agent` est l'expéditeur ; `a_agent` le destinataire (`DIFFUSION` — vide —
    pour une diffusion à toutes les boîtes). `tache_id` situe le message dans le
    plan (la tâche dont on parle), `run_id` dans l'exécution. `objet` est la
    ligne humaine du message (sujet de la lettre) ; `payload` la charge utile
    structurée (statut transmis, tâches débloquées…), toujours JSON-sérialisable.
    """

    type: str
    de_agent: str
    a_agent: str = DIFFUSION
    tache_id: str = ""
    run_id: str = ""
    objet: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    statut: str = STATUT_ENVOYE
    horodatage: str = field(default_factory=_horodatage)

    def to_dict(self) -> dict[str, Any]:
        """Réémet le message en dict JSON-sérialisable (la forme du canal)."""
        return {
            "type": self.type,
            "de_agent": self.de_agent,
            "a_agent": self.a_agent,
            "tache_id": self.tache_id,
            "run_id": self.run_id,
            "objet": self.objet,
            "payload": dict(self.payload),
            "statut": self.statut,
            "horodatage": self.horodatage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentMessage:
        """Reconstruit un message depuis sa forme `to_dict` (aller-retour JSON)."""
        return cls(
            type=data["type"],
            de_agent=data.get("de_agent", ""),
            a_agent=data.get("a_agent", DIFFUSION),
            tache_id=data.get("tache_id", ""),
            run_id=data.get("run_id", ""),
            objet=data.get("objet", ""),
            payload=dict(data.get("payload", {})),
            statut=data.get("statut", STATUT_ENVOYE),
            horodatage=data.get("horodatage", ""),
        )

    def to_json(self) -> str:
        """Sérialise le message en JSON compact (la forme du canal Redis)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, brut: str | bytes) -> AgentMessage:
        """Désérialise un message du canal Redis (`bytes` tels que reçus)."""
        texte = brut.decode("utf-8") if isinstance(brut, bytes) else brut
        data = json.loads(texte)
        if not isinstance(data, dict):
            raise ValueError(f"message JSON attendu (objet), reçu : {texte!r}")
        return cls.from_dict(data)


class MailboxSubscription(ABC):
    """Une boîte aux lettres ouverte : itérateur asynchrone des messages reçus.

    Reçoit les messages **adressés à l'agent** de l'abonnement et les
    **diffusions**. Pas de rejeu (pub/sub éphémère) : seuls les messages
    publiés après l'ouverture arrivent. `close` referme la boîte.
    """

    def __aiter__(self) -> MailboxSubscription:
        return self

    @abstractmethod
    async def __anext__(self) -> AgentMessage:
        """Le prochain message reçu (attend s'il n'y en a pas encore)."""
        raise NotImplementedError

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027).
    async def close(self) -> None:  # noqa: B027
        """Referme la boîte (désabonnement, connexions) — no-op par défaut."""


class Mailbox(ABC):
    """Transport des messages inter-agents — le point d'injection du moteur (#44).

    `publish` dépose le message dans la boîte du destinataire (toutes les
    boîtes si `DIFFUSION`) ; `subscribe(agent)` ouvre la boîte de `agent` et ne
    rend la main que l'abonnement **posé** — un message publié ensuite est
    garanti reçu. Plusieurs boîtes ouvertes pour un même agent reçoivent
    chacune tous ses messages.
    """

    @abstractmethod
    async def publish(self, message: AgentMessage) -> None:
        """Publie `message` dans la ou les boîtes destinataires."""
        raise NotImplementedError

    @abstractmethod
    async def subscribe(self, agent: str) -> MailboxSubscription:
        """Ouvre la boîte aux lettres de `agent` (abonnement posé au retour)."""
        raise NotImplementedError

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027).
    async def close(self) -> None:  # noqa: B027
        """Libère les ressources du transport (connexions) — no-op par défaut."""


class _AbonnementMemoire(MailboxSubscription):
    """Boîte ouverte du transport mémoire : une file asyncio par abonnement."""

    def __init__(self, agent: str, abonnes: list[_AbonnementMemoire]) -> None:
        self.agent = agent
        self.file: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._abonnes = abonnes

    async def __anext__(self) -> AgentMessage:
        return await self.file.get()

    async def close(self) -> None:
        if self in self._abonnes:
            self._abonnes.remove(self)


class InMemoryMailbox(Mailbox):
    """Boîtes aux lettres en mémoire : files asyncio, aucun réseau.

    Le transport des **tests** et d'un déploiement mono-process (l'orchestrateur
    et ses agents partagent la boucle asyncio). Les files sont non bornées : le
    POC échange peu de messages, consommés au fil de l'eau.
    """

    def __init__(self) -> None:
        self._abonnes: list[_AbonnementMemoire] = []

    async def publish(self, message: AgentMessage) -> None:
        # Copie défensive : une boîte peut se refermer pendant la boucle.
        for abonne in list(self._abonnes):
            if message.a_agent in (DIFFUSION, abonne.agent):
                abonne.file.put_nowait(message)

    async def subscribe(self, agent: str) -> MailboxSubscription:
        abonnement = _AbonnementMemoire(agent, self._abonnes)
        self._abonnes.append(abonnement)
        return abonnement


class _AbonnementRedis(MailboxSubscription):
    """Boîte ouverte du transport Redis : un pubsub abonné aux deux canaux."""

    def __init__(self, pubsub: Any, canaux: tuple[str, ...]) -> None:
        self._pubsub = pubsub
        self._canaux = canaux
        self._flux = pubsub.listen()

    async def __anext__(self) -> AgentMessage:
        # `listen` intercale des messages de contrôle (subscribe…) : seuls les
        # messages de données portent un message inter-agents.
        async for brut in self._flux:
            if brut.get("type") != "message":
                continue
            return AgentMessage.from_json(brut["data"])
        raise StopAsyncIteration

    async def close(self) -> None:
        await self._pubsub.unsubscribe(*self._canaux)
        await self._pubsub.aclose()


class RedisMailbox(Mailbox):
    """Boîtes aux lettres adossées à Redis Pub/Sub — le chemin multi-process.

    Publie sur le canal de la boîte du destinataire (`maestro.boite.<agent>`),
    ou sur `maestro.diffusion` pour une diffusion ; chaque boîte ouverte écoute
    ces deux canaux. La dépendance `redis` est déjà tirée par `celery[redis]`
    (#41) ; la connexion est paresseuse (ouverte au premier appel).
    """

    def __init__(self, url: str | None = None) -> None:
        # Import local : seule la branche Redis dépend du client (le moteur
        # testé sur boîtes mémoire n'en a pas besoin).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)

    async def publish(self, message: AgentMessage) -> None:
        canal = CANAL_DIFFUSION if message.a_agent == DIFFUSION else canal_boite(message.a_agent)
        await self._client.publish(canal, message.to_json())

    async def subscribe(self, agent: str) -> MailboxSubscription:
        canaux = (canal_boite(agent), CANAL_DIFFUSION)
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*canaux)
        return _AbonnementRedis(pubsub, canaux)

    async def close(self) -> None:
        await self._client.aclose()


def consigne_message(journal: RunJournal, message: AgentMessage, *, role: str = "") -> None:
    """Journalise `message` dans la télémétrie (#8) — l'échange devient traçable (EF-34).

    Consigne une étape `<tache>:message` au journal de l'exécution : la ligne
    JSON part dans les traces comme les autres étapes, et le pont Control Tower
    (#46) la convertit en événement `message.inter_agents` visible dans le flux
    temps réel. `role` est le rôle de l'expéditeur quand il est connu.
    """
    destinataire = message.a_agent or "diffusion"
    journal.consigne(
        etape=f"{message.tache_id}{SUFFIXE_ETAPE_MESSAGE}",
        nom=message.objet or f"Message inter-agents ({message.type})",
        agent=message.de_agent,
        role=role,
        statut=message.statut,
        entree="",
        sortie=f"{message.type} de {message.de_agent} à {destinataire} : {message.objet}",
        usage=StepUsage(),
    )
