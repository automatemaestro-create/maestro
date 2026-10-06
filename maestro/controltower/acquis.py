"""Où un run de la Control Tower garde son **état acquis** — Redis, ou SQLite en local (#1391).

Le contrat et la forme vivent dans le moteur (`maestro.engine.acquis`) ; ce module
porte les deux magasins **durables** et le seul endroit qui choisit entre eux. Ils
suivent le support du journal durable (`MAESTRO_PERSISTANCE`, #639), et pour la
raison qui fait exister ce magasin : l'état acquis doit survivre à ce que le journal
survit — l'arrêt de l'API, l'extinction de Maestro, le process du run lui-même.

- **Serveur** (Redis, le défaut) : un hash par run, `maestro.acquis:<run_id>`, rangé
  dans l'espace de la copie (#1164) comme toute clé. Le process détaché du run
  (#443) y écrit directement — c'est lui le producteur (docs/28 §12.2) —, et l'API y
  relit. Aucune dépendance nouvelle : Redis porte déjà le bus, le journal, les
  battements (docs/28 §12.3.2).
- **Local** (SQLite) : une table `acquis` dans le **fichier du journal**, ouvert au
  même régime (`ouvrir_sqlite`). En local l'hôte des runs est celui de l'API
  (`_hote_configure`) : l'écrivain et le lecteur sont le même process, et c'est le
  fichier qui fait survivre l'état au redémarrage.

**Pourquoi pas le journal durable lui-même.** Les sorties des tâches n'ont rien à
faire sur le bus : elles partiraient au WebSocket de chaque écran ouvert et
grossiraient une projection qui n'en lit rien. Le journal dit ce qui s'est passé, ce
magasin garde ce qu'il faut pour continuer — docs/28 §12.6 ② : « le format existe,
le magasin manque ».

**Ce qui l'oublie.** Le moteur, à la fin d'un run dont toutes les tâches ont réussi
(il n'y a plus rien à reprendre) ; la purge (`maestro.controltower.purge`), avec le
reste de l'état d'exécution. Un run soldé autrement garde le sien : c'est précisément
celui qu'on voudra peut-être reprendre.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from maestro.config import Settings, load_settings
from maestro.controltower.events import REDIS_URL_DEFAUT
from maestro.controltower.persistence import (
    SUPPORT_SQLITE,
    chemin_sqlite,
    ouvrir_sqlite,
    support_persistance,
)
from maestro.engine.acquis import (
    EtatAcquis,
    MagasinAcquis,
    MagasinAcquisMemoire,
    entree_du_plan,
    entree_du_resultat,
)
from maestro.engine.executor import TaskResult
from maestro.espace import nom_redis
from maestro.orchestrator.schema import Task

#: Préfixe des clés Redis de l'état acquis : `maestro.acquis:<run_id>`, un hash par
#: run. Nom de l'espace commun, rangé dans celui de la copie à la construction
#: (#1164) — la purge le relit d'ici, jamais recopié (#830).
PREFIXE_ACQUIS = "maestro.acquis:"

#: La table de l'état acquis dans le fichier du journal local — une ligne par
#: entrée `(run_id, cle)`, remplacée sur réécriture.
TABLE_ACQUIS = "acquis"


def _texte(valeur: bytes | str) -> str:
    """Décode ce que rend le client Redis (octets par défaut) — jamais de levée."""
    return valeur.decode("utf-8", "replace") if isinstance(valeur, bytes) else str(valeur)


class MagasinAcquisRedis(MagasinAcquis):
    """L'état acquis dans Redis : `HSET`/`HGETALL`/`DEL` sur un hash par run.

    La connexion est paresseuse (construite ici, ouverte au premier appel), comme
    celle de `RegistreBattementsRedis` : se construire n'exige pas Redis, ce qui
    laisse une app se fabriquer sans lui.
    """

    def __init__(self, url: str | None = None, *, prefixe: str | None = None) -> None:
        # Import local : seule la branche Redis dépend du client.
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)
        self._prefixe = prefixe if prefixe is not None else nom_redis(PREFIXE_ACQUIS)

    def _cle(self, run_id: str) -> str:
        return f"{self._prefixe}{run_id}"

    async def poser_plan(self, run_id: str, tasks: Sequence[Task]) -> None:
        cle, valeur = entree_du_plan(tasks)
        await self._client.hset(self._cle(run_id), cle, valeur)

    async def acquerir(self, run_id: str, resultat: TaskResult) -> None:
        if not resultat.ok:
            return
        cle, valeur = entree_du_resultat(resultat)
        await self._client.hset(self._cle(run_id), cle, valeur)

    async def lire(self, run_id: str) -> EtatAcquis | None:
        bruts = await self._client.hgetall(self._cle(run_id))
        return EtatAcquis.depuis_entrees({_texte(c): _texte(v) for c, v in bruts.items()})

    async def oublier(self, run_id: str) -> None:
        await self._client.delete(self._cle(run_id))

    async def close(self) -> None:
        await self._client.aclose()


class MagasinAcquisSqlite(MagasinAcquis):
    """L'état acquis dans le fichier du journal local (#639) : une table `acquis`.

    Même régime que `SqliteEventLog`, et par le même code (`ouvrir_sqlite`) :
    connexion paresseuse, accès sérialisés par un verrou asyncio, chaque geste dans
    un thread — `sqlite3` est synchrone.
    """

    def __init__(self, chemin: Path | str | None = None) -> None:
        self._chemin = Path(chemin) if chemin is not None else chemin_sqlite()
        self._connexion: sqlite3.Connection | None = None
        self._verrou = asyncio.Lock()

    async def poser_plan(self, run_id: str, tasks: Sequence[Task]) -> None:
        await self._ecrire(run_id, *entree_du_plan(tasks))

    async def acquerir(self, run_id: str, resultat: TaskResult) -> None:
        if resultat.ok:
            await self._ecrire(run_id, *entree_du_resultat(resultat))

    async def lire(self, run_id: str) -> EtatAcquis | None:
        async with self._verrou:
            entrees = await asyncio.to_thread(self._lire, run_id)
        return EtatAcquis.depuis_entrees(entrees)

    async def oublier(self, run_id: str) -> None:
        async with self._verrou:
            await asyncio.to_thread(
                lambda: self._connexion_ouverte().execute(
                    f"DELETE FROM {TABLE_ACQUIS} WHERE run_id = ?", (run_id,)
                )
            )

    async def close(self) -> None:
        async with self._verrou:
            if self._connexion is not None:
                connexion, self._connexion = self._connexion, None
                await asyncio.to_thread(connexion.close)

    async def _ecrire(self, run_id: str, cle: str, valeur: str) -> None:
        async with self._verrou:
            await asyncio.to_thread(
                lambda: self._connexion_ouverte().execute(
                    f"INSERT OR REPLACE INTO {TABLE_ACQUIS} (run_id, cle, charge) "
                    "VALUES (?, ?, ?)",
                    (run_id, cle, valeur),
                )
            )

    def _lire(self, run_id: str) -> dict[str, str]:
        curseur = self._connexion_ouverte().execute(
            f"SELECT cle, charge FROM {TABLE_ACQUIS} WHERE run_id = ?", (run_id,)
        )
        return {str(cle): str(charge) for cle, charge in curseur.fetchall()}

    def _connexion_ouverte(self) -> sqlite3.Connection:
        if self._connexion is None:
            connexion = ouvrir_sqlite(self._chemin)
            connexion.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE_ACQUIS} "
                "(run_id TEXT NOT NULL, cle TEXT NOT NULL, charge TEXT NOT NULL, "
                "PRIMARY KEY (run_id, cle))"
            )
            self._connexion = connexion
        return self._connexion


def magasin_acquis_configure(settings: Settings | None = None) -> MagasinAcquis:
    """Le magasin que le **réglage** désigne — celui du journal durable, toujours (#1391).

    Le seul point de bascule, lu par l'API (`create_default_app`) **et** par le
    process détaché d'un run (`hote_detache`) : un écrivain et un lecteur qui
    résoudraient le magasin chacun de son côté finiraient par ne plus parler du
    même endroit.
    """
    settings = settings or load_settings()
    if support_persistance(settings) == SUPPORT_SQLITE:
        return MagasinAcquisSqlite(chemin_sqlite(settings))
    return MagasinAcquisRedis(settings.redis_url)


__all__ = [
    "PREFIXE_ACQUIS",
    "TABLE_ACQUIS",
    "MagasinAcquisMemoire",
    "MagasinAcquisRedis",
    "MagasinAcquisSqlite",
    "magasin_acquis_configure",
]
