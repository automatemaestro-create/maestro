"""Store of the declared projects: one JSON file per project (ticket #221, EF-35).

Same pattern as the other configuration stores (`maestro.agents.store`,
`maestro.agents.capacity`): one `<id>.json` file per project under `core/projets/` (or
`MAESTRO_PROJETS_DIR`), written atomically, re-read on every access. The contract does not
depend on files: a database (table `PROJECT`, docs/03) can take their place without changing
it.

The store is the only entry point that **writes** a project, by design: it is the one that
calls the root validation (EF-38) and the VCS detection, so that no stored project carries a
non-canonical root, a root outside the allowed perimeter, or an invented `vcs`. A project
written by hand into the file remains possible — nothing guards writes to the disk — but
everything that goes through the code goes through `create`/`write`.

Only two methods touch the user's folder, and both because they were asked to:
`create(origin="nouveau")` creates the missing root (need A of docs/24 §2.1), and
`enable_versioning` puts it under Git (#704). Reads never create anything — `detect_vcs`
observes Git without imposing it (EF-38), which is what makes the second gesture explicit.

One writer at a time (the Control Tower API, #223): like its neighbours, the store carries no
concurrency lock.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from maestro.config import Settings, load_settings
from maestro.emplacements import depot_configuration
from maestro.projects.model import (
    ID_PROJET,
    ORIGINS,
    Perimeter,
    Project,
    ToolingDecision,
    new_id,
)
from maestro.projects.root import canonical, detect_vcs, validate_root
from maestro.projects.versioning import init_repository

#: Number of identifier draws before giving up. A collision on 8 hex digits is already
#: unlikely; ten in a row would signal a broken store, not bad luck — better to raise than
#: to loop.
_MAX_ID_DRAWS = 10


class ProjectStore:
    """Store of projects, on files (`<store root>/<id>.json`).

    ⚠ Two "roots" meet here, and they have nothing in common: `self.root` is the folder **of
    the store** (where the JSON files are), `Project.root` is the folder **of the user's
    project** on their disk.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The root of the store (one JSON file per project) — not the root of a project."""
        return self._root

    @classmethod
    def default(cls, settings: Settings | None = None) -> ProjectStore:
        """The configured store: `MAESTRO_PROJETS_DIR`, otherwise `projets/` of the configuration.

        The configuration is the `core/` of a clone, the workstation state of an installation
        (`maestro.emplacements.racine_configuration`, #1620): a reinstallation loses no
        declared project.
        """
        settings = settings or load_settings()
        if settings.projets_dir:
            return cls(Path(settings.projets_dir))
        return cls(depot_configuration("projets"))

    def ids(self) -> tuple[str, ...]:
        """The stored identifiers, sorted — the listing order, deterministic."""
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if ID_PROJET.match(path.stem)
            )
        )

    def all(self) -> tuple[Project, ...]:
        """The stored projects, in identifier order."""
        return tuple(
            project for id_ in self.ids() if (project := self.read(id_)) is not None
        )

    def read(self, id_: str) -> Project | None:
        """The project `id_`, or None if it is not in the store."""
        path = self._path(id_)
        if not path.is_file():
            return None
        project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        # The file name is authoritative for the identifier, as for agents: a project copied
        # under another id stays addressed by its file.
        return replace(project, id=id_)

    def by_root(self, root: Path | str) -> Project | None:
        """The project declared on this root, or None — two projects never share one.

        The comparison is made on **canonicalised** `Path`s (`canonical`), hence regardless
        of case under Windows and without being fooled by two spellings of the same folder:
        `D:/Projets/App`, `d:/projets/app` and `\\\\?\\D:\\projets\\app` are the same project.

        An unreadable file of the store is **skipped** rather than failing the question asked —
        "is this root taken?": a corrupted JSON occupies no root, and raising here would
        forbid creating any project while it lies around.
        """
        try:
            target = canonical(root)
        except OSError:  # pragma: no cover - path unreadable for the OS
            return None
        for id_ in self.ids():
            try:
                project = self.read(id_)
            except (ValueError, OSError):
                continue
            if project is not None and canonical(project.root) == target:
                return project
        return None

    def create(
        self,
        name: str,
        root: Path | str,
        *,
        origin: str = "existant",
        included: tuple[str, ...] | None = None,
        excluded: tuple[str, ...] | None = None,
    ) -> Project:
        """Declares a project: root validated, VCS detected, identifier generated.

        This is the nominal path of a declaration (the one the API serves, #223). The root is
        **canonicalised and checked against the forbidden roots** (EF-38): a refusal raises
        `RootRejected`, with its reason, and nothing is written. `origin="nouveau"`
        **creates** the folder when it is missing (need A of docs/24 §2.1);
        `origin="existant"` requires it to be there. The `vcs` is never asked of the caller:
        it is **observed** on the disk, and is `None` for an unversioned folder — which stays
        declarable.
        """
        if origin not in ORIGINS:
            raise ValueError(
                f"origine invalide : {origin!r} ({' ou '.join(sorted(ORIGINS))} attendu)."
            )
        path = validate_root(root, create=origin == "nouveau")
        perimeter = Perimeter(
            included=included if included is not None else Perimeter().included,
            excluded=excluded if excluded is not None else Perimeter().excluded,
        )
        project = Project(
            id=self._free_id(),
            name=name,
            root=path.as_posix(),
            origin=origin,
            vcs=detect_vcs(path),
            perimeter=perimeter,
        )
        return self.write(project)

    def write(self, project: Project) -> Project:
        """Persists `project` (creation or full replacement) and returns it dated.

        Atomic write (temporary file, then rename): a project appears in the store only when
        complete. Raises `RootRejected` if the root is no longer admissible and `ValueError`
        if the project is invalid (id not a slug, empty name, unknown origin, root already
        taken by another project) — the store never holds an unusable project.
        """
        clean = self._validated(project)
        existing = self.read(clean.id)
        now = _now()
        clean = replace(
            clean,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(clean.id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(clean.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return clean

    def enable_versioning(self, id_: str, *, branch: str = "") -> Project:
        """Puts the project `id_` under Git if it is not, and records its `Vcs` (#704).

        The **explicit** gesture that `detect_vcs` forbids itself: nothing else in the code
        initialises a repository in someone's folder. The root is validated again, then
        initialised by `maestro.projects.versioning`, and `detect_vcs` observes the result —
        the stored `Vcs` is the disk's, never a `Vcs` made up here.

        A project **already versioned** is returned as is: no Git command, no write, not even
        a refreshed `updated_at`. A project declared unversioned whose root has gone under Git
        in the meantime is **caught up** without any repository being created — the
        declaration lagged behind, not the disk.

        Raises `RootRejected` if the root is no longer admissible (EF-38),
        `VersioningRejected` with its reason if the initialisation fails — the root then
        staying in its previous state — and `ValueError` if the project is not in the store.
        """
        project = self.read(id_)
        if project is None:
            raise ValueError(f"projet inconnu : {id_!r} — rien à mettre sous Git.")
        if project.versioned:
            return project
        return self.write(replace(project, vcs=init_repository(project.root, branch=branch)))

    def defer_tooling(self, id_: str) -> Project:
        """Records the "later" of the tooling step (#1034, docs/37 §4.6).

        The only verb that touches `Project.tooling`, and it only sets a date there. It writes
        **nothing** into the user's folder: deferring is precisely not writing there.

        **Idempotent, and the first date wins**: called again on a project already deferred,
        it returns the record as is — no second write, no refreshed `updated_at`. That is what
        lets the screen offer it without fearing a double click, and it is the same trade-off
        as `enable_versioning`.

        Raises `ValueError` if the project is not in the store.
        """
        project = self.read(id_)
        if project is None:
            raise ValueError(f"projet inconnu : {id_!r} — rien à reporter.")
        if project.tooling.deferred:
            return project
        return self.write(replace(project, tooling=ToolingDecision(deferred_at=_now())))

    def resume_tooling(self, id_: str) -> Project:
        """Lifts the "later": the tooling is resumed, in the conversation (#1161).

        The counterpart of `defer_tooling`. Resuming the tooling means no longer putting it
        off: the record stops recalling it (badge and gesture of the Projects screen), and a
        new "later" sets it again. Without it, the project card would say « Outillage
        reporté » while its question waits in the column.

        Idempotent: a project that was not deferred is returned as is, without a write.
        Raises `ValueError` if the project is not in the store.
        """
        project = self.read(id_)
        if project is None:
            raise ValueError(f"projet inconnu : {id_!r} — rien à reprendre.")
        if not project.tooling.deferred:
            return project
        return self.write(replace(project, tooling=ToolingDecision()))

    def delete(self, id_: str) -> bool:
        """Removes the project `id_` from the store; False if it was not there (nothing to do).

        **Never** touches the project's folder on the disk: forgetting a project is not
        deleting the user's work.
        """
        path = self._path(id_)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def file(self, id_: str) -> Path:
        """The file that holds the declaration `id_` — to **show** it, never to write it.

        The workstation cleanup (#1477) names a declaration by its file; it removes it through
        `delete`, like everyone else.
        """
        return self._path(id_)

    def _validated(self, project: Project) -> Project:
        """The normalised project (canonicalised root), or `ValueError` if invalid."""
        if not ID_PROJET.match(project.id):
            raise ValueError(
                f"identifiant de projet invalide : {project.id!r} (slug [a-z0-9_-] attendu)."
            )
        if not project.name.strip():
            raise ValueError(f"nom vide pour le projet {project.id!r}.")
        if project.origin not in ORIGINS:
            raise ValueError(
                f"origine invalide : {project.origin!r} "
                f"({' ou '.join(sorted(ORIGINS))} attendu)."
            )
        path = validate_root(project.root)
        occupant = self.by_root(path)
        if occupant is not None and occupant.id != project.id:
            raise ValueError(
                f"racine déjà déclarée par le projet {occupant.id!r} "
                f"({occupant.name!r}) : {path}."
            )
        return replace(project, name=project.name.strip(), root=path.as_posix())

    def _free_id(self) -> str:
        """A generated identifier that overwrites no project of the store."""
        for _ in range(_MAX_ID_DRAWS):
            candidate = new_id()
            if not self._path(candidate).exists():
                return candidate
        raise RuntimeError(  # pragma: no cover - broken store, not bad luck
            f"impossible d'engendrer un identifiant de projet libre en "
            f"{_MAX_ID_DRAWS} tirages."
        )

    def _path(self, id_: str) -> Path:
        """The storage file of `id_`, identifier validated (never an arbitrary path)."""
        if not ID_PROJET.match(id_):
            raise ValueError(
                f"identifiant de projet invalide : {id_!r} (slug [a-z0-9_-] attendu)."
            )
        return self._root / f"{id_}.json"


def _now() -> str:
    """The write timestamp of a project (ISO 8601, UTC, to the second)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
