"""The browser a login opens, received by Maestro instead of opened (#1704).

A command-line tool that signs in opens the person's browser on an authorization
page. When the API runs that login for the screen, the page has to reach the
screen as a link, and the API opens it itself. The convention that lets Maestro
receive it is public and shared by command-line tools: the `BROWSER` variable
names the command that opens a page, and the tool runs it with the page's address
as its last argument. Maestro points `BROWSER` at this script:

    python -I browser_relay.py <file> <url>

It writes the address into `<file>` and returns. Nothing the tool prints is read.

The script is run by its path, with `-I`: it imports nothing from Maestro and
nothing from the current folder, so it runs the same from a clone, a worktree or
an installation, whatever the tool's working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def relay_command(target: Path, interpreter: str | None = None) -> str:
    """The `BROWSER` value that leads a page's address into `target`.

    Each path is quoted: the tool runs the value through a shell, and Maestro's
    folders may carry spaces.
    """
    python = interpreter or sys.executable
    return f'"{python}" -I "{Path(__file__).resolve()}" "{target}"'


def write_url(target: Path, url: str) -> None:
    """Writes `url` into `target` in one move: a reader never sees half of it."""
    partial = target.with_name(target.name + ".partial")
    partial.write_text(url, encoding="utf-8")
    os.replace(partial, target)


def main(argv: list[str]) -> int:
    """`<file> <url>`: the address is written, and the tool goes on waiting for its callback."""
    if len(argv) != 2 or not argv[1].startswith(("https://", "http://")):
        print("usage: browser_relay.py <file> <url>", file=sys.stderr)
        return 2
    write_url(Path(argv[0]), argv[1])
    return 0


if __name__ == "__main__":  # pragma: no cover - run by the login's tool
    sys.exit(main(sys.argv[1:]))
