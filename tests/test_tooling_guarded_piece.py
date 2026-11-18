"""A piece that writes into a file the project guards plays what guards it first (#1716).

Measured by #1688 on the Maestro repository, imported into the chat: the bridge `@AGENTS.md`
appended to `CLAUDE.md` warned that it *could* turn `tests/test_audit_commandes.py` red,
without playing it. The person accepted, as the protocol asks, and the test went red —
19 941 tokens for a budget of 19 940. The agent then blamed `main`, which was green.

What this suite keeps:

1. **the reading keeps the command that checks a guarded file** — the fourth field of
   `GUARDED`, read by the model in the project's instructions, last so that it may hold a pipe;
2. **the guard is played on a copy of the project that carries the piece**, before the card
   shows, and once *without* the piece first in the same copy: the verdict says whether the
   piece is what turns the test red, which is what the run of #1688 could not tell;
3. **the card says the verdict** — it holds, it turns the test red (with the output, and why
   Maestro has no writing that holds), or it could not be played and why;
4. **the guard verdict judges a copy, not a command the tooling writes** — it is neither
   recorded in the manifest nor taken as the verdict of the same command elsewhere.

No network, no model: the model that reads is a fake provider, and the commands are played by
a fake player, except one test that plays a real bash on a real copy.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from maestro.controltower.chat import MessageChat, PieceProposee
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.outillage import ServiceOutillage
from maestro.controltower.pieces import PieceChangee, ServicePieces
from maestro.controltower.projets import ServiceProjets
from maestro.outillage import analyser, lire_le_projet
from maestro.outillage.clients import Client
from maestro.outillage.detection import CHEMIN_MANIFESTE
from maestro.outillage.exploration import CADRE_LECTURE
from maestro.outillage.modele import Analyse, FichierGarde
from maestro.outillage.verification import (
    A_VERIFIER,
    ECHOUEE,
    USAGE_GARDE,
    VERIFIEE,
    Verificateur,
    Verification,
)
from maestro.projets import ProjetStore
from maestro.projets.modele import Perimetre
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

#: The command that plays what guards `CLAUDE.md`, as the project's instructions name it.
GUARD = ".venv/bin/python -m pytest tests/test_budget.py"

#: What the guard says when the piece does not fit — the project's own words, shown as is.
OVER_BUDGET = "CLAUDE.md coûte 441 tokens pour un budget de 400"

#: The rules of a repository that guards its `CLAUDE.md` with a budget test, like Maestro's.
CLAUDE_MD = """\
# Règles du dépôt

- Le budget de ce fichier est de 400 tokens, gardé par `tests/test_budget.py`.
- Python : toujours `.venv/bin/python`, jamais le python du système.
- Ne rejoue pas la suite complète : `bash scripts/ci/local.sh` joue le périmètre du diff.
"""

#: A fake interpreter for the fake player: it is never launched.
FAKE_BASH = ("bash", "-c")

#: The real interpreter, read **before** the conftest guard takes it away.
BASH = execution.interprete()
GIT = shutil.which("git")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `Path.home()` under `tmp_path` — on Windows, `tmp_path` lives under `AppData` (#221)."""
    home = tmp_path / "home"
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
    """The model that reads the project: it replays its answers, one per turn.

    The last one ends the reading; the next reading — the project changed — starts over from
    the first, and reads the files again.
    """

    name: ClassVar[str] = "fake-reader"

    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)
        self._turn = 0

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
        answer = self._answers[self._turn % len(self._answers)]
        self._turn += 1
        return answer


class _Player:
    """A fake player: the guard passes while `CLAUDE.md` has room, fails once it has none.

    It reads the copy it is played in — that is the point: a guard played anywhere else than
    on a copy carrying the piece would not see the bridge. `red` makes the guard fail even
    without the piece, as a test that is already red on the project would.
    """

    def __init__(self, *, red: bool = False) -> None:
        self.red = red
        self.played: list[tuple[str, str]] = []

    def __call__(
        self, command: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        claude_md = Path(cwd) / "CLAUDE.md"
        text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
        self.played.append((command, text))
        if command == GUARD and (self.red or "@AGENTS.md" in text):
            return execution.Execution(
                code=1, sortie=f"FAILED test_budget\n{OVER_BUDGET}", duree_s=0.1
            )
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)

    def guard_runs(self) -> list[str]:
        """The `CLAUDE.md` each guard run saw, in order."""
        return [text for command, text in self.played if command == GUARD]


def write(root: Path, path: str, content: str) -> Path:
    """Puts a file under `root`, parent folders included."""
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def repository(root: Path) -> Path:
    """A Python repository that guards its `CLAUDE.md` with a budget test."""
    write(root, "pyproject.toml", '[project]\nname = "depot"\n')
    write(root, "depot/__init__.py", "")
    write(root, "tests/test_budget.py", "def test_budget():\n    assert True\n")
    write(root, "scripts/setup.sh", "#!/usr/bin/env bash\necho setup\n")
    write(root, "scripts/ci/local.sh", "#!/usr/bin/env bash\necho local\n")
    write(root, "CLAUDE.md", CLAUDE_MD)
    return root


def conclusion(guard: str) -> str:
    """What the model concludes once it has read `CLAUDE.md` — `guard` is its GUARDED line."""
    return "\n".join(
        (
            "COMMANDE: installer | CLAUDE.md | declaree | mise en route | bash scripts/setup.sh",
            "COMMANDE: tester | CLAUDE.md | declaree | le périmètre du diff"
            " | bash scripts/ci/local.sh",
            guard,
            "FIN",
        )
    )


GUARDED = f"GUARDED: CLAUDE.md | CLAUDE.md | budget de 400 tokens | {GUARD}"


def read(root: Path, guard: str = GUARDED) -> Analyse:
    """The repository analysed, then read by a model that opens its `CLAUDE.md`."""
    reader = _Reader(("LIRE: CLAUDE.md", conclusion(guard)))
    return asyncio.run(
        lire_le_projet(analyser(root), perimetre=Perimetre(), provider=reader, modele="fake")
    )


def service(
    tmp_path: Path, player: _Player, guard: str = GUARDED
) -> tuple[ServicePieces, ServiceProjets]:
    """The pieces service of a project read by the fake model, on a workstation with Claude Code."""
    projects = ServiceProjets(
        ProjetStore(tmp_path / "store"), reglages=ReglagesProjetsStore(tmp_path / "settings")
    )
    claude = Client(cle="claude", libelle="Claude Code")
    tooling = ServiceOutillage(
        projects,
        provider=_Reader(("LIRE: CLAUDE.md", conclusion(guard))),
        clients=lambda: (claude,),
    )
    pieces = ServicePieces(
        tooling, projects, verificateur=Verificateur(joueur=player, interprete=FAKE_BASH)
    )
    return pieces, projects


def declare(projects: ServiceProjets, root: Path) -> str:
    """Declares the repository as an imported project and returns its id."""
    return str(projects.creer("Dépôt", str(root))["id"])


def bridge(pieces: ServicePieces, project: str, fil: Sequence[MessageChat] = ()) -> PieceProposee:
    """The `CLAUDE.md` bridge, asked before the other pieces."""
    piece = asyncio.run(pieces.prochaine(project, list(fil), d_abord=("CLAUDE.md",)))
    assert piece is not None and piece.chemin == "CLAUDE.md", piece
    return piece


def guard_verdict(piece: PieceProposee) -> Verification:
    """The verdict of what guards the piece's file, among the piece's verifications."""
    (verdict,) = [v for v in piece.verifications if v.usage == USAGE_GARDE]
    return verdict


def message(piece: PieceProposee) -> MessageChat:
    """The message of the chat that carries `piece`."""
    return MessageChat(
        agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION, contenu="Une pièce.", piece=piece
    )


# --------------------------------------------------------------------------- #
# 1. The reading keeps the command that checks a guarded file                  #
# --------------------------------------------------------------------------- #


def test_the_reading_keeps_the_command_that_checks_a_guarded_file(home: Path) -> None:
    analysis = read(repository(home / "depot"))

    (guarded,) = analysis.constats.fichiers_gardes
    assert isinstance(guarded, FichierGarde)
    assert (guarded.nom, guarded.chemin, guarded.commande) == ("CLAUDE.md", "CLAUDE.md", GUARD)
    assert analysis.constats.to_dict()["guarded_files"][0]["command"] == GUARD


def test_a_command_that_holds_a_pipe_stays_whole(home: Path) -> None:
    """The command comes last, as for `COMMANDE`: a pipe in it is not a field separator."""
    piped = f"{GUARD} | tail -n 20"

    analysis = read(
        repository(home / "depot"), f"GUARDED: CLAUDE.md | CLAUDE.md | budget | {piped}"
    )

    (guarded,) = analysis.constats.fichiers_gardes
    assert guarded.commande == piped


def test_a_guard_read_without_a_command_is_still_a_guard(home: Path) -> None:
    analysis = read(repository(home / "depot"), "GUARDED: CLAUDE.md | CLAUDE.md | budget")

    (guarded,) = analysis.constats.fichiers_gardes
    assert (guarded.nom, guarded.commande) == ("CLAUDE.md", "")


def test_the_frame_asks_for_the_command_that_plays_the_guard() -> None:
    assert "| <ce qu'il exige> | <commande>\n" in CADRE_LECTURE
    frame = " ".join(CADRE_LECTURE.split())
    assert "le test qui le vérifie, pas la suite entière" in frame
    assert "sur une copie du projet qui porte ce qu'il y écrirait" in frame


# --------------------------------------------------------------------------- #
# 2. The guard is played on a copy that carries the piece                      #
# --------------------------------------------------------------------------- #


def test_the_guard_is_played_without_then_with_the_piece_on_a_copy(home: Path) -> None:
    root = repository(home / "depot")
    player = _Player()
    after = CLAUDE_MD + "\n<!-- BEGIN:maestro-outillage -->\n@AGENTS.md\n"

    verdict = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_garde(
        root,
        FichierGarde(nom="CLAUDE.md", chemin="CLAUDE.md", role="budget", commande=GUARD),
        chemin="CLAUDE.md",
        texte=after,
        perimetre=Perimetre(),
    )

    assert player.guard_runs() == [CLAUDE_MD, after]
    assert (verdict.usage, verdict.commande, verdict.etat) == (USAGE_GARDE, GUARD, ECHOUEE)
    assert verdict.code == 1 and OVER_BUDGET in verdict.sortie
    assert "sans cette écriture" in verdict.raison
    # The copy is played, never the root.
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == CLAUDE_MD


@pytest.mark.skipif(GIT is None, reason="git introuvable")
@pytest.mark.usefixtures("_isolated_git")
def test_on_a_versioned_project_the_fresh_clone_carries_the_piece(home: Path) -> None:
    """The guard plays where the person will: a fresh clone of the branch, installed first."""
    root = repository(home / "depot")
    for arguments in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "init")):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    player = _Player()
    after = CLAUDE_MD + "\n@AGENTS.md\n"

    verdict = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_garde(
        root,
        FichierGarde(nom="CLAUDE.md", chemin="CLAUDE.md", role="budget", commande=GUARD),
        chemin="CLAUDE.md",
        texte=after,
        perimetre=Perimetre(),
        installation=_installation(),
    )

    assert [command for command, _ in player.played] == ["bash scripts/setup.sh", GUARD, GUARD]
    assert player.guard_runs() == [CLAUDE_MD, after]
    assert verdict.etat == ECHOUEE
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    ).stdout == ""


def test_a_guard_already_red_without_the_piece_says_nothing_of_the_piece(home: Path) -> None:
    """Red on the project as it is: the piece is not what turns it red — the run of #1688."""
    player = _Player(red=True)

    verdict = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_garde(
        repository(home / "depot"),
        FichierGarde(nom="CLAUDE.md", chemin="CLAUDE.md", role="budget", commande=GUARD),
        chemin="CLAUDE.md",
        texte=CLAUDE_MD + "\n@AGENTS.md\n",
        perimetre=Perimetre(),
    )

    assert verdict.etat == A_VERIFIER
    assert "déjà" in verdict.raison and "sans cette écriture" in verdict.raison
    assert verdict.code == 1 and OVER_BUDGET in verdict.sortie
    # The piece is not even written into the copy: the first run already decided.
    assert player.guard_runs() == [CLAUDE_MD]


def test_a_guard_without_a_command_is_not_played_and_says_why(home: Path) -> None:
    player = _Player()

    verdict = Verificateur(joueur=player, interprete=FAKE_BASH).verifier_garde(
        repository(home / "depot"),
        FichierGarde(nom="CLAUDE.md", chemin="CLAUDE.md", role="budget"),
        chemin="CLAUDE.md",
        texte=CLAUDE_MD + "\n@AGENTS.md\n",
        perimetre=Perimetre(),
    )

    assert verdict.etat == A_VERIFIER and "aucune commande" in verdict.raison
    assert player.played == []


@pytest.mark.commandes_jouees
@pytest.mark.skipif(BASH is None, reason="aucun bash sur ce poste")
def test_a_real_guard_turns_red_on_a_real_copy_and_the_root_stays_intact(home: Path) -> None:
    """A real bash, a real copy: the budget check reads the file the piece is written into."""
    root = repository(home / "depot")
    budget = len(CLAUDE_MD.encode("utf-8")) + 10
    check = f'test "$(wc -c < CLAUDE.md)" -le {budget} || {{ echo "trop long" >&2; exit 4; }}'
    guard = FichierGarde(nom="CLAUDE.md", chemin="CLAUDE.md", role="budget", commande=check)

    def verdict_for(text: str) -> Verification:
        return Verificateur().verifier_garde(
            root, guard, chemin="CLAUDE.md", texte=text, perimetre=Perimetre()
        )

    red = verdict_for(CLAUDE_MD + "\n<!-- BEGIN:maestro-outillage -->\n@AGENTS.md\n")
    holds = verdict_for(CLAUDE_MD + "\n@A\n")

    assert (red.etat, red.code) == (ECHOUEE, 4), red
    assert "trop long" in red.sortie
    assert holds.etat == VERIFIEE, holds
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == CLAUDE_MD


def _installation() -> Any:
    from maestro.outillage.verification import CommandeEcrite

    return CommandeEcrite("installer", "bash scripts/setup.sh", "scripts/setup.sh")


# --------------------------------------------------------------------------- #
# 3. The card says the verdict                                                 #
# --------------------------------------------------------------------------- #


def test_a_bridge_that_turns_the_guard_red_says_so_before_it_is_written(
    tmp_path: Path, home: Path
) -> None:
    root = repository(home / "depot")
    player = _Player()
    pieces, projects = service(tmp_path, player)

    piece = bridge(pieces, declare(projects, root))

    verdict = guard_verdict(piece)
    assert (verdict.etat, verdict.commande) == (ECHOUEE, GUARD)
    assert OVER_BUDGET in verdict.sortie
    assert player.guard_runs() == [CLAUDE_MD, piece.texte_apres]
    assert piece.raison.startswith("✗")
    assert f"fait rougir `{GUARD}`" in piece.raison
    assert "peut faire rougir" not in piece.raison
    # Why there is no writing that holds — and what the project asks to make room.
    assert "Maestro n'a pas d'écriture plus courte" in piece.raison
    assert "budget de 400 tokens" in piece.raison
    assert piece.ecrivable
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == CLAUDE_MD


def test_a_bridge_that_holds_says_it_holds(tmp_path: Path, home: Path) -> None:
    root = repository(home / "depot")
    player = _PlayerThatHolds()
    pieces, projects = service(tmp_path, player)

    piece = bridge(pieces, declare(projects, root))

    assert guard_verdict(piece).etat == VERIFIEE
    assert piece.raison.startswith("✓")
    assert f"`{GUARD}`" in piece.raison and "tient" in piece.raison
    assert [command for command, _ in player.played].count(GUARD) == 2


def test_a_bridge_whose_guard_cannot_be_played_warns_and_says_why(
    tmp_path: Path, home: Path
) -> None:
    root = repository(home / "depot")
    player = _Player()
    pieces, projects = service(tmp_path, player, "GUARDED: CLAUDE.md | CLAUDE.md | budget")

    piece = bridge(pieces, declare(projects, root))

    assert guard_verdict(piece).etat == A_VERIFIER
    assert piece.raison.startswith("⚠")
    assert "aucune commande" in piece.raison
    assert "peut faire rougir" in piece.raison
    assert player.guard_runs() == []


@pytest.mark.parametrize(
    "guard", [GUARDED, "GUARDED: CLAUDE.md | CLAUDE.md | budget"], ids=["red", "not-played"]
)
def test_each_sentence_of_the_card_starts_with_its_capital(
    tmp_path: Path, home: Path, guard: str
) -> None:
    """The screen capitalizes the reason's first letter only: the joints are written here."""
    pieces, projects = service(tmp_path, _Player(), guard)

    piece = bridge(pieces, declare(projects, repository(home / "depot")))

    assert re.search(r"[.!?] [a-zà-ÿ]", piece.raison) is None, piece.raison


class _PlayerThatHolds(_Player):
    """The guard passes whatever `CLAUDE.md` holds: the file has room for the bridge."""

    def __call__(
        self, command: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.played.append((command, ""))
        return execution.Execution(code=0, sortie="1 passed", duree_s=0.1)


# --------------------------------------------------------------------------- #
# 4. The guard verdict judges a copy, not a command the tooling writes         #
# --------------------------------------------------------------------------- #


def test_the_guard_verdict_is_not_recorded_in_the_manifest(tmp_path: Path, home: Path) -> None:
    root = repository(home / "depot")
    pieces, projects = service(tmp_path, _Player())
    piece = bridge(pieces, declare(projects, root))

    fait = asyncio.run(pieces.ecrire(piece))

    assert fait.ecrite, fait
    manifest = json.loads((root / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    assert GUARD not in json.dumps(manifest, ensure_ascii=False)
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_a_bridge_shown_again_reuses_its_guard_verdict(tmp_path: Path, home: Path) -> None:
    """Shown on the chat, the same bridge on the same file is not played a second time."""
    root = repository(home / "depot")
    player = _Player()
    pieces, projects = service(tmp_path, player)
    project = declare(projects, root)
    first = bridge(pieces, project)

    again = bridge(pieces, project, [message(first)])

    assert guard_verdict(again) == guard_verdict(first)
    assert len(player.guard_runs()) == 2


def test_room_made_in_the_file_plays_the_guard_again(tmp_path: Path, home: Path) -> None:
    """After a red card, the person shortens `CLAUDE.md`: the bridge is played again, and holds."""
    root = repository(home / "depot")
    player = _PlayerWithRoom(room=len(CLAUDE_MD) + 20)
    pieces, projects = service(tmp_path, player)
    project = declare(projects, root)
    first = bridge(pieces, project)
    assert guard_verdict(first).etat == ECHOUEE
    shorter = CLAUDE_MD.replace("# Règles du dépôt\n\n", "").replace(
        "- Python : toujours `.venv/bin/python`, jamais le python du système.\n", ""
    )
    write(root, "CLAUDE.md", shorter)

    with pytest.raises(PieceChangee):
        asyncio.run(pieces.ecrire(first))
    again = bridge(pieces, project, [message(first)])

    assert guard_verdict(again).etat == VERIFIEE
    assert again.raison.startswith("✓")
    assert player.guard_runs()[-2:] == [shorter, again.texte_apres]
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == shorter


class _PlayerWithRoom(_Player):
    """The guard passes while `CLAUDE.md` holds at most `room` characters: a budget, counted."""

    def __init__(self, room: int) -> None:
        super().__init__()
        self.room = room

    def __call__(
        self, command: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        claude_md = Path(cwd) / "CLAUDE.md"
        text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
        self.played.append((command, text))
        if command == GUARD and len(text) > self.room:
            return execution.Execution(code=1, sortie=OVER_BUDGET, duree_s=0.1)
        return execution.Execution(code=0, sortie="1 passed", duree_s=0.1)


def test_a_guard_command_written_elsewhere_keeps_its_own_verdict(
    tmp_path: Path, home: Path
) -> None:
    """The guard failed on a copy carrying the bridge: the tests skill still plays its command."""
    command = "bash scripts/ci/local.sh"
    guard = f"GUARDED: CLAUDE.md | CLAUDE.md | budget | {command}"
    player = _PlayerRedOn(command)
    root = repository(home / "depot")
    pieces, projects = service(tmp_path, player, guard)
    project = declare(projects, root)
    first = bridge(pieces, project)
    assert guard_verdict(first).etat == ECHOUEE

    skill = asyncio.run(
        pieces.prochaine(
            project, [message(first)], d_abord=(".agents/skills/lancer-les-tests/SKILL.md",)
        )
    )

    assert skill is not None and skill.chemin.endswith("lancer-les-tests/SKILL.md"), skill
    (tests,) = [v for v in skill.verifications if v.commande == command]
    assert (tests.usage, tests.etat) == ("tester", VERIFIEE)


class _PlayerRedOn(_Player):
    """`command` fails only on a copy whose `CLAUDE.md` carries the bridge."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command

    def __call__(
        self, command: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        claude_md = Path(cwd) / "CLAUDE.md"
        text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
        self.played.append((command, text))
        if command == self.command and "@AGENTS.md" in text:
            return execution.Execution(code=1, sortie=OVER_BUDGET, duree_s=0.1)
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)
