"""Un faux serveur Redis **partagé** entre toutes les stacks d'un test (#1164).

Le harnais de `tests/test_espace.py` et de `tests/test_etat_banc.py` — un seul
double de Redis, comme `tests/harnais_forge.py` est le seul double de forge.

Ce qu'il imite, et pourquoi : une **instance** Redis — des listes, des hashes, et
des canaux Pub/Sub partagés par tous ses clients, comme une vraie instance les
partage entre toutes ses bases. C'est ce partage qui fait l'intérêt d'un test de
séparation : deux stacks qui se verraient par le nom se verraient ici aussi. Les
deux clients de production y sont servis — le synchrone (`redis.Redis`, hôtes
détachés, pont, purge) et l'asynchrone (`redis.asyncio.Redis`, l'API) —, et les
valeurs rendues sont des **octets**, comme le vrai client par défaut.

    serveur = brancher(monkeypatch)       # remplace `from_url` des deux clients
    ClientSynchrone(serveur).lrange(…)    # lire ce qu'une stack a écrit
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import redis
import redis.asyncio


class ServeurFactice:
    """Une instance Redis : des listes, des hashes, et des canaux **partagés**."""

    def __init__(self) -> None:
        self.listes: dict[str, list[bytes]] = {}
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.abonnements: list[AbonnementFactice] = []

    def publier(self, canal: str, donnee: str | bytes) -> int:
        brut = donnee.encode() if isinstance(donnee, str) else donnee
        recus = 0
        for abonnement in self.abonnements:
            if canal in abonnement.canaux:
                abonnement.recevoir(canal, brut)
                recus += 1
        return recus

    def cles(self) -> set[str]:
        """Les clés qui portent quelque chose — une liste vide n'existe pas pour Redis."""
        return {c for c, v in self.listes.items() if v} | {c for c, v in self.hashes.items() if v}


class AbonnementFactice:
    """Un `pubsub()` asynchrone : ce qu'il écoute, et ce qu'il a reçu."""

    def __init__(self, serveur: ServeurFactice) -> None:
        self._serveur = serveur
        self.canaux: set[str] = set()
        self.recus: list[tuple[str, bytes]] = []
        self._file: asyncio.Queue[dict[str, Any]] | None = None

    def recevoir(self, canal: str, brut: bytes) -> None:
        self.recus.append((canal, brut))
        if self._file is not None:
            self._file.put_nowait({"type": "message", "channel": canal, "data": brut})

    async def subscribe(self, *canaux: str) -> None:
        self.canaux.update(canaux)
        if self not in self._serveur.abonnements:
            self._serveur.abonnements.append(self)

    async def unsubscribe(self, *canaux: str) -> None:
        self.canaux.difference_update(canaux)

    async def get_message(self, **_options: Any) -> dict[str, Any] | None:
        if self._file is None:
            self._file = asyncio.Queue()
        return None if self._file.empty() else self._file.get_nowait()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        if self._file is None:
            self._file = asyncio.Queue()
        while True:
            yield await self._file.get()

    async def aclose(self) -> None:
        if self in self._serveur.abonnements:
            self._serveur.abonnements.remove(self)


class TuyauFactice:
    """Le `MULTI`/`EXEC` du client synchrone de `publieur_redis`."""

    def __init__(self, client: ClientSynchrone) -> None:
        self._client = client
        self._gestes: list[tuple[str, str, str]] = []

    def rpush(self, cle: str, valeur: str) -> TuyauFactice:
        self._gestes.append(("rpush", cle, valeur))
        return self

    def publish(self, canal: str, valeur: str) -> TuyauFactice:
        self._gestes.append(("publish", canal, valeur))
        return self

    def execute(self) -> list[Any]:
        for geste, cible, valeur in self._gestes:
            getattr(self._client, geste)(cible, valeur)
        self._gestes.clear()
        return []


class ClientSynchrone:
    """Le client `redis.Redis` — hôtes détachés, pont télémétrie, purge, état du banc."""

    def __init__(self, serveur: ServeurFactice) -> None:
        self.serveur = serveur

    def ping(self) -> bool:
        return True

    def rpush(self, cle: str, *valeurs: str | bytes) -> int:
        liste = self.serveur.listes.setdefault(cle, [])
        liste.extend(v.encode() if isinstance(v, str) else v for v in valeurs)
        return len(liste)

    def lrange(self, cle: str, debut: int, fin: int) -> list[bytes]:
        valeurs = self.serveur.listes.get(cle, [])
        return list(valeurs[debut:] if fin == -1 else valeurs[debut : fin + 1])

    def llen(self, cle: str) -> int:
        return len(self.serveur.listes.get(cle, []))

    def hset(self, cle: str, champ: str, valeur: str) -> int:
        self.serveur.hashes.setdefault(cle, {})[champ.encode()] = valeur.encode()
        return 1

    def hgetall(self, cle: str) -> dict[bytes, bytes]:
        return dict(self.serveur.hashes.get(cle, {}))

    def hlen(self, cle: str) -> int:
        return len(self.serveur.hashes.get(cle, {}))

    def hdel(self, cle: str, *champs: str) -> int:
        table = self.serveur.hashes.get(cle, {})
        return sum(table.pop(c.encode(), None) is not None for c in champs)

    def delete(self, *cles: str) -> int:
        return sum(
            (self.serveur.listes.pop(c, None) is not None)
            + (self.serveur.hashes.pop(c, None) is not None)
            for c in cles
        )

    def scan_iter(self, match: str | None = None, count: int | None = None) -> Iterator[bytes]:
        for cle in sorted(self.serveur.cles()):
            if match is None or fnmatch.fnmatchcase(cle, match):
                yield cle.encode()

    def publish(self, canal: str, donnee: str | bytes) -> int:
        return self.serveur.publier(canal, donnee)

    def pipeline(self) -> TuyauFactice:
        return TuyauFactice(self)

    def close(self) -> None:
        return None


class ClientAsynchrone:
    """Le client `redis.asyncio.Redis` — l'API, le bus, le journal, les boîtes."""

    def __init__(self, serveur: ServeurFactice) -> None:
        self._sync = ClientSynchrone(serveur)
        self.serveur = serveur

    async def rpush(self, cle: str, *valeurs: str | bytes) -> int:
        return self._sync.rpush(cle, *valeurs)

    async def lrange(self, cle: str, debut: int, fin: int) -> list[bytes]:
        return self._sync.lrange(cle, debut, fin)

    async def hset(self, cle: str, champ: str, valeur: str) -> int:
        return self._sync.hset(cle, champ, valeur)

    async def hgetall(self, cle: str) -> dict[bytes, bytes]:
        return self._sync.hgetall(cle)

    async def hdel(self, cle: str, *champs: str) -> int:
        return self._sync.hdel(cle, *champs)

    async def publish(self, canal: str, donnee: str | bytes) -> int:
        return self._sync.publish(canal, donnee)

    def pubsub(self) -> AbonnementFactice:
        return AbonnementFactice(self.serveur)

    async def aclose(self) -> None:
        return None


def brancher(monkeypatch: pytest.MonkeyPatch) -> ServeurFactice:
    """Une seule instance, servie à toutes les fabriques de clients de toutes les stacks.

    `from_url` est remplacée sur les deux classes : les modules importent `redis`
    localement et résolvent l'attribut à l'appel.
    """
    instance = ServeurFactice()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: ClientSynchrone(instance))
    monkeypatch.setattr(
        redis.asyncio.Redis, "from_url", lambda *_a, **_k: ClientAsynchrone(instance)
    )
    return instance
