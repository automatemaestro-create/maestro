"""The `LOCALAPPDATA` a tooling verification keeps per project (#1709).

`maestro.sandbox.verification_cache` carries the reasons. What this file holds:

- **the same project** is recognised through every tree it reaches the verification by, its
  root as its worktrees, and **another project never** is, even one whose root lies inside
  someone else's repository;
- a cache is **kept** from one verification to the next, and **held by one** at a time, in
  this process or another, and a killed holder releases it;
- the **retention** removes what no verification reopened, keeps at most `KEPT_MAX`, never
  removes a cache another verification holds, and finishes a removal that was cut short;
- a cache that **cannot be opened** leaves the verification to its workbench.

None of it needs the low integrity level: the module only names, locks and removes folders.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from maestro.sandbox import verification_cache
from maestro.sandbox.verification_cache import (
    CACHE_FOLDER,
    KEPT_MAX,
    LOCK_FILE,
    PROJECT_FILE,
    RETENTION_S,
    held,
    project_identity,
    project_key,
    retain,
)

REPOSITORY = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")

needs_git = pytest.mark.skipif(GIT is None, reason="git is needed")


def _git(root: Path, *arguments: str) -> None:
    command = [GIT or "git", "-c", "user.email=t@m", "-c", "user.name=T", *arguments]
    subprocess.run(command, cwd=root, check=True, capture_output=True)


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "base")
    return root


def _age(folder: Path, seconds: float) -> None:
    """Makes the last verification of `folder` `seconds` old."""
    moment = time.time() - seconds
    os.utime(folder / PROJECT_FILE, (moment, moment))


def _cache(caches: Path, root: Path, *, age_s: float = 0.0) -> Path:
    """Opens `root`'s cache once, leaves a witness in it, and ages it by `age_s`."""
    with held(root, caches=caches) as local:
        assert local is not None
        (local / "witness").write_text("kept", encoding="utf-8")
    folder = caches / project_key(root)
    _age(folder, age_s)
    return folder


# --- The same project, and no other ---------------------------------------------------------


@needs_git
def test_a_project_reaches_one_cache_through_its_root_and_through_its_worktrees(
    tmp_path: Path,
) -> None:
    """The generation writes in a new worktree each time: it must find the cache of the root."""
    root = _repository(tmp_path / "depensio")
    worktree = tmp_path / "worktree-1"
    _git(root, "worktree", "add", "--quiet", "-b", "maestro/t1", str(worktree))

    assert project_key(worktree) == project_key(root)
    assert project_identity(worktree) == (root / ".git").resolve()
    assert project_key(_repository(tmp_path / "other")) != project_key(root)


@needs_git
def test_a_root_inside_someone_else_s_repository_is_never_that_repository(
    tmp_path: Path,
) -> None:
    """Two projects in place under one repository: asking Git would give them one cache."""
    enclosing = _repository(tmp_path / "documents")
    first, second = enclosing / "devis", enclosing / "factures"
    first.mkdir()
    second.mkdir()

    keys = {project_key(enclosing), project_key(first), project_key(second)}

    assert len(keys) == 3
    assert project_identity(first) == first.resolve()


def test_a_project_is_named_alike_whatever_spelling_reaches_it(tmp_path: Path) -> None:
    root = tmp_path / "depensio"
    root.mkdir()
    assert project_key(root / "." / "sub" / "..") == project_key(root)


# --- Kept, and held by one --------------------------------------------------------------------


def test_what_a_verification_leaves_is_found_by_the_next_one_of_its_project(
    tmp_path: Path,
) -> None:
    caches, root = tmp_path / "caches", tmp_path / "depensio"
    root.mkdir()

    folder = _cache(caches, root)

    with held(root, caches=caches) as local:
        assert local == folder / CACHE_FOLDER
        assert (local / "witness").read_text(encoding="utf-8") == "kept"
    assert (folder / PROJECT_FILE).read_text(encoding="utf-8").strip() == str(root.resolve())


def test_a_project_never_reaches_another_project_s_cache(tmp_path: Path) -> None:
    caches = tmp_path / "caches"
    first, second = tmp_path / "devis", tmp_path / "factures"
    first.mkdir()
    second.mkdir()
    _cache(caches, first)

    with held(first, caches=caches) as mine, held(second, caches=caches) as theirs:
        assert mine is not None and theirs is not None
        assert mine != theirs
        assert not (theirs / "witness").exists()


def test_a_cache_another_verification_holds_leaves_this_one_to_its_workbench(
    tmp_path: Path,
) -> None:
    """What a tool writes there is unknown to Maestro: two writers at once have no guarantee."""
    caches, root = tmp_path / "caches", tmp_path / "depensio"
    root.mkdir()

    with held(root, caches=caches) as first:
        assert first is not None
        with held(root, caches=caches) as second:
            assert second is None
    with held(root, caches=caches) as again:
        assert again == first


def test_a_cache_held_by_another_process_is_not_shared_and_its_death_releases_it(
    tmp_path: Path,
) -> None:
    """The API and a worker verify on one machine: the lock is the system's, not a variable's."""
    caches, root = tmp_path / "caches", tmp_path / "depensio"
    root.mkdir()
    holder = (
        "import sys\n"
        "from pathlib import Path\n"
        "from maestro.sandbox.verification_cache import held\n"
        "with held(Path(sys.argv[1]), caches=Path(sys.argv[2])) as local:\n"
        "    print('HELD' if local is not None else 'REFUSED', flush=True)\n"
        "    sys.stdin.read()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", holder, str(root), str(caches)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY)},
        cwd=REPOSITORY,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "HELD"
        with held(root, caches=caches) as while_held:
            assert while_held is None
    finally:
        child.kill()
        child.wait()

    assert _held_once_released(root, caches) == caches / project_key(root) / CACHE_FOLDER


def _held_once_released(root: Path, caches: Path, *, within_s: float = 10.0) -> Path | None:
    """Opens `root`'s cache as soon as the system has released a dead holder's lock.

    Windows unlocks what a dead process held at a moment that depends on the system's
    load (`LockFileEx`): the release is certain, not immediate. Meanwhile a verification
    keeps to its workbench, which is the behaviour `held` owes; the test waits for the
    release it asserts, never for a fixed time.
    """
    deadline = time.monotonic() + within_s
    while True:
        with held(root, caches=caches) as local:
            if local is not None or time.monotonic() > deadline:
                return local
        time.sleep(0.05)


def test_a_cache_that_cannot_be_opened_leaves_the_verification_to_its_workbench(
    tmp_path: Path,
) -> None:
    caches = tmp_path / "caches"
    caches.write_text("a file where the folder should be", encoding="utf-8")
    root = tmp_path / "depensio"
    root.mkdir()

    with held(root, caches=caches) as local:
        assert local is None


# --- Retention --------------------------------------------------------------------------------


def _projects(tmp_path: Path, count: int) -> list[Path]:
    roots = [tmp_path / f"projet-{rank}" for rank in range(count)]
    for root in roots:
        root.mkdir()
    return roots


def test_the_retention_removes_what_no_verification_reopened_and_keeps_the_rest(
    tmp_path: Path,
) -> None:
    caches = tmp_path / "caches"
    stale, recent, opened = _projects(tmp_path, 3)
    stale_folder = _cache(caches, stale, age_s=RETENTION_S + 3600)
    recent_folder = _cache(caches, recent, age_s=RETENTION_S - 3600)

    with held(opened, caches=caches) as local:
        assert local is not None

    assert not stale_folder.exists()
    assert (recent_folder / CACHE_FOLDER / "witness").exists()


def test_the_retention_keeps_at_most_the_most_recent_caches(tmp_path: Path) -> None:
    caches = tmp_path / "caches"
    *older, opened = _projects(tmp_path, KEPT_MAX + 2)
    folders = [_cache(caches, root, age_s=3600.0 * (rank + 1)) for rank, root in enumerate(older)]

    with held(opened, caches=caches):
        pass

    kept = [folder for folder in folders if folder.exists()]
    assert kept == folders[: KEPT_MAX - 1], "the one opened counts among those kept"
    assert (caches / project_key(opened)).is_dir()


def test_the_retention_never_removes_a_cache_another_verification_holds(tmp_path: Path) -> None:
    caches = tmp_path / "caches"
    busy, opened = _projects(tmp_path, 2)
    folder = _cache(caches, busy)

    with held(busy, caches=caches) as local:
        assert local is not None
        _age(folder, RETENTION_S * 2)
        assert retain(caches, keep=project_key(opened)) == ()
        assert (local / "witness").exists()


def test_a_removal_cut_short_is_finished_at_the_next_opening(tmp_path: Path) -> None:
    """A folder left without its lock file, or half emptied, is still removed."""
    caches = tmp_path / "caches"
    left = caches / "0123456789abcdef"
    (left / CACHE_FOLDER / "pip").mkdir(parents=True)
    old = time.time() - RETENTION_S * 2
    os.utime(left, (old, old))
    root = tmp_path / "depensio"
    root.mkdir()

    with held(root, caches=caches):
        pass

    assert not left.exists()
    assert (caches / project_key(root) / LOCK_FILE).is_file()


def test_a_removed_cache_is_born_again_empty_at_its_project_s_next_verification(
    tmp_path: Path,
) -> None:
    caches = tmp_path / "caches"
    root, other = _projects(tmp_path, 2)
    folder = _cache(caches, root, age_s=RETENTION_S * 2)
    with held(other, caches=caches):
        pass
    assert not folder.exists()

    with held(root, caches=caches) as local:
        assert local == folder / CACHE_FOLDER
        assert list(local.iterdir()) == []


def test_the_machine_s_caches_are_read_at_each_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conftest moves them for the session; a test, or the machine, moves them the same way."""
    machine = tmp_path / "machine-caches"
    monkeypatch.setattr(verification_cache, "caches_de_verification", lambda: machine)
    root = tmp_path / "depensio"
    root.mkdir()

    with held(root) as local:
        assert local == machine / project_key(root) / CACHE_FOLDER
