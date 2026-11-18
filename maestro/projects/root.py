"""Validation of a project's root and detection of its VCS (ticket #221, EF-38).

The only place in the code that confronts a project with the disk **before** an
agent touches it. Two responsibilities, both required by
[docs/24 §2.5](../../docs/24-projets-locaux-et-poste-de-travail.md) ("escape through
the declared root"):

1. **Validate a root** (`validate_root`) — the path is **canonicalized**
   (`resolve()`: `..` are collapsed and symbolic links followed, so a link to
   `~/.ssh` is seen as `~/.ssh`), then checked against a **list of forbidden
   roots**: drive root, bare home folder and its ancestors, `.ssh`/`AppData` and
   their cousins, system folders, and the Maestro repository itself. A refusal
   **carries its reason** (`RootRejected.reason`), never a silent `False`: the
   Projects screen must be able to say *why*
   ([docs/05 §2.7](../../docs/05-interface-control-tower.md)). Its message
   writes its paths in POSIX (`_readable`), like the screen that shows it.
   The part of these refusals that holds for **any** path — not only for a
   project root — is isolated in `check_forbidden_zone`: the API's folder
   explorer reuses it (#223), so that a zone forbidden at declaration does not
   become readable some other way.
2. **Prevent writing above it** (`path_in_root`) — the second half of EF-38.
   Every path derived from a root goes through it: it is resolved, then checked
   to be **under** the canonicalized root, which neutralizes a
   `../../etc/passwd` as well as a symbolic link placed in the project.

And a convenience that belongs to the same folder: `detect_vcs`, which answers
"is this folder a Git repository?" **without imposing anything** — an unversioned
project remains declarable, it simply gets a copy rather than a worktree per task
(#224, D2). Detection reads Git's files (`.git/HEAD`, `.git/config`) rather than
calling the binary: no subprocess, no dependency on an installed `git`, and a
detection testable on a fake repository of three files.

Nothing here is OS-specific: the Windows forbidden paths are inert under POSIX and
vice versa, which avoids one list per platform.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from maestro.projects.model import Vcs

#: An admissible path anchor: a drive (`C:\\`) under Windows, the root under
#: POSIX. Everything else — network share (`\\\\server\\share`, `//srv`), device
#: (`\\\\.\\C:`) — is refused: these anchors **do not compare** with those of the
#: forbidden roots, so letting them through would disable the list (a
#: `\\\\localhost\\C$\\Users\\me\\.ssh` does designate `~/.ssh`).
_ALLOWED_ANCHOR = re.compile(r"^[A-Za-z]:[\\/]$" if os.name == "nt" else r"^/$")

#: The folders of the user profile that a project cannot take as its root: SSH/GPG
#: keys, cloud credentials, and the large application-state folders (`AppData`
#: under Windows, `Library` under macOS) — declaring one of them would open every
#: secret of the workstation to an agent (docs/24 §2.5).
_FORBIDDEN_UNDER_HOME: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    "AppData",
    "Library",
)

#: The system folders: never a project, always a disaster. The list mixes Windows
#: and POSIX on purpose — a path that does not exist on the current OS costs only a
#: comparison.
_FORBIDDEN_SYSTEM: tuple[str, ...] = (
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/var",
    "/System",
    "/Library",
)


class RootRejected(ValueError):
    """A rejected root (or derived path), **with its reason** (EF-38).

    `reason` is a short, stable code (`racine-de-disque`, `chemin-sensible`,
    `hors-racine`…): it is what the API and the UI show or translate, the message
    remaining the readable sentence. Inherits from `ValueError` so that it stays
    catchable like the refusals of the other stores (`maestro.agents.store`).
    """

    def __init__(self, reason: str, message: str, path: Path | str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.path = str(path)


def validate_root(path: Path | str, *, create: bool = False) -> Path:  # noqa: C901
    """The canonicalized root of `path`, or a reasoned `RootRejected` (EF-38).

    Requires an **absolute** path: a relative path would be resolved against the
    process's current directory — that is, somewhere else depending on who calls,
    which a security boundary cannot afford. The return value is the **resolved**
    path: it is what must be stored, never the input.

    `create=True` creates the folder if it is missing (the "new folder" need of
    docs/24 §2.1) — after the refusals, never before: a folder is not created only
    to be refused afterwards.
    """
    raw = str(path).strip()
    if not raw:
        raise RootRejected("chemin-vide", "Racine vide : indiquez un dossier de projet.")
    candidate = Path(_without_windows_prefix(raw)).expanduser()
    if not candidate.is_absolute():
        raise RootRejected(
            "chemin-relatif",
            f"Racine relative refusée : {raw!r} — indiquez un chemin absolu.",
            raw,
        )
    resolved = candidate.resolve()
    if not _ALLOWED_ANCHOR.match(resolved.anchor):
        raise RootRejected(
            "ancre-non-standard",
            f"Racine refusée : {_readable(resolved)} n'est pas sur un disque local "
            "(partage réseau ou chemin de périphérique).",
            resolved,
        )

    if resolved == Path(resolved.anchor):
        raise RootRejected(
            "racine-de-disque",
            f"Racine de disque refusée : {_readable(resolved)} — choisissez un dossier de projet.",
            resolved,
        )

    home = _home_dir()
    if home is not None:
        if resolved == home:
            raise RootRejected(
                "dossier-utilisateur-nu",
                f"Dossier utilisateur refusé tel quel : {_readable(resolved)} — choisissez un "
                "sous-dossier de projet.",
                resolved,
            )
        if _contains(resolved, home):
            raise RootRejected(
                "au-dessus-du-dossier-utilisateur",
                f"Racine trop haute : {_readable(resolved)} contient le dossier utilisateur "
                f"{_readable(home)}.",
                resolved,
            )

    check_forbidden_zone(resolved)

    repository = _maestro_repository()
    if _contains(resolved, repository):
        raise RootRejected(
            "au-dessus-du-depot-maestro",
            f"Racine trop haute : {_readable(resolved)} contient le dépôt de Maestro "
            f"({_readable(repository)}).",
            resolved,
        )

    if resolved.exists() and not resolved.is_dir():
        raise RootRejected(
            "pas-un-dossier",
            f"Racine invalide : {_readable(resolved)} n'est pas un dossier.",
            resolved,
        )
    if not resolved.exists():
        if not create:
            raise RootRejected(
                "dossier-absent",
                f"Racine introuvable : {_readable(resolved)} n'existe pas.",
                resolved,
            )
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def check_forbidden_zone(path: Path) -> None:
    """Refuse an **already resolved** path lying in a sensitive zone of the workstation.

    The forbidden zones that hold for **any** path (EF-38), whether it is declared
    as a project root (`validate_root`) or listed from the folder explorer
    (`maestro.controltower.projets`, #223): the secrets of the user profile
    (`.ssh`, `.aws`, `AppData`…), the system folders, and the Maestro repository
    itself.

    What **remains** specific to a project root lives in `validate_root` and has
    no place here: one legitimately **goes through** one's home folder, or the
    folder that contains the Maestro repository, to reach a project; one simply
    cannot declare them as they are. Hence the split: this function says "this
    path is forbidden", `validate_root` adds "and this root is not declarable".

    Expects a **resolved** path (`canonical`): it compares, it does not
    canonicalize — a caller handing it raw input would get around the comparison
    with a `..`.
    """
    home = _home_dir()
    if home is not None:
        for name in _FORBIDDEN_UNDER_HOME:
            if _is_under(path, home / name):
                raise RootRejected(
                    "chemin-sensible",
                    f"Chemin sensible refusé : {_readable(path)} est dans "
                    f"{_readable(home / name)}.",
                    path,
                )

    for forbidden in _forbidden_system_dirs():
        if _is_under(path, Path(forbidden)):
            raise RootRejected(
                "chemin-systeme",
                f"Dossier système refusé : {_readable(path)} est dans {_readable(forbidden)}.",
                path,
            )

    repository = _maestro_repository()
    if _is_under(path, repository):
        raise RootRejected(
            "depot-maestro",
            f"Le dépôt de Maestro ne se prend pas lui-même pour projet : {_readable(path)}.",
            path,
        )


def path_in_root(root: Path | str, path: Path | str) -> Path:
    """The path `path` resolved **under** `root`, or `RootRejected` (EF-38).

    Accepts a relative path (joined to the root) as well as an absolute one
    (checked as is), and refuses anything that leaves the root — `..`, a path on
    another drive, or a symbolic link pointing outside, resolution taking place
    **before** the comparison. It is the building block of the derived workspace
    (#224) and of the apply of deliverables (#227): no write is computed any other
    way.

    ⚠ The verdict holds for the state of the disk **at the time of the call**: a
    link placed just afterwards would redirect the returned path (TOCTOU). Closing
    that window falls to whoever **opens** the file (without following links), not
    to whoever computes the path.
    """
    base = canonical(root)
    candidate = Path(_without_windows_prefix(str(path).strip())).expanduser()
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if not _is_under(target, base):
        raise RootRejected(
            "hors-racine",
            f"Chemin hors du projet : {_readable(target)} sort de la racine {_readable(base)}.",
            target,
        )
    return target


def detect_vcs(root: Path | str) -> Vcs | None:
    """The VCS of the folder `root` — `Vcs(type='git', …)`, or `None` if unversioned.

    Detection is **observed, never imposed**: a folder without `.git` returns
    `None` and remains a perfectly declarable project (it works by copy instead of
    a worktree, #224). A `.git` **file** (the case of a Git worktree) is followed
    to its real folder. Branch and remote are read on a best-effort basis: a
    freshly `git init`-ed repository, without any commit or remote, does return a
    `Vcs` — it is a Git repository, with `remote=None`.
    """
    marker = Path(root).expanduser() / ".git"
    git_dir = _git_dir(marker)
    if git_dir is None:
        return None
    return Vcs(
        type="git",
        base_branch=_current_branch(git_dir),
        remote=_origin_url(_git_config(git_dir)),
    )


def canonical(path: Path | str) -> Path:
    """The canonical form of a path — the one on which two roots are compared.

    Removes the Windows extended-length prefix (`\\\\?\\`) **before** resolving:
    without it, the same folder has two spellings that do not compare, and one of
    them escapes every check of `validate_root`. It is also what guarantees that a
    project is not declared twice under two spellings of the same path.
    """
    return Path(_without_windows_prefix(str(path).strip())).expanduser().resolve()


def _readable(path: Path | str) -> str:
    """`path` as a refusal message writes it: in POSIX, like the screen (#1614).

    The explorer, the confirmation and the list of allowed folders write their
    paths with `/`; a Windows `Path` formatted as is would be written with `\\`,
    and a single sentence would then write the same folder in two ways.
    """
    return Path(path).as_posix()


def _without_windows_prefix(raw: str) -> str:
    """`raw` without its `\\\\?\\` (or `\\\\?\\UNC\\`) prefix, unchanged otherwise."""
    normalized = raw.replace("/", "\\")
    if normalized.upper().startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw[8:]
    if normalized.startswith("\\\\?\\"):
        return raw[4:]
    return raw


def _forbidden_system_dirs() -> tuple[str, ...]:
    """The system folders, those of the Windows environment included.

    `%SystemRoot%`/`%ProgramFiles%` are read at call time: a Windows installed on
    `D:` is not covered by the hard-coded list, which assumes `C:`.
    """
    from_env = tuple(
        value
        for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        if (value := os.environ.get(key, "").strip())
    )
    return _FORBIDDEN_SYSTEM + from_env


def _is_under(path: Path, ancestor: Path) -> bool:
    """Is `path` `ancestor` or one of its descendants? (the OS's comparison)."""
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True


def _contains(path: Path, descendant: Path) -> bool:
    """Is `path` a **strict** ancestor of `descendant`?"""
    return path != descendant and _is_under(descendant, path)


def _home_dir() -> Path | None:
    """The current user's home folder, resolved — `None` if the OS does not say."""
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError):  # pragma: no cover - depends on the environment
        return None


def _maestro_repository() -> Path:
    """The root of the Maestro repository (from `maestro/projects/root.py`)."""
    return Path(__file__).resolve().parents[2]


def _git_dir(marker: Path) -> Path | None:
    """The effective `.git` folder behind `marker`, or None if it is not a repository.

    Requires a `HEAD`: an empty or leftover `.git` (aborted clone) is **not** a
    repository, and declaring it one would send #224 to attempt a Git worktree
    where a copy is needed. It is also what bounds the `gitdir:` of a worktree,
    which legitimately points **outside** the project root and therefore cannot be
    constrained to stay there.
    """
    if marker.is_dir():
        return marker if (marker / "HEAD").is_file() else None
    if not marker.is_file():
        return None
    # `.git` file: the case of a Git worktree — "gitdir: <path>".
    text = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return None
    target = Path(text[len("gitdir:") :].strip())
    if not target.is_absolute():
        target = marker.parent / target
    return target.resolve() if (target / "HEAD").is_file() else None


def _git_config(git_dir: Path) -> Path:
    """The repository's `config` — the **common** repository's if `git_dir` is a worktree."""
    common = git_dir / "commondir"
    if common.is_file():
        target = Path(common.read_text(encoding="utf-8", errors="replace").strip())
        if not target.is_absolute():
            target = git_dir / target
        return target.resolve() / "config"
    return git_dir / "config"


def _current_branch(git_dir: Path) -> str:
    """The branch `HEAD` points to, or "" (detached HEAD, or unreadable file)."""
    head = git_dir / "HEAD"
    if not head.is_file():
        return ""
    text = head.read_text(encoding="utf-8", errors="replace").strip()
    prefix = "ref: refs/heads/"
    return text[len(prefix) :].strip() if text.startswith(prefix) else ""


def _origin_url(config: Path) -> str | None:
    """The URL of the `origin` remote in a Git `config`, or None if there is none.

    A deliberate mini-reader: the `git config` format is not quite INI (sections
    with a sub-name `[remote "origin"]`, tab-indented values that `configparser`
    would take for line continuations).
    """
    if not config.is_file():
        return None
    in_origin = False
    for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
        bare = line.strip()
        if bare.startswith("["):
            # Git tolerates a comment after the section header.
            header = bare[: bare.index("]") + 1] if "]" in bare else bare
            in_origin = header.replace(" ", "") == '[remote"origin"]'
            continue
        key, separator, value = bare.partition("=")
        if in_origin and separator and key.strip().lower() == "url":
            if clean := _git_value(value):
                return clean
    return None


def _git_value(raw: str) -> str:
    """The value of a `key = value` line of `git config`: comment removed, unquoted."""
    value = raw.strip()
    for mark in (" #", " ;", "\t#", "\t;"):
        if mark in value:
            value = value[: value.index(mark)].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value.strip()
