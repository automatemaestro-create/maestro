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
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.config import ConfigError, Settings, load_settings
from maestro.controltower.events import (
    REDIS_URL_DEFAUT,
    Event,
    EventBus,
    RedisEventBus,
)
from maestro.controltower.magasin import MagasinRefuse, endpoint_lisible
from maestro.emplacements import NOM_ETAT, racine_etat
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
DOSSIER_LOCAL = NOM_ETAT

#: Un fichier de journal par **espace** (#1164), comme les clés Redis : deux
#: copies de travail ne se relisent pas l'une l'autre, et l'état du banc
#: (`<espace>.banc`) a le sien sans qu'on ait rien à régler.
SUFFIXE_SQLITE = ".sqlite3"

#: La table du journal — une ligne par événement, `rang` croissant : l'ordre de
#: consignation, donc l'ordre du rejeu.
TABLE_EVENEMENTS = "evenements"

#: La table de l'état acquis des runs (`maestro.controltower.acquis`, #1391), dans
#: le même fichier — une ligne par entrée `(run_id, cle)`, remplacée sur réécriture.
#: Déclarée ici parce que le schéma du fichier n'a qu'un auteur, `MIGRATIONS`.
TABLE_ACQUIS = "acquis"

#: Le suffixe de la sauvegarde qu'une mise à niveau prend avant de migrer (#1621) :
#: `<fichier>-sauvegarde`, à côté du fichier, sous l'état du poste — à la manière de
#: `-wal`, si bien que le ménage la range avec son journal (`maestro.menage`).
#: **Conservation** : une seule par fichier, gardée jusqu'à la mise à niveau suivante
#: de ce fichier, qui la remplace. Elle porte le fichier tel qu'il était avant sa
#: dernière migration. Un fichier qui ne porte encore aucune table n'en a pas.
SUFFIXE_SAUVEGARDE = "-sauvegarde"

#: Ce qu'une écriture attend que l'autre process rende le verrou d'écriture, en
#: millisecondes (`PRAGMA busy_timeout`) — voir `SqliteEventLog` pour le régime.
ATTENTE_VERROU_MS = 5_000

#: Entre deux essais du passage en WAL (`_passer_en_wal`), en secondes. La
#: fenêtre disputée est celle d'une réécriture d'en-tête — des microsecondes —,
#: donc on repasse vite : c'est `ATTENTE_VERROU_MS` qui borne l'attente, pas ce
#: pas.
PAS_ESSAI_WAL_S = 0.005


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

    # Hook optionnel, comme `close` : un journal en process ou dans un fichier
    # local n'a pas de service à perdre en route. Seul un journal adossé à un
    # service (Redis) peut devenir injoignable pendant que l'API tourne (#1206).
    async def sonder(self) -> None:  # noqa: B027
        """Lève si le journal ne répond plus — no-op par défaut (#1206)."""

    def lieu(self) -> str | None:
        """Où vit le journal, lisible et **sans secret** — `None` par défaut (#1206).

        C'est ce que la panne nomme : un diagnostic doit dire où il a frappé.
        """
        return None


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

        self._url = url or REDIS_URL_DEFAUT
        self._client = redis_asyncio.Redis.from_url(self._url)
        # Le journal de **l'espace de la stack** (#1164) : c'est lui que l'API
        # rejoue au démarrage, donc lui qui décide de ce qu'une stack voit.
        self._cle = cle if cle is not None else nom_redis(CLE_JOURNAL_EVENEMENTS)

    async def sonder(self) -> None:
        # Le même client que le journal : c'est **lui** qui doit répondre, pas
        # une seconde connexion qui pourrait réussir à sa place.
        await self._client.ping()

    def lieu(self) -> str | None:
        return f"Redis, {endpoint_lisible(self._url)}"

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

    **Le schéma du fichier** n'a qu'un auteur, `MIGRATIONS` : à l'ouverture, le
    fichier est mis à niveau après sauvegarde, ou refusé s'il est plus récent que le
    code (`ouvrir_sqlite`, #1621). Un refus se dit au démarrage (`SchemaLocalRefuse`).

    **Le régime de concurrence, écrit ici plutôt que découvert en production.**
    SQLite n'a pas le modèle de Redis : un seul écrivain à la fois sur la base,
    là où Redis sérialise N clients. Trois réglages, et ce qu'ils promettent :

    - `journal_mode=WAL` — les lecteurs ne bloquent pas l'écrivain et
      réciproquement, et **plusieurs process** peuvent ouvrir la même base. C'est
      ce qui permet à une API et à un producteur voisin d'appendre au même
      journal. Le **passage** en WAL est le seul geste que `busy_timeout` ne
      couvre pas, et il s'attend donc à la main (`_passer_en_wal`) ;
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

    def __init__(
        self,
        chemin: Path | str | None = None,
        *,
        migrations: Sequence[Migration] | None = None,
    ) -> None:
        self._chemin = Path(chemin) if chemin is not None else chemin_sqlite()
        # `None` : le schéma du code (`MIGRATIONS`). Nommées, ce sont celles d'une
        # version à venir — ce par quoi les tests jouent une migration qui échoue.
        self._migrations = migrations
        self._connexion: sqlite3.Connection | None = None
        self._refus: SchemaLocalRefuse | None = None
        self._verrou = asyncio.Lock()

    @property
    def chemin(self) -> Path:
        """Le fichier de ce journal — ce que l'annonce du démarrage montre."""
        return self._chemin

    def lieu(self) -> str | None:
        return f"SQLite, {self._chemin}"

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

    def _connexion_ouverte(self) -> sqlite3.Connection:
        """La connexion, ouverte au premier accès — le fichier mis au schéma du code.

        Un refus du schéma est **retenu** pour la vie du journal : la pompe de l'API
        retente le rejeu, et chaque essai rejouerait sur le fichier de la personne
        sauvegarde, migration et restauration pour le même échec. Relancer Maestro
        réessaie, et c'est le geste que le refus nomme.
        """
        if self._refus is not None:
            # Sans sa pile : relevé à chaque essai, il l'allongerait d'autant.
            raise self._refus.with_traceback(None)
        if self._connexion is None:
            try:
                self._connexion = ouvrir_sqlite(self._chemin, migrations=self._migrations)
            except SchemaLocalRefuse as refus:
                self._refus = refus
                raise
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


# ── Le schéma du fichier local (#1621) ────────────────────────────────────────


@dataclass(frozen=True)
class Migration:
    """Une marche du schéma du fichier local : la version qu'elle atteint, et son geste.

    `appliquer` reçoit la connexion **dans la transaction de la mise à niveau** et
    n'y fait que des `execute` : ni `commit`, ni `rollback`, ni `executescript`, qui
    validerait en passant tout ce qui précède. Une marche qui referme la transaction
    est une marche en échec — ses effets ne s'annuleraient plus (`_mettre_a_niveau`).
    """

    #: La version que le fichier porte une fois la marche passée (`user_version`).
    version: int
    #: Ce qu'elle pose, en mots — ce que dit un échec.
    objet: str
    appliquer: Callable[[sqlite3.Connection], None]


def _poser_le_journal(connexion: sqlite3.Connection) -> None:
    connexion.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE_EVENEMENTS} "
        "(rang INTEGER PRIMARY KEY, charge TEXT NOT NULL)"
    )


def _poser_l_etat_acquis(connexion: sqlite3.Connection) -> None:
    # `IF NOT EXISTS` : un fichier en v1 peut déjà la porter — l'état acquis la
    # créait lui-même, sans que la version du fichier bouge (#1391).
    connexion.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE_ACQUIS} "
        "(run_id TEXT NOT NULL, cle TEXT NOT NULL, charge TEXT NOT NULL, "
        "PRIMARY KEY (run_id, cle))"
    )


#: Les marches du schéma, dans l'ordre : la n-ième porte le fichier à la version n.
#: Une marche passée **ne change plus** — un fichier qui l'a reçue ne la rejoue
#: jamais. Changer le schéma, c'est ajouter une marche en fin de liste.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "journal des événements", _poser_le_journal),
    Migration(2, "état acquis des runs", _poser_l_etat_acquis),
)

#: Version du schéma du code, posée dans `PRAGMA user_version` : la marche la plus
#: haute. Un fichier qui porte moins se met à niveau à l'ouverture ; un fichier qui
#: porte plus est refusé, sans être touché.
SCHEMA_VERSION = MIGRATIONS[-1].version

#: Codes de `sqlite3_backup_step` qui disent « verrou pris, repasser » — SQLITE_BUSY
#: et SQLITE_LOCKED.
_COPIE_OCCUPEE = frozenset({5, 6})


class SchemaLocalRefuse(MagasinRefuse):
    """Le fichier local n'est pas servi : son schéma ne se met pas au code (#1621).

    Une panne qui se nomme (`MagasinRefuse`) : le démarrage sert son titre, son
    motif et son geste tels qu'écrits ici, là où l'on sait ce qui s'est passé.
    """

    def __init__(self, chemin: Path, *, titre: str, motif: str, geste: str) -> None:
        super().__init__(titre=titre, motif=motif, geste=geste)
        self.chemin = chemin


class SchemaPlusRecent(SchemaLocalRefuse):
    """Le fichier porte un schéma que ce code ne connaît pas : il n'est ni réécrit ni rétrogradé."""

    def __init__(self, chemin: Path, *, version: int, connue: int) -> None:
        super().__init__(
            chemin,
            titre="Journal local écrit par une version plus récente de Maestro",
            motif=(
                f"le fichier porte le schéma v{version}, et cette version de Maestro ne "
                f"connaît que jusqu'au v{connue}. Il n'a pas été touché : l'ouvrir ici le "
                "rétrograderait"
            ),
            geste=(
                "mettre Maestro à jour : la version qui a écrit ce fichier, ou une plus "
                "récente, le rouvre avec tout son historique"
            ),
        )
        self.version = version
        self.connue = connue


class MigrationEchouee(SchemaLocalRefuse):
    """La mise à niveau a échoué : le fichier est remis tel qu'il était, ou le refus dit comment."""

    def __init__(
        self,
        chemin: Path,
        *,
        depuis: int,
        vers: int,
        etape: str,
        cause: str,
        sauvegarde: Path | None,
        restauration: str | None,
    ) -> None:
        if restauration is None:
            motif = (
                f"{etape} a échoué : {cause}. Le fichier a été remis tel qu'il était, au "
                f"schéma v{depuis} : rien n'est perdu, mais l'historique n'est pas servi"
            )
            geste = (
                f"relancer Maestro réessaie la mise à niveau vers v{vers} ; si elle échoue "
                "encore, la version précédente de Maestro rouvre ce fichier sans perte"
            )
        else:
            motif = (
                f"{etape} a échoué : {cause}, et le fichier n'a pas pu être remis "
                f"({restauration}). Il est peut-être à moitié migré"
            )
            geste = (
                f"Maestro arrêté, remettre la sauvegarde « {sauvegarde} » à la place de "
                f"« {chemin} », puis relancer"
                if sauvegarde is not None
                else f"Maestro arrêté, supprimer « {chemin} », qui ne portait encore aucun "
                "historique, puis relancer"
            )
        super().__init__(
            chemin, titre="Mise à niveau du journal local impossible", motif=motif, geste=geste
        )
        self.depuis = depuis
        self.vers = vers
        self.sauvegarde = sauvegarde
        self.restauree = restauration is None


def chemin_sauvegarde(chemin: Path) -> Path:
    """La sauvegarde d'avant migration de `chemin` — à côté de lui (`SUFFIXE_SAUVEGARDE`)."""
    return chemin.with_name(chemin.name + SUFFIXE_SAUVEGARDE)


def ouvrir_sqlite(
    chemin: Path, *, migrations: Sequence[Migration] | None = None
) -> sqlite3.Connection:
    """Ouvre (et crée au besoin) un fichier SQLite au régime du journal local, au schéma du code.

    Le régime de `SqliteEventLog`, écrit une fois pour tout ce qui vit dans ce
    fichier — le journal, et l'état acquis des runs (`maestro.controltower.acquis`) :
    deux écrivains du même fichier qui ne s'ouvriraient pas pareil seraient deux
    régimes de concurrence à tenir d'accord. Le **schéma** a le même auteur unique :
    `migrations` (`MIGRATIONS` par défaut), jouées ici, quel que soit l'écrivain qui
    ouvre le premier.

    Dans cet ordre :

    1. la version du fichier est **lue avant tout geste qui écrit**. Plus récente
       que le code : refus (`SchemaPlusRecent`), le fichier n'est pas touché — pas
       même passé en WAL ;
    2. le régime : WAL, `synchronous=NORMAL` ;
    3. en retard : mise à niveau (`_mettre_a_niveau`), sous le verrou d'écriture,
       après sauvegarde ; un échec remet le fichier tel qu'il était
       (`MigrationEchouee`).

    `isolation_level=None` : autocommit. Chaque écriture est une transaction, et
    il n'existe aucun geste à regrouper — la transaction implicite de `sqlite3` ne
    ferait que retenir une écriture qu'on vient de promettre. La mise à niveau
    ouvre la sienne, explicitement.
    """
    marches = tuple(MIGRATIONS if migrations is None else migrations)
    cible = _version_cible(marches)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    connexion = sqlite3.connect(str(chemin), check_same_thread=False, isolation_level=None)
    try:
        # `busy_timeout` **en premier**, avant tout geste qui peut trouver le
        # verrou pris : il ne vaut que pour les instructions qui le suivent.
        connexion.execute(f"PRAGMA busy_timeout={ATTENTE_VERROU_MS}")
        version = _version(connexion)
        if version > cible:
            raise SchemaPlusRecent(chemin, version=version, connue=cible)
        _passer_en_wal(connexion, chemin)
        connexion.execute("PRAGMA synchronous=NORMAL")
        if version < cible:
            _mettre_a_niveau(connexion, chemin, marches, cible)
    except BaseException:
        connexion.close()
        raise
    return connexion


def _version_cible(marches: Sequence[Migration]) -> int:
    """La version que `marches` atteint — refusées si elles ne vont pas de 1 à n, sans trou."""
    versions = [marche.version for marche in marches]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"migrations du journal local hors d'ordre : versions {versions}")
    return len(versions)


def _version(connexion: sqlite3.Connection) -> int:
    # Curseur refermé tout de suite : une instruction laissée ouverte garderait un
    # instantané de lecture, et la prise du verrou d'écriture qui suit le heurterait.
    curseur = connexion.execute("PRAGMA user_version")
    try:
        ligne = curseur.fetchone()
    finally:
        curseur.close()
    return int(ligne[0]) if ligne is not None else 0


def _mettre_a_niveau(  # noqa: C901
    connexion: sqlite3.Connection, chemin: Path, marches: Sequence[Migration], cible: int
) -> None:
    """Joue les marches qui manquent au fichier, une fois, sous le verrou, après sauvegarde.

    **Une seule fois**, même à deux écrivains : `BEGIN IMMEDIATE` prend le verrou
    d'écriture avant de relire la version. Le second à ouvrir attend ici
    (`busy_timeout`), puis relit une version déjà à jour et n'a rien à faire.

    **Tout ou rien** : toutes les marches et leurs `user_version` vivent dans une
    seule transaction, et le fichier ne porte la version cible qu'au `COMMIT`. Un
    échec l'annule, puis **remet la sauvegarde** : l'annulation ne couvre pas une
    marche qui aurait validé en route, la sauvegarde si. Un fichier vide n'a pas de
    sauvegarde ; il est remis vide.
    """
    connexion.execute("BEGIN IMMEDIATE")
    try:
        depuis = _version(connexion)
        if depuis > cible:
            raise SchemaPlusRecent(chemin, version=depuis, connue=cible)
        if depuis == cible:
            connexion.execute("COMMIT")
            return
        a_sauver = _porte_un_schema(connexion)
    except BaseException:
        _annuler(connexion)
        raise
    sauvegarde: Path | None = None
    if a_sauver:
        try:
            sauvegarde = _sauvegarder(chemin)
        except Exception as cause:
            _annuler(connexion)
            raise MigrationEchouee(
                chemin,
                depuis=depuis,
                vers=cible,
                etape=f"la sauvegarde d'avant migration (« {chemin_sauvegarde(chemin)} »)",
                cause=_cause(cause),
                sauvegarde=None,
                restauration=None,
            ) from cause
    etape = f"la validation de la mise à niveau vers v{cible}"
    try:
        for marche in marches:
            if marche.version <= depuis:
                continue
            etape = f"la migration v{marche.version} ({marche.objet})"
            marche.appliquer(connexion)
            if not connexion.in_transaction:
                raise sqlite3.ProgrammingError(
                    "elle a validé la transaction de la mise à niveau, ses effets ne "
                    "s'annulent plus"
                )
            connexion.execute(f"PRAGMA user_version={marche.version}")
        etape = f"la validation de la mise à niveau vers v{cible}"
        connexion.execute("COMMIT")
    except Exception as cause:
        _annuler(connexion)
        raise MigrationEchouee(
            chemin,
            depuis=depuis,
            vers=cible,
            etape=etape,
            cause=_cause(cause),
            sauvegarde=sauvegarde,
            restauration=_restaurer(connexion, sauvegarde, depuis),
        ) from cause
    except BaseException:
        _annuler(connexion)
        raise


def _porte_un_schema(connexion: sqlite3.Connection) -> bool:
    """Le fichier porte au moins une table, un index, une vue — il y a quelque chose à sauver."""
    curseur = connexion.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    try:
        return curseur.fetchone() is not None
    finally:
        curseur.close()


def _sauvegarder(chemin: Path) -> Path:
    """Copie `chemin` dans sa sauvegarde par l'API de sauvegarde de SQLite, et la rend.

    Depuis une **seconde** connexion : celle qui migre tient le verrou d'écriture,
    et SQLite refuse de sauvegarder depuis la connexion qui écrit (`SQLITE_LOCKED`,
    que `Connection.backup` réessaierait sans fin). En WAL, ce lecteur voit le
    fichier d'avant la migration, et le verrou tenu garantit que personne ne l'a
    changé depuis. Écrite sous un nom provisoire puis renommée : une copie coupée
    en route ne prend jamais la place de la sauvegarde.
    """
    sauvegarde = chemin_sauvegarde(chemin)
    provisoire = sauvegarde.with_name(sauvegarde.name + ".partielle")
    provisoire.unlink(missing_ok=True)
    source = sqlite3.connect(str(chemin), check_same_thread=False)
    try:
        destination = sqlite3.connect(str(provisoire), isolation_level=None)
        try:
            _copier(source, destination)
            # Une copie qui se suffit à elle-même : elle hérite du mode WAL de la
            # source, et un `-wal` à côté d'elle serait une moitié qu'on oublie.
            destination.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            destination.close()
    finally:
        source.close()
    # Les voisins d'une sauvegarde précédente rouverte à la main : laissés là, SQLite
    # les rejouerait sur la nouvelle.
    for voisin in ("-wal", "-shm"):
        sauvegarde.with_name(sauvegarde.name + voisin).unlink(missing_ok=True)
    os.replace(provisoire, sauvegarde)
    return sauvegarde


def _restaurer(
    connexion: sqlite3.Connection, sauvegarde: Path | None, depuis: int
) -> str | None:
    """Remet le fichier tel qu'il était : `None` si c'est fait, sinon ce qui l'a empêché."""
    try:
        source = (
            sqlite3.connect(str(sauvegarde))
            if sauvegarde is not None
            # Pas de sauvegarde : le fichier ne portait rien, une base vide le remet.
            else sqlite3.connect(":memory:")
        )
        try:
            _copier(source, connexion)
        finally:
            source.close()
        remise = _version(connexion)
    except Exception as erreur:  # noqa: BLE001 - le refus dit ce qui a manqué
        return _cause(erreur)
    if remise != depuis:
        return f"le fichier remis porte le schéma v{remise}, et non v{depuis}"
    return None


def _copier(source: sqlite3.Connection, destination: sqlite3.Connection) -> None:
    """`Connection.backup` borné : un verrou qui ne se rend pas lève au lieu d'attendre sans fin.

    `sqlite3` repasse indéfiniment sur `SQLITE_BUSY`/`SQLITE_LOCKED` ; la borne est
    celle des écritures, `ATTENTE_VERROU_MS`.
    """
    echeance = time.monotonic() + ATTENTE_VERROU_MS / 1000

    def _borner(statut: int, _restantes: int, _total: int) -> None:
        if statut in _COPIE_OCCUPEE and time.monotonic() >= echeance:
            raise sqlite3.OperationalError(
                f"copie interrompue : verrou non rendu après {ATTENTE_VERROU_MS} ms"
            )

    source.backup(destination, progress=_borner)


def _annuler(connexion: sqlite3.Connection) -> None:
    """Annule la transaction en cours, s'il y en a une — sans jamais masquer la cause."""
    if connexion.in_transaction:
        try:
            connexion.execute("ROLLBACK")
        except sqlite3.Error:
            _LOGGER.warning("Annulation de la mise à niveau du journal local impossible.")


def _cause(erreur: BaseException) -> str:
    return str(erreur) or type(erreur).__name__


def _passer_en_wal(connexion: sqlite3.Connection, chemin: Path) -> None:
    """Passe la base en WAL en **attendant son tour** — la borne est `ATTENTE_VERROU_MS`.

    Le seul geste de l'ouverture que `busy_timeout` ne couvre pas. Changer le mode
    de journal exige un accès exclusif le temps de réécrire l'en-tête, et SQLite
    rend alors `SQLITE_BUSY` **sur-le-champ** sans appeler le gestionnaire
    d'attente : poser `busy_timeout` avant ne suffit pas, c'est mesuré — 45
    « database is locked » sur 300 ouvertures concurrentes, l'attente posée
    d'abord. Or c'est exactement là que se rencontrent les deux connexions du
    régime décrit dans `SqliteEventLog` — l'API et un producteur voisin qui
    démarrent ensemble sur un fichier neuf.

    On attend donc à la main ce que `busy_timeout` aurait attendu : la fenêtre
    disputée est une réécriture d'en-tête, et le perdant retrouve au second essai
    une base **déjà** en WAL, où il n'a plus rien à changer. Au-delà de la borne,
    l'erreur passe — cinq secondes sur ce verrou-là disent que quelque chose est
    cassé, et un journal qui resterait en mode rollback perdrait justement
    l'ouverture multi-process qu'on promet.
    """
    echeance = time.monotonic() + ATTENTE_VERROU_MS / 1000
    while True:
        try:
            ligne = connexion.execute("PRAGMA journal_mode=WAL").fetchone()
        except sqlite3.OperationalError:
            if time.monotonic() >= echeance:
                raise
        else:
            if ligne is not None and str(ligne[0]).lower() == "wal":
                return
            # Pas d'erreur et pas WAL : un lecteur tient la base. Même attente, et
            # une erreur franche si elle ne se libère pas.
            if time.monotonic() >= echeance:
                mode = ligne[0] if ligne is not None else "inconnu"
                raise sqlite3.OperationalError(
                    f"journal SQLite « {chemin} » : passage en WAL refusé "
                    f"après {ATTENTE_VERROU_MS} ms (mode resté « {mode} »)"
                )
        time.sleep(PAS_ESSAI_WAL_S)


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
        etat = racine_etat()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - dépend de l'environnement
        raise ConfigError(
            "Dossier personnel introuvable : nommez le fichier du journal local "
            "par MAESTRO_SQLITE_FICHIER."
        ) from exc
    return etat / f"{espace_courant(environnement).nom}{SUFFIXE_SQLITE}"


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
