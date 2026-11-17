"""An agent can neither stop a process outside its tree nor write outside its project (#1515).

Two halves. The first runs everywhere: the setting and its default, what the provider,
the launcher and the machine section say, the copy of Git. The second runs only on
Windows, where the mechanism exists, and exercises it for real: a process born at low
integrity, the command of #1502 and a script that holds it, a write outside the
project and the workbench.

**Nothing here stops a process of the person.** The command of #1502 is played only
after the same test has proved, on that very child, that it runs at low integrity and
that a witness named after nothing else on the machine survives a stop by its name. A
low process does not even see the person's processes; the only ones it could stop are
other low processes, and the suite starts none outside these tests.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from maestro.config import ConfigError, load_settings
from maestro.machine import LIGNE_INTEGRITE_BASSE, Machine
from maestro.providers.base import Credentials
from maestro.providers.claude import ClaudeProvider
from maestro.sandbox import confinement, low_integrity, low_session
from maestro.sandbox.arbre import Arbre
from maestro.sandbox.container import (
    ENV_TRANSMISES,
    MODE_CONTENEUR,
    MODE_HOTE,
    MODE_INTEGRITE_BASSE,
    IsolationConfig,
    mode_isolation,
)

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"
on_windows = pytest.mark.skipif(
    not WINDOWS, reason="the low integrity level exists only on Windows"
)


def _settings(isolation: str | None):
    return dataclasses.replace(load_settings(), isolation=isolation)


# --- The setting and its default ------------------------------------------------------------


def test_without_any_setting_an_agent_runs_at_low_integrity_where_windows_has_it(monkeypatch):
    monkeypatch.setattr(low_integrity, "available", lambda: True)
    assert mode_isolation(_settings(None)) == MODE_INTEGRITE_BASSE
    monkeypatch.setattr(low_integrity, "available", lambda: False)
    assert mode_isolation(_settings(None)) == MODE_HOTE


def test_each_mode_is_read_as_written_and_an_unknown_one_is_refused(monkeypatch):
    monkeypatch.setattr(low_integrity, "available", lambda: True)
    for mode in (MODE_INTEGRITE_BASSE, MODE_HOTE, MODE_CONTENEUR):
        assert mode_isolation(_settings(mode)) == mode
    with pytest.raises(ConfigError, match="inconnu"):
        mode_isolation(_settings("bas"))


def test_low_integrity_asked_where_it_does_not_exist_is_refused_never_replaced(monkeypatch):
    monkeypatch.setattr(low_integrity, "available", lambda: False)
    with pytest.raises(ConfigError, match="n'existe que sous Windows"):
        mode_isolation(_settings(MODE_INTEGRITE_BASSE))


def test_the_container_settings_answer_only_for_the_container(monkeypatch):
    monkeypatch.setattr(low_integrity, "available", lambda: True)
    assert IsolationConfig.from_settings(_settings(None)) is None
    assert IsolationConfig.from_settings(_settings(MODE_HOTE)) is None


# --- The provider ---------------------------------------------------------------------------


def test_every_session_runs_the_cli_in_maestro_s_own_folder(tmp_path):
    provider = ClaudeProvider(Credentials(), dossier_cli=tmp_path / "cli")
    assert provider._auth_env()["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cli")
    # The container receives nothing of it: its CLI keeps its own folder.
    assert "CLAUDE_CONFIG_DIR" not in ENV_TRANSMISES


def test_the_provider_built_from_settings_uses_the_machine_s_cli_folder(monkeypatch, tmp_path):
    from maestro.emplacements import dossier_du_cli_claude

    monkeypatch.setattr(low_integrity, "available", lambda: True)
    provider = ClaudeProvider.from_settings(_settings(None))
    assert provider._auth_env()["CLAUDE_CONFIG_DIR"] == str(dossier_du_cli_claude())
    assert provider.integrite_basse is True
    assert ClaudeProvider.from_settings(_settings(MODE_HOTE)).integrite_basse is False


def test_the_subscription_access_names_the_command_that_connects_maestro(monkeypatch, tmp_path):
    from maestro.providers import claude_login

    interpreter = tmp_path / "Program Files" / "Maestro" / "python" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # A clone whose package was not reinstalled: the module, through this interpreter.
    assert claude_login.command() == f'"{interpreter}" -m maestro.providers.claude_login'
    # An installation: the command beside its interpreter, off the PATH, quoted.
    name = claude_login.COMMAND + (".exe" if WINDOWS else "")
    (interpreter.parent / name).write_bytes(b"")
    assert claude_login.command() == f'"{interpreter.parent / name}"'
    subscription = next(a for a in ClaudeProvider.ACCES if a.id == "abonnement")
    assert subscription.to_dict()["connexion"] == claude_login.command()
    others = [a for a in ClaudeProvider.ACCES if a.id != "abonnement"]
    assert all(a.to_dict()["connexion"] is None for a in others)


def test_the_provider_lowers_only_on_the_host():
    conteneur = IsolationConfig(image="i", reseau="bridge", shim=Path("shim"))
    provider = ClaudeProvider(Credentials(), isolation=conteneur, integrite_basse=True)
    assert provider.integrite_basse is False
    assert provider._session_basse(Path(".")) is None


# --- The launcher ---------------------------------------------------------------------------


def test_the_launcher_refuses_an_integrity_it_does_not_know(monkeypatch, capsys):
    monkeypatch.setenv(confinement.ENV_COMMANDE, '["true"]')
    monkeypatch.setenv(confinement.ENV_INTEGRITE, "moyenne")
    assert confinement.main([]) == 2
    assert "inconnue" in capsys.readouterr().err


def test_a_low_session_reaches_the_launcher_and_leaves_with_the_session(monkeypatch, tmp_path):
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path(sys.executable))
    workbench = tmp_path / "etabli"
    workbench.mkdir()
    basse = low_session.LowSession(workbench, {"TEMP": str(workbench / "tmp")})
    session = confinement.SessionConfinee.preparer(["claude"], basse=basse)
    assert session.integrite_basse
    assert session.env[confinement.ENV_INTEGRITE] == confinement.INTEGRITE_BASSE
    assert session.env["TEMP"] == str(workbench / "tmp")
    session.solder()
    assert not workbench.exists()


def test_the_names_maestro_s_env_files_declare_reach_the_launcher_to_be_withheld(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path(sys.executable))
    workbench = tmp_path / "etabli"
    workbench.mkdir()
    basse = low_session.LowSession(
        workbench, withheld=frozenset({"SLACK_BOT_TOKEN", "GITHUB_TOKEN"})
    )
    session = confinement.SessionConfinee.preparer(["claude"], basse=basse)
    assert session.env[confinement.ENV_RETENUES] == "GITHUB_TOKEN,SLACK_BOT_TOKEN"
    session.solder()


def test_maestro_s_variables_are_the_names_its_env_files_declare(tmp_path):
    product = tmp_path / ".env"
    product.write_text(
        "# Forge\nGITHUB_TOKEN=ghp_x\nLANGFUSE_SECRET_KEY=sk\nMAESTRO_API_PORT=8000\n",
        encoding="utf-8",
    )
    names = low_session.maestro_variables([product, tmp_path / "absent.env"])
    assert names == {"GITHUB_TOKEN", "LANGFUSE_SECRET_KEY", "MAESTRO_API_PORT"}


def test_a_lowered_process_inherits_neither_maestro_s_namespace_nor_the_withheld_names():
    """One filter for the launcher and the verification (#1691), case ignored as on Windows."""
    environ = {
        "MAESTRO_API_TOKEN": "t",
        "maestro_settings": "s",
        "GitHub_Token": "g",
        "PATH": "p",
        "ANTHROPIC_API_KEY": "k",
    }
    kept = low_session.withhold(environ, {"GITHUB_TOKEN"})
    assert kept == {"PATH": "p", "ANTHROPIC_API_KEY": "k"}


def test_the_provider_keeps_from_a_low_cli_what_is_maestro_s_and_not_the_model_s(
    monkeypatch, tmp_path
):
    declared = {"GITHUB_TOKEN", "REDIS_URL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    monkeypatch.setattr(low_session, "maestro_variables", lambda: frozenset(declared))
    monkeypatch.setattr(low_session, "maestro_secrets", lambda: ())
    seen = {}

    def prepare(workspace, **kwargs):
        seen.update(kwargs)
        return low_session.LowSession(tmp_path)

    monkeypatch.setattr(low_session, "prepare", prepare)
    ClaudeProvider(Credentials(), integrite_basse=True)._session_basse(tmp_path)
    assert set(seen["withheld"]) == {"GITHUB_TOKEN", "REDIS_URL"}


def test_a_session_that_will_not_be_contained_drops_its_workbench_at_once(monkeypatch, tmp_path):
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: None)
    workbench = tmp_path / "etabli"
    workbench.mkdir()
    session = confinement.SessionConfinee.preparer(
        ["claude"], basse=low_session.LowSession(workbench)
    )
    assert session.lanceur is None
    assert not workbench.exists()


@pytest.mark.skipif(WINDOWS, reason="elsewhere than Windows, the request is refused")
def test_a_tree_refuses_low_integrity_where_it_does_not_exist():
    with pytest.raises(low_integrity.LowIntegrityError):
        Arbre.lancer(["true"], integrite_basse=True)


# --- What the agent is told -----------------------------------------------------------------


def test_the_machine_says_what_the_isolation_bounds_and_what_it_does_not():
    machine = Machine(systeme="Windows 11", temporaire=r"C:\Temp", windows=True)
    lowered = machine.consigne(integrite_basse=True)
    assert LIGNE_INTEGRITE_BASSE in lowered
    assert "ton établi" in lowered and r"C:\Temp" not in lowered
    for not_bounded in ("tu lis ce que la personne lit", "127.0.0.1", "un autre agent du poste"):
        assert not_bounded in LIGNE_INTEGRITE_BASSE
    plain = machine.consigne()
    assert LIGNE_INTEGRITE_BASSE not in plain and r"C:\Temp" in plain


# --- The copy of Git ------------------------------------------------------------------------


def _fake_git(root: Path) -> Path:
    for relative in ("bin/bash.exe", "usr/bin/msys-2.0.dll", "mingw64/bin/git.exe", "cmd/git.exe"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(b"x")
    return root


def test_git_is_copied_once_per_version_and_earlier_copies_leave(tmp_path):
    installation = _fake_git(tmp_path / "Git")
    tools = tmp_path / "outils"
    first = low_session.git_copy(installation, tools=tools)
    assert first is not None and (first / "bin" / "bash.exe").is_file()
    assert low_session.git_copy(installation, tools=tools) == first
    # A Git updated by the person: a new copy, and the earlier one goes.
    (installation / "usr/bin/msys-2.0.dll").write_bytes(b"version 2")
    second = low_session.git_copy(installation, tools=tools)
    assert second is not None and second != first
    assert not first.exists()
    assert [p.name for p in tools.iterdir()] == [second.name]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")
def test_a_worktree_names_the_git_folder_it_writes_into(tmp_path):
    repository = tmp_path / "depot"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "a.txt").write_text("a", encoding="utf-8")
    git = ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "a"], check=True)
    worktree = tmp_path / "arbre"
    subprocess.run([*git, "worktree", "add", "-q", "-b", "t", str(worktree)], check=True)
    (common,) = low_session.git_folders(worktree)
    assert common.resolve() == (repository / ".git").resolve()
    assert low_session.git_folders(repository) == ()
    assert low_session.git_folders(tmp_path) == ()


# --- Windows: the mechanism, for real -------------------------------------------------------

WHOAMI = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
TASKKILL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
PING = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)


def _play(argv: list[str], **kw) -> tuple[int, str]:
    """Runs `argv` at low integrity in its tree, and returns its code and its output."""
    tree = Arbre.lancer(argv, integrite_basse=True, **kw)
    try:
        assert tree.process.stdout is not None
        output = tree.process.stdout.read().decode("utf-8", "replace")
        code = tree.process.wait(timeout=120)
    finally:
        tree.arreter()
        tree.fermer()
    return code, output


def _assert_low() -> None:
    code, output = _play([str(WHOAMI), "/groups", "/fo", "csv"])
    assert code == 0 and low_integrity.LOW_INTEGRITY_SID in output, output


@pytest.fixture
def witness(tmp_path) -> Iterator[tuple[str, subprocess.Popen[bytes]]]:
    """A program of the person, at her level, under a name nothing else on the machine has."""
    name = f"temoin-{uuid.uuid4().hex[:12]}.exe"
    shutil.copy2(PING, tmp_path / name)
    process = subprocess.Popen(
        [str(tmp_path / name), "-n", "300", "127.0.0.1"], stdout=subprocess.DEVNULL
    )
    time.sleep(0.3)
    assert process.poll() is None
    yield name, process
    process.kill()
    process.wait()


@pytest.fixture(scope="module")
def git_tools(tmp_path_factory) -> Path:
    """The machine's tools folder, moved under the test's own folder, where Git is copied."""
    return tmp_path_factory.mktemp("outils")


@on_windows
@pytest.mark.integrite_basse
def test_a_tree_born_at_low_integrity_runs_at_low_integrity():
    _assert_low()


@on_windows
@pytest.mark.integrite_basse
def test_a_low_tree_cannot_stop_a_witness_by_its_name_nor_by_a_script(witness, tmp_path):
    name, process = witness
    _assert_low()
    code, output = _play([str(TASKKILL), "/IM", name, "/F"])
    assert code != 0, output
    assert process.poll() is None, "the witness was stopped by a low process"
    script = tmp_path / "nettoyer.cmd"
    script.write_text(f"@{TASKKILL} /IM {name} /F\r\n", encoding="ascii")
    code, output = _play([str(CMD), "/c", str(script)])
    assert code != 0, output
    assert process.poll() is None, "the witness was stopped by a script a low process ran"


@on_windows
@pytest.mark.integrite_basse
@pytest.mark.skipif(low_session.git_installation() is None, reason="Git for Windows is needed")
def test_the_command_of_1502_and_a_script_that_holds_it_touch_neither_maestro_nor_a_witness(
    witness, tmp_path, monkeypatch, git_tools
):
    name, process = witness
    # Two proofs on this very machine before anything named after python.exe is played.
    _assert_low()
    code, _ = _play([str(TASKKILL), "/IM", name, "/F"])
    assert code != 0 and process.poll() is None
    maestro_like = subprocess.Popen([BASE_PYTHON, "-c", "import time; time.sleep(300)"])
    try:
        monkeypatch.setattr(low_session, "outils_du_poste", lambda: git_tools)
        (tmp_path / "espace").mkdir()
        session = low_session.prepare(tmp_path / "espace", cli_folder=None)
        try:
            bash = str(session.bash)
            env = {**os.environ, **session.env}
            code, output = _play([bash, "-lc", "taskkill //IM python.exe //F"], env=env)
            assert code != 0, output
            script = tmp_path / "espace" / "nettoyer.sh"
            script.write_text("taskkill //IM python.exe //F\n", encoding="utf-8")
            code, output = _play(
                [bash, "-lc", "bash nettoyer.sh"], env=env, cwd=tmp_path / "espace"
            )
            assert code != 0, output
        finally:
            session.close()
        assert maestro_like.poll() is None, "a python.exe of the person was stopped"
        assert process.poll() is None
    finally:
        maestro_like.kill()
        maestro_like.wait()


@on_windows
@pytest.mark.integrite_basse
def test_a_write_outside_the_project_and_the_workbench_fails(tmp_path, monkeypatch, git_tools):
    monkeypatch.setattr(low_session, "outils_du_poste", lambda: git_tools)
    workspace = tmp_path / "espace"
    workspace.mkdir()
    secret = tmp_path / "jeton"
    secret.write_text("secret", encoding="utf-8")
    readable = tmp_path / "lisible.txt"
    readable.write_text("lu", encoding="utf-8")
    session = low_session.prepare(workspace, cli_folder=tmp_path / "cli", closed=[secret])
    workbench = session.workbench
    probe = (
        "import sys\n"
        "from pathlib import Path\n"
        "for target in sys.argv[1:5]:\n"
        "    try:\n"
        "        Path(target).write_text('x', encoding='utf-8'); print('WRITTEN', target)\n"
        "    except OSError:\n"
        "        print('REFUSED', target)\n"
        "for target in sys.argv[5:]:\n"
        "    try:\n"
        "        Path(target).read_text(encoding='utf-8'); print('READ', target)\n"
        "    except OSError:\n"
        "        print('UNREAD', target)\n"
    )
    targets = [
        workspace / "livrable.txt",
        workbench / "tmp" / "brouillon.txt",
        tmp_path / "cli" / "transcription.jsonl",
        tmp_path / "dehors.txt",
        secret,
        readable,
    ]
    try:
        code, output = _play(
            [BASE_PYTHON, "-c", probe, *map(str, targets)], env={**os.environ, **session.env}
        )
    finally:
        session.close()
    lines = set(output.split("\n"))
    assert code == 0, output
    for written in targets[:3]:
        assert f"WRITTEN {written}" in {line.strip() for line in lines}, output
    assert f"REFUSED {targets[3]}" in {line.strip() for line in lines}, output
    assert not (tmp_path / "dehors.txt").exists()
    assert f"UNREAD {secret}" in {line.strip() for line in lines}, output
    assert f"READ {readable}" in {line.strip() for line in lines}, output
    assert not workbench.exists()
    # The person, at her level, still reads what the agent may not.
    assert secret.read_text(encoding="utf-8") == "secret"


@on_windows
@pytest.mark.integrite_basse
def test_the_launcher_lowers_the_cli_and_withholds_maestro_s_names(monkeypatch):
    commande = [str(CMD), "/c", f"{WHOAMI} /groups /fo csv & set MAESTRO_ & set TEMOIN_"]
    env = {
        **os.environ,
        confinement.ENV_COMMANDE: json.dumps(commande),
        confinement.ENV_INTEGRITE: confinement.INTEGRITE_BASSE,
        confinement.ENV_RETENUES: "TEMOIN_RETENU_1515",
        "MAESTRO_TEMOIN_1515": "visible",
        "TEMOIN_RETENU_1515": "visible",
        "TEMOIN_HERITE_1515": "visible",
        "PYTHONPATH": str(ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-m", "maestro.sandbox.confinement"],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    output = result.stdout.decode("utf-8", "replace")
    assert low_integrity.LOW_INTEGRITY_SID in output, (output, result.stderr)
    assert "MAESTRO_TEMOIN_1515" not in output
    assert "TEMOIN_RETENU_1515" not in output
    # What the person's environment carries otherwise is inherited: `set` did list it.
    assert "TEMOIN_HERITE_1515=visible" in output
