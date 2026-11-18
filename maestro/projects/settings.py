"""The **projects directory**: where a new project is born (#1022).

A setting **of the workstation**, not of a project: it says nothing about a
declared project, only in which folder the next one will be created. The
declaration form uses it to **pre-fill** the parent folder of a new project; the
user can choose another one for that project without touching the setting, and
importing an **existing** project never sees it.

⚠ **Two "roots" cross in the repository, and they have nothing in common.**
`MAESTRO_PROJETS_DIR` (`maestro.projects.store`) is the store of the
**declarations** — one `<id>.json` per project, what Maestro knows about them.
`MAESTRO_REGLAGES_DIR`, here, is the store of the **workstation's settings**,
including this `directory`: *where* a new project will be created on disk. Hence
two distinct folders, rather than a `reglages.json` slipped among the declarations
— which `ProjectStore.all()` would read as one more unreadable project.

## The default, and why it has a name

`~/Maestro` — a **named** subfolder of the home folder, never the bare home
folder, which `validate_root` refuses (`dossier-utilisateur-nu`) and which is not
a place to keep projects anyway. It is also the choice made by the comparable
products captured by the design survey of #1022 — `~/IdeaProjects` for IntelliJ,
`Documents/GitHub` for GitHub Desktop.

## "Created on first use"

The folder does not necessarily exist: the setting is an **intention**, and
creating it when the API starts would put a folder on the disk of someone who
will never open the projects screen. `resolve(create=True)` creates it, and it is
`GET /api/projets/repertoire` that asks for it — the first time one asks *where a
new project is born* is exactly its first use, and returning a path that the
explorer would then refuse (`dossier-absent`) would be worse than creating it.
The response **says** it created it rather than doing so silently.

## Allowed folders (#1610)

The second setting of the same file: the folders that the user has **allowed from
the screen** because the explorer refused them, outside the explorable folders.
They **add to** the boundary (`MAESTRO_EXPLORATEUR_RACINES` or the default)
without replacing it, apply from the next call — no restart — and are removed in
*Paramètres › Projets*.

Everything goes through `validate_root` (EF-38): there is no second validation
gate, here no more than elsewhere (docs/24 §2.5). A folder is allowed only if
`validate_root` would accept it as a project root: an allowance therefore opens no
forbidden zone, and the service judges it again on every read.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.emplacements import NOM_PROJETS, depot_configuration, racine_projets
from maestro.projects.root import RootRejected, validate_root

#: The name of the folder proposed under the home folder — named, never bare.
DEFAULT_NAME = NOM_PROJETS

#: The single file of the settings store (one object, not one per entity).
_FILE = "projets.json"


def default_directory() -> Path:
    """`~/Maestro` — the directory proposed when nothing has been set.

    Returned **unresolved and not created**: it is a proposal, which
    `validate_root` judges like any other. Raises `RootRejected`
    (`dossier-utilisateur-introuvable`) if the OS cannot tell where the home
    folder is — the case of containers without `HOME`, where the setting has to be
    set by hand.
    """
    try:
        return racine_projets()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - depends on the environment
        raise RootRejected(
            "dossier-utilisateur-introuvable",
            "Dossier personnel introuvable : réglez le répertoire des projets à la main.",
        ) from exc


@dataclass(frozen=True)
class ProjectSettings:
    """The workstation's settings on the projects side: directory and allowed folders.

    `directory` at `None` means "the default", **not** "none": storing it null
    rather than resolved is what makes it follow a move of the home folder without
    rewriting the file.

    `allowed_folders` carries the folders allowed from the screen (#1610), in POSIX
    and in the order in which they were allowed.
    """

    directory: str | None = None
    allowed_folders: tuple[str, ...] = ()
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Re-emit the settings as a JSON-serializable dict (the stored file)."""
        return {
            "repertoire": self.directory,
            "dossiers_autorises": list(self.allowed_folders),
            "modifie_le": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectSettings:
        """Rebuild the settings from their `to_dict` form.

        An allowed-folder entry that is not a non-empty string is ignored: a file
        damaged by hand does not prevent reading the rest.
        """
        raw = data.get("repertoire")
        directory = raw.strip() if isinstance(raw, str) and raw.strip() else None
        allowed = data.get("dossiers_autorises")
        folders = (
            tuple(d.strip() for d in allowed if isinstance(d, str) and d.strip())
            if isinstance(allowed, list)
            else ()
        )
        updated = data.get("modifie_le")
        return cls(
            directory=directory,
            allowed_folders=folders,
            updated_at=updated if isinstance(updated, str) else "",
        )


class ProjectSettingsStore:
    """Store of the workstation's settings on the projects side (`<root>/projets.json`).

    A **single file** — it is a settings object, not a collection —, written
    atomically (temporary file, then rename). An empty, missing or unreadable store
    returns the default settings: a setting that cannot be read must not prevent
    declaring a project.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The root of the store (a single file in it)."""
        return self._root

    @classmethod
    def default(cls, settings: Settings | None = None) -> ProjectSettingsStore:
        """The configured store: `MAESTRO_REGLAGES_DIR`, else the configuration's `reglages/`.

        The configuration is the `core/` of a clone, the workstation state of an
        installation (`maestro.emplacements.racine_configuration`, #1620).
        """
        settings = settings or load_settings()
        if settings.reglages_dir:
            return cls(Path(settings.reglages_dir))
        return cls(depot_configuration("reglages"))

    def read(self) -> ProjectSettings:
        """The stored settings, or the default ones — never raises."""
        path = self._root / _FILE
        if not path.is_file():
            return ProjectSettings()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ProjectSettings()
        if not isinstance(data, dict):
            return ProjectSettings()
        return ProjectSettings.from_dict(data)

    def write(self, directory: str | Path | None) -> ProjectSettings:
        """Persist the directory (`None` = back to the default), return the dated settings.

        The path is **validated and created** before being stored (`validate_root`,
        `create=True`): a setting that points to a refused folder would only make
        the next declaration fail, later and elsewhere. What is stored is the
        **resolved** path, never the input.
        """
        value: str | None = None
        if directory is not None and str(directory).strip():
            # Stored in **POSIX**, like every root the API returns (docs/05
            # §6.7): otherwise the same folder would be written `C:\…` here and
            # `C:/…` in the explorer, and the screen would no longer know that it
            # is the same one (#1022).
            value = validate_root(directory, create=True).as_posix()
        return self._write(replace(self.read(), directory=value))

    def allow(self, folder: str | Path) -> tuple[ProjectSettings, str]:
        """Add `folder` to the allowed folders; return `(settings, POSIX path)`.

        The folder is validated like a project root (`validate_root`, without
        creation: one allows a folder that exists). Otherwise raises a reasoned
        `RootRejected`, and **nothing is written**. Allowing the same folder twice
        records it only once and does not rewrite the file.
        """
        value = validate_root(folder).as_posix()
        current = self.read()
        if any(_same(value, d) for d in current.allowed_folders):
            return current, value
        added = replace(current, allowed_folders=(*current.allowed_folders, value))
        return self._write(added), value

    def remove(self, folder: str | Path) -> ProjectSettings:
        """Remove `folder` from the allowed folders and return the settings.

        The folder is **not** validated again: an allowance that has become invalid
        (unplugged drive, deleted folder) can be removed too. Raises
        `RootRejected` (`autorisation-inconnue`) when nothing matches, and nothing
        is written.
        """
        target = Path(str(folder).strip()).as_posix()
        current = self.read()
        remaining = tuple(d for d in current.allowed_folders if not _same(target, d))
        if len(remaining) == len(current.allowed_folders):
            raise RootRejected(
                "autorisation-inconnue",
                f"Ce dossier n'est pas autorisé sur ce poste : {target}.",
                target,
            )
        return self._write(replace(current, allowed_folders=remaining))

    def _write(self, project_settings: ProjectSettings) -> ProjectSettings:
        """Write the dated settings, atomically (temporary file, then rename)."""
        stamped = replace(project_settings, updated_at=_now())
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / _FILE
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(stamped.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return stamped

    def resolve(self, *, create: bool = False) -> tuple[Path, bool, bool]:
        """The current directory: `(path, is_default, created)`.

        `is_default` says that no setting has been set and that the path is
        `~/Maestro`. `created` says that **this call** created the folder — it is
        what the API response reports, so that a creation is never silent. Raises a
        reasoned `RootRejected` when the path cannot be a project root (sensitive
        folder, unplugged drive…), and — with `create=False` only — when it does
        not exist yet.
        """
        current = self.read()
        is_default = current.directory is None
        raw = current.directory if not is_default else default_directory()
        assert raw is not None  # `default_directory` raises rather than returning None
        existed = Path(raw).expanduser().is_dir()
        resolved = validate_root(raw, create=create)
        return resolved, is_default, create and not existed


def _same(a: str, b: str) -> bool:
    """Do two paths designate the same folder? — case does not count under Windows."""
    return os.path.normcase(Path(a).as_posix()) == os.path.normcase(Path(b).as_posix())


def _now() -> str:
    """The UTC ISO-8601 timestamp of the settings (same form as the other stores)."""
    return datetime.now(UTC).isoformat(timespec="seconds")
