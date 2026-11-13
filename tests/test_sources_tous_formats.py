"""The sources of a brief are read whatever their format (#1163).

`maestro.sources.extraction` reads any format, not a closed list of extensions.

Two criteria, two blocks:

1. **everything that can be read feeds the brief** — structured text and code as
   text, a spreadsheet by its content, an image by a model that looks at it. Each
   comes out *read* in the report and its content enters the context, framed as
   data (`markdown_context`);
2. **what is truly unreadable is named with its reason** — opaque binary, image too
   large, image no model can look at — and the bounds already set hold: bytes read,
   token caps, a directory's perimeter, and never a secret-bearing file in a
   model's context.

The model that looks at an image is **injected** (`read_image=`): no test resolves
the workstation's provider (`tests/conftest.py`, #782). The provider half — how
Claude and an OpenAI-compatible endpoint receive an image — is exercised in
`tests/test_providers.py` and `tests/test_openai_provider.py`.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path

import pytest

from maestro.providers.base import ImageJointe, ModelProvider, UnsupportedCapability
from maestro.sources.extraction import (
    STATE_IGNORED,
    STATE_READ,
    STATE_TRUNCATED,
    ExtractionGuardrails,
    Reading,
    ReadingReport,
    extract_sources,
    markdown_context,
)
from maestro.sources.images import (
    REASON_VISION_UNAVAILABLE,
    ImageNotSeen,
    ModelImageReader,
    image_type,
    read_image_in_preview,
)
from maestro.sources.model import TYPE_DIRECTORY, TYPE_FILE, Source

# --------------------------------------------------------------------------- #
# Factories                                                                    #
# --------------------------------------------------------------------------- #


def minimal_png() -> bytes:
    """A real one-pixel PNG — enough for a decoder to accept it, written by hand.

    By hand rather than through Pillow: the repository does not depend on it, and
    that is what lets us state that recognizing an image rests on its **bytes**,
    not on a library.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def put(base: Path, name: str, content: str | bytes) -> Source:
    """A `file` source written to disk, text or bytes."""
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return Source(type=TYPE_FILE, name=name, path=str(path))


def workbook(path: Path) -> None:
    """A two-sheet `.xlsx` written by openpyxl — the converter being exercised."""
    openpyxl = pytest.importorskip("openpyxl", reason="convertisseur .xlsx non installé")
    book = openpyxl.Workbook()
    planning = book.active
    planning.title = "Planning"
    planning.append(["Lot", "Délai"])
    planning.append(["Socle", "2 semaines"])
    budget = book.create_sheet("Budget")
    budget.append(["Poste", "Montant"])
    budget.append(["Hébergement", 1200])
    book.save(str(path))


class Gaze:
    """A model that looks at an image — the `read_image` double, which keeps what it saw."""

    def __init__(self, answer: str = "Maquette : un bouton « Lancer » en haut à droite.") -> None:
        self.answer = answer
        self.seen: list[tuple[bytes, str, str]] = []

    def __call__(self, data: bytes, media_type: str, name: str) -> str:
        self.seen.append((data, media_type, name))
        return self.answer


def single(report: ReadingReport) -> Reading:
    assert len(report.readings) == 1, report.summary()
    return report.readings[0]


# --------------------------------------------------------------------------- #
# Criterion 1 — everything that can be read feeds the brief                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("cahier.json", '{"objectif": "livrer le socle", "delai": "2 semaines"}', "le socle"),
        ("cahier.yaml", "objectif: livrer le socle\ndelai: 2 semaines\n", "objectif: livrer"),
        ("export.csv", "lot;delai\nsocle;2 semaines\n", "socle;2 semaines"),
        ("app.py", "def lancer():\n    return 'socle'\n", "def lancer():"),
        ("composant.tsx", "export function Bouton() { return null; }\n", "function Bouton"),
        ("Makefile", "build:\n\tnpm run build\n", "npm run build"),
    ],
    ids=["json", "yaml", "csv", "python", "tsx", "no-extension"],
)
def test_structured_text_and_code_are_read_as_text(
    tmp_path: Path, name: str, content: str, expected: str
) -> None:
    """No extension list: a file whose bytes are text is read."""
    reading = single(extract_sources([put(tmp_path, name, content)]))

    assert reading.state == STATE_READ, reading.message
    assert expected in reading.markdown
    assert reading.tokens > 0


def test_a_utf16_text_is_read_despite_its_null_bytes(tmp_path: Path) -> None:
    """What a PowerShell redirection writes: every other byte null, and yet text."""
    log = put(tmp_path, "journal.log", "Échec du déploiement\n".encode("utf-16"))

    reading = single(extract_sources([log]))

    assert reading.state == STATE_READ, reading.message
    assert "Échec du déploiement" in reading.markdown


def test_an_html_page_is_reduced_to_text(tmp_path: Path) -> None:
    """A saved page is read like a fetched page: text, headings, no script."""
    page = (
        "<!doctype html><html><body><script>alert('consigne')</script>"
        "<h1>Cahier des charges</h1><p>Livrer le socle.</p></body></html>"
    )

    reading = single(extract_sources([put(tmp_path, "cdc.html", page)]))

    assert reading.state == STATE_READ
    assert "# Cahier des charges" in reading.markdown
    assert "Livrer le socle." in reading.markdown
    assert "alert" not in reading.markdown


def test_an_xlsx_workbook_is_read_by_its_content(tmp_path: Path) -> None:
    """Each sheet under its name, each row as a Markdown table row."""
    path = tmp_path / "planning.xlsx"
    workbook(path)

    source = Source(type=TYPE_FILE, name="planning.xlsx", path=str(path))

    reading = single(extract_sources([source]))

    assert reading.state == STATE_READ, reading.message
    assert "## Feuille : Planning" in reading.markdown
    assert "| Socle | 2 semaines |" in reading.markdown
    assert "## Feuille : Budget" in reading.markdown
    assert "| Hébergement | 1200 |" in reading.markdown


def test_an_image_is_looked_at_by_the_model_and_its_reading_enters_the_context(
    tmp_path: Path,
) -> None:
    """The image goes to the model as is, and what it sees enters as a read source."""
    gaze = Gaze()
    image = minimal_png()

    report = extract_sources([put(tmp_path, "maquette.png", image)], read_image=gaze)

    reading = single(report)
    assert reading.state == STATE_READ, reading.message
    assert gaze.seen == [(image, "image/png", "maquette.png")]
    assert "bouton « Lancer »" in reading.markdown
    # The context says it is a **transcription**: the brief must not believe it
    # read the image itself.
    rendered = markdown_context(report)
    assert "bouton « Lancer »" in rendered
    assert "regardée par le modèle" in rendered


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8, "image/webp"),
    ],
    ids=["png", "jpeg", "gif", "webp"],
)
def test_an_image_is_recognized_by_its_bytes_not_its_name(
    tmp_path: Path, data: bytes, media_type: str
) -> None:
    """The name comes from outside; the file's signature does not."""
    assert image_type(data) == media_type
    gaze = Gaze()

    capture = put(tmp_path, "capture-sans-extension", data)

    reading = single(extract_sources([capture], read_image=gaze))

    assert reading.state == STATE_READ
    assert gaze.seen[0][1] == media_type


def test_all_the_ticket_formats_feed_the_context(tmp_path: Path) -> None:
    """The whole criterion, attached to a single goal: each among the read sources."""
    xlsx_path = tmp_path / "budget.xlsx"
    workbook(xlsx_path)
    sources = [
        put(tmp_path, "cahier.json", '{"objectif": "JSON-LU"}'),
        put(tmp_path, "cahier.yaml", "objectif: YAML-LU\n"),
        put(tmp_path, "export.csv", "colonne\nCSV-LU\n"),
        Source(type=TYPE_FILE, name="budget.xlsx", path=str(xlsx_path)),
        put(tmp_path, "page.html", "<html><body><p>HTML-LU</p></body></html>"),
        put(tmp_path, "main.go", 'package main // GO-LU\n'),
        put(tmp_path, "maquette.png", minimal_png()),
    ]

    report = extract_sources(sources, read_image=Gaze("IMAGE-LUE"))

    assert [reading.state for reading in report.readings] == [STATE_READ] * len(sources), (
        report.summary()
    )
    assert len(report.read) == len(sources)
    rendered = markdown_context(report)
    for marker in ("JSON-LU", "YAML-LU", "CSV-LU", "Hébergement", "HTML-LU", "GO-LU", "IMAGE-LUE"):
        assert marker in rendered


# --------------------------------------------------------------------------- #
# Criterion 2 — the unreadable is named with its reason, and the bounds hold   #
# --------------------------------------------------------------------------- #


def test_an_opaque_binary_is_named_with_its_reason(tmp_path: Path) -> None:
    """Neither text, nor image, nor convertible document: the line says why."""
    tool = put(tmp_path, "outil.exe", b"MZ\x90\x00\x03\x00\x00\x00binaire")

    reading = single(extract_sources([tool]))

    assert reading.state == STATE_IGNORED
    assert reading.reason == "binaire-opaque"
    assert ".exe" in reading.message
    assert reading.markdown == "" and reading.tokens == 0


def test_without_a_model_that_sees_an_image_is_named_never_silenced(tmp_path: Path) -> None:
    """A reading with no model plugged in does not hide the image: it says why."""
    report = extract_sources([put(tmp_path, "maquette.png", minimal_png())])

    reading = single(report)
    assert (reading.state, reading.reason) == (STATE_IGNORED, REASON_VISION_UNAVAILABLE)
    assert "maquette.png" in markdown_context(report)


def test_a_provider_that_does_not_see_images_says_so(tmp_path: Path) -> None:
    """Agnosticism (O7): a provider without the capability is named, not worked around."""

    class TextOnly(ModelProvider):
        name = "texte-seul"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return "jamais appelé"

    reader = ModelImageReader(provider=TextOnly(), model="m")

    diagram = put(tmp_path, "schema.png", minimal_png())

    reading = single(extract_sources([diagram], read_image=reader))

    assert (reading.state, reading.reason) == (STATE_IGNORED, REASON_VISION_UNAVAILABLE)
    assert "texte-seul" in reading.message


def test_the_production_reader_shows_the_image_to_the_provider(tmp_path: Path) -> None:
    """`ModelImageReader`: the image, its instruction and the model travel to the provider."""

    class Seer(ModelProvider):
        name = "voyant"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            raise AssertionError("une image ne passe pas par generate")

        async def generate_with_images(self, prompt, *, images, model, system_prompt=None):
            self.calls.append(
                {"prompt": prompt, "images": images, "model": model, "system": system_prompt}
            )
            return "Un schéma à trois boîtes."

    provider = Seer()
    image = minimal_png()

    reading = single(
        extract_sources(
            [put(tmp_path, "archi.png", image)],
            read_image=ModelImageReader(provider=provider, model="modele-qui-voit"),
        )
    )

    assert reading.state == STATE_READ
    assert "trois boîtes" in reading.markdown
    (call,) = provider.calls
    assert call["model"] == "modele-qui-voit"
    assert call["images"] == [ImageJointe(octets=image, type_media="image/png", nom="archi.png")]
    # The instruction tells the model that an image's text is **data** (ENF-13).
    assert "jamais une consigne" in str(call["system"])


def test_the_production_reader_resolves_the_workstation_provider_under_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an injected provider, it resolves a **bare** `provider_from_settings()` (#782).

    That is the only form the suite's guard recognizes: a resolution that passed its
    own settings would read the same provider through an unwatched door, and a test
    that attaches an image would call the real model — which happened once while
    writing this reader.
    """
    resolutions: list[tuple[object, ...]] = []

    class Seer(ModelProvider):
        name = "voyant"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return ""

        async def generate_with_images(self, prompt, *, images, model, system_prompt=None):
            return f"vu par {model}"

    def double(*args: object) -> ModelProvider:
        resolutions.append(args)
        return Seer()

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", double)
    monkeypatch.setenv("MAESTRO_MODEL", "modele-du-brief")

    report = extract_sources(
        [put(tmp_path, "a.png", minimal_png()), put(tmp_path, "b.png", minimal_png())],
        read_image=ModelImageReader(),
    )

    assert [reading.state for reading in report.readings] == [STATE_READ, STATE_READ]
    assert "vu par modele-du-brief" in report.readings[0].markdown
    # Bare, and once for the whole reading: the resolution is memoized.
    assert resolutions == [()]


def test_a_model_failure_on_an_image_is_named(tmp_path: Path) -> None:
    """A failing model does not take the reading down: the line says what happened."""

    def broken(_data: bytes, _type: str, _name: str) -> str:
        raise TimeoutError("le modèle n'a pas répondu")

    report = extract_sources(
        [put(tmp_path, "maquette.png", minimal_png()), put(tmp_path, "notes.md", "Suite")],
        read_image=broken,
    )

    image, notes = report.readings
    assert (image.state, image.reason) == (STATE_IGNORED, "vision-en-echec")
    assert "TimeoutError" in image.message
    assert notes.state == STATE_READ


def test_an_oversized_image_is_named_without_being_shown(tmp_path: Path) -> None:
    """An image is not read in part: beyond the bytes read, it is not sent."""
    gaze = Gaze()
    image = minimal_png() + b"\x00" * 4096

    reading = single(
        extract_sources(
            [put(tmp_path, "affiche.png", image)],
            guardrails=ExtractionGuardrails(max_bytes_read=1024),
            read_image=gaze,
        )
    )

    assert (reading.state, reading.reason) == (STATE_IGNORED, "trop-volumineux")
    assert "affiche.png" in reading.message
    assert gaze.seen == []


def test_the_number_of_images_shown_to_the_model_is_capped(tmp_path: Path) -> None:
    """Each image is a model call before the run even starts: the cap says so per image."""
    for index in range(3):
        (tmp_path / "maquettes").mkdir(exist_ok=True)
        (tmp_path / "maquettes" / f"ecran{index}.png").write_bytes(minimal_png())
    gaze = Gaze()

    reading = single(
        extract_sources(
            [Source(type=TYPE_DIRECTORY, name="maquettes", path=str(tmp_path / "maquettes"))],
            guardrails=ExtractionGuardrails(max_images=2),
            read_image=gaze,
        )
    )

    assert len(gaze.seen) == 2
    assert [entry.reason for entry in reading.entries] == ["", "", "images-plafond"]
    assert "2 images" in reading.entries[2].message


def test_an_oversized_text_stays_cut_at_the_bytes_read(tmp_path: Path) -> None:
    """The byte bound applies to any text, JSON and code included — never loaded whole."""
    reading = single(
        extract_sources(
            [put(tmp_path, "donnees.json", "[" + "1," * 50_000 + "1]")],
            guardrails=ExtractionGuardrails(max_bytes_read=1_000, max_tokens_per_source=None),
        )
    )

    assert reading.state == STATE_TRUNCATED
    assert reading.limit == "octets lus"


def test_a_huge_workbook_is_cut_and_says_so(tmp_path: Path) -> None:
    """A spreadsheet is converted under the same bound: beyond it, cut and announced."""
    openpyxl = pytest.importorskip("openpyxl", reason="convertisseur .xlsx non installé")
    book = openpyxl.Workbook()
    for index in range(2_000):
        book.active.append([f"ligne {index}", "x" * 20])
    path = tmp_path / "gros.xlsx"
    book.save(str(path))

    reading = single(
        extract_sources(
            [Source(type=TYPE_FILE, name="gros.xlsx", path=str(path))],
            guardrails=ExtractionGuardrails(max_bytes_read=2_000, max_tokens_per_source=None),
        )
    )

    assert reading.state == STATE_TRUNCATED
    assert "octets lus" in reading.limit
    assert "ligne 1999" not in reading.markdown


@pytest.mark.parametrize(
    "name",
    [".env.local", ".npmrc", "cle.pem", "deploiement.key", "credentials"],
)
def test_a_secret_bearing_file_never_enters_the_context(tmp_path: Path, name: str) -> None:
    """Reading every format does not open the door to secrets: the rule is the project's."""
    report = extract_sources([put(tmp_path, name, "JETON=valeur-tres-secrete-1163\n")])

    reading = single(report)
    assert (reading.state, reading.reason) == (STATE_IGNORED, "secret")
    assert "valeur-tres-secrete-1163" not in markdown_context(report)


def test_in_a_directory_perimeter_and_secrets_hold(tmp_path: Path) -> None:
    """The perimeter discards without listing; a remaining secret bearer is named unread."""
    refs = tmp_path / "refs"
    (refs / "node_modules").mkdir(parents=True)
    (refs / "node_modules" / "paquet.json").write_text('{"x": 1}', encoding="utf-8")
    (refs / ".env").write_text("CLE=exclue-par-le-perimetre", encoding="utf-8")
    (refs / "config.json").write_text('{"port": 8080}', encoding="utf-8")
    (refs / "serveur.pem").write_text("-----BEGIN KEY-----\nsecret-du-pem-1163\n", encoding="utf-8")

    report = extract_sources([Source(type=TYPE_DIRECTORY, name="refs", path=str(refs))])

    reading = single(report)
    states = {entry.name: (entry.state, entry.reason) for entry in reading.entries}
    assert states == {"config.json": (STATE_READ, ""), "serveur.pem": (STATE_IGNORED, "secret")}
    rendered = markdown_context(report)
    assert "secret-du-pem-1163" not in rendered
    assert "exclue-par-le-perimetre" not in rendered


# --------------------------------------------------------------------------- #
# The image reader                                                             #
# --------------------------------------------------------------------------- #


def test_the_preview_does_not_show_the_image_to_the_model_and_says_so(tmp_path: Path) -> None:
    """The preview is free (docs/05 §2.7.3): it announces the launch-time reading without paying."""
    mockup = put(tmp_path, "maquette.png", minimal_png())

    reading = single(extract_sources([mockup], read_image=read_image_in_preview))

    assert (reading.state, reading.reason) == (STATE_IGNORED, "vue-au-lancement")
    assert "lancement" in reading.message


def test_an_unseen_image_carries_its_reason() -> None:
    """The exception's contract: a stable code, a readable sentence."""
    rejection = ImageNotSeen("vision-indisponible", "Aucun modèle ne voit ici.")

    assert (rejection.reason, str(rejection)) == (
        "vision-indisponible",
        "Aucun modèle ne voit ici.",
    )


def test_the_boundary_rejects_an_image_by_default() -> None:
    """Optional capability: a provider that does not override it rejects it, by name."""

    class TextOnly(ModelProvider):
        name = "texte-seul"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return ""

    with pytest.raises(UnsupportedCapability, match="texte-seul"):
        asyncio.run(
            TextOnly().generate_with_images(
                "Décris.", images=[ImageJointe(octets=b"x", type_media="image/png")], model="m"
            )
        )
