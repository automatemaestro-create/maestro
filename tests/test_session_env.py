"""An agent session receives the variables someone named, and no others (#1312).

`maestro.sandbox.session_env` holds the rule; the confinement launcher applies it to
the CLI it starts; the Claude provider says what it names. Three layers, three
groups of tests:

① the rule alone — names, families, fingerprints, what a damaged payload does;
② the launcher, played for real on a session that writes the environment it got;
③ what the provider names, and that a host agent session hands it to the launcher.

What only the real CLI shows — that the agent's Bash does not see an unnamed
variable — is proven by the contract suite (`tests/test_claude_contract.py`).
Container mode has its own whitelist (`ENV_TRANSMISES`), kept by
`tests/test_isolation.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from maestro.providers import claude as claude_mod
from maestro.providers.base import Credentials
from maestro.providers.claude import VARIABLES_NOMMEES_DU_CLI, ClaudeProvider
from maestro.sandbox import confinement, low_session, session_env
from maestro.sandbox.confinement import ENV_AMBIANTES, ENV_COMMANDE

#: The repository root: the launcher, played by `python -m`, must import **this** `maestro`.
ROOT = Path(__file__).resolve().parents[1]

#: The launcher, as the SDK would start it.
LAUNCHER = [sys.executable, "-m", "maestro.sandbox.confinement"]

#: The interpreter of the fake session — the base one, not the venv's redirector
#: (same reason as `tests/test_confinement.py`).
PYTHON = getattr(sys, "_base_executable", sys.executable)

WINDOWS = sys.platform == "win32"


# --- ① The rule -----------------------------------------------------------------------------------


def test_the_person_names_variables_by_a_comma_separated_list():
    assert session_env.parse_names(" GITHUB_TOKEN, ,NPM_*,") == ("GITHUB_TOKEN", "NPM_*")
    assert session_env.parse_names(None) == ()
    assert session_env.parse_names("") == ()


def test_a_name_is_named_by_itself_or_by_its_family():
    patterns = ("PATH", "LC_*", "NPM_*")
    assert session_env.is_named("PATH", patterns)
    assert session_env.is_named("LC_ALL", patterns)
    assert session_env.is_named("NPM_CONFIG_CACHE", patterns)
    assert not session_env.is_named("GITHUB_TOKEN", patterns)
    assert not session_env.is_named("XPATH", patterns)


@pytest.mark.skipif(not WINDOWS, reason="Windows names its variables without case")
def test_on_windows_a_name_is_named_whatever_its_case():
    assert session_env.is_named("Path", ("PATH",))
    assert session_env.is_named("lc_all", ("LC_*",))


def test_the_base_names_what_any_session_needs_and_no_secret():
    for needed in ("PATH", "SYSTEMROOT", "USERPROFILE", "HOME", "TEMP", "TMP", "LANG"):
        assert session_env.is_named(needed, session_env.BASE_NAMES)
    for ambient in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "MAESTRO_API_PORT"):
        assert not session_env.is_named(ambient, session_env.BASE_NAMES)


def test_an_unnamed_variable_is_withheld_by_a_fingerprint_that_does_not_reveal_it():
    environ = {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_secret_value"}

    withheld = session_env.withheld(environ, session_env.BASE_NAMES)

    assert set(withheld) == {"GITHUB_TOKEN"}
    payload = session_env.encode(withheld)
    assert "ghp_secret_value" not in payload
    assert session_env.decode(payload) == withheld
    assert withheld["GITHUB_TOKEN"] == session_env.fingerprint("ghp_secret_value")
    assert len(withheld["GITHUB_TOKEN"]) == session_env.FINGERPRINT_LENGTH


def test_the_filter_removes_what_arrived_unchanged_and_keeps_what_was_set_on_the_way():
    ambient = {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_x", "PWD": "/where/the/api/runs"}
    withheld = session_env.withheld(ambient, ("PATH",))
    # On the way to the CLI, the SDK sets `PWD` to the session's folder, and adds its own.
    arrived = {**ambient, "PWD": "/the/workspace", "CLAUDE_AGENT_SDK_VERSION": "0.2.159"}

    kept = session_env.without_withheld(arrived, withheld)

    assert kept == {
        "PATH": "/usr/bin",
        "PWD": "/the/workspace",
        "CLAUDE_AGENT_SDK_VERSION": "0.2.159",
    }


def test_an_empty_list_of_withheld_variables_filters_nothing():
    environ = {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_x"}
    assert session_env.without_withheld(environ, {}) == environ


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", '{"GITHUB_TOKEN": 3}', '"GITHUB_TOKEN"'],
)
def test_a_damaged_payload_is_refused_never_half_read(payload):
    with pytest.raises(ValueError):
        session_env.decode(payload)


# --- ② The launcher -------------------------------------------------------------------------------


SESSION = textwrap.dedent(
    '''
    """A session: writes the environment it received, and leaves."""
    import json, os
    with open(os.environ["SESSION_SEEN"], "w", encoding="utf-8") as seen:
        json.dump(dict(os.environ), seen)
    '''
)


def _launch(
    tmp_path: Path, environ: dict[str, str], withheld: str | None
) -> subprocess.CompletedProcess:
    script = tmp_path / "session.py"
    script.write_text(SESSION, encoding="utf-8")
    env = {
        **environ,
        "SESSION_SEEN": str(tmp_path / "seen.json"),
        ENV_COMMANDE: json.dumps([PYTHON, str(script)]),
        "PYTHONPATH": str(ROOT),
    }
    if withheld is not None:
        env[ENV_AMBIANTES] = withheld
    return subprocess.run(
        LAUNCHER,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _seen(tmp_path: Path) -> dict[str, str]:
    return json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))


def test_the_launcher_withholds_an_unnamed_variable_and_keeps_one_set_on_the_way(tmp_path):
    ambient = {**os.environ, "CONTRACT_AMBIENT": "from-the-workstation", "CONTRACT_SET": "before"}
    withheld = session_env.withheld(ambient, (*session_env.BASE_NAMES, "PYTHONPATH"))
    assert {"CONTRACT_AMBIENT", "CONTRACT_SET"} <= set(withheld)
    # `CONTRACT_SET` is changed on the way, as the SDK changes `PWD`.
    arrived = {**ambient, "CONTRACT_SET": "set-by-the-sdk"}

    done = _launch(tmp_path, arrived, session_env.encode(withheld))

    assert done.returncode == 0, done.stderr
    seen = _seen(tmp_path)
    assert "CONTRACT_AMBIENT" not in seen
    assert seen["CONTRACT_SET"] == "set-by-the-sdk"
    assert ENV_AMBIANTES not in seen
    assert "PATH" in {name.upper() for name in seen}


def test_without_the_list_the_launcher_passes_the_ambient_variable(tmp_path):
    """The faulty sample: no list, no filter — the variable arrives, so the test above sees."""
    done = _launch(tmp_path, {**os.environ, "CONTRACT_AMBIENT": "from-the-workstation"}, None)

    assert done.returncode == 0, done.stderr
    assert _seen(tmp_path)["CONTRACT_AMBIENT"] == "from-the-workstation"


def test_a_damaged_list_stops_the_launcher_before_the_cli(tmp_path):
    done = _launch(tmp_path, dict(os.environ), "not json")

    assert done.returncode == 2
    assert confinement.NOM_LANCEUR in done.stderr
    assert not (tmp_path / "seen.json").exists()


def test_the_list_reaches_the_launcher_through_the_prepared_session(monkeypatch):
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path(sys.executable))

    session = confinement.SessionConfinee.preparer(["claude"], ambiantes={"GITHUB_TOKEN": "abc"})

    assert session_env.decode(session.env[ENV_AMBIANTES]) == {"GITHUB_TOKEN": "abc"}
    session.solder()
    assert ENV_AMBIANTES not in confinement.SessionConfinee.preparer(["claude"]).env


def test_an_unconfined_session_says_it_received_the_whole_environment():
    releve = confinement.SessionConfinee.preparer(None).solder()
    assert "l'environnement du poste entier" in releve.phrase()


# --- ③ What the provider names --------------------------------------------------------------------


@pytest.fixture
def workstation(monkeypatch):
    """A workstation with an ambient secret, a gateway the person declared, and Maestro's own."""
    monkeypatch.setenv("CONTRACT_AMBIENT_SECRET", "s3cr3t")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_maestro")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", r"C:\Git\bin\bash.exe")
    monkeypatch.setenv("NAMED_BY_THE_PERSON", "kept")
    monkeypatch.setattr(
        low_session,
        "maestro_variables",
        lambda: frozenset({"ANTHROPIC_BASE_URL", "GITHUB_TOKEN"}),
    )


def test_the_provider_withholds_what_nobody_named(workstation):
    provider = ClaudeProvider(Credentials(), env_agent=("NAMED_BY_THE_PERSON",))

    set_by_the_provider = {"CLAUDE_CODE_OAUTH_TOKEN": "set-by-the-provider"}
    withheld = provider._ambiantes_retenues(set_by_the_provider, None)

    # Maestro's own variables are not the agent's (docs/17 §8): withheld like any other.
    assert {"CONTRACT_AMBIENT_SECRET", "GITHUB_TOKEN"} <= set(withheld)
    for named in (
        "PATH",
        "ANTHROPIC_BASE_URL",  # the CLI's namespace, declared by Maestro's .env
        "CLAUDE_CODE_OAUTH_TOKEN",  # set by the provider
        "NAMED_BY_THE_PERSON",  # MAESTRO_AGENT_ENV
        *VARIABLES_NOMMEES_DU_CLI,
    ):
        assert not any(name.upper() == named.upper() for name in withheld), named


def test_the_person_names_variables_through_the_settings(monkeypatch):
    from maestro.config import load_settings

    monkeypatch.setenv("MAESTRO_AGENT_ENV", "NPM_TOKEN, PIP_*")
    settings = load_settings()

    provider = ClaudeProvider.from_settings(settings)

    assert settings.env_agent == "NPM_TOKEN, PIP_*"
    assert provider._env_agent == ("NPM_TOKEN", "PIP_*")


def test_a_host_agent_session_hands_the_list_to_the_launcher(workstation, monkeypatch, tmp_path):
    """The run_agent seam: what `_ambiantes_retenues` says is what the prepared session carries."""
    prepared: dict[str, object] = {}

    def preparer(commande, **kwargs):
        prepared.update(kwargs)
        raise RuntimeError("stop before the CLI")

    monkeypatch.setattr(claude_mod.SessionConfinee, "preparer", preparer)
    monkeypatch.setattr(claude_mod, "_commande_du_cli", lambda: ("claude",))
    provider = ClaudeProvider(Credentials())

    with pytest.raises(RuntimeError, match="stop before the CLI"):
        asyncio.run(provider.run_agent("x", model="m", workspace=tmp_path, tools=("Bash",)))

    withheld = prepared["ambiantes"]
    assert isinstance(withheld, dict)
    assert "CONTRACT_AMBIENT_SECRET" in withheld
    assert withheld["CONTRACT_AMBIENT_SECRET"] == session_env.fingerprint("s3cr3t")
