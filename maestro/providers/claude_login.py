"""Connecting Maestro to Claude, once per machine: `maestro-connexion` (#1515).

Every Claude session of Maestro — agents, project lead, assistant, probe — runs the
CLI in a folder that belongs to Maestro (`maestro.emplacements.dossier_du_cli_claude`,
passed as `CLAUDE_CONFIG_DIR`). An agent at low integrity cannot write the person's
`~/.claude`, and resuming a session reads what the CLI wrote; so the CLI keeps its
login, its refreshed token and its transcripts there. That folder does not see the
login of the person's own CLI: Maestro is connected once, by this command, which
runs the CLI's own login in that folder. A `CLAUDE_CODE_OAUTH_TOKEN` or an API key
in the settings needs no login at all.

Only the CLI's public commands are run (`auth login`, `auth status`); nothing the
CLI writes in its folder is ever read here.

    maestro-connexion           # opens the CLI's login, in Maestro's folder
    maestro-connexion --etat    # says whether Maestro is connected
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from maestro.emplacements import dossier_du_cli_claude
from maestro.installation import cli_claude_resolu

#: The command's name, as `pyproject.toml` declares it.
COMMAND = "maestro-connexion"

#: How long `auth status` may take to answer.
_STATUS_TIMEOUT_S = 30.0


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
