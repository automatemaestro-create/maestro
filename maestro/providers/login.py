"""Connecting a provider's access from the screen: the contract a login keeps (#1704).

Some accesses need the person to connect an account once on the workstation — the
Claude subscription does, by the browser. When an access knows how to open that
login itself, it declares it (`AccesFournisseur.ouvrir_connexion`), and the API
runs it for the screen: no terminal, no command to type.

A login is followed by its state, read from typed facts only — the exit code of a
process, the answer of a status command — never from a text a tool printed:

- `pending`: the authorization page is open, and the login waits for the person;
- `connected`: the login ended, and the account is connected;
- `failed`: the login ended without connecting; `detail` names the typed fact;
- `cancelled`: the person cancelled it, or another login replaced it.

`url` is the authorization page, so the screen can open it again when the browser
did not show it. It carries no secret: the proof that completes the login stays in
the process that started it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: The states of a login, in the order a login goes through them.
LOGIN_STATES: tuple[str, ...] = ("pending", "connected", "failed", "cancelled")

#: The states a login does not leave.
FINAL_STATES: frozenset[str] = frozenset({"connected", "failed", "cancelled"})


@dataclass(frozen=True)
class LoginState:
    """Where a login stands, as the API serves it."""

    state: str
    url: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in LOGIN_STATES:
            raise ValueError(f"unknown login state: {self.state!r}")

    @property
    def final(self) -> bool:
        return self.state in FINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "url": self.url, "detail": self.detail}


class Login(Protocol):
    """One login of an access, opened by the API and followed until it ends."""

    async def start(self) -> LoginState:
        """Opens the login and returns once the authorization page is known, or the login ended."""
        ...

    def state(self) -> LoginState:
        """Where the login stands now — cheap, called each time the screen asks."""
        ...

    def cancel(self) -> LoginState:
        """Ends a pending login; a login already ended keeps its state."""
        ...
