"""Tests of the **projects directory**: where a new project is born (#1022, EF-38).

The ticket's three criteria, in this order:

① the **setting** (`maestro.projects.settings`): a named default `~/Maestro` — which
   `validate_root` accepts where it rejects the bare home folder —, created on first
   use and **said** when it is, stored resolved, back to the default through `None`,
   and a missing or corrupt store that returns the defaults rather than raising;
② **reading and writing through the API** (`GET`/`PUT /api/projets/repertoire`):
   a rejected setting writes nothing, a directory that can no longer be declared comes
   back as 200 with its reason, and the set directory becomes explorable — a proposed
   parent that the explorer would refuse would be a dead end;
③ what the setting **does not do**: it does not enter the declarations store
   (`MAESTRO_PROJETS_DIR`), and declaring a project does not touch it.

No network, no Redis: the FastAPI app runs on the in-memory bus through the TestClient,
on throwaway stores and a fake home folder — without which pytest's `tmp_path`, which
lives under `AppData/Local/Temp` on Windows, would be rejected rightly but not for the
reason under test (same isolation as in #221).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.config import Settings
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.projets import ServiceProjets
from maestro.projects import ProjectStore, RootRejected
from maestro.projects.settings import (
    DEFAULT_NAME,
    ProjectSettingsStore,
    default_directory,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home folder — same reason as in #221 and #223."""
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def home(_isolated_home: Path) -> Path:
    """The fake home folder, under a readable name as a test argument."""
    return _isolated_home


@pytest.fixture()
def settings_store(tmp_path: Path) -> ProjectSettingsStore:
    """A settings store on a throwaway folder."""
    return ProjectSettingsStore(tmp_path / "reglages")


@pytest.fixture()
def workshop(tmp_path: Path) -> Path:
    """An explorable folder, outside the home folder."""
    workshop = tmp_path / "atelier"
    workshop.mkdir()
    return workshop


@pytest.fixture()
def service(tmp_path: Path, settings_store: ProjectSettingsStore) -> ServiceProjets:
    """The service without any exploration restriction — the nominal configuration."""
    return ServiceProjets(ProjectStore(tmp_path / "depot"), reglages=settings_store)


@pytest.fixture()
def client(service: ServiceProjets) -> TestClient:
    """The app's TestClient, wired to that service."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=service,
    )
    with TestClient(app) as client:
        yield client


# --- ① The setting: a named default, created on first use --------------------


def test_the_default_is_a_named_subfolder_of_the_home_folder(home: Path) -> None:
    """`~/Maestro`, never `~`: the bare home folder is rejected by EF-38."""
    assert default_directory() == home / DEFAULT_NAME
    # The counter-test that gives the previous one its meaning: the bare home folder
    # itself would not pass — that is the nuance the default rests on.
    with pytest.raises(RootRejected) as rejected:
        ProjectSettingsStore(home / "vide").write(home)
    assert rejected.value.reason == "dossier-utilisateur-nu"


def test_without_a_setting_the_directory_is_the_default_and_does_not_exist_yet(
    settings_store: ProjectSettingsStore, home: Path
) -> None:
    """Reading the setting creates nothing: `create=True` does, and it alone."""
    with pytest.raises(RootRejected) as rejected:
        settings_store.resolve()
    assert rejected.value.reason == "dossier-absent"
    assert not (home / DEFAULT_NAME).exists()


def test_the_first_use_creates_the_folder_and_says_so(
    settings_store: ProjectSettingsStore, home: Path
) -> None:
    """`created` tells « I just created it » from « it was there » — never silently."""
    path, by_default, created = settings_store.resolve(create=True)
    assert path == (home / DEFAULT_NAME).resolve()
    assert (by_default, created) == (True, True)
    # Idempotent, and the second time no longer claims to have created it.
    _, _, again = settings_store.resolve(create=True)
    assert again is False


def test_a_set_directory_is_stored_resolved(
    settings_store: ProjectSettingsStore, workshop: Path
) -> None:
    """The **resolved** path is what is stored, never the input (EF-38)."""
    target = workshop / "ailleurs"
    target.mkdir()
    written = settings_store.write(workshop / "ailleurs" / "." / ".." / "ailleurs")
    assert written.directory == target.resolve().as_posix()
    assert written.updated_at != ""
    path, by_default, _ = settings_store.resolve()
    assert path == target.resolve()
    assert by_default is False


def test_setting_creates_the_missing_folder(
    settings_store: ProjectSettingsStore, workshop: Path
) -> None:
    """A setting once set is always a declarable folder, never an intention."""
    target = workshop / "a-creer"
    settings_store.write(target)
    assert target.is_dir()


def test_going_back_to_the_default_clears_the_setting(
    settings_store: ProjectSettingsStore, workshop: Path, home: Path
) -> None:
    """`None` does not mean « no directory » but « the one Maestro proposes »."""
    (workshop / "ailleurs").mkdir()
    settings_store.write(workshop / "ailleurs")
    settings_store.write(None)
    path, by_default, _ = settings_store.resolve(create=True)
    assert (path, by_default) == ((home / DEFAULT_NAME).resolve(), True)


def test_a_rejected_setting_writes_nothing(
    settings_store: ProjectSettingsStore, workshop: Path, home: Path
) -> None:
    """The previous setting stays in place: rejecting is not resetting."""
    (workshop / "bon").mkdir()
    settings_store.write(workshop / "bon")
    with pytest.raises(RootRejected):
        settings_store.write(home)  # bare home folder
    assert settings_store.read().directory == (workshop / "bon").resolve().as_posix()


def test_a_corrupt_store_returns_the_defaults(
    settings_store: ProjectSettingsStore, home: Path
) -> None:
    """A setting that cannot be read does not prevent declaring a project."""
    settings_store.root.mkdir(parents=True, exist_ok=True)
    (settings_store.root / "projets.json").write_text("{ pas du json", encoding="utf-8")
    assert settings_store.read().directory is None
    path, by_default, _ = settings_store.resolve(create=True)
    assert (path, by_default) == ((home / DEFAULT_NAME).resolve(), True)


def test_the_settings_store_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAESTRO_REGLAGES_DIR`, else the repository's `core/reglages/` — never `core/projets/`."""
    monkeypatch.setenv("MAESTRO_REGLAGES_DIR", str(tmp_path / "ailleurs"))
    assert ProjectSettingsStore.default(Settings.from_env()).root == tmp_path / "ailleurs"

    monkeypatch.delenv("MAESTRO_REGLAGES_DIR", raising=False)
    default = ProjectSettingsStore.default(Settings.from_env()).root
    assert default.name == "reglages"
    assert default.parent.name == "core"


# --- ② The API: read, set, and stay explorable --------------------------------


def test_reading_the_directory_returns_the_default_and_creates_it(
    client: TestClient, home: Path
) -> None:
    """Reading creates the folder — « created on first use » — and says so."""
    body = client.get("/api/projets/repertoire").json()
    assert body["par_defaut"] is True
    assert body["cree"] is True
    assert body["refus"] is None
    assert Path(body["chemin"]) == (home / DEFAULT_NAME).resolve()
    assert (home / DEFAULT_NAME).is_dir()
    # The second call no longer claims to have created it: `cree` is a fact, not a state.
    assert client.get("/api/projets/repertoire").json()["cree"] is False


def test_setting_a_directory_reads_it_back(client: TestClient, workshop: Path) -> None:
    """`PUT` then `GET` say the same thing — a single holder of the truth."""
    target = workshop / "mes-projets"
    body = client.put("/api/projets/repertoire", json={"chemin": str(target)}).json()
    assert body["par_defaut"] is False
    assert Path(body["chemin"]) == target.resolve()
    assert client.get("/api/projets/repertoire").json()["chemin"] == body["chemin"]


def test_the_path_is_returned_in_posix_like_the_other_roots(
    client: TestClient, workshop: Path
) -> None:
    """One folder, one spelling — otherwise the screen can no longer recognize them.

    The explorer returns its paths in POSIX (`_fiche_dossier`), and the screen
    **compares** the chosen folder with the set directory to know whether it is
    « outside » it. Two spellings of the same folder would make it announce the
    opposite of what it shows (#1022).
    """
    target = workshop / "mes-projets"
    stored = client.put("/api/projets/repertoire", json={"chemin": str(target)}).json()
    assert stored["chemin"] == target.resolve().as_posix()
    assert "\\" not in client.get("/api/projets/repertoire").json()["chemin"]
    # And the default follows the same rule: `~/Maestro` does not escape the form.
    default = client.put("/api/projets/repertoire", json={"chemin": None}).json()
    assert "\\" not in default["chemin"]
    # The same folder, listed by the explorer, is spelled exactly the same.
    view = client.get("/api/projets/explorateur", params={"chemin": str(target)}).json()
    assert view["chemin"] == stored["chemin"]


def test_setting_null_goes_back_to_the_default(
    client: TestClient, workshop: Path, home: Path
) -> None:
    """`chemin: null` goes back to the default — it does not mean « no directory »."""
    (workshop / "mes-projets").mkdir()
    client.put("/api/projets/repertoire", json={"chemin": str(workshop / "mes-projets")})
    body = client.put("/api/projets/repertoire", json={"chemin": None}).json()
    assert body["par_defaut"] is True
    assert Path(body["chemin"]) == (home / DEFAULT_NAME).resolve()


def test_a_rejected_directory_returns_a_reason_and_changes_nothing(
    client: TestClient, workshop: Path, home: Path
) -> None:
    """The EF-38 rejection crosses the API with its reason, and the previous setting holds."""
    (workshop / "bon").mkdir()
    client.put("/api/projets/repertoire", json={"chemin": str(workshop / "bon")})
    response = client.put("/api/projets/repertoire", json={"chemin": str(home)})
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "dossier-utilisateur-nu"
    assert (
        Path(client.get("/api/projets/repertoire").json()["chemin"])
        == (workshop / "bon").resolve()
    )


def test_a_directory_that_became_unavailable_returns_200_with_its_reason(
    client: TestClient, workshop: Path
) -> None:
    """An unplugged disk does not prevent declaring a project elsewhere."""
    target = workshop / "sur-un-disque"
    client.put("/api/projets/repertoire", json={"chemin": str(target)})
    target.rmdir()
    target.write_text("plus un dossier", encoding="utf-8")
    body = client.get("/api/projets/repertoire").json()
    assert body["existe"] is False
    assert body["refus"]["motif"] == "pas-un-dossier"
    # The set path is still returned, and in its usual form: the person fixes what
    # they see, and must recognize it.
    assert body["chemin"] == target.resolve().as_posix()


def test_the_set_directory_is_an_entry_point_of_the_explorer(
    client: TestClient, workshop: Path
) -> None:
    """The first place to look is the one you come from (#1022)."""
    target = workshop / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(target)})
    folders = client.get("/api/projets/explorateur").json()["dossiers"]
    by_origin = {d["origine"]: d["chemin"] for d in folders}
    assert by_origin["repertoire"] == target.resolve().as_posix()
    assert folders[0]["origine"] == "repertoire"


def test_the_set_directory_stays_explorable_despite_a_restriction(
    tmp_path: Path, settings_store: ProjectSettingsStore, workshop: Path
) -> None:
    """A prefilled parent that the explorer would refuse to open would be a dead end.

    `MAESTRO_EXPLORATEUR_RACINES` stays a **restriction**: it does not widen by
    itself. But the projects directory is a folder the screen **proposes** — and
    proposing a folder that will be refused on click is worse than proposing
    nothing.
    """
    outside_workshop = tmp_path / "hors-atelier"
    outside_workshop.mkdir()
    settings_store.write(outside_workshop)
    service = ServiceProjets(
        ProjectStore(tmp_path / "depot"),
        racines_exploration=(workshop,),
        reglages=settings_store,
    )
    view = service.explorer(str(outside_workshop))
    assert view["chemin"] == outside_workshop.resolve().as_posix()


def test_a_missing_directory_is_not_an_entry_point(
    client: TestClient, workshop: Path
) -> None:
    """An entry point that would refuse on click would be worse than its absence."""
    target = workshop / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(target)})
    target.rmdir()
    origins = {
        d["origine"] for d in client.get("/api/projets/explorateur").json()["dossiers"]
    }
    assert "repertoire" not in origins


# --- ③ What the setting does not do -------------------------------------------


def test_the_setting_does_not_mix_with_the_declarations(
    client: TestClient, service: ServiceProjets, workshop: Path
) -> None:
    """Two « roots » meet here, and they do not share a folder.

    The declarations store (`MAESTRO_PROJETS_DIR`) holds only `<id>.json` files:
    a settings `projets.json` slipped into it would be read back as one more
    unreadable project.
    """
    target = workshop / "mes-projets"
    client.put("/api/projets/repertoire", json={"chemin": str(target)})
    declarations = service.store.root
    assert declarations != service.reglages.root
    assert not (declarations / "projets.json").exists()
    assert client.get("/api/projets").json() == []


def test_declaring_a_project_does_not_touch_the_setting(
    client: TestClient, settings_store: ProjectSettingsStore, workshop: Path
) -> None:
    """Choosing another parent for **this** project sets nothing (criterion 2)."""
    client.put("/api/projets/repertoire", json={"chemin": str(workshop / "mes-projets")})
    before = json.loads((settings_store.root / "projets.json").read_text(encoding="utf-8"))
    elsewhere = workshop / "ailleurs"
    elsewhere.mkdir()
    response = client.post(
        "/api/projets",
        json={"nom": "Dépensio", "racine": str(elsewhere / "depensio"), "origine": "nouveau"},
    )
    assert response.status_code == 201
    after = json.loads((settings_store.root / "projets.json").read_text(encoding="utf-8"))
    assert after == before
