"""Persistance de l'état Control Tower — journal durable des événements (ticket #97).

La projection de la Control Tower (`ControlTowerState`) est reconstruite en
**rejouant les événements** (`state.appliquer(event)` est l'unique fonction de
projection). Tant que ces événements ne vivaient que sur le bus **éphémère**
(pub/sub, pas de rejeu, `maestro.controltower.events`), un redémarrage de l'API
repartait sur une projection vide : exécutions passées, grands livres par run et
page Coûts & analytics disparaissaient (docs/13-demo-v1.md §5, réserve 2), seuls
les artefacts JSON du moteur restaient.

Ce module ajoute au bus son **pendant durable** : un `EventLog` qui *consigne*
chaque événement hors de la mémoire du process et le *relit* au démarrage. La
persistance est donc de l'**event sourcing** — on ne sérialise pas la projection,
on garde le flux qui la reconstruit : rejouer le journal au démarrage rebâtit à
l'identique tâches, agents, exécutions (donc grands livres et analytics, qui en
dérivent) et validations, sans nouveau code de projection.

Deux implémentations au même contrat, comme le bus (`EventBus`) et les boîtes
(`Mailbox`) :

- `InMemoryEventLog` : une liste en process — le levier des tests et d'un
  déploiement mono-process (aucune durabilité inter-redémarrage : la projection
  se reconstruit du flux reçu depuis le démarrage, comme avant #97) ;
- `RedisEventLog` : une **liste Redis** (`RPUSH`/`LRANGE`) sur l'instance déjà
  mutualisée avec la file de tâches (#41), le bus (#46) et les boîtes (#44) —
  le chemin de production. L'événement y est appendu au fil de l'eau et relu
  intégralement au démarrage, dans l'ordre d'arrivée.

Le journal double le canal pub/sub **sans le remplacer** : le bus reste le
transport temps réel (diffusion aux WebSockets), le journal en est la mémoire
longue. La rétention n'est pas bornée au POC (la liste croît avec l'historique,
pour préserver « l'historique complet » des critères #97) ; la bascule vers
PostgreSQL (entités RUN/TASK de docs/03) et sa politique de rétention viennent
plus tard.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from maestro.controltower.events import REDIS_URL_DEFAUT, Event

#: Clé Redis de la liste des événements persistés — sur l'instance mutualisée
#: avec la file (#41), le bus (#46) et les boîtes (#44), d'où une clé nommée
#: proche du canal du bus (`CANAL_EVENEMENTS`) sans lui être confondue.
CLE_JOURNAL_EVENEMENTS = "maestro.evenements:journal"


class EventLog(ABC):
    """Journal durable des événements — le pendant persistant du bus (#97).

    `consigner` ajoute un événement en fin de journal ; `relire` rend **tous**
    les événements consignés, dans l'ordre de consignation — la matière du
    rejeu au démarrage de l'API. À la différence du bus (`EventBus`), le journal
    n'est pas éphémère : ce qu'il consigne survit à la vie du process (selon
    l'implémentation).
    """

    @abstractmethod
    async def consigner(self, event: Event) -> None:
        """Ajoute `event` en fin de journal (persistance durable selon l'implémentation)."""
        raise NotImplementedError

    @abstractmethod
    async def relire(self) -> list[Event]:
        """Rend tous les événements consignés, dans l'ordre — la matière du rejeu."""
        raise NotImplementedError

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027) —
    # seul un journal à connexions (Redis) a quelque chose à libérer.
    async def close(self) -> None:  # noqa: B027
        """Libère les ressources du journal (connexions) — no-op par défaut."""


class InMemoryEventLog(EventLog):
    """Journal en mémoire : une liste en process, aucune durabilité inter-redémarrage.

    Le journal des **tests** et d'un déploiement mono-process. Il honore le
    contrat (consigner/relire), mais son contenu vit et meurt avec le process :
    un `relire` au démarrage retrouve une liste vide, et la projection se
    reconstruit du seul flux reçu depuis — le comportement d'avant #97. La
    durabilité inter-redémarrage exige `RedisEventLog`.
    """

    def __init__(self) -> None:
        self._evenements: list[Event] = []

    async def consigner(self, event: Event) -> None:
        self._evenements.append(event)

    async def relire(self) -> list[Event]:
        # Copie : le rejeu itère pendant que la pompe peut consigner la suite.
        return list(self._evenements)


class RedisEventLog(EventLog):
    """Journal adossé à une liste Redis — la durabilité inter-redémarrage (#97).

    Consigne chaque événement par `RPUSH` (JSON compact, `Event.to_json`) sur la
    clé `cle` et les relit tous par `LRANGE 0 -1`, dans l'ordre d'insertion —
    l'ordre du rejeu. L'instance visée est celle du docker-compose (mutualisée
    avec la file #41, le bus #46 et les boîtes #44) ; la dépendance `redis` est
    déjà tirée par `celery[redis]`. La connexion est paresseuse (construite ici,
    ouverte au premier appel).
    """

    def __init__(self, url: str | None = None, *, cle: str = CLE_JOURNAL_EVENEMENTS) -> None:
        # Import local : seule la branche Redis dépend du client (le journal
        # mémoire des tests n'en a pas besoin).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)
        self._cle = cle

    async def consigner(self, event: Event) -> None:
        await self._client.rpush(self._cle, event.to_json())

    async def relire(self) -> list[Event]:
        bruts = await self._client.lrange(self._cle, 0, -1)
        return [Event.from_json(brut) for brut in bruts]

    async def close(self) -> None:
        await self._client.aclose()
