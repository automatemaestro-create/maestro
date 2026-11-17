"""Connecting Maestro to Claude, once per machine: `maestro-connexion` (#1515).

Every Claude session of Maestro — agents, project lead, assistant, probe — runs the
CLI in a folder that belongs to Maestro (`maestro.emplacements.dossier_du_cli_claude`,
passed as `CLAUDE_CONFIG_DIR`). An agent at low integrity cannot write the person's
`~/.claude`, and resuming a session reads what the CLI wrote; so the CLI keeps its
login, its refreshed token and its transcripts there. That folder does not see the
login of the person's own CLI: Maestro is connected once, by this command, which
runs the CLI's own login in that folder. A `CLAUDE_CODE_OAUTH_TOKEN` or an API key
in the settings needs no login at all.

The screen connects Maestro the same way, without a terminal (#1704):
`ClaudeLogin` runs that login for the API. The CLI opens the authorization page
through the command `BROWSER` names, a public convention, and listens for the
page's callback on the workstation; Maestro points `BROWSER` at its relay
(`maestro.browser_relay`), receives the page's address, opens it itself and serves
it to the screen. The login's outcome is its exit code, confirmed by `auth status`.

Only the CLI's public commands are run (`auth login`, `auth status`); nothing the
CLI writes in its folder, and nothing it prints, is ever read here.

    maestro-connexion           # opens the CLI's login, in Maestro's folder
    maestro-connexion --etat    # says whether Maestro is connected
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

from maestro.browser_relay import relay_command
from maestro.emplacements import dossier_du_cli_claude, jetable
from maestro.installation import cli_claude_resolu
from maestro.providers.login import LoginState

#: The command's name, as `pyproject.toml` declares it.
COMMAND = "maestro-connexion"

#: How long `auth status` may take to answer.
_STATUS_TIMEOUT_S = 30.0

#: How long the CLI may take to hand over its authorization page. Past it, the
#: login is ended: a login without a page leaves the person nothing to do.
URL_TIMEOUT_S = 30.0

#: How often a starting login looks for the page's address.
_POLL_S = 0.1

#: The prefix of the folder where the relay leaves the page's address.
WORKSHOP_PREFIX = "maestro-connexion-"

#: No console window for the CLI the API runs: the person sees the browser, nothing else.
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def command() -> str:
    """What the person types in a terminal to connect Maestro on this machine.

    An installation puts the command next to its interpreter, off the `PATH`: its full
    path is named then. A clone whose package was not reinstalled has no command at
    all, and the module is named through this interpreter. A path with a space is
    quoted, so the line can be pasted as it is.
    """
    name = COMMAND + (".exe" if sys.platform == "win32" else "")
    beside = Path(sys.executable).with_name(name)
    if beside.is_file():
        return _quoted(str(beside))
    if shutil.which(COMMAND):
        return COMMAND
    return f"{_quoted(sys.executable)} -m {__name__}"


def _quoted(path: str) -> str:
    return f'"{path}"' if " " in path else path


def cli_environment(folder: Path | None = None) -> dict[str, str]:
    """This process's environment, leading the CLI into Maestro's folder."""
    target = dossier_du_cli_claude() if folder is None else folder
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(target)}


def logged_in(folder: Path | None = None) -> bool | None:
    """Whether the CLI is connected in Maestro's folder — `None` when it cannot tell.

    Asked of the CLI (`auth status`), whose answer is a JSON object carrying
    `loggedIn`. No CLI, a CLI that does not answer in time, or an answer that does
    not read: `None`, never a guess.
    """
    _, cli = cli_claude_resolu()
    if cli is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - argv built from the resolved CLI
            [str(cli), "auth", "status"],
            env=cli_environment(folder),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_STATUS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return None
    value = answer.get("loggedIn") if isinstance(answer, dict) else None
    return value if isinstance(value, bool) else None


class ClaudeLogin:
    """The CLI's login in Maestro's folder, run by the API for the screen (#1704).

    `start` runs `auth login` with `BROWSER` pointed at Maestro's relay, waits for
    the authorization page's address, opens it in the person's browser and returns
    `pending` with it. The CLI then waits for the page's callback on the
    workstation; `state` reads its exit code: `0` is confirmed by `auth status`
    (`connected`, or `failed` when the folder is still not connected), anything
    else is `failed` with the code. `cancel` ends the CLI.

    The CLI's input, output and errors go nowhere: nothing it prints is read.
    `command` is what runs the CLI, the resolved CLI by default.
    """

    def __init__(
        self,
        folder: Path | None = None,
        *,
        command: Sequence[str] | None = None,
        open_browser: Callable[[str], object] | None = None,
        check_login: Callable[[Path], bool | None] | None = None,
        url_timeout_s: float = URL_TIMEOUT_S,
    ) -> None:
        self._folder = dossier_du_cli_claude() if folder is None else folder
        self._command = None if command is None else list(command)
        self._open_browser = webbrowser.open if open_browser is None else open_browser
        self._check_login = logged_in if check_login is None else check_login
        self._url_timeout_s = url_timeout_s
        self._process: subprocess.Popen[bytes] | None = None
        self._workshop: Path | None = None
        self._url: str | None = None
        self._final: LoginState | None = None

    async def start(self) -> LoginState:
        """Runs the login and returns once its page is open, or the login ended."""
        if self._process is not None or self._final is not None:
            return self.state()
        command = self._cli_command()
        if command is None:
            return self._end(LoginState("failed", detail="CLI Claude introuvable"))
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            self._workshop = jetable(WORKSHOP_PREFIX)
            target = self._workshop / "url"
            env = {**cli_environment(self._folder), "BROWSER": relay_command(target)}
            process = subprocess.Popen(  # noqa: S603 - argv built from the resolved CLI
                [*command, "auth", "login"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            return self._end(LoginState("failed", detail=type(exc).__name__))
        self._process = process
        if not await self._wait_for_page(process, target):
            return self._end(LoginState("failed", detail="aucune page de connexion rendue"))
        if self._url is not None:
            try:
                self._open_browser(self._url)
            except Exception:  # noqa: BLE001 — the screen still offers the page as a link
                pass
        return self.state()

    def _cli_command(self) -> list[str] | None:
        if self._command is not None:
            return self._command
        cli = cli_claude_resolu()[1]
        return None if cli is None else [str(cli)]

    async def _wait_for_page(self, process: subprocess.Popen[bytes], target: Path) -> bool:
        """Waits for the page's address, or the CLI's end; `False` once it took too long."""
        deadline = time.monotonic() + self._url_timeout_s
        while self._url is None and process.poll() is None:
            if time.monotonic() >= deadline:
                self._stop()
                return False
            self._url = _read_url(target)
            if self._url is None:
                await asyncio.sleep(_POLL_S)
        return True

    def state(self) -> LoginState:
        """`pending` while the CLI waits; its outcome once it has ended."""
        if self._final is not None:
            return self._final
        if self._process is None:
            return LoginState("pending")
        code = self._process.poll()
        if code is None:
            return LoginState("pending", url=self._url)
        if code != 0:
            return self._end(LoginState("failed", detail=exit_detail(code)))
        if self._check_login(self._folder) is False:
            return self._end(LoginState("failed", detail="auth status : non connecté"))
        return self._end(LoginState("connected"))

    def cancel(self) -> LoginState:
        """Ends a pending login. A login that already ended keeps its outcome."""
        if self._final is not None:
            return self._final
        if self._process is not None and self._process.poll() is not None:
            return self.state()
        self._stop()
        return self._end(LoginState("cancelled"))

    def _stop(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._process.kill()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - a kill the system delays
            pass

    def _end(self, outcome: LoginState) -> LoginState:
        self._final = outcome
        if self._workshop is not None:
            shutil.rmtree(self._workshop, ignore_errors=True)
            self._workshop = None
        return outcome


def exit_detail(code: int) -> str:
    """The exit code as the person reads it in the screen's statement.

    Windows hands over a process's code unsigned: a process ended from outside
    returns `0xFFFFFFFF`, a system failure an `NTSTATUS` such as `0xC0000142`.
    Those are written in hexadecimal, as Windows names them.
    """
    if code > 0x7FFFFFFF:
        return f"code de sortie 0x{code:08X}"
    return f"code de sortie {code}"


def _read_url(target: Path) -> str | None:
    """The page's address the relay left, once written whole."""
    try:
        url = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return url if url.startswith(("https://", "http://")) else None


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of `maestro-connexion`: the login, or with `--etat` its state."""
    parser = argparse.ArgumentParser(
        prog="maestro-connexion",
        description=(
            "Connecte Maestro à Claude sur ce poste : la connexion du CLI, dans le "
            "dossier que toutes les sessions de Maestro utilisent."
        ),
    )
    parser.add_argument(
        "--etat", action="store_true", help="dit seulement si Maestro est connecté"
    )
    arguments = parser.parse_args(argv)
    folder = dossier_du_cli_claude()
    if arguments.etat:
        state = logged_in(folder)
        if state is None:
            print(f"Maestro ne sait pas dire s'il est connecté (dossier du CLI : {folder}).")
            return 1
        print(
            f"Maestro est {'connecté' if state else 'non connecté'} à Claude "
            f"(dossier du CLI : {folder})."
        )
        return 0 if state else 3
    _, cli = cli_claude_resolu()
    if cli is None:
        print("Le CLI Claude est introuvable : réinstallez Maestro.", file=sys.stderr)
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Connexion de Maestro à Claude, dans {folder} :")
    return subprocess.call(  # noqa: S603 - argv built from the resolved CLI
        [str(cli), "auth", "login"], env=cli_environment(folder)
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
