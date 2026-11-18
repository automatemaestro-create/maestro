"""The container broker: an agent at low integrity has a command played in a container (#1725).

Docker Desktop's engine refuses a process at low integrity (#1714), and opening its
pipe to the agent would let it mount any folder the person can write. The decision of
docs/57 (§4) is a broker, on the pattern of Chromium's sandbox: the lowered target asks,
the broker checks the request against its policy, does the permitted operation itself,
and returns the result.

**What the agent names**: an image, or a Dockerfile of its workspace, and a command,
through `maestro-container` (`maestro.sandbox.container_broker_client`), or a script
that runs it. Nothing else: no mount, no network, no privilege, no folder. A request
that names anything else is refused, and the refusal says why.

**What Maestro composes**, at the person's level, by the code of the isolated mode and
never by a second contract (`maestro.sandbox.container.protocole_sandbox`, then
`commande_courtier`): the session's workspace on `/workspace`, with the masks of its
scope; for a versioned project, the container's own repository
(`maestro.sandbox.depot_conteneur`), built for the command and removed after it, before
its code is told; a
read-only root, the non-root user `agent`, no capability, no new privilege, the caps;
an outbound network. Nothing of the person's environment enters.

**What Maestro builds** from a Dockerfile (#1726, `maestro.sandbox.container_image`):
the project's image, on a context that carries what the agent's ignore file keeps of
the workspace and nothing the scope excludes, without entitlement, secret or ssh agent.
Its tag is the fingerprint of that context, so the same content builds nothing twice;
each project has its repository, and a new image removes the previous one.

**What binds a request to a workspace** is the session's token. The provider opens a
session when an agent's CLI is born low (`ContainerBroker.open`) and closes it with the
session: a token that is not the one of a live session is refused. The token travels
outside `MAESTRO_*`, which the launcher withholds from a lowered CLI (docs/17 §8).
Another low agent can read it, since two low agents see each other (docs/17 §7): it
then obtains the other's workspace, which it can already write.

**The channel** is a TCP socket on the loopback, which a process at low integrity
reaches (docs/17 §7). It listens in the process that runs the agents' sessions, from
the first session it serves until that process ends.

**One container at a time per project**, a build included: the container's repository
of a versioned project is rebuilt for each command, a build removes the project's
previous image, and the caps hold for the project, not for each of its sessions. A
second request waits for the first, and is told so. A session without a project is its
own. A request whose program goes away has its container removed, or its build
stopped.

**Docker is only an adapter** (docs/44): the verb is Maestro's, and its regime is chosen
on a fact, the broker being there, never on the text of an error. A failure of docker is
relayed as docker wrote it, with its code.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from maestro.config import ConfigError
from maestro.emplacements import jetable
from maestro.fichiers import retirer_arbre
from maestro.projects.model import Project
from maestro.sandbox import container, container_image
from maestro.sandbox.container_broker_client import (
    ENV_ADDRESS,
    ENV_TOKEN,
    FIELD_COMMAND,
    FIELD_DOCKERFILE,
    FIELD_IMAGE,
    FIELD_TOKEN,
    FRAME_EXIT,
    FRAME_REFUSED,
    FRAME_STDERR,
    FRAME_STDOUT,
    NOT_RUN,
    OPTION_DOCKERFILE,
    PROGRAM,
    REQUEST_FIELDS,
    REQUEST_MAX_BYTES,
    frame,
)
from maestro.sandbox.container_image import ImageRefusal

#: The network of a broker's container: outbound only, no port published.
NETWORK = container.RESEAU_DEFAUT

#: The prefix of a broker's container name, followed by a random part.
CONTAINER_PREFIX = "maestro-broker-"

#: The prefix of the folder that holds the program's launchers, under the disposable root.
LAUNCHERS_PREFIX = "maestro-courtier-"

#: The prefix of the folder of one build — Maestro's Dockerfile, its ignore file, the
#: context's tar —, under the disposable root, removed after the build.
BUILD_PREFIX = "maestro-courtier-image-"

#: How long a connection may take to send its request: a guard against a client that
#: connects and says nothing, never a bound on the command.
REQUEST_TIMEOUT_S = 30.0

#: How long `docker rm -f` may take to remove a container whose request went away.
REMOVE_TIMEOUT_S = 60.0

#: How long a short docker call may take: inspecting, listing or removing an image.
DOCKER_TIMEOUT_S = 120.0

#: An image reference, on the grammar docker writes for one (`distribution/reference`):
#: an optional registry, lowercase path components, an optional tag and digest. It never
#: starts with `-`, so docker never reads it as an option.
_IMAGE = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*"
    r"(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    r"(?:@[A-Za-z][A-Za-z0-9]*(?:[-_+.][A-Za-z][A-Za-z0-9]*)*:[0-9A-Fa-f]{32,})?"
)

#: The longest image reference docker accepts.
_IMAGE_MAX = 255

#: The size of a chunk of output relayed to the program.
_CHUNK = 64 * 1024

#: What a refusal says about what a request may name, after its own reason.
_ONLY_IMAGE_AND_COMMAND = (
    f"une demande nomme une image, ou un Dockerfile de ton espace ({OPTION_DOCKERFILE} "
    "CHEMIN), puis la commande à jouer. Un montage, un réseau, un privilège ou un dossier ne "
    "se demandent pas : Maestro compose le conteneur sur l'espace de ta session"
)

#: Starts a composed docker command — `docker run`, or a build fed from a file on stdin.
Spawn = Callable[[Sequence[str], "IO[bytes] | None"], "subprocess.Popen[bytes]"]

#: Removes a container by its name.
Remove = Callable[[str], None]

#: Plays a short docker command and returns what it did.
Docker = Callable[[Sequence[str]], "subprocess.CompletedProcess[bytes]"]


class BrokerRefusal(Exception):
    """A request the broker does not serve; its text says why, to the agent."""


def available() -> bool:
    """Can a broker serve this machine? — docker's program is on the person's `PATH`.

    A fact, read where the broker plays: whether the engine runs is docker's own answer,
    relayed with its code.
    """
    return shutil.which("docker") is not None


@dataclass
class _Session:
    """What the broker knows of a live session: its workspace, its project, its images.

    `lock` is its project's, shared by every session of that project; `repository` the
    project's image repository. A session without a project has both to itself, and
    its images are removed with it (`owns_images`).
    """

    workspace: Path
    project: Project | None
    repository: str
    lock: threading.Lock
    owns_images: bool
    containers: set[str] = field(default_factory=set)
    built: bool = False


@dataclass(frozen=True)
class _Request:
    """A request the broker serves: an image or a Dockerfile, and the command."""

    session: _Session
    image: str | None
    dockerfile: str | None
    command: list[str]


@dataclass
class BrokerSession:
    """The broker as one session holds it: what its agent receives, and its end.

    `env` carries the broker's address and the session's token; `launchers` is the
    folder of the `maestro-container` program, to be put first on the agent's `PATH`.
    """

    env: dict[str, str]
    launchers: Path
    _close: Callable[[], None]

    def close(self) -> None:
        """Ends the session: its token is refused from now on, its containers removed."""
        self._close()


class ContainerBroker:
    """The broker of the process that runs the agents' sessions.

    `spawn` starts a composed docker command, `remove` removes a container by its name,
    and `docker` plays a short docker command: docker's program by default, doubles in
    a test. The server starts with the first session it serves.
    """

    def __init__(
        self,
        *,
        spawn: Spawn | None = None,
        remove: Remove | None = None,
        docker: Docker | None = None,
    ) -> None:
        self._spawn = spawn or _spawn_docker
        self._remove = remove or _remove_container
        self._docker = docker or _run_docker
        self._sessions: dict[str, _Session] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingTCPServer | None = None
        self._launchers: Path | None = None

    # ------------------------------------------------------------------ sessions

    def open(self, workspace: Path, project: Project | None = None) -> BrokerSession:
        """Opens the broker to the session working in `workspace`, on `project` if any."""
        token = secrets.token_urlsafe(32)
        key = _key(token)
        owner = project.id if project is not None else f"session-{key[:16]}"
        with self._lock:
            host, port = self._started()
            lock = self._locks.setdefault(owner, threading.Lock())
            self._sessions[key] = _Session(
                workspace,
                project,
                container_image.repository(owner),
                lock,
                owns_images=project is None,
            )
            launchers = self._launchers_folder()
        env = {ENV_ADDRESS: f"{host}:{port}", ENV_TOKEN: token}
        return BrokerSession(env, launchers, lambda: self._close(key))

    def _close(self, key: str) -> None:
        with self._lock:
            session = self._sessions.pop(key, None)
            if session is not None and session.owns_images:
                self._locks.pop(f"session-{key[:16]}", None)
        if session is None:
            return
        for name in list(session.containers):
            self._remove(name)
        if session.owns_images and session.built:
            self._collect(session.repository, None)

    def shutdown(self) -> None:
        """Stops the server and ends every session — for a process that stops serving."""
        with self._lock:
            server, self._server = self._server, None
            keys = list(self._sessions)
        for key in keys:
            self._close(key)
        if server is not None:
            server.shutdown()
            server.server_close()

    @property
    def address(self) -> tuple[str, int] | None:
        """Where the broker listens, `None` before its first session."""
        with self._lock:
            if self._server is None:
                return None
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    def _started(self) -> tuple[str, int]:
        if self._server is None:
            broker = self

            class _Handler(socketserver.BaseRequestHandler):
                def handle(self) -> None:
                    broker.serve(self.request)

            server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
            server.daemon_threads = True
            threading.Thread(
                target=server.serve_forever, name="container-broker", daemon=True
            ).start()
            self._server = server
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def _launchers_folder(self) -> Path:
        if self._launchers is None:
            self._launchers = write_launchers(jetable(LAUNCHERS_PREFIX))
        return self._launchers

    # ------------------------------------------------------------------ requests

    def serve(self, connection: socket.socket) -> None:
        """Answers one connection: a request read, checked, then played or refused."""
        channel = _Channel(connection)
        try:
            connection.settimeout(REQUEST_TIMEOUT_S)
            try:
                raw = connection.makefile("rb").readline(REQUEST_MAX_BYTES + 1)
            except OSError:
                return
            connection.settimeout(None)
            try:
                served = self._checked(raw)
                channel.watch()
                code = self._play(served, channel)
            except (BrokerRefusal, ImageRefusal) as refusal:
                channel.send(frame(**{FRAME_REFUSED: str(refusal)}))
            except Exception as exc:  # noqa: BLE001 - said to the program, never swallowed
                channel.say(f"le courtier a échoué : {type(exc).__name__}: {exc}")
                channel.send(frame(**{FRAME_EXIT: NOT_RUN}))
            else:
                channel.send(frame(**{FRAME_EXIT: code}))
        finally:
            with contextlib.suppress(OSError):
                connection.close()

    def _checked(self, raw: bytes) -> _Request:
        """The session, the image or Dockerfile and the command of a request, or its refusal."""
        if not raw.endswith(b"\n"):
            raise BrokerRefusal(
                f"la demande dépasse {REQUEST_MAX_BYTES} octets ou n'est pas terminée."
            )
        not_an_object = BrokerRefusal(
            f"la demande n'est pas un objet JSON ; {_ONLY_IMAGE_AND_COMMAND}."
        )
        try:
            request = json.loads(raw)
        except ValueError:
            raise not_an_object from None
        if not isinstance(request, dict):
            raise not_an_object
        named = sorted(set(request) - set(REQUEST_FIELDS))
        if named:
            listed = ", ".join(f"`{name}`" for name in named)
            raise BrokerRefusal(f"la demande nomme {listed} ; {_ONLY_IMAGE_AND_COMMAND}.")
        token = request.get(FIELD_TOKEN)
        with self._lock:
            session = self._sessions.get(_key(token)) if isinstance(token, str) else None
        if session is None:
            raise BrokerRefusal(
                "ce jeton n'est celui d'aucune session vivante : le courtier ne sert une "
                "session que tant qu'elle travaille, et sur son seul espace."
            )
        image, dockerfile = _named(request, session)
        command = request.get(FIELD_COMMAND)
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and "\0" not in part for part in command)
        ):
            raise BrokerRefusal(
                "la commande manque, ou n'est pas une liste de mots : nomme-la après l'image, "
                f"`{PROGRAM} IMAGE COMMANDE [ARGUMENT...]`."
            )
        return _Request(session, image, dockerfile, command)

    def _play(self, served: _Request, channel: _Channel) -> int:
        """Composes the container on the session's workspace, its image if need be, plays it.

        Returns the code, never sends it: the program returns on its code, so `serve`
        tells it once the container's repository is removed and the project's turn
        freed, and what the program does next finds nothing of the command (#1748).
        """
        session = served.session
        if not session.lock.acquire(blocking=False):
            whose = "ton projet" if session.project is not None else "ta session"
            channel.say(f"un conteneur de {whose} tourne déjà ; celui-ci attend son tour.")
            session.lock.acquire()
        try:
            try:
                environ = container.protocole_sandbox(
                    session.workspace,
                    image=served.image or "",
                    reseau=NETWORK,
                    projet=session.project,
                )
            except ConfigError as exc:
                channel.say(f"le conteneur n'a pas pu être composé : {exc}")
                return NOT_RUN
            try:
                if served.dockerfile is not None:
                    built = self._image(session, served.dockerfile, environ, channel)
                    if built is None:
                        return NOT_RUN
                    environ[container.ENV_IMAGE] = built
                return self._run(session, environ, served.command, channel)
            finally:
                depot = environ.get(container.ENV_DEPOT)
                if depot:
                    retirer_arbre(Path(depot))
        finally:
            session.lock.release()

    def _run(
        self,
        session: _Session,
        environ: Mapping[str, str],
        command: list[str],
        channel: _Channel,
    ) -> int:
        """Plays the command in its container until it ends; the code to tell."""
        name = CONTAINER_PREFIX + secrets.token_hex(6)
        argv = container.commande_courtier(environ, command, nom=name)
        session.containers.add(name)
        try:
            code = self._follow(argv, channel, gone=lambda: self._remove(name))
        finally:
            session.containers.discard(name)
        return NOT_RUN if code is None else code

    # ------------------------------------------------------------------ images

    def _image(
        self, session: _Session, dockerfile: str, environ: Mapping[str, str], channel: _Channel
    ) -> str | None:
        """The tag of the project's image for `dockerfile`, built if it is not there yet.

        `None` when it could not be: the reason has been said. A refusal of the
        Dockerfile or of its context is raised, before anything of the agent's is built.
        """
        excluded = container_image.masks(environ)
        container_image.refuse_masked(dockerfile, excluded)
        ignore = container_image.context_ignore(
            container_image.agent_ignore(session.workspace, dockerfile), dockerfile, excluded
        )
        folder = jetable(BUILD_PREFIX)
        try:
            (folder / container_image.CONTEXT_DOCKERFILE_NAME).write_text(
                container_image.CONTEXT_DOCKERFILE, encoding="utf-8", newline="\n"
            )
            ignore_file = container_image.CONTEXT_DOCKERFILE_NAME + container_image.IGNORE_SUFFIX
            (folder / ignore_file).write_bytes(ignore)
            if not self._context(folder, session.workspace, channel):
                return None
            fingerprint = container_image.inspect_context(
                folder / container_image.CONTEXT_TAR_NAME, dockerfile, excluded
            )
            image = container_image.tag(session.repository, fingerprint)
            if self._exists(image):
                channel.say(f"image {image} à jour : rien à construire.")
                return image
            if not self._build(folder, image, session, dockerfile, channel):
                return None
            session.built = True
            removed = self._collect(session.repository, image)
            if removed:
                channel.say(f"{removed} image(s) précédente(s) de ton projet retirée(s).")
            return image
        finally:
            retirer_arbre(folder)

    def _context(self, folder: Path, workspace: Path, channel: _Channel) -> bool:
        """The first step: the context exported as a tar; its output said only on failure."""
        output: list[bytes] = []
        code = self._follow(
            container_image.context_argv(folder, workspace), channel, collect=output
        )
        if code == 0:
            return True
        channel.write_stderr(b"".join(output))
        channel.say(
            f"le contexte de construction n'a pas pu être composé (docker a rendu {code}) : "
            "la commande n'a pas été jouée."
        )
        return False

    def _build(
        self, folder: Path, image: str, session: _Session, dockerfile: str, channel: _Channel
    ) -> bool:
        """The second step: the image built from the context's tar, its output relayed."""
        channel.say(f"construction de l'image {image} depuis `{dockerfile}`…")
        argv = container_image.build_argv(image, dockerfile, session.repository)
        with (folder / container_image.CONTEXT_TAR_NAME).open("rb") as stdin:
            code = self._follow(argv, channel, stdin=stdin, out=FRAME_STDERR)
        if code == 0 and self._exists(image):
            return True
        channel.say(
            f"la construction de l'image a échoué (docker a rendu {code}) : la commande n'a "
            "pas été jouée."
        )
        return False

    def _exists(self, image: str) -> bool:
        done = self._docker(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        return done.returncode == 0

    def _collect(self, repository: str, current: str | None) -> int:
        """Removes the images of `repository` but `current`; how many were removed.

        Best effort: an image docker refuses to remove is left there, and a listing that
        fails removes nothing.
        """
        listing = self._docker(container_image.listing_argv(repository))
        if listing.returncode != 0:
            return 0
        text = listing.stdout.decode("utf-8", "replace")
        return sum(
            1
            for image in container_image.stale(text, repository, current)
            if self._docker(["docker", "rmi", image]).returncode == 0
        )

    # ------------------------------------------------------------------ processes

    def _follow(
        self,
        argv: Sequence[str],
        channel: _Channel,
        *,
        stdin: IO[bytes] | None = None,
        out: str = FRAME_STDOUT,
        collect: list[bytes] | None = None,
        gone: Callable[[], None] | None = None,
    ) -> int | None:
        """Plays `argv` until it ends, its output relayed or collected; its code.

        `None` when docker could not be started, which has been said. If the program
        goes away first, `gone` is called and the process killed.
        """
        try:
            process = self._spawn(argv, stdin)
        except OSError as exc:
            channel.say(f"docker n'a pas pu être lancé : {exc}")
            return None
        done = threading.Event()

        def stop() -> None:
            channel.closed.wait()
            if not done.is_set():
                if gone is not None:
                    gone()
                with contextlib.suppress(OSError):
                    process.kill()

        def relay(stream: IO[bytes], kind: str) -> None:
            if collect is None:
                channel.relay(stream, kind)
            else:
                collect.extend(_chunks(stream))

        streams = ((process.stdout, out), (process.stderr, FRAME_STDERR))
        relays = [
            threading.Thread(target=relay, args=(stream, kind), daemon=True)
            for stream, kind in streams
            if stream is not None
        ]
        threading.Thread(target=stop, daemon=True).start()
        for thread in relays:
            thread.start()
        try:
            code = process.wait()
            for thread in relays:
                thread.join()
        finally:
            done.set()
        return code


def _named(request: Mapping[str, object], session: _Session) -> tuple[str | None, str | None]:
    """The image, or the Dockerfile relative to the workspace, that a request names."""
    image = request.get(FIELD_IMAGE)
    dockerfile = request.get(FIELD_DOCKERFILE)
    if (image is None) == (dockerfile is None):
        raise BrokerRefusal(
            f"une demande nomme une image ou un Dockerfile, l'un des deux ; "
            f"{_ONLY_IMAGE_AND_COMMAND}."
        )
    if dockerfile is not None:
        return None, container_image.dockerfile_path(session.workspace, dockerfile)
    if not isinstance(image, str) or len(image) > _IMAGE_MAX or not _IMAGE.fullmatch(image):
        raise BrokerRefusal(f"{image!r} n'est pas le nom d'une image ; {_ONLY_IMAGE_AND_COMMAND}.")
    return image, None


class _Channel:
    """The broker's side of one connection: frames sent one at a time, its end observed."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._lock = threading.Lock()
        self._watched = False
        self.closed = threading.Event()

    def send(self, data: bytes) -> None:
        with self._lock:
            if self.closed.is_set():
                return
            try:
                self._connection.sendall(data)
            except OSError:
                self.closed.set()

    def say(self, message: str) -> None:
        """A line of the broker's own, on the program's stderr."""
        self.write_stderr(f"{PROGRAM} : {message}\n".encode())

    def write_stderr(self, data: bytes) -> None:
        """Bytes on the program's stderr."""
        if data:
            self.send(frame(**{FRAME_STDERR: base64.b64encode(data).decode("ascii")}))

    def relay(self, stream: IO[bytes], kind: str) -> None:
        """Relays `stream` until it ends, chunk by chunk."""
        for chunk in _chunks(stream):
            self.send(frame(**{kind: base64.b64encode(chunk).decode("ascii")}))

    def watch(self) -> None:
        """Observes the connection's end from now on: `closed` is set when it comes."""
        if not self._watched:
            self._watched = True
            threading.Thread(target=self.wait_closed, daemon=True).start()

    def wait_closed(self) -> None:
        """Returns once the program has closed its side, or the connection is broken."""
        try:
            while self._connection.recv(1):
                pass
        except OSError:
            pass
        self.closed.set()


def _chunks(stream: IO[bytes]) -> Iterator[bytes]:
    read = getattr(stream, "read1", stream.read)
    while True:
        try:
            chunk = read(_CHUNK)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        yield chunk


def _key(token: str) -> str:
    """What the registry keeps of a token: its digest, so that a lookup leaks nothing of it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def write_launchers(folder: Path) -> Path:
    """Writes `maestro-container` in `folder`, for bash and for cmd, and returns `folder`.

    Both run the client file under this interpreter, isolated (`-I`): it imports
    nothing but the standard library. The folder carries no low label: the agent runs
    the program and cannot change it, though nothing it would change there would open
    more than the broker serves.

    The sh launcher turns off, for its own `exec` only, the conversion by which Git
    Bash rewrites an argument that looks like a POSIX path into a Windows one (#1439):
    what follows the image is read in the container, where `/workspace` is a path and
    `C:/…/workspace` is not.
    """
    python = Path(getattr(sys, "_base_executable", None) or sys.executable)
    client = Path(__file__).with_name("container_broker_client.py")
    (folder / PROGRAM).write_text(
        "#!/bin/sh\n"
        "export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'\n"
        f"exec {_sh(python)} -I {_sh(client)} \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    (folder / f"{PROGRAM}.cmd").write_text(
        f'@"{python}" -I "{client}" %*\r\n@exit /b %ERRORLEVEL%\r\n',
        encoding="utf-8",
        newline="",
    )
    if os.name != "nt":
        (folder / PROGRAM).chmod(0o755)
    return folder


def _sh(path: Path) -> str:
    """`path` quoted for sh, with forward slashes, which Git Bash runs as well."""
    return "'" + path.as_posix().replace("'", "'\\''") + "'"


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _spawn_docker(argv: Sequence[str], stdin: IO[bytes] | None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - composed argv, no shell
        list(argv),
        stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_no_window(),
    )


def _run_docker(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603 - composed argv, no shell
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=DOCKER_TIMEOUT_S,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(list(argv), NOT_RUN, b"", str(exc).encode())


def _remove_container(name: str) -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "rm", "--force", name],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=REMOVE_TIMEOUT_S,
            check=False,
            **_no_window(),
        )


_SHARED: ContainerBroker | None = None
_SHARED_LOCK = threading.Lock()


def shared() -> ContainerBroker:
    """The broker of this process, created once; its server starts with its first session."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = ContainerBroker()
        return _SHARED


__all__ = [
    "BUILD_PREFIX",
    "CONTAINER_PREFIX",
    "ENV_ADDRESS",
    "ENV_TOKEN",
    "NETWORK",
    "NOT_RUN",
    "PROGRAM",
    "BrokerRefusal",
    "BrokerSession",
    "ContainerBroker",
    "available",
    "shared",
    "write_launchers",
]
