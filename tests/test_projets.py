"""Tests of the Project entity: model, validated root, persistence (ticket #221, EF-35/EF-38).

Foundation of Phase 7 (parent #219). Covers the three acceptance criteria:

① the **model** (`Project`, `Vcs`, `Perimeter`): the fields of docs/24 §2.3, the
   default exclusions (`.git`, `node_modules`, `.env`, `**/secrets/**`), a faithful
   `to_dict`/`from_dict` round trip (a null `vcs` included) and generated ids of
   the form `prj-<hex>`, distinct from one draw to the next;
② **root validation** (EF-38): canonicalization (`..` and symbolic links),
   **reasoned** rejection of forbidden roots — drive root, bare home directory or
   one of its ancestors, `.ssh`/`AppData`, the Maestro repository itself, relative
   path, missing directory, file — and no writing above the declared root
   (`path_in_root`);
③ **VCS detection** and the **store** (`ProjectStore`): a directory with `.git`
   yields its branch and its remote, a directory without `.git` stays declarable
   (`vcs=None`); atomic write and read-back, the file's id prevails, rejection
   without writing anything (non-slug id — no path traversal —, empty name,
   unknown origin, root already declared), `origin="nouveau"` creates the
   directory, and resolution of the configured store (`MAESTRO_PROJETS_DIR`).

No network call, no `git` invoked: the test Git repositories are three
hand-written files, exactly what detection reads.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from maestro.config import Settings
from maestro.projects import (
    DEFAULT_EXCLUDED,
    Perimeter,
    Project,
    ProjectStore,
    RootRejected,
    Vcs,
    check_forbidden_zone,
    detect_vcs,
    new_id,
    path_in_root,
    validate_root,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home directory, outside of which the test directories live.

    Required under Windows: pytest's `tmp_path` lives under
    `C:/Users/<me>/AppData/Local/Temp`, which validation rightly rejects
    (`chemin-sensible`). Without this isolation, every test that declares a
    project in `tmp_path` would be rejected — for a good reason, but not the
    one it measures.
    """
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def store(tmp_path: Path) -> ProjectStore:
    """A project store on a throwaway directory."""
    return ProjectStore(tmp_path / "depot")


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """A plausible project directory on disk (unversioned)."""
    root = tmp_path / "projets" / "depensio"
    root.mkdir(parents=True)
    return root


def _dir_link(link: Path, target: Path) -> None:
    """Creates a directory link, or skips the test if the OS refuses.

    Under Windows, `symlink_to` needs rights an ordinary workstation lacks —
    but a **junction** (`mklink /J`) needs none and behaves the same from
    `resolve()`'s point of view. Without this fallback, the only two tests of
    the "symbolic link" vector (docs/24 §2.5) would never run where we develop.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":  # pragma: no cover - Windows-only fallback
        pytest.skip("liens de dossier indisponibles sur ce poste")
    result = subprocess.run(  # noqa: S603 - fixed arguments, no external content
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not link.exists():  # pragma: no cover - locked-down workstation
        pytest.skip("ni lien symbolique ni jonction disponibles sur ce poste")


def _git_repository(root: Path, *, branch: str = "main", remote: str | None = None) -> Path:
    """Builds a minimal `.git` — what `detect_vcs` actually reads."""
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if remote is not None:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*\n'
    (git / "config").write_text(config, encoding="utf-8")
    return git


# --- ① The model: fields, defaults and serialization -----------------------


def test_perimeter_excludes_secret_hotspots_by_default() -> None:
    perimeter = Perimeter()
    assert perimeter.included == (".",)
    assert perimeter.excluded == DEFAULT_EXCLUDED
    assert set(perimeter.excluded) == {".git", "node_modules", ".env", "**/secrets/**"}


def test_project_serializes_to_the_documented_shape() -> None:
    project = Project(
        id="prj-7f3a1c04",
        name="Dépensio",
        root="D:/projets/depensio",
        origin="existant",
        vcs=Vcs(type="git", base_branch="main", remote="git@exemple:depensio.git"),
    )
    document = project.to_dict()
    assert set(document) == {
        "id",
        "nom",
        "racine",
        "origine",
        "vcs",
        "perimetre",
        # The tooling decision (#1034): what someone answered at the tooling
        # step, and nothing else — the state of the disk is not stored here.
        "outillage",
        "cree_le",
        "modifie_le",
    }
    assert document["outillage"] == {"reporte_le": ""}
    assert document["vcs"] == {
        "type": "git",
        "branche_base": "main",
        "distant": "git@exemple:depensio.git",
    }
    assert document["perimetre"]["exclus"] == list(DEFAULT_EXCLUDED)
    assert Project.from_dict(document) == project


def test_unversioned_project_serializes_a_null_vcs() -> None:
    project = Project(id="prj-1", name="Notes", root="D:/notes", vcs=None)
    assert project.to_dict()["vcs"] is None
    assert project.versioned is False
    assert Project.from_dict(project.to_dict()).vcs is None


def test_explicitly_empty_perimeter_is_respected() -> None:
    # An empty `exclus` is the user's choice, not missing data.
    perimeter = Perimeter.from_dict({"inclus": ["src"], "exclus": []})
    assert perimeter.included == ("src",)
    assert perimeter.excluded == ()


def test_generated_ids_are_prefixed_and_distinct() -> None:
    ids = {new_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(id_.startswith("prj-") for id_ in ids)


# --- ② The root: canonicalization, reasoned bans, bounded writing ----------


def test_root_is_canonicalized_before_storage(project_dir: Path) -> None:
    roundabout = project_dir / "sous" / ".." / "."
    (project_dir / "sous").mkdir()
    assert validate_root(roundabout) == project_dir.resolve()


def test_relative_root_rejected() -> None:
    with pytest.raises(RootRejected) as rejection:
        validate_root("projets/depensio")
    assert rejection.value.reason == "chemin-relatif"


def test_drive_root_rejected(tmp_path: Path) -> None:
    with pytest.raises(RootRejected) as rejection:
        validate_root(Path(tmp_path.resolve().anchor))
    assert rejection.value.reason == "racine-de-disque"


def test_bare_home_directory_rejected(_isolated_home: Path) -> None:
    home = _isolated_home
    with pytest.raises(RootRejected) as rejection:
        validate_root(home)
    assert rejection.value.reason == "dossier-utilisateur-nu"
    # But a project subdirectory passes.
    (home / "code").mkdir()
    assert validate_root(home / "code") == (home / "code").resolve()


def test_ancestor_of_home_directory_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The "C:\\Users" case: neither a drive root nor a bare home directory.
    users = tmp_path / "Users"
    home = users / "sam"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(RootRejected) as rejection:
        validate_root(users)
    assert rejection.value.reason == "au-dessus-du-dossier-utilisateur"


@pytest.mark.parametrize("sensitive", [".ssh", ".gnupg", ".aws", "AppData", "Library"])
def test_sensitive_profile_paths_rejected(_isolated_home: Path, sensitive: str) -> None:
    target = _isolated_home / sensitive / "quelque-part"
    target.mkdir(parents=True)
    with pytest.raises(RootRejected) as rejection:
        validate_root(target)
    assert rejection.value.reason == "chemin-sensible"


def test_symlink_to_a_forbidden_path_rejected(tmp_path: Path, _isolated_home: Path) -> None:
    (_isolated_home / ".ssh").mkdir(parents=True)
    trap = tmp_path / "projet-innocent"
    _dir_link(trap, _isolated_home / ".ssh")
    with pytest.raises(RootRejected) as rejection:
        validate_root(trap)
    assert rejection.value.reason == "chemin-sensible"


@pytest.mark.skipif(os.name != "nt", reason="préfixes de chemin propres à Windows")
def test_windows_prefix_does_not_bypass_forbidden_zones(_isolated_home: Path) -> None:
    # `\\?\C:\...` names the same directory as `C:\...` but does not compare
    # equal to it: letting this spelling through would disable the whole list.
    (_isolated_home / ".ssh").mkdir()
    for spelling in [
        f"\\\\?\\{_isolated_home}\\.ssh",
        f"//?/{_isolated_home}/.ssh",
    ]:
        with pytest.raises(RootRejected) as rejection:
            validate_root(spelling)
        assert rejection.value.reason == "chemin-sensible"


@pytest.mark.skipif(os.name != "nt", reason="partages réseau propres à Windows")
def test_network_share_rejected() -> None:
    with pytest.raises(RootRejected) as rejection:
        validate_root("\\\\localhost\\C$\\projets")
    assert rejection.value.reason == "ancre-non-standard"


def test_maestro_repository_rejected() -> None:
    repository = Path(__file__).resolve().parents[1]
    with pytest.raises(RootRejected) as rejection:
        validate_root(repository)
    assert rejection.value.reason == "depot-maestro"
    with pytest.raises(RootRejected) as rejection:
        validate_root(repository / "maestro")
    assert rejection.value.reason == "depot-maestro"
    with pytest.raises(RootRejected) as rejection:
        validate_root(repository.parent)
    assert rejection.value.reason == "au-dessus-du-depot-maestro"


def test_missing_directory_rejected_unless_creation_requested(tmp_path: Path) -> None:
    new_dir = tmp_path / "projets" / "tout-neuf"
    with pytest.raises(RootRejected) as rejection:
        validate_root(new_dir)
    assert rejection.value.reason == "dossier-absent"
    assert not new_dir.exists()

    assert validate_root(new_dir, create=True) == new_dir.resolve()
    assert new_dir.is_dir()


def test_file_rejected_as_root(tmp_path: Path) -> None:
    file = tmp_path / "notes.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(RootRejected) as rejection:
        validate_root(file)
    assert rejection.value.reason == "pas-un-dossier"


def test_rejection_always_carries_a_reason_and_the_path(tmp_path: Path) -> None:
    with pytest.raises(RootRejected) as rejection:
        validate_root(tmp_path / "absent")
    assert rejection.value.reason
    assert tmp_path.resolve().as_posix() in str(rejection.value)


# --- ②bis A rejection writes its paths one way only, in POSIX (#1614) -------


def test_sensitive_path_rejection_writes_both_paths_in_posix(_isolated_home: Path) -> None:
    """The rejected directory and the protected zone that holds it compare at a glance.

    Under Windows, a formatted `Path` is written with `\\`: the sentence would
    then write the directory and the zone differently from the explorer's field.
    """
    target = _isolated_home / ".ssh" / "cles"
    target.mkdir(parents=True)
    with pytest.raises(RootRejected) as rejection:
        validate_root(target)

    zone = _isolated_home.resolve() / ".ssh"
    assert rejection.value.reason == "chemin-sensible"
    assert str(rejection.value) == (
        f"Chemin sensible refusé : {target.resolve().as_posix()} est dans {zone.as_posix()}."
    )


@pytest.mark.parametrize("source", ["liste", "environnement"])
def test_system_directory_rejection_writes_both_paths_in_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """`C:/Windows` (hard-coded) and `%SystemRoot%` (written with `\\` by Windows): all in `/`.

    The `liste` case reproduces the ticket — « C:\\Windows est dans
    C:/Windows »; the `environnement` case covers a forbidden zone read from
    the environment, which Windows writes with backslashes.
    """
    if source == "liste":
        forbidden = Path("C:/Windows")
    else:
        forbidden = tmp_path / "Windows"
        monkeypatch.setenv("SystemRoot", str(forbidden))
    with pytest.raises(RootRejected) as rejection:
        check_forbidden_zone(forbidden / "System32")

    assert rejection.value.reason == "chemin-systeme"
    assert str(rejection.value) == (
        f"Dossier système refusé : {(forbidden / 'System32').as_posix()} "
        f"est dans {forbidden.as_posix()}."
    )


def _file(path: Path) -> Path:
    path.write_text("x", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("factory", "reason"),
    [
        (lambda home, tmp: Path(tmp.resolve().anchor), "racine-de-disque"),
        (lambda home, tmp: home, "dossier-utilisateur-nu"),
        (lambda home, tmp: tmp, "au-dessus-du-dossier-utilisateur"),
        (lambda home, tmp: tmp / "absent", "dossier-absent"),
        (lambda home, tmp: _file(tmp / "notes.txt"), "pas-un-dossier"),
        (lambda home, tmp: Path(__file__).resolve().parents[1], "depot-maestro"),
        (lambda home, tmp: Path(__file__).resolve().parents[2], "au-dessus-du-depot-maestro"),
    ],
    ids=[
        "racine-de-disque",
        "dossier-utilisateur-nu",
        "au-dessus-du-dossier-utilisateur",
        "dossier-absent",
        "pas-un-dossier",
        "depot-maestro",
        "au-dessus-du-depot-maestro",
    ],
)
def test_every_root_rejection_writes_its_paths_in_posix(
    _isolated_home: Path,
    tmp_path: Path,
    factory,  # noqa: ANN001 - parametrized factory
    reason: str,
) -> None:
    """Every message of `validate_root` writes its paths the way the screen writes them."""
    with pytest.raises(RootRejected) as rejection:
        validate_root(factory(_isolated_home, tmp_path))

    assert rejection.value.reason == reason
    assert "\\" not in str(rejection.value)


def test_outside_root_rejection_writes_its_paths_in_posix(project_dir: Path) -> None:
    with pytest.raises(RootRejected) as rejection:
        path_in_root(project_dir, "../dehors")

    assert rejection.value.reason == "hors-racine"
    assert "\\" not in str(rejection.value)
    assert project_dir.resolve().as_posix() in str(rejection.value)
    assert isinstance(rejection.value, ValueError)


def test_write_above_the_root_rejected(project_dir: Path) -> None:
    assert path_in_root(project_dir, "src/app.py") == (
        project_dir / "src" / "app.py"
    ).resolve()
    for escape in ["../voisin", "../../etc/passwd", str(project_dir.parent)]:
        with pytest.raises(RootRejected) as rejection:
            path_in_root(project_dir, escape)
        assert rejection.value.reason == "hors-racine"


def test_symlink_leaving_the_project_rejected(project_dir: Path) -> None:
    outside = project_dir.parent / "dehors"
    outside.mkdir()
    _dir_link(project_dir / "fuite", outside)
    with pytest.raises(RootRejected) as rejection:
        path_in_root(project_dir, "fuite")
    assert rejection.value.reason == "hors-racine"


# --- ③ VCS detection and the store ------------------------------------------


def test_directory_without_git_stays_declarable(project_dir: Path, store: ProjectStore) -> None:
    assert detect_vcs(project_dir) is None
    project = store.create("Notes", project_dir)
    assert project.vcs is None
    assert project.versioned is False


def test_git_repository_detected_with_branch_and_remote(project_dir: Path) -> None:
    _git_repository(project_dir, branch="develop", remote="git@exemple:depensio.git")
    vcs = detect_vcs(project_dir)
    assert vcs == Vcs(type="git", base_branch="develop", remote="git@exemple:depensio.git")


def test_git_repository_without_remote_stays_a_repository(project_dir: Path) -> None:
    _git_repository(project_dir, remote=None)
    vcs = detect_vcs(project_dir)
    assert vcs is not None
    assert vcs.base_branch == "main"
    assert vcs.remote is None


def test_detached_head_yields_no_branch(project_dir: Path) -> None:
    git = _git_repository(project_dir)
    (git / "HEAD").write_text("9f1c2b3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b\n", encoding="utf-8")
    vcs = detect_vcs(project_dir)
    assert vcs is not None
    assert vcs.base_branch == ""


def test_git_worktree_follows_its_gitdir_file(tmp_path: Path, project_dir: Path) -> None:
    # A worktree carries a `.git` FILE pointing at the common repository.
    main_repository = tmp_path / "principal"
    main_repository.mkdir()
    git = _git_repository(main_repository, remote="git@exemple:depensio.git")
    internal = git / "worktrees" / "wt"
    internal.mkdir(parents=True)
    (internal / "HEAD").write_text("ref: refs/heads/feat/221\n", encoding="utf-8")
    (internal / "commondir").write_text("../..\n", encoding="utf-8")
    (project_dir / ".git").write_text(f"gitdir: {internal}\n", encoding="utf-8")

    vcs = detect_vcs(project_dir)
    assert vcs == Vcs(type="git", base_branch="feat/221", remote="git@exemple:depensio.git")


def test_incomplete_git_is_not_a_repository(project_dir: Path) -> None:
    # A leftover `.git` (aborted clone) would send #224 into trying a Git worktree.
    (project_dir / ".git").mkdir()
    assert detect_vcs(project_dir) is None
    (project_dir / ".git" / "notes.txt").write_text("x", encoding="utf-8")
    assert detect_vcs(project_dir) is None


def test_commented_and_quoted_git_config_yields_a_clean_url(project_dir: Path) -> None:
    git = _git_repository(project_dir)
    (git / "config").write_text(
        '[remote "origin"] ; le remote de prod\n\turl = "git@exemple:depensio.git" # prod\n',
        encoding="utf-8",
    )
    vcs = detect_vcs(project_dir)
    assert vcs is not None
    assert vcs.remote == "git@exemple:depensio.git"


def test_perimeter_read_from_a_string_stays_one_pattern(store: ProjectStore) -> None:
    # A hand-edited `.env` must not burst into characters, or the exclusion it
    # sets would disappear.
    assert Perimeter.from_dict({"exclus": ".env"}).excluded == (".env",)


def test_unreadable_store_does_not_prevent_declaring(
    store: ProjectStore, project_dir: Path
) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "prj-casse.json").write_text("{ pas du json", encoding="utf-8")
    project = store.create("Dépensio", project_dir)
    assert store.by_root(project_dir) == project


def test_creation_persists_and_reads_back_the_project(
    store: ProjectStore, project_dir: Path
) -> None:
    _git_repository(project_dir, remote="git@exemple:depensio.git")
    project = store.create("Dépensio", project_dir)

    assert project.id.startswith("prj-")
    assert project.root == project_dir.resolve().as_posix()
    assert project.created_at and project.updated_at
    assert project.perimeter.excluded == DEFAULT_EXCLUDED

    read_back = store.read(project.id)
    assert read_back == project
    assert store.all() == (project,)
    assert store.by_root(project_dir) == project

    # The stored file is readable UTF-8, not escape sequences.
    raw = (store.root / f"{project.id}.json").read_text(encoding="utf-8")
    assert "Dépensio" in raw
    assert json.loads(raw)["vcs"]["type"] == "git"


def test_origin_nouveau_creates_the_directory(store: ProjectStore, tmp_path: Path) -> None:
    target = tmp_path / "projets" / "tout-neuf"
    project = store.create("Tout neuf", target, origin="nouveau")
    assert target.is_dir()
    assert project.origin == "nouveau"
    assert project.vcs is None


def test_origin_existant_requires_the_directory(store: ProjectStore, tmp_path: Path) -> None:
    with pytest.raises(RootRejected) as rejection:
        store.create("Absent", tmp_path / "nulle-part")
    assert rejection.value.reason == "dossier-absent"
    assert not store.ids()


def test_forbidden_root_rejected_without_writing_anything(
    store: ProjectStore, tmp_path: Path
) -> None:
    with pytest.raises(RootRejected):
        store.create("Le disque entier", Path(tmp_path.resolve().anchor))
    assert not store.root.exists() or not list(store.root.iterdir())


def test_two_projects_do_not_share_a_root(
    store: ProjectStore, project_dir: Path
) -> None:
    store.create("Dépensio", project_dir)
    with pytest.raises(ValueError, match="déjà déclarée"):
        store.create("Dépensio (bis)", project_dir)
    assert len(store.ids()) == 1


def test_rewriting_the_same_project_keeps_its_creation_date(
    store: ProjectStore, project_dir: Path
) -> None:
    project = store.create("Dépensio", project_dir)
    renamed = store.write(replace(project, name="Dépensio v2"))
    assert renamed.name == "Dépensio v2"
    assert renamed.created_at == project.created_at
    assert len(store.ids()) == 1


def test_non_slug_id_rejected_without_path_traversal(store: ProjectStore) -> None:
    for bad in ["../evasion", "prj/../../etc", "PRJ-MAJ", ""]:
        with pytest.raises(ValueError, match="identifiant de projet invalide"):
            store.read(bad)
    assert not store.root.exists()


def test_invalid_project_rejected(store: ProjectStore, project_dir: Path) -> None:
    with pytest.raises(ValueError, match="nom vide"):
        store.write(Project(id="prj-1", name="   ", root=str(project_dir)))
    with pytest.raises(ValueError, match="origine invalide"):
        store.write(
            Project(id="prj-1", name="X", root=str(project_dir), origin="importe")
        )
    with pytest.raises(ValueError, match="origine invalide"):
        store.create("X", project_dir, origin="importe")
    assert not store.ids()


def test_the_file_name_prevails_over_the_id(
    store: ProjectStore, project_dir: Path
) -> None:
    project = store.create("Dépensio", project_dir)
    path = store.root / f"{project.id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["id"] = "prj-menteur"
    path.write_text(json.dumps(document), encoding="utf-8")
    read_back = store.read(project.id)
    assert read_back is not None
    assert read_back.id == project.id


def test_deletion_forgets_the_project_without_touching_the_disk(
    store: ProjectStore, project_dir: Path
) -> None:
    project = store.create("Dépensio", project_dir)
    assert store.delete(project.id) is True
    assert store.delete(project.id) is False
    assert store.read(project.id) is None
    # The user's directory is untouched.
    assert project_dir.is_dir()


def test_missing_store_lists_without_complaint(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "jamais-cree")
    assert store.ids() == ()
    assert store.all() == ()
    assert store.read("prj-inconnu") is None
    assert store.by_root(tmp_path) is None


def test_the_configured_store_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "ailleurs"))
    assert ProjectStore.default(Settings.from_env()).root == tmp_path / "ailleurs"

    monkeypatch.delenv("MAESTRO_PROJETS_DIR", raising=False)
    default_root = ProjectStore.default(Settings.from_env()).root
    assert default_root.name == "projets"
    assert default_root.parent.name == "core"
