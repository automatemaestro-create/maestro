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

⚠ **On consigne en PUBLIANT, plus en consommant** (#699). Jusqu'ici le seul
écrivain du journal était la **pompe** de l'API (`app._pompe`), c'est-à-dire un
*consommateur* : la durabilité d'un événement dépendait donc de la présence d'un
consommateur vivant. Or le pub/sub Redis est éphémère et ne bufferise rien, et
un run vit depuis #441/#446 dans un **process détaché** qui publie pendant que
l'API est arrêtée — `start.sh` l'arrête et la relance à chaque `/control-tower`.
Tout ce qui était publié dans cet intervalle n'était consigné par personne, donc
perdu **définitivement** : le rejeu au démarrage rebâtissait fidèlement une
projection trouée (incident du 2026-08-28, run `811d738020d5` — une tâche finie
qui reste « en cours », une tâche démarrée qui n'a aucun statut, un run qui
annonce une tâche là où son plan en porte cinq).

La consignation vit donc là où l'événement **naît** : `BusDurable` (ci-dessous)
pour les producteurs asynchrones, `bridge.publieur_redis` pour le producteur
synchrone du pont télémétrie. Deux conséquences, et ce sont les deux moitiés du
même choix :

- la durabilité ne dépend plus de personne — un événement est acquis dès qu'il
  est publié, que l'API tourne, redémarre ou soit arrêtée ;
- l'**exactement-une-fois** est acquis *par construction* et non par un
  dédoublonnage qui n'existe pas (un `Event` n'a pas d'identifiant) : un
  événement est publié une fois, donc consigné une fois, et la pompe **ne
  consigne plus rien**. Ajouter un second écrivain sans retirer le premier
  aurait doublé chaque ligne du journal requêtable — le quatrième critère de
  #699 est là pour ça.

Un point à connaître avant d'y toucher : l'ordre du journal est désormais celui
des **publications** et non plus celui des réceptions de la pompe. Les deux
coïncident pour un producteur unique ; sur une seconde où deux process publient,
ils peuvent différer d'un cran. Ce qui compte reste vrai — le journal est
append-only, donc son rejeu rend les mêmes événements dans le même ordre, donc
les mêmes rangs (`j-0002`, `journal.py`) d'un redémarrage à l'autre.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from maestro.controltower.events import (
    CANAL_EVENEMENTS,
    REDIS_URL_DEFAUT,
    Event,
    EventBus,
    RedisEventBus,
)

_LOGGER = logging.getLogger("maestro.controltower")

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


class BusDurable(EventBus):
    """Le bus qui **consigne en publiant** — la durabilité rendue au producteur (#699).

    Un décorateur, et pas une troisième implémentation de bus : il enveloppe
    n'importe quel `EventBus` et n'importe quel `EventLog`, si bien que la
    propriété se vérifie sur les doubles mémoire des tests et se déploie sur le
    couple Redis (`bus_durable`). Le transport ne change pas d'un octet — c'est
    toujours le pub/sub qui diffuse, toujours la liste qui garde.

    `publish` consigne **puis** publie, dans cet ordre. C'est celui que tenait
    déjà la pompe (projeter, consigner, *puis* diffuser) et il a la même raison :
    un client qui reçoit un événement en direct doit le retrouver dans
    l'historique, jamais l'inverse.

    Une consignation en échec est **tracée et n'interrompt pas la publication**,
    exactement comme lorsque la pompe la portait : le flux temps réel et la
    projection valent mieux que rien, et le seul prix est que cet événement-là
    manquera au prochain rejeu. C'est la promesse d'avant, déplacée avec le
    geste — pas une promesse en moins.

    `subscribe` délègue tel quel : consigner est une affaire de producteur, et un
    abonné qui passerait par ici ne doit rien voir de différent.

    `close` ferme le bus enveloppé, et le journal **seulement s'il lui
    appartient** (`possede_le_journal`) — c'est-à-dire quand une fabrique les a
    construits tous les deux. Le journal de l'API a son propre propriétaire (le
    lifespan, qui le relit au démarrage et le referme en partant) ; le refermer
    ici en ferait une ressource à deux maîtres.
    """

    def __init__(
        self, bus: EventBus, journal: EventLog, *, possede_le_journal: bool = False
    ) -> None:
        self._bus = bus
        self._journal = journal
        self._possede_le_journal = possede_le_journal

    async def publish(self, event: Event) -> None:
        try:
            await self._journal.consigner(event)
        except Exception:
            _LOGGER.exception(
                "Échec de persistance d'un événement à la publication : il manquera "
                "au prochain rejeu au démarrage (le flux temps réel est préservé)."
            )
        await self._bus.publish(event)

    def subscribe(self) -> AsyncIterator[Event]:
        return self._bus.subscribe()

    async def close(self) -> None:
        await self._bus.close()
        if self._possede_le_journal:
            await self._journal.close()


def bus_durable(
    url: str | None = None,
    *,
    canal: str = CANAL_EVENEMENTS,
    cle: str = CLE_JOURNAL_EVENEMENTS,
) -> BusDurable:
    """Le bus de production d'un producteur **hors de l'API** (#699).

    Le pendant asynchrone de `bridge.publieur_redis` : même instance Redis, même
    canal `maestro.evenements`, même liste `maestro.evenements:journal`. C'est ce
    que construisent l'hôte détaché et les fabriques d'arbitres du moteur, là où
    un `RedisEventBus` nu publiait dans le vide dès que l'API était arrêtée.

    Il **possède** ses deux clients, donc les referme tous les deux : les
    appelants concernés fermaient déjà leur bus (`hote_detache`), et leur
    demander un second geste serait la moitié de fuite qu'on ne remarque qu'au
    trentième run. Les deux connexions sont paresseuses, comme celles qu'elles
    remplacent — se construire n'exige pas un Redis joignable.
    """
    return BusDurable(
        RedisEventBus(url, canal=canal),
        RedisEventLog(url, cle=cle),
        possede_le_journal=True,
    )
