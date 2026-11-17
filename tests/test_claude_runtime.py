"""Every run says what the Claude adapter ran on: SDK, CLI, the image's CLI (#1312).

① the readings (`maestro.providers.claude_runtime`) — each version read where it
  lives, an unreadable one said unknown, an image that does not carry the host's
  CLI said to be rebuilt;
② the run's journal — one line per run, at its first task, that no ledger counts
  and that the Control Tower files under the run, not under a phantom task;
③ `check_env`, which shows the same readings before anything runs.

No real CLI is launched here: the readers are doubles. The contract suite
(`tests/test_claude_contract.py`) reads the real one.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from maestro import check_env
from maestro.controltower.bridge import evenements_depuis_step
from maestro.engine.executor import LocalExecutor
from maestro.engine.retry import PolitiqueRelance
from maestro.orchestrator import Task
from maestro.providers import claude_runtime
from maestro.providers.base import Credentials, ModelProvider
from maestro.providers.claude import ClaudeProvider
from maestro.providers.claude_runtime import (
    IMAGE_CLI_LABEL,
    ClaudeRuntime,
    RuntimeVersions,
    image_label,
)
from maestro.sandbox import image_cli
from maestro.telemetry import STEP_VERSIONS, RunJournal, StepUsage
from maestro.telemetry.costs import RunCost

# --- ① The readings -------------------------------------------------------------------------------


def test_on_the_host_the_journal_reads_the_sdk_and_the_cli_it_ships():
    lines = RuntimeVersions(sdk="0.2.159", cli="2.1.281", cli_origin="paquet").as_lines()

    assert lines == {
        "SDK claude-agent-sdk": "0.2.159",
        "CLI claude": "2.1.281 (embarqué par le SDK)",
    }


def test_a_cli_taken_off_the_path_says_so():
    lines = RuntimeVersions(sdk="0.2.159", cli="2.1.300", cli_origin="poste").as_lines()
    assert lines["CLI claude"] == "2.1.300 (pris sur le PATH du poste, hors du SDK)"


def test_what_cannot_be_read_is_said_unknown_never_guessed():
    lines = RuntimeVersions(sdk=None, cli=None, cli_origin="absent").as_lines()
    assert lines == {
        "SDK claude-agent-sdk": "inconnue (paquet introuvable)",
        "CLI claude": "introuvable",
    }
    unread = RuntimeVersions(sdk="0.2.159", cli=None, cli_origin="paquet").as_lines()
    assert unread["CLI claude"] == "version inconnue (embarqué par le SDK)"


def test_an_image_that_carries_the_host_s_cli_matches():
    versions = RuntimeVersions(
        sdk="0.2.159", cli="2.1.281", cli_origin="paquet", image="maestro-sandbox:latest",
        image_cli="2.1.281",
    )
    assert versions.image_matches
    assert versions.as_lines()["CLI claude de l'image maestro-sandbox:latest"] == "2.1.281"


def test_an_image_with_another_cli_is_said_to_be_rebuilt():
    versions = RuntimeVersions(
        sdk="0.2.159", cli="2.1.281", cli_origin="paquet", image="maestro-sandbox:latest",
        image_cli="2.1.200",
    )
    assert not versions.image_matches
    line = versions.as_lines()["CLI claude de l'image maestro-sandbox:latest"]
    assert line.startswith("2.1.200 — ce n'est pas le CLI de l'hôte (2.1.281)")
    assert "à reconstruire" in line


def test_an_image_built_before_the_label_is_said_to_be_rebuilt():
    versions = RuntimeVersions(
        sdk="0.2.159", cli="2.1.281", cli_origin="paquet", image="maestro-sandbox:latest"
    )
    assert not versions.image_matches
    line = versions.as_lines()["CLI claude de l'image maestro-sandbox:latest"]
    assert IMAGE_CLI_LABEL in line and "à reconstruire" in line


def test_the_cli_is_read_once_per_binary_and_again_when_it_changes(monkeypatch, tmp_path):
    cli = tmp_path / "claude.exe"
    cli.write_bytes(b"one")
    monkeypatch.setattr(claude_runtime, "cli_claude_resolu", lambda: ("paquet", cli))
    reads: list[str] = []

    def read(path: str) -> str:
        reads.append(path)
        return f"2.1.{len(reads)}"

    runtime = ClaudeRuntime(read_version=read)

    assert runtime.versions().cli == "2.1.1"
    assert runtime.versions().cli == "2.1.1"
    assert len(reads) == 1
    cli.write_bytes(b"a replaced binary")
    assert runtime.versions().cli == "2.1.2"


def test_the_image_is_described_at_each_run(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_runtime, "cli_claude_resolu", lambda: ("paquet", None))
    labels = iter(["2.1.200", "2.1.281"])
    runtime = ClaudeRuntime(
        image="maestro-sandbox:latest", read_label=lambda image, label: next(labels)
    )

    assert runtime.versions().image_cli == "2.1.200"
    assert runtime.versions().image_cli == "2.1.281"


class _Docker:
    def __init__(self, returncode: int = 0, stdout: str = "", raises: Exception | None = None):
        self.returncode, self.stdout, self.raises = returncode, stdout, raises
        self.argv: list[str] = []

    def __call__(self, argv, **kwargs):
        self.argv = argv
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_the_label_is_read_in_docker_s_public_description(monkeypatch):
    docker = _Docker(stdout=json.dumps({IMAGE_CLI_LABEL: "2.1.281", "other": "x"}))
    monkeypatch.setattr(claude_runtime.subprocess, "run", docker)

    assert image_label("maestro-sandbox:latest", IMAGE_CLI_LABEL) == "2.1.281"
    assert docker.argv[:3] == ["docker", "image", "inspect"]


@pytest.mark.parametrize(
    "docker",
    [
        _Docker(returncode=1),
        _Docker(raises=FileNotFoundError("docker")),
        _Docker(stdout="not json"),
        _Docker(stdout="null"),
        _Docker(stdout=json.dumps({"other": "x"})),
        _Docker(stdout=json.dumps({IMAGE_CLI_LABEL: "  "})),
    ],
    ids=["no-image", "no-docker", "not-json", "no-labels", "no-label", "blank-label"],
)
def test_a_label_docker_cannot_give_is_none(monkeypatch, docker):
    monkeypatch.setattr(claude_runtime.subprocess, "run", docker)
    assert image_label("maestro-sandbox:latest", IMAGE_CLI_LABEL) is None


def test_the_image_is_built_with_the_cli_the_host_s_sdk_ships(monkeypatch, capsys, tmp_path):
    cli = tmp_path / "claude.exe"
    monkeypatch.setattr(image_cli, "cli_claude_resolu", lambda: ("paquet", cli))
    monkeypatch.setattr(image_cli, "lire_version", lambda path: "2.1.281")

    assert image_cli.main() == 0
    assert capsys.readouterr().out == "2.1.281\n"


def test_without_a_readable_cli_the_image_is_not_built(monkeypatch, capsys):
    monkeypatch.setattr(image_cli, "cli_claude_resolu", lambda: ("absent", None))

    assert image_cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "illisible" in captured.err


def test_the_provider_from_the_settings_reads_its_versions(monkeypatch):
    from maestro.config import load_settings

    provider = ClaudeProvider.from_settings(load_settings())
    seen = []
    monkeypatch.setattr(
        ClaudeRuntime,
        "versions",
        lambda self: seen.append(self.image)
        or RuntimeVersions(sdk="0.2.159", cli="2.1.281", cli_origin="paquet"),
    )

    assert provider.versions_du_fournisseur()["CLI claude"] == "2.1.281 (embarqué par le SDK)"
    assert len(seen) == 1
    assert ClaudeProvider(Credentials()).versions_du_fournisseur() == {}


# --- ② The run's journal --------------------------------------------------------------------------


class _Provider(ModelProvider):
    """Answers every task, and declares what it runs on — or fails to read it."""

    name = "double-versions"

    def __init__(self, versions=None, raises: Exception | None = None) -> None:
        self._versions = versions if versions is not None else {}
        self._raises = raises
        self.reads = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "fait"

    def versions_du_fournisseur(self):
        self.reads += 1
        if self._raises is not None:
            raise self._raises
        return self._versions


def _task(identifier: str) -> Task:
    return Task(
        id=identifier,
        title="Une tâche",
        description="…",
        required_competencies=("sql",),
        output_format="Texte",
        dependencies=(),
    )


def _run(provider: ModelProvider, journals: list[RunJournal], tasks_per_run: int = 2) -> None:
    executor = LocalExecutor(
        provider, runtimes={}, relance=PolitiqueRelance(max_tentatives=1, backoff_s=0.0)
    )

    async def play() -> None:
        for journal in journals:
            for index in range(tasks_per_run):
                await executor.execute(_task(f"t{index}"), [], journal)

    asyncio.run(play())


def _versions_records(journal: RunJournal):
    return [record for record in journal.records if record.etape == STEP_VERSIONS]


def test_each_run_writes_once_what_its_provider_runs_on():
    provider = _Provider(
        {"SDK claude-agent-sdk": "0.2.159", "CLI claude": "2.1.281 (embarqué par le SDK)"}
    )
    first, second = RunJournal(), RunJournal()

    _run(provider, [first, second])

    for journal in (first, second):
        (record,) = _versions_records(journal)
        assert record.sortie == (
            "SDK claude-agent-sdk : 0.2.159 · CLI claude : 2.1.281 (embarqué par le SDK)"
        )
        assert record.nom == "Versions du fournisseur"
        assert record.usage == StepUsage()
        assert journal.records.index(record) == 0
    assert provider.reads == 2


def test_a_provider_that_declares_nothing_writes_nothing():
    journal = RunJournal()
    _run(_Provider({}), [journal])
    assert _versions_records(journal) == []


def test_a_reading_that_fails_is_said_and_does_not_fail_the_task():
    journal = RunJournal()

    _run(_Provider(raises=OSError("docker")), [journal], tasks_per_run=1)

    (record,) = _versions_records(journal)
    assert record.sortie == "versions illisibles (OSError)"
    assert any(r.etape != STEP_VERSIONS and r.sortie == "fait" for r in journal.records)


def test_no_ledger_counts_the_versions_line():
    journal = RunJournal()
    _run(_Provider({"CLI claude": "2.1.281"}), [journal], tasks_per_run=1)

    ledger = RunCost.from_journal(journal)

    assert [task.task_id for task in ledger.tasks] == ["t0"]


def test_the_control_tower_files_the_versions_under_the_run():
    line = {
        "run_id": "run-versions",
        "etape": STEP_VERSIONS,
        "nom": "Versions du fournisseur",
        "agent": "orchestrateur",
        "role": "Orchestrateur",
        "statut": "terminee",
        "sortie": "CLI claude : 2.1.281",
        "usage": StepUsage().to_dict(),
        "horodatage": "2026-11-17T10:00:00+00:00",
    }

    (event,) = evenements_depuis_step(line)

    assert event.etape_run == STEP_VERSIONS


# --- ③ check_env ----------------------------------------------------------------------------------


def test_check_env_shows_the_versions_and_goes_red_on_what_is_unread_or_mismatched():
    ok, detail = check_env._verdict_versions(
        RuntimeVersions(sdk="0.2.159", cli="2.1.281", cli_origin="paquet")
    )
    assert ok
    assert detail == "SDK claude-agent-sdk : 0.2.159 · CLI claude : 2.1.281 (embarqué par le SDK)"

    for versions in (
        RuntimeVersions(sdk="0.2.159", cli=None, cli_origin="paquet"),
        RuntimeVersions(sdk=None, cli="2.1.281", cli_origin="paquet"),
        RuntimeVersions(
            sdk="0.2.159", cli="2.1.281", cli_origin="paquet", image="i", image_cli="2.1.200"
        ),
    ):
        assert not check_env._verdict_versions(versions)[0]


def test_check_env_reads_the_image_of_container_mode(monkeypatch):
    seen: list[str | None] = []
    monkeypatch.setenv("MAESTRO_ISOLATION", "conteneur")
    monkeypatch.setattr(
        ClaudeRuntime,
        "versions",
        lambda self: seen.append(self.image)
        or RuntimeVersions(sdk="0.2.159", cli="2.1.281", cli_origin="paquet"),
    )

    check_env._check_versions()

    assert seen and seen[0] is not None


def test_the_sdk_version_is_the_installed_distribution_s():
    from importlib import metadata

    assert claude_runtime.sdk_version() == metadata.version("claude-agent-sdk")


def test_the_paths_named_in_the_module_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "infra" / "sandbox" / "Dockerfile").is_file()
    assert (root / "tests" / "test_claude_contract.py").is_file()
