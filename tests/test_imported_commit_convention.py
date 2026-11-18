"""Maestro's commits follow the convention an imported project declares, ticket included (#1679).

On Maestro's own repository imported (`~/Maestro/maestro-1592`, docs/56 §4.5), none of the
18 commits passed its `commit-msg` hook: 15 engine commits read "Maestro : …", and the
agent's 3 commits did not cite `Refs #1592`, one using the `style` type the hook rejects.
Three causes, each held here where it lives:

1. **the reading of an imported project** never recorded the convention its files
   impose. The model now returns a `COMMITS` finding, which must cite a file it read like
   any other finding, and the analysis source of the manifest keeps it
   (`CLE_CONVENTION_DE_COMMIT`), read back by the same reader as a chosen convention;
2. **the manifest of a project that ignores `.maestro/`** never reached its root: it
   stayed in the worktree that the teardown erases, so no run ever read the convention.
   It now travels by its text, and reaches the root once the merge is accepted;
3. **what the writer and the agent are told**: the commit writer sees the run's demand
   and may return the footer the convention requires (`Refs #…`), never one the demand
   does not name; the agent's consigne names the convention, its admitted types and the
   reference rule; and the brief keeps a reference the demand names, since the brief is
   the only place where the run's commits read it.

The model is a double everywhere. What it really understands is measured on the real
stack, where every commit of a run is checked by the project's own hook.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.controltower.chat import MessageChat
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.outillage import ServiceOutillage
from maestro.controltower.pieces import CorrectionModele, ServicePieces
from maestro.controltower.projets import ServiceProjets
from maestro.engine.executor import LocalExecutor
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.orchestrator import Brief
from maestro.orchestrator.schema import Task
from maestro.outillage import (
    CADRE_LECTURE,
    CHEMIN_MANIFESTE,
    VERSION_MANIFESTE,
    analyser,
    lire_le_projet,
    outillage_du_projet,
)
from maestro.outillage.contexte import TITRE_CONVENTION_DE_COMMIT
from maestro.outillage.modele import (
    CLE_CONVENTION_DE_COMMIT,
    CONVENTION_MAX,
    SOURCE_ANALYSE,
    ConventionDeCommit,
)
from maestro.outillage.questionnaire import convention_de_la_source
from maestro.outillage.verification import Verificateur
from maestro.projects import Project, ProjectStore
from maestro.projects.apply import ignored_by_project
from maestro.projects.commits import (
    FOOTER_MAX,
    SUBJECT_MAX,
    CommitDrafter,
    message_prompt,
    readable_message,
)
from maestro.projects.model import Perimeter
from maestro.projects.settings import ProjectSettingsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import branche_de_tache
from maestro.sandbox import verification as execution
from maestro.telemetry import RunJournal

GIT = shutil.which("git")
with_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

ROOT = Path(__file__).resolve().parent.parent
BRIEF_PLAYBOOK = ROOT / "maestro" / "orchestrator" / "playbook_brief.md"

#: The convention Maestro's own `CLAUDE.md` declares, in the words a reading returns.
CONVENTION = (
    "Conventional Commits (feat, fix, chore, docs, refactor, test, ci, build, perf), "
    "et chaque message cite le ticket : Refs #<iid> ou Closes #<iid>"
)

#: The project's instructions, as an imported repository writes them.
CLAUDE_MD = (
    "# Règles\n\n"
    "Commits en Conventional Commits + `Refs #<iid>` (intermédiaires) / `Closes #<iid>`.\n"
)

#: A fake interpreter: the doubled player never runs it.
FAKE_BASH = ("bash", "-c")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home — under Windows, `tmp_path` lives in `AppData` (#221)."""
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cuts the workstation's global Git configuration (#333)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Maestro Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@maestro.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Maestro Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@maestro.invalid")


# --------------------------------------------------------------------------- #
# Doubles and builders                                                         #
# --------------------------------------------------------------------------- #


class _Reader(ModelProvider):
    """The model reading a project: replays its answers in order, and keeps the prompts.

    `answers` may also be a function of the prompt, for a project read more than once.
    """

    name = "reader-double"

    def __init__(self, answers: Sequence[str] | Callable[[str], str]) -> None:
        self._answers = answers if callable(answers) else list(answers)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        if system_prompt != CADRE_LECTURE:
            raise RuntimeError("only the reading is expected from this double")
        self.prompts.append(prompt)
        if callable(self._answers):
            return self._answers(prompt)
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


def _reads_claude_md_then_names_its_convention(prompt: str) -> str:
    """A model that reads `CLAUDE.md` first, every time it reads the project."""
    if "Tes lectures précédentes" in prompt:
        return f"COMMITS: CLAUDE.md | {CONVENTION}\nFIN"
    return "LIRE: CLAUDE.md"


class _Writer(ModelProvider):
    """The model writing Maestro's commit messages: one answer per gesture, prompts kept."""

    name = "writer-double"

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.prompts.append(prompt)
        if self.answer is not None:
            return self.answer
        if "<commit>fusionne" in prompt:
            return "chore(outillage): fusionne les instructions des agents"
        return "chore(outillage): ajoute les instructions des agents"


class _Player:
    """A doubled player: every command succeeds, and nothing runs on the workstation."""

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)


class _Agent(ModelProvider):
    """The developer of a run: writes the deliverable its task names, and keeps its prompts."""

    name = "agent-double"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("a tooled role goes through run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self.prompts.append(prompt)
        (Path(workspace) / "note.md").write_text("une note\n", encoding="utf-8")
        return "Fait."


class _Approves:
    """The human validator of a run: approves every write."""

    async def __call__(self, demande: DemandeValidation) -> bool:
        return True


def _write(root: Path, path: str, content: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(arguments)} : {result.stderr}"
    return result.stdout


def _commit_all(cwd: Path, message: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.email=t@maestro", "-c", "user.name=T", "commit", "-q", "-m", message)


def _imported_repository(home: Path, *, ignores_maestro: bool) -> Path:
    """An existing Node repository whose `CLAUDE.md` imposes the convention, committed."""
    root = home / "sites" / "depensio"
    _write(root, "package.json", json.dumps({"name": "depensio", "scripts": {"test": "jest"}}))
    _write(root, "src/index.js", "console.log('depensio')\n")
    _write(root, "CLAUDE.md", CLAUDE_MD)
    if ignores_maestro:
        _write(root, ".gitignore", "node_modules/\n.maestro/\n")
    _git(root, "init", "-q")
    _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    _commit_all(root, "chore: socle\n\nRefs #1")
    return root


def _read(root: Path, reader: _Reader):
    """The reading played to its end, as the Control Tower plays it."""
    return asyncio.run(
        lire_le_projet(
            analyser(root), perimetre=Perimeter(), provider=reader, modele="modele-du-test"
        )
    )


def _pieces(projets: ServiceProjets, reader: _Reader, writer: _Writer) -> ServicePieces:
    service = ServicePieces(
        ServiceOutillage(projets, provider=reader),
        projets,
        verificateur=Verificateur(joueur=_Player(), interprete=FAKE_BASH),
        correction=CorrectionModele(reader),
    )
    service._redacteur = CommitDrafter(writer, model="modele-du-test")
    return service


def _manifest(root: Path) -> dict[str, Any]:
    return json.loads((root / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


#: The reading of the imported repository: it reads `CLAUDE.md`, then names its convention.
READING = ("LIRE: CLAUDE.md", f"COMMITS: CLAUDE.md | {CONVENTION}\nFIN")


# --------------------------------------------------------------------------- #
# 1. The reading records the convention a read file imposes                    #
# --------------------------------------------------------------------------- #


def test_the_reading_records_the_commit_convention_a_read_file_imposes(tmp_path: Path) -> None:
    root = tmp_path / "depensio"
    _write(root, "CLAUDE.md", CLAUDE_MD)

    analysis = _read(root, _Reader(READING))

    assert analysis.constats.convention_de_commit == ConventionDeCommit(
        texte=CONVENTION, chemin="CLAUDE.md"
    )
    assert analysis.constats.to_dict()["commit_convention"] == {
        "text": CONVENTION,
        "path": "CLAUDE.md",
    }
    source = analysis.source_manifeste()
    assert source["type"] == SOURCE_ANALYSE
    assert source[CLE_CONVENTION_DE_COMMIT] == CONVENTION
    # The key is stored in the manifest, so it is born in English (docs/55 §3).
    assert CLE_CONVENTION_DE_COMMIT == "commit_convention"


def test_the_reading_frame_asks_for_the_convention_a_file_imposes() -> None:
    assert "COMMITS: <fichier lu> |" in CADRE_LECTURE


def test_a_convention_citing_an_unread_file_is_set_aside(tmp_path: Path) -> None:
    """The model proposes, the disk decides: without the file read, nothing enters."""
    root = tmp_path / "depensio"
    _write(root, "CLAUDE.md", CLAUDE_MD)

    analysis = _read(root, _Reader((f"COMMITS: CLAUDE.md | {CONVENTION}\nFIN",)))

    assert analysis.constats.convention_de_commit is None
    assert CLE_CONVENTION_DE_COMMIT not in analysis.source_manifeste()
    assert analysis.lecture is not None
    (aside,) = analysis.lecture.ecartes
    assert "CLAUDE.md" in aside.raison and "pas lu" in aside.raison


def test_a_paragraph_an_empty_rule_or_a_second_convention_is_set_aside(tmp_path: Path) -> None:
    root = tmp_path / "depensio"
    _write(root, "CLAUDE.md", CLAUDE_MD)
    paragraph = "x" * (CONVENTION_MAX + 1)
    answer = "\n".join(
        (
            f"COMMITS: CLAUDE.md | {paragraph}",
            "COMMITS: CLAUDE.md |",
            f"COMMITS: CLAUDE.md | {CONVENTION}",
            "COMMITS: CLAUDE.md | gitmoji",
            "FIN",
        )
    )

    analysis = _read(root, _Reader(("LIRE: CLAUDE.md", answer)))

    assert analysis.constats.convention_de_commit == ConventionDeCommit(CONVENTION, "CLAUDE.md")
    assert analysis.lecture is not None
    reasons = [aside.raison for aside in analysis.lecture.ecartes]
    assert len(reasons) == 3
    assert "paragraphe" in reasons[0]
    assert "vide" in reasons[1]
    assert "déjà" in reasons[2]


# --------------------------------------------------------------------------- #
# 2. One reader for both sources, and the agent is told                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"type": "analyse", CLE_CONVENTION_DE_COMMIT: CONVENTION}, CONVENTION),
        ({"type": "analyse", CLE_CONVENTION_DE_COMMIT: "  "}, ""),
        ({"type": "analyse", CLE_CONVENTION_DE_COMMIT: "aucune"}, ""),
        ({"type": "analyse", CLE_CONVENTION_DE_COMMIT: 12}, ""),
        ({"type": "analyse"}, ""),
        (
            {"type": "choix", "choix": [{"cle": "conventions", "valeur": "Conventional Commits"}]},
            "Conventional Commits",
        ),
        ({"type": "inconnu", CLE_CONVENTION_DE_COMMIT: CONVENTION}, ""),
    ],
)
def test_the_convention_is_read_from_either_source(source: dict[str, Any], expected: str) -> None:
    assert convention_de_la_source(source) == expected


def test_an_overlong_convention_written_by_hand_is_bounded() -> None:
    hand_written = {"type": "analyse", CLE_CONVENTION_DE_COMMIT: "y" * (CONVENTION_MAX * 2)}

    assert 0 < len(convention_de_la_source(hand_written)) <= CONVENTION_MAX


def test_the_agent_is_told_the_imported_convention_and_the_reference_rule(tmp_path: Path) -> None:
    root = tmp_path / "depensio"
    _write(root, "AGENTS.md", "# Dépensio\n")
    _write(
        root,
        CHEMIN_MANIFESTE,
        json.dumps(
            {
                "manifeste": VERSION_MANIFESTE,
                "source": {"type": "analyse", CLE_CONVENTION_DE_COMMIT: CONVENTION},
                "entrees": [{"chemin": "AGENTS.md", "role": "instructions"}],
            }
        ),
    )
    project = Project(id="prj-0000dead", name="Dépensio", root=root.as_posix())

    tooling = outillage_du_projet(project)
    consigne = tooling.consigne()

    assert tooling.convention_de_commit == CONVENTION
    assert TITRE_CONVENTION_DE_COMMIT in consigne
    section = consigne.split(TITRE_CONVENTION_DE_COMMIT, 1)[1]
    assert CONVENTION in section
    assert "types" in section and "ticket" in section and "invente" in section


def test_without_a_convention_the_consigne_says_nothing_of_commits(tmp_path: Path) -> None:
    root = tmp_path / "depensio"
    _write(root, "AGENTS.md", "# Dépensio\n")
    _write(
        root,
        CHEMIN_MANIFESTE,
        json.dumps(
            {
                "manifeste": VERSION_MANIFESTE,
                "source": {"type": "analyse"},
                "entrees": [{"chemin": "AGENTS.md", "role": "instructions"}],
            }
        ),
    )
    project = Project(id="prj-0000dead", name="Dépensio", root=root.as_posix())

    assert TITRE_CONVENTION_DE_COMMIT not in outillage_du_projet(project).consigne()


# --------------------------------------------------------------------------- #
# 3. The writer cites the reference the demand names, and nothing else          #
# --------------------------------------------------------------------------- #


def test_the_writer_keeps_a_footer_and_drops_what_is_not_one() -> None:
    assert readable_message("fix(fil): corrige le refus\n\nRefs #1592") == (
        "fix(fil): corrige le refus\n\nRefs #1592"
    )
    # Fences, quotes and blank lines are what a model puts around an answer.
    assert readable_message("```\nfix: a\n\nRefs #1\n```") == "fix: a\n\nRefs #1"
    assert readable_message("fix: a") == "fix: a"
    # More lines than a footer, or a paragraph: the subject stays alone.
    too_many = "\n".join(["fix: a", *(f"Refs #{n}" for n in range(FOOTER_MAX + 1))])
    assert readable_message(too_many) == "fix: a"
    assert readable_message("fix: a\n\n" + "z" * (SUBJECT_MAX + 1)) == "fix: a"
    # No subject, no message: Maestro keeps its own.
    assert readable_message("") == ""
    assert readable_message("z" * (SUBJECT_MAX + 1)) == ""


def test_the_writer_sees_the_demand_and_is_told_never_to_invent_a_reference() -> None:
    prompt = message_prompt(CONVENTION, "fusionne la branche", request="Corriger #1592")

    assert "<demande>Corriger #1592</demande>" in prompt
    assert "<demande>" not in message_prompt(CONVENTION, "fusionne la branche")

    from maestro.projects.commits import SYSTEM

    assert "N'invente jamais" in SYSTEM and "pied" in SYSTEM


def test_the_writer_returns_the_subject_and_the_footer_it_was_given() -> None:
    writer = _Writer("fix(fil): corrige le refus\n\nRefs #1592")

    message = asyncio.run(
        CommitDrafter(writer, model="m").message(
            convention=CONVENTION, change="fusionne", request="Objectif : #1592"
        )
    )

    assert message == "fix(fil): corrige le refus\n\nRefs #1592"
    (prompt,) = writer.prompts
    assert "<demande>Objectif : #1592</demande>" in prompt


def test_the_brief_keeps_a_reference_the_demand_names() -> None:
    """The brief is the only place where the run's commits read the reference (#1402)."""
    text = BRIEF_PLAYBOOK.read_text(encoding="utf-8")

    assert "## Une référence que l'objectif nomme se garde" in text
    section = text.split("## Une référence que l'objectif nomme se garde", 1)[1].split("\n## ")[0]
    assert "telle qu'elle est écrite" in section and "N'en ajoute jamais" in section


# --------------------------------------------------------------------------- #
# 4. A run's merge cites the ticket its brief names, in the footer              #
# --------------------------------------------------------------------------- #


@with_git
@pytest.mark.usefixtures("_isolated_git")
def test_a_run_merge_follows_the_imported_convention_and_cites_the_ticket(
    tmp_path: Path, _home: Path
) -> None:
    root = _imported_repository(_home, ignores_maestro=False)
    _write(root, "AGENTS.md", "# Dépensio\n")
    _write(
        root,
        CHEMIN_MANIFESTE,
        json.dumps(
            {
                "manifeste": VERSION_MANIFESTE,
                "source": {"type": "analyse", CLE_CONVENTION_DE_COMMIT: CONVENTION},
                "entrees": [{"chemin": "AGENTS.md", "role": "instructions"}],
            }
        ),
    )
    _commit_all(root, "chore(outillage): outillage\n\nRefs #1")
    store = ProjectStore(tmp_path / "depot")
    project = store.create("Dépensio", root)
    agent = _Agent()
    writer = _Writer("fix(fil): corrige le refus d'un dossier\n\nRefs #1592")
    executor = LocalExecutor(
        agent,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(agent, DEVELOPER_PROFILE)},
        projets=store,
        guardrails=Guardrails(validateur=_Approves()),
    )
    executor._redacteur = CommitDrafter(writer, model="modele-du-test")
    journal = RunJournal(run_id="run-1679")
    executor.retient_le_brief(
        journal.run_id,
        Brief(
            goal="Corriger le ticket #1592 : le fil n'annonce plus une proposition absente",
            in_scope=(),
            out_of_scope=(),
            acceptance_criteria=("Le test du refus passe",),
        ),
    )
    task = Task(
        id="t1",
        title="Corriger le refus",
        description="Écrire la note",
        required_competencies=("backend",),
        output_format="markdown",
        project_id=project.id,
    )

    result = asyncio.run(executor.execute(task, [], journal))

    assert result.ok
    merge = _git(root, "log", "--merges", "-1", "--format=%B").strip()
    paragraphs = merge.split("\n\n")
    assert paragraphs[0] == "fix(fil): corrige le refus d'un dossier"
    assert paragraphs[1].startswith(f"Maestro : application de {branche_de_tache('t1')}")
    # The footer comes last: that is where the convention and Git read it.
    assert paragraphs[-1] == "Refs #1592"
    (prompt,) = writer.prompts
    assert f"<convention>{CONVENTION}</convention>" in prompt
    assert "<demande>" in prompt and "#1592" in prompt.split("<demande>", 1)[1]
    # The agent was told the same convention, and the same brief.
    (told,) = agent.prompts
    assert TITRE_CONVENTION_DE_COMMIT in told and CONVENTION in told and "#1592" in told


# --------------------------------------------------------------------------- #
# 5. The manifest of a project that ignores `.maestro/` reaches its root        #
# --------------------------------------------------------------------------- #


@with_git
@pytest.mark.usefixtures("_isolated_git")
def test_git_says_whether_the_project_ignores_its_manifest(_home: Path) -> None:
    ignoring = _imported_repository(_home, ignores_maestro=True)

    assert ignored_by_project(ignoring, CHEMIN_MANIFESTE)

    tracking = _imported_repository(_home / "autre", ignores_maestro=False)
    assert not ignored_by_project(tracking, CHEMIN_MANIFESTE)
    # Not a repository: Git cannot answer, and nothing more is done than before.
    assert not ignored_by_project(_home, CHEMIN_MANIFESTE)


@with_git
@pytest.mark.usefixtures("_isolated_git")
def test_an_imported_project_that_ignores_maestro_keeps_its_manifest_and_convention(
    tmp_path: Path, _home: Path
) -> None:
    """The defect of `maestro-1592`: seven pieces written, and no manifest at the root.

    Maestro's repository ignores `.maestro/`. The manifest the piece's worktree carried
    was erased with it, so no run read the convention. It now reaches the root once the
    merge is accepted, keeps the pieces written before, and leaves the tree clean.
    """
    root = _imported_repository(_home, ignores_maestro=True)
    projets = ServiceProjets(
        ProjectStore(tmp_path / "depot"), reglages=ProjectSettingsStore(tmp_path / "reglages")
    )
    project_id = str(projets.creer("Dépensio", str(root))["id"])
    writer = _Writer()
    service = _pieces(projets, _Reader(_reads_claude_md_then_names_its_convention), writer)

    first = asyncio.run(service.prochaine(project_id, []))
    assert first is not None and first.chemin == "AGENTS.md" and first.regime == "branche"
    written = asyncio.run(service.ecrire(first))

    assert written.ecrite, written
    manifest = _manifest(root)
    assert [entry["chemin"] for entry in manifest["entrees"]] == ["AGENTS.md"]
    assert manifest["source"][CLE_CONVENTION_DE_COMMIT] == CONVENTION
    project = projets.entite(project_id)
    assert outillage_du_projet(project).convention_de_commit == CONVENTION
    # Ignored, it changes nothing Git sees: the person's tree stays clean.
    assert _git(root, "status", "--porcelain").strip() == ""
    # The merge, then the piece it brings (its second parent).
    assert _git(root, "log", "-1", "--format=%s").strip() == (
        "chore(outillage): fusionne les instructions des agents"
    )
    assert _git(root, "log", "-1", "--format=%s", "HEAD^2").strip() == (
        "chore(outillage): ajoute les instructions des agents"
    )
    assert all(f"<convention>{CONVENTION}</convention>" in p for p in writer.prompts)

    said = {"agent": NOM_ORCHESTRATION, "auteur": NOM_ORCHESTRATION}
    thread = [
        MessageChat(**said, contenu="Une pièce.", piece=first),
        MessageChat(**said, contenu="Écrit.", piece_ecrite=written),
    ]
    second = asyncio.run(service.prochaine(project_id, thread))
    assert second is not None and second.chemin != "AGENTS.md"
    assert asyncio.run(service.ecrire(second)).ecrite

    # The second piece is added to the entries already declared, not written alone.
    assert [entry["chemin"] for entry in _manifest(root)["entrees"]] == [
        "AGENTS.md",
        second.chemin,
    ]
    assert _git(root, "status", "--porcelain").strip() == ""


@with_git
@pytest.mark.usefixtures("_isolated_git")
def test_a_whole_generation_on_a_project_that_ignores_maestro_keeps_its_manifest(
    tmp_path: Path, _home: Path
) -> None:
    """The same defect on `…/outillage/generation`, which writes the whole tooling at once (#1686).

    The generation's worktree carried the only manifest, and the teardown erased it with
    the worktree. It now reaches the root once the merge is accepted, the tree stays clean,
    and a second generation finds what the first one wrote instead of starting over.
    """
    root = _imported_repository(_home, ignores_maestro=True)
    projets = ServiceProjets(
        ProjectStore(tmp_path / "depot"), reglages=ProjectSettingsStore(tmp_path / "reglages")
    )
    project_id = str(projets.creer("Dépensio", str(root))["id"])
    service = ServiceOutillage(
        projets,
        provider=_Reader(_reads_claude_md_then_names_its_convention),
        validateur=_Approves(),
    )

    response = asyncio.run(service.generer(project_id))

    assert response["regime"] == "branche"
    assert response["application"]["statut"] == "approuvee", response["application"]
    assert "AGENTS.md" in response["rapport"]["ecrits"]
    manifest = _manifest(root)
    assert "AGENTS.md" in [entry["chemin"] for entry in manifest["entrees"]]
    assert manifest["source"][CLE_CONVENTION_DE_COMMIT] == CONVENTION
    assert outillage_du_projet(projets.entite(project_id)).convention_de_commit == CONVENTION
    # Ignored, it changes nothing Git sees: the person's tree stays clean.
    assert _git(root, "status", "--porcelain").strip() == ""
    # `main` carries the merge, whose second parent is the generation's branch.
    assert _git(root, "rev-parse", "HEAD^2").strip() == (
        _git(root, "rev-parse", response["branche"]).strip()
    )
    assert _git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"

    # The root manifest enters the next worktree: nothing is rewritten, nothing to merge.
    again = asyncio.run(service.generer(project_id))
    assert again["rapport"]["ecrits"] == [], again["rapport"]["ecritures"]
    assert again["application"]["statut"] == "sans_objet"
    assert _manifest(root)["entrees"] == manifest["entrees"]
    assert _git(root, "status", "--porcelain").strip() == ""
