"""The ingestion preview (#319): what sources would give, without committing anything.

Two levels, and the second is the one that counts for the screen: the module
`maestro.sources.preview` (what a preview reads, rejects and **erases**) and the
route `POST /api/sources/apercu` (the shape of the contract, docs/05 §6.9).

The rest of Phase 8's coverage is deferred to the final lot #323 (checklist of
#314); this file keeps only what a screen cannot catch up on -- a preview that
would leave material behind, or that would reject what it should merely signal.

No network: `url` sources go through the `fetch_url` injection of #316, as
`tests/conftest.py` requires (#195).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.emplacements import NOM_JETABLE, VARIABLES_TEMP, racine_jetable
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import SourceRejected, ingestion_root
from maestro.sources.preview import preview_sources


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake user home -- same reason as in #221/#223.

    On Windows, pytest's `tmp_path` lives in `AppData/Local/Temp`, which
    `valider_racine` rejects for good reason: without this isolation, a
    references directory declared in `tmp_path` would be rejected for a good
    reason, but not the one these tests measure.
    """
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def client() -> TestClient:
    """The app's TestClient -- the preview depends on no injected service."""
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState())
    with TestClient(app) as client:
        yield client


def _declare(name: str, content: bytes) -> dict[str, object]:
    """The declaration of a deposited file, as the screen sends it."""
    return {"type": "fichier", "nom": name, "taille": len(content)}


# --- The module: read, reject, erase ----------------------------------------


def test_a_deposited_file_is_read_and_costed() -> None:
    """The nominal case: the content enters, and its cost is announced."""
    content = "Cahier des charges\n\nRefondre l'écran de lancement.".encode()
    report = preview_sources(
        [_declare("cdc.md", content)],
        files=[("cdc.md", io.BytesIO(content))],
    )
    (reading,) = report.readings
    assert reading.name == "cdc.md"
    assert reading.state == "lu"
    assert reading.tokens > 0
    assert report.tokens == reading.tokens


def test_a_preview_leaves_nothing_behind(tmp_path: Path) -> None:
    """The module's promise: the bytes read go away with the disposable directory.

    Measured on disk and not on an intention: neither in the runs' ingestion
    location, nor elsewhere -- looking twice must not fill a disk for a gesture
    reputed free.
    """
    content = b"une matiere quelconque"
    before = sorted(ingestion_root().glob("*")) if ingestion_root().exists() else []
    report = preview_sources(
        [_declare("note.txt", content)], files=[("note.txt", io.BytesIO(content))]
    )
    assert report.readings[0].state == "lu"
    after = sorted(ingestion_root().glob("*")) if ingestion_root().exists() else []
    assert after == before


def test_an_image_is_announced_for_the_launch_never_rejected() -> None:
    """Having nothing to read is not refusing to read (criterion 3).

    A deposited `.png` must make no preview fail: it comes out as a **line of the
    report**, with its reason. Since #1163 this reason says the launch will show
    it to the model -- the free preview does not pay for that look in advance.
    """
    content = b"\x89PNG\r\n\x1a\n binaire"
    report = preview_sources(
        [_declare("maquette.png", content)],
        files=[("maquette.png", io.BytesIO(content))],
    )
    (reading,) = report.readings
    assert reading.state == "ignore"
    assert reading.reason == "vue-au-lancement"
    assert reading.tokens == 0


def test_an_out_of_bounds_source_is_rejected_with_its_index() -> None:
    """The resolution's regime: reject, saying **which one** to fix."""
    small = b"ok"
    too_big = b"x" * 4096
    with pytest.raises(SourceRejected) as rejection:
        preview_sources(
            [_declare("a.txt", small), _declare("b.txt", too_big)],
            files=[("a.txt", io.BytesIO(small)), ("b.txt", io.BytesIO(too_big))],
            guardrails=GardeFousIngestion(taille_max_source_octets=1024),
        )
    assert rejection.value.reason == "source-trop-volumineuse"
    assert rejection.value.index == 1


def test_the_received_bytes_are_capped_and_not_the_announced_size() -> None:
    """The size comes from the browser: it is what **arrives** that is capped.

    An honest declaration accompanied by a bigger stream must not pass: the
    rejection happens during the write, chunk by chunk, as at the reception of
    an upload (#317).
    """
    with pytest.raises(SourceRejected) as rejection:
        preview_sources(
            [{"type": "fichier", "nom": "menteur.txt", "taille": 10}],
            files=[("menteur.txt", io.BytesIO(b"y" * 4096))],
            guardrails=GardeFousIngestion(taille_max_source_octets=1024),
        )
    assert rejection.value.reason == "source-trop-volumineuse"


def test_a_wrong_file_count_is_rejected() -> None:
    """Two ordered lists and no correspondence: mispairing would be worse.

    Associating the wrong document with the wrong name would produce a credible
    and false report -- the rejection is therefore preferred to a random pairing.
    """
    with pytest.raises(SourceRejected) as rejection:
        preview_sources([_declare("a.txt", b"ok")], files=[])
    assert rejection.value.reason == "apercu-sans-octets"


def test_a_references_directory_returns_one_line_per_file(home: Path) -> None:
    """The directory is a source, its files are its `entries` (#316)."""
    references = home / "references"
    references.mkdir()
    (references / "specification.md").write_text("# Spec\n\nDeux phrases.", encoding="utf-8")
    (references / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    report = preview_sources([{"type": "dossier", "chemin": str(references)}])

    (reading,) = report.readings
    assert reading.type == "dossier"
    assert {entry.name for entry in reading.entries} == {"specification.md", "logo.png"}
    assert {entry.state for entry in reading.entries} == {"lu", "ignore"}


@pytest.fixture()
def home(_isolated_home: Path) -> Path:
    """The fake user home, under a readable name as a test argument."""
    return _isolated_home


# --- The route: the shape of the contract (docs/05 §6.9) --------------------


def test_the_route_returns_the_report_of_the_deposited_file(client: TestClient) -> None:
    """`POST /api/sources/apercu`: multipart in, `ReadingReport` out."""
    response = client.post(
        "/api/sources/apercu",
        data={"sources": json.dumps([_declare("cdc.md", b"# Objectif\n\nRefondre.")])},
        files=[("fichier", ("cdc.md", b"# Objectif\n\nRefondre.", "text/markdown"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tokens"] > 0
    assert [reading["nom"] for reading in body["lectures"]] == ["cdc.md"]
    assert body["lectures"][0]["etat"] == "lu"


def test_the_route_returns_an_empty_report_without_a_source(client: TestClient) -> None:
    """No source: an empty report, not an error -- the screen opens on it."""
    response = client.post("/api/sources/apercu", data={"sources": "[]"})
    assert response.status_code == 200
    assert response.json() == {"tokens": 0, "lectures": []}


def test_the_route_rejects_an_unfollowable_url_with_its_reason(client: TestClient) -> None:
    """A rejection is reasoned and located: `{motif, message, index}`, never a bare sentence."""
    response = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [
                    {"type": "url", "valeur": "https://exemple.test/spec"},
                    {"type": "url", "valeur": "file:///etc/passwd"},
                ]
            )
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["motif"] == "url-non-suivable"
    assert detail["index"] == 1


def test_the_route_rejects_an_unknown_type(client: TestClient) -> None:
    """A type outside `fichier`/`dossier`/`url` is rejected, never ignored (#315)."""
    response = client.post(
        "/api/sources/apercu",
        data={"sources": json.dumps([{"type": "presse-papier", "valeur": "…"}])},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "type-inconnu"


def test_the_route_rejects_unreadable_json(client: TestClient) -> None:
    """The body is an input like any other: it rejects with a reason."""
    response = client.post("/api/sources/apercu", data={"sources": "{pas du json"})
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "sources-illisibles"


# --- The rest, deferred to the final lot (#323) -----------------------------


def test_a_url_is_previewed_without_ever_going_out_on_the_network() -> None:
    """The third source type was exercised nowhere on the preview side.

    The `fetch_url=` injection is the only path through which a URL enters a
    preview: without a test, nothing guaranteed that the parameter is passed on
    down to the extraction.
    """
    called: list[str] = []

    def _fetch(url: str) -> str:
        called.append(url)
        return "# Spécification\n\nLe contenu de la page."

    report = preview_sources(
        [{"type": "url", "valeur": "https://exemple.test/spec"}],
        fetch_url=_fetch,
    )
    (reading,) = report.readings
    assert called == ["https://exemple.test/spec"]
    assert reading.state == "lu"
    assert "Spécification" in reading.markdown


def test_the_total_of_the_received_bytes_is_capped_too() -> None:
    """Two lawful files one after the other can be unlawful together.

    The **per-source** cap was covered, the **total** cap was not -- yet it is
    the one that bounds the cost of a preview.
    """
    half = b"z" * 700
    with pytest.raises(SourceRejected) as rejection:
        preview_sources(
            [_declare("a.txt", half), _declare("b.txt", half)],
            files=[("a.txt", io.BytesIO(half)), ("b.txt", io.BytesIO(half))],
            guardrails=GardeFousIngestion(
                taille_max_source_octets=1024, taille_max_totale_octets=1024
            ),
        )
    assert rejection.value.reason == "ingestion-trop-volumineuse"
    assert rejection.value.index == 1


def test_the_total_is_counted_on_the_received_bytes_and_not_the_announced_ones() -> None:
    """The counterpart of the previous test, on the only path that really counts.

    There, the **declared** sizes already exceeded: the rejection fell before a
    single byte was read. Here they are tiny and the bytes overflow -- the case
    of a client announcing twelve bytes for twelve megabytes, and the only one
    where the total cap must defend itself **during** the write. The announced
    size comes from the browser: it cannot be what the ingestion budget relies
    on.
    """
    half = b"z" * 700
    with pytest.raises(SourceRejected) as rejection:
        preview_sources(
            [
                {"type": "fichier", "nom": "a.txt", "taille": 10},
                {"type": "fichier", "nom": "b.txt", "taille": 10},
            ],
            files=[("a.txt", io.BytesIO(half)), ("b.txt", io.BytesIO(half))],
            guardrails=GardeFousIngestion(
                taille_max_source_octets=1024, taille_max_totale_octets=1024
            ),
        )
    assert rejection.value.reason == "ingestion-trop-volumineuse"
    assert rejection.value.index == 1


def test_a_rejected_preview_does_not_leave_its_bytes_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cleanup is in a `finally`: it must hold on the failure path too.

    It is the case that counts most -- a rejection writes a **partial** file, and
    that is precisely what must not be kept.

    The temporary directory is moved through the environment, not through
    `tempfile.tempdir`, which the disposable root ignores on purpose (#992, #1455).
    """
    temporary = tmp_path / "temporaire"
    temporary.mkdir()
    for variable in VARIABLES_TEMP:
        monkeypatch.setenv(variable, str(temporary))

    too_big = b"x" * 4096
    with pytest.raises(SourceRejected):
        preview_sources(
            [_declare("gros.txt", too_big)],
            files=[("gros.txt", io.BytesIO(too_big))],
            guardrails=GardeFousIngestion(taille_max_source_octets=1024),
        )

    assert racine_jetable() == temporary / NOM_JETABLE
    assert list(racine_jetable().iterdir()) == [], "le dossier de l'aperçu est parti"


def test_the_route_previews_several_files_in_the_declared_order(
    client: TestClient,
) -> None:
    """A multipart carries two ordered lists, not a correspondence.

    The order is what decides what enters when the budget runs out: a preview
    that reversed it would announce a launch that will not happen.
    """
    first = b"# Premier\n\nUn contenu."
    second = b"# Second\n\nUn autre contenu."
    response = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [_declare("un.md", first), _declare("deux.md", second)]
            )
        },
        files=[
            ("fichier", ("un.md", first, "text/markdown")),
            ("fichier", ("deux.md", second, "text/markdown")),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert [reading["nom"] for reading in body["lectures"]] == ["un.md", "deux.md"]
    assert body["tokens"] == sum(reading["tokens"] for reading in body["lectures"])


def test_the_route_rejects_a_wrong_file_count(client: TestClient) -> None:
    """Two declarations for a single stream: pairing at random would be credible and false."""
    content = b"# Un\n"
    response = client.post(
        "/api/sources/apercu",
        data={
            "sources": json.dumps(
                [_declare("un.md", content), _declare("deux.md", content)]
            )
        },
        files=[("fichier", ("un.md", content, "text/markdown"))],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "apercu-sans-octets"
