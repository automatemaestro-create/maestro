"""The ingestion preview: what sources would give, **without committing anything** (#319).

Criterion 2 of the *compose an objective* screen asks for the extraction to be
**visible before launching**: per source, read / ignored (with the reason) /
truncated, and the estimated cost. This is what keeps the gesture reversible
**while it is free**: fixing an input costs a click, fixing twelve tasks costs
twelve executions
([docs/24 §3.4](../../docs/24-projets-locaux-et-poste-de-travail.md)).

Now, the reading report (#316) is returned by the **launch** (docs/05 §6.1), that
is, by the paying gesture. The same reading, played empty, is what this module
is, and it fits in one sentence: we **resolve** like the launch (#315), we
**read** like the launch (#316), and we **erase**.

**A preview deposits nothing**, and that is its whole contract. The bytes of a
dropped file are written into a **disposable** directory, read, then removed in
the `finally`: never in `core/ingestion/`, never in the upload store (#317),
never in the user's project. Two reasons, and the second is the real one:

- answering "this document is worth 4,200 tokens" must leave **nothing** behind:
  otherwise looking twice would fill the disk with material nobody claims, and a
  collection would have to be invented for a gesture that is itself free;
- the preview and the launch stay **independent**. The upload store exists so
  that material **survives** until the run that consumes it; a preview survives
  nothing. Mixing them would make a question ("what does this give?") depend on
  a storage that only makes sense for the next answer.

The disposable directory therefore goes into the system's temporary directory and
not under `.maestro/`: the repository rule (CLAUDE.md) reserves
`.maestro/<domain>/` for **what a human is invited to re-read**, and nobody will
re-read a buffer erased three lines below.

The refusal regime is resolution's, not extraction's: a faulty input is
**refused** with its reason and index (`SourceRejected`, hence a reasoned 422),
where an unreadable content is only an "ignored" line of the report. This is
intended, and it is the heart of criterion 3 of the screen: "nothing to read
here" and "I refuse to read this" never display the same.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO, Any

from maestro.config import Settings, load_settings
from maestro.emplacements import jetable
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources.extraction import ExtractionGuardrails, ReadingReport, extract_sources
from maestro.sources.images import read_image_in_preview
from maestro.sources.model import TYPE_FILE, Source, SourceRejected
from maestro.sources.resolution import ingestion_location, resolve_sources

#: The `run_id` of a preview. A preview is nobody's run, but `ingestion_location`
#: requires one: it is what gives the dropped file the **same** relative path as
#: at launch, hence the same resolution and the same refusals. The name is fixed:
#: the directory that carries it is unique and disposable.
PREVIEW_RUN = "apercu"

#: Size of the read chunks of an uploaded stream. The bytes are checked against
#: the caps **during** writing (like #317 on reception): a half-written file is
#: not a source but a trap: the report would call it "read" and the screen would
#: conclude on an amputated document.
CHUNK_SIZE = 64 * 1024


def preview_sources(
    declarations: Sequence[Mapping[str, Any]] | None,
    *,
    files: Sequence[tuple[str, IO[bytes]]] = (),
    guardrails: GardeFousIngestion | None = None,
    extraction_guardrails: ExtractionGuardrails | None = None,
    settings: Settings | None = None,
    fetch_url: Callable[[str], str] | None = None,
) -> ReadingReport:
    """The reading report of `declarations`, **played empty**: nothing is kept.

    `declarations` is the list of sources in **the order the screen composed
    them**: this order decides what enters when the token budget runs out (#316),
    so a preview that changed it would lie about the launch it announces.

    `files` carries the dropped bytes, as `(name, stream)` pairs. The n-th one
    matches the n-th declaration of type `fichier`: a multipart carries no
    correspondence, it carries two ordered lists. Counting right is therefore the
    caller's job, and a wrong count is **refused** (`apercu-sans-octets`) rather
    than matched at random: pairing the wrong document with the wrong name would
    produce a credible and false report.

    Raises `SourceRejected` (hence `ValueError`) on a refused input, exactly like
    the launch: same caps (`GardeFousIngestion`), same reasons, same index. What
    is not refused but unreadable comes out as an "ignored" line of the report,
    extraction never raising.
    """
    guardrails = guardrails if guardrails is not None else GardeFousIngestion()
    entries = list(declarations or ())
    expected = sum(1 for raw in entries if _is_file(raw))
    if expected != len(files):
        raise SourceRejected(
            "apercu-sans-octets",
            f"Aperçu incomplet : {expected} source(s) de type fichier déclarée(s) "
            f"pour {len(files)} fichier(s) reçu(s).",
        )

    # The disposable directory is created **before** resolution: it serves as the
    # ingestion root, so it must exist when `ingestion_location` canonicalises
    # (`chemin_dans_racine`, #221).
    root = jetable("maestro-apercu-")
    try:
        effective_settings = replace(settings or load_settings(), ingestion_dir=str(root))
        sources = resolve_sources(
            entries, run_id=PREVIEW_RUN, guardrails=guardrails, settings=effective_settings
        )
        if files:
            ingestion_location(PREVIEW_RUN, settings=effective_settings, create=True)
            _deposit(sources, files, guardrails=guardrails)
        # Images are **not** shown to the model (#1163): looking is a paid call,
        # and the preview is free. They come out named, with what the launch will
        # do (`vue-au-lancement`), rather than paid in advance.
        return extract_sources(
            sources,
            guardrails=extraction_guardrails,
            fetch_url=fetch_url,
            read_image=read_image_in_preview,
        )
    finally:
        # `ignore_errors`: a preview that cannot do its cleanup must not turn a
        # returned answer into a failure. What would remain carries the API's pid
        # in its name: the collection takes it when it dies (#1455).
        shutil.rmtree(root, ignore_errors=True)


def _is_file(raw: Any) -> bool:
    """Does the declaration `raw` announce an uploaded file?

    Read on the **raw** declaration and not on the resolved source: the count
    happens before resolution, without which a badly paired preview would only be
    detected after writing to disk.
    """
    return isinstance(raw, Mapping) and str(raw.get("type") or "").strip().lower() == TYPE_FILE


def _deposit(
    sources: Sequence[Source],
    files: Sequence[tuple[str, IO[bytes]]],
    *,
    guardrails: GardeFousIngestion,
) -> None:
    """Writes the dropped bytes at the location resolution gave them.

    The caps are checked **chunk by chunk** rather than on the announced size:
    the latter comes from the browser, that is, from outside. An overrun
    interrupts the writing and refuses: the partial file goes away with the
    disposable directory, in the caller's `finally`.
    """
    targets = [
        (index, source)
        for index, source in enumerate(sources)
        if source.type == TYPE_FILE
    ]
    total = 0
    for (index, source), (_, stream) in zip(targets, files, strict=True):
        total += _write(
            Path(source.path), stream, index=index, already=total, guardrails=guardrails
        )


def _write(
    destination: Path,
    stream: IO[bytes],
    *,
    index: int,
    already: int,
    guardrails: GardeFousIngestion,
) -> int:
    """Writes `stream` to `destination` under the caps: returns the number of bytes written."""
    source_cap = guardrails.taille_max_source_octets
    total_cap = guardrails.taille_max_totale_octets
    written = 0
    with destination.open("wb") as output:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                return written
            written += len(chunk)
            if source_cap is not None and written > source_cap:
                raise SourceRejected(
                    "source-trop-volumineuse",
                    f"Source {index + 1} trop volumineuse : plus de {source_cap} "
                    f"octets reçus, {source_cap} au maximum.",
                    index=index,
                )
            if total_cap is not None and already + written > total_cap:
                raise SourceRejected(
                    "ingestion-trop-volumineuse",
                    f"Sources trop volumineuses au total : plus de {total_cap} "
                    f"octets cumulés à la source {index + 1}, {total_cap} au maximum.",
                    index=index,
                )
            output.write(chunk)
