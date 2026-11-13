"""The ingestion chain in one gesture: declare, resolve, attach (ticket #482).

Three modules each know half of the path: `upload` completes a
`{"type": "fichier", "id": …}` reference, `resolution` canonicalises and
refuses, `upload` again copies the bytes where resolution has just said they go.
Nobody else carries the sequence, which is what this module does.

A launch is not the only gesture able to carry sources: the chat thread is a
second one (#482, lot 1 of #481). A message can carry files, a directory, an
address, **resolved by the existing ingestion chain, never by a second one**.
Copying the three steps into `ServiceChat` would give two similar chains, and
the one forgetting a cap would be the hole: the lesson is already written in
`file_name` (#317), the single sanitiser of resolution and upload precisely
because "two sanitisers written separately would diverge".

A module of its own rather than one more function in `resolution`: composition
needs the uploads **store**, and `upload` already imports `resolution`. Putting it
there would invert the dependency; here, both are upstream and nothing loops.

This module invents **no guardrail** and relaxes none: it calls, in order, what
#315 and #317 wrote. The refusals that come out are theirs, with their reason and
their index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources.model import Source
from maestro.sources.resolution import resolve_sources
from maestro.sources.upload import UploadStore, declare_uploads


def compose_sources(
    raw_sources: Sequence[Mapping[str, Any] | Source] | None,
    *,
    key: str,
    store: UploadStore,
    guardrails: GardeFousIngestion | None = None,
    project_root: Path | str | None = None,
) -> tuple[Source, ...]:
    """The material of `raw_sources`, resolved and its bytes attached: `()` when there is none.

    Three steps, and their order is the content of the function (it comes from
    `ServiceExecutions._composer`, #317, of which it is the extraction):

    1. **Complete**: a `{"type": "fichier", "id": …}` says neither name nor size;
       the store gives them, and they are those of the **bytes received**, not
       those a client announces. Without it the per-source cap could be bypassed
       by declaring twelve bytes.
    2. **Resolve** (#315): canonicalisation, forbidden roots, caps. It is the
       only place that refuses, and it refuses **before** any write: nothing that
       follows must leave a caller half enrolled.
    3. **Attach**: resolution has just computed *where* the material must be; the
       bytes are copied there. This order is forced: the ingestion location
       depends on the `key`, which does not exist at upload.

    `key` names the **ingestion location**: one directory per act, and not a
    shared one: it is what allows saying "this material belongs to this run / to
    this message", finding it in a trace and collecting it without touching the
    neighbour's. A `run_id` for a launch, the message identifier for a thread;
    `resolution.RUN_ID_PATTERN` bounds its shape, the function knows nothing of
    its origin.

    A source declared **without** `id` crosses the three steps without bytes: it
    will come out `ignore` / `source-absente` in the reading report, which is
    exactly what we want it to say.

    Raises `SourceRejected` (hence `ValueError`): the routes already turn it into
    a reasoned 422, index included.
    """
    declared, identifiers = declare_uploads(raw_sources, store=store)
    material = resolve_sources(
        declared, run_id=key, guardrails=guardrails, project_root=project_root
    )
    for source, identifier in zip(material, identifiers, strict=False):
        if identifier:
            store.attach(identifier, Path(source.path))
    return material
