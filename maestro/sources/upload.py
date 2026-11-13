"""The store of uploaded sources: receive bytes, then attach them to a run (#317).

The link between a browser and the sources model. A browser **never delivers an
absolute path**, it delivers bytes; the `chemin` of a `fichier` source is
therefore something the backend **computes** (#315, `ingestion_location`), never
something a client declares. A place to put these bytes was needed, and an
identifier to find them again: this module, served by `POST /api/sources`
([docs/05 §6.8](../../docs/05-interface-control-tower.md)).

**Two steps, and that is the point.** Uploading drops the material into a store
**outside any project and any run**; launching **attaches** it to the run, in its
own ingestion location. The separation is not plumbing:

- it allows **composing** an objective (dropping, seeing, removing a document)
  before spending anything;
- it guarantees uploaded material **never** lands in the user's directory, for
  the reason that already forbids agents to write into the root (EF-36): an
  untrusted input ([docs/19 §2](../../docs/19-securite-modele-de-menace.md)) does
  not mix with the files the user wrote;
- attaching **copies** rather than moves: relaunching the same objective after a
  failure must not require uploading again. The downside is accepted: the bytes
  stay in the store until someone removes them (`forget`); nothing collects
  them.

**Nothing is truncated on entry.** `receive` reads in chunks and checks the
ingestion caps (ENF-07, `GardeFousIngestion`) **during** reading: at the first
overrun, what was written is erased and the refusal leaves with its reason. This
is resolution's regime (#315), not extraction's (#316): an input can be fixed,
so it is refused; a half-received file is not a source but a trap: the reading
report would call it "read" and the brief would conclude on an amputated
document.

No FastAPI dependency here: the store receives any **binary stream**, which
makes it testable without an HTTP request and reusable by the CLI.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from maestro.config import Settings
from maestro.engine.guardrails import GardeFousIngestion
from maestro.projets.racine import RacineRefusee, chemin_dans_racine
from maestro.sources.model import TYPE_FILE, SourceRejected
from maestro.sources.resolution import file_name, ingestion_root

#: The subdirectory of the ingestion storage where bytes wait for their run.
#: The underscore is not cosmetic: `RUN_ID_PATTERN` (#315) requires an
#: **alphanumeric** first character, so no `run_id` can ever designate this
#: directory. A collision between the uploads' space and the runs' is therefore
#: impossible by construction, not merely improbable.
UPLOADS_DIRECTORY = "_televersements"

#: An upload identifier admissible as a **path segment**. Same precaution as
#: `RUN_ID_PATTERN` and `ID_PROJET`: the identifier comes back from a client, and
#: nothing non-conforming must be able to climb a level.
UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{6,64}\Z")

#: Size of the read chunks (64 KiB). Reading in chunks is what allows refusing
#: **during** reception rather than after: a 2 GiB file must not first be written
#: to disk to then be judged too big.
CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Upload:
    """Bytes received and stored, waiting for a run.

    `id` is what the route returns to the client and what it will carry into the
    `sources[]` of its launch; `name` is the **sanitised** name (the browser's
    only serves as a proposal); `size` counts the bytes **received**, never those
    announced; `path` says where they are laid, in the store and not yet in a run.
    """

    id: str
    name: str
    size: int
    path: Path

    def to_dict(self) -> dict[str, Any]:
        """The shape returned by `POST /api/sources` (docs/05 §6.8).

        Without the `path`: it is a server implementation detail, and the client
        has no use for it: it designates its material by its `id`.
        """
        return {"id": self.id, "type": TYPE_FILE, "nom": self.name, "taille": self.size}

    def declaration(self) -> dict[str, Any]:
        """The source declaration that the launch will have resolved by #315.

        The name and size come **from the store** and not from the request: they
        are those of the bytes actually received. A client announcing 12 bytes for
        a 12 MiB file would otherwise pass the per-source cap.
        """
        return {"type": TYPE_FILE, "nom": self.name, "taille": self.size}


class UploadStore:
    """The store of received bytes: receive them, read them back, attach them to a run.

    `root` is the directory that carries them (`<ingestion>/_televersements/` by
    default, see `default`); `guardrails` carries the ingestion caps applied on
    reception: active by default, adjustable by injection: it is a deployment
    setting, not a choice of the uploading client.
    """

    def __init__(self, root: Path | str, *, guardrails: GardeFousIngestion | None = None) -> None:
        self._root = Path(root)
        self._guardrails = guardrails if guardrails is not None else GardeFousIngestion()

    @classmethod
    def default(
        cls, settings: Settings | None = None, *, guardrails: GardeFousIngestion | None = None
    ) -> UploadStore:
        """The configuration's store: `<MAESTRO_INGESTION_DIR|core/ingestion>/_televersements/`.

        Nothing is created here: a deployment where nobody uploads creates no
        directory, like `ingestion_root` whose pattern it inherits.
        """
        return cls(ingestion_root(settings) / UPLOADS_DIRECTORY, guardrails=guardrails)

    @property
    def guardrails(self) -> GardeFousIngestion:
        """The caps applied: the route needs them to bound the **count**."""
        return self._guardrails

    def receive(
        self, name: str, stream: IO[bytes], *, index: int = 0, already_received: int = 0
    ) -> Upload:
        """Stores the bytes of `stream` under a new identifier and returns the upload.

        `name` is the name proposed by the client: it is **sanitised** by the same
        `file_name` as resolution (#315): no separator, no `..`, no forbidden
        character, never copied as is. Two sanitisers would diverge, and that is
        the classic vector of directory traversal.

        `index` locates the file in the call (a form drops several) and travels
        to the refusal: "a file is too big" without saying which would force
        starting over. `already_received` carries the total of files already
        accepted in the **same** call, without which the total cap would never be
        reached: twenty files under the per-file cap would all pass.

        Raises a reasoned `SourceRejected` (`nom-invalide`,
        `source-trop-volumineuse`, `ingestion-trop-volumineuse`…) and **leaves
        nothing behind**: what was written is erased before the refusal leaves.
        """
        clean = file_name(name, index=index)
        identifier = uuid.uuid4().hex[:12]
        directory = self._root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / clean
        try:
            size = self._write(stream, destination, index=index, already_received=already_received)
        except BaseException:
            # `BaseException` and not `Exception`: a cancellation of the task
            # carrying the copy (client disconnected, app shutdown) would
            # otherwise leave a truncated file in the store, indistinguishable
            # from a complete upload: precisely what this module refuses.
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return Upload(id=identifier, name=clean, size=size, path=destination)

    def read(self, upload_id: str) -> Upload | None:
        """The upload `upload_id`, or **None** if it designates none.

        None rather than an exception: the caller knows which refusal it is:
        `televersement-inconnu` at the source's index, at launch. A
        non-conforming identifier is treated as unknown **without touching the
        disk**: it does not have to become a path to be discarded.
        """
        if not UPLOAD_ID_PATTERN.match(upload_id or ""):
            return None
        try:
            directory = chemin_dans_racine(self._root, upload_id)
        except RacineRefusee:  # pragma: no cover - the pattern above already bounds it
            return None
        if not directory.is_dir():
            return None
        # One directory, one file: the upload's name is that of the file it
        # contains, which avoids a metadata file to write, read and desynchronise.
        files = sorted(entry for entry in directory.iterdir() if entry.is_file())
        if not files:
            return None
        file = files[0]
        return Upload(
            id=upload_id,
            name=file.name,
            size=file.stat().st_size,
            path=file,
        )

    def attach(self, upload_id: str, destination: Path | str) -> Path:
        """Copies the bytes of `upload_id` to `destination` (the run's location).

        The receiving directory is created along the way: `ingestion_location`
        (#315) only **computes** the path unless asked to write, and attaching is
        precisely the moment where one writes.

        Raises `SourceRejected("televersement-inconnu")` if the identifier
        designates nothing: never a silently skipped copy, which would leave a run
        working on absent material.
        """
        uploaded = self.read(upload_id)
        if uploaded is None:
            raise SourceRejected(
                "televersement-inconnu",
                f"Aucun téléversement sous l'identifiant {upload_id!r}.",
            )
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(uploaded.path, target)
        return target

    def forget(self, upload_id: str) -> None:
        """Removes an upload from the store: best-effort, silent if it is gone.

        The gesture that makes a call **atomic**: if a form's third file overflows
        the total cap, the first two do not have to stay in the store under
        identifiers nobody received.
        """
        if not UPLOAD_ID_PATTERN.match(upload_id or ""):
            return
        shutil.rmtree(self._root / upload_id, ignore_errors=True)

    def _write(
        self, stream: IO[bytes], destination: Path, *, index: int, already_received: int
    ) -> int:
        """Writes `stream` to `destination` in chunks, caps applied **while writing**.

        The cap is checked at each chunk and not at the end: it is what
        distinguishes "refused" from "received then thrown away", and what avoids
        a multi-gigabyte file being entirely written to then be declared too big.
        """
        per_source = self._guardrails.taille_max_source_octets
        total = self._guardrails.taille_max_totale_octets
        received = 0
        with destination.open("wb") as output:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
                if per_source is not None and received > per_source:
                    raise SourceRejected(
                        "source-trop-volumineuse",
                        f"Fichier {index + 1} trop volumineux : plus de {per_source} octets "
                        "reçus — la réception est interrompue, rien n'est conservé.",
                        index=index,
                    )
                if total is not None and already_received + received > total:
                    raise SourceRejected(
                        "ingestion-trop-volumineuse",
                        f"Téléversement trop volumineux au total : plus de {total} octets "
                        f"cumulés au fichier {index + 1}.",
                        index=index,
                    )
                output.write(chunk)
        return received


def declare_uploads(
    raw_sources: Any, *, store: UploadStore
) -> tuple[Any, tuple[str, ...]]:
    """Replaces the `{"type": "fichier", "id": …}` references with what the store knows.

    The switch between the two shapes a `fichier` source can take (docs/05 §6.1):
    the **reference** to an upload (`id`), and the bare declaration of
    [docs/24 §3.2](../../docs/24-projets-locaux-et-poste-de-travail.md)
    (`nom` + `taille`, no bytes). The first is completed here (name and size
    **from the store**, never the client's), the second passes as is: it will
    resolve then come out `ignore` / `source-absente` in the reading report,
    which is exactly what the report exists to say.

    Returns the completed array and, **aligned on its indexes**, the upload
    identifier of each entry (empty string when there is none): it is what will
    allow attaching the bytes once the run's path is known.

    What is not a list comes out **unchanged**: the refusal of shape belongs to
    `resolve_sources`, which already gives it a reason (`sources-illisibles`);
    duplicating it here would give two messages for one fault.
    """
    if (
        raw_sources is None
        or not isinstance(raw_sources, Sequence)
        or isinstance(raw_sources, str | bytes)
    ):
        return raw_sources, ()
    declared: list[Any] = []
    identifiers: list[str] = []
    for index, raw in enumerate(raw_sources):
        identifier = _uploaded_id(raw)
        if not identifier:
            declared.append(raw)
            identifiers.append("")
            continue
        uploaded = store.read(identifier)
        if uploaded is None:
            raise SourceRejected(
                "televersement-inconnu",
                f"Source {index + 1} : aucun téléversement sous l'identifiant "
                f"{identifier!r} — il a peut-être déjà été retiré.",
                index=index,
            )
        declared.append(uploaded.declaration())
        identifiers.append(identifier)
    return declared, tuple(identifiers)


def _uploaded_id(raw: Any) -> str:
    """The upload `id` of a `sources[]` entry: empty if it carries none."""
    if not isinstance(raw, Mapping):
        return ""
    if str(raw.get("type") or "").strip().lower() != TYPE_FILE:
        return ""
    return str(raw.get("id") or "").strip()
