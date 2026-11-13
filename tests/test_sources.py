"""Sources of an objective: model, resolution and ingestion guardrails (#315, EF-39).

Phase 8's first lot deferred its tests to the final lot (#323) **except these**:
the **rejections**. Forbidden root, escape through `..`, URL scheme, file name
that is a path, cap overflow -- these are security and cost guardrails, they are
tested with their code, never eight lots later.

Three things are checked, in this order:

1. the **shape** is re-read without being re-judged -- a durable journal replayed
   after the caps were tightened must stay readable;
2. the **resolution** rejects **with its reason**, type by type;
3. the sources **join the run** -- event, projection, summary -- and a launch
   that declares none returns exactly the view from before this lot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT, Event
from maestro.controltower.state import EXECUTION_TERMINEE, ControlTowerState
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import (
    TYPE_DIRECTORY,
    TYPE_FILE,
    TYPE_URL,
    Source,
    SourceRejected,
    ingestion_location,
    resolve_sources,
    sources_as_list,
    sources_from,
)
from maestro.sources.resolution import MAX_RUN_ID_LENGTH, ingestion_root

RUN = "a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake user home, outside of which the test directories live.

    Taken from `tests/test_projets.py`, and needed for the same reason: on
    Windows pytest's `tmp_path` lives under
    `C:/Users/<me>/AppData/Local/Temp`, which the root validation rejects **for
    good reason** (`chemin-sensible`). Without this isolation, every reference
    directory would be rejected -- for a good reason, but not the one the test
    measures.
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
def refs_dir(tmp_path: Path) -> Path:
    """A plausible references directory: outside home, outside any forbidden zone."""
    refs = tmp_path / "refs" / "maquettes"
    refs.mkdir(parents=True)
    return refs


def _rejection(sources: object, **extra: object) -> SourceRejected:
    """Resolve `sources` expecting a rejection, and return the exception to inspect it."""
    with pytest.raises(SourceRejected) as capture:
        resolve_sources(sources, run_id=RUN, **extra)  # type: ignore[arg-type]
    return capture.value


# --- 1. The shape: serialization, and a re-read that re-judges nothing --------


def test_json_round_trip_of_a_source() -> None:
    """`to_dict`/`from_dict` keep the source identical."""
    source = Source(
        type=TYPE_FILE, name="CDC-v2.docx", path="/ing/run/CDC-v2.docx", size=184320
    )
    assert Source.from_dict(source.to_dict()) == source


def test_re_reading_does_not_re_judge_an_already_accepted_source() -> None:
    """A re-read source is rebuilt as it was, even outside today's bounds.

    It is the exact counterpart of the rejection: we reject **once**, at input,
    where someone can still fix it. Re-judging at replay would make a past run
    unreadable the day a cap is tightened -- and a past run is not rewritten.
    """
    old = {"type": TYPE_URL, "valeur": "ftp://ancien/spec", "taille": 10**12}
    reread = Source.from_dict(old)
    assert reread.value == "ftp://ancien/spec"
    assert reread.size == 10**12


def test_unreadable_entries_are_dropped_one_by_one() -> None:
    """`sources_from` drops what tells nothing without losing the rest."""
    read = sources_from(
        [{"type": TYPE_URL, "valeur": "https://a.test"}, "bruit", {}, {"type": "", "nom": "x"}]
    )
    assert [s.value for s in read] == ["https://a.test"]


def test_a_string_is_not_a_list_of_sources() -> None:
    """A string is a `Sequence`: reading it character by character would be absurd."""
    assert sources_from("https://a.test") == []
    assert sources_as_list(None) == []


# --- 2. URL resolution: http(s), and nothing else ----------------------------


def test_an_http_url_is_accepted_as_is() -> None:
    sources = resolve_sources(
        [{"type": TYPE_URL, "nom": "Spec", "valeur": "https://exemple.test/spec"}], run_id=RUN
    )
    assert sources == (
        Source(type=TYPE_URL, name="Spec", value="https://exemple.test/spec", read_only=True),
    )


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",  # would read the disk through an uncontrolled door
        "javascript:alert(1)",  # is not an address
        "../spec.md",  # neither scheme nor host
        "https://exemple.test/a b",  # space: never a URL as is
    ],
)
def test_a_non_http_url_is_rejected(value: str) -> None:
    """The filter is the one of contract #183 (`SCHEMAS_URL`), not a second list."""
    rejection = _rejection([{"type": TYPE_URL, "valeur": value}])
    assert rejection.reason == "url-non-suivable"
    assert rejection.index == 0


def test_a_missing_or_oversized_url_is_rejected() -> None:
    assert _rejection([{"type": TYPE_URL, "valeur": "  "}]).reason == "url-absente"
    long_url = "https://exemple.test/" + "a" * 3000
    assert _rejection([{"type": TYPE_URL, "valeur": long_url}]).reason == "url-trop-longue"


# --- 3. Directory resolution: the root validation, and nothing new -----------


def test_a_references_directory_is_canonicalized_and_read_only(
    refs_dir: Path,
) -> None:
    """The stored path is the **resolved** one, never the input -- and writing is barred."""
    detoured = str(refs_dir / ".." / refs_dir.name)
    sources = resolve_sources(
        [{"type": TYPE_DIRECTORY, "chemin": detoured, "lecture_seule": False}], run_id=RUN
    )
    assert sources[0].path == str(refs_dir.resolve())
    assert sources[0].name == refs_dir.name
    # Forced: a references directory declared writable would be a disguised
    # project, which would have passed none of Phase 7's checks.
    assert sources[0].read_only is True


def test_a_sensitive_directory_is_rejected_with_the_root_validation_reason(
    _isolated_home: Path,
) -> None:
    """A references directory is a disk path: same prohibitions as a root."""
    secrets = _isolated_home / ".ssh"
    secrets.mkdir()
    rejection = _rejection([{"type": TYPE_DIRECTORY, "chemin": str(secrets)}])
    # The reason of `RacineRefusee` is kept as is: it is a code the UI already
    # knows how to translate, renaming it would make two vocabularies diverge.
    assert rejection.reason == "chemin-sensible"


def test_a_detour_through_dot_dot_does_not_launder_a_forbidden_directory(
    _isolated_home: Path,
) -> None:
    """Canonicalization happens **before** the comparison -- otherwise this is decorative."""
    secrets = _isolated_home / ".ssh"
    secrets.mkdir()
    evasion = str(_isolated_home / ".ssh" / ".." / ".ssh" / "refs")
    assert _rejection([{"type": TYPE_DIRECTORY, "chemin": evasion}]).reason == "chemin-sensible"


def test_a_missing_relative_or_absent_directory_is_rejected(tmp_path: Path) -> None:
    assert _rejection(
        [{"type": TYPE_DIRECTORY, "chemin": str(tmp_path / "nulle-part")}]
    ).reason == ("dossier-absent")
    assert (
        _rejection([{"type": TYPE_DIRECTORY, "chemin": "refs/maquettes"}]).reason
        == "chemin-relatif"
    )
    assert _rejection([{"type": TYPE_DIRECTORY, "chemin": ""}]).reason == "chemin-absent"


# --- 4. File resolution: outside the project, and without traversal ----------


def test_a_file_lands_in_the_run_location(_disposable_ingestion: Path) -> None:
    sources = resolve_sources(
        [{"type": TYPE_FILE, "nom": "CDC-v2.docx", "taille": 184320}], run_id=RUN
    )
    expected = (_disposable_ingestion / RUN / "CDC-v2.docx").resolve()
    assert Path(sources[0].path) == expected
    assert sources[0].size == 184320


def test_the_ingestion_location_is_rejected_if_it_falls_inside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is outside the project; the check is **active** -- not decorative.

    A `MAESTRO_INGESTION_DIR` set inside a project is rejected, never obeyed:
    uploaded material does not mix with the user's files.
    """
    project = tmp_path / "projets" / "depensio"
    project.mkdir(parents=True)
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(project / "pieces-jointes"))
    rejection = _rejection(
        [{"type": TYPE_FILE, "nom": "note.md", "taille": 10}], project_root=project
    )
    assert rejection.reason == "ingestion-dans-le-projet"


def test_a_location_outside_the_project_is_still_accepted(
    tmp_path: Path, _disposable_ingestion: Path
) -> None:
    """The counterpart of the previous test: it is indeed the **position** that decides."""
    project = tmp_path / "projets" / "depensio"
    project.mkdir(parents=True)
    sources = resolve_sources(
        [{"type": TYPE_FILE, "nom": "note.md", "taille": 10}],
        run_id=RUN,
        project_root=project,
    )
    assert Path(sources[0].path) == (_disposable_ingestion / RUN / "note.md").resolve()


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
def test_a_file_name_that_is_a_path_is_rejected(name: str) -> None:
    """The name comes from the user's browser, hence from the outside."""
    rejection = _rejection([{"type": TYPE_FILE, "nom": name, "taille": 10}])
    assert rejection.reason == "nom-invalide"


def test_a_file_without_a_name_or_with_an_oversized_name_is_rejected() -> None:
    assert _rejection([{"type": TYPE_FILE, "nom": "  ", "taille": 10}]).reason == "nom-absent"
    assert _rejection([{"type": TYPE_FILE, "nom": "n" * 300, "taille": 10}]).reason == (
        "nom-trop-long"
    )


def test_a_file_without_a_size_is_rejected() -> None:
    """An unknown size is a size that cannot be capped -- hence a circumventable cap."""
    assert _rejection([{"type": TYPE_FILE, "nom": "a.md"}]).reason == "taille-absente"
    assert _rejection([{"type": TYPE_FILE, "nom": "a.md", "taille": -1}]).reason == (
        "taille-invalide"
    )
    assert _rejection([{"type": TYPE_FILE, "nom": "a.md", "taille": "gros"}]).reason == (
        "taille-invalide"
    )


def test_a_run_id_that_is_a_path_is_rejected() -> None:
    """The `run_id` serves as a path segment: nothing non-conforming climbs a level."""
    with pytest.raises(SourceRejected) as capture:
        ingestion_location("../autre-run")
    assert capture.value.reason == "run-invalide"


def test_the_location_is_created_only_when_asked(_disposable_ingestion: Path) -> None:
    """This lot neither reads nor writes anything: creation belongs to the upload (#317)."""
    computed = ingestion_location(RUN)
    assert not computed.exists()
    assert ingestion_location(RUN, create=True).is_dir()


# --- 5. The ingestion caps: active by default, rejected with their reason ----


def test_the_caps_are_active_by_default() -> None:
    """The assumed divergence with `Guardrails`, whose caps are inactive at None.

    An absent spending cap lets a run cost what it costs, which shows; an absent
    ingestion cap lets a document enter the context in full, and then the
    spending bar lies (docs/24 §3.4).
    """
    defaults = GardeFousIngestion()
    assert defaults.taille_max_source_octets
    assert defaults.taille_max_totale_octets
    assert defaults.nb_max_sources


def test_an_oversized_source_is_rejected() -> None:
    rejection = _rejection(
        [{"type": TYPE_FILE, "nom": "enorme.pdf", "taille": 20 * 1024 * 1024}],
        guardrails=GardeFousIngestion(taille_max_source_octets=1024),
    )
    assert rejection.reason == "source-trop-volumineuse"
    assert rejection.index == 0


def test_the_total_of_the_sources_is_capped() -> None:
    """Without a total cap, fifty files under the per-source cap would all pass."""
    rejection = _rejection(
        [
            {"type": TYPE_FILE, "nom": "a.md", "taille": 600},
            {"type": TYPE_FILE, "nom": "b.md", "taille": 600},
        ],
        guardrails=GardeFousIngestion(taille_max_source_octets=1000, taille_max_totale_octets=1000),
    )
    assert rejection.reason == "ingestion-trop-volumineuse"
    # The index locates the source that makes it overflow: "it is too much"
    # without saying where would force rereading everything to know what to drop.
    assert rejection.index == 1


def test_the_number_of_sources_is_capped() -> None:
    """Checked **before** any resolution: five hundred sources do not touch the disk."""
    rejection = _rejection(
        [{"type": TYPE_URL, "valeur": f"https://a.test/{i}"} for i in range(5)],
        guardrails=GardeFousIngestion(nb_max_sources=3),
    )
    assert rejection.reason == "trop-de-sources"


def test_a_cap_set_to_none_is_a_choice_not_an_oversight() -> None:
    sources = resolve_sources(
        [{"type": TYPE_FILE, "nom": "enorme.pdf", "taille": 10**9}],
        run_id=RUN,
        guardrails=GardeFousIngestion(
            taille_max_source_octets=None, taille_max_totale_octets=None
        ),
    )
    assert sources[0].size == 10**9


def test_a_zero_or_negative_cap_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="nb_max_sources"):
        GardeFousIngestion(nb_max_sources=0)
    with pytest.raises(ValueError, match="taille_max_source_octets"):
        GardeFousIngestion(taille_max_source_octets=-1)


# --- 6. The edge: nothing to ingest, or an unreadable declaration ------------


def test_no_source_keeps_the_behavior_from_before_this_lot() -> None:
    assert resolve_sources(None, run_id=RUN) == ()
    assert resolve_sources([], run_id=RUN) == ()


def test_an_unknown_type_is_rejected_not_ignored() -> None:
    """Ignoring would be worse: the user would believe they attached missing material."""
    assert _rejection([{"type": "texte", "valeur": "..."}]).reason == "type-inconnu"
    assert _rejection([{"nom": "sans-type.md"}]).reason == "type-inconnu"


def test_an_unreadable_declaration_is_rejected() -> None:
    assert _rejection(["https://a.test"]).reason == "source-illisible"
    assert _rejection("https://a.test").reason == "sources-illisibles"


# --- 7. Attachment to the run: event, projection, summary --------------------


def test_the_sources_survive_the_bus_round_trip() -> None:
    """They travel as JSON like the ticket (#187) and the project (#222)."""
    source = Source(type=TYPE_URL, name="Spec", value="https://exemple.test/spec")
    event = Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, sources=[source])
    assert Event.from_json(event.to_json()).sources == [source]


def test_an_event_without_sources_does_not_erase_those_of_the_launch() -> None:
    """`None` says "I don't know", not "there are none left" -- like `etapes` (#246)."""
    state = ControlTowerState()
    source = Source(type=TYPE_DIRECTORY, name="maquettes", path="/refs/maquettes")
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            titre="Reprends le cahier des charges",
            statut="en_cours",
            sources=[source],
        )
    )
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_TERMINEE)
    )
    execution = state.execution(RUN)
    assert execution is not None
    assert execution.resume()["sources"] == [source.to_dict()]


def test_a_run_without_a_source_returns_the_view_from_before_this_lot() -> None:
    state = ControlTowerState()
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, titre="Objectif texte seul")
    )
    execution = state.execution(RUN)
    assert execution is not None
    assert execution.resume()["sources"] == []


# --- 8. The rest, deferred to the final lot (#323) ---------------------------
#
# What #315 had rightly left aside -- these are not rejections, so nothing that
# must be tested with its code. But they are the points through which ingestion
# moves from one machine to another (where the files land) or accepts an input
# that no screen produces (an already resolved `Source`).


def test_the_ingestion_root_is_in_the_repository_unless_the_environment_moves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repository directory by default, movable for whoever stores data elsewhere."""
    monkeypatch.delenv("MAESTRO_INGESTION_DIR", raising=False)
    default = ingestion_root()
    assert default.parts[-2:] == ("core", "ingestion")

    elsewhere = tmp_path / "hors-depot"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(elsewhere))
    assert ingestion_root() == elsewhere


def test_the_root_is_never_created_by_merely_reading_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing is created as long as no file has been uploaded."""
    elsewhere = tmp_path / "jamais-creee"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(elsewhere))
    assert ingestion_root() == elsewhere
    assert not elsewhere.exists()


@pytest.mark.parametrize(
    "identifier",
    ["", "a" * (MAX_RUN_ID_LENGTH + 1), "run 42", "run/42"],
)
def test_a_malformed_run_identifier_is_rejected(identifier: str) -> None:
    """The identifier becomes a **directory name**: whatever is not a word is rejected."""
    with pytest.raises(SourceRejected) as capture:
        ingestion_location(identifier)
    assert capture.value.reason == "run-invalide"


def test_an_already_resolved_source_resolves_identically_again(refs_dir: Path) -> None:
    """Replaying a resolution must not be a special case -- the journal depends on it."""
    (resolved,) = resolve_sources(
        [{"type": TYPE_DIRECTORY, "chemin": str(refs_dir)}], run_id=RUN
    )
    assert resolve_sources([resolved], run_id=RUN) == (resolved,)


@pytest.mark.parametrize("raw", [{"type": TYPE_URL}, 42, object()])
def test_a_list_of_sources_that_is_not_one_is_rejected(raw: object) -> None:
    """A mapping or an integer are not *lists*: the rejection names the whole."""
    assert _rejection(raw).reason == "sources-illisibles"


def test_an_empty_source_recognizes_itself() -> None:
    """A source is empty without a type, or without any landmark -- a name alone is enough.

    A nuance to know before using it as an input check: it is not one
    (`resolve_sources` is), it is the **re-read** filter that drops a journal
    line that tells nothing.
    """
    assert Source(type="", name="Spec", value="https://a.test").empty is True
    assert Source(type=TYPE_URL).empty is True
    assert Source(type=TYPE_URL, name="Rien").empty is False
