"""What the Claude adapter runs on: the SDK, the CLI it drives, the container's CLI (#1312).

The adapter rests on behaviours of the CLI that its SDK ships: how a session is
closed off, how a refusal of Maestro is applied, how usage is reported. Those are
verified, never assumed: the **contract suite** (`tests/test_claude_contract.py`,
docs/44 §7) proves them on the real CLI, and it is played before any change of the
SDK's version.

That is why the versions are **locked**: `pyproject.toml` pins the SDK, which ships
its CLI, and the sandbox image installs the CLI of the same version
(`infra/sandbox/Dockerfile`, built with `python -m maestro.sandbox.image_cli`).
The lock serves reproducibility and verification — a run says what it ran on, and a
version nobody verified does not pass. It never serves to keep a behaviour: a
behaviour Maestro needs is written in its own contracts (docs/44).

Every run writes what it ran on (`maestro.engine.executor`), and each version is
**read** where it lives, never deduced from another:

- the SDK's, in the metadata of its installed distribution;
- the CLI's, on the binary the provider launches (`<cli> --version`,
  `maestro.clients_du_poste.lire_version`), as the Settings show it (`maestro.installation`);
- the container's, in the label its image carries (`IMAGE_CLI_LABEL`), which the
  Dockerfile writes from the version it installed. An image built before the label
  says nothing, and is said to be rebuilt.

A version that cannot be read is **unknown**, and said so.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from maestro.clients_du_poste import lire_version
from maestro.installation import Provenance, cli_claude_resolu

#: The distribution that ships the SDK (and, in it, its CLI).
SDK_DISTRIBUTION = "claude-agent-sdk"

#: The label of the sandbox image that names the CLI it carries
#: (`infra/sandbox/Dockerfile` writes it, from the version it installed).
IMAGE_CLI_LABEL = "maestro.claude-cli"

#: How long Docker may take to describe an image.
_DOCKER_TIMEOUT_S = 15.0

#: How each origin of the CLI reads in the run's journal.
_ORIGINS: dict[str, str] = {
    "paquet": "embarqué par le SDK",
    "poste": "pris sur le PATH du poste, hors du SDK",
}

#: Reads the version of the binary at a path, or `None` (`lire_version`'s signature).
VersionReader = Callable[[str], str | None]

#: Reads a label of an image, or `None` (`image_label`'s signature).
LabelReader = Callable[[str, str], str | None]


def sdk_version() -> str | None:
    """The installed SDK's version, from its distribution's metadata — `None` when absent."""
    try:
        return metadata.version(SDK_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None


def image_label(image: str, label: str) -> str | None:
    """The value of `label` on `image`, as Docker describes it — `None` when it cannot say.

    Docker absent, an image that does not exist, an image without that label: all
    are `None`. Only Docker's public description is read (`image inspect`).
    """
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, the image is a setting
            ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DOCKER_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            check=False,
            creationflags=no_window,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        labels = json.loads(done.stdout or "null")
    except ValueError:
        return None
    value = labels.get(label) if isinstance(labels, dict) else None
    if not isinstance(value, str):
        return None
    return value.strip() or None


@dataclass(frozen=True)
class RuntimeVersions:
    """The versions one run ran on. `image` is set in container mode only (docs/17)."""

    sdk: str | None
    cli: str | None
    cli_origin: Provenance
    image: str | None = None
    image_cli: str | None = None

    @property
    def image_matches(self) -> bool:
        """Does the container run the CLI the host's SDK ships? True outside container mode."""
        return self.image is None or (self.image_cli is not None and self.image_cli == self.cli)

    def as_lines(self) -> dict[str, str]:
        """What the run's journal writes: one reading per component, in the person's words."""
        lines = {
            "SDK claude-agent-sdk": self.sdk or "inconnue (paquet introuvable)",
            "CLI claude": self._cli(),
        }
        if self.image is not None:
            lines[f"CLI claude de l'image {self.image}"] = self._image_cli()
        return lines

    def _cli(self) -> str:
        if self.cli_origin == "absent":
            return "introuvable"
        version = self.cli or "version inconnue"
        return f"{version} ({_ORIGINS[self.cli_origin]})"

    def _image_cli(self) -> str:
        if self.image_cli is None:
            return (
                f"inconnue — l'image ne porte pas le label {IMAGE_CLI_LABEL} : "
                "elle est à reconstruire sur la version du CLI de l'hôte"
            )
        if self.image_matches:
            return self.image_cli
        return (
            f"{self.image_cli} — ce n'est pas le CLI de l'hôte ({self.cli or 'inconnu'}) : "
            "l'image est à reconstruire sur sa version"
        )


@dataclass
class ClaudeRuntime:
    """Reads what the adapter runs on, each time a run asks (`versions`).

    `image` is the sandbox image of container mode, `None` on the host. The CLI's
    version is kept by the **identity of its binary** (path, modification time,
    size): a run does not relaunch the CLI to read the same version, and a CLI
    that was replaced is read again. The image is described again at each run: a
    rebuilt image must be seen as rebuilt.
    """

    image: str | None = None
    read_version: VersionReader = lire_version
    read_label: LabelReader = image_label
    _cli_versions: dict[tuple[str, int, int], str] = field(default_factory=dict, repr=False)

    def versions(self) -> RuntimeVersions:
        origin, path = cli_claude_resolu()
        return RuntimeVersions(
            sdk=sdk_version(),
            cli=self._cli_version(path),
            cli_origin=origin,
            image=self.image,
            image_cli=(
                self.read_label(self.image, IMAGE_CLI_LABEL) if self.image is not None else None
            ),
        )

    def _cli_version(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._cli_versions:
            read = self.read_version(str(path))
            if read is None:
                return None
            self._cli_versions[key] = read
        return self._cli_versions[key]
