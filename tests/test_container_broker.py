"""An agent at low integrity has a command played in a container Maestro composes (#1725).

Two halves, as in `test_low_integrity.py`. The first runs everywhere: the real broker,
on its real socket, with the real program, and docker replaced by a double that records
the `docker run` it was given. It proves what is composed, what is relayed, and what is
refused, with its reason. The second runs on Windows, with Docker's engine answering at
the person's level: a process born at low integrity runs `maestro-container` from its
Git Bash, as an agent does, and the container it obtains is looked at from inside.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import IO

import pytest

from maestro.machine import LIGNE_COURTIER, LIGNE_INTEGRITE_BASSE, Machine
from maestro.projects.model import Perimeter, Project, Vcs
from maestro.providers.base import Credentials
from maestro.providers.claude import ClaudeProvider
from maestro.sandbox import (
    container,
    container_broker,
    container_image,
    low_integrity,
    low_session,
)
from maestro.sandbox.arbre import Arbre
from maestro.sandbox.container_broker import ContainerBroker
from maestro.sandbox.container_broker_client import (
    ENV_ADDRESS,
    ENV_TOKEN,
    FRAME_EXIT,
    NOT_RUN,
    PROGRAM,
    request,
)

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "maestro" / "sandbox" / "container_broker_client.py"
GIT = shutil.which("git")
WINDOWS = sys.platform == "win32"

#: A double of docker: writes bytes on both streams, then exits with a code of its own.
_FAKE_DOCKER = (
    "import sys\n"
    "sys.stdout.buffer.write(b'dehors\\n'); sys.stdout.flush()\n"
    "sys.stderr.buffer.write(b'erreur\\n'); sys.stderr.flush()\n"
    "sys.exit(7)\n"
)


class _Docker:
    """What the broker was given to run and to remove, and what plays in docker's place."""

    def __init__(self, script: str = _FAKE_DOCKER) -> None:
        self.script = script
        self.runs: list[list[str]] = []
        self.removed: list[str] = []
        self.processes: list[subprocess.Popen[bytes]] = []

    def spawn(self, argv: Sequence[str], stdin: IO[bytes] | None = None) -> subprocess.Popen[bytes]:
        self.runs.append(list(argv))
        process = subprocess.Popen(
            [sys.executable, "-c", self.script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.processes.append(process)
        return process

    def remove(self, name: str) -> None:
        self.removed.append(name)


@pytest.fixture
def docker() -> _Docker:
    return _Docker()


@pytest.fixture
def broker(docker: _Docker) -> Iterator[ContainerBroker]:
    served = ContainerBroker(spawn=docker.spawn, remove=docker.remove)
    yield served
    served.shutdown()
    for process in docker.processes:
        if process.poll() is None:
            process.kill()
            process.wait()


def _program(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """`maestro-container` as the agent runs it: the client file, isolated."""
    return subprocess.run(
        [sys.executable, "-I", str(CLIENT), *arguments],
        env={**os.environ, **env},
        capture_output=True,
        timeout=60,
        check=False,
    )


def _ask(broker: ContainerBroker, body: dict | bytes) -> list[dict]:
    """A raw request to the broker, and every frame of its answer."""
    address = broker.address
    assert address is not None
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8") + b"\n"
    with socket.create_connection(address, timeout=30) as connection:
        connection.sendall(raw)
        lines = connection.makefile("rb").read().splitlines()
    return [json.loads(line) for line in lines]


def _volumes(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, word in enumerate(argv) if word == "--volume"]


def _envs(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, word in enumerate(argv) if word == "--env"]
    return dict(pair.split("=", 1) for pair in pairs)


def _git(cwd: Path, *arguments: str) -> None:
    assert GIT is not None
    done = subprocess.run(
        [GIT, "-c", "user.name=Personne", "-c", "user.email=personne@localhost", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def _versioned_project(tmp_path: Path, project_id: str = "prj-depensio") -> tuple[Path, Project]:
    """A versioned project that versions a `.env`, and the worktree of a task."""
    root = tmp_path / "depensio"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# Dépensio\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=ne-doit-pas-sortir\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    workspace = tmp_path / "espaces" / "tache"
    workspace.parent.mkdir()
    _git(root, "worktree", "add", "-q", "-b", "maestro/tache", str(workspace), "main")
    project = Project(
        id=project_id,
        name="Dépensio",
        root=root.as_posix(),
        origin="existant",
        vcs=Vcs(type="git", base_branch="main"),
        perimeter=Perimeter(),
    )
    return workspace, project


# --- What the broker composes and relays ----------------------------------------------------


def test_the_program_gets_the_output_and_the_code_of_a_container_composed_on_its_workspace(
    broker, docker, tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ne-doit-pas-entrer")
    session = broker.open(tmp_path)

    done = _program(session.env, "python:3.11", "pytest", "-q", "--privileged")

    assert done.returncode == 7, done.stderr
    assert done.stdout == b"dehors\n"
    assert done.stderr == b"erreur\n"
    (argv,) = docker.runs
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--interactive" not in argv
    assert argv[argv.index("--name") + 1].startswith(container_broker.CONTAINER_PREFIX)
    # The contract of docs/17 §3: the workspace only, read-only root, non-root, no
    # capability, capped, outbound network only.
    assert _volumes(argv) == [f"{tmp_path}:{container.POINT_MONTAGE}"]
    assert argv[argv.index("--workdir") + 1] == container.POINT_MONTAGE
    assert "--read-only" in argv
    assert argv[argv.index("--user") + 1] == f"{container.UID_AGENT}:{container.UID_AGENT}"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in argv
    assert argv[argv.index("--pids-limit") + 1] == str(container.PIDS_MAX)
    assert argv[argv.index("--memory") + 1] == container.MEMOIRE_MAX
    assert argv[argv.index("--cpus") + 1] == container.CPUS_MAX
    assert argv[argv.index("--network") + 1] == "bridge"
    options = set(argv[: argv.index("python:3.11")])
    assert not {"--publish", "-p", "--privileged", "--cap-add"} & options
    # Nothing of the host's environment, the model's credentials least of all.
    assert not {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"} & set(_envs(argv))
    assert _envs(argv)["HOME"] == "/home/agent"
    # What the agent named comes after the image, where docker reads no option.
    assert argv[-4:] == ["python:3.11", "pytest", "-q", "--privileged"]


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_a_versioned_project_s_container_gets_its_own_repository_and_its_masks(
    broker, docker, tmp_path
):
    workspace, project = _versioned_project(tmp_path)
    session = broker.open(workspace, project)

    done = _program(session.env, "alpine:3", "git", "status")

    assert done.returncode == 7, done.stderr
    (argv,) = docker.runs
    volumes = _volumes(argv)
    targets = {volume.removesuffix(":ro").rsplit(":/", 1)[1] for volume in volumes}
    assert {"workspace", "workspace/.env", "workspace/.git", "maestro/depot"} <= targets
    depot = next(v for v in volumes if v.endswith(":/maestro/depot")).rsplit(":/", 1)[0]
    # Rebuilt for the command, removed after it: never read by the host's git.
    assert not Path(depot).exists()
    assert not any(project.root_path.resolve() == Path(v.rsplit(":/", 1)[0]) for v in volumes)


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_the_program_gets_its_code_once_the_repository_is_removed_and_the_turn_freed(
    broker, docker, tmp_path, monkeypatch
):
    """The program returns on its code (#1748): what it does next finds nothing of the command.

    The race is forced by a barrier, never left to a delay: whatever the broker would do
    once a code has left it is held until the program has looked — at the container's
    repository, then at a second request of its project.
    """
    workspace, project = _versioned_project(tmp_path)
    session = broker.open(workspace, project)
    looked = threading.Event()
    send = container_broker._Channel.send

    def sending(channel: object, data: bytes) -> None:
        send(channel, data)
        if FRAME_EXIT in json.loads(data):
            looked.wait(timeout=60)

    monkeypatch.setattr(container_broker._Channel, "send", sending)
    second: subprocess.Popen[bytes] | None = None
    try:
        first = _program(session.env, "alpine:3", "git", "status")
        volumes = _volumes(docker.runs[0])
        depot = next(v for v in volumes if v.endswith(":/maestro/depot")).rsplit(":/", 1)[0]
        left = Path(depot).exists()
        second = subprocess.Popen(
            [sys.executable, "-I", str(CLIENT), "alpine:3", "true"],
            env={**os.environ, **session.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert second.stderr is not None
        told = second.stderr.readline().decode("utf-8")
    finally:
        looked.set()
        if second is not None:
            second.communicate(timeout=60)

    assert first.returncode == 7, first.stderr
    assert not left, "le programme a eu son code avant que le dépôt du conteneur soit retiré"
    assert "attend son tour" not in told, told
    assert second.returncode == 7
    assert not Path(depot).exists()


def test_a_second_request_of_the_session_waits_for_the_first_and_is_told(tmp_path):
    slow = _Docker("import time, sys; time.sleep(1.5); sys.exit(0)\n")
    served = ContainerBroker(spawn=slow.spawn, remove=slow.remove)
    try:
        session = served.open(tmp_path)
        first = subprocess.Popen(
            [sys.executable, "-I", str(CLIENT), "alpine:3", "true"],
            env={**os.environ, **session.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 30
        while not slow.runs and time.monotonic() < deadline:
            time.sleep(0.05)
        second = _program(session.env, "alpine:3", "true")
        first.communicate(timeout=60)
    finally:
        served.shutdown()
    assert second.returncode == 0, second.stderr
    assert "attend son tour" in second.stderr.decode("utf-8")
    assert len(slow.runs) == 2


def test_a_request_whose_program_goes_away_has_its_container_removed(tmp_path):
    endless = _Docker("import time; time.sleep(120)\n")
    served = ContainerBroker(spawn=endless.spawn, remove=endless.remove)
    try:
        session = served.open(tmp_path)
        address = served.address
        assert address is not None
        with socket.create_connection(address, timeout=30) as connection:
            connection.sendall(request(session.env[ENV_TOKEN], "alpine:3", ["sleep", "120"]))
            deadline = time.monotonic() + 30
            while not endless.runs and time.monotonic() < deadline:
                time.sleep(0.05)
        deadline = time.monotonic() + 30
        while not endless.removed and time.monotonic() < deadline:
            time.sleep(0.05)
        name = endless.runs[0][endless.runs[0].index("--name") + 1]
        assert endless.removed == [name]
        endless.processes[0].wait(timeout=30)
    finally:
        served.shutdown()
        for process in endless.processes:
            if process.poll() is None:
                process.kill()


# --- What the broker refuses, and why --------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ({"volumes": ["C:/:/hote"]}, "`volumes`"),
        ({"network": "host"}, "`network`"),
        ({"privileged": True}, "`privileged`"),
        ({"workspace": "C:/espace/d-un-autre"}, "`workspace`"),
    ],
)
def test_a_request_that_names_a_mount_a_network_a_privilege_or_a_workspace_is_refused(
    broker, docker, tmp_path, extra, named
):
    session = broker.open(tmp_path)
    body = {"token": session.env[ENV_TOKEN], "image": "alpine:3", "command": ["id"], **extra}

    (answer,) = _ask(broker, body)

    assert named in answer["refused"]
    assert "Maestro compose le conteneur" in answer["refused"]
    assert docker.runs == []


def test_the_images_docker_names_are_images():
    for image in (
        "alpine:3",
        "python:3.11-slim",
        "ghcr.io/owner/projet/outil:v1.2",
        "localhost:5000/maestro-pytest:877f2af7550f",
        "busybox@sha256:" + "a" * 64,
    ):
        assert container_broker._IMAGE.fullmatch(image), image


@pytest.mark.parametrize(
    "image", ["--privileged", "-v", "C:/:/hote", "alpine 3", "Alpine:3", "a" * 256, ""]
)
def test_an_image_that_is_not_one_is_refused_before_docker_reads_it(
    broker, docker, tmp_path, image
):
    session = broker.open(tmp_path)

    done = _program(session.env, image, "id")

    assert done.returncode == NOT_RUN
    text = done.stderr.decode("utf-8")
    assert "refusé" in text and "n'est pas le nom d'une image" in text
    assert docker.runs == []


def test_a_request_without_a_command_is_refused(broker, docker, tmp_path):
    session = broker.open(tmp_path)

    (answer,) = _ask(broker, {"token": session.env[ENV_TOKEN], "image": "alpine:3", "command": []})

    assert "la commande manque" in answer["refused"]
    assert docker.runs == []


def test_a_token_that_is_not_a_live_session_s_is_refused(broker, docker, tmp_path):
    session = broker.open(tmp_path)
    session.close()
    stranger = broker.open(tmp_path / "ailleurs")

    for token in (session.env[ENV_TOKEN], "jeton-invente", None):
        (answer,) = _ask(broker, {"token": token, "image": "alpine:3", "command": ["id"]})
        assert "aucune session vivante" in answer["refused"]
    done = _program({**stranger.env, ENV_TOKEN: session.env[ENV_TOKEN]}, "alpine:3", "id")
    assert done.returncode == NOT_RUN
    assert "aucune session vivante" in done.stderr.decode("utf-8")
    assert docker.runs == []


def test_what_is_not_a_request_is_refused_with_its_reason(broker, docker, tmp_path):
    broker.open(tmp_path)

    for raw, reason in ((b"pas du json\n", "objet JSON"), (b"[1, 2]\n", "objet JSON")):
        (answer,) = _ask(broker, raw)
        assert reason in answer["refused"]
    assert docker.runs == []


def test_without_a_broker_the_program_says_that_nothing_ran(monkeypatch):
    monkeypatch.delenv(ENV_ADDRESS, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)

    done = _program({}, "alpine:3", "id")

    assert done.returncode == NOT_RUN
    assert "aucun courtier de conteneur" in done.stderr.decode("utf-8")


def test_a_broker_that_is_gone_is_said_so(broker, tmp_path):
    session = broker.open(tmp_path)
    broker.shutdown()

    done = _program(session.env, "alpine:3", "id")

    assert done.returncode == NOT_RUN
    assert "ne répond pas" in done.stderr.decode("utf-8")


# --- The session that holds it ---------------------------------------------------------------


def test_a_low_session_receives_the_broker_and_ends_it_with_itself(broker, tmp_path, monkeypatch):
    monkeypatch.setattr(low_session, "maestro_variables", lambda: frozenset())
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    workbench = tmp_path / "etabli"
    workbench.mkdir()
    monkeypatch.setattr(
        low_session,
        "prepare",
        lambda workspace, **kw: low_session.LowSession(workbench, {"PATH": "C:/copie-de-git/cmd"}),
    )
    provider = ClaudeProvider(Credentials(), integrite_basse=True, courtier=broker)
    assert provider.courtier_conteneur

    session = provider._session_basse(tmp_path / "espace")

    assert session is not None
    token = session.env[ENV_TOKEN]
    assert session.env[ENV_ADDRESS] == "{}:{}".format(*broker.address)
    launchers, rest = session.env["PATH"].split(os.pathsep, 1)
    assert rest == "C:/copie-de-git/cmd"
    assert (Path(launchers) / PROGRAM).is_file()
    assert not any(name.upper().startswith("MAESTRO_") for name in (ENV_ADDRESS, ENV_TOKEN))
    session.close()
    (answer,) = _ask(broker, {"token": token, "image": "alpine:3", "command": ["id"]})
    assert "aucune session vivante" in answer["refused"]
    assert not workbench.exists()


def test_no_broker_where_the_agent_is_not_lowered(broker):
    isolated = container.IsolationConfig(image="img", reseau="bridge", shim=Path("shim"))
    assert not ClaudeProvider(Credentials(), courtier=broker).courtier_conteneur
    assert not ClaudeProvider(
        Credentials(), isolation=isolated, integrite_basse=True, courtier=broker
    ).courtier_conteneur


def test_the_provider_built_from_settings_offers_the_broker_where_docker_is(monkeypatch):
    import dataclasses

    from maestro.config import load_settings

    monkeypatch.setattr(low_integrity, "available", lambda: True)
    settings = dataclasses.replace(load_settings(), isolation=None)
    monkeypatch.setattr(container_broker, "available", lambda: True)
    assert ClaudeProvider.from_settings(settings).courtier_conteneur
    monkeypatch.setattr(container_broker, "available", lambda: False)
    assert not ClaudeProvider.from_settings(settings).courtier_conteneur


def test_the_machine_tells_a_low_agent_how_to_ask_and_what_it_gets():
    machine = Machine(systeme="Windows 11", temporaire=r"C:\Temp", windows=True)

    told = machine.consigne(integrite_basse=True, courtier=True)

    assert LIGNE_INTEGRITE_BASSE in told and LIGNE_COURTIER in told
    for fact in (
        f"`{PROGRAM} IMAGE COMMANDE [ARGUMENT...]`",
        f"`{PROGRAM} --dockerfile CHEMIN COMMANDE [ARGUMENT...]`",
        "ne la reconstruit que si ce contenu change",
        "remplace la précédente du projet",
        "sans réseau de l'hôte, secret ni agent ssh",
        "Un conteneur à la fois par projet",
        "/workspace",
        "lecture seule",
        f"utilisateur {container.UID_AGENT}",
        "réseau sort sans rien recevoir",
        "refusés, avec leur raison",
        f"code {NOT_RUN}",
    ):
        assert fact in LIGNE_COURTIER
    assert LIGNE_COURTIER not in machine.consigne(integrite_basse=True)
    assert LIGNE_COURTIER not in machine.consigne(courtier=True)


def test_the_launchers_run_the_client_file_isolated(tmp_path):
    container_broker.write_launchers(tmp_path)

    sh = (tmp_path / PROGRAM).read_text(encoding="utf-8")
    cmd = (tmp_path / f"{PROGRAM}.cmd").read_text(encoding="utf-8")

    assert sh.startswith("#!/bin/sh\n")
    assert "export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'\nexec " in sh
    assert f"-I '{CLIENT.as_posix()}' \"$@\"" in sh
    assert f'-I "{CLIENT}" %*' in cmd


# --- The image a Dockerfile of the workspace builds (#1726) ----------------------------------


def _tar(path: Path, files: dict[str, bytes], *, mtime: int = 0) -> Path:
    """A tar as docker exports a context: one entry per file, its folders before it."""
    with tarfile.open(path, "w") as archive:
        folders = sorted({str(PurePosixPath(n).parent) for n in files} - {"."})
        for folder in folders:
            info = tarfile.TarInfo(folder)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = mtime
            archive.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            info.mtime = mtime
            archive.addfile(info, io.BytesIO(content))
    return path


def _output_dest(value: str) -> Path:
    fields = next(csv.reader([value]))
    return Path(dict(field.split("=", 1) for field in fields)["dest"])


class _Engine:
    """docker as the broker builds with it: the context step writes a tar, the build records.

    `files` is what the context step exports. Images live in `images`; `listing` adds
    lines that `docker images` lists before them.
    """

    def __init__(
        self, files: dict[str, bytes], *, build_code: int = 0, listing: Sequence[str] = ()
    ) -> None:
        self.files = files
        self.build_code = build_code
        self.listing = list(listing)
        self.images: set[str] = set()
        self.spawned: list[list[str]] = []
        self.calls: list[list[str]] = []
        self.ignores: list[str] = []
        self.contexts: list[bytes] = []
        self.processes: list[subprocess.Popen[bytes]] = []

    def spawn(self, argv: Sequence[str], stdin: IO[bytes] | None = None) -> subprocess.Popen[bytes]:
        argv = list(argv)
        self.spawned.append(argv)
        script = "import sys; print('joue'); sys.exit(0)"
        if "--output" in argv:
            dockerfile = argv[argv.index("--file") + 1]
            self.ignores.append(Path(dockerfile + ".dockerignore").read_text(encoding="utf-8"))
            _tar(_output_dest(argv[argv.index("--output") + 1]), self.files)
            script = "import sys; sys.stderr.write('#1 contexte\\n'); sys.exit(0)"
        elif argv[-1] == "-":
            assert stdin is not None
            self.contexts.append(stdin.read())
            if self.build_code == 0:
                self.images.add(argv[argv.index("--tag") + 1])
            script = f"import sys; sys.stderr.write('#2 construit\\n'); sys.exit({self.build_code})"
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.processes.append(process)
        return process

    def docker(self, argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        argv = list(argv)
        self.calls.append(argv)
        code, output = 1, b""
        if argv[1:3] == ["image", "inspect"]:
            code = 0 if argv[-1] in self.images else 1
        elif argv[1] == "images":
            code, output = 0, "\n".join([*self.listing, *sorted(self.images)]).encode()
        elif argv[1] == "rmi":
            self.images.discard(argv[2])
            self.listing = [line for line in self.listing if line != argv[2]]
            code = 0
        return subprocess.CompletedProcess(argv, code, output, b"")

    def builds(self) -> list[list[str]]:
        return [argv for argv in self.spawned if argv[-1] == "-"]

    def removed(self) -> list[str]:
        return [argv[2] for argv in self.calls if argv[1] == "rmi"]


@pytest.fixture
def engine() -> Iterator[_Engine]:
    served = _Engine({"docker/app.Dockerfile": b"FROM alpine:3\n", "app/x.txt": b"un\n"})
    yield served
    for process in served.processes:
        if process.poll() is None:
            process.kill()
            process.wait()


def _builder(engine: _Engine) -> ContainerBroker:
    return ContainerBroker(spawn=engine.spawn, remove=lambda name: None, docker=engine.docker)


def _unversioned_project(root: Path, project_id: str = "prj-sansgit") -> Project:
    """A project that works in its root (#839): its root is the workspace."""
    return Project(id=project_id, name="Sans git", root=root.as_posix(), perimeter=Perimeter())


def test_a_project_s_repository_is_a_docker_name_of_its_own():
    assert container_image.repository("prj-7f3a1b2c") == "maestro-broker/prj-7f3a1b2c"
    odd = container_image.repository("Prj Dépensio")
    assert odd.startswith("maestro-broker/prj-d")
    assert container_broker._IMAGE.fullmatch(odd + ":0123456789ab")
    assert odd != container_image.repository("Prj Dépensio!")


@pytest.mark.parametrize(
    ("requested", "reason"),
    [
        ("docker/app.Dockerfile", "ne nomme pas un Dockerfile"),
        ("", "ne nomme pas un Dockerfile"),
        (None, "ne nomme pas un Dockerfile"),
        ("{ws}", "n'est pas dans ton espace"),
        ("{ws}/../autre/Dockerfile", "n'est pas dans ton espace"),
        ("{other}/Dockerfile", "n'est pas dans ton espace"),
        ("{ws}/docker/*.Dockerfile", "comme un motif"),
        ("{ws}/!Dockerfile", "comme un motif"),
    ],
)
def test_a_dockerfile_outside_the_workspace_or_read_as_a_pattern_is_refused(
    tmp_path, requested, reason
):
    workspace = tmp_path / "espace"
    workspace.mkdir()
    if isinstance(requested, str):
        requested = requested.format(ws=workspace, other=tmp_path / "espace-bis")
    with pytest.raises(container_image.ImageRefusal, match=reason):
        container_image.dockerfile_path(workspace, requested)


def test_a_dockerfile_of_the_workspace_is_named_by_its_path_in_it(tmp_path):
    requested = os.path.join(tmp_path, "docker", "ci", "app.Dockerfile")

    assert container_image.dockerfile_path(tmp_path, requested) == "docker/ci/app.Dockerfile"


def test_the_context_s_ignore_file_ends_with_the_dockerfile_then_the_masks():
    agent = b"*\n!app\n!.env"

    ignore = container_image.context_ignore(agent, "docker/app.Dockerfile", [".env", "secrets"])

    assert ignore.startswith(agent + b"\n")
    lines = ignore.decode("utf-8").splitlines()
    # The last pattern that matches decides: the agent's `!.env` loses to the mask.
    assert lines[-3:] == ["!docker/app.Dockerfile", ".env", "secrets"]


def test_a_mask_docker_would_read_as_a_pattern_refuses_the_build():
    with pytest.raises(container_image.ImageRefusal, match="motif"):
        container_image.context_ignore(b"", "Dockerfile", ["config/[prod].env"])


def test_the_context_s_fingerprint_follows_content_and_paths_never_times(tmp_path):
    files = {"Dockerfile": b"FROM alpine:3\n", "app/x.txt": b"un\n"}

    def fingerprint(name: str, content: dict[str, bytes], mtime: int = 0) -> str:
        tar = _tar(tmp_path / name, content, mtime=mtime)
        return container_image.inspect_context(tar, "Dockerfile", [])

    same = fingerprint("a.tar", files)
    assert fingerprint("b.tar", files, mtime=1_700_000_000) == same
    assert fingerprint("c.tar", {**files, "app/x.txt": b"deux\n"}) != same
    assert fingerprint("d.tar", {"Dockerfile": files["Dockerfile"], "app/y.txt": b"un\n"}) != same
    assert fingerprint("e.tar", {**files, "Dockerfile": b"FROM alpine:3.20\n"}) != same


@pytest.mark.parametrize("leak", [".env", "secrets/cle.pem"])
def test_a_masked_path_in_the_exported_context_refuses_the_build(tmp_path, leak):
    tar = _tar(tmp_path / "c.tar", {"Dockerfile": b"FROM alpine:3\n", leak: b"SECRET\n"})

    with pytest.raises(container_image.ImageRefusal, match="que le périmètre exclut"):
        container_image.inspect_context(tar, "Dockerfile", [".env", "secrets"])


def test_a_dockerfile_absent_from_the_exported_context_or_too_large_is_refused(tmp_path):
    absent = _tar(tmp_path / "a.tar", {"app/x.txt": b"un\n"})
    large = _tar(
        tmp_path / "b.tar", {"Dockerfile": b"#" * (container_image.DOCKERFILE_MAX_BYTES + 1)}
    )

    with pytest.raises(container_image.ImageRefusal, match="atteint par un lien"):
        container_image.inspect_context(absent, "Dockerfile", [])
    with pytest.raises(container_image.ImageRefusal, match="dépasse"):
        container_image.inspect_context(large, "Dockerfile", [])


def test_stale_images_are_the_project_s_others_never_the_current_nor_another_s():
    listing = "\n".join(
        [
            "maestro-broker/prj-a:111111111111",
            "maestro-broker/prj-a:222222222222",
            "maestro-broker/prj-a:<none>",
            "maestro-broker/prj-ab:333333333333",
            "python:3.11",
        ]
    )

    found = container_image.stale(
        listing, "maestro-broker/prj-a", "maestro-broker/prj-a:222222222222"
    )

    assert found == ["maestro-broker/prj-a:111111111111"]
    assert len(container_image.stale(listing, "maestro-broker/prj-a", None)) == 2


def test_the_build_passes_no_entitlement_secret_ssh_or_network(tmp_path):
    build = container_image.build_argv("maestro-broker/prj-a:0123456789ab", "d/Dockerfile", "ns")
    context = container_image.context_argv(tmp_path / "avec,virgule", tmp_path / "espace")

    for argv in (build, context):
        assert argv[:3] == ["docker", "buildx", "build"]
        assert not {"--allow", "--secret", "--ssh", "--network", "--privileged"} & set(argv)
    assert build[-1] == "-" and "--load" in build
    assert build[build.index("--file") + 1] == "d/Dockerfile"
    assert f"{container_image.CACHE_NAMESPACE_ARG}=ns" in build
    output = context[context.index("--output") + 1]
    assert _output_dest(output) == tmp_path / "avec,virgule" / container_image.CONTEXT_TAR_NAME
    assert context[-1] == str(tmp_path / "espace")


def test_the_agent_s_ignore_file_is_the_one_docker_would_read(tmp_path):
    (tmp_path / "docker").mkdir()
    (tmp_path / ".dockerignore").write_bytes(b"racine\n")

    assert container_image.agent_ignore(tmp_path, "docker/app.Dockerfile") == b"racine\n"
    (tmp_path / "docker" / "app.Dockerfile.dockerignore").write_bytes(b"propre\n")
    assert container_image.agent_ignore(tmp_path, "docker/app.Dockerfile") == b"propre\n"
    assert container_image.agent_ignore(tmp_path / "docker", "Dockerfile") == b""


def _link_out(link: Path, target: Path) -> None:
    """A folder link from the workspace to `target`: a junction under Windows, as an agent
    makes one without privilege; a symbolic link elsewhere."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_a_file_reached_through_a_link_out_of_the_workspace_is_not_read(tmp_path):
    workspace = tmp_path / "espace"
    workspace.mkdir()
    closed = tmp_path / "maestro"
    closed.mkdir()
    (closed / "app.Dockerfile.dockerignore").write_bytes(b"SECRET=jeton-de-maestro\n")
    _link_out(workspace / "docker", closed)

    with pytest.raises(container_image.ImageRefusal, match="hors de ton espace"):
        container_image.read_inside(workspace, "docker/app.Dockerfile.dockerignore", 1024)
    with pytest.raises(container_image.ImageRefusal, match="hors de ton espace"):
        container_image.agent_ignore(workspace, "docker/app.Dockerfile")
    assert container_image.read_inside(workspace, "absent", 1024) is None


def test_a_dockerfile_request_builds_the_project_s_image_then_plays_in_it(engine, tmp_path):
    project = _unversioned_project(tmp_path)
    (tmp_path / ".dockerignore").write_bytes(b"*\n!app\n!.env\n")
    (tmp_path / ".env").write_text("SECRET=x\n", encoding="utf-8")
    broker = _builder(engine)
    try:
        session = broker.open(tmp_path, project)
        done = _program(
            session.env, "--dockerfile", str(tmp_path / "docker" / "app.Dockerfile"), "cat", "x"
        )
    finally:
        broker.shutdown()

    assert done.returncode == 0, done.stderr
    context, build, run = engine.spawned
    # The context: Maestro's own Dockerfile, on the workspace, the agent's patterns first.
    assert (
        Path(context[context.index("--file") + 1]).name == container_image.CONTEXT_DOCKERFILE_NAME
    )
    assert context[-1] == str(tmp_path)
    (ignore,) = engine.ignores
    assert ignore.startswith("*\n!app\n!.env\n")
    assert ignore.splitlines()[-2:] == ["!docker/app.Dockerfile", ".env"]
    # The image: from that tar, tagged by its fingerprint in the project's repository.
    image = build[build.index("--tag") + 1]
    assert image.startswith("maestro-broker/prj-sansgit:")
    assert len(image.rsplit(":", 1)[1]) == container_image.TAG_LENGTH
    with tarfile.open(fileobj=io.BytesIO(engine.contexts[0])) as archive:
        assert sorted(archive.getnames()) == ["app", "app/x.txt", "docker", "docker/app.Dockerfile"]
    # The command plays in it, on the contract of lot 1.
    assert run[:3] == ["docker", "run", "--rm"]
    assert run[-3:] == [image, "cat", "x"]
    assert "construction de l'image" in done.stderr.decode("utf-8")


def test_a_second_request_on_the_same_content_builds_nothing(engine, tmp_path):
    broker = _builder(engine)
    dockerfile = str(tmp_path / "docker" / "app.Dockerfile")
    try:
        session = broker.open(tmp_path, _unversioned_project(tmp_path))
        first = _program(session.env, "--dockerfile", dockerfile, "true")
        second = _program(session.env, f"--dockerfile={dockerfile}", "true")
    finally:
        broker.shutdown()

    assert first.returncode == 0 and second.returncode == 0, second.stderr
    assert len(engine.builds()) == 1
    assert "à jour : rien à construire" in second.stderr.decode("utf-8")
    assert engine.removed() == []


def test_a_new_image_removes_the_previous_one_of_its_project_and_no_other(engine, tmp_path):
    engine.listing = [
        "maestro-broker/prj-sansgit:aaaaaaaaaaaa",
        "maestro-broker/prj-sansgit-bis:bbbbbbbbbbbb",
        "python:3.11",
    ]
    broker = _builder(engine)
    try:
        session = broker.open(tmp_path, _unversioned_project(tmp_path))
        done = _program(
            session.env, "--dockerfile", str(tmp_path / "docker" / "app.Dockerfile"), "true"
        )
    finally:
        broker.shutdown()

    assert done.returncode == 0, done.stderr
    assert engine.removed() == ["maestro-broker/prj-sansgit:aaaaaaaaaaaa"]
    assert "1 image(s) précédente(s) de ton projet retirée(s)" in done.stderr.decode("utf-8")


def test_a_build_that_fails_plays_nothing_and_removes_nothing(tmp_path):
    engine = _Engine(
        {"Dockerfile": b"FROM alpine:3\n"},
        build_code=1,
        listing=["maestro-broker/prj-sansgit:aaaaaaaaaaaa"],
    )
    broker = _builder(engine)
    try:
        session = broker.open(tmp_path, _unversioned_project(tmp_path))
        done = _program(session.env, "--dockerfile", str(tmp_path / "Dockerfile"), "true")
    finally:
        broker.shutdown()

    assert done.returncode == NOT_RUN
    assert "la construction de l'image a échoué" in done.stderr.decode("utf-8")
    assert not [argv for argv in engine.spawned if argv[1] == "run"]
    assert engine.removed() == []


def test_a_dockerfile_the_scope_excludes_is_refused_before_anything_is_built(engine, tmp_path):
    (tmp_path / ".env").write_text("FROM alpine:3\n", encoding="utf-8")
    broker = _builder(engine)
    try:
        session = broker.open(tmp_path, _unversioned_project(tmp_path))
        done = _program(session.env, "--dockerfile", str(tmp_path / ".env"), "true")
    finally:
        broker.shutdown()

    assert done.returncode == NOT_RUN
    text = done.stderr.decode("utf-8")
    assert "refusé" in text and "exclu par le périmètre" in text
    assert engine.spawned == []


def test_a_session_without_a_project_has_its_images_removed_with_it(engine, tmp_path):
    broker = _builder(engine)
    try:
        session = broker.open(tmp_path)
        done = _program(
            session.env, "--dockerfile", str(tmp_path / "docker" / "app.Dockerfile"), "true"
        )
        (build,) = engine.builds()
        image = build[build.index("--tag") + 1]
        assert image.startswith("maestro-broker/session-")
        session.close()
    finally:
        broker.shutdown()

    assert done.returncode == 0, done.stderr
    assert engine.removed() == [image]


@pytest.mark.parametrize(
    "body",
    [
        {"image": "alpine:3", "dockerfile": "C:/espace/Dockerfile", "command": ["id"]},
        {"command": ["id"]},
    ],
)
def test_a_request_names_an_image_or_a_dockerfile_one_of_the_two(broker, docker, tmp_path, body):
    session = broker.open(tmp_path)

    (answer,) = _ask(broker, {"token": session.env[ENV_TOKEN], **body})

    assert "l'un des deux" in answer["refused"]
    assert docker.runs == []


def _until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def test_two_sessions_of_a_project_play_one_container_at_a_time(tmp_path):
    # Each container holds until the gate opens: what is simultaneous is so by a barrier.
    gate = tmp_path / "porte"
    held = _Docker(
        "import os, sys, time\n"
        f"while not os.path.exists({str(gate)!r}): time.sleep(0.02)\n"
        "sys.exit(0)\n"
    )
    served = ContainerBroker(spawn=held.spawn, remove=held.remove)
    workspace = tmp_path / "espace"
    other = tmp_path / "autre"
    workspace.mkdir()
    other.mkdir()
    project = _unversioned_project(workspace)

    def start(env: dict[str, str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-I", str(CLIENT), "alpine:3", "true"],
            env={**os.environ, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    started: list[subprocess.Popen[bytes]] = []
    try:
        first = start(served.open(workspace, project).env)
        started.append(first)
        _until(lambda: len(held.runs) == 1, "le premier conteneur n'a pas démarré")
        second = start(served.open(workspace, project).env)
        started.append(second)
        assert second.stderr is not None
        waiting = second.stderr.readline().decode("utf-8")
        alone = start(served.open(other, _unversioned_project(other, "prj-autre")).env)
        started.append(alone)
        _until(lambda: len(held.runs) == 2, "le conteneur d'un autre projet a attendu")
        gate.touch()
        answers = [process.communicate(timeout=60) for process in started]
    finally:
        gate.touch()
        served.shutdown()
        for process in [*started, *held.processes]:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert "un conteneur de ton projet tourne déjà ; celui-ci attend son tour" in waiting
    # The second container of the project ran after the other project's, never beside it.
    assert [_volumes(argv)[0].rsplit(":", 1)[0] for argv in held.runs] == [
        str(workspace),
        str(other),
        str(workspace),
    ]
    assert "attend son tour" not in answers[2][1].decode("utf-8")
    assert [process.returncode for process in started] == [0, 0, 0]


def test_the_program_sends_the_dockerfile_absolute_from_its_directory(tmp_path, monkeypatch):
    from maestro.sandbox import container_broker_client as client

    monkeypatch.chdir(tmp_path)

    assert client._what_to_play(["--dockerfile", "d/Dockerfile", "id"]) == (
        None,
        str(tmp_path / "d" / "Dockerfile"),
        ["id"],
    )
    assert client._what_to_play(["--dockerfile=Dockerfile", "id", "-u"])[1:] == (
        str(tmp_path / "Dockerfile"),
        ["id", "-u"],
    )
    assert client._what_to_play(["alpine:3", "id"]) == ("alpine:3", None, ["id"])


# --- On the machine: a low process, the real engine -----------------------------------------

#: The image of the real test: small, and enough for a shell.
_IMAGE = "alpine:3"


def _engine_answers() -> str | None:
    """Why the real test cannot play here, or `None`: Docker's engine and its image."""
    if not container_broker.available():
        return "docker est introuvable sur ce poste"
    probe = subprocess.run(
        ["docker", "image", "inspect", _IMAGE], capture_output=True, timeout=60, check=False
    )
    if probe.returncode == 0:
        return None
    version = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if version.returncode != 0:
        return "le moteur Docker ne répond pas au niveau de la personne"
    pulled = subprocess.run(
        ["docker", "pull", _IMAGE], capture_output=True, timeout=300, check=False
    )
    return None if pulled.returncode == 0 else f"l'image {_IMAGE} n'a pas pu être obtenue"


@pytest.mark.skipif(not WINDOWS, reason="the low integrity level exists only on Windows")
@pytest.mark.skipif(low_session.git_installation() is None, reason="Git for Windows is needed")
@pytest.mark.skipif(GIT is None, reason="git introuvable")
@pytest.mark.integrite_basse
def test_a_low_process_plays_a_command_in_a_container_maestro_composes_on_its_workspace(
    tmp_path, tmp_path_factory, monkeypatch
):
    reason = _engine_answers()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: tmp_path_factory.mktemp("outils"))
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    workspace, project = _versioned_project(tmp_path)
    (workspace / "a-cote").write_text("dans l'espace\n", encoding="utf-8")
    served = ContainerBroker()
    provider = ClaudeProvider(
        Credentials(), integrite_basse=True, courtier=served, dossier_cli=tmp_path / "cli"
    )
    session = provider._session_basse(workspace, project)
    assert session is not None and session.bash is not None
    # What plays in the container, read there: the host's bash expands nothing of it.
    (workspace / "dedans.sh").write_text(
        "id -u\n"
        "touch /etc/essai 2>/dev/null && echo racine-ecrite || echo racine-en-lecture-seule\n"
        "grep CapEff /proc/self/status\n"
        "cat /sys/fs/cgroup/pids.max /sys/fs/cgroup/memory.max\n"
        'printf "env:%s\\n" "$(wc -c < /workspace/.env)"\n'
        "cat /workspace/.git /maestro/depot/HEAD /workspace/a-cote\n"
        "echo sur-stderr >&2\n"
        "exit 7\n",
        encoding="utf-8",
        newline="\n",
    )
    # What the agent writes in its own shell: a script that asks, as one may.
    (workspace / "demande.sh").write_text(
        f"{PROGRAM} {_IMAGE} sh /workspace/dedans.sh\n"
        'echo "code:$?"\n'
        '"$SYSTEMROOT\\System32\\whoami.exe" //groups //fo csv\n',
        encoding="utf-8",
        newline="\n",
    )
    env = {**low_session.withhold(os.environ, session.withheld), **session.env}
    try:
        tree = Arbre.lancer(
            [str(session.bash), "demande.sh"], integrite_basse=True, env=env, cwd=workspace
        )
        try:
            assert tree.process.stdout is not None
            output = tree.process.stdout.read().decode("utf-8", "replace")
            assert tree.process.wait(timeout=600) == 0, output
        finally:
            tree.arreter()
            tree.fermer()
    finally:
        session.close()
        served.shutdown()
    lines = [line.strip() for line in output.splitlines()]
    # The asking process was low, and got the container's output and code.
    assert low_integrity.LOW_INTEGRITY_SID in output, output
    assert "code:7" in lines, output
    assert "sur-stderr" in lines, output
    # The container: non-root, read-only root, no capability, capped.
    assert str(container.UID_AGENT) in lines, output
    assert "racine-en-lecture-seule" in lines, output
    assert "CapEff:\t0000000000000000" in output, output
    assert str(container.PIDS_MAX) in lines, output
    assert str(2 * 1024**3) in lines, output
    # The workspace only, its `.env` masked, and the container's own repository.
    assert "env:0" in lines, output
    assert "gitdir: /maestro/depot" in lines, output
    assert "ref: refs/heads/maestro/tache" in lines, output
    assert "dans l'espace" in lines, output
    assert (workspace / ".env").read_text(encoding="utf-8") == "SECRET=ne-doit-pas-sortir\n"


def _low(session: low_session.LowSession, workspace: Path, script: str, body: str) -> str:
    """What a process born low writes when it runs `script` from its Git Bash, as an agent."""
    assert session.bash is not None
    (workspace / script).write_text(body, encoding="utf-8", newline="\n")
    env = {**low_session.withhold(os.environ, session.withheld), **session.env}
    tree = Arbre.lancer([str(session.bash), script], integrite_basse=True, env=env, cwd=workspace)
    try:
        assert tree.process.stdout is not None
        output = tree.process.stdout.read().decode("utf-8", "replace")
        assert tree.process.wait(timeout=900) == 0, output
    finally:
        tree.arreter()
        tree.fermer()
    return output


def _images_of(repository: str) -> list[str]:
    done = subprocess.run(
        container_image.listing_argv(repository), capture_output=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    return sorted(done.stdout.decode("utf-8").split())


def _person_builds(dockerfile: str, *options: str) -> str:
    """A build of the person's, at the person's level, that keeps nothing but its cache."""
    done = subprocess.run(
        ["docker", "buildx", "build", "--progress", "plain", "--output", "type=cacheonly"]
        + [*options, "-"],
        input=dockerfile.encode("utf-8"),
        capture_output=True,
        timeout=600,
        check=False,
    )
    output = done.stderr.decode("utf-8", "replace")
    assert done.returncode == 0, output
    return output


@pytest.mark.skipif(not WINDOWS, reason="the low integrity level exists only on Windows")
@pytest.mark.skipif(low_session.git_installation() is None, reason="Git for Windows is needed")
@pytest.mark.skipif(GIT is None, reason="git introuvable")
@pytest.mark.integrite_basse
def test_a_low_process_builds_its_project_s_image_and_it_does_not_pile_up(
    tmp_path, tmp_path_factory, monkeypatch
):
    reason = _engine_answers()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: tmp_path_factory.mktemp("outils"))
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    token = secrets.token_hex(4)
    workspace, project = _versioned_project(tmp_path, project_id=f"prj-essai-{token}")
    repository = container_image.repository(project.id)
    cache_id, marker = f"maestro-essai-1726-{token}", f"marque-{token}"
    # The agent's ignore file tries to bring back what the scope excludes.
    (workspace / ".dockerignore").write_text("*\n!app\n!.env\n!.git\n", encoding="utf-8")
    (workspace / "app").mkdir()
    (workspace / "app" / "x.txt").write_text("un\n", encoding="utf-8")
    (workspace / "b.txt").write_text("hors du contexte\n", encoding="utf-8")
    (workspace / "docker").mkdir()
    (workspace / "docker" / "essai.Dockerfile").write_text(
        "FROM alpine:3\nCOPY . /ctx\n"
        f"RUN --mount=type=cache,target=/cache,id={cache_id} touch /cache/{marker}\n",
        encoding="utf-8",
    )
    (workspace / "docker" / "hote.Dockerfile").write_text(
        "FROM alpine:3\nRUN --network=host true\n", encoding="utf-8"
    )
    served = ContainerBroker()
    provider = ClaudeProvider(
        Credentials(), integrite_basse=True, courtier=served, dossier_cli=tmp_path / "cli"
    )
    session = provider._session_basse(workspace, project)
    assert session is not None
    try:
        try:
            first = _low(
                session,
                workspace,
                "demande.sh",
                f"{PROGRAM} --dockerfile docker/essai.Dockerfile sh -c 'find /ctx | sort; id -u'\n"
                'echo "code1:$?"\n'
                f"{PROGRAM} --dockerfile docker/essai.Dockerfile true\n"
                'echo "code2:$?"\n'
                '"$SYSTEMROOT\\System32\\whoami.exe" //groups //fo csv\n',
            )
            # The person changes the content; the agent asks again, then asks for the host.
            (workspace / "app" / "x.txt").write_text("deux\n", encoding="utf-8")
            second = _low(
                session,
                workspace,
                "reprise.sh",
                f"{PROGRAM} --dockerfile=docker/essai.Dockerfile cat /ctx/app/x.txt\n"
                'echo "code3:$?"\n'
                f"{PROGRAM} --dockerfile docker/hote.Dockerfile true\n"
                'echo "code4:$?"\n',
            )
        finally:
            session.close()
            served.shutdown()
        remaining = _images_of(repository)
        probe = f"FROM alpine:3\nRUN --mount=type=cache,target=/cache,id={cache_id} ls /cache"
        unnamespaced = _person_builds(f"{probe} # {token}-sans\n")
        namespaced = _person_builds(
            f"{probe} # {token}-avec\n",
            "--build-arg",
            f"{container_image.CACHE_NAMESPACE_ARG}={repository}",
        )
    finally:
        listed = subprocess.run(
            container_image.listing_argv(repository), capture_output=True, timeout=60, check=False
        )
        for image in listed.stdout.decode("utf-8").split():
            subprocess.run(["docker", "rmi", image], capture_output=True, timeout=60, check=False)

    built = re.findall(r"construction de l'image (\S+) depuis `([^`]+)`", first + second)
    lines = [line.strip() for line in first.splitlines()]
    assert low_integrity.LOW_INTEGRITY_SID in first, first
    # 1. Built, then played in it, on a context the scope and the agent's ignore file shaped.
    assert "code1:0" in lines, first
    assert [line for line in lines if line.startswith("/ctx")] == [
        "/ctx",
        "/ctx/app",
        "/ctx/app/x.txt",
        "/ctx/docker",
        "/ctx/docker/essai.Dockerfile",
    ], first
    assert str(container.UID_AGENT) in lines, first
    # 2. The same content builds nothing.
    assert "code2:0" in lines, first
    assert "rien à construire" in first, first
    assert [dockerfile for _, dockerfile in built] == [
        "docker/essai.Dockerfile",
        "docker/essai.Dockerfile",
        "docker/hote.Dockerfile",
    ], first + second
    (old, _), (new, _), _ = built
    assert old != new and old.startswith(repository + ":")
    # 3. A new image removes the previous one, and plays.
    tail = [line.strip() for line in second.splitlines()]
    assert "deux" in tail and "code3:0" in tail, second
    assert "1 image(s) précédente(s) de ton projet retirée(s)" in second, second
    # 4. No entitlement: a Dockerfile that asks for the host's network is not built.
    assert "network.host is not allowed" in second, second
    assert "la construction de l'image a échoué" in second, second
    assert f"code4:{NOT_RUN}" in tail, second
    # 5. Nothing piles up: the project's last image, and it alone.
    assert remaining == [new], remaining
    # 6. The agent's cache mounts are its project's: a build of the person's does not see them.
    assert marker not in unnamespaced, unnamespaced
    assert marker in namespaced, namespaced
