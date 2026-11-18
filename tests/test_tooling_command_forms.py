"""A command keeps the form its instructions give it, after a run as at import (#1717).

Measured by #1688 on the Maestro repository, imported into the chat: its `CLAUDE.md` writes
`.venv/Scripts/python.exe -m pytest tests/…`, where `tests/…` stands for the suite one targets.
The review after the run (#1343, #1381) read it again and declared
`.venv/Scripts/python.exe -m pytest tests/`, which runs the whole suite. The reading at import
had declared, for the tests, only `bash scripts/ci/local.sh` and `cd apps/web && npm test`.

What this suite keeps:

1. **a form cut short is not a command** — a command the cited file only writes as the start of
   a longer form (`tests/…`, `<arguments pytest>`) would run everything that form left open: the
   confrontation sets it aside, with the form it was cut from. The file's code spans are the
   evidence, never a list of words;
2. **the review after a run concludes on what failed** — its question is the failed usages, and
   whatever it renders for another usage stays with the reading it does not replace;
3. **the instructions still hold after a run** — a failed usage that the instructions prescribe
   or exclude is never replaced by a command they set aside (the tables' bare `pytest`), unless
   the instructions file changed since the import.

No network, no model: the model that reads is a fake provider, and the commands are played by a
fake runner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from maestro.controltower.outillage import ServiceOutillage
from maestro.controltower.pieces import ServicePieces
from maestro.controltower.projets import ServiceProjets
from maestro.outillage import analyser, lire_le_projet
from maestro.outillage.exploration import CADRE_LECTURE
from maestro.outillage.modele import ORIGINE_PRESCRITE, Analyse
from maestro.outillage.verification import ECHOUEE, Verificateur, Verification
from maestro.projets import ProjetStore
from maestro.projets.modele import Perimetre
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

#: The rules of a repository that writes its instructions like Maestro's `CLAUDE.md` does: a
#: whole command, a form with a part to fill, and a script whose argument is a placeholder.
CLAUDE_MD = """\
# Règles du dépôt

- Avant de pousser : `bash scripts/ci/local.sh`, qui joue le périmètre du diff.
- Itération ciblée : `.venv/bin/python -m pytest tests/…` pour une suite applicative.
- Une suite d'outillage : `bash scripts/ci/pytest.sh <arguments pytest>`.
- Ne rejoue pas la suite complète.
"""

#: The rules once a run has changed them on the point of the tests: they no longer say how.
CLAUDE_MD_CHANGED = """\
# Règles du dépôt

- Écris tes tests avant ton code.
"""

#: The command the instructions prescribe for the tests, and the one the tables deduce.
PRESCRIBED = "bash scripts/ci/local.sh"
DEDUCED = "pytest"

#: The command the review read in `CLAUDE.md` on #1688, its `…` removed: the whole suite.
CUT = ".venv/bin/python -m pytest tests/"

#: A fake shell: the fake runner never launches it.
FAKE_BASH = ("bash", "-c")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `Path.home()` under `tmp_path` — on Windows, `tmp_path` lives under `AppData` (#221)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class _Reader(ModelProvider):
    """The model that reads the project: it replays its answers, one per call, in order."""

    name: ClassVar[str] = "fake-reader"

    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise AssertionError("the project had no more reason to be read")
        return self._answers.pop(0)


class _Runner:
    """A fake runner: one result per command, and what it played, in order."""

    def __init__(self, results: dict[str, execution.Execution]) -> None:
        self._results = results
        self.played: list[str] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.played.append(commande)
        return self._results.get(commande, execution.Execution(code=0, sortie="ok", duree_s=0.1))


def write(root: Path, path: str, content: str) -> Path:
    """Puts a file under `root`, parent folders included."""
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def repository(root: Path, claude_md: str = CLAUDE_MD) -> Path:
    """A Python repository whose configuration declares `pytest`, and whose rules say otherwise."""
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "depot"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    )
    write(root, "depot/__init__.py", "")
    write(root, "tests/test_depot.py", "def test_depot():\n    assert True\n")
    write(root, "scripts/ci/local.sh", "#!/usr/bin/env bash\necho local\n")
    write(root, "scripts/ci/pytest.sh", "#!/usr/bin/env bash\necho pytest\n")
    write(root, "CLAUDE.md", claude_md)
    return root


def conclusion(*lines: str) -> str:
    """A model's conclusion: its findings, then `FIN`."""
    return "\n".join((*lines, "FIN"))


def line_for_tests(origin_line: str, command: str) -> str:
    """A `COMMANDE` line for the tests, read in `CLAUDE.md`."""
    return f"COMMANDE: tester | CLAUDE.md | declaree | {origin_line} | {command}"


def read(root: Path, answer: str, failures: Sequence[Verification] = ()) -> Analyse:
    """The repository analysed, then read by a model that opens `CLAUDE.md` and concludes."""
    reader = _Reader(("LIRE: CLAUDE.md", answer))
    return asyncio.run(
        lire_le_projet(
            analyser(root),
            perimetre=Perimetre(),
            provider=reader,
            modele="fake",
            echecs=failures,
        )
    )


def commands_of(analysis: Analyse, usage: str) -> list[str]:
    """The commands the analysis keeps for `usage`, in their order."""
    return [c.commande for c in analysis.constats.commandes if c.usage == usage]


def reasons_for(analysis: Analyse, command: str) -> list[str]:
    """Why the reading set aside each line that renders `command`."""
    assert analysis.lecture is not None
    return [
        e.raison for e in analysis.lecture.ecartes if e.ligne.rstrip().endswith(f"| {command}")
    ]


# --------------------------------------------------------------------------- #
# 1. A form cut short is not a command                                         #
# --------------------------------------------------------------------------- #


def test_a_command_cut_from_the_form_the_instructions_write_is_set_aside(tmp_path: Path) -> None:
    """The defect of #1688: `tests/…` read as `tests/`, the whole suite."""
    analysis = read(
        repository(tmp_path),
        conclusion(
            line_for_tests("avant de pousser", PRESCRIBED),
            line_for_tests("itération ciblée", CUT),
        ),
    )

    assert CUT not in commands_of(analysis, "tester")
    (reason,) = reasons_for(analysis, CUT)
    assert "`.venv/bin/python -m pytest tests/…`" in reason
    assert "CLAUDE.md" in reason
    # The whole command the same file writes is still prescribed.
    tests = analysis.constats.commande_de("tester")
    assert tests is not None
    assert (tests.commande, tests.origine) == (PRESCRIBED, ORIGINE_PRESCRITE)


def test_a_placeholder_argument_dropped_is_set_aside_too(tmp_path: Path) -> None:
    """`<arguments pytest>` left out: the script would run every suite it was meant to narrow."""
    analysis = read(
        repository(tmp_path),
        conclusion(line_for_tests("une suite d'outillage", "bash scripts/ci/pytest.sh")),
    )

    assert "bash scripts/ci/pytest.sh" not in commands_of(analysis, "tester")
    (reason,) = reasons_for(analysis, "bash scripts/ci/pytest.sh")
    assert "`bash scripts/ci/pytest.sh <arguments pytest>`" in reason


def test_a_form_in_a_fenced_block_counts_too(tmp_path: Path) -> None:
    """A command written on its own line, prompt included, is a form like an inline one."""
    rules = "# Règles\n\nPour une suite :\n\n```bash\n$ make test SUITE=…\n```\n"
    analysis = read(
        repository(tmp_path, claude_md=rules),
        conclusion(line_for_tests("une suite", "make test")),
    )

    assert "make test" not in commands_of(analysis, "tester")
    (reason,) = reasons_for(analysis, "make test")
    assert "`make test SUITE=…`" in reason


def test_a_command_the_instructions_also_write_whole_is_kept(tmp_path: Path) -> None:
    """A longer form elsewhere in the file does not cut a command the file writes whole."""
    rules = CLAUDE_MD + "- Tout rejouer : `bash scripts/ci/local.sh --complet`.\n"
    analysis = read(
        repository(tmp_path, claude_md=rules),
        conclusion(line_for_tests("avant de pousser", PRESCRIBED)),
    )

    assert commands_of(analysis, "tester")[0] == PRESCRIBED
    assert reasons_for(analysis, PRESCRIBED) == []


def test_a_command_drawn_from_what_the_instructions_name_is_kept(tmp_path: Path) -> None:
    """An option read in the script the instructions name is not a form cut short (#1678)."""
    drawn = "bash scripts/ci/local.sh --only lint"
    analysis = read(
        repository(tmp_path),
        conclusion(f"COMMANDE: lint | CLAUDE.md | declaree | le job lint | {drawn}"),
    )

    lint = analysis.constats.commande_de("lint")
    assert lint is not None
    assert (lint.commande, lint.origine) == (drawn, ORIGINE_PRESCRITE)


def test_the_frame_tells_the_model_that_a_form_is_not_a_command() -> None:
    """The model is told before it answers; the confrontation checks after."""
    frame = " ".join(CADRE_LECTURE.split())

    assert "est une forme, pas une commande" in frame
    assert "amputée" in frame


# --------------------------------------------------------------------------- #
# 2. The review after a run concludes on what failed                           #
# --------------------------------------------------------------------------- #

#: What failed on #1688 before the review read the project again.
START_FAILED = Verification(
    usage="demarrer",
    commande="npm run start",
    etat=ECHOUEE,
    raison="a échoué",
    code=127,
    sortie="npm error enoent Could not read package.json",
)


def test_the_review_keeps_only_the_usages_that_failed(tmp_path: Path) -> None:
    """On #1688, the start command failed, and the review declared commands for the tests."""
    root = repository(tmp_path)
    write(root, "scripts/start.sh", "#!/usr/bin/env bash\necho start\n")
    analysis = read(
        root,
        conclusion(
            line_for_tests("avant de pousser", PRESCRIBED),
            "COMMANDE: demarrer | CLAUDE.md | declaree | lancement local | bash scripts/start.sh",
        ),
        failures=(START_FAILED,),
    )

    assert analysis.lecture is not None
    kept = [c.commande for c in analysis.lecture.retenus.commandes]
    assert kept == ["bash scripts/start.sh"]
    (reason,) = reasons_for(analysis, PRESCRIBED)
    assert "demarrer" in reason and "tester" in reason


def test_the_review_tells_the_model_which_usages_it_looks_for(tmp_path: Path) -> None:
    """The question is asked before the answer is confronted."""
    reader = _Reader(("FIN",))
    asyncio.run(
        lire_le_projet(
            analyser(repository(tmp_path)),
            perimetre=Perimetre(),
            provider=reader,
            modele="fake",
            echecs=(START_FAILED,),
        )
    )

    prompt = " ".join(reader.prompts[0].split())
    assert "Ne rends de COMMANDE que pour ces usages" in prompt


# --------------------------------------------------------------------------- #
# 3. The instructions still hold after a run                                   #
# --------------------------------------------------------------------------- #

#: What the model concludes at import, once it has read `CLAUDE.md`.
IMPORT_READING = ("LIRE: CLAUDE.md", conclusion(line_for_tests("avant de pousser", PRESCRIBED)))

#: The prescribed command fails on the project the run built.
PRESCRIBED_FAILS = {PRESCRIBED: execution.Execution(code=1, sortie="1 failed", duree_s=1.0)}


def _pieces(tmp_path: Path, reader: _Reader, runner: _Runner) -> tuple[ServicePieces, str, Path]:
    """The tooling of an imported repository, piece by piece, read by `reader`."""
    projects = ServiceProjets(
        ProjetStore(tmp_path / "store"), reglages=ReglagesProjetsStore(tmp_path / "settings")
    )
    root = repository(Path.home() / "depots" / "depot")
    project_id = str(projects.creer("depot", str(root))["id"])
    pieces = ServicePieces(
        ServiceOutillage(projects, provider=reader),
        projects,
        verificateur=Verificateur(joueur=runner, interprete=FAKE_BASH),
    )
    return pieces, project_id, root


def _review(
    tmp_path: Path, review_reading: Sequence[str], *, rules_after_the_run: str | None = None
) -> tuple[_Runner, Any]:
    """The import reading, then a run, then the review: what was played, and what it proposes."""
    reader = _Reader((*IMPORT_READING, *review_reading))
    runner = _Runner(PRESCRIBED_FAILS)
    pieces, project_id, root = _pieces(tmp_path, reader, runner)
    first = asyncio.run(pieces.prochaine(project_id, []))
    assert first is not None  # the import reading is done, and pinned for this conversation
    if rules_after_the_run is not None:
        write(root, "CLAUDE.md", rules_after_the_run)
    runner.played.clear()
    piece = asyncio.run(pieces.prochaine(project_id, [], revoir=True))
    assert PRESCRIBED in runner.played  # replayed on the built project, and failed
    return runner, piece


def test_after_a_run_a_prescribed_usage_never_falls_back_to_the_deduced_command(
    tmp_path: Path,
) -> None:
    """`local.sh` fails: the tables' bare `pytest`, set aside by the instructions, is not tried."""
    runner, piece = _review(tmp_path, ("LIRE: CLAUDE.md", "FIN"))

    assert DEDUCED not in runner.played
    assert piece is None or DEDUCED not in piece.proposees


def test_after_a_run_the_review_tries_only_what_the_instructions_prescribe(
    tmp_path: Path,
) -> None:
    """The review reads another prescribed form: it is tried, the deduced command is not."""
    other = ".venv/bin/python -m pytest tests/test_depot.py"
    rules = CLAUDE_MD + f"- Le test du dépôt : `{other}`.\n"
    reader_answers = ("LIRE: CLAUDE.md", conclusion(line_for_tests("le test du dépôt", other)))
    reader = _Reader((*IMPORT_READING, *reader_answers))
    runner = _Runner(PRESCRIBED_FAILS)
    pieces, project_id, root = _pieces(tmp_path, reader, runner)
    write(root, "CLAUDE.md", rules)
    assert asyncio.run(pieces.prochaine(project_id, [])) is not None
    runner.played.clear()

    piece = asyncio.run(pieces.prochaine(project_id, [], revoir=True))

    assert other in runner.played
    assert DEDUCED not in runner.played
    assert piece is not None and piece.proposees == (other,)


def test_after_a_run_instructions_changed_on_that_point_let_the_deduction_back(
    tmp_path: Path,
) -> None:
    """The run rewrote `CLAUDE.md`, which no longer says how to test: the tables speak again."""
    runner, piece = _review(
        tmp_path, ("LIRE: CLAUDE.md", "FIN"), rules_after_the_run=CLAUDE_MD_CHANGED
    )

    assert DEDUCED in runner.played
    assert piece is not None and DEDUCED in piece.proposees
