"""Applying a task's work **into** the user's project (#227, EF-37).

An agent works in a workspace **derived** from the project — a Git worktree on a
`maestro/<task>` branch when the project is versioned, a copy of its perimeter
otherwise (#224, `maestro.sandbox.projet`). This module brings that work back into
the project (parent #219): without it, the work would be a worktree to merge by hand.

This module computes **what would change** and writes it **when allowed to**. It
decides nothing: human approval belongs to
`maestro.controltower.validation.appliquer_sous_validation`, which plugs this
action into the existing approval mechanism (EF-08) instead of inventing one.

Two paths, those of decision **D2**
([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md)):

- **versioned project** → the diff is the task branch against the **declared base
  branch** (`Project.vcs.base_branch`), and applying it means **merging** that
  branch. Rollback stays native: it is a merge commit, `git revert` undoes it;
- **unversioned project** → the diff is the comparison of the **copy** with the
  root, and applying it means copying the files back there. With no history to
  roll back, the diff shown to the human is the safety net.
  ⚠ The engine never calls this path (#839, D2 revised, docs/24 §2.4): an
  unversioned project is filled in place, with neither copy nor diff. It is
  reachable only through `appliquer_sous_validation`, given an explicit
  workspace.

Four choices to know before touching it:

1. **the task branch name is never computed here** — it is *passed* by the
   caller, which holds it from `maestro.sandbox.projet.branche_de_tache`
   (#224). Two spellings of one convention would end up diverging, and the
   wrong one would merge another task's branch;
2. **the whole diff is checked before the first write** — no path leaves the
   root (EF-38, `path_in_root`), and a rejection leaves the project exactly as
   it was: “rejected with its reason, not partially applied” is the criterion,
   and it does not hold when validation happens while writing;
3. **the call point belongs to whoever holds the workspace.** For a versioned
   project, everything is read from the branch: the apply can be requested
   long after the task. For an **unversioned** project, the copy is the diff —
   closed by `AgentRuntime.execute` (#224), it takes the work with it, so the
   apply must be requested while it is open. That is why this module takes the
   workspace as an argument instead of fetching it: it does not decide its
   lifetime;
4. **no deletion is applied to an unversioned project.** A file missing from the
   copy cannot be told apart from a file the perimeter never put there
   (`DEFAULT_EXCLUDED` removes `.env`, `node_modules`, `**/secrets/**`);
   deleting on that ambiguity, in a project with no history, is exactly what
   ENF-13 exists to prevent. On a versioned project, where Git knows what it is
   talking about and rollback is native, deletions are part of the merge like
   everything else.
"""

from __future__ import annotations

import difflib
import os
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.projects.model import AUTHOR_EMAIL, AUTHOR_NAME, Project
from maestro.projects.root import RootRejected, path_in_root, validate_root

#: The three kinds of a file change, as the UI shows them.
KIND_ADDED = "ajoute"
KIND_MODIFIED = "modifie"
KIND_DELETED = "supprime"

#: Maximum duration of a Git command — the same anomaly bound as the derived
#: workspace (#224): mounting, diffing or merging are local operations of a few
#: tens of milliseconds, so a minute says “something is wrong” (network drive,
#: huge repository) rather than “wait a little longer”.
_GIT_TIMEOUT_S = 60

#: Size beyond which a file is no longer counted line by line: we know it
#: changes, not by how much. Same threshold as the capture of deliverables
#: (`maestro.sandbox.workspace`) — beyond it, it is an artefact, not code.
_MAX_COUNTED_BYTES = 1_000_000

#: Number of files named one by one in the text summary of a diff. The full
#: diff stays in `ProjectDiff.changes` (the UI shows it entirely); what is
#: bounded is the sentence of the journal and of the notification.
_MAX_SUMMARY_LINES = 40

#: The identity of the commits this module writes into the project — the catch-up commit
#: (`commit_pending`) and the merge commit (`_merge`, `--no-ff`) — lives in
#: `maestro.projects.model` (`AUTHOR_NAME`, `AUTHOR_EMAIL`), shared with the first commit
#: that puts a root under Git (#704). The reason for the `-c` is given there.


class ApplyRejected(RuntimeError):
    """The apply did not happen — **with its reason** (EF-37, EF-38).

    Same choice as `RootRejected` (#221) and `EspaceProjetIndisponible`
    (#224): the rejection carries a short, stable code that the API and the UI
    can show or translate (`hors-racine`, `branche-introuvable`, `base-introuvable`,
    `racine-occupee`, `fusion-refusee`, `git-indisponible`, `espace-introuvable`),
    the message being the readable sentence.

    A rejection is **total**: when it is raised, nothing was written into the project.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FileChange:
    """A file of the diff: what happens to it, and by how many lines.

    `path` is **relative to the project root**, in POSIX — the same form as
    the perimeter patterns and the deliverables (`ProducedFile.chemin`), hence
    comparable without conversion on the three OSes. `additions`/`deletions` are 0
    for a binary or too large file, which `binary` tells apart from a file that is
    really unchanged.
    """

    path: str
    kind: str = KIND_MODIFIED
    additions: int = 0
    deletions: int = 0
    binary: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Re-emit the change as a JSON-serialisable dict (the REST form)."""
        return {
            "chemin": self.path,
            "nature": self.kind,
            "ajouts": self.additions,
            "suppressions": self.deletions,
            "binaire": self.binary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileChange:
        """Rebuild the change from its `to_dict` form."""
        return cls(
            path=str(data.get("chemin", "")),
            kind=str(data.get("nature", KIND_MODIFIED)),
            additions=int(data.get("ajouts") or 0),
            deletions=int(data.get("suppressions") or 0),
            binary=bool(data.get("binaire")),
        )


@dataclass(frozen=True)
class ProjectDiff:
    """What the apply would change in the project — the attachment of the request.

    It is **the object the human approves**: the merge (or the copy back) that
    follows applies only what is described here. `branch` and `base` are set
    only for a versioned project — `branch` is the task branch to merge (set by
    #224, never recomputed here), `base` the project's declared base branch it
    leaves from.
    """

    changes: tuple[FileChange, ...] = ()
    branch: str = ""
    base: str = ""

    @property
    def versioned(self) -> bool:
        """Does the diff bear on a branch (versioned project)?"""
        return bool(self.branch)

    @property
    def empty(self) -> bool:
        """Nothing to apply — the task changed nothing in the project."""
        return not self.changes

    @property
    def files(self) -> int:
        """Number of files touched."""
        return len(self.changes)

    @property
    def additions(self) -> int:
        """Total of added lines."""
        return sum(m.additions for m in self.changes)

    @property
    def deletions(self) -> int:
        """Total of deleted lines."""
        return sum(m.deletions for m in self.changes)

    def summary(self) -> str:
        """The diff as text, as the journal and the notification carry it.

        A first line of totals, then one file per line — bounded by
        `_MAX_SUMMARY_LINES`, the rest being counted. The UI shows `changes`
        entirely: this summary serves where there is no screen.
        """
        if self.empty:
            return "Aucune modification à appliquer."
        header = (
            f"{self.files} fichier(s), +{self.additions} / −{self.deletions}"
        )
        if self.versioned:
            header += f" — fusion de {self.branch} vers {self.base}"
        lines = [header]
        for change in self.changes[:_MAX_SUMMARY_LINES]:
            count = "binaire" if change.binary else (
                f"+{change.additions} −{change.deletions}"
            )
            lines.append(f"  {_SIGN[change.kind]} {change.path} ({count})")
        remaining = self.files - _MAX_SUMMARY_LINES
        if remaining > 0:
            lines.append(f"  … et {remaining} autre(s) fichier(s)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Re-emit the diff as a JSON-serialisable dict (the WebSocket and REST form)."""
        return {
            "modifications": [m.to_dict() for m in self.changes],
            "branche": self.branch,
            "base": self.base,
            "fichiers": self.files,
            "ajouts": self.additions,
            "suppressions": self.deletions,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProjectDiff:
        """Rebuild the diff from its `to_dict` form (JSON round trip).

        The totals (keys `fichiers`, `ajouts`, `suppressions`) are not read back:
        they are **derived** from the changes, and reading them back would let
        through an event whose header contradicts its list.
        """
        raw = data.get("modifications") or ()
        return cls(
            changes=tuple(
                FileChange.from_dict(m) for m in raw if isinstance(m, Mapping)
            ),
            branch=str(data.get("branche", "") or ""),
            base=str(data.get("base", "") or ""),
        )


#: The sign of a kind in the text summary — the reading order of a diff.
_SIGN = {
    KIND_ADDED: "+",
    KIND_MODIFIED: "~",
    KIND_DELETED: "−",
}


# --------------------------------------------------------------------------- #
# Computing the diff
# --------------------------------------------------------------------------- #


def work_diff(
    project: Project,
    *,
    branch: str = "",
    workspace: Path | str | None = None,
) -> ProjectDiff:
    """What the task's work would change in `project`, without writing anything.

    `branch` is the task branch set by the derived workspace (#224,
    `branche_de_tache`): **required** for a versioned project, where it carries
    the work once the worktree is removed. `workspace` is the task's working
    directory: required for an **unversioned** project (it is the copy compared
    with the root), optional for a versioned project — while it is still mounted,
    the **uncommitted** work it holds enters the diff, otherwise only what is
    committed on the branch counts.

    Raises `RootRejected` if the root is no longer admissible (EF-38 — it is
    revalidated here, the project store being a folder of JSON files that can be
    edited by hand) and a reasoned `ApplyRejected` if the diff cannot be
    computed.
    """
    root = validate_root(project.root)
    if not project.versioned:
        return ProjectDiff(changes=_diff_copy(root, _require_workspace(workspace)))
    if not branch:
        raise ApplyRejected(
            "branche-inconnue",
            f"Projet versionné {project.name!r} : indiquez la branche de tâche à "
            "fusionner (celle posée par l'espace de travail dérivé).",
        )
    base = _base(project)
    _require_ref(root, branch, "branche-introuvable")
    _require_ref(root, base, "base-introuvable")
    path = Path(workspace) if workspace is not None else None
    changes = (
        _diff_worktree(path, base)
        if path is not None and path.is_dir()
        else _diff_branch(root, base, branch)
    )
    return ProjectDiff(changes=changes, branch=branch, base=base)


def _base(project: Project) -> str:
    """The project's declared base branch, or a reasoned rejection.

    A repository declared on a detached HEAD has no base branch: there is then
    **nothing to merge into**, and saying so is better than choosing a branch in
    the user's place.
    """
    base = project.vcs.base_branch if project.vcs is not None else ""
    if not base:
        raise ApplyRejected(
            "base-introuvable",
            f"Projet {project.name!r} : aucune branche de travail déclarée "
            "(dépôt en HEAD détaché à la déclaration) — re-déclarez le projet "
            "sur la branche vers laquelle fusionner.",
        )
    return base


def _require_workspace(workspace: Path | str | None) -> Path:
    """The task's workspace, or a reasoned rejection (unversioned project).

    Without Git history, the diff **is** the comparison of the copy with the
    root: without the copy, it does not exist. This happens when the workspace
    has already been cleaned up — the apply must then be requested before the
    task closes its workspace.
    """
    if workspace is None:
        raise ApplyRejected(
            "espace-introuvable",
            "Projet non versionné : l'espace de travail de la tâche est requis "
            "pour calculer le diff (la copie a-t-elle déjà été nettoyée ?).",
        )
    path = Path(workspace)
    if not path.is_dir():
        raise ApplyRejected(
            "espace-introuvable",
            f"Espace de travail introuvable : {path}.",
        )
    return path


def _diff_copy(root: Path, workspace: Path) -> tuple[FileChange, ...]:
    """Compare the copy `workspace` with the root, file by file (D2, option C).

    Walks **the copy**, not the root: the copy *is* the perimeter (#224 applies
    it there), so sticking to it guarantees that no excluded file comes out of
    the diff — without replaying the pattern engine, which lives with whoever
    applies it. Accepted corollary: this walk cannot see a deletion (see the
    module docstring), it only yields additions and modifications.

    No symbolic link is followed — the same rule as for the copy (#224), for the
    same reason: a link to `~/.ssh` copied back into the root would be the leak
    this claims to close.
    """
    changes: list[FileChange] = []
    for relative in sorted(_files(workspace)):
        source = workspace / relative
        target = root / relative
        if not target.exists():
            changes.append(
                FileChange(
                    path=relative,
                    kind=KIND_ADDED,
                    **_line_counts(None, source),
                )
            )
            continue
        if _identical(source, target):
            continue
        changes.append(
            FileChange(
                path=relative,
                kind=KIND_MODIFIED,
                **_line_counts(target, source),
            )
        )
    return tuple(changes)


def _files(workspace: Path) -> Iterator[str]:
    """The relative POSIX paths of the files of `workspace`, symbolic links excluded."""
    stack = [""]
    while stack:
        current = stack.pop()
        base = workspace / current if current else workspace
        try:
            with os.scandir(base) as entries:
                batch = list(entries)
        except OSError:  # folder turned unreadable: skipped, never fatal
            continue
        for entry in batch:
            relative = f"{current}/{entry.name}" if current else entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(relative)
            elif entry.is_file():
                yield relative


def _identical(source: Path, target: Path) -> bool:
    """Do the two files have exactly the same content?

    Comparison **by bytes**, not by size and date: we are about to write into
    someone's project, and a fingerprint is enough to list a deliverable (#224)
    but not to decide a replacement.
    """
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
        return source.read_bytes() == target.read_bytes()
    except OSError:
        return False


def _line_counts(before: Path | None, after: Path) -> dict[str, Any]:
    """Lines added/deleted between `before` (None = new file) and `after`.

    Also returns `binary`, true as soon as one of the two sides is not decodable
    text or exceeds `_MAX_COUNTED_BYTES`: we then know *that* the file changes,
    not *by how much*, and claiming otherwise would be worth less than saying so.
    The keys are the keyword arguments of `FileChange`, which receives the dict
    unpacked.
    """
    new_lines = _lines(after)
    old_lines = _lines(before) if before is not None else []
    if new_lines is None or old_lines is None:
        return {"additions": 0, "deletions": 0, "binary": True}
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, start_a, end_a, start_b, end_b in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            additions += end_b - start_b
        if tag in ("delete", "replace"):
            deletions += end_a - start_a
    return {"additions": additions, "deletions": deletions, "binary": False}


def _lines(path: Path) -> list[str] | None:
    """The lines of `path`, or None when it is binary, unreadable or too large."""
    try:
        if path.stat().st_size > _MAX_COUNTED_BYTES:
            return None
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Versioned project: the diff is read from Git
# --------------------------------------------------------------------------- #


def _diff_branch(root: Path, base: str, branch: str) -> tuple[FileChange, ...]:
    """The diff of `branch` since its starting point on `base` (worktree removed).

    `base...branch` (three dots) and not `base branch`: what is about to be
    merged is the changes **of the branch**, not those `base` has taken since.
    With two dots, a colleague's work merged in the meantime would come out as a
    deletion proposed by the agent.
    """
    return _git_changes(root, f"{base}...{branch}")


def _diff_worktree(workspace: Path, base: str) -> tuple[FileChange, ...]:
    """The diff of the still-mounted worktree: committed **and** uncommitted, plus new files.

    A tooled agent writes files; it does not necessarily `git add`. Settling for
    what is committed on the branch would show an empty diff to the human, then
    merge a branch without the work it was supposed to carry. Untracked files are
    therefore added to the diff — `--exclude-standard` removes what the project
    itself declared ignored, which is the only place where the user's
    `.gitignore` has a say.
    """
    tracked = _git_changes(workspace, base)
    known = {m.path for m in tracked}
    added = [
        FileChange(
            path=relative,
            kind=KIND_ADDED,
            **_line_counts(None, workspace / relative),
        )
        for relative in _untracked(workspace)
        if relative not in known
    ]
    return tuple(sorted([*tracked, *added], key=lambda m: m.path))


def _git_changes(cwd: Path, revision: str) -> tuple[FileChange, ...]:
    """The changes Git reports for `revision`, kinds and lines together.

    Two calls because Git does not return both at once: `--numstat` gives the
    lines (`-` for a binary) and `--name-status` the kind. `-z` avoids the
    escaping of file names — without it, an accented path comes out between
    quotes with its bytes as `\\303\\251`, and the path applied would not be the
    one Git saw. `--no-renames` keeps one record per line: a rename counts as a
    deletion and an addition, which is also the form in which it is applied.
    """
    counts = _read_numstat(_git(cwd, "diff", "--numstat", "-z", "--no-renames", revision))
    kinds = _read_name_status(
        _git(cwd, "diff", "--name-status", "-z", "--no-renames", revision)
    )
    return tuple(
        FileChange(
            path=path,
            kind=kinds.get(path, KIND_MODIFIED),
            additions=additions,
            deletions=deletions,
            binary=binary,
        )
        for path, (additions, deletions, binary) in sorted(counts.items())
    )


def _read_numstat(result: subprocess.CompletedProcess[str]) -> dict[str, tuple[int, int, bool]]:
    """`git diff --numstat -z` → `{path: (additions, deletions, binary)}`."""
    counts: dict[str, tuple[int, int, bool]] = {}
    for record in _records(result):
        parts = record.split("\t", 2)
        if len(parts) != 3:
            continue
        additions, deletions, path = parts
        binary = additions == "-" or deletions == "-"
        counts[path] = (
            0 if binary else int(additions),
            0 if binary else int(deletions),
            binary,
        )
    return counts


def _read_name_status(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """`git diff --name-status -z` → `{path: kind}` (status and path alternating)."""
    fields = _records(result)
    kinds: dict[str, str] = {}
    for index in range(0, len(fields) - 1, 2):
        status = fields[index][:1]
        kinds[fields[index + 1]] = _GIT_KINDS.get(status, KIND_MODIFIED)
    return kinds


#: A file's status in Git, brought back to the three kinds of `FileChange`.
#: Everything else (copy, type change, unmerged) is treated as a modification:
#: it is one, and inventing a fourth kind for a case the UI would show the same
#: way would help nobody.
_GIT_KINDS = {"A": KIND_ADDED, "D": KIND_DELETED, "M": KIND_MODIFIED}


def _records(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The fields of a Git output in `-z` (NUL-separated), empty ones removed."""
    if result.returncode != 0:
        raise ApplyRejected("git-refuse", f"Git a refusé : {_git_message(result)}")
    return [value for value in result.stdout.split("\0") if value]


def _untracked(workspace: Path) -> list[str]:
    """The untracked files of the worktree, minus what the project ignores."""
    result = _git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    return _records(result)


def _require_ref(root: Path, branch: str, reason: str) -> None:
    """Reject, with `reason`, when the local branch `branch` does not exist."""
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
        raise ApplyRejected(
            reason, f"Branche {branch!r} introuvable dans le dépôt {root}."
        )


#: How many commit subjects a task branch tells about itself, at most
#: (`branch_subjects`): enough to recognise a form and a language.
_SUBJECTS_MAX = 10


def branch_subjects(project: Project, branch: str) -> tuple[str, ...]:
    """The subjects of the commits `branch` carries beyond the base branch (#1507).

    From newest to oldest, bounded by `_SUBJECTS_MAX`. It is what the task's agent
    wrote, in the project's convention: the message of the merge that Maestro has
    drafted follows it (`maestro.projects.commits`). Silent — `()` — on an unversioned
    project, a rejected root or a Git that does not answer: this reading informs a
    drafting, it must never prevent the merge that follows.
    """
    base = project.vcs.base_branch if project.vcs is not None else ""
    if not base or not branch:
        return ()
    try:
        root = validate_root(project.root)
        result = _git(root, "log", f"-n{_SUBJECTS_MAX}", "--format=%s", f"{base}..{branch}")
    except (ApplyRejected, RootRejected):
        return ()
    if result.returncode != 0:
        return ()
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run `git <arguments>` in `cwd` and return the completed process.

    Raises **only** for what is not a verdict of Git (missing binary, timeout
    exceeded): a non-zero return code is an answer, which the caller turns into
    a reasoned rejection — that is what lets `_require_ref` ask a question
    without catching an exception.
    """
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApplyRejected(
            "git-indisponible", f"Git indisponible pour {cwd} : {exc}"
        ) from exc


def ignored_by_project(workspace: Path, relative: str) -> bool:
    """Does the project's `.gitignore` ignore `relative`? Git decides (`check-ignore`).

    An ignored file does not travel with a branch: `git add -A` leaves it in the
    worktree, and unmounting erases it (#1679 — the tooling manifest of a project
    that ignores `.maestro/`). A tracked file is never “ignored”, even when a pattern
    designates it. False when Git cannot answer: the caller then does nothing more
    than it would without this check.
    """
    try:
        return _git(workspace, "check-ignore", "-q", "--", relative).returncode == 0
    except ApplyRejected:
        return False


def _git_message(result: subprocess.CompletedProcess[str]) -> str:
    """Git's error message, on one line (stderr, or else stdout)."""
    raw = (result.stderr or result.stdout or "").strip()
    return " ".join(raw.split()) or f"code de retour {result.returncode}"


# --------------------------------------------------------------------------- #
# Perimeter: the check that precedes any write
# --------------------------------------------------------------------------- #


def check_perimeter(project: Project, diff: ProjectDiff) -> tuple[Path, ...]:
    """The absolute paths targeted by `diff`, or `ApplyRejected` (EF-38).

    Each path is resolved **under the root** by `path_in_root` — the building
    block of the base layer (#221): “no write is computed any other way”. A
    `..`, an absolute path from elsewhere or a link pointing outside the project
    are rejected with the base layer's reason (`hors-racine`).

    Called **before the first write**, and on the whole diff: the criterion is
    “rejected with its reason, not partially applied”, and it does not hold when
    checking happens along the writing.

    This check bears on the **boundary** (EF-38), not on the inclusion/exclusion
    patterns of the perimeter: those are applied when the workspace is derived
    (#224), by the project's single pattern engine — replaying a second one here
    would make two truths where one is needed.
    """
    paths: list[Path] = []
    for change in diff.changes:
        try:
            paths.append(path_in_root(project.root, change.path))
        except RootRejected as rejection:
            raise ApplyRejected(
                rejection.reason,
                f"Application refusée — {change.path} sort du périmètre "
                f"du projet {project.name!r} : {rejection}",
            ) from rejection
    return tuple(paths)


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def apply_diff(
    project: Project,
    diff: ProjectDiff,
    *,
    workspace: Path | str | None = None,
    message: str = "",
) -> tuple[str, ...]:
    """Write `diff` into the project and return the applied paths (EF-37, D2).

    Called only **after** a human approval (see
    `maestro.controltower.validation.appliquer_sous_validation`): this module
    asks nobody. Versioned project → the task branch is merged into the declared
    base branch; otherwise → the files of the diff are copied back into the
    root.

    `message` (#1507) is the subject of the merge commit, drafted in the project's
    convention (`maestro.projects.commits`); empty, it is Maestro's own
    (`_merge_message`). Irrelevant for a copy back, which commits nothing.

    The perimeter is checked in full first (`check_perimeter`): raising here
    leaves the project intact.
    """
    root = validate_root(project.root)
    check_perimeter(project, diff)
    if diff.empty:
        return ()
    if diff.versioned:
        return _merge(
            root, diff, Path(workspace) if workspace is not None else None, message=message
        )
    return _copy_back(root, diff, _require_workspace(workspace))


def _copy_back(root: Path, diff: ProjectDiff, workspace: Path) -> tuple[str, ...]:
    """Copy the files of the diff from `workspace` to `root` (unversioned project).

    Writes through `read_bytes`/`write_bytes` and not `shutil.copy2`: copying the
    metadata of a file produced in a temporary workspace brings nothing, and the
    explicit write follows no symbolic link already in place at the target.
    """
    applied: list[str] = []
    for change in diff.changes:
        if change.kind == KIND_DELETED:
            continue  # see the module docstring: never on a project without history
        source = workspace / change.path
        target = path_in_root(root, change.path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        except OSError as exc:
            raise ApplyRejected(
                "ecriture-refusee",
                f"Écriture impossible dans le projet : {change.path} — {exc}",
            ) from exc
        applied.append(change.path)
    return tuple(applied)


def _merge(
    root: Path, diff: ProjectDiff, workspace: Path | None, *, message: str = ""
) -> tuple[str, ...]:
    """Merge the task branch into the declared base branch (D2, option B).

    Three steps, in this order: the work still uncommitted in the worktree is
    **committed on its branch** (otherwise the merge would carry less than the
    approved diff), the state of the root is checked, then the merge happens.

    The root must be **clean and on the base branch**: merging under the feet of
    someone with changes in progress, or switching their working copy to another
    branch for an agent's needs, are precisely the destructions EF-36/EF-37
    exist to prevent. The rejection names what blocks rather than forcing.

    `message` (#1507), when given, is the **subject** of the merge commit — the
    project's convention wants it so —, and what Maestro said of it goes into the
    body: the merged branch and its totals stay readable in `git log`. The footer the
    convention requires (#1679, the ticket reference) comes **last**, below that
    body: that is where a convention and Git read a footer.
    """
    if workspace is not None and workspace.is_dir():
        commit_pending(workspace, diff.branch)
    _require_root_available(root, diff.base)
    messages = ["-m", _merge_message(diff)]
    subject, _, footer = message.strip().partition("\n")
    if subject.strip():
        messages = ["-m", subject.strip(), *messages]
    if footer.strip():
        messages += ["-m", footer.strip()]
    # `--no-ff` writes a merge COMMIT: it needs an identity, exactly like the catch-up
    # commit above (#333). Without it, the merge fails at the very last step on any
    # repository without an identity (fresh repository, container, CI runner) — as
    # `fusion-refusee`, a reason that designates a conflict and sends the search where the
    # problem is not.
    result = _git(
        root,
        "-c",
        f"user.name={AUTHOR_NAME}",
        "-c",
        f"user.email={AUTHOR_EMAIL}",
        "merge",
        "--no-ff",
        "--no-edit",
        *messages,
        diff.branch,
    )
    if result.returncode != 0:
        _git(root, "merge", "--abort")
        raise ApplyRejected(
            "fusion-refusee",
            f"Fusion de {diff.branch} vers {diff.base} refusée par Git "
            f"({_git_message(result)}) — la branche conserve le travail.",
        )
    return tuple(m.path for m in diff.changes)


def _merge_message(diff: ProjectDiff) -> str:
    """The message of the merge commit — the approved diff, summed up in one line."""
    return (
        f"Maestro : application de {diff.branch} "
        f"({diff.files} fichier(s), +{diff.additions} / −{diff.deletions})"
    )


def commit_pending(
    workspace: Path, branch: str, *, message: str = "", since: str = ""
) -> None:
    """Commit on `branch` what the worktree still carries, if it carries anything.

    `message`, when given, is the commit's message — a **new** commit, never an
    amendment: it is Maestro committing its own work (a tooling piece, #1507), under
    a message drafted in the project's convention.

    **Into the agent's last commit when this run made it** (#1507). The agent commits
    its work itself, in the project's convention (its task message tells it so,
    `maestro.sandbox.projet.CONSIGNE_COPIE_DE_TRAVAIL`): what it leaves afterwards is
    a remainder of **its** work, and it goes into its last commit, message included —
    `git commit --amend --no-edit`. An extra Maestro commit would take the deliverable
    out of the convention the person chose. `since` is the head of the branch **when
    the worktree was mounted**: only a commit made since, and carried by no other
    reference, is amended (`_amendable`). Everything else gets a new commit, under
    Maestro's message (`save_commit_message`) — without `since`, for lack of knowing
    what the run did; and the **save** of an interrupted work
    (`maestro.sandbox.projet.sauver_le_travail_en_vol`), which is the head at the
    mount of the run that resumes. A saved commit, once rewritten, leaves the history,
    and the “saved” work would no longer be the one the project delivers. **A commit
    this run did not make is never rewritten.**

    The repository's hooks are **not** bypassed: they are the user's, and a
    `pre-commit` that rejects an agent's work is exactly right to do so — the
    rejection comes back reasoned rather than forcing through. The identity is
    set with `-c` so as to write nothing into the project's configuration.

    Public, with two callers (#705): the merge, and the **end of life of the
    worktree**, which runs it before unmounting. “The worktree is removed, never the
    branch — it carries the work up to the merge” (`maestro.sandbox.projet`) holds
    only if the work is committed: without this commit, `--force` would take the
    uncommitted work away and the branch would carry nothing.

    One spelling for one gesture: two implementations of “commit what the worktree
    still carries” would end up diverging, and the wrong one would leave an agent's
    work outside the branch being merged. Each caller keeps, however, its own
    **failure policy** — the merge rejects with a reason, the end of life of the
    worktree abstains silently (it lives in a `finally`, where raising would mask
    the task's real cause).
    """
    if _git(workspace, "add", "-A").returncode != 0:
        raise ApplyRejected(
            "commit-refuse", f"Git n'a pas pu indexer le travail de {branch} dans {workspace}."
        )
    if _git(workspace, "diff", "--cached", "--quiet").returncode == 0:
        return  # nothing pending: everything is already committed on the branch
    if message.strip():
        form = ["-m", message.strip()]
    elif _amendable(workspace, branch, since):
        form = ["--amend", "--no-edit"]
    else:
        form = ["-m", save_commit_message(branch)]
    result = _git(
        workspace,
        "-c",
        f"user.name={AUTHOR_NAME}",
        "-c",
        f"user.email={AUTHOR_EMAIL}",
        "commit",
        *form,
    )
    if result.returncode != 0:
        raise ApplyRejected(
            "commit-refuse",
            f"Commit du travail sur {branch} refusé : {_git_message(result)}",
        )


def save_commit_message(branch: str) -> str:
    """The message of a commit Maestro makes of a task's work — remainder or save.

    One spelling, because it is **read back** (#1507): the branch state that a
    delivery's verifier reads marks these commits as Maestro's, to keep as they are
    (`maestro.sandbox.projet.etat_de_la_branche`). It is our own text, compared
    verbatim — never a pattern searched for in someone else's message.
    """
    return f"Maestro : travail de la tâche ({branch})"


def _amendable(workspace: Path, branch: str, since: str) -> bool:
    """Is the head of `workspace` a commit of this run, and of this run only?

    Of this run: the branch carries commits beyond `since`, its head at mount
    time. Of this run only: no other reference contains the head
    (`_commit_only_on_branch`). False without `since`, or when Git cannot answer.
    """
    if not since.strip():
        return False
    count = _git(workspace, "rev-list", "--count", f"{since}..HEAD")
    if count.returncode != 0 or not count.stdout.strip().isdigit():
        return False
    return int(count.stdout.strip()) > 0 and _commit_only_on_branch(workspace, branch)


def _commit_only_on_branch(workspace: Path, branch: str) -> bool:
    """Is the last commit of `workspace` carried **only** by `branch`?

    True when no other reference of the repository — the base branch, the branch
    of a task that a resplit takes over (#1396), a remote branch, a tag — contains
    this commit: amending it rewrites nothing someone else carries. Without a commit
    of its own, the head of the branch is the one it left from, and another
    reference contains it. False also when Git cannot answer: in doubt, one commit
    too many is better than one amendment too many.
    """
    result = _git(workspace, "for-each-ref", "--contains", "HEAD", "--format=%(refname)")
    if result.returncode != 0:
        return False
    holders = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return holders == {f"refs/heads/{branch}"}


def commit_to_base(project: Project, relative: str, content: str, *, message: str) -> Path:
    """Write a **Maestro** file into a versioned project and commit it on its base (#1530).

    The run's acceptance (`maestro.engine.recette`) is written into the project, and
    in a versioned project a file left uncommitted would make the root “occupied”:
    the next merge would be rejected (`_require_root_available`), so the next run
    would write nothing more. The file is therefore **committed**, alone, on the
    base branch — a commit that `git revert` undoes, like a merge.

    Same requirements as the merge, and for the same reason: the root is **clean
    and on its base**, otherwise nothing is written — one does not commit under
    someone's feet. The path is resolved under the root (EF-38). The repository's
    hooks are not bypassed (`commit_pending`): a rejection comes back reasoned, and
    what was written is undone — the root is returned as it was found.

    Does not decide that writing is allowed: the caller holds that from the run's
    write agreement (#706). Raises `ApplyRejected` or `RootRejected`.
    """
    root = validate_root(project.root)
    _require_root_available(root, _base(project))
    target = path_in_root(root, relative)
    previous = target.read_bytes() if target.is_file() else None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ApplyRejected(
            "ecriture-refusee", f"Écriture impossible dans le projet : {relative} — {exc}"
        ) from exc
    staged = _git(root, "add", "--", relative)
    commit = (
        _git(
            root,
            "-c",
            f"user.name={AUTHOR_NAME}",
            "-c",
            f"user.email={AUTHOR_EMAIL}",
            "commit",
            "-m",
            message,
            "--",
            relative,
        )
        if staged.returncode == 0
        else staged
    )
    if commit.returncode != 0:
        # Return the root as it was found: neither index nor Maestro file.
        _git(root, "reset", "-q", "--", relative)
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)
        raise ApplyRejected(
            "commit-refuse", f"Commit de {relative} refusé : {_git_message(commit)}"
        )
    return target


def _require_root_available(root: Path, base: str) -> None:
    """Reject when the root is not clean, or not on the base branch."""
    head = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    current = head.stdout.strip() if head.returncode == 0 else ""
    if current != base:
        raise ApplyRejected(
            "racine-occupee",
            f"Le projet est sur {current or 'HEAD détachée'}, pas sur sa branche "
            f"de travail {base!r} — placez-vous dessus avant d'appliquer.",
        )
    state = _git(root, "status", "--porcelain")
    if state.returncode != 0 or state.stdout.strip():
        raise ApplyRejected(
            "racine-occupee",
            f"Le projet a des changements en cours dans {root} — committez-les "
            "ou mettez-les de côté avant d'appliquer.",
        )


#: The three outcomes of an apply request (see `ApplyResult`): the diff was empty
#: (nobody was disturbed), the human said yes, the human said no. A refusal **is not
#: an error** — it is an answer, which is why it returns a result where a path outside
#: the perimeter raises.
APPLY_NOT_NEEDED = "sans_objet"
APPLY_APPROVED = "approuvee"
APPLY_DECLINED = "refusee"


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of an apply request: what was decided, and what was written.

    `status` is `APPLY_NOT_NEEDED` (empty diff — nobody was disturbed),
    `APPLY_APPROVED` or `APPLY_DECLINED`. `applied` is empty in the last two cases
    unless approved: on refusal, **nothing is written** and the work stays available
    (task branch kept, or copy intact).
    """

    status: str
    diff: ProjectDiff
    detail: str = ""
    applied: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved(self) -> bool:
        """Was the apply allowed and done?"""
        return self.status == APPLY_APPROVED

    def to_dict(self) -> dict[str, Any]:
        """Re-emit the result as a JSON-serialisable dict."""
        return {
            "statut": self.status,
            "diff": self.diff.to_dict(),
            "detail": self.detail,
            "appliques": list(self.applied),
        }
