"""An agent at low integrity has a command played in a container Maestro composes (#1725).

Two halves, as in `test_low_integrity.py`. The first runs everywhere: the real broker,
on its real socket, with the real program, and docker replaced by a double that records
the `docker run` it was given. It proves what is composed, what is relayed, and what is
refused, with its reason. The second runs on Windows, with Docker's engine answering at
the person's level: a process born at low integrity runs `maestro-container` from its
Git Bash, as an agent does, and the container it obtains is looked at from inside.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from maestro.machine import LIGNE_COURTIER, LIGNE_INTEGRITE_BASSE, Machine
from maestro.projects.model import Perimeter, Project, Vcs
from maestro.providers.base import Credentials
from maestro.providers.claude import ClaudeProvider
from maestro.sandbox import container, container_broker, low_integrity, low_session
from maestro.sandbox.arbre import Arbre
from maestro.sandbox.container_broker import ContainerBroker
from maestro.sandbox.container_broker_client import (
    ENV_ADDRESS,
    ENV_TOKEN,
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

    def spawn(self, argv: Sequence[str]) -> subprocess.Popen[bytes]:
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


def _versioned_project(tmp_path: Path) -> tuple[Path, Project]:
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
        id="prj-depensio",
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


def test_a_low_session_receives_the_broker_and_ends_it_with_itself(
    broker, tmp_path, monkeypatch
):
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
