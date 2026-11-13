"""The sources of an objective: the model (ticket #315, EF-39, SOURCE entity of docs/03).

A **source** is the place where an objective declares what it carries: the
requirements document to take over, the mockups folder to consult, the
specification page to read. It is a **declared**, typed and bounded input
material, attached to the run next to its objective, and it is the boundary of
what enters the context.

Three types, those of the contract fixed in
([docs/24 §3.2](../../docs/24-projets-locaux-et-poste-de-travail.md)):
`fichier` (uploaded), `dossier` (read-only references) and `url`. The `texte`
that the SOURCE entity of [docs/03](../../docs/03-modele-de-donnees.md)
mentions is not one here: it is the objective itself, which already has its
own field.

**This module reads no content and refuses nothing**: it only carries the
shape. Resolution (canonicalising a directory, an URL's scheme, a file's
ingestion location) and the reasoned refusals live in
`maestro.sources.resolution`; text extraction lives in
`maestro.sources.extraction`.

It is a **leaf**, like `maestro.references` (#187) and `maestro.detail_tache`
(#246), for the same reason: a source travels from the launch
(`maestro.controltower.executions`) to the projection through the events,
layers that cannot depend on one another. `maestro.controltower.events`
re-exports it.

One rule governs reading back, and it is the inverse of resolution's: **nothing
is refused on replay**. `from_dict` and `sources_from` rebuild what was
accepted once without judging it again: a durable journal (#97) read after the
guardrails were tightened must stay readable, and a past run is not rewritten.
The refusal happens **once**, at launch, where someone can still fix their
input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: An uploaded file: its material is copied into the run's ingestion location
#: (never into the user's project; see `resolution`).
TYPE_FILE = "fichier"
#: A directory of **references** on disk: consulted, never written
#: (`read_only`). It is a reference, not a project (docs/03, SOURCE).
TYPE_DIRECTORY = "dossier"
#: A page to read, `http(s)` only (see `resolution`).
TYPE_URL = "url"

#: The only admitted types. An unknown type is **refused** at resolution rather
#: than ignored: a source that cannot be resolved is material the user believes
#: attached and that would never arrive.
SOURCE_TYPES: tuple[str, ...] = (TYPE_FILE, TYPE_DIRECTORY, TYPE_URL)

#: Maximum length of a source name: the bound of a file name on the usual
#: filesystems (NTFS, ext4, APFS). Beyond it, it is no longer a name but a paste,
#: and writing would fail at ingestion anyway.
MAX_NAME_LENGTH = 255


class SourceRejected(ValueError):
    """A rejected source, **with its reason**: never a silent rejection (#315).

    Same contract as `RacineRefusee` (#221), whose reasons it takes over when the
    refusal comes from root validation: `reason` is a stable, short code
    (`type-inconnu`, `url-non-suivable`, `source-trop-volumineuse`,
    `chemin-sensible`…), the message remaining the readable sentence. `index`
    locates the faulty source in the submitted list: without it, "a source is too
    big" does not tell anyone which one to remove.

    It inherits from `ValueError` on purpose: the launch route
    (`POST /api/executions`) already turns it into a **422** through the branch
    that exists for out-of-bounds guardrails: a rejected source is an invalid
    request, not a failure.
    """

    def __init__(self, reason: str, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.index = index


@dataclass(frozen=True)
class Source:
    """An input material declared by an objective (EF-39, SOURCE entity).

    `type` is one of `SOURCE_TYPES`. The other fields are filled according to the
    type, and it is resolution that fills them:

    - `name`: the displayable label (name of the file, directory or page);
    - `path`: the **resolved** path of a `fichier` (inside the run's ingestion
      location) or of a `dossier` (canonicalised, on the user's disk); empty for
      a `url`;
    - `value`: the URL of a `url` source; empty for the other two. Two fields
      rather than a single "where", because they are not the same namespaces: a
      path is compared, contained and canonicalised, a URL is not;
    - `size`: the size in **bytes** when known (a file), `None` otherwise (a
      directory is not measured, a URL not yet fetched). It is what the ingestion
      guardrails cap;
    - `read_only`: Maestro **never** writes into a source. True by default, and
      **forced** to true on a `dossier`: it is a reference, not a project
      (docs/03), and declaring it writable would be a disguised project that
      passed none of the Phase 7 checks.

    Immutable: what is resolved is not touched up, it is resolved again.
    """

    type: str
    name: str = ""
    path: str = ""
    value: str = ""
    size: int | None = None
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Re-emits the source as a JSON-serialisable dict (the shape of contract #183)."""
        return {
            "type": self.type,
            "nom": self.name,
            "chemin": self.path,
            "valeur": self.value,
            "taille": self.size,
            "lecture_seule": self.read_only,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Source:
        """Rebuilds a source from its `to_dict` shape (JSON round trip).

        **Judges nothing again** (see the module docstring): it is the reading
        back of an already accepted source (bus event, replayed durable journal),
        not an input. Missing keys fall back to the defaults.
        """
        size = data.get("taille")
        return cls(
            type=str(data.get("type") or ""),
            name=str(data.get("nom") or ""),
            path=str(data.get("chemin") or ""),
            value=str(data.get("valeur") or ""),
            size=size if isinstance(size, int) and not isinstance(size, bool) else None,
            read_only=bool(data.get("lecture_seule", True)),
        )

    @property
    def empty(self) -> bool:
        """Does the source tell nothing (neither type nor location)?"""
        return not self.type or not (self.name or self.path or self.value)


def sources_from(data: Any) -> list[Source]:
    """The readable sources of a raw value: an **empty list** when there are none.

    The counterpart of `etapes_depuis` (#246): the order is the stream's (a list
    of attachments reads in the order it was composed), and what tells nothing
    is dropped entry by entry: an unreadable line does not lose the others. Like
    `from_dict`, it is **reading back**: nothing is refused here.
    """
    read = (Source.from_dict(raw) for raw in _sequence(data) if isinstance(raw, Mapping))
    return [source for source in read if not source.empty]


def sources_as_list(sources: Sequence[Source] | None) -> list[dict[str, Any]]:
    """The JSON shape of a list of sources: `[]` when there are none.

    The counterpart of `ticket_en_dict` (#187) and `etapes_en_liste` (#246): on
    the client side, an empty list says "no source".
    """
    return [source.to_dict() for source in sources or ()]


def _sequence(data: Any) -> tuple[Any, ...]:
    """The entries of a raw value supposed to be a list: `()` otherwise.

    A string is a `Sequence`: excluding it explicitly avoids reading
    "https://…" as a list of characters, each giving an empty source.
    """
    if isinstance(data, Sequence) and not isinstance(data, str | bytes):
        return tuple(data)
    return ()
