"""The tooling of an imported project takes its commands from the instructions it writes (#1678).

Measured by #1587 on the Maestro repository, imported into the chat: `CLAUDE.md` says not to
replay the whole suite and to run Python from the repository's venv, yet the tooling wrote
`pytest`, `ruff format .` and `mypy .`, read from `pyproject.toml`. The agent then ran the whole
suite for about 1 h 23. `mettre-en-route` wrote `bash scripts/setup.sh` twice, and the bridge
appended to `CLAUDE.md` pushed that file over the token budget its own tests guard.

What this suite keeps, from the reading to the written files:

1. **a command the instructions prescribe wins over the configuration** — it comes from an
   instructions file the model has read, so it is `prescribed`, and it is ranked first for its
   usage, even when the tables found the very same text elsewhere;
2. **a usage the instructions exclude loses its commands** — `ruff format .` is not written when
   the instructions say the repository does not format;
3. **only an instructions file prescribes or excludes** — a configuration file, or a file the
   model has not read, does not;
4. **a skill writes each command once**;
5. **a piece that would write into a file the project guards says so** before it is written;
6. **an instructions file is read in full** — reading a truncated file again serves what follows.

No network, no model: the model that reads is a fake provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from maestro.outillage import analyser, lire_le_projet, rediger
from maestro.outillage.clients import Client
from maestro.outillage.exploration import CADRE_LECTURE
from maestro.outillage.modele import ORIGINE_PRESCRITE, Analyse, Bornes
from maestro.outillage.recommandation import recommander
from maestro.projects.model import Perimeter
from maestro.providers.base import ModelProvider

#: The rules of a repository that writes its instructions in `CLAUDE.md`, like Maestro's.
CLAUDE_MD = """\
# Règles du dépôt

- Le budget de ce fichier est de 400 tokens, gardé par `tests/test_budget.py`.
- Python : toujours `.venv/bin/python`, jamais le python du système.
- Ne rejoue pas la suite complète : `bash scripts/ci/local.sh` joue le périmètre du diff.
- Seul `ruff check` est exigé : le dépôt ne formate pas.
"""


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `Path.home()` under `tmp_path` — on Windows, `tmp_path` lives under `AppData` (#221)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class _Reader(ModelProvider):
    """The model that reads the project: it replays its answers, one per turn."""

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
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


def write(root: Path, path: str, content: str) -> Path:
    """Puts a file under `root`, parent folders included."""
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def repository(root: Path, claude_md: str = CLAUDE_MD) -> Path:
    """A Python repository whose configuration and instructions disagree, as Maestro's did."""
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "depot"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n\n'
        "[tool.ruff]\nline-length = 100\n\n[tool.ruff.format]\n\n[tool.mypy]\nstrict = true\n",
    )
    write(root, "depot/__init__.py", "")
    write(root, "tests/test_budget.py", "def test_budget():\n    assert True\n")
    write(root, "scripts/setup.sh", "#!/usr/bin/env bash\necho setup\n")
    write(root, "scripts/ci/local.sh", "#!/usr/bin/env bash\necho local\n")
    write(root, "CONTRIBUTING.md", "Mise en route : `bash scripts/setup.sh`.\n")
    write(root, "CLAUDE.md", claude_md)
    return root


#: What the model concludes once it has read `CLAUDE.md` and `CONTRIBUTING.md`.
CONCLUSION = "\n".join(
    (
        "COMMANDE: installer | CONTRIBUTING.md | declaree | mise en route | bash scripts/setup.sh",
        "COMMANDE: tester | CLAUDE.md | declaree | le périmètre du diff, pas la suite complète"
        " | bash scripts/ci/local.sh",
        "COMMANDE: lint | CLAUDE.md | declaree | seul ruff check est exigé | ruff check .",
        "COMMANDE: types | CLAUDE.md | declaree | le venv | .venv/bin/python -m mypy depot",
        "EXCLUDED: formater | CLAUDE.md | le dépôt ne formate pas",
        "GUARDED: CLAUDE.md | CLAUDE.md | budget de 400 tokens gardé par tests/test_budget.py",
        "FIN",
    )
)


def read(analysis: Analyse, reader: _Reader) -> Analyse:
    """The model's reading played to the end, as the Control Tower plays it."""
    return asyncio.run(
        lire_le_projet(analysis, perimetre=Perimeter(), provider=reader, modele="fake")
    )


def read_repository(root: Path, conclusion: str = CONCLUSION) -> Analyse:
    """The repository analysed, then read by a model that opens its instructions."""
    reader = _Reader(("LIRE: CLAUDE.md\nLIRE: CONTRIBUTING.md", conclusion))
    return read(analyser(repository(root)), reader)


def written(analysis: Analyse, path: str) -> str:
    """The text the drafting writes at `path` for this analysis."""
    (file,) = [
        f for f in rediger(analysis.constats, analysis.recommandation) if f.chemin == path
    ]
    return file.contenu


# --------------------------------------------------------------------------- #
# 1. A command the instructions prescribe wins over the configuration          #
# --------------------------------------------------------------------------- #


def test_the_tables_alone_take_the_configuration_commands(tmp_path: Path) -> None:
    """The defect's starting point: without a reading, the tests are `pytest`, from pyproject."""
    tester = analyser(repository(tmp_path)).constats.commande_de("tester")

    assert tester is not None
    assert (tester.commande, tester.chemin) == ("pytest", "pyproject.toml")


def test_the_command_the_instructions_prescribe_beats_the_configuration(tmp_path: Path) -> None:
    """`CLAUDE.md` says to run the scope of the diff: that is what the tooling writes."""
    analysis = read_repository(tmp_path)

    tester = analysis.constats.commande_de("tester")
    assert tester is not None
    assert (tester.commande, tester.chemin, tester.origine) == (
        "bash scripts/ci/local.sh",
        "CLAUDE.md",
        ORIGINE_PRESCRITE,
    )
    types = analysis.constats.commande_de("types")
    assert types is not None and types.commande == ".venv/bin/python -m mypy depot"
    skill = written(analysis, ".agents/skills/lancer-les-tests/SKILL.md")
    assert "bash scripts/ci/local.sh" in skill
    assert "\npytest\n" not in skill
    agents = written(analysis, "AGENTS.md")
    assert "`bash scripts/ci/local.sh` — prescrite par `CLAUDE.md`" in agents


def test_a_command_the_instructions_repeat_is_kept_as_theirs(tmp_path: Path) -> None:
    """`ruff check .` is read in pyproject **and** prescribed: it stays once, as prescribed."""
    analysis = read_repository(tmp_path)

    lints = [c for c in analysis.constats.commandes if c.usage == "lint"]
    assert [(c.commande, c.chemin, c.origine) for c in lints] == [
        ("ruff check .", "CLAUDE.md", ORIGINE_PRESCRITE)
    ]
    assert analysis.lecture is not None
    assert not [e for e in analysis.lecture.ecartes if "ruff check" in e.ligne]


# --------------------------------------------------------------------------- #
# 2. A usage the instructions exclude loses its commands                       #
# --------------------------------------------------------------------------- #


def test_a_usage_the_instructions_exclude_loses_its_commands(tmp_path: Path) -> None:
    """`ruff format .` came from `[tool.ruff.format]`; the instructions say no formatting."""
    root = tmp_path / "repo"
    tables = analyser(repository(root)).constats.commande_de("formater")
    assert tables is not None and tables.commande == "ruff format ."

    analysis = read_repository(tmp_path / "read")

    assert analysis.constats.commande_de("formater") is None
    (excluded,) = analysis.constats.usages_exclus
    assert (excluded.nom, excluded.chemin) == ("formater", "CLAUDE.md")
    assert "ruff format" not in written(analysis, ".agents/skills/verifier-le-style/SKILL.md")
    assert "formatage" not in written(analysis, "AGENTS.md")
    assert excluded.to_dict()["chemin"] == "CLAUDE.md"
    assert analysis.constats.to_dict()["excluded_usages"][0]["nom"] == "formater"


# --------------------------------------------------------------------------- #
# 3. Only an instructions file prescribes or excludes                          #
# --------------------------------------------------------------------------- #


def test_only_an_instructions_file_the_model_read_prescribes_or_excludes(tmp_path: Path) -> None:
    """A configuration file declares, it does not prescribe; an unread file proves nothing."""
    conclusion = "\n".join(
        (
            "COMMANDE: tester | CONTRIBUTING.md | declaree | contribution | pytest -x",
            "EXCLUDED: types | CONTRIBUTING.md | pas de types",
            "EXCLUDED: formater | README.md | jamais lu",
            "GUARDED: CLAUDE.md | README.md | jamais lu",
            "EXCLUDED: deployer | CLAUDE.md | usage hors liste",
            "FIN",
        )
    )

    analysis = read_repository(tmp_path, conclusion)

    tester = [c for c in analysis.constats.commandes if c.commande == "pytest -x"]
    assert [c.origine for c in tester] == ["declaree"]
    assert analysis.constats.commande_de("types") is not None
    assert analysis.constats.commande_de("formater") is not None
    assert analysis.constats.usages_exclus == ()
    assert analysis.constats.fichiers_gardes == ()
    assert analysis.lecture is not None
    reasons = [e.raison for e in analysis.lecture.ecartes]
    assert any("CONTRIBUTING.md" in r and "instructions" in r for r in reasons)
    assert sum("que la lecture n'a pas lu" in r for r in reasons) == 2
    assert any("usage inconnu" in r for r in reasons)


def test_a_usage_both_prescribed_and_excluded_keeps_its_prescription(tmp_path: Path) -> None:
    """Two answers that contradict each other: the command, read in the file, is kept."""
    conclusion = "\n".join(
        (
            "COMMANDE: lint | CLAUDE.md | declaree | seul ruff check | ruff check .",
            "EXCLUDED: lint | CLAUDE.md | pas de lint",
            "FIN",
        )
    )

    analysis = read_repository(tmp_path, conclusion)

    lint = analysis.constats.commande_de("lint")
    assert lint is not None and lint.origine == ORIGINE_PRESCRITE
    assert analysis.constats.usages_exclus == ()
    assert analysis.lecture is not None
    assert any("prescrit" in e.raison for e in analysis.lecture.ecartes)


# --------------------------------------------------------------------------- #
# 4. A skill writes each command once                                          #
# --------------------------------------------------------------------------- #


def test_the_setup_skill_writes_its_command_once(tmp_path: Path) -> None:
    """`scripts/setup.sh` is both the project's script and the command `CONTRIBUTING.md` names."""
    analysis = read_repository(tmp_path)

    skill = written(analysis, ".agents/skills/mettre-en-route/SKILL.md")

    assert skill.count("bash scripts/setup.sh\n") == 1
    (entry,) = [e for e in analysis.recommandation.entrees if e.nom == "mettre-en-route"]
    assert entry.commandes.count("bash scripts/setup.sh") == 1


# --------------------------------------------------------------------------- #
# 5. A piece that writes into a guarded file says so before it is written      #
# --------------------------------------------------------------------------- #


def test_a_bridge_into_a_file_the_project_guards_says_so(tmp_path: Path) -> None:
    """The bridge `@AGENTS.md` appended to `CLAUDE.md` broke its budget test on #1587."""
    analysis = read_repository(tmp_path)
    (guarded,) = analysis.constats.fichiers_gardes
    assert (guarded.nom, guarded.chemin) == ("CLAUDE.md", "CLAUDE.md")

    recommendation = recommander(analysis.constats, (Client(cle="claude", libelle="Claude Code"),))

    (bridge,) = [e for e in recommendation.entrees if e.type == "pont"]
    assert bridge.etat == "a-completer"
    assert bridge.raison.startswith("⚠")
    assert "tests/test_budget.py" in bridge.raison
    assert "faire rougir" in bridge.raison


def test_a_piece_into_an_unguarded_file_keeps_its_reason(tmp_path: Path) -> None:
    """No guard read, no warning: the bridge says what it did before."""
    conclusion = CONCLUSION.replace(
        "GUARDED: CLAUDE.md | CLAUDE.md | budget de 400 tokens gardé par tests/test_budget.py\n", ""
    )
    analysis = read_repository(tmp_path, conclusion)

    recommendation = recommander(analysis.constats, (Client(cle="claude", libelle="Claude Code"),))

    (bridge,) = [e for e in recommendation.entrees if e.type == "pont"]
    assert not bridge.raison.startswith("⚠")


def test_a_guard_on_a_file_the_project_does_not_have_is_refused(tmp_path: Path) -> None:
    """A guard on a file that is not there guards nothing Maestro would write into."""
    conclusion = "GUARDED: GEMINI.md | CLAUDE.md | budget\nFIN"

    analysis = read_repository(tmp_path, conclusion)

    assert analysis.constats.fichiers_gardes == ()
    assert analysis.lecture is not None
    assert any("GEMINI.md" in e.raison for e in analysis.lecture.ecartes)


# --------------------------------------------------------------------------- #
# 6. An instructions file is read in full                                      #
# --------------------------------------------------------------------------- #


def test_reading_a_truncated_file_again_serves_what_follows(tmp_path: Path) -> None:
    """Maestro's `CLAUDE.md` names its venv past the first 12 000 bytes the model was served."""
    rule = "- Python : toujours `.venv/bin/python`."
    long_rules = "# Règles\n\n" + "".join(f"- règle {n} du dépôt.\n" for n in range(400)) + rule
    root = repository(tmp_path, claude_md=long_rules + "\n")
    bounds = Bornes(octets_par_lecture_max=4_000)
    reader = _Reader(("LIRE: CLAUDE.md", "LIRE: CLAUDE.md\nLIRE: CLAUDE.md", "FIN"))

    analysis = read(analyser(root, bornes=bounds), reader)

    assert rule not in reader.prompts[1]
    assert rule in reader.prompts[2]
    assert "relis-le pour la suite" in reader.prompts[1]
    # The root listing, then three slices: each slice is a reading, the file is read once.
    assert f"Lectures restantes : {Bornes().lectures_max - 4}" in reader.prompts[2]
    assert analysis.lecture is not None
    assert analysis.lecture.lus.count("CLAUDE.md") == 1


def test_a_file_served_to_its_end_is_not_served_again(tmp_path: Path) -> None:
    """Once the end is served, a new request says so instead of spending a reading."""
    root = repository(tmp_path)
    reader = _Reader(("LIRE: CLAUDE.md", "LIRE: CLAUDE.md", "FIN"))

    analysis = read(analyser(root), reader)

    assert "déjà servi en entier" in reader.prompts[2]
    assert analysis.lecture is not None
    assert analysis.lecture.lus.count("CLAUDE.md") == 1


def test_the_frame_tells_the_model_that_the_instructions_prevail() -> None:
    """The model is told what to render when the instructions disagree with the indices."""
    assert "EXCLUDED:" in CADRE_LECTURE
    assert "GUARDED:" in CADRE_LECTURE
    assert "l'emportent sur les indices" in CADRE_LECTURE
    # A command drawn from a script the instructions designate is still theirs: cited to the
    # script, it would rank behind the configuration's bare `mypy .` (#1678).
    assert "vaut pour chaque usage qu'il couvre" in " ".join(CADRE_LECTURE.split())
