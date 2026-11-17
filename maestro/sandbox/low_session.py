"""What an agent's session receives when it runs at low integrity (#1515).

`maestro.sandbox.low_integrity` lowers a process and labels a folder. This module
decides, for one session, what is labelled and what the agent's environment says.

**Written by the agent, so labelled low** (inherited by everything beneath):

- the workspace;
- the Git directory that a versioned project's worktree writes into — objects,
  refs, the worktree's own metadata. A worktree's `.git` is a pointer to it;
- the CLI's folder (`maestro.emplacements.dossier_du_cli_claude`), where the CLI
  keeps its login, its refreshed token and its transcripts, which the resumption
  of a session reads (#1513). The person's `~/.claude` is not touched: at low
  integrity the CLI could not write there;
- the **workbench**, a folder of its own per session under the disposable root.
  `TEMP`, `TMP` and `TMPDIR` lead to its `tmp`, so a `mktemp -d` gives a folder the
  scope recognises (`maestro.portee.etabli_de`); `APPDATA` and `LOCALAPPDATA` lead
  to its `appdata`, so what a tool writes in the profile — a lock, a cache — lands
  there. It leaves with the session (`LowSession.close`);
- the `LOCALAPPDATA` a caller keeps from one session to the next, when it hands one
  (`local_appdata`): the tooling verification keeps one per project, so that what its
  tools cache survives (`maestro.sandbox.verification_cache`, #1709). It does not leave
  with the session. `APPDATA`, where settings live, stays in the workbench.

**Git for Windows, copied** once per installed version under the machine's tools
(`maestro.emplacements.outils_du_poste`), and put first on the `PATH`. Its runtime
dies at low integrity under its own path: its named-object directory already
exists at the person's level, and a low process may not open it. Under another
path it creates its own. The whole installation is copied, `mingw64` included, so
that the `git` the agent runs, and the `sh` its hooks run, are the copy's. The copy
carries no label: the agent cannot change its own tools.

**The agent's runtimes keep their named objects apart** (#1708). A path written in
full in a setting (the `script-shell` of npm, an editor, an ssh command) bypasses the
`PATH` and leads to the original installation. The environment therefore carries the
variable the Cygwin runtime reads (`RUNTIME_APART`), so that every runtime the agent
starts, whatever path led to it, opens a directory of named objects of its own and never
the person's. No tool and no setting is recognised: what is acted on is the runtime
that would die.

**Closed to the agent's reading** (`refuse_read_up`): what belongs to Maestro and
would let an agent act as Maestro — the API token, the secrets vault, the `.env`
files Maestro loads. Their values are in Maestro's environment too, so the names
those files declare are withheld from the CLI (`maestro_variables`). The person's
profile is not touched (#1515): what she reads, the agent reads.

What the isolation does not cover is said to the agent (`maestro.machine`) and in
docs/17 §7.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from maestro.emplacements import jetable, outils_du_poste
from maestro.fichiers import retirer_arbre
from maestro.sandbox import low_integrity

#: The prefix of a session's workbench under the disposable root.
WORKBENCH_PREFIX = "maestro-etabli-"

#: The prefix of a copy of Git under the machine's tools, followed by its fingerprint.
GIT_COPY_PREFIX = "git-"

#: What a Git for Windows installation carries, relative to its root: the shell the
#: CLI runs, and the runtime whose path names its object directory.
_BASH = Path("bin") / "bash.exe"
_RUNTIME = Path("usr") / "bin" / "msys-2.0.dll"
_GIT = Path("mingw64") / "bin" / "git.exe"

#: The variable the Cygwin runtime (MSYS2's, hence Git for Windows') reads to keep the
#: named objects of a process apart from its installation's, under a directory suffixed
#: by the runtime's build date (`get_shared_parent_dir` in its `shared.cc`). A low process
#: may not create objects in the directory the person's processes opened: with it, a
#: runtime the agent reaches by any path lives in a directory of its own (#1708).
RUNTIME_APART = "CYGWIN_TESTING"

#: How long Git may take to name a workspace's Git directory.
_GIT_TIMEOUT_S = 15.0

#: Maestro's namespace, withheld from a lowered process: the API token, the vault key,
#: the settings. What remains is the person's environment.
MAESTRO_PREFIX = "MAESTRO_"


@dataclass
class LowSession:
    """The workbench of one session and the variables that lead the agent into it."""

    workbench: Path
    env: dict[str, str] = field(default_factory=dict)
    #: The shell of the Git copy, `None` when the machine has no Git for Windows.
    bash: Path | None = None
    #: The variables the launcher withholds from the CLI, on top of `MAESTRO_*`.
    withheld: frozenset[str] = frozenset()

    def close(self) -> None:
        """Removes the workbench — what the agent left there leaves with the session."""
        retirer_arbre(self.workbench)


def withhold(environ: Mapping[str, str], withheld: Iterable[str] = ()) -> dict[str, str]:
    """`environ` without Maestro's namespace and without the `withheld` names.

    Case is ignored, as Windows ignores it. This is what a lowered process inherits
    of the environment it starts from: closing Maestro's files to its reading would
    close nothing if their values reached it through the environment.
    """
    names = {name.upper() for name in withheld}
    return {
        name: value
        for name, value in environ.items()
        if not name.upper().startswith(MAESTRO_PREFIX) and name.upper() not in names
    }


def prepare(
    workspace: Path,
    *,
    cli_folder: Path | None = None,
    local_appdata: Path | None = None,
    closed: Iterable[Path] = (),
    withheld: Iterable[str] = (),
    environ: Mapping[str, str] | None = None,
) -> LowSession:
    """Labels what the agent writes, closes what it must not read, opens its workbench.

    `cli_folder` is the CLI's own folder, created when missing. `local_appdata` is the
    `LOCALAPPDATA` the caller keeps beyond the session, created when missing and labelled
    low like `cli_folder`; without it, `LOCALAPPDATA` leads into the workbench and leaves
    with it. `closed` names what Maestro owns and the agent must not read; what does not
    exist is skipped. `withheld` names the variables the launcher keeps from the CLI.
    `environ` is the environment the session starts from (default: this process's),
    whose `PATH` the Git copy is put in front of. Raises `LowIntegrityError` when
    the system refuses a label: a session that would run lowered without the
    folders it writes would fail on its first write, and is refused here instead.
    """
    base = os.environ if environ is None else environ
    folders = [workspace, *git_folders(workspace)]
    for kept in (cli_folder, local_appdata):
        if kept is not None:
            kept.mkdir(parents=True, exist_ok=True)
            folders.append(kept)
    for folder in folders:
        low_integrity.label_low(folder)
    for path in closed:
        if path.exists():
            low_integrity.refuse_read_up(path)
    workbench = jetable(WORKBENCH_PREFIX)
    try:
        temporary = workbench / "tmp"
        roaming = workbench / "appdata" / "roaming"
        local = workbench / "appdata" / "local"
        for folder in (temporary, roaming, local):
            folder.mkdir(parents=True)
        low_integrity.label_low(workbench)
        env = {
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "APPDATA": str(roaming),
            "LOCALAPPDATA": str(local if local_appdata is None else local_appdata),
            RUNTIME_APART: "1",
        }
        copy = git_copy()
        bash: Path | None = None
        if copy is not None:
            inherited = base.get("PATH", "")
            env["PATH"] = str(copy / "cmd") + (os.pathsep + inherited if inherited else "")
            bash = copy / _BASH
        return LowSession(workbench, env, bash, frozenset(withheld))
    except BaseException:
        retirer_arbre(workbench)
        raise


def git_folders(workspace: Path) -> tuple[Path, ...]:
    """The Git directory a worktree writes into, when it lies outside `workspace`.

    Asked of Git itself (`rev-parse --git-common-dir`), never read from the pointer
    file. Nothing when `workspace` is not under Git, or when Git does not answer:
    the session then labels only what it knows.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return ()
    common = Path(text)
    try:
        common.resolve().relative_to(workspace.resolve())
    except ValueError:
        return (common,)
    return ()


def git_installation() -> Path | None:
    """The root of the Git for Windows the `PATH` leads to — `None` without one.

    `git.exe` lives under `cmd`, `bin` or `mingw64/bin` of its installation; the
    root is the first parent that carries the shell and the runtime.
    """
    found = shutil.which("git")
    if found is None:
        return None
    for parent in Path(found).resolve().parents:
        if (parent / _BASH).is_file() and (parent / _RUNTIME).is_file():
            return parent
    return None


def git_copy(installation: Path | None = None, *, tools: Path | None = None) -> Path | None:
    """The copy of Git for Windows an agent at low integrity runs — made once per version.

    The copy is named after a fingerprint of the installation (its path, and the
    size and date of its runtime and of `git.exe`): a Git updated by the person
    gets a new copy, and the copies of earlier versions are removed when no
    process holds them. It is made under a temporary name and renamed into place,
    so a session never sees half a copy, and two sessions copying at once keep
    the first one. `None` when the machine has no Git for Windows.
    """
    root = installation if installation is not None else git_installation()
    if root is None:
        return None
    parent = outils_du_poste() if tools is None else tools
    target = parent / f"{GIT_COPY_PREFIX}{_fingerprint(root)}"
    if (target / _BASH).is_file():
        return target
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{target.name}.{os.getpid()}"
    retirer_arbre(staging)
    try:
        shutil.copytree(root, staging, symlinks=True)
        try:
            staging.rename(target)
        except OSError:
            if not (target / _BASH).is_file():
                raise
    finally:
        retirer_arbre(staging)
    _remove_earlier_copies(parent, keep=target)
    return target


def _fingerprint(root: Path) -> str:
    """Twelve hexadecimal digits that change when the installation changes."""
    parts = [str(root).casefold()]
    for relative in (_RUNTIME, _GIT):
        try:
            status = (root / relative).stat()
        except OSError:
            parts.append(f"{relative}:absent")
            continue
        parts.append(f"{relative}:{status.st_size}:{status.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def _remove_earlier_copies(parent: Path, *, keep: Path) -> None:
    """Removes the other copies — renamed first, so one that a process holds stays whole.

    Windows refuses to rename a folder while a file beneath it is open: a copy an
    agent still runs keeps its name and its content, and goes at a later session.
    """
    for entry in parent.glob(f"{GIT_COPY_PREFIX}*"):
        if entry == keep or not entry.is_dir():
            continue
        leaving = entry.with_name(f".{entry.name}.leaving.{os.getpid()}")
        try:
            entry.rename(leaving)
        except OSError:
            continue
        retirer_arbre(leaving)


def maestro_env_files() -> tuple[Path, ...]:
    """The `.env` files Maestro loads: the product's, and the machine's when installed.

    Found by the functions `maestro.config.charger_les_env` uses. An installation
    setting that does not read leaves the machine's out.
    """
    from maestro.emplacements import NOM_ENV, env_du_poste, racine_du_code

    found = [racine_du_code() / NOM_ENV]
    try:
        poste = env_du_poste()
    except Exception:  # noqa: BLE001 - an unreadable installation setting closes less
        poste = None
    if poste is not None:
        found.append(poste)
    return tuple(found)


def maestro_secrets() -> tuple[Path, ...]:
    """What Maestro owns and an agent must not read: API token, secrets vault, `.env` files.

    Read from the same functions that Maestro uses to find them, never listed
    twice. What cannot be located (no home folder, a setting that does not read)
    is left out: closing less is said by docs/17 §7, never a failure of the session.
    """
    from maestro.agents.secrets import SecretStore
    from maestro.config import load_settings
    from maestro.controltower.acces import chemin_du_jeton

    found: list[Path] = []
    try:
        settings = load_settings()
        found.append(chemin_du_jeton(settings))
        found.append(SecretStore.default(settings).racine)
    except Exception:  # noqa: BLE001 - closing less never stops a session
        pass
    return (*found, *maestro_env_files())


def maestro_variables(files: Iterable[Path] | None = None) -> frozenset[str]:
    """The names that Maestro's `.env` files declare (default: `maestro_env_files`).

    Maestro loads those files into its own environment, so an agent would inherit
    their values (a forge token, a Slack token, the Langfuse key) even though it
    cannot read the files. A file that does not exist declares nothing.
    """
    from dotenv import dotenv_values

    names: set[str] = set()
    for path in maestro_env_files() if files is None else files:
        if path.is_file():
            names.update(dotenv_values(path))
    return frozenset(names)


__all__ = [
    "GIT_COPY_PREFIX",
    "MAESTRO_PREFIX",
    "RUNTIME_APART",
    "WORKBENCH_PREFIX",
    "LowSession",
    "git_copy",
    "git_folders",
    "git_installation",
    "maestro_env_files",
    "maestro_secrets",
    "maestro_variables",
    "prepare",
    "withhold",
]
