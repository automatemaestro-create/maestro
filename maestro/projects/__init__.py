"""The user's projects: the entity, its validated root and its persistence (#221).

The foundation of the local projects of
[docs/24 §2](../../docs/24-projets-locaux-et-poste-de-travail.md) (parent #219): **a project
has a root on the disk**, declared and validated, and its tasks work *in* it.

    from maestro.projects import ProjectStore

    store = ProjectStore.default()
    project = store.create("Dépensio", "D:/projets/depensio")
    project.vcs         # Vcs(type='git', base_branch='main', remote='git@…') or None
    project.perimeter   # excludes .git, node_modules, .env, **/secrets/** by default

One question per module:

- `maestro.projects.model` — *what is a project?* The `Project` entity (`Vcs`, `Perimeter`,
  `ToolingDecision`) and its serialisation. Inert: it never touches the disk (EF-35);
- `maestro.projects.root` — *can this path be a root, and does this path stay under it?* The
  **boundary**: `validate_root` canonicalises and refuses **with a reason** (drive root, bare
  home folder, `.ssh`, `AppData`, Maestro repository…), `check_forbidden_zone` isolates the
  part that holds for **any** path (the one the folder explorer of #223 reuses),
  `path_in_root` forbids writing above the declared root, and `detect_vcs` observes Git
  without ever imposing it (EF-38);
- `maestro.projects.store` — *which projects are declared?* Persistence, one JSON file per
  project under `projets/` of the configuration (or `MAESTRO_PROJETS_DIR`), on the pattern of
  the other stores;
- `maestro.projects.apply` — *what would a task's work change, and how does it land in the
  project?* (#227, EF-37) The diff (`work_diff`), the check that precedes any write
  (`check_perimeter`) and the write itself (`apply_diff` — merge of the task branch when the
  project is versioned, copy otherwise). It asks no one: human approval is the business of
  `maestro.controltower.validation.appliquer_sous_validation`, which plugs this action into
  the existing approval (EF-08) instead of inventing one;
- `maestro.projects.versioning` — *how does an unversioned project become versioned?* (#704)
  The **only** place in the code base that writes a `.git` into someone's folder, and only
  when asked (`init_repository`, `ProjectStore.enable_versioning`). EF-38 holds: detection
  observes Git without ever imposing it, which is why becoming versioned is a verb of its own;
- `maestro.projects.perimeter` — *which paths does the perimeter remove?* It **applies** to
  the disk the patterns `model` declares (#226);
- `maestro.projects.secrets` — *which values of the project must the redaction hide?* It has
  the redaction (#109) cover the values read in the user's project, not only Maestro's (#226);
- `maestro.projects.settings` — *where is a new project created, and which folders has the
  person allowed?* The workstation's settings for projects (#1022, #1610);
- `maestro.projects.commits` — *what message does a commit Maestro writes itself carry?* The
  model drafts it in the project's convention (#1507).

The rest builds on this package and lives elsewhere: the `projet_id` carried by the task and
the run (#222, `maestro.engine`), the API and the folder explorer (#223,
`maestro.controltower.projets`), the derived workspace — a Git worktree, or the root itself
for an unversioned project (#224, #839, `maestro.sandbox`) —, the Projects screen (#225,
`apps/web`) and the isolated-mode mount (#226, `maestro.sandbox.container`). A versioned
project is **never** written directly (EF-36, D2 revised by #703): nothing here opens its root
to an agent.
"""

from __future__ import annotations

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
    work_diff,
)
from maestro.projects.model import (
    AUTHOR_EMAIL,
    AUTHOR_NAME,
    DEFAULT_EXCLUDED,
    DEFAULT_INCLUDED,
    ID_PREFIX,
    ID_PROJET,
    ORIGINS,
    Perimeter,
    Project,
    ToolingDecision,
    Vcs,
    new_id,
)
from maestro.projects.perimeter import ExcludedPath, excluded_files, exclusions
from maestro.projects.root import (
    RootRejected,
    canonical,
    check_forbidden_zone,
    detect_vcs,
    path_in_root,
    validate_root,
)
from maestro.projects.secrets import register_project_secrets
from maestro.projects.settings import (
    DEFAULT_NAME,
    ProjectSettings,
    ProjectSettingsStore,
    default_directory,
)
from maestro.projects.store import ProjectStore
from maestro.projects.versioning import (
    FIRST_COMMIT_MESSAGE,
    VersioningRejected,
    init_repository,
)

__all__ = [
    "APPLY_APPROVED",
    "APPLY_DECLINED",
    "APPLY_NOT_NEEDED",
    "AUTHOR_EMAIL",
    "AUTHOR_NAME",
    "DEFAULT_EXCLUDED",
    "DEFAULT_INCLUDED",
    "DEFAULT_NAME",
    "FIRST_COMMIT_MESSAGE",
    "ID_PREFIX",
    "ID_PROJET",
    "KIND_ADDED",
    "KIND_DELETED",
    "KIND_MODIFIED",
    "ORIGINS",
    "ApplyRejected",
    "ApplyResult",
    "ExcludedPath",
    "FileChange",
    "Perimeter",
    "Project",
    "ProjectDiff",
    "ProjectSettings",
    "ProjectSettingsStore",
    "ProjectStore",
    "RootRejected",
    "ToolingDecision",
    "Vcs",
    "VersioningRejected",
    "apply_diff",
    "canonical",
    "check_forbidden_zone",
    "check_perimeter",
    "commit_pending",
    "default_directory",
    "detect_vcs",
    "excluded_files",
    "exclusions",
    "init_repository",
    "new_id",
    "path_in_root",
    "register_project_secrets",
    "validate_root",
    "work_diff",
]
