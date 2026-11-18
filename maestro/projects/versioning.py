"""Turn an unversioned root into a Git repository — **on request** (ticket #704).

The prerequisite of the parent #703. Merging a task's branch into the base branch once the
task is settled assumes a **versioned** project: the branch is what holds the work between the
task's workspace and the root. A project declared unversioned (`Project.vcs is None`) does not
take that path: it fills up **in place**, in its root (`maestro.sandbox.en_place`, #839), since
a disposable copy of the perimeter has nothing to hold the work and loses the deliverable with
it (#568). This module is what lets such a project **change regime**: once versioned, its next
tasks take the branch, the merge and the approval.

It gives such a project the means to **become** versioned. It does not change the rule, it
opens a door beside it:

- `detect_vcs` **observes Git without ever imposing it** (EF-38) and does not move by one
  character. Nothing that walks through a project — declaration (`ProjectStore.create`),
  re-reading, derivation of the workspace (#224), application of the work (#227) —
  initialises anything;
- this module is the **only** place in the repository that writes a `.git` into someone's
  folder, and it is called only by a verb invoked on purpose (`init_repository`,
  `ProjectStore.enable_versioning`). *The gesture is explicit or it does not happen.*

**This is not a bare `git init`, and that is the whole point.** A freshly born repository has
an **unborn** `HEAD`: `.git/HEAD` does point to a branch — so `detect_vcs` returns a `Vcs` with
its `base_branch` — but `refs/heads/<base>` does not resolve. In that state, nothing the parent
aims at works: `maestro.sandbox.projet` cannot branch a task's worktree off the base (Git
switches to `--orphan` and the agent inherits an **empty** workspace, without the project it is
meant to see), and `maestro.projects.apply` rejects the diff with `base-introuvable`. The goal
is for the `Vcs` to be filled in "as if it had always been": the root is therefore **recorded
in a first commit**, which gives the base branch a real reference and makes the project as it
stands today the starting line of the tasks to come.

Three choices to know before touching it:

1. **we never initialise over what exists.** A root already versioned is returned as is,
   `detect_vcs` being authoritative — including when the declared project believes it is
   unversioned (someone may have run `git init` by hand since): it is then a mere catch-up of
   the declaration, and no command is run. And a root **contained in another repository** is
   rejected (`depot-englobant`): placing a nested repository there would modify the repository
   above, which would see a submodule link appear where it had files — precisely the previous
   state we commit to leaving untouched;
2. **the first commit takes everything the root holds** (`git add -A`, hence what the project's
   `.gitignore` lets through, and nothing more). Neither the project's perimeter nor a
   `.gitignore` written for the occasion takes its place: the perimeter says what an **agent**
   sees, not what the user versions, and leaving files out of the index would make `git status`
   dirty forever — and `maestro.projects.apply` refuses to merge into a root that is not clean
   (`racine-occupee`). An empty project gives an empty commit (`--allow-empty`): the base branch
   must exist even when there is nothing to record yet;
3. **a failure leaves the project exactly in its previous state.** If the commit fails after
   the initialisation, the `.git` just born is removed and the refusal comes back with its
   reason. Removed **only if it did not exist before the call**: a leftover `.git` found in
   place (aborted clone) belongs to the user, not to us.

The repository's hooks are not bypassed, same rule and same reason as `maestro.projects.apply`:
they are the user's, and a `pre-commit` that refuses has every right to do so — the refusal
comes back with its reason rather than forcing through.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from maestro.fichiers import retirer_arbre
from maestro.projects.model import AUTHOR_EMAIL, AUTHOR_NAME, Vcs
from maestro.projects.root import canonical, detect_vcs, validate_root

#: Maximum duration of the **plumbing** Git commands (`init`, `rev-parse`) — same anomaly bound
#: as the derived workspace (#224) and the application of the work (#227): these are local
#: operations of a few tens of milliseconds, and a minute says "something is wrong" rather than
#: "wait".
_GIT_TIMEOUT_S = 60

#: Maximum duration of the first commit. It is the **only** command of the module whose cost is
#: proportional to the project rather than constant: it indexes then records everything the
#: root holds, `node_modules` included when nothing ignores it. A minute there would be a
#: manufactured failure on a perfectly healthy project.
_COMMIT_TIMEOUT_S = 300

#: The message of the first commit. Fixed and without interpolation: it is the marker read in
#: `git log` to know where the history of a project put under Git by Maestro comes from.
FIRST_COMMIT_MESSAGE = "Maestro : état initial du projet"


class VersioningRejected(RuntimeError):
    """Putting the root under Git did not happen — **with its reason** (#704).

    Same choice as `RootRejected` (#221), `EspaceProjetIndisponible` (#224) and
    `ApplyRejected` (#227): the refusal carries a short, stable code that the API and the UI
    can display or translate (`depot-englobant`, `init-refuse`, `commit-refuse`,
    `vcs-introuvable`, `git-indisponible`), the message remaining the readable sentence.

    A refusal is **total**: when it is raised, the root is in the state it was in before the
    call.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def init_repository(root: Path | str, *, branch: str = "") -> Vcs:
    """Makes `root` a Git repository if it is not one, and returns its `Vcs`.

    The root is **validated again** (`validate_root`, EF-38) rather than taken as is: the
    project store is a folder of JSON files that can be edited by hand, and this is the last
    door before writing into someone's folder.

    Returns the `Vcs` **observed** by `detect_vcs` — never a made-up `Vcs`: the base branch
    returned is the one Git actually set. A root already versioned is returned as is, without
    any command being run.

    `branch` imposes the name of the initial branch (`git init --initial-branch`); empty — the
    nominal case — lets Git answer according to the user's configuration
    (`init.defaultBranch`), which is not ours to decide in their place.

    Raises `RootRejected` if the root is no longer admissible and `VersioningRejected` with its
    reason if the initialisation fails, the root then staying in the state it was in.
    """
    path = validate_root(root)
    existing = detect_vcs(path)
    if existing is not None:
        return existing

    _require_outside_repository(path)
    # Measured **before** the first command: this is what tells the `.git` we create — hence
    # the one we may remove — from one found in place.
    git_absent_before = not (path / ".git").exists()
    try:
        _init(path, branch)
        _first_commit(path)
        vcs = detect_vcs(path)
        if vcs is None:  # pragma: no cover - Git returned 0 without setting up a repository
            raise VersioningRejected(
                "vcs-introuvable",
                f"Dépôt introuvable après initialisation de {path} — rien n'a "
                "été enregistré dans la déclaration du projet.",
            )
        return vcs
    except VersioningRejected:
        if git_absent_before:
            _remove_new_repository(path / ".git")
        raise


def _remove_new_repository(git_dir: Path) -> None:
    """Removes the `.git` this call has just created — **read-only** objects included.

    `shutil.rmtree(ignore_errors=True)` is not enough, and the defect only shows under Windows
    (#707): Git writes its objects read-only, and `os.remove` there refuses a file without
    write permission where POSIX only looks at the folder. The `.git` would then survive
    **truncated** — `objects/` alone, without `HEAD` —, that is exactly the "leftover found in
    place" that the next call forbids itself to remove: one failure would leave a second,
    permanent one.

    The gesture itself lives in `maestro.fichiers.retirer_arbre`, shared with the disposable
    workspaces (#992), since two spellings of the same remedy end up diverging. What stays here
    is what belongs to this module — *which* `.git` is removed, and why.
    """
    retirer_arbre(git_dir)


def _require_outside_repository(path: Path) -> None:
    """Rejects a root **contained in another Git repository** (`depot-englobant`).

    `detect_vcs` only looks at `<root>/.git`, so a subfolder of a repository strictly answers
    "unversioned" to it. Initialising a nested repository there would nonetheless be a
    modification of the repository **above** — which would see its files replaced by a
    submodule link —, and the user only asked to touch their project.
    """
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return  # outside any repository: the nominal case
    raw = result.stdout.strip()
    if not raw:
        return
    try:
        enclosing = canonical(raw)
    except OSError:  # pragma: no cover - path unreadable for the OS
        return
    if enclosing == path:
        return
    raise VersioningRejected(
        "depot-englobant",
        f"{path} est déjà dans le dépôt Git {enclosing} — un dépôt imbriqué "
        "modifierait celui-ci. Déclarez le projet sur la racine du dépôt, ou "
        "sortez le dossier de son arborescence.",
    )


def _init(path: Path, branch: str) -> None:
    """`git init` in `path`, the initial branch imposed only when it is asked for."""
    arguments = ["init"]
    if branch:
        arguments += ["--initial-branch", branch]
    result = _git(path, *arguments)
    if result.returncode != 0:
        raise VersioningRejected(
            "init-refuse",
            f"Initialisation Git refusée dans {path} : {_git_message(result)}",
        )


def _first_commit(path: Path) -> None:
    """Records the root as it is, so that the base branch exists.

    `--allow-empty` because a still empty project must also have a resolvable base branch:
    without a commit, `HEAD` names a branch that does not exist, and everything that relies on
    it (task worktree, diff, merge) fails while naming the wrong cause.
    """
    if _git(path, "add", "-A", timeout=_COMMIT_TIMEOUT_S).returncode != 0:
        raise VersioningRejected(
            "commit-refuse", f"Git n'a pas pu indexer le contenu de {path}."
        )
    result = _git(
        path,
        "-c",
        f"user.name={AUTHOR_NAME}",
        "-c",
        f"user.email={AUTHOR_EMAIL}",
        "commit",
        "--allow-empty",
        "-m",
        FIRST_COMMIT_MESSAGE,
        timeout=_COMMIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise VersioningRejected(
            "commit-refuse",
            f"Premier commit refusé dans {path} : {_git_message(result)}",
        )


def _git(
    cwd: Path, *arguments: str, timeout: int = _GIT_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Runs `git <arguments>` in `cwd` and returns the completed process.

    Raises **only** for what is not a verdict of Git (missing binary, timeout exceeded): a
    non-zero return code is an answer, which the caller translates into a refusal with its
    reason — that is what lets `_require_outside_repository` ask a question without catching
    an exception.
    """
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VersioningRejected(
            "git-indisponible", f"Git indisponible pour {cwd} : {exc}"
        ) from exc


def _git_message(result: subprocess.CompletedProcess[str]) -> str:
    """Git's error message, on one line (stderr, otherwise stdout)."""
    raw = (result.stderr or result.stdout or "").strip()
    return " ".join(raw.split()) or f"code de retour {result.returncode}"
