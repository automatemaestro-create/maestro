"""The login of a provider's access, opened by the API for the screen (#1704).

When the default provider's check says an access is not connected
(`authentification`), the screen offers to connect it. This service runs that
login — the access declares how (`AccesFournisseur.ouvrir_connexion`) — and serves
where it stands until it ends (`maestro.providers.login`).

There is one login at a time on the workstation: an access's login writes in a
folder of the workstation, and two at once would race for it. Opening a login
cancels the one still pending. The login dies with the API: a login nobody can
follow any more is not left waiting for a callback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # typing only — the provider layer pulls the SDK, paid on first use
    from maestro.providers.base import ModelProvider
    from maestro.providers.login import Login, LoginState


class LoginRefused(ValueError):
    """The access does not exist, or does not open its login itself."""


@dataclass
class _Current:
    provider: str
    access: str
    login: Login


def _registry() -> Mapping[str, type[ModelProvider]]:
    from maestro.providers.factory import classes_configurables

    return classes_configurables()


class ProviderLoginService:
    """Opens, follows and cancels the login of a provider's access — one at a time."""

    def __init__(
        self, registry: Callable[[], Mapping[str, type[ModelProvider]]] | None = None
    ) -> None:
        self._registry = registry or _registry
        self._current: _Current | None = None
        self._lock = asyncio.Lock()

    async def start(self, provider: str, access: str) -> dict[str, Any]:
        """Opens the login of `provider`'s `access`, replacing a pending one.

        Raises `LoginRefused` when the access is unknown or has no login to open.
        """
        factory = self._factory(provider, access)
        async with self._lock:
            if self._current is not None:
                await asyncio.to_thread(self._current.login.cancel)
            current = _Current(provider, access, factory())
            self._current = current
            state = await current.login.start()
        return self._served(current, state)

    async def state(self) -> dict[str, Any]:
        """The login in progress or the last one, `login: null` when none was opened."""
        current = self._current
        if current is None:
            return {"login": None}
        # Reading a login that just ended asks the provider whether it is connected:
        # a status command, kept off the event loop.
        state = await asyncio.to_thread(current.login.state)
        return self._served(current, state)

    async def cancel(self) -> dict[str, Any]:
        """Cancels the pending login; a login that already ended keeps its outcome."""
        current = self._current
        if current is None:
            return {"login": None}
        return self._served(current, await asyncio.to_thread(current.login.cancel))

    def close(self) -> None:
        """Ends a pending login with the API."""
        if self._current is not None:
            self._current.login.cancel()

    def _factory(self, provider: str, access: str) -> Callable[[], Login]:
        cls = self._registry().get(provider)
        if cls is None:
            raise LoginRefused(f"Fournisseur inconnu : {provider!r}.")
        declared = next((a for a in cls.ACCES if a.id == access), None)
        if declared is None:
            raise LoginRefused(f"Accès inconnu pour {provider} : {access!r}.")
        if declared.ouvrir_connexion is None:
            raise LoginRefused(f"L'accès {access!r} de {provider} n'a pas de connexion à ouvrir.")
        return declared.ouvrir_connexion

    @staticmethod
    def _served(current: _Current, state: LoginState) -> dict[str, Any]:
        served = {"provider": current.provider, "access": current.access, **state.to_dict()}
        return {"login": served}
