"""Tests du banc `scripts/claude/essai-worktree-gere.py` (#847).

**Ni CLI, ni réseau, ni quota.** Le banc n'a qu'un correspondant — les sessions `claude -p` qu'il
lance — et c'est ce qu'on ne joue pas en CI : chaque session coûte un appel modèle. On substitue
donc `lance()`, et ce qui est vérifié ici est tout le reste : le dépôt d'essai et ses DEUX
worktrees (l'un géré, l'autre frère — sans le second, un A qui passe ne prouve rien), la lecture
du flux, la mesure sur DISQUE et surtout le verdict, qui est la sortie utile du banc.

Un verdict faux ferait rouvrir #847 dans le mauvais sens : replacer les worktrees dans un dossier
frère sur la foi d'une mesure qui n'a rien mesuré, et retrouver la question à chaque
`/ticket-start`.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "claude" / "essai-worktree-gere.py"


def _module():
    """Le script porte un tiret dans son nom : il s'importe par son chemin."""
    spec = importlib.util.spec_from_file_location("essai_worktree_gere", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def essai():
    return _module()


def _resultat(refus: list[tuple[str, str]] = (), cout: float = 0.03) -> dict:
    return {
        "type": "result",
        "total_cost_usd": cout,
        "permission_denials": [
            {"tool_name": outil, "tool_input": {"path": cible}} for outil, cible in refus
        ],
    }


# --- Le dépôt d'essai -----------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent de cette machine")
def test_prepare_monte_un_worktree_gere_et_un_worktree_frere(essai, tmp_path):
    """Les deux emplacements, sur le MÊME dépôt : c'est la comparaison qui fait la mesure."""
    depot, gere, frere = essai.prepare(tmp_path)

    assert gere == depot / ".claude" / "worktrees" / "essai", "le géré vit sous .claude/worktrees/"
    assert frere.parent.parent == depot.parent, "le frère vit à côté du dépôt, comme avant #847"
    assert (gere / ".git").is_file() and (frere / ".git").is_file(), "deux worktrees LIÉS"
    liste = subprocess.run(
        ["git", "worktree", "list"], cwd=depot, capture_output=True, text=True, check=True,
    ).stdout.replace("\\", "/")
    assert "/.claude/worktrees/essai" in liste and "/ailleurs-worktrees/essai2" in liste, liste
    # Le témoin du garde-fou de C : un `.claude/` DANS le worktree géré, portant l'AVANT.
    skill = gere / ".claude" / "skills" / "essai" / "SKILL.md"
    assert essai.AVANT in skill.read_text(encoding="utf-8")
    assert essai.AVANT in (gere / "README.md").read_text(encoding="utf-8"), "le README à éditer"


# --- Les verdicts, lus sur le disque et dans les retours d'outils ---------------------------------


def test_entree_se_lit_dans_le_retour_d_outil(essai):
    """« Entered worktree » vient de l'outil ; la prose finale de la session ne compte pas."""
    assert essai.entre(["EnterWorktree /x", "  ↳ Entered worktree at /x on branch essai."])
    assert not essai.entre(["EnterWorktree /x", "  ↳ ERREUR Enter the worktree at \"/x\"?"])
    assert not essai.entre([])


def test_verdict_a_exige_l_entree_sans_refus_et_le_temoin(essai, tmp_path):
    gere = tmp_path / "gere"
    gere.mkdir()
    outils = ["EnterWorktree x", "  ↳ Entered worktree at x on branch essai."]

    ok, _ = essai.verdict_entree_geree(_resultat(), outils, gere)
    assert not ok, "sans témoin sur le disque, « entré » ne suffit pas"

    (gere / "temoin.txt").write_text(essai.APRES + "\n", encoding="utf-8")
    ok, detail = essai.verdict_entree_geree(_resultat(), outils, gere)
    assert ok, detail

    ok, detail = essai.verdict_entree_geree(_resultat([("EnterWorktree", "x")]), outils, gere)
    assert not ok, "un refus consigné sur EnterWorktree fait tomber A même si le reste est là"
    assert "refus EnterWorktree" in detail

    ok, _ = essai.verdict_entree_geree(_resultat(), ["EnterWorktree x", "  ↳ ERREUR …?"], gere)
    assert not ok, "pas entré, pas de verdict favorable"


def test_verdict_b_exige_le_refus_et_rien_sur_le_disque(essai, tmp_path):
    """B est le témoin : un CLI qui n'entrerait nulle part sans question rendrait A vide de sens."""
    frere = tmp_path / "frere"
    frere.mkdir()
    refuse = ["EnterWorktree y", "  ↳ ERREUR Enter the worktree at \"y\"? This moves…"]

    ok, detail = essai.verdict_entree_ailleurs(_resultat([("EnterWorktree", "y")]), refuse, frere)
    assert ok, detail

    ok, _ = essai.verdict_entree_ailleurs(_resultat(), refuse, frere)
    assert not ok, "sans refus CONSIGNÉ, une session qui renonce d'elle-même passerait pour refusée"

    entre = ["EnterWorktree y", "  ↳ Entered worktree at y on branch essai2."]
    ok, _ = essai.verdict_entree_ailleurs(_resultat([("EnterWorktree", "y")]), entre, frere)
    assert not ok, "entré = le CLI ne demande plus rien nulle part, et A ne prouve plus rien"

    (frere / "temoin.txt").write_text(essai.APRES + "\n", encoding="utf-8")
    ok, _ = essai.verdict_entree_ailleurs(_resultat([("EnterWorktree", "y")]), refuse, frere)
    assert not ok, "un témoin écrit DANS le worktree frère dit que la session y est allée"


def test_verdict_c_lit_le_disque_et_exige_le_garde_fou_intact(essai, tmp_path):
    """Trois écritures ordinaires présentes, celle sous `.claude/` du worktree ABSENTE."""
    gere = tmp_path / "gere"
    (gere / "sous" / "dossier").mkdir(parents=True)
    (gere / ".claude" / "skills" / "essai").mkdir(parents=True)
    (gere / "README.md").write_text(f"# Essai\n\n{essai.AVANT}\n", encoding="utf-8")

    ok, detail = essai.verdict_ecriture(_resultat(), gere)
    assert not ok and "temoin.txt : False" in detail

    (gere / "temoin.txt").write_text(essai.APRES + "\n", encoding="utf-8")
    (gere / "sous" / "dossier" / "NOUVEAU.md").write_text(essai.APRES + "\n", encoding="utf-8")
    ok, detail = essai.verdict_ecriture(_resultat(), gere)
    assert not ok and "README édité : False" in detail, "« fait » dans la prose ne vaut rien"

    (gere / "README.md").write_text(f"# Essai\n\n{essai.APRES}\n", encoding="utf-8")
    ok, detail = essai.verdict_ecriture(_resultat(), gere)
    assert ok, detail

    (gere / ".claude" / "skills" / "essai" / "NOUVEAU.md").write_text("x\n", encoding="utf-8")
    ok, detail = essai.verdict_ecriture(_resultat(), gere)
    assert not ok and "intact : False" in detail, (
        "un garde-fou .claude/ qui ne tire plus est un fait nouveau, pas un succès"
    )


def test_un_temoin_au_mauvais_contenu_ne_compte_pas(essai, tmp_path):
    gere = tmp_path / "gere"
    (gere / "sous" / "dossier").mkdir(parents=True)
    (gere / "temoin.txt").write_text("autre chose\n", encoding="utf-8")
    (gere / "sous" / "dossier" / "NOUVEAU.md").write_text("x\n", encoding="utf-8")
    (gere / "README.md").write_text(essai.APRES, encoding="utf-8")
    ok, detail = essai.verdict_ecriture(_resultat(), gere)
    assert not ok and "temoin.txt : False" in detail


def test_refus_rend_l_outil_et_la_cible(essai):
    resultat = {
        "permission_denials": [
            {"tool_name": "EnterWorktree", "tool_input": {"path": "/w"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/w/.claude/x.md"}},
            {"tool_name": "Bash", "tool_input": {"command": "cp a b"}},
        ]
    }
    assert essai.refus(resultat) == [
        ("EnterWorktree", "/w"), ("Write", "/w/.claude/x.md"), ("Bash", "cp a b"),
    ]
    assert essai.refus({}) == []


# --- La session : ce qui part, ce qui revient ----------------------------------------------------


def test_environnement_ote_le_projet_de_la_session_mere(essai, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/maestro")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = essai.environnement()
    assert "CLAUDE_PROJECT_DIR" not in env and "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["PATH"] == "/usr/bin", "le PATH reste : sans lui la session fille ne démarre pas"


def test_lance_tire_le_resultat_et_les_appels_du_flux(essai, tmp_path, monkeypatch):
    """`stream-json` : les appels ET les retours, dans l'ordre — c'est là que l'entrée se lit."""
    gere = "/w/.claude/worktrees/e"

    def appel(nom: str, entree: dict) -> str:
        return json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": nom, "input": entree},
        ]}})

    def retour(contenu, erreur: bool = False) -> str:
        bloc: dict = {"type": "tool_result", "content": contenu}
        if erreur:
            bloc["is_error"] = True
        return json.dumps({"type": "user", "message": {"content": [bloc]}})

    flux = "\n".join([
        "bruit hors JSON",
        appel("EnterWorktree", {"path": gere}),
        retour(f"Entered worktree at {gere} on branch e."),
        appel("Write", {"file_path": f"{gere}/temoin.txt"}),
        retour([{"type": "text", "text": "refusé"}], erreur=True),
        json.dumps({"type": "result", "total_cost_usd": 0.02, "permission_denials": []}),
    ])
    vus: dict = {}

    def faux_run(argv, **options):
        vus["argv"] = argv
        vus["cwd"] = options["cwd"]
        vus["env"] = options["env"]
        return SimpleNamespace(stdout=flux, stderr="", returncode=0)

    monkeypatch.setattr(essai.subprocess, "run", faux_run)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/maestro")
    resultat, outils = essai.lance(
        "claude", tmp_path, ["EnterWorktree", "Write"], "consigne", "haiku", "0.60", 30,
    )

    assert resultat["total_cost_usd"] == 0.02
    assert outils == [
        "EnterWorktree /w/.claude/worktrees/e",
        "  ↳ Entered worktree at /w/.claude/worktrees/e on branch e.",
        "Write /w/.claude/worktrees/e/temoin.txt",
        "  ↳ ERREUR refusé",
    ]
    assert essai.entre(outils)
    argv = vus["argv"]
    assert argv[:3] == ["claude", "-p", "consigne"]
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert json.loads(argv[argv.index("--settings") + 1]) == {
        "permissions": {"allow": ["EnterWorktree", "Write"]}
    }
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--strict-mcp-config" in argv
    assert vus["cwd"] == tmp_path
    assert "CLAUDE_PROJECT_DIR" not in vus["env"]


def test_lance_refuse_un_flux_sans_objet_result(essai, tmp_path, monkeypatch):
    monkeypatch.setattr(
        essai.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="boum", returncode=1),
    )
    with pytest.raises(RuntimeError, match="boum"):
        essai.lance("claude", tmp_path, [], "c", "m", "0.60", 30)


# --- Le verdict d'ensemble ------------------------------------------------------------------------


def _fausse_preparation(tmp_path: Path):
    depot = tmp_path / "depot"
    gere = depot / ".claude" / "worktrees" / "essai"
    frere = tmp_path / "ailleurs-worktrees" / "essai2"
    for d in (gere / "sous" / "dossier", gere / ".claude" / "skills" / "essai", frere):
        d.mkdir(parents=True)
    (gere / "README.md").write_text("AVANT-847\n", encoding="utf-8")
    return depot, gere, frere


def _fausse_session_conforme(essai, tmp_path):
    """Ce qu'une session ferait si le CLI se comporte comme mesuré : A entre, B refuse, C écrit."""
    def faux_lance(exe, cwd, allow, consigne, modele, budget, delai):
        gere = tmp_path / "depot" / ".claude" / "worktrees" / "essai"
        if "EnterWorktree" in allow and gere.as_posix() in consigne:
            (gere / "temoin.txt").write_text(essai.APRES + "\n", encoding="utf-8")
            return _resultat(), ["EnterWorktree g", "  ↳ Entered worktree at g on branch essai."]
        if "EnterWorktree" in allow:
            return _resultat([("EnterWorktree", "f")]), ["EnterWorktree f", "  ↳ ERREUR Enter…?"]
        (gere / "temoin.txt").write_text(essai.APRES + "\n", encoding="utf-8")
        (gere / "sous" / "dossier" / "NOUVEAU.md").write_text(essai.APRES, encoding="utf-8")
        (gere / "README.md").write_text(essai.APRES + "\n", encoding="utf-8")
        return _resultat([("Write", ".claude/skills/essai/NOUVEAU.md")]), ["Write …"]
    return faux_lance


def _monte(essai, monkeypatch, tmp_path, lance):
    monkeypatch.setattr(essai.shutil, "which", lambda nom: "claude")
    monkeypatch.setattr(essai.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(essai, "prepare", lambda racine: _fausse_preparation(tmp_path))
    monkeypatch.setattr(essai, "lance", lance)
    monkeypatch.setattr(essai.shutil, "rmtree", lambda *a, **k: None)


def test_main_rend_zero_quand_les_trois_attentes_tiennent(essai, monkeypatch, tmp_path, capsys):
    _monte(essai, monkeypatch, tmp_path, _fausse_session_conforme(essai, tmp_path))
    assert essai.main([]) == 0
    sortie = capsys.readouterr().out
    assert "Verdicts : {'A': True, 'B': True, 'C': True}" in sortie
    assert "→ 0" in sortie


def test_main_rend_trois_si_le_cli_entre_aussi_ailleurs(essai, monkeypatch, tmp_path, capsys):
    """Le témoin qui tombe : un CLI qui n'interroge plus nulle part rendrait A sans valeur."""
    conforme = _fausse_session_conforme(essai, tmp_path)

    def lance_laxiste(exe, cwd, allow, consigne, modele, budget, delai):
        frere = tmp_path / "ailleurs-worktrees" / "essai2"
        if "EnterWorktree" in allow and frere.as_posix() in consigne:
            return _resultat(), ["EnterWorktree f", "  ↳ Entered worktree at f on branch essai2."]
        return conforme(exe, cwd, allow, consigne, modele, budget, delai)

    _monte(essai, monkeypatch, tmp_path, lance_laxiste)
    assert essai.main([]) == 3
    sortie = capsys.readouterr().out
    assert "'B': False" in sortie and "→ 3" in sortie


def test_main_rend_trois_si_le_garde_fou_ne_tire_plus(essai, monkeypatch, tmp_path, capsys):
    conforme = _fausse_session_conforme(essai, tmp_path)

    def lance_qui_ouvre_claude(exe, cwd, allow, consigne, modele, budget, delai):
        resultat, outils = conforme(exe, cwd, allow, consigne, modele, budget, delai)
        if "Edit" in allow:
            gere = tmp_path / "depot" / ".claude" / "worktrees" / "essai"
            (gere / ".claude" / "skills" / "essai" / "NOUVEAU.md").write_text("x", encoding="utf-8")
        return resultat, outils

    _monte(essai, monkeypatch, tmp_path, lance_qui_ouvre_claude)
    assert essai.main([]) == 3
    assert "'C': False" in capsys.readouterr().out


def test_main_rend_un_sans_cli_ou_sur_une_session_en_echec(essai, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(essai.shutil, "which", lambda nom: None)
    assert essai.main([]) == 1
    assert "introuvable" in capsys.readouterr().err

    def lance_en_echec(*a, **k):
        raise RuntimeError("le CLI n'a rendu aucun objet `result`")

    _monte(essai, monkeypatch, tmp_path, lance_en_echec)
    assert essai.main([]) == 1, "pas de verdict sur un essai non conduit — surtout pas un 3"
    assert "non conduit" in capsys.readouterr().err


def test_le_script_se_lance_comme_un_programme(tmp_path):
    """`python essai-worktree-gere.py --help` : le point d'entrée existe et parse ses options."""
    acheve = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    assert acheve.returncode == 0, acheve.stderr
    assert "--garder" in acheve.stdout and "--modele" in acheve.stdout
