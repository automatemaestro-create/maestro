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

Trois implémentations au même contrat, comme le bus (`EventBus`) et les boîtes
(`Mailbox`) :

- `InMemoryEventLog` : une liste en process — le levier des tests et d'un
  déploiement mono-process (aucune durabilité inter-redémarrage : la projection
  se reconstruit du flux reçu depuis le démarrage, comme avant #97) ;
- `RedisEventLog` : une **liste Redis** (`RPUSH`/`LRANGE`) sur l'instance déjà
  mutualisée avec la file de tâches (#41), le bus (#46) et les boîtes (#44) —
  le chemin de production. L'événement y est appendu au fil de l'eau et relu
  intégralement au démarrage, dans l'ordre d'arrivée ;
- `SqliteEventLog` (#639) : une **table SQLite** dans un fichier, sans aucun
  service à installer — le mode **local**, celui d'un produit qu'on pose sur un
  poste. Même contrat, donc aucun appelant ne change : ce qui bascule est un
  **réglage** (`MAESTRO_PERSISTANCE`, `support_persistance` ci-dessous), jamais
  un embranchement de code (ENF-12, docs/24 §4.7).

Le support se choisit **à un seul endroit**, `create_default_app`, là où sont
déjà résolus le bus, le registre des battements et l'hôte des runs : le journal
est un choix de déploiement parmi les autres, et le résoudre ailleurs en ferait
une connaissance que chaque appelant aurait à porter.

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

import asyncio
import logging
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from maestro.config import ConfigError, Settings, load_settings
from maestro.controltower.events import (
    REDIS_URL_DEFAUT,
    Event,
    EventBus,
    RedisEventBus,
)
from maestro.espace import espace_courant, nom_redis

_LOGGER = logging.getLogger("maestro.controltower")

#: Clé Redis de la liste des événements persistés — sur l'instance mutualisée
#: avec la file (#41), le bus (#46) et les boîtes (#44), d'où une clé nommée
#: proche du canal du bus (`CANAL_EVENEMENTS`) sans lui être confondue. Nom de
#: l'espace commun, rangé dans celui de la stack à la construction (#1164).
CLE_JOURNAL_EVENEMENTS = "maestro.evenements:journal"

#: Les deux supports du journal durable (`MAESTRO_PERSISTANCE`, #639) : le
#: **serveur** (Redis, le défaut et le comportement d'avant ce lot) et le
#: **local** (un fichier SQLite, aucun service à installer). Le mode de
#: distribution change les défauts, pas les fonctions (docs/24 §4.7).
SUPPORT_REDIS = "redis"
SUPPORT_SQLITE = "sqlite"
SUPPORTS = (SUPPORT_REDIS, SUPPORT_SQLITE)

#: Où vit le journal local quand rien n'est réglé : un dossier du **poste**, sous
#: le dossier personnel — jamais dans le dépôt, qu'un `git clean` emporterait et
#: qu'une installation n'a pas. `~/Maestro` est déjà le répertoire *des projets*
#: (`maestro.projets.reglages`) : ce dossier-ci porte les données du produit, pas
#: le travail de l'utilisateur, d'où un nom masqué et distinct.
DOSSIER_LOCAL = ".maestro"

#: Un fichier de journal par **espace** (#1164), comme les clés Redis : deux
#: copies de travail ne se relisent pas l'une l'autre, et l'état du banc
#: (`<espace>.banc`) a le sien sans qu'on ait rien à régler.
SUFFIXE_SQLITE = ".sqlite3"

#: La table du journal — une ligne par événement, `rang` croissant : l'ordre de
#: consignation, donc l'ordre du rejeu.
TABLE_EVENEMENTS = "evenements"

#: Version du schéma, posée dans `PRAGMA user_version` : ce par quoi une reprise
#: ultérieure (PostgreSQL, rétention) saura à quoi elle a affaire. `1` est le
#: schéma de ce lot.
SCHEMA_VERSION = 1

#: Ce qu'une écriture attend que l'autre process rende le verrou d'écriture, en
#: millisecondes (`PRAGMA busy_timeout`) — voir `SqliteEventLog` pour le régime.
ATTENTE_VERROU_MS = 5_000


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

    def __init__(self, url: str | None = None, *, cle: str | None = None) -> None:
        # Import local : seule la branche Redis dépend du client (le journal
        # mémoire des tests n'en a pas besoin).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)
        # Le journal de **l'espace de la stack** (#1164) : c'est lui que l'API
        # rejoue au démarrage, donc lui qui décide de ce qu'une stack voit.
        self._cle = cle if cle is not None else nom_redis(CLE_JOURNAL_EVENEMENTS)

    async def consigner(self, event: Event) -> None:
        await self._client.rpush(self._cle, event.to_json())

    async def relire(self) -> list[Event]:
        bruts = await self._client.lrange(self._cle, 0, -1)
        return [Event.from_json(brut) for brut in bruts]

    async def close(self) -> None:
        await self._client.aclose()


class SqliteEventLog(EventLog):
    """Journal adossé à un fichier SQLite — la durabilité **sans service** (#639).

    Une table append-only (`rang INTEGER PRIMARY KEY`, `charge TEXT`) : `consigner`
    y insère le JSON compact de l'événement (`Event.to_json`, le même octet à
    octet que sur Redis), `relire` les rend tous `ORDER BY rang`, c'est-à-dire
    dans l'ordre de consignation — l'ordre du rejeu. Aucune dépendance ajoutée :
    `sqlite3` est dans la bibliothèque standard, et c'est tout l'intérêt pour un
    produit qu'on installe en double-cliquant (docs/24 §4.6).

    **Le régime de concurrence, écrit ici plutôt que découvert en production.**
    SQLite n'a pas le modèle de Redis : un seul écrivain à la fois sur la base,
    là où Redis sérialise N clients. Trois réglages, et ce qu'ils promettent :

    - `journal_mode=WAL` — les lecteurs ne bloquent pas l'écrivain et
      réciproquement, et **plusieurs process** peuvent ouvrir la même base. C'est
      ce qui permet à une API et à un producteur voisin d'appendre au même
      journal ;
    - `busy_timeout` (`ATTENTE_VERROU_MS`) — une écriture qui trouve le verrou
      pris **attend** au lieu d'échouer sur-le-champ. Un `INSERT` d'une ligne se
      compte en fractions de milliseconde : cinq secondes d'attente ne sont pas
      un délai, c'est la borne au-delà de laquelle quelque chose est cassé ;
    - `synchronous=NORMAL` — en WAL, une transaction commise survit à l'arrêt du
      process (c'est le cas qui nous intéresse : `start.sh` arrête et relance
      l'API à chaque fois) ; seule une coupure de courant peut coûter les toutes
      dernières. C'est **strictement plus sûr** que le Redis d'`infra/` en face,
      dont les snapshots RDB perdent, eux, jusqu'à la dernière minute.

    Ce que cela ne fait pas : un bus. Le journal est ce qui **survit**, le bus ce
    qui **diffuse en direct** — deux questions distinctes, et une table qu'on
    interrogerait en boucle pour imiter un pub/sub serait la réponse à aucune des
    deux. En mode local, le transport temps réel est celui d'un déploiement
    mono-process (`InMemoryEventBus`), et c'est `create_default_app` qui le dit.

    Côté asyncio : `sqlite3` est synchrone, donc chaque accès part dans un
    thread (`asyncio.to_thread`) et un verrou asyncio sérialise les accès à la
    connexion — elle est ouverte avec `check_same_thread=False` précisément parce
    qu'un thread de service n'est pas toujours le même d'un appel à l'autre. La
    connexion est **paresseuse** (construite ici, ouverte au premier appel),
    comme celle de `RedisEventLog` : se construire n'exige ni fichier ni dossier,
    ce qui laisse `create_default_app` fabriquer l'app sans rien écrire sur le
    disque.
    """

    def __init__(self, chemin: Path | str | None = None) -> None:
        self._chemin = Path(chemin) if chemin is not None else chemin_sqlite()
        self._connexion: sqlite3.Connection | None = None
        self._verrou = asyncio.Lock()

    @property
    def chemin(self) -> Path:
        """Le fichier de ce journal — ce que l'annonce du démarrage montre."""
        return self._chemin

    async def consigner(self, event: Event) -> None:
        charge = event.to_json()
        async with self._verrou:
            await asyncio.to_thread(self._inserer, charge)

    async def relire(self) -> list[Event]:
        async with self._verrou:
            charges = await asyncio.to_thread(self._lire)
        # Hors du verrou, comme la relecture Redis : décoder N événements est du
        # calcul, et rien n'oblige une consignation concurrente à l'attendre.
        return [Event.from_json(charge) for charge in charges]

    async def close(self) -> None:
        async with self._verrou:
            await asyncio.to_thread(self._fermer)

    # ── Ce qui tourne dans le thread ──────────────────────────────────────────

    def _ouvrir(self) -> sqlite3.Connection:
        """Ouvre (et crée au besoin) le fichier, ses réglages et sa table."""
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        # `isolation_level=None` : autocommit. Un journal est append-only, chaque
        # ligne est une transaction, et il n'existe aucun geste à regrouper — la
        # transaction implicite de `sqlite3` ne ferait que retenir une écriture
        # qu'on vient de promettre.
        connexion = sqlite3.connect(
            str(self._chemin), check_same_thread=False, isolation_level=None
        )
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute(f"PRAGMA busy_timeout={ATTENTE_VERROU_MS}")
        connexion.execute("PRAGMA synchronous=NORMAL")
        connexion.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE_EVENEMENTS} "
            "(rang INTEGER PRIMARY KEY, charge TEXT NOT NULL)"
        )
        connexion.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return connexion

    def _connexion_ouverte(self) -> sqlite3.Connection:
        if self._connexion is None:
            self._connexion = self._ouvrir()
        return self._connexion

    def _inserer(self, charge: str) -> None:
        self._connexion_ouverte().execute(
            f"INSERT INTO {TABLE_EVENEMENTS} (charge) VALUES (?)", (charge,)
        )

    def _lire(self) -> list[str]:
        curseur = self._connexion_ouverte().execute(
            f"SELECT charge FROM {TABLE_EVENEMENTS} ORDER BY rang"
        )
        return [str(ligne[0]) for ligne in curseur.fetchall()]

    def _fermer(self) -> None:
        if self._connexion is not None:
            self._connexion.close()
            self._connexion = None


def support_persistance(settings: Settings | None = None) -> str:
    """Le support du journal durable — `redis` par défaut, `sqlite` en mode local.

    Une valeur inconnue est une **erreur franche** et non un repli silencieux :
    `MAESTRO_PERSISTANCE=sqlit` laisserait croire qu'un poste persiste dans son
    fichier alors qu'il parle à un Redis absent — et cela ne se verrait qu'au
    premier redémarrage, c'est-à-dire trop tard. Même parti pris que
    `MAESTRO_HOTE_RUN` (#446) et `MAESTRO_ISOLATION` (#108).
    """
    settings = settings or load_settings()
    nom = (settings.persistance or SUPPORT_REDIS).strip().lower()
    if nom not in SUPPORTS:
        raise ConfigError(
            f"MAESTRO_PERSISTANCE : support inconnu {nom!r} "
            f"(attendu : {' | '.join(SUPPORTS)}, ou vide pour « {SUPPORT_REDIS} »)."
        )
    return nom


def chemin_sqlite(
    settings: Settings | None = None, environnement: Mapping[str, str] | None = None
) -> Path:
    """Le fichier du journal local : `MAESTRO_SQLITE_FICHIER`, sinon l'espace sous `~/.maestro`.

    **Hors du dépôt** dans les deux cas par défaut : une installation n'a pas de
    clone, et un fichier rangé dans l'arbre de travail serait emporté par le
    premier ménage. Le nom dérive de l'**espace** de la stack (#1164), donc deux
    copies de travail — et l'état du banc, `<espace>.banc` — ont chacune le leur
    sans qu'on ait rien à régler. Un chemin réglé à la main, lui, est partagé par
    toute stack réglée dessus : c'est un choix explicite, comme pour les dépôts
    de fichiers déplacés par variable (`maestro.controltower.donnees`).

    Rendu **non créé** : c'est `SqliteEventLog` qui pose le dossier et le fichier,
    au premier accès.
    """
    settings = settings or load_settings()
    regle = (settings.sqlite_fichier or "").strip()
    if regle:
        return Path(regle).expanduser()
    try:
        maison = Path.home()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - dépend de l'environnement
        raise ConfigError(
            "Dossier personnel introuvable : nommez le fichier du journal local "
            "par MAESTRO_SQLITE_FICHIER."
        ) from exc
    return maison / DOSSIER_LOCAL / f"{espace_courant(environnement).nom}{SUFFIXE_SQLITE}"


def journal_configure(settings: Settings | None = None) -> EventLog:
    """Le journal durable que le **réglage** désigne — le seul point de bascule (#639).

    `redis` rend exactement l'objet d'avant ce lot, sur la même URL et la même
    clé ; `sqlite` rend le journal local. Aucun appelant d'`EventLog` ne change :
    c'est la fabrique qui sait, et elle est appelée là où se résolvent déjà le
    bus, le registre des battements et l'hôte des runs.
    """
    settings = settings or load_settings()
    if support_persistance(settings) == SUPPORT_SQLITE:
        return SqliteEventLog(chemin_sqlite(settings))
    return RedisEventLog(settings.redis_url)


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
    canal: str | None = None,
    cle: str | None = None,
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
    remplacent — se construire n'exige pas un Redis joignable. Canal et liste
    sont ceux de l'espace de la stack (#1164), résolus par les deux objets.
    """
    return BusDurable(
        RedisEventBus(url, canal=canal),
        RedisEventLog(url, cle=cle),
        possede_le_journal=True,
    )
