"""The sources of an objective — input material declared, resolved, read and bounded.

Foundation of **Phase 8** ([docs/24 §3](../../docs/24-projets-locaux-et-poste-de-travail.md),
parent #314): a launch used to carry only a text objective, it now carries what
that objective brings along — uploaded files, a reference directory, URLs
(EF-39, SOURCE entity of [docs/03](../../docs/03-modele-de-donnees.md)).

Seven modules, seven responsibilities that do not mix:

- `model` — the **shape** (`Source`, `SourceRejected`), leaf module, reread
  without ever being rejudged: a replayed durable journal does not refuse
  itself;
- `resolution` (#315) — what a declaration **becomes** against the disk, the
  network and the caps of the run, and what makes it rejected **with its
  reason**;
- `extraction` (#316) — what a source **says**: everything brought back to
  Markdown, whatever the format (#1163), with its reading report and its cost in
  tokens, and framed as **data** before entering a context;
- `images` (#1163) — what an image **shows**: recognized by its bytes, looked at
  by the workstation's model, and rendered as Markdown at extraction — or named
  with the reason why nobody looked at it;
- `upload` (#317) — where received bytes **wait** for their run, and how they are
  attached to it. A browser delivers no path, it delivers bytes: this is the only
  way a `fichier` source can designate real ones;
- `preview` (#319) — what the sources **would give**, played dry: the same
  reading, rendered before launch and keeping nothing, so that composing an
  objective stays reversible as long as it is free;
- `composition` (#482) — the **chaining** of the three previous ones (declare,
  resolve, attach), which lived privately in the launch as long as it was the
  only gesture able to carry sources. The chat thread is a second one: they call
  the same chain, and that is the literal meaning of the "never through a
  second one" criterion.

The two regimes are opposed on purpose: the resolution **rejects** (an input is
corrected), the extraction **ignores or truncates while saying so** (a content
is not yet known to whoever attached it). The upload follows that of the
resolution: what exceeds a cap is rejected **during** reception, never
truncated.

The routes that serve them — `POST /api/sources` and `POST /api/executions` — are
in [§6.8 and §6.1 of docs/05](../../docs/05-interface-control-tower.md).
"""

from maestro.sources.composition import compose_sources
from maestro.sources.extraction import (
    CONVERTED_EXTENSIONS,
    HTML_EXTENSIONS,
    STATE_IGNORED,
    STATE_READ,
    STATE_TRUNCATED,
    STATES,
    ExtractionGuardrails,
    Reading,
    ReadingReport,
    default_reader,
    estimate_tokens,
    extract_sources,
    markdown_context,
)
from maestro.sources.images import (
    REASON_SEEN_AT_LAUNCH,
    REASON_VISION_UNAVAILABLE,
    ImageNotSeen,
    ModelImageReader,
    ReadImage,
    image_type,
    read_image_in_preview,
)
from maestro.sources.model import (
    MAX_NAME_LENGTH,
    SOURCE_TYPES,
    TYPE_DIRECTORY,
    TYPE_FILE,
    TYPE_URL,
    Source,
    SourceRejected,
    sources_as_list,
    sources_from,
)
from maestro.sources.preview import PREVIEW_RUN, preview_sources
from maestro.sources.resolution import (
    MAX_RUN_ID_LENGTH,
    MAX_URL_LENGTH,
    RUN_ID_PATTERN,
    file_name,
    ingestion_location,
    ingestion_root,
    resolve_sources,
)
from maestro.sources.upload import (
    UPLOAD_ID_PATTERN,
    UPLOADS_DIRECTORY,
    Upload,
    UploadStore,
    declare_uploads,
)

__all__ = [
    "CONVERTED_EXTENSIONS",
    "HTML_EXTENSIONS",
    "MAX_NAME_LENGTH",
    "MAX_RUN_ID_LENGTH",
    "MAX_URL_LENGTH",
    "PREVIEW_RUN",
    "REASON_SEEN_AT_LAUNCH",
    "REASON_VISION_UNAVAILABLE",
    "RUN_ID_PATTERN",
    "SOURCE_TYPES",
    "STATES",
    "STATE_IGNORED",
    "STATE_READ",
    "STATE_TRUNCATED",
    "TYPE_DIRECTORY",
    "TYPE_FILE",
    "TYPE_URL",
    "UPLOADS_DIRECTORY",
    "UPLOAD_ID_PATTERN",
    "ExtractionGuardrails",
    "ImageNotSeen",
    "ModelImageReader",
    "ReadImage",
    "Reading",
    "ReadingReport",
    "Source",
    "SourceRejected",
    "Upload",
    "UploadStore",
    "compose_sources",
    "declare_uploads",
    "default_reader",
    "estimate_tokens",
    "extract_sources",
    "file_name",
    "image_type",
    "ingestion_location",
    "ingestion_root",
    "markdown_context",
    "preview_sources",
    "read_image_in_preview",
    "resolve_sources",
    "sources_as_list",
    "sources_from",
]
