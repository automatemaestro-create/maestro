"""The Project entity: its stored form and its defaults (#221, EF-35).

It is the `PROJECT` entity of [docs/03](../../docs/03-modele-de-donnees.md), with the fields
that **tie it to the disk** ([docs/24 §2.3](../../docs/24-projets-locaux-et-poste-de-travail.md)):

- `root` — the absolute path of the project on the workstation, the **single boundary** of what
  agents can read and write (validation lives in `maestro.projects.root`, EF-38);
- `origin` — `nouveau` (a folder being started) or `existant` (a project being taken over);
- `vcs` — the version control system **detected** at declaration, `None` when the project is
  not versioned: an unversioned project can still be declared, and VCS is never imposed;
- `perimeter` — what the project exposes (`included`) and what it removes by default
  (`excluded`: the Git plumbing, installed dependencies and the two places where secrets live,
  see `DEFAULT_EXCLUDED`).

The model is **inert**: it describes and serialises, it never touches the disk. What confronts
it with the disk lives elsewhere — `maestro.projects.root` (validate, detect) and
`maestro.projects.store` (persist). That is what makes it testable without a real project, and
reusable as is by the projects API (#223).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.appartenance import ID_PROJET as ID_PROJET  # explicit re-export (#222)

#: The exclusions set by default on a project's perimeter (docs/24 §2.3): the Git plumbing,
#: installed dependencies, and above all the two places where secrets live in a user's
#: repository — the `.env` and everything under a `secrets/` folder (docs/24 §2.5, "leak of
#: the project's secrets").
DEFAULT_EXCLUDED: tuple[str, ...] = (".git", "node_modules", ".env", "**/secrets/**")

#: The default inclusion: the whole root. Perimeter patterns are **relative to the root** —
#: never absolute paths.
DEFAULT_INCLUDED: tuple[str, ...] = (".",)

#: The two origins of a project (docs/24 §2.1): a new folder being started (need A), or an
#: existing project being taken over (need B).
ORIGINS: frozenset[str] = frozenset({"nouveau", "existant"})

#: The identity carried by **every** commit Maestro writes in the user's repository: the first
#: commit of putting a root under Git (`maestro.projects.versioning`, #704), the commit that
#: gathers uncommitted work and the merge commit (`maestro.projects.apply`, #227). Someone's
#: repository has no reason to carry a Git identity valid for an agent, and a missing
#: `user.email` would make the write fail at the very last step (#333). It is always set
#: through `-c`, so never written into the project's configuration.
#:
#: It lives **here**, with the inert constants, and not in either of the two modules that use
#: it: two spellings of the same identity would end up diverging, and the author of a history
#: would then read under two names.
AUTHOR_NAME = "Maestro"
AUTHOR_EMAIL = "maestro@localhost"

#: The same identity for the commits **the agent** makes itself (#1507): it commits its work
#: in the project's convention, and without it its `git commit` would fail on a workstation
#: without a Git identity — that of a person who does not code. It is set in the environment of
#: its session, never in the project's configuration: the agent need neither know it nor write
#: it, and the history names with a single name what Maestro and its agents did.
GIT_IDENTITY_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": AUTHOR_NAME,
    "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
    "GIT_COMMITTER_NAME": AUTHOR_NAME,
    "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
}

#: A project id that can serve as a file name — the same lock as the other stores
#: (`maestro.agents.store`): a safe slug, without separator or dot, which forbids any path
#: traversal from an id coming from the API. **Defined** in `maestro.appartenance` (#222) and
#: only re-exported here (see the import at the top): the same contract validates what the
#: journal and the events carry. It is also served from here, as
#: `from maestro.projects.model import ID_PROJET`.

#: Prefix of generated ids (docs/24 §2.3 gives `prj-7f3a` as an example). The entropy comes
#: from a truncated `uuid4`, like run ids (`maestro.controltower.executions`) — 8 hex rather
#: than 4, since a collision here **overwrites a project** rather than duplicating a journal
#: entry.
ID_PREFIX = "prj-"


def new_id() -> str:
    """A new project id, of the form `prj-<8 hex>` (docs/24 §2.3)."""
    return f"{ID_PREFIX}{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Vcs:
    """The version control system of a project — **detected**, never imposed.

    `base_branch` is the branch the project sits on at declaration: the per-task branches and
    worktrees start from it (#224, EF-36). Empty when the repository is on a detached HEAD.
    `remote` is the URL of the `origin` remote, `None` when there is none (a purely local
    repository).
    """

    type: str = "git"
    base_branch: str = ""
    remote: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The VCS as a JSON-serialisable dict (the `vcs` fragment of the file)."""
        return {
            "type": self.type,
            "branche_base": self.base_branch,
            "distant": self.remote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vcs:
        """Rebuilds a VCS from its `to_dict` form."""
        return cls(
            type=data.get("type", "git"),
            base_branch=data.get("branche_base", "") or "",
            remote=data.get("distant") or None,
        )


@dataclass(frozen=True)
class Perimeter:
    """What a project exposes to agents: included patterns, excluded patterns.

    Patterns are **relative to the root** (glob style). `excluded` wins over `included` — it is
    the rule expected of a perimeter, and it is what guarantees that the default exclusions
    (`DEFAULT_EXCLUDED`) hold even under an `included` as wide as `.`. This class **declares**
    the patterns and stores them; `maestro.projects.perimeter` applies them to the disk.
    """

    included: tuple[str, ...] = DEFAULT_INCLUDED
    excluded: tuple[str, ...] = DEFAULT_EXCLUDED

    def to_dict(self) -> dict[str, Any]:
        """The perimeter as a JSON-serialisable dict (lists, not tuples)."""
        return {"inclus": list(self.included), "exclus": list(self.excluded)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Perimeter:
        """Rebuilds a perimeter from its `to_dict` form.

        An absent `inclus`/`exclus` falls back to the default; an **explicitly empty**
        `exclus` is respected (it is the user's choice, not missing data).
        """
        return cls(
            included=_patterns(data.get("inclus"), DEFAULT_INCLUDED),
            excluded=_patterns(data.get("exclus"), DEFAULT_EXCLUDED),
        )


def _patterns(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    """The patterns of a perimeter read back from the file, whoever wrote it.

    An **absent** `exclus` falls back to the default; explicitly **empty**, it is respected (it
    is a choice). A **string** counts as a single pattern: a hand-edited file carries
    `"exclus": ".env"` far more often than one would think, and splitting it into characters
    would make the exclusion disappear without a word.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(pattern) for pattern in value)


@dataclass(frozen=True)
class ToolingDecision:
    """What the user has **decided** about their project's tooling (#1034, docs/37 §4.6).

    A single field, on purpose: `deferred_at`, the timestamp of the moment someone answered
    "later" at the tooling step. Empty as long as the question has not been set aside.

    ⚠ **This is not the state of the tooling, it is a decision.** Whether a project *is* tooled
    is read on the disk — `.maestro/outillage/manifeste.json` (docs/38 §4.1) —, never here: the
    manifest can appear without going through this screen (#1033, a relaunched generation), be
    removed by hand, or come from a clone. A "tooled" boolean stored here would diverge from the
    folder at the first of these three cases, and the folder is right. The record the API
    serves **crosses** both (`maestro.controltower.projets`): *a project whose tooling was
    deferred says so, as long as it is not tooled*.
    """

    deferred_at: str = ""

    @property
    def deferred(self) -> bool:
        """Has the question been set aside with a "later"?"""
        return self.deferred_at != ""

    def to_dict(self) -> dict[str, Any]:
        """The decision as a JSON-serialisable dict (the `outillage` fragment)."""
        return {"reporte_le": self.deferred_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolingDecision:
        """Rebuilds the decision from its `to_dict` form."""
        return cls(deferred_at=data.get("reporte_le", "") or "")


@dataclass(frozen=True)
class Project:
    """A user's project: a root on the disk and its perimeter (EF-35).

    `root` is stored as an **absolute POSIX path** (`D:/projets/depensio`, as in docs/24
    §2.3): it is the same string on the three OSes, it goes through JSON and the API without
    escaping, and `root_path` hands it to the code as a `Path`. The store sets it
    **canonicalised** — never as typed (EF-38).

    `created_at`/`updated_at` are set by the store on write (ISO 8601, UTC, docs/03: a
    timestamp on every main table).
    """

    id: str
    name: str
    root: str
    origin: str = "existant"
    vcs: Vcs | None = None
    perimeter: Perimeter = field(default_factory=Perimeter)
    tooling: ToolingDecision = field(default_factory=ToolingDecision)
    created_at: str = ""
    updated_at: str = ""

    @property
    def root_path(self) -> Path:
        """The root as a `Path` — the path the engine and the sandbox use."""
        return Path(self.root)

    @property
    def versioned(self) -> bool:
        """Is the project under version control? (decides the write pattern, #224)."""
        return self.vcs is not None

    def to_dict(self) -> dict[str, Any]:
        """The project as a JSON-serialisable dict (the stored file)."""
        return {
            "id": self.id,
            "nom": self.name,
            "racine": self.root,
            "origine": self.origin,
            "vcs": self.vcs.to_dict() if self.vcs is not None else None,
            "perimetre": self.perimeter.to_dict(),
            "outillage": self.tooling.to_dict(),
            "cree_le": self.created_at,
            "modifie_le": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        """Rebuilds a project from its `to_dict` form (the stored file).

        A file without an `outillage` fragment falls back to the default (never deferred,
        #1034), which is exactly what it means: no migration to run.
        """
        vcs = data.get("vcs")
        perimeter = data.get("perimetre")
        tooling = data.get("outillage")
        return cls(
            id=data["id"],
            name=data.get("nom", ""),
            root=data.get("racine", ""),
            origin=data.get("origine", "existant"),
            vcs=Vcs.from_dict(vcs) if vcs else None,
            perimeter=Perimeter.from_dict(perimeter) if perimeter else Perimeter(),
            tooling=ToolingDecision.from_dict(tooling) if tooling else ToolingDecision(),
            created_at=data.get("cree_le", ""),
            updated_at=data.get("modifie_le", ""),
        )
