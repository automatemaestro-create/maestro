"""Applying a project's perimeter patterns (#226, EF-37).

The model (`maestro.projects.model`, #221) **declares** a perimeter —
`Perimeter(included=…, excluded=…)`, patterns relative to the root, `excluded` winning over
`included`. This module **applies** it to the disk and answers the one question isolated mode
needs: *which paths of the project does the perimeter remove?*

That list is what the container **masks** (`maestro.sandbox.container`) — "what is excluded is
not mounted" — and it is also what names the places where secrets live, for the redaction to
cover (`maestro.projects.secrets`).

Three choices, all dictated by use:

1. **the highest path wins** — an excluded folder is returned as is, without descending into
   it. A `node_modules` of 40,000 files costs one entry, not forty thousand, and it is also
   what a mount can mask in a single stroke;
2. **no symbolic link is followed or returned** — the same rule as the perimeter copy (#224):
   it is the escape vector of docs/24 §2.5, and a link to `~/.ssh` has no business becoming a
   path one handles;
3. **nothing is capped here** — the list is returned whole and the caller decides what a list
   that is too long means to it (the container refuses rather than mount halfway).

The pattern conventions are those of `.gitignore`, because that is what a user writes without
thinking: `.` means everything, a pattern without `/` applies at any depth (`node_modules`
catches `apps/web/node_modules`), and `**` crosses separators where `*` and `?` stop at the
segment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.projects.model import Perimeter


@dataclass(frozen=True)
class ExcludedPath:
    """A path the perimeter removes: its relative path and its nature.

    `path` is **relative to the analysed root**, in POSIX (`apps/web/.env`): it is the form
    that goes through a `docker run` and a JSON without escaping. `is_dir` says whether to mask
    it with an empty folder or with an empty file — the distinction is observed here, on the
    host, rather than letting the shim redo a `stat` on a path it did not enumerate.
    """

    path: str
    is_dir: bool


def exclusions(root: Path | str, perimeter: Perimeter) -> tuple[ExcludedPath, ...]:
    """The paths of `root` that `perimeter` excludes, the highest first.

    An iterative walk (Python's stack is not the place to rely on the depth of a real project),
    deterministic (sorted entries), and one that **never descends** into a path already
    excluded: an excluded folder is a verdict, not an invitation to enumerate it.

    Empty when the root is not a readable folder: this module observes, it does not judge the
    root — that is the job of `maestro.projects.root`.
    """
    base = Path(root)
    patterns = compiled_patterns(perimeter.excluded)
    if not patterns:
        return ()
    found: list[ExcludedPath] = []
    stack = [""]
    while stack:
        relative_dir = stack.pop()
        current = base / relative_dir if relative_dir else base
        try:
            with os.scandir(current) as entries:
                sorted_entries = sorted(entries, key=lambda entry: entry.name)
        except OSError:  # unreadable or vanished folder: skipped, never fatal
            continue
        for entry in sorted_entries:
            relative = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
            if entry.is_symlink():
                continue
            is_dir = entry.is_dir()
            if _matches(relative, patterns):
                found.append(ExcludedPath(path=relative, is_dir=is_dir))
            elif is_dir:
                stack.append(relative)
    return tuple(sorted(found, key=lambda excluded: excluded.path))


def excluded_files(root: Path | str, perimeter: Perimeter) -> Iterator[Path]:
    """The **files** the perimeter removes, including under an excluded folder.

    Where `exclusions` stops at the highest path — what masking a mount needs —, this one
    descends: an excluded `secrets/` holds files whose values the redaction (#109) must know.
    Symbolic links are never followed, at any depth.
    """
    base = Path(root)
    for excluded in exclusions(base, perimeter):
        path = base / excluded.path
        if not excluded.is_dir:
            yield path
            continue
        for found in sorted(path.rglob("*")):
            if found.is_file() and not found.is_symlink():
                yield found


def compiled_patterns(patterns: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """Compiles perimeter patterns into anchored regular expressions."""
    return tuple(
        _compile(normalised) for pattern in patterns for normalised in _normalised_forms(pattern)
    )


def excluded_by(relative: str, perimeter: Perimeter) -> bool:
    """Is the path `relative` (POSIX, relative to the root) removed by `perimeter`?

    The question asked by a reader that **already holds** the path — the orchestrator about to
    open a file of the project (#1223) —, where `exclusions` answers that of a caller who starts
    from the root and wants the list. No second pattern mechanism: it is `compiled_patterns`
    and the same anchored `match`, exposed in the form the reader uses them.

    ⚠ **A path is excluded as soon as one of its parents is.** A perimeter that removes
    `secrets/` removes `secrets/prod.key`, which the pattern does not target directly
    (`**/secrets/**` targets it, `secrets` alone does not): the verdict is therefore taken on
    the path **and on each of its prefixes**, otherwise the boundary would open one segment
    further.
    """
    patterns = compiled_patterns(perimeter.excluded)
    if not patterns:
        return False
    segments = [segment for segment in relative.replace("\\", "/").split("/") if segment]
    for depth in range(1, len(segments) + 1):
        if _matches("/".join(segments[:depth]), patterns):
            return True
    return False


def _matches(relative: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Is `relative` targeted by one of the patterns?"""
    return any(pattern.match(relative) for pattern in patterns)


def _normalised_forms(pattern: str) -> tuple[str, ...]:
    """The normalised forms of a pattern — one, sometimes two.

    - `.` (and the empty string) means **everything** — it is `DEFAULT_INCLUDED`;
    - a pattern **without `/`** applies at **any depth**: `.env` catches
      `services/api/.env`;
    - a pattern ending with `/**` also targets **the folder itself**, otherwise
      `**/secrets/**` would exclude the content of `secrets/` but not its name — and the name
      is what a mount masks in a single stroke.
    """
    normalised = pattern.strip().replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    normalised = normalised.rstrip("/")
    if normalised in ("", "."):
        return ("**",)
    if "/" not in normalised:
        normalised = f"**/{normalised}"
    if normalised.endswith("/**"):
        return (normalised, normalised[: -len("/**")])
    return (normalised,)


def _compile(normalised: str) -> re.Pattern[str]:
    """A normalised pattern as a regular expression anchored on the whole path.

    `**` crosses separators, `*` and `?` stop at the segment — the distinction `fnmatch` lacks,
    whose `*` swallows the `/` and would make `*.py` a recursive pattern. `[…]` classes are not
    handled: they serve no purpose in a project perimeter.
    """
    segments = normalised.split("/")
    last = len(segments) - 1
    parts = [
        (".*" if index == last else "(?:[^/]+/)*")
        if segment == "**"
        else _segment(segment) + ("" if index == last else "/")
        for index, segment in enumerate(segments)
    ]
    return re.compile("".join(parts) + r"\Z")


def _segment(segment: str) -> str:
    """A pattern segment (without `/`) as a regular expression fragment."""
    return "".join(
        "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
        for char in segment
    )
