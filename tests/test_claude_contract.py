"""The Claude adapter's contract, proven on the CLI its SDK ships (#1312, docs/44 §7).

The adapter rests on behaviours of the CLI: how a session is closed off, how a
refusal of Maestro holds, how usage is reported, how a turn limit ends a session.
Doubles cannot prove them — they say what we expect, not what the CLI does. This
suite proves them on the real CLI, with a real model, and it is **played before
any change of the SDK's version**:

    env MAESTRO_TESTS_CLI_REEL=1 .venv/Scripts/python.exe -m pytest -m claude_contract

A version that reddens it does not pass; the version it passed on is written in
docs/44 §7, and `pyproject.toml` pins nothing else (`test_the_pinned_sdk_is_a_verified_one`).

The suite is everything marked `claude_contract`: this module, and the contract
tests that live next to their mechanism — the checkpoint (#1304,
`tests/test_point_de_controle.py`) and the folder of the text paths (#1397,
`tests/test_dossier_neutre.py`). Each isolation test has its **faulty sample**: the
same question asked where the door is open, whose witness must come out — without
it, a green would be blindness.

What launches the CLI carries `cli_reel` (skipped unless asked, tests/conftest.py);
what reads the repository only runs everywhere, CI included. The model is the most
economical of the range, read in `familles-claude.tsv` — never written here.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from importlib import metadata
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage

from maestro.providers import claude as claude_mod
from maestro.providers.base import Credentials, TurnLimitReached
from maestro.providers.claude import ClaudeProvider
from maestro.providers.claude_runtime import IMAGE_CLI_LABEL, ClaudeRuntime
from maestro.telemetry import collect_usage

pytestmark = pytest.mark.claude_contract

ROOT = Path(__file__).resolve().parents[1]

#: The table of verified versions: SDK, the CLI it ships, the day the suite was green.
_VERIFIED_ROW = re.compile(
    r"^\|\s*(?P<sdk>\d+\.\d+\.\d+)\s*\|\s*(?P<cli>\d+\.\d+\.\d+)\s*\|\s*(?P<day>\d{4}-\d{2}-\d{2})\s*\|",
    re.MULTILINE,
)


def _verified() -> dict[str, str]:
    """docs/44 §7: each SDK version the suite passed on, with the CLI it shipped."""
    doc = ROOT / "docs" / "44-decision-maestro-possede-ses-contrats.md"
    text = doc.read_text(encoding="utf-8")
    section = text.split("## 7.", 1)[1].split("\n## ", 1)[0]
    return {row["sdk"]: row["cli"] for row in _VERIFIED_ROW.finditer(section)}


def _pinned_sdk() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (pin,) = re.findall(r'"claude-agent-sdk([^"]*)"', text)
    assert pin.startswith("=="), f"the SDK is not pinned: claude-agent-sdk{pin}"
    return pin[2:]


def _economical_model() -> str:
    from maestro.familles_claude import derniere_version

    return derniere_version("haiku")


def _witness(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"



# --- The lock: read everywhere --------------------------------------------------------------------


def test_the_pinned_sdk_is_a_verified_one():
    """The SDK is pinned, the installed one is it, and the suite has passed on it."""
    pinned = _pinned_sdk()

    assert metadata.version("claude-agent-sdk") == pinned
    assert pinned in _verified(), (
        f"claude-agent-sdk {pinned} has no row in docs/44 §7: play the contract suite on it, "
        "then write the version it passed on"
    )


def test_the_sandbox_image_installs_the_cli_it_is_given_and_says_which():
    dockerfile = (ROOT / "infra" / "sandbox" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^ARG CLAUDE_CODE_VERSION\s*$", dockerfile, re.MULTILINE), (
        "the CLI version is a required build argument, without a default"
    )
    assert '"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"' in dockerfile
    assert not re.search(r"@anthropic-ai/claude-code(\s|$)", dockerfile), "an unversioned install"
    assert f"LABEL {IMAGE_CLI_LABEL}=$CLAUDE_CODE_VERSION" in dockerfile
    docs = (ROOT / "docs" / "17-isolation-execution.md").read_text(encoding="utf-8")
    assert "--build-arg CLAUDE_CODE_VERSION=" in docs


# --- The CLI the SDK ships ------------------------------------------------------------------------


@pytest.mark.cli_reel
def test_the_cli_the_sdk_ships_is_the_verified_one():
    versions = ClaudeRuntime().versions()

    assert versions.cli_origin == "paquet", "the adapter must run the CLI its SDK ships"
    assert versions.cli is not None
    assert _verified().get(_pinned_sdk()) == versions.cli


# --- A text path exposes no tool ------------------------------------------------------------------


@pytest.fixture
def folder_with_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A folder holding a token file, made the text paths' folder."""
    token = _witness("JETON")
    (tmp_path / "jeton.txt").write_text(f"{token}\n", encoding="utf-8")
    monkeypatch.setattr(claude_mod, "dossier_neutre", lambda: tmp_path)
    return tmp_path, token


_READ_THE_TOKEN = (
    "Lis le fichier jeton.txt du dossier courant avec ton outil de lecture de fichiers, "
    "puis réponds par son contenu exact, sans rien ajouter. Si tu n'as aucun outil pour "
    "le lire, réponds exactement SANS-OUTIL."
)


@pytest.mark.cli_reel
def test_sample_an_agent_with_a_read_tool_finds_the_token(folder_with_a_token):
    """The faulty sample: where a tool is exposed, the question does bring the token out."""
    folder, token = folder_with_a_token

    answer = asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            _READ_THE_TOKEN, model=_economical_model(), workspace=folder, tools=("Read",)
        )
    )

    assert token in answer, answer


@pytest.mark.cli_reel
def test_a_text_path_exposes_no_tool(folder_with_a_token):
    _, token = folder_with_a_token

    answer = asyncio.run(
        ClaudeProvider(Credentials()).generate(_READ_THE_TOKEN, model=_economical_model())
    )

    assert answer.strip(), "an empty answer proves nothing"
    assert token not in answer, answer


# --- Neither CLAUDE.md nor a skill of the folder enters a session ---------------------------------


@pytest.fixture
def folder_with_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, str]:
    """A folder with a `CLAUDE.md` and a skill, each carrying its witness."""
    password, skill = _witness("MEMOIRE"), _witness("SKILL")
    (tmp_path / "CLAUDE.md").write_text(
        f"# Instructions du projet\n\n- Le mot de passe de ce projet est {password}.\n",
        encoding="utf-8",
    )
    folder = tmp_path / ".claude" / "skills" / skill.lower()
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {skill.lower()}\ndescription: Skill témoin {skill}, à citer quand on te "
        f"demande tes skills.\n---\n\nRéponds {skill}.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_mod, "dossier_neutre", lambda: tmp_path)
    return tmp_path, password, skill


_ASK_THE_INSTRUCTIONS = (
    "Quel mot de passe tes instructions de projet donnent-elles, et quels skills as-tu à ta "
    "disposition (nom et description) ? Réponds de mémoire, sans lire aucun fichier. Si tu "
    "n'as ni instruction de projet ni skill, réponds exactement AUCUN."
)


@pytest.mark.cli_reel
def test_sample_project_settings_bring_the_folder_s_instructions_in(
    folder_with_instructions, monkeypatch
):
    """The faulty sample: the CLI's defaults reopened, the witnesses come out."""
    _, password, skill = folder_with_instructions
    monkeypatch.setattr(claude_mod, "sans_reglages_du_poste", lambda: ["project"])
    monkeypatch.setattr(claude_mod, "sans_skills_du_poste", lambda: None)

    answer = asyncio.run(
        ClaudeProvider(Credentials()).generate(_ASK_THE_INSTRUCTIONS, model=_economical_model())
    )

    assert password in answer, answer


@pytest.mark.cli_reel
def test_no_instruction_nor_skill_of_the_folder_enters_a_session(folder_with_instructions):
    """Neither the text path nor an agent session reads the folder's `CLAUDE.md` or skills."""
    folder, password, skill = folder_with_instructions
    provider = ClaudeProvider(Credentials())
    model = _economical_model()

    async def both() -> list[str]:
        text = await provider.generate(_ASK_THE_INSTRUCTIONS, model=model)
        agent = await provider.run_agent(
            _ASK_THE_INSTRUCTIONS, model=model, workspace=folder, tools=()
        )
        return [text, agent]

    answers = asyncio.run(both())

    assert all(answer.strip() for answer in answers), answers
    for answer in answers:
        assert password not in answer and skill not in answer, answers


# --- Usage: counted once per turn, settled on the result ------------------------------------------


@pytest.mark.cli_reel
def test_usage_is_counted_once_per_turn_and_settles_on_what_the_cli_bills(tmp_path, monkeypatch):
    """What runs during a session never exceeds what its result bills, and the sum is the bill.

    Several API calls (one per file read, then the answer): the CLI may repeat one
    call's `usage` on several messages, which only their `message_id` tells apart.
    The provider counts each call once while the session runs (#835), then settles
    on the result: the in-flight count is bounded by the bill, and the total is it.
    """
    for name in ("a", "b"):
        (tmp_path / f"{name}.txt").write_text(f"contenu {name}\n", encoding="utf-8")
    messages: list[object] = []
    reports = []
    absorb, report = claude_mod._absorbe, claude_mod.report_usage

    def spy_absorb(message, *args, **kwargs):
        messages.append(message)
        return absorb(message, *args, **kwargs)

    def spy_report(usage):
        reports.append(usage)
        report(usage)

    monkeypatch.setattr(claude_mod, "_absorbe", spy_absorb)
    monkeypatch.setattr(claude_mod, "report_usage", spy_report)

    with collect_usage() as harvest:
        asyncio.run(
            ClaudeProvider(Credentials()).run_agent(
                "Lis le fichier a.txt avec l'outil Read, puis, dans un second appel, le fichier "
                "b.txt, puis réponds par leurs deux contenus.",
                model=_economical_model(),
                workspace=tmp_path,
                tools=("Read",),
            )
        )

    (result,) = [m for m in messages if isinstance(m, ResultMessage)]
    turns = [m for m in messages if isinstance(m, AssistantMessage) and m.usage]
    assert result.usage and result.total_cost_usd is not None
    assert turns and all(m.message_id for m in turns), "a turn without its identifier"
    assert len({m.message_id for m in turns}) >= 2, "one API call cannot show the count"
    billed_in = claude_mod._tokens_entree(result.usage)
    billed_out = int(result.usage.get("output_tokens") or 0)
    in_flight = [usage for usage in reports if usage.calls == 0]
    assert sum(u.output_tokens for u in in_flight) <= billed_out
    assert sum(u.input_tokens for u in in_flight) <= billed_in
    assert harvest.total.input_tokens == billed_in
    assert harvest.total.output_tokens == billed_out
    assert harvest.total.cost_usd == pytest.approx(result.total_cost_usd)


# --- A turn limit is a typed error ----------------------------------------------------------------


@pytest.mark.cli_reel
@pytest.mark.xfail(
    strict=True,
    raises=Exception,
    reason="#1305 : depuis le SDK 0.2.159, le plafond de tours n'est plus reconnu par le "
    "fournisseur, qui lisait le texte de l'erreur ; il se lira dans son champ typé",
)
def test_a_turn_limit_ends_the_session_as_turn_limit_reached(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.txt").write_text(f"contenu {name}\n", encoding="utf-8")

    with pytest.raises(TurnLimitReached):
        asyncio.run(
            ClaudeProvider(Credentials()).run_agent(
                "Lis a.txt, puis b.txt, puis c.txt, chacun avec l'outil Read dans un appel "
                "séparé, puis réponds par leurs contenus.",
                model=_economical_model(),
                workspace=tmp_path,
                tools=("Read",),
                plafond_tours=1,
            )
        )


# --- The session's environment: the named variables only ------------------------------------------


@pytest.fixture
def ambient(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Two ambient variables: a probe nobody names, a witness the person names."""
    probe, witness = _witness("SONDE"), _witness("TEMOIN")
    monkeypatch.setenv("CONTRACT_PROBE", probe)
    monkeypatch.setenv("CONTRACT_WITNESS", witness)
    return probe, witness


_ECHO = (
    "Lance exactement cette commande avec l'outil Bash, en un seul appel : "
    'echo "sonde=[$CONTRACT_PROBE] temoin=[$CONTRACT_WITNESS]" '
    "— puis recopie sa sortie telle quelle, sans rien ajouter."
)


def _echo(tmp_path: Path, named: tuple[str, ...]) -> str:
    provider = ClaudeProvider(Credentials(), env_agent=named)
    return asyncio.run(
        provider.run_agent(_ECHO, model=_economical_model(), workspace=tmp_path, tools=("Bash",))
    )


@pytest.mark.cli_reel
def test_sample_a_variable_the_person_names_reaches_the_agent_s_shell(ambient, tmp_path):
    """The faulty sample: named, the probe does come out of the agent's shell."""
    probe, witness = ambient

    output = _echo(tmp_path, ("CONTRACT_PROBE", "CONTRACT_WITNESS"))

    assert probe in output and witness in output, output


@pytest.mark.cli_reel
def test_an_ambient_variable_nobody_names_does_not_reach_the_agent_s_shell(ambient, tmp_path):
    probe, witness = ambient

    output = _echo(tmp_path, ("CONTRACT_WITNESS",))

    assert witness in output, f"the shell did not run, the absence proves nothing: {output}"
    assert probe not in output, output
