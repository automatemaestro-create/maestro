"""The CLI version the sandbox image installs: the one the host's SDK ships (#1312).

In container mode, the shim relays the host SDK's arguments to the CLI of the image
(docs/17 §2): both must be of the same version. The image is therefore built with
the version this module prints, alone on its line (docs/17 §4):

    docker build \\
      --build-arg CLAUDE_CODE_VERSION=$(python -m maestro.sandbox.image_cli) \\
      -t maestro-sandbox:latest infra/sandbox

The version is **read** on the binary the provider launches
(`maestro.installation.cli_claude_resolu`, `maestro.clients_du_poste.lire_version`),
never deduced from the SDK's. A module of its own, which nothing imports: run by
`python -m`, it is not executed a second time as part of its package.
"""

from __future__ import annotations

import sys

from maestro.clients_du_poste import lire_version
from maestro.installation import cli_claude_resolu


def main() -> int:
    """Prints the host CLI's version; exits 1, with the reason on stderr, when it is unreadable.

    A CLI taken off the PATH rather than shipped by the SDK is still printed, with a
    warning on stderr: its version is the workstation's, not the one the SDK was
    verified with (docs/44 §7).
    """
    origin, path = cli_claude_resolu()
    version = lire_version(str(path)) if path is not None else None
    if version is None:
        print(
            "la version du CLI claude que le SDK embarque est illisible "
            f"(CLI {'introuvable' if path is None else path})",
            file=sys.stderr,
        )
        return 1
    if origin != "paquet":
        print(
            f"CLI claude pris hors du SDK ({path}) : sa version est celle du poste",
            file=sys.stderr,
        )
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
