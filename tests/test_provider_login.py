"""Connecting the subscription from the screen: the login the API opens and follows (#1704).

What is kept here, in the order the login goes:

① **the relay** (`maestro.browser_relay`) — the `BROWSER` command Maestro gives the
   CLI leads the authorization page's address into a file, through a real shell,
   quoted paths and all;
② **the login** (`ClaudeLogin`) — it runs `auth login` in Maestro's folder, opens
   the page the CLI hands over, waits, and reads the outcome in typed facts only:
   the exit code, confirmed by the status; cancelling ends the CLI;
③ **the routes** (`/api/fournisseurs/login`) — one login at a time, a refused
   access is a 422, and the screen learns which accesses open their login.

No network and no real CLI: the CLI is a script that calls `BROWSER` as the real
one does, then waits for a callback the test sends by writing a file.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro import browser_relay
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.provider_login import ProviderLoginService
from maestro.emplacements import racine_jetable
from maestro.providers.base import AccesFournisseur
from maestro.providers.claude import ClaudeProvider
from maestro.providers.claude_login import WORKSHOP_PREFIX, ClaudeLogin, exit_detail
from maestro.providers.login import LoginState

PAGE = "https://example.test/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A9%2Fcallback"

#: The CLI's login, as far as Maestro sees it: it runs `BROWSER` with the page,
#: then waits for the page's callback (here, a file), and exits with a code.
FAKE_CLI = textwrap.dedent(
    """
    import os, subprocess, sys, time
    from pathlib import Path

    assert sys.argv[1:] == ["auth", "login"], sys.argv
    Path(os.environ["FAKE_SEEN"]).write_text(os.environ["CLAUDE_CONFIG_DIR"], encoding="utf-8")
    if os.environ.get("FAKE_MODE") != "no-page":
        subprocess.run(os.environ["BROWSER"] + ' "' + os.environ["FAKE_PAGE"] + '"', shell=True)
    callback = Path(os.environ["FAKE_CALLBACK"])
    for _ in range(600):
        if callback.exists():
            sys.exit(int(callback.read_text(encoding="utf-8")))
        time.sleep(0.05)
    sys.exit(9)
    """
)


@dataclass
class Bench:
    folder: Path
    callback: Path
    seen: Path
    opened: list[str]
    script: Path

    def login(self, **kwargs: Any) -> ClaudeLogin:
        options: dict[str, Any] = {
            "command": [sys.executable, str(self.script)],
            "open_browser": self.opened.append,
            "check_login": lambda folder: True,
        }
        options.update(kwargs)
        return ClaudeLogin(self.folder, **options)

    def callback_with(self, code: int) -> None:
        self.callback.write_text(str(code), encoding="utf-8")


@pytest.fixture()
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    bench = Bench(
        folder=tmp_path / "dossier du cli",
        callback=tmp_path / "callback",
        seen=tmp_path / "seen",
        opened=[],
        script=script,
    )
    monkeypatch.setenv("FAKE_PAGE", PAGE)
    monkeypatch.setenv("FAKE_CALLBACK", str(bench.callback))
    monkeypatch.setenv("FAKE_SEEN", str(bench.seen))
    return bench


def _until_final(login: ClaudeLogin, timeout_s: float = 20.0) -> LoginState:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = login.state()
        if state.final:
            return state
        time.sleep(0.05)
    raise AssertionError(f"the login is still {login.state().state}")


# --- ① The relay ----------------------------------------------------------------------


def test_the_relay_writes_the_page_it_receives_through_a_shell(tmp_path: Path) -> None:
    target = tmp_path / "un dossier avec espaces" / "url"
    target.parent.mkdir()

    subprocess.run(f'{browser_relay.relay_command(target)} "{PAGE}"', shell=True, check=True)

    assert target.read_text(encoding="utf-8") == PAGE


def test_the_relay_refuses_what_is_not_a_page(tmp_path: Path) -> None:
    target = tmp_path / "url"

    assert browser_relay.main([str(target), "file:///etc/passwd"]) == 2
    assert not target.exists()


# --- ② The login ----------------------------------------------------------------------


def test_the_login_opens_the_page_the_cli_hands_over_then_waits(bench: Bench) -> None:
    login = bench.login()

    state = asyncio.run(login.start())

    assert state == LoginState("pending", url=PAGE)
    assert bench.opened == [PAGE]
    # The CLI runs in Maestro's folder, which the login creates.
    assert Path(bench.seen.read_text(encoding="utf-8")) == bench.folder
    assert bench.folder.is_dir()
    assert login.state() == LoginState("pending", url=PAGE)
    login.cancel()


def test_a_login_that_ends_well_is_connected_once_the_status_says_so(bench: Bench) -> None:
    asked: list[Path] = []
    login = bench.login(check_login=lambda folder: asked.append(folder) or True)
    asyncio.run(login.start())

    bench.callback_with(0)

    assert _until_final(login) == LoginState("connected")
    assert asked == [bench.folder]
    # The outcome is kept: the status is asked once.
    assert login.state() == LoginState("connected")
    assert asked == [bench.folder]


def test_a_login_that_exits_well_without_an_account_is_a_failure(bench: Bench) -> None:
    login = bench.login(check_login=lambda folder: False)
    asyncio.run(login.start())

    bench.callback_with(0)

    assert _until_final(login) == LoginState("failed", detail="auth status : non connecté")


def test_a_status_that_cannot_tell_does_not_undo_a_successful_exit(bench: Bench) -> None:
    login = bench.login(check_login=lambda folder: None)
    asyncio.run(login.start())

    bench.callback_with(0)

    assert _until_final(login) == LoginState("connected")


def test_a_failed_login_names_its_exit_code(bench: Bench) -> None:
    login = bench.login()
    asyncio.run(login.start())

    bench.callback_with(1)

    assert _until_final(login) == LoginState("failed", detail="code de sortie 1")


@pytest.mark.parametrize(
    ("code", "detail"),
    [
        (1, "code de sortie 1"),
        (-9, "code de sortie -9"),
        (4294967295, "code de sortie 0xFFFFFFFF"),
        (3221225794, "code de sortie 0xC0000142"),
    ],
)
def test_a_windows_exit_code_reads_in_hexadecimal(code: int, detail: str) -> None:
    assert exit_detail(code) == detail


def test_cancelling_ends_the_cli_and_the_outcome_stays(bench: Bench) -> None:
    login = bench.login()
    asyncio.run(login.start())
    process = login._process
    assert process is not None

    assert login.cancel() == LoginState("cancelled")

    assert process.poll() is not None
    bench.callback_with(0)
    assert login.state() == LoginState("cancelled")


def test_a_cli_that_hands_over_no_page_is_ended(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "no-page")
    login = bench.login(url_timeout_s=1.0)

    state = asyncio.run(login.start())

    assert state == LoginState("failed", detail="aucune page de connexion rendue")
    assert login._process is not None and login._process.poll() is not None
    assert bench.opened == []


def test_the_page_reaches_the_screen_when_the_browser_does_not_open(bench: Bench) -> None:
    def broken(url: str) -> None:
        raise OSError("no browser")

    login = bench.login(open_browser=broken)

    assert asyncio.run(login.start()) == LoginState("pending", url=PAGE)
    login.cancel()


def test_the_relay_folder_is_disposable_and_removed_once_the_login_ends(bench: Bench) -> None:
    login = bench.login()
    asyncio.run(login.start())
    workshop = login._workshop
    assert workshop is not None and workshop.is_dir()
    assert workshop.parent == racine_jetable() and workshop.name.startswith(WORKSHOP_PREFIX)

    login.cancel()

    assert not workshop.exists()


# --- ③ The routes ---------------------------------------------------------------------


class FakeLogin:
    """A login the test drives: pending until the test ends it."""

    created: list[FakeLogin] = []

    def __init__(self) -> None:
        self.outcome: LoginState | None = None
        FakeLogin.created.append(self)

    async def start(self) -> LoginState:
        return LoginState("pending", url=PAGE)

    def state(self) -> LoginState:
        return self.outcome or LoginState("pending", url=PAGE)

    def cancel(self) -> LoginState:
        if self.outcome is None:
            self.outcome = LoginState("cancelled")
        return self.outcome


class FakeProvider:
    ACCES = (
        AccesFournisseur(id="compte", libelle="Compte", ouvrir_connexion=FakeLogin),
        AccesFournisseur(id="cle", libelle="Clé", secret="FAKE_KEY", cle_requise=True),
    )


def _app() -> Any:
    service = ProviderLoginService(registry=lambda: {"faux": FakeProvider})  # type: ignore[dict-item]
    return create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), connexion_fournisseur=service
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    FakeLogin.created.clear()
    app = _app()
    with TestClient(app) as client:
        yield client


COMPTE = {"fournisseur": "faux", "acces": "compte"}


def test_no_login_is_served_before_one_is_opened(client: TestClient) -> None:
    assert client.get("/api/fournisseurs/login").json() == {"login": None}


def test_opening_a_login_serves_its_page_then_its_outcome(client: TestClient) -> None:
    reponse = client.post("/api/fournisseurs/login", json=COMPTE)

    assert reponse.status_code == 200
    attendu = {
        "provider": "faux", "access": "compte", "state": "pending", "url": PAGE, "detail": ""
    }
    assert reponse.json() == {"login": attendu}
    assert client.get("/api/fournisseurs/login").json() == {"login": attendu}

    FakeLogin.created[0].outcome = LoginState("connected")
    assert client.get("/api/fournisseurs/login").json()["login"]["state"] == "connected"


def test_a_login_is_cancelled_by_the_screen(client: TestClient) -> None:
    client.post("/api/fournisseurs/login", json=COMPTE)

    reponse = client.delete("/api/fournisseurs/login")

    assert reponse.json()["login"]["state"] == "cancelled"
    assert client.get("/api/fournisseurs/login").json()["login"]["state"] == "cancelled"


def test_opening_a_second_login_cancels_the_pending_one(client: TestClient) -> None:
    client.post("/api/fournisseurs/login", json=COMPTE)
    client.post("/api/fournisseurs/login", json=COMPTE)

    premier, second = FakeLogin.created
    assert premier.state() == LoginState("cancelled")
    assert second.state() == LoginState("pending", url=PAGE)


@pytest.mark.parametrize(
    "corps",
    [
        {"fournisseur": "inconnu", "acces": "compte"},
        {"fournisseur": "faux", "acces": "inconnu"},
        {"fournisseur": "faux", "acces": "cle"},
    ],
)
def test_an_access_without_a_login_to_open_is_refused(
    client: TestClient, corps: dict[str, str]
) -> None:
    reponse = client.post("/api/fournisseurs/login", json=corps)

    assert reponse.status_code == 422
    assert FakeLogin.created == []


def test_a_pending_login_ends_with_the_api() -> None:
    FakeLogin.created.clear()
    app = _app()
    with TestClient(app) as client:
        client.post("/api/fournisseurs/login", json=COMPTE)

    assert FakeLogin.created[0].state() == LoginState("cancelled")


def test_the_subscription_says_it_opens_its_login_and_nothing_else_does() -> None:
    abonnement, cle = (acces.to_dict() for acces in ClaudeProvider.ACCES)

    assert abonnement["id"] == "abonnement" and abonnement["opens_login"] is True
    assert cle["opens_login"] is False
