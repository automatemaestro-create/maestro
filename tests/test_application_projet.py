"""Tests of applying the work into the user's project (#227, EF-37).

Covers the three criteria of the lot: **what would change** (`work_diff`, on both paths of
decision D2 — Git branch or copy), the **boundary check** that precedes any write
(`check_perimeter`, EF-38) and the **write** itself (`apply_diff`: merge or copy back), plus the
hook into the existing human validation (`appliquer_sous_validation`).

No network, no model, no human: the tests that need a real repository set up a throwaway one and
are skipped where `git` is missing; the validator is a function of the test, which makes approval
and refusal playable without a UI. The two invariants that cost the most if they give way each
have their named test: a refusal **writes nothing** (neither partially nor « almost »), and a
deletion is **never** applied to an unversioned project (ENF-13).

The last section covers three ways of losing the user's work that the rest of the file does not
put to the test (#220): a diff that would write along the way, a UI that falls over while it is
being asked for approval, and a refusal that would take the task branch down with it.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import DemandeValidation
from maestro.projects.apply import (
    APPLY_APPROVED,
    APPLY_DECLINED,
    APPLY_NOT_NEEDED,
    KIND_ADDED,
    KIND_DELETED,
    KIND_MODIFIED,
    ApplyRejected,
    ApplyResult,
    FileChange,
    ProjectDiff,
    apply_diff,
    check_perimeter,
    commit_pending,
    save_commit_message,
    work_diff,
)
from maestro.projects.model import Perimeter, Project, Vcs
from maestro.projects.root import detect_vcs
from maestro.sandbox import branche_de_tache

GIT = shutil.which("git")

needs_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `Path.home()` under `tmp_path`, as in the tests of the foundation (#221, #224).

    Required under Windows: pytest's `tmp_path` lives under `AppData`, which `validate_root`
    rightly forbids — without this isolation, every project root of this file would be rejected.
    """
    home = tmp_path / "maison"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def _no_ambient_git_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cuts the workstation's Git identity, so these tests answer the same everywhere (#333).

    The throwaway repository never sets an identity, on purpose: that is the situation
    `maestro.projects.apply` says it handles, a user's project that has no reason to hold one
    valid for an agent. The workstation's `~/.gitconfig` would provide one silently, so the merge
    would succeed on every development machine **without ever going through the code's `-c`**;
    only a bare runner, where git answers « Committer identity unknown », would show the gap.

    `GIT_CONFIG_GLOBAL` on a missing file (git ≥ 2.32) amounts to an empty `~/.gitconfig`. The
    SYSTEM configuration stays in place: under Windows it carries `core.autocrlf`, which these
    tests depend on far more than on an identity, and the identity is not there.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


def _project(root: Path, *, vcs: Vcs | None = None) -> Project:
    """A project pointing at `root` (the factory does not touch the repository)."""
    return Project(
        id="prj-0000dead",
        name="Démo",
        root=root.as_posix(),
        vcs=vcs,
        perimeter=Perimeter(),
    )


def _copied_project(tmp_path: Path) -> tuple[Project, Path]:
    """An **unversioned** project and the workspace (the copy) of its task."""
    root = tmp_path / "projets" / "depensio"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    (root / "README.md").write_text("# Démo\n", encoding="utf-8")
    workspace = tmp_path / "espaces" / "t1"
    (workspace / "src").mkdir(parents=True)
    shutil.copy2(root / "README.md", workspace / "README.md")  # unchanged
    (workspace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")  # modified
    (workspace / "src" / "neuf.py").write_text("neuf\n", encoding="utf-8")  # added
    return _project(root), workspace


def _git(cwd: Path, *arguments: str) -> str:
    """Runs `git` in `cwd` and returns its output — fails the test if Git fails."""
    result = subprocess.run(
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(arguments)} : {result.stderr}"
    return result.stdout


def _commit(cwd: Path, message: str) -> None:
    """A commit with an identity set by `-c` (the throwaway repository has none)."""
    _git(cwd, "add", "-A")
    _git(
        cwd,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def _git_project(tmp_path: Path, *, branch: str = "main") -> tuple[Project, Path]:
    """A **versioned** project: throwaway repository, one commit, VCS detected as for real."""
    root = tmp_path / "projets" / "depensio-git"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")
    (root / "README.md").write_text("# Démo\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _commit(root, "socle")
    return _project(root, vcs=detect_vcs(root)), root


def _worktree(root: Path, tmp_path: Path, task_id: str = "t1") -> tuple[Path, str]:
    """Mounts the task's worktree, as the derived workspace does (#224)."""
    branch = branche_de_tache(task_id)
    workspace = tmp_path / "espaces" / task_id
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "--quiet", "-b", branch, str(workspace))
    return workspace, branch


# --------------------------------------------------------------------------- #
# Unversioned project: the diff compares the copy with the root
# --------------------------------------------------------------------------- #


def test_the_diff_of_a_copy_tells_added_modified_and_unchanged_apart(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    diff = work_diff(project, workspace=workspace)
    by_path = {c.path: c for c in diff.changes}
    # `README.md` is identical on both sides: it is not part of the diff.
    assert set(by_path) == {"src/app.py", "src/neuf.py"}
    assert by_path["src/app.py"].kind == KIND_MODIFIED
    assert (by_path["src/app.py"].additions, by_path["src/app.py"].deletions) == (1, 0)
    assert by_path["src/neuf.py"].kind == KIND_ADDED
    assert by_path["src/neuf.py"].additions == 1
    assert not diff.versioned and not diff.empty
    assert (diff.files, diff.additions, diff.deletions) == (2, 2, 0)


def test_a_copy_never_yields_a_deletion(tmp_path: Path) -> None:
    """Invariant 4 of the module: a file missing from the copy is ambiguous, never erased."""
    project, workspace = _copied_project(tmp_path)
    (Path(project.root) / "src" / "ancien.py").write_text("vieux\n", encoding="utf-8")
    diff = work_diff(project, workspace=workspace)
    assert all(c.kind != KIND_DELETED for c in diff.changes)
    assert "src/ancien.py" not in {c.path for c in diff.changes}


def test_a_binary_file_is_reported_without_being_counted(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    diff = work_diff(project, workspace=workspace)
    binary = next(c for c in diff.changes if c.path == "logo.png")
    assert binary.binary and (binary.additions, binary.deletions) == (0, 0)


def test_symbolic_links_in_the_copy_are_ignored(tmp_path: Path) -> None:
    """Same rule as for the copy (#224): a link to `~/.ssh` would be the leak being closed."""
    project, workspace = _copied_project(tmp_path)
    outside = tmp_path / "dehors.txt"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        (workspace / "lien.txt").symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without the link right
        pytest.skip("liens symboliques indisponibles sur ce poste")
    diff = work_diff(project, workspace=workspace)
    assert "lien.txt" not in {c.path for c in diff.changes}


def test_without_a_workspace_an_unversioned_project_is_rejected_with_its_reason(
    tmp_path: Path,
) -> None:
    project, _ = _copied_project(tmp_path)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project)
    assert rejection.value.reason == "espace-introuvable"


def test_a_vanished_workspace_is_rejected_too(tmp_path: Path) -> None:
    project, _ = _copied_project(tmp_path)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project, workspace=tmp_path / "espaces" / "envole")
    assert rejection.value.reason == "espace-introuvable"


# --------------------------------------------------------------------------- #
# Versioned project: the diff reads from Git
# --------------------------------------------------------------------------- #


@needs_git
def test_a_versioned_project_requires_the_task_branch(tmp_path: Path) -> None:
    """The branch name is *passed*, never recomputed here (design choice 1 of the module)."""
    project, _ = _git_project(tmp_path)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project)
    assert rejection.value.reason == "branche-inconnue"


@needs_git
def test_a_missing_task_branch_is_rejected_with_its_reason(tmp_path: Path) -> None:
    project, _ = _git_project(tmp_path)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project, branch="maestro/jamais-creee")
    assert rejection.value.reason == "branche-introuvable"


@needs_git
def test_a_repository_without_a_declared_base_branch_is_rejected(tmp_path: Path) -> None:
    """HEAD detached at declaration: there is nothing to merge into."""
    _, root = _git_project(tmp_path)
    project = _project(root, vcs=Vcs(type="git", base_branch=""))
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project, branch="maestro/t1")
    assert rejection.value.reason == "base-introuvable"


@needs_git
def test_the_diff_of_a_mounted_worktree_carries_the_uncommitted_work(tmp_path: Path) -> None:
    """An agent writes files; it does not necessarily `git add` them."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    # A tracked file, modified, then an untracked one.
    (workspace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    by_path = {c.path: c for c in diff.changes}
    assert set(by_path) == {"src/app.py", "note.md"}
    assert by_path["note.md"].kind == KIND_ADDED
    assert by_path["src/app.py"].kind == KIND_MODIFIED
    assert diff.versioned and (diff.branch, diff.base) == (branch, "main")


@needs_git
def test_the_diff_still_reads_from_the_branch_once_the_worktree_is_removed(
    tmp_path: Path,
) -> None:
    """For a versioned project, applying can be asked for long after the task."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    (workspace / "README.md").unlink()
    _commit(workspace, "travail de la tâche")
    _git(root, "worktree", "remove", "--force", str(workspace))
    diff = work_diff(project, branch=branch)
    kind_by_path = {c.path: c.kind for c in diff.changes}
    assert kind_by_path == {"src/app.py": KIND_MODIFIED, "README.md": KIND_DELETED}


@needs_git
def test_work_the_base_took_in_the_meantime_does_not_show_up_as_a_deletion(
    tmp_path: Path,
) -> None:
    """`base...branch` (three dots): what gets merged is the branch's changes."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    _commit(workspace, "travail de la tâche")
    _git(root, "worktree", "remove", "--force", str(workspace))
    # A colleague moves `main` forward on their side, on another file.
    (root / "collegue.md").write_text("moi aussi\n", encoding="utf-8")
    _commit(root, "travail du collègue")
    diff = work_diff(project, branch=branch)
    assert {c.path for c in diff.changes} == {"src/app.py"}


# --------------------------------------------------------------------------- #
# The boundary check (EF-38)
# --------------------------------------------------------------------------- #


def test_the_perimeter_check_returns_absolute_paths_under_the_root(tmp_path: Path) -> None:
    project, _ = _copied_project(tmp_path)
    diff = ProjectDiff(changes=(FileChange(path="src/app.py"),))
    (path,) = check_perimeter(project, diff)
    assert path == (Path(project.root) / "src" / "app.py").resolve()


def test_a_path_leaving_the_root_is_rejected_with_its_reason(tmp_path: Path) -> None:
    project, _ = _copied_project(tmp_path)
    diff = ProjectDiff(changes=(FileChange(path="../dehors.txt"),))
    with pytest.raises(ApplyRejected) as rejection:
        check_perimeter(project, diff)
    assert rejection.value.reason == "hors-racine"


def test_the_whole_perimeter_is_checked_before_the_first_write(tmp_path: Path) -> None:
    """« Rejected with its reason, not partially applied »: the criterion of the lot."""
    project, workspace = _copied_project(tmp_path)
    diff = ProjectDiff(
        changes=(
            FileChange(path="src/neuf.py", kind=KIND_ADDED),
            FileChange(path="../dehors.txt", kind=KIND_ADDED),
        )
    )
    with pytest.raises(ApplyRejected) as rejection:
        apply_diff(project, diff, workspace=workspace)
    assert rejection.value.reason == "hors-racine"
    assert not (Path(project.root) / "src" / "neuf.py").exists()  # nothing was written
    assert not (tmp_path / "projets" / "dehors.txt").exists()


# --------------------------------------------------------------------------- #
# Applying — unversioned project: the copy back
# --------------------------------------------------------------------------- #


def test_apply_copies_the_diff_files_back_into_the_root(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    root = Path(project.root)
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    diff = work_diff(project, workspace=workspace)
    applied = apply_diff(project, diff, workspace=workspace)
    assert set(applied) == {"src/app.py", "src/neuf.py", "docs/guide.md"}
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert (root / "src" / "neuf.py").read_text(encoding="utf-8") == "neuf\n"
    # The folder missing from the root is created along the way.
    assert (root / "docs" / "guide.md").read_text(encoding="utf-8") == "guide\n"


def test_no_deletion_is_applied_to_an_unversioned_project(tmp_path: Path) -> None:
    """ENF-13: erasing on an ambiguity, in a project without history, never."""
    project, workspace = _copied_project(tmp_path)
    root = Path(project.root)
    diff = ProjectDiff(changes=(FileChange(path="README.md", kind=KIND_DELETED),))
    assert apply_diff(project, diff, workspace=workspace) == ()
    assert (root / "README.md").exists()


def test_an_empty_diff_writes_nothing_and_bothers_no_one(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    assert apply_diff(project, ProjectDiff(), workspace=workspace) == ()


def test_an_impossible_write_is_rejected_with_its_reason(tmp_path: Path) -> None:
    """The source vanished between the diff and the approval: reasoned rejection, no trace."""
    project, workspace = _copied_project(tmp_path)
    diff = ProjectDiff(changes=(FileChange(path="src/envole.py", kind=KIND_ADDED),))
    with pytest.raises(ApplyRejected) as rejection:
        apply_diff(project, diff, workspace=workspace)
    assert rejection.value.reason == "ecriture-refusee"


# --------------------------------------------------------------------------- #
# Applying — versioned project: the merge
# --------------------------------------------------------------------------- #


@needs_git
def test_apply_merges_the_task_branch_into_the_base_branch(tmp_path: Path) -> None:
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "src" / "app.py").write_text("un\ndeux\ntrois\n", encoding="utf-8")
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    applied = apply_diff(project, diff, workspace=workspace)
    assert set(applied) == {"src/app.py", "note.md"}
    # The worktree's uncommitted work was committed on its branch, then merged.
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert (root / "note.md").read_text(encoding="utf-8") == "bonjour\n"
    # `--no-ff`: it is a merge commit, so `git revert` undoes it (D2, option B).
    assert len(_git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    # The task branch is never deleted: the work stays inspectable.
    assert branch in _git(root, "branch", "--list", branch)


@needs_git
def test_the_merge_is_rejected_when_the_root_is_on_another_branch(tmp_path: Path) -> None:
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    _git(root, "checkout", "--quiet", "-b", "autre-chose")
    with pytest.raises(ApplyRejected) as rejection:
        apply_diff(project, diff, workspace=workspace)
    assert rejection.value.reason == "racine-occupee"
    assert not (root / "note.md").exists()


@needs_git
def test_the_merge_is_rejected_when_the_root_has_changes_in_progress(tmp_path: Path) -> None:
    """Merging under someone's feet is exactly what EF-37 prevents."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    (root / "README.md").write_text("# En cours d'édition\n", encoding="utf-8")
    with pytest.raises(ApplyRejected) as rejection:
        apply_diff(project, diff, workspace=workspace)
    assert rejection.value.reason == "racine-occupee"
    assert not (root / "note.md").exists()


@needs_git
def test_a_conflicting_merge_is_rejected_and_the_branch_keeps_the_work(
    tmp_path: Path,
) -> None:
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "src" / "app.py").write_text("version de l'agent\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    # The base branch changes the same line on its side: the merge cannot decide.
    (root / "src" / "app.py").write_text("version de l'humain\n", encoding="utf-8")
    _commit(root, "édition concurrente")
    with pytest.raises(ApplyRejected) as rejection:
        apply_diff(project, diff, workspace=workspace)
    assert rejection.value.reason == "fusion-refusee"
    # `merge --abort` ran: the root is clean and on its branch.
    assert _git(root, "status", "--porcelain").strip() == ""
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert branch in _git(root, "branch", "--list", branch)


@needs_git
def test_an_already_committed_worktree_is_merged_without_a_catch_up_commit(
    tmp_path: Path,
) -> None:
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    _commit(workspace, "travail de la tâche")
    diff = work_diff(project, branch=branch, workspace=workspace)
    before = _git(workspace, "rev-parse", "HEAD").strip()
    apply_diff(project, diff, workspace=workspace)
    assert _git(workspace, "rev-parse", "HEAD").strip() == before  # nothing to catch up
    assert (root / "note.md").read_text(encoding="utf-8") == "bonjour\n"


# --------------------------------------------------------------------------- #
# Maestro's commits follow the project's convention (#1507)
# --------------------------------------------------------------------------- #


def _subjects(cwd: Path, revision_range: str) -> list[str]:
    """The subjects of the commits of `revision_range`, from newest to oldest."""
    return _git(cwd, "log", "--format=%s", revision_range).splitlines()


@needs_git
def test_the_uncommitted_leftover_joins_the_agents_last_commit(tmp_path: Path) -> None:
    """The catch-up does not invent a message (on p5, 33 « Maestro : … » commits).

    The agent committed in the project's convention, then left a file behind: it goes into
    **its** commit, whose message stays its own — no commit from Maestro. `since` is the head of
    the branch at mount: the amended commit belongs to this execution.
    """
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    at_mount = _git(workspace, "rev-parse", "HEAD").strip()
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    _commit(workspace, "feat(notes): ajoute la note d'accueil")
    (workspace / "oubli.md").write_text("resté hors du commit\n", encoding="utf-8")
    commit_pending(workspace, branch, since=at_mount)
    assert _subjects(workspace, "main..HEAD") == ["feat(notes): ajoute la note d'accueil"]
    assert "oubli.md" in _git(workspace, "show", "--name-only", "--format=", "HEAD").split()
    assert _git(workspace, "status", "--porcelain").strip() == ""


@needs_git
def test_a_maestro_save_commit_is_never_amended(tmp_path: Path) -> None:
    """A work Maestro saved at shutdown never disappears (S12, run `20261022-075529`).

    Maestro saved the work in flight in a commit; on resumption, that commit is the head of the
    branch **at mount** — it does not belong to this execution. The leftover at teardown goes
    into a new commit: the saved commit stays in the history, and everything said about it stays
    true.
    """
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "recettes.ts").write_text("export {}\n", encoding="utf-8")
    commit_pending(workspace, branch)  # the save at shutdown
    saved = _git(workspace, "rev-parse", "HEAD").strip()
    assert _subjects(workspace, "-1") == [save_commit_message(branch)]
    (workspace / "page.tsx").write_text("export default null\n", encoding="utf-8")
    commit_pending(workspace, branch, since=saved)  # the teardown, after the resumption
    assert _git(workspace, "rev-parse", "HEAD~1").strip() == saved
    _git(workspace, "merge-base", "--is-ancestor", saved, "HEAD")


@needs_git
def test_without_a_head_at_mount_nothing_is_amended(tmp_path: Path) -> None:
    """A caller that does not know where the branch started from rewrites nothing."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    _commit(workspace, "feat(notes): ajoute la note d'accueil")
    agent_commit = _git(workspace, "rev-parse", "HEAD").strip()
    (workspace / "oubli.md").write_text("resté hors du commit\n", encoding="utf-8")
    commit_pending(workspace, branch)
    assert _git(workspace, "rev-parse", "HEAD~1").strip() == agent_commit


@needs_git
def test_without_an_agent_commit_the_catch_up_keeps_maestros_message(
    tmp_path: Path,
) -> None:
    """The base is never rewritten: without a commit of its own, the branch gets Maestro's."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    base_commit = _git(root, "rev-parse", "main").strip()
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    commit_pending(workspace, branch)
    assert _subjects(workspace, "main..HEAD") == [f"Maestro : travail de la tâche ({branch})"]
    assert _git(root, "rev-parse", "main").strip() == base_commit


@needs_git
def test_a_commit_another_branch_carries_is_never_amended(tmp_path: Path) -> None:
    """A re-split starts from another task's branch (#1396): that commit is not its own."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "package.json").write_text("{}\n", encoding="utf-8")
    _commit(workspace, "feat: pose le socle")
    base_commit = _git(workspace, "rev-parse", "HEAD").strip()
    resumed = tmp_path / "espaces" / "t1-r1"
    _git(root, "worktree", "add", "--quiet", "-b", "maestro/t1-r1", str(resumed), branch)
    (resumed / "init.ts").write_text("export {}\n", encoding="utf-8")
    # Even when claiming the branch started further back: `base_commit` belongs to another one.
    commit_pending(resumed, "maestro/t1-r1", since=_git(root, "rev-parse", "main").strip())
    assert _git(resumed, "rev-parse", "HEAD~1").strip() == base_commit
    assert _subjects(resumed, f"{branch}..HEAD") == [
        "Maestro : travail de la tâche (maestro/t1-r1)"
    ]


@needs_git
def test_the_merge_takes_the_subject_drafted_in_the_convention(tmp_path: Path) -> None:
    """The subject comes from the convention; what Maestro said about it stays, in the body."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    _commit(workspace, "feat(notes): ajoute la note d'accueil")
    diff = work_diff(project, branch=branch)
    apply_diff(project, diff, message="feat(notes): intègre la note d'accueil")
    assert _git(root, "log", "-1", "--format=%s").strip() == (
        "feat(notes): intègre la note d'accueil"
    )
    body = _git(root, "log", "-1", "--format=%b")
    assert f"Maestro : application de {branch}" in body
    assert len(_git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


@needs_git
def test_without_a_drafted_message_the_merge_keeps_maestros(tmp_path: Path) -> None:
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")
    diff = work_diff(project, branch=branch, workspace=workspace)
    apply_diff(project, diff, workspace=workspace)
    assert _git(root, "log", "-1", "--format=%s").strip().startswith(
        f"Maestro : application de {branch}"
    )


# --------------------------------------------------------------------------- #
# Git missing or angry: the failure paths, playable everywhere
# --------------------------------------------------------------------------- #


def test_unavailable_git_becomes_a_reasoned_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, root = (
        _project(tmp_path / "projets" / "faux", vcs=Vcs(type="git", base_branch="main")),
        tmp_path / "projets" / "faux",
    )
    root.mkdir(parents=True)

    def _no_git(*args: object, **kwargs: object) -> object:
        raise OSError("git introuvable")

    monkeypatch.setattr(subprocess, "run", _no_git)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project, branch="maestro/t1")
    assert rejection.value.reason == "git-indisponible"


@needs_git
def test_a_non_zero_git_verdict_becomes_a_reasoned_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A return code is an answer, which the caller translates — not an exception."""
    project, root = _git_project(tmp_path)
    _, branch = _worktree(root, tmp_path)
    real_run = subprocess.run

    def _fails_on_the_diff(command: list[str], **kwargs: object) -> object:
        if "diff" in command:
            return subprocess.CompletedProcess(command, 128, "", "fatal: objet illisible")
        return real_run(command, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _fails_on_the_diff)
    with pytest.raises(ApplyRejected) as rejection:
        work_diff(project, branch=branch)
    assert rejection.value.reason == "git-refuse"
    assert "objet illisible" in str(rejection.value)


# --------------------------------------------------------------------------- #
# The diff as an attachment: summary and JSON round trip
# --------------------------------------------------------------------------- #


def test_the_summary_of_an_empty_diff_says_so() -> None:
    assert ProjectDiff().summary() == "Aucune modification à appliquer."


def test_the_summary_carries_the_totals_the_merge_and_the_per_file_detail() -> None:
    diff = ProjectDiff(
        changes=(
            FileChange(path="src/app.py", kind=KIND_MODIFIED, additions=3, deletions=1),
            FileChange(path="logo.png", kind=KIND_ADDED, binary=True),
        ),
        branch="maestro/t1",
        base="main",
    )
    lines = diff.summary().splitlines()
    assert lines[0] == "2 fichier(s), +3 / −1 — fusion de maestro/t1 vers main"
    assert lines[1] == "  ~ src/app.py (+3 −1)"
    assert lines[2] == "  + logo.png (binaire)"


def test_the_summary_caps_the_list_and_counts_the_rest() -> None:
    """It is the log sentence that gets capped — the UI shows everything."""
    diff = ProjectDiff(changes=tuple(FileChange(path=f"f{i}.txt") for i in range(45)))
    lines = diff.summary().splitlines()
    assert lines[-1] == "  … et 5 autre(s) fichier(s)"
    assert len(lines) == 1 + 40 + 1


def test_the_diff_round_trips_through_json_without_losing_its_kinds() -> None:
    diff = ProjectDiff(
        changes=(
            FileChange(path="a.py", kind=KIND_ADDED, additions=2),
            FileChange(path="b.py", kind=KIND_DELETED, deletions=7),
            FileChange(path="c.bin", binary=True),
        ),
        branch="maestro/t1",
        base="main",
    )
    assert ProjectDiff.from_dict(diff.to_dict()) == diff


def test_the_totals_of_a_reread_diff_are_derived_never_read() -> None:
    """A header that contradicts its list must not be able to come in through the JSON."""
    raw = ProjectDiff(
        changes=(FileChange(path="a.py", kind=KIND_ADDED, additions=2),)
    ).to_dict()
    raw["fichiers"], raw["ajouts"], raw["suppressions"] = 99, 99, 99
    reread = ProjectDiff.from_dict(raw)
    assert (reread.files, reread.additions, reread.deletions) == (1, 2, 0)


def test_a_reread_diff_ignores_changes_that_are_not_objects() -> None:
    reread = ProjectDiff.from_dict({"modifications": ["pas un objet", {"chemin": "a.py"}]})
    assert [c.path for c in reread.changes] == ["a.py"]


def test_an_apply_result_serializes() -> None:
    result = ApplyResult(
        status=APPLY_APPROVED,
        diff=ProjectDiff(changes=(FileChange(path="a.py"),)),
        detail="approuvée",
        applied=("a.py",),
    )
    assert result.approved
    raw = result.to_dict()
    assert raw["statut"] == APPLY_APPROVED
    assert raw["appliques"] == ["a.py"]
    assert raw["diff"]["fichiers"] == 1


# --------------------------------------------------------------------------- #
# The hook into the existing human validation (EF-08)
# --------------------------------------------------------------------------- #


def test_an_empty_diff_bothers_no_human(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    shutil.rmtree(workspace / "src")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("un\ndeux\n", encoding="utf-8")  # identical
    requests: list[DemandeValidation] = []

    def _approve(request: DemandeValidation) -> bool:
        requests.append(request)
        return True

    result = asyncio.run(
        appliquer_sous_validation(
            project, tache_id="t1", validateur=_approve, espace=workspace
        )
    )
    assert result.status == APPLY_NOT_NEEDED
    assert requests == []  # no one was asked


def test_a_human_approval_triggers_the_write(tmp_path: Path) -> None:
    project, workspace = _copied_project(tmp_path)
    requests: list[DemandeValidation] = []

    def _approve(request: DemandeValidation) -> bool:
        requests.append(request)
        return True

    result = asyncio.run(
        appliquer_sous_validation(
            project,
            tache_id="t1",
            validateur=_approve,
            espace=workspace,
            agent="dev",
            role="Dev",
        )
    )
    assert result.status == APPLY_APPROVED and result.approved
    assert set(result.applied) == {"src/app.py", "src/neuf.py"}
    assert (Path(project.root) / "src" / "neuf.py").exists()
    # The diff travels as an attachment: without it, the question cannot be decided.
    (request,) = requests
    assert request.diff is not None and request.diff.files == 2
    assert "2 fichier(s)" in request.description


def test_a_human_refusal_writes_nothing_and_leaves_the_work_inspectable(
    tmp_path: Path,
) -> None:
    project, workspace = _copied_project(tmp_path)
    result = asyncio.run(
        appliquer_sous_validation(
            project, tache_id="t1", validateur=lambda _: False, espace=workspace
        )
    )
    assert result.status == APPLY_DECLINED and not result.approved
    assert result.applied == ()
    assert not (Path(project.root) / "src" / "neuf.py").exists()
    assert (workspace / "src" / "neuf.py").exists()  # the copy stays where it is


def test_without_a_validator_the_guardrails_fail_safe_refuses(tmp_path: Path) -> None:
    """The fail-safe is the one from #9, reused as is rather than rewritten."""
    project, workspace = _copied_project(tmp_path)
    result = asyncio.run(
        appliquer_sous_validation(project, tache_id="t1", validateur=None, espace=workspace)
    )
    assert result.status == APPLY_DECLINED
    assert not (Path(project.root) / "src" / "neuf.py").exists()


def test_a_path_outside_the_perimeter_raises_before_the_request_is_even_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is asked to approve what will be refused at write time (step 2 of the flow).

    The diff is forged: `work_diff` cannot produce a path outside the root, and it is precisely
    for a diff that comes from elsewhere — a replayed event, a project file edited by hand — that
    the boundary check exists.
    """
    project, workspace = _copied_project(tmp_path)
    asked = False

    def _approve(_: DemandeValidation) -> bool:
        nonlocal asked
        asked = True
        return True

    monkeypatch.setattr(
        "maestro.controltower.validation.work_diff",
        lambda *_args, **_kwargs: ProjectDiff(
            changes=(FileChange(path="../dehors.txt", kind=KIND_ADDED),)
        ),
    )
    with pytest.raises(ApplyRejected) as rejection:
        asyncio.run(
            appliquer_sous_validation(
                project, tache_id="t1", validateur=_approve, espace=workspace
            )
        )
    assert rejection.value.reason == "hors-racine"
    assert not asked


# --------------------------------------------------------------------------- #
# Three ways of losing the user's work (#220)
# --------------------------------------------------------------------------- #


def test_the_diff_writes_nothing_into_the_root(tmp_path: Path) -> None:
    """« What the work would change » is asked **before** anything is decided."""
    project, workspace = _copied_project(tmp_path)
    root = Path(project.root)

    diff = work_diff(project, workspace=workspace)

    assert not diff.empty  # there was indeed something to write…
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "un\ndeux\n"
    assert not (root / "src" / "neuf.py").exists()  # …and none of it was


@needs_git
def test_the_diff_of_a_versioned_project_leaves_the_root_where_it_was(tmp_path: Path) -> None:
    """The diff goes through Git: it could move the root without writing anything into it."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")

    diff = work_diff(project, branch=branch, workspace=workspace)

    assert not diff.empty
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(root, "status", "--porcelain") == ""
    assert not (root / "note.md").exists()


def test_a_validator_that_raises_gets_nothing_written(tmp_path: Path) -> None:
    """The UI falls over while approval is being asked: that is a refusal, not a blank cheque."""
    project, workspace = _copied_project(tmp_path)

    def _falls_over(_: DemandeValidation) -> bool:
        raise RuntimeError("l'onglet a été fermé pendant la demande")

    result = asyncio.run(
        appliquer_sous_validation(
            project, tache_id="t1", validateur=_falls_over, espace=workspace
        )
    )

    assert result.status == APPLY_DECLINED
    assert not (Path(project.root) / "src" / "neuf.py").exists()


@needs_git
def test_a_refusal_never_deletes_the_task_branch(tmp_path: Path) -> None:
    """Refusing means putting off: the work must stay resubmittable."""
    project, root = _git_project(tmp_path)
    workspace, branch = _worktree(root, tmp_path)
    (workspace / "note.md").write_text("bonjour\n", encoding="utf-8")

    result = asyncio.run(
        appliquer_sous_validation(
            project,
            tache_id="t1",
            validateur=lambda _: False,
            branche=branch,
            espace=workspace,
        )
    )

    assert result.status == APPLY_DECLINED
    assert not (root / "note.md").exists()
    assert branch in _git(root, "branch", "--list", branch)
    assert (workspace / "note.md").exists()  # the work stayed in its workspace
