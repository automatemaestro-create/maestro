"""The verification of a task plays under the bound of the machine, and keeps the container (#1728).

The text of a task's controls comes from the verifier; what they execute comes from the
agent: its tests, its `conftest.py`, its scripts. The scope reads only the text. The
controls therefore play under the bound of the machine's agents
(`maestro.sandbox.verification.borne_du_poste`), the one of the tooling's verification
(#1691): at low integrity where the agents run so. What asks for a container goes
through Maestro's broker, opened on the place where the controls play (docs/57 §5).

Two halves, as for the tooling. The first runs everywhere, with doubles: what is
lowered, once, on which place, what the broker gives, what is not played and why. The
second runs only on Windows, where the mechanism exists, and plays real commands: a
script that writes in a profile outside the workspace, a service and its probe, a
fresh clone.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from maestro.engine.verification import (
    CONSTAT_NON_JOUE,
    CONSTAT_NON_TENU,
    CONSTAT_TENU,
    Livraison,
    Verdict,
    VerificateurTaches,
)
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.sandbox import container_broker, low_integrity, low_session
from maestro.sandbox import verification as execution

WINDOWS = sys.platform == "win32"

#: The real interpreter, read **before** the conftest guard removes it.
BASH = execution.interprete()

#: An interpreter for the doubled player: it is never launched.
FAKE_BASH = ("bash", "-c")


class _Verifier(ModelProvider):
    """Answers as the verifier: the controls it was given, then nothing to revise."""

    name = "double-task-verification"

    def __init__(self, controls: list[dict[str, Any]]) -> None:
        self._controls = controls
        self.calls = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"controles": self._controls})
        return "{}"

    async def run_agent(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("the verifier never runs an agent")


class _Player:
    """A doubled player: every command succeeds, and what it received is kept."""

    def __init__(self) -> None:
        self.played: list[tuple[str, Path, dict[str, Any]]] = []

    def __call__(self, command: str, cwd: Path, **options: Any) -> execution.Execution:
        self.played.append((command, Path(cwd), options))
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)


def _task() -> Task:
    return Task(
        id="t1",
        title="Écrire les tests",
        description="Écrire les tests du module.",
        required_competencies=("dev",),
        output_format="code",
    )


def _verify(
    verifier: VerificateurTaches, delivery: Livraison
) -> Verdict:
    _, verdict = asyncio.run(verifier.verifier(_task(), delivery, modele="double"))
    return verdict


def _control(criterion: str, command: str, **more: Any) -> dict[str, Any]:
    return {"critere": criterion, "commande": command, **more}


class _Lowering:
    """A doubled `execution.abaissement`: what was lowered, with what, and when it left."""

    def __init__(self, tmp_path: Path) -> None:
        workbench = tmp_path / "etabli"
        workbench.mkdir()
        self.given = execution.Abaissement(("low-bash", "-c"), low_session.LowSession(workbench))
        self.entered: list[tuple[Path, Path | None, Any]] = []
        self.left: list[Path] = []

    @contextmanager
    def __call__(
        self, place: Path, *, projet: Path | None = None, courtier: Any = None
    ) -> Iterator[execution.Abaissement]:
        self.entered.append((Path(place), projet, courtier))
        yield self.given
        self.left.append(Path(place))


@pytest.fixture
def lowering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Lowering:
    double = _Lowering(tmp_path)
    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_INTEGRITE_BASSE)
    monkeypatch.setattr(execution, "abaissement", double)
    return double


# --- What is played under the bound ---------------------------------------------------------


def test_under_low_integrity_every_control_of_a_task_plays_lowered_in_its_workspace(
    tmp_path: Path, lowering: _Lowering
) -> None:
    """The lowering is prepared once for the verification, on the workspace, and leaves with it."""
    workspace = tmp_path / "espace"
    workspace.mkdir()
    player = _Player()
    provider = _Verifier(
        [_control("the tests pass", "bash tests.sh"), _control("the style holds", "ruff check .")]
    )

    verdict = _verify(
        VerificateurTaches(provider, joueur=player, interprete=FAKE_BASH),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue, verdict.preuves()
    assert [command for command, _, _ in player.played] == ["bash tests.sh", "ruff check ."]
    for _, cwd, options in player.played:
        assert cwd == workspace
        assert options["interprete"] == ("low-bash", "-c")
        assert options["abaissement"] is lowering.given
    assert [(place, project) for place, project, _ in lowering.entered] == [(workspace, workspace)]
    assert lowering.left == [workspace]


def test_the_cache_of_a_mirrored_root_is_the_root_s(tmp_path: Path, lowering: _Lowering) -> None:
    """A verification that plays in the mirror of a root keeps the caches of that root (#1709)."""
    mirror, root = tmp_path / "miroir", tmp_path / "racine"
    mirror.mkdir()
    root.mkdir()

    _verify(
        VerificateurTaches(
            _Verifier([_control("c", "true")]), joueur=_Player(), interprete=FAKE_BASH
        ),
        Livraison(sortie="done", espace=mirror, origine=root),
    )

    assert [(place, project) for place, project, _ in lowering.entered] == [(mirror, root)]


def test_a_started_service_and_its_probe_receive_the_lowering(
    tmp_path: Path, lowering: _Lowering
) -> None:
    received: dict[str, Any] = {}

    def starter(command: str, cwd: Path, **options: Any) -> execution.Demarrage:
        received.update(options)
        probe = execution.Execution(code=0, sortie="200", duree_s=0.1)
        return execution.Demarrage(repondu=True, code=None, sortie="", sonde=probe, duree_s=0.2)

    workspace = tmp_path / "espace"
    workspace.mkdir()
    provider = _Verifier(
        [_control("the server answers", "npm start", demarrage=True, sonde="curl -s localhost")]
    )

    verdict = _verify(
        VerificateurTaches(provider, demarreur=starter, interprete=FAKE_BASH),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue, verdict.preuves()
    assert received["abaissement"] is lowering.given
    assert received["interprete"] == ("low-bash", "-c")


def test_a_fresh_clone_is_a_place_of_its_own_and_receives_its_own_lowering(
    tmp_path: Path, lowering: _Lowering, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, clone = tmp_path / "espace", tmp_path / "clone"
    workspace.mkdir()
    clone.mkdir()

    @contextmanager
    def fresh_clone(space: Path) -> Iterator[Path]:
        assert space == workspace
        yield clone

    monkeypatch.setattr(execution, "clone_neuf", fresh_clone)
    player = _Player()
    provider = _Verifier([_control("it builds from a clone", "npm ci", clone_neuf=True)])

    verdict = _verify(
        VerificateurTaches(provider, joueur=player, interprete=FAKE_BASH),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue, verdict.preuves()
    assert [(place, project) for place, project, _ in lowering.entered] == [(clone, workspace)]
    (_, cwd, options), = player.played
    assert cwd == clone and options["abaissement"] is lowering.given


def test_a_verification_that_plays_nothing_lowers_nothing(
    tmp_path: Path, lowering: _Lowering
) -> None:
    workspace = tmp_path / "espace"
    workspace.mkdir()
    provider = _Verifier(
        [{"critere": "the report names the risk", "lecture": "x", "tenu": True, "preuve": "y"}]
    )

    verdict = _verify(
        VerificateurTaches(provider, joueur=_Player(), interprete=FAKE_BASH),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue
    assert lowering.entered == []


def test_where_the_agents_run_at_the_person_s_level_the_controls_do_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_HOTE)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing is lowered where the agents are not")

    monkeypatch.setattr(execution, "abaissement", refuse)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    player = _Player()

    verdict = _verify(
        VerificateurTaches(
            _Verifier([_control("c", "true")]), joueur=player, interprete=FAKE_BASH
        ),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue
    (_, _, options), = player.played
    assert "abaissement" not in options and options["interprete"] == FAKE_BASH


# --- What is not played, and why ------------------------------------------------------------


def test_a_bound_the_machine_cannot_hold_plays_no_control_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never played at the person's level in its stead: neither held nor refuted."""
    asked: list[int] = []

    def unbounded() -> str:
        asked.append(1)
        raise execution.BorneIndisponible("les agents de ce poste tournent dans un conteneur")

    monkeypatch.setattr(execution, "borne_du_poste", unbounded)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    player = _Player()
    provider = _Verifier([_control("a", "bash a.sh"), _control("b", "bash b.sh")])

    verdict = _verify(
        VerificateurTaches(provider, joueur=player, interprete=FAKE_BASH),
        Livraison(sortie="done", espace=workspace),
    )

    assert player.played == []
    assert [c.etat for c in verdict.constats] == [CONSTAT_NON_JOUE, CONSTAT_NON_JOUE]
    for finding in verdict.constats:
        assert finding.preuve == (
            "pas jouée — le poste ne borne pas ce qu'une commande écrirait hors de l'espace de "
            "la vérification : les agents de ce poste tournent dans un conteneur"
        )
    assert len(asked) == 1, "the refusal is kept: the machine is asked once per verification"
    assert provider.calls == 1, "nothing was refuted, so nothing is cross-examined"


# --- The broker, on the place where the controls play ---------------------------------------


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[low_session.LowSession]:
    """A doubled `low_session.prepare`: a real workbench, nothing labelled, a bash that is named."""
    sessions: list[low_session.LowSession] = []

    def prepare(workspace: Path, **options: Any) -> low_session.LowSession:
        workbench = tmp_path / f"etabli-{len(sessions)}"
        workbench.mkdir()
        session = low_session.LowSession(
            workbench, {"PATH": "git-copy"}, bash=Path("low-bash")
        )
        sessions.append(session)
        return session

    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_INTEGRITE_BASSE)
    monkeypatch.setattr(low_session, "prepare", prepare)
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    monkeypatch.setattr(low_session, "maestro_variables", lambda: frozenset())
    return sessions


def _no_docker(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("no container is asked for in this test")


def test_a_lowered_control_receives_the_broker_opened_on_its_workspace(
    tmp_path: Path, prepared: list[low_session.LowSession]
) -> None:
    """Address, token and program, as an agent's session; closed with the verification."""
    broker = container_broker.ContainerBroker(
        spawn=_no_docker, remove=_no_docker, docker=_no_docker
    )
    workspace = tmp_path / "espace"
    workspace.mkdir()
    seen: dict[str, Any] = {}

    def player(command: str, cwd: Path, **options: Any) -> execution.Execution:
        session = options["abaissement"].session
        seen["env"] = dict(session.env)
        seen["live"] = len(broker._sessions)
        (session_served,) = broker._sessions.values()
        seen["workspace"] = session_served.workspace
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)

    try:
        verdict = _verify(
            VerificateurTaches(
                _Verifier([_control("the net is green", "bash scripts/ci/local.sh")]),
                joueur=player,
                interprete=FAKE_BASH,
                courtier=broker,
            ),
            Livraison(sortie="done", espace=workspace),
        )
        assert verdict.tenue, verdict.preuves()
        host, port = broker.address or ("", 0)
        assert seen["env"][container_broker.ENV_ADDRESS] == f"{host}:{port}"
        assert seen["env"][container_broker.ENV_TOKEN]
        launchers, inherited = seen["env"]["PATH"].split(os.pathsep, 1)
        assert (Path(launchers) / container_broker.PROGRAM).is_file()
        assert inherited == "git-copy"
        assert seen["workspace"] == workspace and seen["live"] == 1
        assert broker._sessions == {}, "the broker's session ends with the verification"
        assert not prepared[0].workbench.exists()
    finally:
        broker.shutdown()


def test_a_broker_that_does_not_open_leaves_the_control_lowered_without_it(
    tmp_path: Path, prepared: list[low_session.LowSession]
) -> None:
    """The bound is the safety property; the container is a regime, and its absence is said."""

    class _Closed:
        def open(self, workspace: Path, project: Any = None) -> Any:
            raise OSError("no loopback")

    workspace = tmp_path / "espace"
    workspace.mkdir()
    player = _Player()

    verdict = _verify(
        VerificateurTaches(
            _Verifier([_control("c", "true")]),
            joueur=player,
            interprete=FAKE_BASH,
            courtier=_Closed(),  # type: ignore[arg-type]
        ),
        Livraison(sortie="done", espace=workspace),
    )

    assert verdict.tenue
    (_, _, options), = player.played
    session = options["abaissement"].session
    assert container_broker.ENV_TOKEN not in session.env and session.env["PATH"] == "git-copy"


# --- Windows: the bound, for real -----------------------------------------------------------


def _fingerprint(path: Path) -> tuple[str, int, int]:
    """What says a file has not moved: its content, its size, its modification date."""
    status = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), status.st_size, status.st_mtime_ns


def _script(workspace: Path, name: str, text: str) -> None:
    (workspace / "scripts").mkdir(exist_ok=True)
    (workspace / "scripts" / name).write_text(text, encoding="utf-8", newline="\n")


def _version(root: Path) -> None:
    """Makes `root` a repository whose `main` branch carries the whole tree."""
    (root / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    git = ["git", "-C", str(root), "-c", "user.email=t@m", "-c", "user.name=T"]
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run([*git, "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "socle"], check=True)


@pytest.fixture(scope="module")
def git_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where Git is copied for low integrity: a folder of the module, never `~/.maestro`."""
    return tmp_path_factory.mktemp("outils")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_tools: Path) -> Path:
    """The real mechanism, with nothing of this machine's Maestro in it; the person's profile."""
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: git_tools)
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    monkeypatch.setattr(container_broker, "available", lambda: False)
    profile = tmp_path / "Documents" / "WindowsPowerShell" / "profile.ps1"
    profile.parent.mkdir(parents=True)
    profile.write_text("# the person's profile\n", encoding="utf-8")
    return profile


def _real(function: Any) -> Any:
    """The marks of a test that plays real commands at low integrity."""
    for mark in (
        pytest.mark.commandes_jouees,
        pytest.mark.integrite_basse,
        pytest.mark.skipif(
            low_session.git_installation() is None, reason="Git for Windows is needed"
        ),
        pytest.mark.skipif(BASH is None, reason="no bash on this machine"),
        pytest.mark.skipif(not WINDOWS, reason="the low integrity level exists only on Windows"),
    ):
        function = mark(function)
    return function


@_real
def test_a_control_that_writes_outside_its_workspace_is_refused_by_the_machine(
    tmp_path: Path, machine: Path
) -> None:
    """The agent's script, run by a control whose text says nothing of it, writes in a profile.

    The vector of #1502, above the confinement until #1728: the control is `bash
    scripts/check.sh`, and the script is the agent's. It fails on the refused write, and
    the profile is the same byte for byte, at the same date: nothing was restored. A
    control that writes only in the workspace and in its workbench holds.
    """
    before = _fingerprint(machine)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    _script(
        workspace,
        "check.sh",
        f"echo checked > checked.txt\necho 'function bash {{ }}' >> '{machine.as_posix()}'\n",
    )
    _script(
        workspace,
        "tests.sh",
        'scratch="$(mktemp)" && echo draft > "$scratch" && rm -f "$scratch"\n'
        "echo ok > in-the-workspace.txt\n",
    )
    provider = _Verifier(
        [
            _control("the check passes", "bash scripts/check.sh"),
            _control("the tests pass", "bash scripts/tests.sh"),
        ]
    )

    verdict = _verify(VerificateurTaches(provider), Livraison(sortie="done", espace=workspace))

    assert _fingerprint(machine) == before, machine.read_text(encoding="utf-8")
    check, tests = verdict.constats
    assert check.etat == CONSTAT_NON_TENU and check.code not in (None, 0), check
    assert tests.etat == CONSTAT_TENU, tests
    assert (workspace / "in-the-workspace.txt").is_file()
    assert (workspace / "checked.txt").is_file(), "the script did run, up to the refused write"


@_real
def test_a_started_service_and_its_probe_write_nothing_outside_the_workspace(
    tmp_path: Path, machine: Path
) -> None:
    """A control that starts a service passes under the same bound, its probe too."""
    before = _fingerprint(machine)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    outside = machine.as_posix()
    _script(
        workspace,
        "serve.sh",
        f"echo 'service' >> '{outside}' 2>/dev/null\necho up > up.txt\nsleep 60\n",
    )
    _script(
        workspace,
        "probe.sh",
        f"echo 'probe' >> '{outside}' 2>/dev/null\ntest -f up.txt\n",
    )
    provider = _Verifier(
        [
            _control(
                "the service answers",
                "bash scripts/serve.sh",
                demarrage=True,
                sonde="bash scripts/probe.sh",
            )
        ]
    )

    verdict = _verify(VerificateurTaches(provider), Livraison(sortie="done", espace=workspace))

    assert _fingerprint(machine) == before, machine.read_text(encoding="utf-8")
    assert verdict.tenue, verdict.preuves()
    assert (workspace / "up.txt").is_file()


@_real
def test_a_control_on_a_fresh_clone_writes_nothing_outside_the_clone(
    tmp_path: Path, machine: Path
) -> None:
    before = _fingerprint(machine)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    _script(
        workspace,
        "build.sh",
        f"echo built > built.txt\necho 'function bash {{ }}' >> '{machine.as_posix()}'\n",
    )
    _version(workspace)
    provider = _Verifier(
        [_control("it builds from a fresh clone", "bash scripts/build.sh", clone_neuf=True)]
    )

    verdict = _verify(VerificateurTaches(provider), Livraison(sortie="done", espace=workspace))

    assert _fingerprint(machine) == before, machine.read_text(encoding="utf-8")
    (build,) = verdict.constats
    assert build.etat == CONSTAT_NON_TENU and build.code not in (None, 0), build
    assert not (workspace / "built.txt").exists(), "played in the clone, never in the workspace"


def test_the_machine_is_asked_for_its_bound_with_the_agents_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sous_la_borne` is `borne_du_poste`, never a second reading of the setting."""
    monkeypatch.setenv("MAESTRO_ISOLATION", "hote")
    monkeypatch.setattr(low_integrity, "available", lambda: True)
    with execution.sous_la_borne(Path("anywhere")) as lowered:
        assert lowered is None
