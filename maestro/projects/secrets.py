"""The secrets of the user's project, covered by the redaction (#226, #109).

The redaction (#109, `maestro.telemetry.redact`) masks three families of secrets: the
sensitive environment values **of Maestro**, the known key patterns (`sk-ant-…`), and the
values **served** to agents by the vault. Opening a local project adds a fourth — the secrets
**of the user** ([docs/24 §2.5](../../docs/24-projets-locaux-et-poste-de-travail.md)) — that
none of the three catches: the `.env` of a third-party project carries neither Maestro's
variables nor a recognisable prefix, and has never gone through the vault.

This module fills that gap before an agent works in the project: the secret holders that the
**perimeter already excludes** (`.env`, `**/secrets/**` — `DEFAULT_EXCLUDED`) are read **on the
host**, and their values registered as served secrets. They are then masked wherever they would
reappear — agent summary, captured deliverable, Langfuse trace.

The gesture may surprise: secrets are read the better to keep them quiet. It is exactly what
`_SENSITIVE_ENV` already does with the workstation's environment, and it is the only way to
mask a value that cannot be recognised on sight. Three safeguards frame it:

- **the reading stays on the host** — these files are precisely the ones the container does
  not mount (`maestro.sandbox.container`): knowing them to mask them is not exposing them;
- **nothing is kept** — the values go into the in-memory registry of #109, never persisted,
  never re-emitted, consulted only to substitute;
- **nothing is logged** — the function returns a **number**, never a value nor a path, so that
  no caller can turn it into a log line.
"""

from __future__ import annotations

from pathlib import Path

from maestro.projects.model import Project
from maestro.projects.perimeter import excluded_files
from maestro.telemetry.redact import register_secret

#: Names and suffixes of the files whose values are read. Deliberately narrow: outside this
#: list, an excluded file is a file that is not read — a whole `node_modules` has no business in
#: a secret registry.
_HOLDER_NAMES: tuple[str, ...] = (".env", ".netrc", ".npmrc", ".pypirc", "credentials")
_HOLDER_SUFFIXES: tuple[str, ...] = (".pem", ".key", ".token", ".secret", ".p12", ".pfx")

#: The folder that makes everything it contains a holder (docs/24 §2.5).
_HOLDER_FOLDER = "secrets"

#: Reading bounds: a secret registry has no business absorbing a repository. Beyond them,
#: reading stops — the redaction is a defence in depth, not an exhaustive inventory, and a 4 MB
#: `.pem` is not a value to substitute.
_MAX_FILES = 64
_MAX_SIZE = 64 * 1024
_MAX_LINE = 4096
_MAX_VALUES = 500

#: Minimum length of a registered value — same anti-false-positive threshold as the
#: redaction: below it, substituting would do more harm than good (`true`, `8080`, a database
#: name).
_MIN_LENGTH = 8


def register_project_secrets(project: Project) -> int:
    """Registers the secret values readable in `project`; returns their number.

    Idempotent (the registry of #109 is a set) and **best-effort**: an unreadable file, a
    vanished folder or a missing root are skipped silently. Failing a task because a `.env` is
    locked would trade a back-up protection for an outage.

    Returns **only** the count: no value, no path, nothing a caller could log.
    """
    root = project.root_path
    if not root.is_dir():
        return 0
    registered = 0
    read_count = 0
    for file in excluded_files(root, project.perimeter):
        if read_count >= _MAX_FILES or registered >= _MAX_VALUES:
            break
        if not holds_secrets(file, root):
            continue
        read_count += 1
        for value in _values(file):
            register_secret(value)
            registered += 1
            if registered >= _MAX_VALUES:
                break
    return registered


def holds_secrets(file: Path, root: Path) -> bool:
    """Is `file` of a kind whose values deserve to be masked?

    Three signs, in the order they are met: a known holder **name** (`.env`, `.env.local`,
    `.npmrc`…), a cryptographic material **suffix** (`.pem`, `.key`…), or a **`secrets/`
    folder** somewhere above — the one `DEFAULT_EXCLUDED` removes by default.

    Public because the reading of sources (`maestro.sources.extraction`, #1163) reads any file
    whose bytes are text, and it is **this** rule, the same as the masking one, that forbids it
    to bring a secret holder into a model's context. Two lists would end up diverging, and a
    secret would slip through the gap.
    """
    name = file.name
    if name in _HOLDER_NAMES or name.startswith(".env"):
        return True
    if file.suffix.lower() in _HOLDER_SUFFIXES:
        return True
    try:
        relative = file.relative_to(root)
    except ValueError:  # outside the root: what is not bounded is not read
        return False
    return _HOLDER_FOLDER in (part.lower() for part in relative.parts[:-1])


def _values(file: Path) -> list[str]:
    """The secret values of a file — right-hand side of the `KEY=value` lines, or the line.

    Two forms cover what lies around in a project: the variables file (`KEY=value`, quotes
    removed, `export ` tolerated) and the file that **is** the secret (a lone token, a PEM
    key). When in doubt the whole line is taken: registering a line that was not secret masks
    nothing more than itself, whereas missing it would leak a token.
    """
    try:
        if file.stat().st_size > _MAX_SIZE:
            return []
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or len(line) > _MAX_LINE:
            continue
        if line.startswith("-----") and line.endswith("-----"):
            continue  # the bounds of a PEM block are not the secret
        _, separator, after = line.partition("=")
        value = after.strip() if separator else line
        value = _unquoted(value)
        if len(value) >= _MIN_LENGTH:
            values.append(value)
    return values


def _unquoted(value: str) -> str:
    """Removes the pair of quotes around a value of a `.env` file."""
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1]
    return value
