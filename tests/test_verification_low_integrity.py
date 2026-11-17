"""The verification of a tooling has no effect outside its copy (#1691).

Maestro plays the commands a tooling writes before writing them (#1160), in a copy of
the project. A script those commands run says nothing in their text, and the scope
(`maestro.portee`) reads only the text: the `setup.sh` of Maestro itself writes a
function into the person's PowerShell profile. What bounds such a write is the
machine. The verification plays under the bound of the machine's agents
(`maestro.sandbox.verification.borne_du_poste`): at low integrity where the agents
run so (#1515), and not at all where the agents are bounded and the machine cannot
bound the verification.

Two halves. The first runs everywhere, with a doubled player: which bound each
setting gives, what is played under it, what is not played and why. The second runs
only on Windows, where the mechanism exists, and plays real commands: a script that
writes in a profile outside the copy, and a command that writes only in its copy
and its workbench.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from maestro.outillage.verification import (
    A_VERIFIER,
    ECHOUEE,
    VERIFIEE,
    CommandeEcrite,
    Verificateur,
)
from maestro.projets.modele import Perimetre
from maestro.sandbox import low_integrity, low_session
from maestro.sandbox import verification as execution

WINDOWS = sys.platform == "win32"

#: The real interpreter, read **before** the conftest guard removes it.
BASH = execution.interprete()


def _fingerprint(path: Path) -> tuple[str, int, int]:
    """What says a file has not moved: its content, its size, its modification date."""
    status = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), status.st_size, status.st_mtime_ns


def _project(tmp_path: Path, scripts: dict[str, str]) -> Path:
    """A project carrying `scripts` under `scripts/`, with LF endings bash reads as written.

    `.gitattributes` keeps them LF in a fresh clone too, whatever `core.autocrlf` says.
    """
    root = tmp_path / "projets" / "depensio"
    (root / "scripts").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (root / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    for name, text in scripts.items():
        (root / "scripts" / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def _version(root: Path) -> None:
    """Makes `root` a repository whose `main` branch carries the whole tree."""
    git = ["git", "-C", str(root), "-c", "user.email=t@m", "-c", "user.name=T"]
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run([*git, "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "socle"], check=True)


#: An interpreter for the doubled player: it is never launched.
FAKE_BASH = ("bash", "-c")

#: The two commands of the doubled half: one the project plays, one whose file is not
#: written yet, whose program is only probed (#1343).
COMMANDS = (
    CommandeEcrite("tester", "bash scripts/tests.sh", "scripts/tests.sh"),
    CommandeEcrite("lint", "ruff check .", "ruff.toml"),
)


class _Player:
    """A doubled player: every command succeeds, and what it received is kept."""

    def __init__(self) -> None:
        self.played: list[tuple[str, Path, dict[str, Any]]] = []

    def __call__(self, command: str, cwd: Path, **options: Any) -> execution.Execution:
        self.played.append((command, Path(cwd), options))
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)


def _doubled_project(tmp_path: Path) -> Path:
    return _project(tmp_path, {"tests.sh": "echo ok\n"})


# --- Which bound the machine gives ----------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "lowers", "bound"),
    [
        ("", True, execution.BORNE_INTEGRITE_BASSE),
        ("", False, execution.BORNE_HOTE),
        ("integrite-basse", True, execution.BORNE_INTEGRITE_BASSE),
        ("hote", True, execution.BORNE_HOTE),
        ("hote", False, execution.BORNE_HOTE),
        ("conteneur", True, execution.BORNE_INTEGRITE_BASSE),
    ],
)
def test_the_verification_plays_under_the_bound_of_the_machine_s_agents(
    monkeypatch: pytest.MonkeyPatch, setting: str, lowers: bool, bound: str
) -> None:
    monkeypatch.setenv("MAESTRO_ISOLATION", setting)
    monkeypatch.setattr(low_integrity, "available", lambda: lowers)
    assert execution.borne_du_poste() == bound


@pytest.mark.parametrize(
    ("setting", "said"),
    [
        ("conteneur", "tournent dans un conteneur"),
        ("integrite-basse", "n'existe que sous Windows"),
        ("bas", "inconnu"),
    ],
)
def test_a_bound_the_machine_cannot_hold_is_named(
    monkeypatch: pytest.MonkeyPatch, setting: str, said: str
) -> None:
    """Agents in a container on a machine without low integrity: nothing bounds the host."""
    monkeypatch.setenv("MAESTRO_ISOLATION", setting)
    monkeypatch.setattr(low_integrity, "available", lambda: False)
    with pytest.raises(execution.BorneIndisponible, match=said):
        execution.borne_du_poste()


# --- What is played under it ----------------------------------------------------------------


def test_under_low_integrity_everything_played_in_the_copy_is_lowered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command and the probe of a program both run lowered, in the copy that was lowered."""
    lowered: list[Path] = []
    left: list[Path] = []
    workbench = tmp_path / "etabli"
    workbench.mkdir()
    lowering = execution.Abaissement(("low-bash", "-c"), low_session.LowSession(workbench))

    @contextmanager
    def lower(copy: Path) -> Iterator[execution.Abaissement]:
        lowered.append(Path(copy))
        yield lowering
        left.append(Path(copy))

    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_INTEGRITE_BASSE)
    monkeypatch.setattr(execution, "abaissement", lower)
    player = _Player()

    verdicts = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_commandes(
        _doubled_project(tmp_path), COMMANDS, perimetre=Perimetre()
    )

    assert [v.etat for v in verdicts] == [VERIFIEE, A_VERIFIER]
    assert [command for command, _, _ in player.played] == ["bash scripts/tests.sh", "type -- ruff"]
    for _, cwd, options in player.played:
        assert options["interprete"] == ("low-bash", "-c")
        assert options["abaissement"] is lowering
        assert cwd == lowered[0]
    assert lowered == left, "the lowering is prepared once, and leaves with the copy"


def test_where_the_agents_run_at_the_person_s_level_the_verification_does_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_HOTE)

    def refuse(copy: Path) -> Iterator[execution.Abaissement]:
        raise AssertionError("nothing is lowered where the agents are not")

    monkeypatch.setattr(execution, "abaissement", refuse)
    player = _Player()

    Verificateur(joueur=player, interprete=FAKE_BASH).verifier_commandes(
        _doubled_project(tmp_path), COMMANDS, perimetre=Perimetre()
    )

    assert player.played and all("abaissement" not in opts for _, _, opts in player.played)
    assert all(opts["interprete"] == FAKE_BASH for _, _, opts in player.played)


# --- What is not played, and why ------------------------------------------------------------


def test_a_bound_the_machine_cannot_hold_plays_nothing_and_copies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unbounded() -> str:
        raise execution.BorneIndisponible("les agents de ce poste tournent dans un conteneur")

    def no_copy(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no copy is made for commands that will not be played")

    monkeypatch.setattr(execution, "borne_du_poste", unbounded)
    monkeypatch.setattr(execution, "copie_de_verification", no_copy)
    player = _Player()

    tests, lint = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_commandes(
        _doubled_project(tmp_path), COMMANDS, perimetre=Perimetre()
    )

    assert player.played == []
    assert tests.etat == A_VERIFIER
    assert tests.raison == (
        "pas jouée — le poste ne borne pas ce qu'une commande écrirait hors de la copie de "
        "vérification : les agents de ce poste tournent dans un conteneur"
    )
    # The project's reason still comes first: it changes by itself once the file exists.
    assert lint.etat == A_VERIFIER and "`ruff.toml` n'existe pas encore" in lint.raison


@pytest.mark.parametrize(
    ("refusal", "said"),
    [
        (
            low_integrity.LowIntegrityError(5, r"the label of C:\x\clone could not be set"),
            "le système a refusé de préparer l'intégrité basse (erreur 5)",
        ),
        (None, "Git for Windows est introuvable sur ce poste"),
    ],
    ids=["label-refused", "no-git-copy"],
)
def test_a_lowering_the_system_refuses_plays_nothing_and_says_why_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: OSError | None, said: str
) -> None:
    """Never played at the person's level in its stead; the reason names no temporary path."""
    workbench = tmp_path / "etabli"

    def prepare(workspace: Path, **kwargs: Any) -> low_session.LowSession:
        if refusal is not None:
            raise refusal
        workbench.mkdir()
        return low_session.LowSession(workbench, bash=None)

    monkeypatch.setattr(execution, "borne_du_poste", lambda: execution.BORNE_INTEGRITE_BASSE)
    monkeypatch.setattr(low_session, "prepare", prepare)
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    player = _Player()

    tests, _ = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_commandes(
        _doubled_project(tmp_path), COMMANDS, perimetre=Perimetre()
    )

    assert player.played == []
    assert tests.etat == A_VERIFIER and said in tests.raison, tests.raison
    assert tests.raison.startswith("pas jouée — le poste ne borne pas")
    assert "\\" not in tests.raison and "clone" not in tests.raison
    assert not workbench.exists(), "a workbench that was opened leaves with the verification"


# --- Windows: the bound, for real -----------------------------------------------------------


@pytest.fixture(scope="module")
def git_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where Git is copied for low integrity: a folder of the module, never `~/.maestro`."""
    return tmp_path_factory.mktemp("outils")


@pytest.mark.skipif(not WINDOWS, reason="the low integrity level exists only on Windows")
@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
@pytest.mark.skipif(low_session.git_installation() is None, reason="Git for Windows is needed")
@pytest.mark.integrite_basse
@pytest.mark.commandes_jouees
@pytest.mark.parametrize("versioned", [False, True], ids=["copy", "fresh-clone"])
def test_a_verified_command_writes_nothing_outside_its_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_tools: Path, versioned: bool
) -> None:
    """A script the scope does not read writes in a profile: the machine refuses it.

    The installation appends to a profile outside the copy, as the `shell` step of
    Maestro's `setup.sh` does. The tests write only in their copy and their temporary
    folder. Both are played. The installation fails on the refused write, and the
    profile is the same byte for byte, at the same date: nothing was restored.
    """
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: git_tools)
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    profile = tmp_path / "Documents" / "WindowsPowerShell" / "profile.ps1"
    profile.parent.mkdir(parents=True)
    profile.write_text("# the person's profile\n", encoding="utf-8")
    before = _fingerprint(profile)
    root = _project(
        tmp_path,
        {
            "installer.sh": (
                "echo installed > installed.txt\n"
                f"echo 'function bash {{ }}' >> '{profile.as_posix()}'\n"
            ),
            "tests.sh": (
                "test -f installed.txt\n"
                'scratch="$(mktemp)" && echo draft > "$scratch" && rm -f "$scratch"\n'
                "echo ok > in-the-copy.txt\n"
            ),
        },
    )
    if versioned:
        _version(root)
    commands = (
        CommandeEcrite("installer", "bash scripts/installer.sh", "scripts/installer.sh"),
        CommandeEcrite("tester", "bash scripts/tests.sh", "scripts/tests.sh"),
    )

    installer, tests = Verificateur().verifier_commandes(root, commands, perimetre=Perimetre())

    assert _fingerprint(profile) == before, profile.read_text(encoding="utf-8")
    assert installer.etat == ECHOUEE, installer
    assert tests.etat == VERIFIEE, tests
    assert not (root / "installed.txt").exists()
    assert not (root / "in-the-copy.txt").exists()


@pytest.mark.skipif(not WINDOWS, reason="the low integrity level exists only on Windows")
@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
@pytest.mark.skipif(low_session.git_installation() is None, reason="Git for Windows is needed")
@pytest.mark.integrite_basse
@pytest.mark.commandes_jouees
def test_a_lowered_command_receives_its_workbench_and_none_of_maestro_s_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_tools: Path
) -> None:
    """What Maestro closes to reading would be open again if its values came by the environment."""
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: git_tools)
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    monkeypatch.setattr(low_session, "maestro_variables", lambda: frozenset({"WITNESS_1691"}))
    monkeypatch.setenv("MAESTRO_WITNESS_1691", "seen")
    monkeypatch.setenv("WITNESS_1691", "seen")
    monkeypatch.setenv("INHERITED_1691", "seen")
    command = CommandeEcrite(
        "tester",
        'printf "%s|%s|%s|%s" "${MAESTRO_WITNESS_1691-none}" "${WITNESS_1691-none}" '
        '"${INHERITED_1691-none}" "$LOCALAPPDATA"',
    )

    (verdict,) = Verificateur().verifier_commandes(
        _project(tmp_path, {}), (command,), perimetre=Perimetre()
    )

    assert verdict.etat == VERIFIEE, verdict
    maestro, withheld, inherited, local = verdict.sortie.split("|")
    assert (maestro, withheld, inherited) == ("none", "none", "seen")
    assert low_session.WORKBENCH_PREFIX in local, local
    assert not Path(local).exists(), "the workbench leaves with the verification"
