"""Resolution of an objective's sources and ingestion guardrails (ticket #315, EF-39).

The active counterpart of `maestro.sources.model`: what a **declared** source
becomes once confronted with the disk, the network and the run's caps. Three
resolutions, one per type, and a shared principle: **a refusal carries its
reason**, never a silence nor a `False` (same contract as `RacineRefusee`, #221):

- **dossier**: goes through `valider_racine` (#221), the existing root
  validation: canonicalisation (`..` flattened, links followed) then
  confrontation with the list of **forbidden roots** (disk root, bare user
  directory, `.ssh`/`AppData`, system directories, Maestro's repository). A
  references directory is a path on the user's disk: there is no reason for it to
  be less controlled than a project root (EF-38), and two guardrails written
  separately would diverge. It comes out **read-only**, forced: a reference is
  not a project;
- **url**: `http(s)` and nothing else, reusing `SCHEMAS_URL`
  (`maestro.references`, #187) rather than a second list: a `file://` would read
  the disk through a door nobody controlled, a `javascript:` is not an address;
- **fichier**: it **never** lands in the user's project but in an **ingestion
  location of the run's own** (`core/ingestion/<run_id>/`, or
  `MAESTRO_INGESTION_DIR`). The reason is the one that forbids agents to write
  into the root (EF-36): uploaded material is an **untrusted input**
  ([docs/19 §2](../../docs/19-securite-modele-de-menace.md)), and mixing it with
  the project's files would make it indistinguishable from what the user wrote.

On top of that, the **ingestion caps** (`GardeFousIngestion`, ENF-07): size per
source, total size, number of sources. They live with the run's other limits, in
`maestro.engine.guardrails`, not here: a third place to read "what this run
allows itself" would be one more place to forget.

**Nothing is read, nothing is written.** This module declares, resolves and
refuses; it copies no byte and creates no directory unless asked
(`ingestion_location(..., create=True)`, which the upload calls).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.emplacements import depot_configuration
from maestro.engine.guardrails import GardeFousIngestion
from maestro.projets.racine import RacineRefusee, chemin_dans_racine, valider_racine
from maestro.references import SCHEMAS_URL
from maestro.sources.model import (
    MAX_NAME_LENGTH,
    SOURCE_TYPES,
    TYPE_DIRECTORY,
    TYPE_FILE,
    TYPE_URL,
    Source,
    SourceRejected,
)

#: Run identifier admissible as an ingestion **directory name**. Same purpose as
#: `ID_PROJET` (#222): the `run_id` comes from elsewhere (`uuid4().hex[:12]`
#: today, a journal resumption tomorrow) and serves as a path segment: nothing
#: non-conforming must be able to climb a level.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\Z")

#: Length beyond which a `run_id` is no longer one (same bound as the project's
#: other identifiers: `maestro.appartenance.LONGUEUR_MAX_ID`).
MAX_RUN_ID_LENGTH = 64

#: Maximum length of a source URL. The practical bound of browsers and servers;
#: beyond it, it is no longer an address that was pasted.
MAX_URL_LENGTH = 2048

#: Characters an uploaded file name cannot carry. Separators are in (a name is
#: not a path), and with them the Windows forbidden ones, including `:`, which
#: would open an NTFS **alternate data stream** (`note.md:cache`), that is, a
#: content invisible in a listing.
_FORBIDDEN_NAME_CHARACTERS = frozenset('/\\<>:"|?*')


def ingestion_root(settings: Settings | None = None) -> Path:
    """The ingestion root: `MAESTRO_INGESTION_DIR`, otherwise the configuration's `ingestion/`.

    Same pattern as the other stores (`maestro.projets.store`,
    `maestro.agents.store`): the `core/` of a clone, the workstation state of an
    installation (`maestro.emplacements.racine_configuration`, #1620), movable by
    the environment for whoever keeps their data elsewhere. Nothing is created
    here: the root may not exist as long as no file was uploaded.
    """
    settings = settings or load_settings()
    if settings.ingestion_dir:
        return Path(settings.ingestion_dir)
    return depot_configuration("ingestion")


def ingestion_location(
    run_id: str,
    *,
    settings: Settings | None = None,
    project_root: Path | str | None = None,
    create: bool = False,
) -> Path:
    """The ingestion location **of the run's own** `run_id`: one directory per run.

    One directory per run rather than a shared one: it is what allows saying
    "this material belongs to this execution", finding it in a trace and
    collecting it without touching a neighbouring run's.

    `project_root` makes the criterion's promise hold: an uploaded file is stored
    **outside the project root**. The check is active: it is not enough for the
    default to be outside the project, a `MAESTRO_INGESTION_DIR` set inside a
    project must be refused rather than obeyed.

    `create=True` creates the directory (what the upload does, #317); by default
    the function only **computes**.
    """
    identifier = str(run_id or "").strip()
    if (
        not identifier
        or len(identifier) > MAX_RUN_ID_LENGTH
        or not RUN_ID_PATTERN.match(identifier)
    ):
        raise SourceRejected(
            "run-invalide",
            f"Identifiant de run inutilisable comme dossier d'ingestion : {run_id!r}.",
        )
    root = ingestion_root(settings)
    # `chemin_dans_racine` (#221) rather than a `root / identifier`: the pattern
    # above already bounds the identifier, but the second half of EF-38 is
    # written once and for all and can be re-read: the day the pattern changes,
    # it is the one still holding.
    location = chemin_dans_racine(root, identifier)
    if project_root is not None:
        _refuse_inside_project(location, project_root)
    if create:
        location.mkdir(parents=True, exist_ok=True)
    return location


def resolve_sources(
    raw_sources: Any,
    *,
    run_id: str,
    guardrails: GardeFousIngestion | None = None,
    project_root: Path | str | None = None,
    settings: Settings | None = None,
) -> tuple[Source, ...]:
    """The sources of `raw_sources`, **resolved and bounded**: `()` when there are none.

    The order is the input's: a list of attachments is read back in the order it
    was composed. Raises `SourceRejected` (hence `ValueError`, which the launch
    route already turns into a 422) at the **first** faulty source, locating it by
    its index: refusing in bulk at the end would force re-reading everything to
    know what to remove.

    The caps are those of `guardrails` (`GardeFousIngestion()` by default, hence
    active). The count is checked **before** any resolution: five hundred sources
    are refused without touching the disk five hundred times.
    """
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, str | bytes):
        raise SourceRejected(
            "sources-illisibles",
            f"Sources illisibles : une liste était attendue, reçu {type(raw_sources).__name__}.",
        )
    entries = tuple(raw_sources)
    if not entries:
        return ()

    guardrails = guardrails if guardrails is not None else GardeFousIngestion()
    count_cap = guardrails.nb_max_sources
    if count_cap is not None and len(entries) > count_cap:
        raise SourceRejected(
            "trop-de-sources",
            f"Trop de sources : {len(entries)} déclarées, {count_cap} au maximum.",
        )

    resolved: list[Source] = []
    total = 0
    for index, raw in enumerate(entries):
        source = _resolve_one(
            raw,
            index=index,
            run_id=run_id,
            guardrails=guardrails,
            project_root=project_root,
            settings=settings,
        )
        if source.size is not None:
            total += source.size
            total_cap = guardrails.taille_max_totale_octets
            if total_cap is not None and total > total_cap:
                raise SourceRejected(
                    "ingestion-trop-volumineuse",
                    f"Sources trop volumineuses au total : {total} octets cumulés "
                    f"à la source {index + 1}, {total_cap} au maximum.",
                    index=index,
                )
        resolved.append(source)
    return tuple(resolved)


def _resolve_one(
    raw: Any,
    *,
    index: int,
    run_id: str,
    guardrails: GardeFousIngestion,
    project_root: Path | str | None,
    settings: Settings | None,
) -> Source:
    """A source resolved according to its type, or a reasoned `SourceRejected`."""
    data = raw.to_dict() if isinstance(raw, Source) else raw
    if not isinstance(data, Mapping):
        raise SourceRejected(
            "source-illisible",
            f"Source {index + 1} illisible : un objet était attendu, "
            f"reçu {type(raw).__name__}.",
            index=index,
        )
    source_type = str(data.get("type") or "").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise SourceRejected(
            "type-inconnu",
            f"Source {index + 1} de type inconnu : {source_type or '(vide)'!r} — "
            f"attendu {', '.join(SOURCE_TYPES)}.",
            index=index,
        )
    name = " ".join(str(data.get("nom") or "").split())
    if source_type == TYPE_URL:
        return _resolve_url(data, index=index, name=name)
    if source_type == TYPE_DIRECTORY:
        return _resolve_directory(data, index=index, name=name)
    return _resolve_file(
        data,
        index=index,
        name=name,
        run_id=run_id,
        guardrails=guardrails,
        project_root=project_root,
        settings=settings,
    )


def _resolve_url(data: Mapping[str, Any], *, index: int, name: str) -> Source:
    """A `url` source: `http(s)` and nothing else, no whitespace, bounded."""
    value = str(data.get("valeur") or data.get("chemin") or "").strip()
    if not value:
        raise SourceRejected(
            "url-absente", f"Source {index + 1} : URL manquante.", index=index
        )
    if len(value) > MAX_URL_LENGTH:
        raise SourceRejected(
            "url-trop-longue",
            f"Source {index + 1} : URL de {len(value)} caractères, "
            f"{MAX_URL_LENGTH} au maximum.",
            index=index,
        )
    # Same filter as the links of contract #183 (`maestro.references`): a scheme
    # other than `http(s)` is not an address to go and read: `file://` would
    # open the disk, `javascript:` opens nothing at all.
    if not value.lower().startswith(SCHEMAS_URL) or any(c.isspace() for c in value):
        raise SourceRejected(
            "url-non-suivable",
            f"Source {index + 1} refusée : {value!r} n'est pas une URL "
            f"{' ou '.join(s.rstrip(':/') for s in SCHEMAS_URL)}.",
            index=index,
        )
    return Source(type=TYPE_URL, name=name, value=value, read_only=True)


def _resolve_directory(data: Mapping[str, Any], *, index: int, name: str) -> Source:
    """A `dossier` source: validated like a project root, read-only."""
    raw = str(data.get("chemin") or data.get("valeur") or "").strip()
    if not raw:
        raise SourceRejected(
            "chemin-absent", f"Source {index + 1} : chemin de dossier manquant.", index=index
        )
    try:
        resolved = valider_racine(raw)
    except RacineRefusee as exc:
        # The root validation's reason is **kept** as is (`chemin-sensible`,
        # `racine-de-disque`, `dossier-absent`…): it is a stable code the UI
        # already translates (docs/05 §2.7), and renaming it would make two
        # vocabularies diverge for one refusal.
        raise SourceRejected(exc.motif, f"Source {index + 1} : {exc}", index=index) from exc
    return Source(
        type=TYPE_DIRECTORY,
        name=name or resolved.name,
        path=str(resolved),
        # Forced, never read from the declaration: a writable references
        # directory would be a disguised project that passed none of the
        # Phase 7 checks (docs/03, SOURCE entity).
        read_only=True,
    )


def _resolve_file(
    data: Mapping[str, Any],
    *,
    index: int,
    name: str,
    run_id: str,
    guardrails: GardeFousIngestion,
    project_root: Path | str | None,
    settings: Settings | None,
) -> Source:
    """A `fichier` source: safe name, known and bounded size, destination outside the project."""
    clean_name = file_name(name or str(data.get("chemin") or ""), index=index)
    size = _size(data.get("taille"), index=index, guardrails=guardrails)
    location = ingestion_location(run_id, settings=settings, project_root=project_root)
    # Second net: the name has just been sanitised, but it is `chemin_dans_racine`
    # (#221) that **proves** the destination stays under the run's location.
    destination = chemin_dans_racine(location, clean_name)
    return Source(
        type=TYPE_FILE,
        name=clean_name,
        path=str(destination),
        size=size,
        read_only=True,
    )


def file_name(raw: str, *, index: int = 0) -> str:
    """The sanitised file name of an uploaded source, or `SourceRejected`.

    A name, not a path: no separator, no `..`, no forbidden character. An
    uploaded file's name comes from the user's browser, that is, from outside:
    it is the classic vector of directory traversal.

    Public so that the upload (`maestro.sources.upload`) sanitises the name **on
    reception**, where resolution sanitises it at declaration. Two sanitisers
    written separately would diverge, and the one forgetting a class of
    characters would be the hole.
    """
    name = " ".join(str(raw or "").split())
    if not name:
        raise SourceRejected(
            "nom-absent", f"Source {index + 1} : nom de fichier manquant.", index=index
        )
    if len(name) > MAX_NAME_LENGTH:
        raise SourceRejected(
            "nom-trop-long",
            f"Source {index + 1} : nom de {len(name)} caractères, "
            f"{MAX_NAME_LENGTH} au maximum.",
            index=index,
        )
    if name in {".", ".."} or set(name) & _FORBIDDEN_NAME_CHARACTERS:
        raise SourceRejected(
            "nom-invalide",
            f"Source {index + 1} refusée : {name!r} est un chemin, pas un nom de fichier.",
            index=index,
        )
    return name


def _size(raw: Any, *, index: int, guardrails: GardeFousIngestion) -> int:
    """The size in bytes of an uploaded file, cap applied.

    **Required**, and that is the point: an unknown size is a size that cannot be
    capped, hence a cap that could be bypassed by omitting the field. Whoever
    uploads has the bytes at hand and can count them.
    """
    if raw is None:
        raise SourceRejected(
            "taille-absente",
            f"Source {index + 1} : taille manquante — une source non mesurée "
            "ne peut pas être plafonnée.",
            index=index,
        )
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise SourceRejected(
            "taille-invalide",
            f"Source {index + 1} : taille {raw!r} — un nombre d'octets positif était attendu.",
            index=index,
        )
    cap = guardrails.taille_max_source_octets
    if cap is not None and raw > cap:
        raise SourceRejected(
            "source-trop-volumineuse",
            f"Source {index + 1} trop volumineuse : {raw} octets, {cap} au maximum.",
            index=index,
        )
    return raw


def _refuse_inside_project(location: Path, project_root: Path | str) -> None:
    """Refuses an ingestion location situated **inside** the project root.

    The check is written upside down: `chemin_dans_racine` (#221) usually
    verifies that a path **stays** under a root, and here its success means
    refusal: an ingestion location valid in the project's sense is an ingestion
    location **inside** the project. Writing it so avoids copying the
    canonicalised path comparison, and so seeing it diverge.
    """
    try:
        chemin_dans_racine(project_root, location)
    except RacineRefusee:
        return  # outside the project: exactly what we want.
    raise SourceRejected(
        "ingestion-dans-le-projet",
        f"Emplacement d'ingestion refusé : {location} est dans le projet "
        f"({project_root}) — une matière téléversée ne se mêle pas à ses fichiers.",
    )
