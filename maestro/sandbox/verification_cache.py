"""The `LOCALAPPDATA` a tooling verification keeps from one pass to the next of a project (#1709).

At low integrity, a command Maestro plays to verify a tooling runs under the workbench of a
session (`maestro.sandbox.low_session`), and the workbench leaves with the verification. What
the command's tools store in `LOCALAPPDATA` (pip's and npm's caches, under Windows) would leave
with it, and every verification would download everything again. The verification therefore
receives, in place of the workbench's, a `LOCALAPPDATA` of its **project**, kept under the
machine's state (`maestro.emplacements.caches_de_verification`). `APPDATA`, where settings live
(`pip.ini`, `.npmrc`), stays with the session.

**Per project, never shared.** What one verification leaves in this folder, the next one reads
and runs: a wheel in pip's cache gets installed. Shared between projects, the folder would carry
what the verification of project A left into the verification of project B. Kept per project, it
opens no new channel: the agents of a project can already leave in its tree what the next
verification copies. The person chose this on 2026-11-17 (#1709), with two residual risks that
docs/17 §7 names: a low process of another project can write in this folder, since what is
labelled low is open to every low process; and what a command leaves there persists until the
retention, readable by every agent.

**What makes two verifications the same project** is the tree they verify, never a declaration.
The `Verificateur` receives a tree, and one project reaches it through several: the person's root
for an item written in place, and a worktree under the disposable root for a tooling written on a
branch, a new one at every generation. A tree that is the top of a repository or of a worktree
(it carries a `.git`) is therefore known by its **Git repository**, asked of Git
(`rev-parse --git-common-dir`) and never read from the pointer file. Any other tree is known by
its resolved root. Git is not asked about a root that lies inside someone else's repository:
that root is not the repository, and two such roots never share a cache. Two declared projects
that share one repository already share what it carries, labelled low
(`low_session.git_folders`).

**One verification at a time per cache.** What a tool writes in `LOCALAPPDATA` is unknown to
Maestro, and sorting it by tool would recognise a tool by its name (docs/44): two commands
writing it at once have no guarantee. A verification **holds** its project's cache for the whole
block, by a lock on a file the system releases when its holder dies, so a killed process leaves
nothing to collect. A verification that finds the cache held plays with the workbench's own,
empty, as a session did before: slower, never wrong. Waiting would eat delays that do not move
(`maestro.outillage.verification.Delais`). The same happens when the cache cannot be opened at
all (no home folder, a full disk).

**Retention.** Each opening removes the caches that no verification reopened for `RETENTION_S`,
and keeps no more than `KEPT_MAX` of them, the most recent. What a command leaves persists at most
that long after the last verification of its project, and the disk holds at most that many
caches. Both bounds are decisions, not measures. A cache another verification holds is never
removed, and a cache being removed cannot be held: the remover holds its lock until the lock file
is gone. `python -m maestro.menage` shows the folder.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from maestro.emplacements import caches_de_verification
from maestro.fichiers import retirer_arbre

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: The `LOCALAPPDATA` itself, inside a project's cache. The session that receives it labels
#: it low (`low_session.prepare`): the rest of the project's folder stays Maestro's.
CACHE_FOLDER = "local"

#: The tree a cache serves, in a file of Maestro's next to it: what `maestro.menage` and a
#: person read to know whose cache it is. Its date is the last verification.
PROJECT_FILE = "project.txt"

#: The file whose lock a verification holds for the whole block.
LOCK_FILE = "lock"

#: How long a cache that no verification reopened is kept: two weeks. A decision, not a
#: measure: a tooling reverified within two weeks finds its caches, and what a command left
#: lives at most that long after the last verification.
RETENTION_S = 14 * 24 * 3600

#: How many caches the machine keeps at most, the most recently used. A decision, not a
#: measure: each one holds a project's downloaded packages, a gigabyte or more for a web
#: application.
KEPT_MAX = 8

#: How long Git may take to name a tree's repository.
_GIT_TIMEOUT_S = 15.0

#: The length of a project's key, in hexadecimal digits.
_KEY_DIGITS = 16


def project_identity(root: Path) -> Path:
    """What makes two verifications the same project: its Git repository, or its root.

    The repository only for a tree that carries a `.git` (the top of a repository, or a
    worktree) and when Git names it. Otherwise the resolved root.
    """
    resolved = Path(root).resolve()
    if (resolved / ".git").exists():
        common = _git_common_dir(resolved)
        if common is not None:
            return common
    return resolved


def project_key(root: Path) -> str:
    """The name of `root`'s cache: a fingerprint of its identity, in the system's case."""
    return _key_of(project_identity(root))


def _key_of(identity: Path) -> str:
    text = os.path.normcase(str(identity))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_KEY_DIGITS]


@contextmanager
def held(
    root: Path, *, caches: Path | None = None, now: float | None = None
) -> Iterator[Path | None]:
    """The `LOCALAPPDATA` of `root`'s project, held for the block — `None` if it cannot be.

    Created at the project's first verification. `None` when another verification holds
    it, or when it cannot be opened: the caller then plays with a workbench of its own.
    Each opening applies the retention to the other caches (`retain`). `caches` defaults to
    the machine's (`caches_de_verification`), read at the call.
    """
    base = caches_de_verification() if caches is None else caches
    lock: int | None = None
    local: Path | None = None
    try:
        identity = project_identity(root)
        folder = base / _key_of(identity)
        lock = _hold(folder, create=True)
        if lock is not None:
            (folder / PROJECT_FILE).write_text(f"{identity}\n", encoding="utf-8")
            (folder / CACHE_FOLDER).mkdir(exist_ok=True)
            local = folder / CACHE_FOLDER
            retain(base, keep=folder.name, now=now)
    except OSError:
        local = None
    try:
        yield local
    finally:
        if lock is not None:
            _release(lock)


def retain(caches: Path, *, keep: str = "", now: float | None = None) -> tuple[Path, ...]:
    """Removes the caches past the retention, and beyond `KEPT_MAX` — never one that is held.

    `keep` names the cache being opened: it is neither judged nor removed, and counts among
    the `KEPT_MAX`. Returns what was removed. Never raises: a cache that resists stays, and
    the next opening tries again.
    """
    moment = time.time() if now is None else now
    try:
        folders = [entry for entry in caches.iterdir() if entry.is_dir() and entry.name != keep]
    except OSError:
        return ()
    dated = sorted(((_last_use(folder), folder) for folder in folders), reverse=True)
    room = KEPT_MAX - (1 if keep else 0)
    removed: list[Path] = []
    for rank, (last, folder) in enumerate(dated):
        if rank < room and moment - last <= RETENTION_S:
            continue
        if _remove(folder):
            removed.append(folder)
    return tuple(removed)


def _last_use(folder: Path) -> float:
    """When a verification last opened `folder` — the oldest date when nothing says."""
    for witness in (folder / PROJECT_FILE, folder):
        try:
            return witness.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _remove(folder: Path) -> bool:
    """Removes `folder` if no verification holds it; its lock is held until the lock file goes.

    Under POSIX the lock file is unlinked while held. Windows refuses to remove an open
    file: it is removed once released, and stays if a verification opened it in between, which
    then holds a cache that is empty and whole.
    """
    lock = _hold(folder, create=False)
    if lock is None:
        return False
    path = folder / LOCK_FILE
    try:
        for entry in folder.iterdir():
            if entry.name == LOCK_FILE:
                continue
            if entry.is_dir() and not entry.is_symlink():
                retirer_arbre(entry)
            else:
                entry.unlink()
        try:
            os.unlink(path)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        _release(lock)
    try:
        if path.exists():
            os.unlink(path)
        os.rmdir(folder)
    except OSError:
        return False
    return True


def _hold(folder: Path, *, create: bool) -> int | None:
    """A descriptor on `folder`'s lock, locked — `None` when another holds it.

    `create` makes the folder when missing; without it, a missing folder is `None`. The lock
    file is made when missing either way: a folder whose removal was cut short is still
    removable. The lock is checked to still be the file at its path: a lock file removed
    between the opening and the locking is a lock on nothing, and it is tried once more.
    Raises `OSError` when the folder cannot be created.
    """
    path = folder / LOCK_FILE
    for _ in range(2):
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except FileNotFoundError:
            return None
        if not _lock(descriptor):
            os.close(descriptor)
            return None
        try:
            if os.path.samestat(os.fstat(descriptor), os.stat(path)):
                return descriptor
        except OSError:
            pass
        _release(descriptor)
    return None


def _lock(descriptor: int) -> bool:
    """Locks `descriptor` without waiting: `False` when another description holds it."""
    try:
        if sys.platform == "win32":
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(descriptor: int) -> None:
    """Unlocks and closes `descriptor` — closing alone releases it under POSIX."""
    try:
        if sys.platform == "win32":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _git_common_dir(tree: Path) -> Path | None:
    """The repository Git writes for `tree`, resolved — `None` when Git does not answer."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=tree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return None
    return Path(text).resolve()


__all__ = [
    "CACHE_FOLDER",
    "KEPT_MAX",
    "LOCK_FILE",
    "PROJECT_FILE",
    "RETENTION_S",
    "held",
    "project_identity",
    "project_key",
    "retain",
]
