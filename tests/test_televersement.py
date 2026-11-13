"""The store of uploaded sources (#317): receive bytes, then attach them to a run.

Lot 7 deferred all its coverage to the final lot (checklist of #314), and this
is the Phase 8 module that needed it most: it **writes to disk** content coming
from the outside, at a place computed from a name the browser proposes. So the
three questions to ask it:

1. **is what comes in sanitized** -- a file name coming from a browser is the
   classic vector of directory traversal, and an identifier sent back by a
   client becomes a path segment again on re-read;
2. **does a rejection leave anything behind** -- it is the module's central
   promise ("nothing is truncated at input"): a half-received file would be
   reported "read" by the reading report, and the brief would conclude on an
   amputated document without anyone being able to see it;
3. **does the store prevail over the client** -- name and size of an uploaded
   source come from the received bytes, never from what the request announces,
   otherwise the per-source cap is circumvented by declaring twelve bytes.

All three are measured **on disk** and not on an intention: every rejection test
re-reads the store to check that it is empty. No network, no backend -- the
module receives any binary stream, which is precisely what makes it testable
without an HTTP request.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import IO, Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import SourceRejected, ingestion_location
from maestro.sources.model import TYPE_DIRECTORY, TYPE_FILE, TYPE_URL
from maestro.sources.upload import (
    UPLOAD_ID_PATTERN,
    UPLOADS_DIRECTORY,
    UploadStore,
    declare_uploads,
)

CONTENT = "Cahier des charges\n\nRefondre l'écran de lancement.".encode()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake user home -- same reason as in #221/#223 and #315.

    On Windows, pytest's `tmp_path` lives in `AppData/Local/Temp`, which the root
    validation rejects for good reason: without this isolation, the paths of
    these tests would be rejected for a good reason, but not the one they
    measure.
    """
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def _disposable_ingestion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ingestion location pointed at a disposable directory, never `core/`."""
    root = tmp_path / "ingestion"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(root))
    return root


@pytest.fixture()
def store(tmp_path: Path) -> UploadStore:
    """An isolated store, default caps."""
    return UploadStore(tmp_path / "depot")


def _entries(store: UploadStore) -> list[Path]:
    """What the store really holds -- measured on disk, never deduced."""
    root = store._root  # noqa: SLF001 - the on-disk state is precisely the subject
    return sorted(root.iterdir()) if root.is_dir() else []


class CountingStream(io.BytesIO):
    """A stream that remembers how many bytes were actually taken from it.

    It is what tells "rejected during reception" from "received then thrown
    away": without this counter, both regimes return the same rejection and the
    2 GiB file has gone through the disk anyway.
    """

    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.consumed = 0

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk


class InterruptedStream(io.RawIOBase):
    """A stream that returns one chunk then raises a `BaseException`.

    Reproduces what the module says it anticipates: the cancellation of the task
    carrying the copy (client disconnected, app shutdown). `KeyboardInterrupt`
    does not inherit from `Exception` -- an `except Exception` would therefore
    leave the file truncated.
    """

    def __init__(self) -> None:
        self.chunks = 0

    def read(self, size: int | None = -1, /) -> bytes:
        self.chunks += 1
        if self.chunks == 1:
            return b"debut de document"
        raise KeyboardInterrupt("copie interrompue")


# --- 1. Receive: bytes, an identifier, a sanitized name ----------------------


def test_received_bytes_are_re_read_under_their_identifier(
    store: UploadStore,
) -> None:
    """The nominal case: what is deposited is found again, byte for byte."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    assert UPLOAD_ID_PATTERN.match(upload.id)
    assert upload.name == "cdc.md"
    assert upload.size == len(CONTENT)
    assert upload.path.read_bytes() == CONTENT
    reread = store.read(upload.id)
    assert reread == upload


def test_the_store_directory_is_outside_any_run_and_any_project(
    _disposable_ingestion: Path,
) -> None:
    """Uploading deposits the material before a run exists -- hence next to them."""
    store = UploadStore.default()
    upload = store.receive("note.md", io.BytesIO(b"un mot"))
    assert upload.path.parent.parent == _disposable_ingestion / UPLOADS_DIRECTORY


def test_the_default_store_creates_nothing_by_merely_being_read(
    _disposable_ingestion: Path,
) -> None:
    """A deployment where nobody uploads lays no directory (pattern of #315)."""
    UploadStore.default()
    assert not _disposable_ingestion.exists()


def test_the_uploads_directory_cannot_be_mistaken_for_a_run() -> None:
    """The store <-> run collision is impossible **by construction**, not unlikely.

    `UPLOADS_DIRECTORY` starts with an underscore, whereas `RUN_ID_PATTERN` (#315)
    requires an alphanumeric first character: no `run_id` can therefore ever
    designate this directory. It is what the module's comment asserts -- we
    measure it rather than rereading it.
    """
    with pytest.raises(SourceRejected) as capture:
        ingestion_location(UPLOADS_DIRECTORY)
    assert capture.value.reason == "run-invalide"


@pytest.mark.parametrize(
    "name",
    [
        "../../evasion.md",  # directory traversal, the classic
        "..",
        "/etc/passwd",
        "C:\\Windows\\system32\\cle.md",
        "note.md:cache",  # NTFS alternate data stream: invisible content
    ],
)
def test_a_name_that_is_a_path_is_rejected_before_any_write(
    store: UploadStore, name: str
) -> None:
    """The name comes from the browser, hence from the outside -- rejected **before** the mkdir.

    The same `file_name` as the resolution (#315): two sanitizers written
    separately would diverge, and the one forgetting a character class would be
    the hole.
    """
    with pytest.raises(SourceRejected) as capture:
        store.receive(name, io.BytesIO(CONTENT))
    assert capture.value.reason == "nom-invalide"
    assert _entries(store) == []


def test_a_missing_name_is_rejected(store: UploadStore) -> None:
    """An empty `filename` field on the browser side must not produce a nameless file."""
    with pytest.raises(SourceRejected) as capture:
        store.receive("   ", io.BytesIO(CONTENT))
    assert capture.value.reason == "nom-absent"
    assert _entries(store) == []


def test_two_files_with_the_same_name_do_not_overwrite_each_other(
    store: UploadStore,
) -> None:
    """One identifier per upload: two `cdc.md` coexist instead of replacing each other."""
    first = store.receive("cdc.md", io.BytesIO(b"version 1"))
    second = store.receive("cdc.md", io.BytesIO(b"version 2"))
    assert first.id != second.id
    assert first.path.read_bytes() == b"version 1"
    assert second.path.read_bytes() == b"version 2"


# --- 2. A rejection leaves nothing behind ------------------------------------


def test_a_file_that_is_too_big_is_rejected_and_the_store_stays_empty(
    tmp_path: Path,
) -> None:
    """The per-source cap (ENF-07), applied at reception."""
    store = UploadStore(
        tmp_path / "depot", guardrails=GardeFousIngestion(taille_max_source_octets=1_000)
    )
    with pytest.raises(SourceRejected) as capture:
        store.receive("gros.bin", io.BytesIO(b"x" * 5_000))
    assert capture.value.reason == "source-trop-volumineuse"
    assert _entries(store) == []


def test_the_rejection_falls_during_reception_and_not_after(tmp_path: Path) -> None:
    """The distinction the module exists to hold.

    A 2 GiB file must not first be written to disk to then be declared too big.
    It is measured on the bytes actually **taken** from the stream: the reading
    stops at the first chunk that exceeds, not at the end.
    """
    store = UploadStore(
        tmp_path / "depot", guardrails=GardeFousIngestion(taille_max_source_octets=1_000)
    )
    stream = CountingStream(b"x" * (4 * 1024 * 1024))
    with pytest.raises(SourceRejected):
        store.receive("gros.bin", stream)
    assert stream.consumed < len(stream.getvalue())


def test_the_total_of_the_call_is_capped_too(tmp_path: Path) -> None:
    """Without `already_received`, twenty files under the per-source cap would all pass."""
    store = UploadStore(
        tmp_path / "depot",
        guardrails=GardeFousIngestion(
            taille_max_source_octets=10_000, taille_max_totale_octets=1_000
        ),
    )
    with pytest.raises(SourceRejected) as capture:
        store.receive("suite.bin", io.BytesIO(b"x" * 600), already_received=600)
    assert capture.value.reason == "ingestion-trop-volumineuse"
    assert _entries(store) == []


def test_the_index_of_the_faulty_file_travels_with_the_rejection(tmp_path: Path) -> None:
    """Saying a file is too big without saying which would force starting over."""
    store = UploadStore(
        tmp_path / "depot", guardrails=GardeFousIngestion(taille_max_source_octets=10)
    )
    with pytest.raises(SourceRejected) as capture:
        store.receive("troisieme.bin", io.BytesIO(b"x" * 100), index=2)
    assert capture.value.index == 2
    assert "Fichier 3" in str(capture.value)


def test_an_interruption_does_not_leave_a_truncated_file(
    store: UploadStore,
) -> None:
    """`except BaseException` and not `except Exception`: the choice is measured here.

    A cancellation of the task carrying the copy would otherwise leave in the
    store a truncated file **indistinguishable** from a complete upload -- the
    reading report would say it "read".
    """
    with pytest.raises(KeyboardInterrupt):
        store.receive("interrompu.md", InterruptedStream())
    assert _entries(store) == []


# --- 3. Re-read, attach, forget ----------------------------------------------


def test_an_unknown_identifier_designates_no_upload(
    store: UploadStore,
) -> None:
    """None rather than an exception: the caller is the one who knows which rejection it is."""
    assert store.read("a1b2c3d4e5f6") is None


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "court",  # fewer than 6 characters
        "x" * 65,  # more than 64
        "avec-tiret",
        "../voisin",  # the climb of one level, the one that counts
        "sous/dossier",
    ],
)
def test_a_non_conforming_identifier_is_dropped_without_touching_the_disk(
    store: UploadStore, identifier: str
) -> None:
    """An identifier comes back from a client: it need not become a path to be dropped."""
    assert store.read(identifier) is None


def test_the_re_read_does_not_climb_out_of_the_store(
    store: UploadStore, tmp_path: Path
) -> None:
    """The concrete counterpart of the previous test: a neighbour of the store stays unreachable."""
    neighbour = tmp_path / "depot" / ".." / "voisin"
    neighbour.mkdir(parents=True)
    (neighbour / "secret.md").write_bytes(b"rien a voir ici")
    assert store.read("../voisin") is None


def test_an_empty_directory_designates_no_upload(
    store: UploadStore, tmp_path: Path
) -> None:
    """The upload's name is that of the file it contains: without a file, nothing."""
    (tmp_path / "depot" / "a1b2c3d4e5f6").mkdir(parents=True)
    assert store.read("a1b2c3d4e5f6") is None


def test_attach_copies_the_bytes_and_leaves_the_original(
    store: UploadStore, tmp_path: Path
) -> None:
    """Attaching **copies**: relaunching the same objective must not force re-uploading."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    target = tmp_path / "ingestion" / "a1b2c3d4e5f6" / "cdc.md"
    returned = store.attach(upload.id, target)
    assert returned == target
    assert target.read_bytes() == CONTENT
    assert upload.path.read_bytes() == CONTENT


def test_attach_creates_the_run_directory(
    store: UploadStore, tmp_path: Path
) -> None:
    """`ingestion_location` (#315) merely computes; writing happens here."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    target = tmp_path / "jamais" / "cree" / "cdc.md"
    store.attach(upload.id, target)
    assert target.is_file()


def test_attaching_an_unknown_upload_raises_instead_of_skipping_the_copy(
    store: UploadStore, tmp_path: Path
) -> None:
    """A silently skipped copy would make a run work on absent material."""
    with pytest.raises(SourceRejected) as capture:
        store.attach("a1b2c3d4e5f6", tmp_path / "cible.md")
    assert capture.value.reason == "televersement-inconnu"


def test_forget_removes_the_upload_and_stays_silent_on_an_unknown_one(
    store: UploadStore,
) -> None:
    """Best-effort: it is what lets a rejected call leave nothing behind."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    store.forget(upload.id)
    assert store.read(upload.id) is None
    store.forget(upload.id)  # twice: no effect, no error


def test_forget_deletes_nothing_outside_the_store(
    store: UploadStore, tmp_path: Path
) -> None:
    """The guardrail that counts: `forget` takes an identifier coming from a client."""
    neighbour = tmp_path / "depot" / ".." / "voisin"
    neighbour.mkdir(parents=True)
    (neighbour / "secret.md").write_bytes(b"rien a voir ici")
    store.forget("../voisin")
    assert (neighbour / "secret.md").is_file()


# --- 4. Declare: the store prevails, never the client ------------------------


def test_a_back_reference_is_completed_by_the_name_and_size_of_the_store(
    store: UploadStore,
) -> None:
    """A client announcing twelve bytes for 12 MiB would otherwise pass the cap."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    declared, identifiers = declare_uploads(
        [{"type": TYPE_FILE, "id": upload.id, "nom": "menteur.md", "taille": 12}],
        store=store,
    )
    assert declared == [
        {"type": TYPE_FILE, "nom": "cdc.md", "taille": len(CONTENT)}
    ]
    assert identifiers == (upload.id,)


def test_a_bare_declaration_passes_as_is(store: UploadStore) -> None:
    """The second form of a `fichier` source (docs/24 §3.2): `nom` + `taille`, without bytes.

    It will come out `ignore` / `source-absente` in the reading report -- which
    is precisely what the report exists to say.
    """
    bare = {"type": TYPE_FILE, "nom": "cdc.md", "taille": 42}
    declared, identifiers = declare_uploads([bare], store=store)
    assert declared == [bare]
    assert identifiers == ("",)


def test_the_identifiers_stay_aligned_with_the_indexes(
    store: UploadStore,
) -> None:
    """This alignment is what will allow attaching the bytes once the run is known."""
    upload = store.receive("cdc.md", io.BytesIO(CONTENT))
    declared, identifiers = declare_uploads(
        [
            {"type": TYPE_URL, "valeur": "https://exemple.test/spec"},
            {"type": TYPE_FILE, "id": upload.id},
            {"type": TYPE_DIRECTORY, "nom": "maquettes", "chemin": "/refs/maquettes"},
        ],
        store=store,
    )
    assert len(declared) == 3
    assert identifiers == ("", upload.id, "")


def test_an_unknown_back_reference_is_rejected_with_its_index(
    store: UploadStore,
) -> None:
    """An already removed identifier must not produce a run with absent material."""
    with pytest.raises(SourceRejected) as capture:
        declare_uploads(
            [
                {"type": TYPE_URL, "valeur": "https://exemple.test/spec"},
                {"type": TYPE_FILE, "id": "a1b2c3d4e5f6"},
            ],
            store=store,
        )
    assert capture.value.reason == "televersement-inconnu"
    assert capture.value.index == 1


@pytest.mark.parametrize("raw", [None, "pas une liste", 42, {"type": TYPE_FILE}])
def test_what_is_not_a_list_comes_out_unchanged(
    store: UploadStore, raw: Any
) -> None:
    """The shape rejection belongs to `resolve_sources`: duplicating it would make two messages."""
    declared, identifiers = declare_uploads(raw, store=store)
    assert declared is raw
    assert identifiers == ()


@pytest.mark.parametrize(
    "entry",
    [
        {"type": TYPE_URL, "id": "a1b2c3d4e5f6"},
        {"type": TYPE_DIRECTORY, "id": "a1b2c3d4e5f6"},
        {"type": TYPE_FILE, "id": "   "},
        "pas un objet",
    ],
)
def test_what_does_not_refer_to_an_upload_passes_without_a_read(
    store: UploadStore, entry: Any
) -> None:
    """Only a `fichier` carrying an `id` is a back-reference; the rest never queries the store."""
    declared, identifiers = declare_uploads([entry], store=store)
    assert declared == [entry]
    assert identifiers == ("",)


# --- 5. The `POST /api/sources` route ----------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """The real app, upload store injected on a disposable directory."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        televersements=UploadStore(tmp_path / "depot"),
    )
    with TestClient(app) as client:
        yield client


def _upload_part(name: str, content: bytes) -> tuple[str, tuple[str, IO[bytes], str]]:
    """A form file, as the composition screen sends it."""
    return ("fichier", (name, io.BytesIO(content), "text/markdown"))


def test_the_route_returns_one_identifier_per_file_and_the_total(client: TestClient) -> None:
    """`POST /api/sources`: 201, one `id` per file, and the total received (docs/05 §6.8)."""
    response = client.post(
        "/api/sources",
        files=[_upload_part("cdc.md", CONTENT), _upload_part("notes.md", b"trois mots ici")],
    )
    assert response.status_code == 201
    body = response.json()
    assert [source["nom"] for source in body["sources"]] == ["cdc.md", "notes.md"]
    assert body["total_octets"] == len(CONTENT) + len(b"trois mots ici")
    assert {source["type"] for source in body["sources"]} == {TYPE_FILE}


def test_the_route_never_publishes_the_path_of_the_bytes(client: TestClient) -> None:
    """A server implementation detail -- the client designates its material by its `id`."""
    body = client.post("/api/sources", files=[_upload_part("cdc.md", CONTENT)]).json()
    assert set(body["sources"][0]) == {"id", "type", "nom", "taille"}


def test_the_route_bounds_the_number_of_files_before_touching_the_disk(
    tmp_path: Path,
) -> None:
    """The **count** cap is judged on the request: nothing is written to be then thrown away."""
    store = UploadStore(
        tmp_path / "depot", guardrails=GardeFousIngestion(nb_max_sources=2)
    )
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=store
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/sources",
            files=[_upload_part(f"f{i}.md", b"court") for i in range(3)],
        )
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "trop-de-sources"
    assert _entries(store) == []


def test_a_rejection_removes_the_files_already_accepted_in_the_same_call(
    tmp_path: Path,
) -> None:
    """The atomicity of the call -- the reason for being of `forget`.

    If the second file overflows, the first need not stay in the store under an
    identifier nobody received: it would be there forever, no run ever claiming
    it.
    """
    store = UploadStore(
        tmp_path / "depot", guardrails=GardeFousIngestion(taille_max_source_octets=100)
    )
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=store
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/sources",
            files=[_upload_part("petit.md", b"court"), _upload_part("gros.bin", b"x" * 500)],
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["motif"] == "source-trop-volumineuse"
    assert detail["index"] == 1
    assert _entries(store) == []


def test_the_route_rejects_a_file_whose_name_is_a_path(client: TestClient) -> None:
    """Lot 1's rejection goes through the route with its reason, not as a 500."""
    response = client.post("/api/sources", files=[_upload_part("../../evasion.md", CONTENT)])
    assert response.status_code == 422
    assert response.json()["detail"]["motif"] == "nom-invalide"


# --- 6. From the store to the run: the bytes join the ingestion location -----


def test_the_uploaded_bytes_join_the_run_location(
    tmp_path: Path, _disposable_ingestion: Path
) -> None:
    """The end to end of #317: upload, then launch by referring to the identifier.

    It is the service's mandatory order (`_composer`) that is checked here:
    complete the declaration from the store, resolve to know **where** the
    material must be, then copy the bytes there -- the location depends on the
    `run_id`, which does not exist at upload time.
    """
    store = UploadStore(tmp_path / "depot")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=store
    )
    with TestClient(app) as client:
        deposited = client.post("/api/sources", files=[_upload_part("cdc.md", CONTENT)]).json()
        identifier = deposited["sources"][0]["id"]
        launch = client.post(
            "/api/executions",
            json={
                "objectif": "Reprendre le cahier des charges",
                "sources": [{"type": TYPE_FILE, "id": identifier}],
            },
        )
        assert launch.status_code == 202
        summary = launch.json()

    (source,) = summary["sources"]
    in_the_run = Path(source["chemin"])
    assert in_the_run == (_disposable_ingestion / summary["run_id"] / "cdc.md").resolve()
    assert in_the_run.read_bytes() == CONTENT
    # The store keeps its own: relaunching after a failure does not re-upload.
    assert store.read(identifier) is not None


def test_an_unknown_back_reference_rejects_the_launch_with_its_reason(tmp_path: Path) -> None:
    """A stale identifier costs a reasoned 422, never a run with absent material."""
    store = UploadStore(tmp_path / "depot")
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), televersements=store
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/executions",
            json={
                "objectif": "Reprendre le cahier des charges",
                "sources": [{"type": TYPE_FILE, "id": "a1b2c3d4e5f6"}],
            },
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["motif"] == "televersement-inconnu"
    assert detail["index"] == 0
