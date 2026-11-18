"""The program an agent runs to have a command played in a container (#1725, docs/57 §4).

`maestro-container IMAGE COMMAND [ARG...]` asks the container broker of its session
(`maestro.sandbox.container_broker`) to play COMMAND in a container of IMAGE, then
writes what the container writes and exits with its exit code. The agent names the
image and the command, nothing else: Maestro composes the container on the session's
workspace, at the person's level, on the contract of docs/17 §3.

`maestro-container --dockerfile PATH COMMAND [ARG...]` names, instead of an image, a
Dockerfile of the workspace (#1726): Maestro builds the project's image from it, unless
it is already built on the same content, then plays COMMAND in it. PATH is sent
absolute, resolved against this program's working directory.

This file runs at low integrity, under the agent, with `python -I`: it imports the
standard library only, and nothing of Maestro. It decides nothing either. What it
sends is checked by the broker, which is the only one to refuse, and says why; this
program only relays. The broker module imports the protocol from here, so that it is
written once.

The broker is reached on the loopback address that `ENV_ADDRESS` names, with the
session's token (`ENV_TOKEN`). Neither lives under `MAESTRO_*`, which the launcher
withholds from a lowered CLI (docs/17 §7, §8).
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
from collections.abc import Sequence
from typing import BinaryIO

#: The broker's address, `127.0.0.1:<port>`, in the agent's environment.
ENV_ADDRESS = "CONTAINER_BROKER_ADDRESS"

#: The session's token, which binds a request to the session's workspace.
ENV_TOKEN = "CONTAINER_BROKER_TOKEN"

#: The name the agent types.
PROGRAM = "maestro-container"

#: The fields a request carries, and the only ones: an image or a Dockerfile, not both.
FIELD_TOKEN = "token"
FIELD_IMAGE = "image"
FIELD_DOCKERFILE = "dockerfile"
FIELD_COMMAND = "command"
REQUEST_FIELDS: tuple[str, ...] = (FIELD_TOKEN, FIELD_IMAGE, FIELD_DOCKERFILE, FIELD_COMMAND)

#: The option that names a Dockerfile instead of an image.
OPTION_DOCKERFILE = "--dockerfile"

#: The fields of the broker's answer, one JSON object per line: output in base64,
#: then the exit code, or a refusal that says why.
FRAME_STDOUT = "stdout"
FRAME_STDERR = "stderr"
FRAME_EXIT = "exit"
FRAME_REFUSED = "refused"

#: The exit code when nothing of the command ran: refused, no broker, a broker that
#: went away, or a container that could not be composed. It is docker's own code for
#: a `docker run` that could not run its command. A command can return it too, and
#: the reason on stderr is what tells them apart.
NOT_RUN = 125

#: The largest request the broker reads.
REQUEST_MAX_BYTES = 1 << 20

#: What the program says before its own messages.
_PREFIX = f"{PROGRAM} : "


def request(
    token: str, image: str | None, command: Sequence[str], *, dockerfile: str | None = None
) -> bytes:
    """The request line for `image` (or `dockerfile`) and `command`, as the broker reads it."""
    body: dict[str, object] = {FIELD_TOKEN: token}
    if dockerfile is not None:
        body[FIELD_DOCKERFILE] = dockerfile
    else:
        body[FIELD_IMAGE] = image
    body[FIELD_COMMAND] = list(command)
    return json.dumps(body, ensure_ascii=True).encode("ascii") + b"\n"


def _what_to_play(arguments: list[str]) -> tuple[str | None, str | None, list[str]]:
    """The image, or the Dockerfile made absolute, and the command, as typed after them."""
    first = arguments[0]
    if first == OPTION_DOCKERFILE:
        if len(arguments) < 2:
            return None, "", []
        return None, os.path.abspath(arguments[1]), arguments[2:]
    if first.startswith(OPTION_DOCKERFILE + "="):
        path = first.split("=", 1)[1]
        return None, os.path.abspath(path) if path else "", arguments[1:]
    return first, None, arguments[1:]


def frame(**fields: object) -> bytes:
    """One line of the broker's answer."""
    return json.dumps(fields, ensure_ascii=True).encode("ascii") + b"\n"


def address(raw: str) -> tuple[str, int]:
    """`host:port` read, `ValueError` when it is not one."""
    host, _, port = raw.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"adresse illisible : {raw!r}")
    return host, int(port)


def main(argv: Sequence[str] | None = None) -> int:
    """Relays a request to the broker and its answer to this process's streams."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in ("-h", "--help"):
        _say(
            f"usage : {PROGRAM} IMAGE COMMANDE [ARGUMENT...], ou {PROGRAM} "
            f"{OPTION_DOCKERFILE} CHEMIN COMMANDE [ARGUMENT...]. Tu nommes l'image, ou un "
            "Dockerfile de ton espace dont Maestro construit l'image, et la commande ; "
            "Maestro compose le conteneur sur ton espace de travail."
        )
        return 0 if arguments else NOT_RUN
    image, dockerfile, command = _what_to_play(arguments)
    raw_address = os.environ.get(ENV_ADDRESS, "")
    token = os.environ.get(ENV_TOKEN, "")
    if not raw_address or not token:
        _say(
            f"aucun courtier de conteneur dans cette session ({ENV_ADDRESS} et {ENV_TOKEN} "
            "absentes) : la commande n'a pas été jouée."
        )
        return NOT_RUN
    try:
        target = address(raw_address)
    except ValueError as exc:
        _say(f"{exc} : la commande n'a pas été jouée.")
        return NOT_RUN
    try:
        connection = socket.create_connection(target)
    except OSError as exc:
        _say(f"le courtier de conteneur ne répond pas ({exc}) : la commande n'a pas été jouée.")
        return NOT_RUN
    with connection:
        connection.sendall(request(token, image, command, dockerfile=dockerfile))
        return _relay(connection.makefile("rb"), sys.stdout.buffer, sys.stderr.buffer)


def _relay(answer: BinaryIO, stdout: BinaryIO, stderr: BinaryIO) -> int:
    """Writes each frame where it belongs, and returns the exit code it ends with."""
    for line in answer:
        try:
            fields = json.loads(line)
        except ValueError:
            continue
        if not isinstance(fields, dict):
            continue
        for name, stream in ((FRAME_STDOUT, stdout), (FRAME_STDERR, stderr)):
            data = fields.get(name)
            if isinstance(data, str):
                stream.write(base64.b64decode(data))
                stream.flush()
        if FRAME_REFUSED in fields:
            _say(f"refusé — {fields[FRAME_REFUSED]}")
            return NOT_RUN
        code = fields.get(FRAME_EXIT)
        if isinstance(code, int):
            return code
    _say("le courtier s'est arrêté avant la fin de la commande.")
    return NOT_RUN


def _say(message: str) -> None:
    """A message of this program, in UTF-8 whatever the console's code page (#141)."""
    sys.stderr.flush()
    sys.stderr.buffer.write((_PREFIX + message + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()


if __name__ == "__main__":
    sys.exit(main())
