"""Tests du banc d'essai `scripts/claude/essai-ecriture-claude.py` (#238).

**Ni CLI, ni réseau, ni quota.** Le banc n'a qu'un correspondant — la session `claude -p`
qu'il lance — et c'est justement ce qu'on ne peut pas jouer en CI : chaque variante coûte
un appel modèle. On substitue donc `lance()`, et ce qui est vérifié ici est tout le reste :
le régime passé à chaque variante, la lecture du flux `stream-json`, la mesure sur disque
et surtout **le verdict**, qui est la sortie utile du banc.

Un verdict faux serait le pire des résultats : il ferait rouvrir #238 dans le mauvais sens,
c'est-à-dire élargir `settings.run.json` sur la foi d'une mesure qui n'a rien mesuré. D'où
le soin porté aux trois issues — garde-fou levé, garde-fou tenu, essai non conduit.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "claude" / "essai-ecriture-claude.py"


def _module():
    """Le script porte un tiret dans son nom : il s'importe par son chemin."""
    spec = importlib.util.spec_from_file_location("essai_ecriture_claude", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def essai():
    return _module()


# --- Le régime de chaque variante ----------------------------------------------------------------


def test_les_variantes_de_regle_ne_different_que_par_le_allow(essai, tmp_path):
    """Les cinq de #238 : une variante qui changerait autre chose ne mesurerait plus la règle."""
    regimes = essai.variantes(tmp_path / "projet")
    assert set(regimes) == set(essai.NOMS)
    for nom in ("nu", "cible", "absolue"):
        assert regimes[nom]["consigne"] == essai.CONSIGNE
    # Aucune des cinq ne touche au cadre : c'est ce qui borne la portée du verdict de #238.
    for nom in ("nu", "cible", "absolue", "bash", "script"):
        assert "mode" not in regimes[nom] and "hook" not in regimes[nom]


def test_les_regimes_changent_le_cadre_et_pas_la_regle(essai, tmp_path):
    """#614 : « hook » et « bypass » mesurent le cadre — donc la règle, elle, ne bouge pas."""
    regimes = essai.variantes(tmp_path / "projet")
    assert set(essai.REGIMES) == {"hook", "bypass"}

    # « hook » garde le `allow` du dépôt, au mot près : sinon on mesurerait deux choses.
    assert regimes["hook"]["allow"] == ["Read", "Write", "Edit"]
    assert regimes["hook"]["hook"] is essai.HOOK_ALLOW
    assert "mode" not in regimes["hook"]

    # « bypass » n'ajoute QUE de quoi rendre le hook seul comptable du refus du 4e geste : sans
    # `Bash(printf:*)`, un refus venu des règles se lirait « le hook a tiré », qui est le contraire.
    assert regimes["bypass"]["mode"] == "bypassPermissions"
    assert regimes["bypass"]["allow"] == ["Read", "Write", "Edit", "Bash(printf:*)"]
    assert essai.SENTINELLE in regimes["bypass"]["consigne"]

    # Ni l'un ni l'autre ne porte de règle de chemin — celle-là est la question de #238.
    for nom in essai.REGIMES:
        assert not [r for r in regimes[nom]["allow"] if r.startswith(("Write(", "Edit("))]


def test_la_variante_nu_reproduit_le_depot(essai, tmp_path):
    """« nu » est le témoin : le `allow` de `settings.run.json`, sans règle de chemin."""
    allow = essai.variantes(tmp_path / "projet")["nu"]["allow"]
    assert allow == ["Read", "Write", "Edit"]
    assert not [regle for regle in allow if "(" in regle]


def test_les_variantes_ciblees_portent_bien_un_chemin(essai, tmp_path):
    projet = tmp_path / "projet"
    regimes = essai.variantes(projet)
    assert "Write(.claude/skills/**)" in regimes["cible"]["allow"]
    assert "Edit(.claude/skills/**)" in regimes["cible"]["allow"]
    # L'orthographe absolue doit pointer LE projet jetable, pas une racine en dur.
    absolues = [regle for regle in regimes["absolue"]["allow"] if projet.as_posix() in regle]
    assert len(absolues) == 2


def test_les_variantes_de_repli_passent_par_bash(essai, tmp_path):
    """Le repli se mesure par une commande, pas par un outil de fichier."""
    regimes = essai.variantes(tmp_path / "projet")
    assert set(essai.REPLIS) == {"bash", "script"}
    for nom in essai.REPLIS:
        assert any(regle.startswith("Bash(") for regle in regimes[nom]["allow"])
        assert "outil Bash" in regimes[nom]["consigne"]
        # Aucune règle de chemin : sinon on mesurerait deux choses à la fois.
        assert not [r for r in regimes[nom]["allow"] if r.startswith(("Write(", "Edit("))]


# --- Le projet jetable et la mesure ---------------------------------------------------------------


def test_prepare_seme_un_avant_et_de_quoi_appliquer(essai, tmp_path):
    projet = essai.prepare(tmp_path)
    skill = projet / ".claude" / "skills" / "essai" / "SKILL.md"
    assert essai.AVANT in skill.read_text(encoding="utf-8")
    assert essai.APRES in (projet / "remplacement.md").read_text(encoding="utf-8")
    assert (projet / "appliquer.sh").is_file()
    # Le témoin ne doit PAS préexister : c'est la session qui doit l'écrire.
    assert not (projet / "temoin.txt").exists()


def test_mesure_lit_le_disque_et_pas_la_prose(essai, tmp_path):
    projet = essai.prepare(tmp_path)
    vide = {"temoin": False, "write": False, "edit": False, "sonde": False}
    assert essai.mesure(projet) == vide

    (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
    skills = projet / ".claude" / "skills" / "essai"
    (skills / "NOUVEAU.md").write_text(essai.APRES, encoding="utf-8")
    (skills / "SKILL.md").write_text(essai.APRES, encoding="utf-8")
    assert essai.mesure(projet) == {**vide, "temoin": True, "write": True, "edit": True}


def test_le_temoin_du_hook_se_lit_a_l_existence_et_pas_au_marqueur(essai, tmp_path):
    """`sonde.txt` dit que le hook n'a PAS tiré — son contenu n'a aucune importance (#614)."""
    projet = essai.prepare(tmp_path)
    assert essai.mesure(projet)["sonde"] is False
    (projet / "sonde.txt").write_text("n'importe quoi", encoding="utf-8")
    assert essai.mesure(projet)["sonde"] is True


def test_un_fichier_ecrit_sans_le_marqueur_ne_compte_pas(essai, tmp_path):
    """Le skill de départ existe déjà : sans marqueur, il n'a pas été édité."""
    projet = essai.prepare(tmp_path)
    assert essai.mesure(projet)["edit"] is False


# --- La lecture du flux ---------------------------------------------------------------------------


def test_refus_ne_retient_que_les_chemins_sous_claude(essai):
    resultat = {
        "permission_denials": [
            {"tool_name": "Write", "tool_input": {"file_path": "C:\\p\\.claude\\skills\\a.md"}},
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/p/src/a.md"}},
        ]
    }
    assert essai.refus_sous_claude(resultat) == ["Write C:\\p\\.claude\\skills\\a.md"]


def test_resume_retour_signale_une_erreur_et_aplatit_le_contenu(essai):
    bloc = {"is_error": True, "content": [{"text": "Claude requested"}, {"text": "permissions"}]}
    assert essai.resume_retour(bloc) == "ERREUR Claude requested permissions"
    assert essai.resume_retour({"content": "ok"}) == "ok"


def test_environnement_ote_le_projet_de_la_session_mere(essai, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(RACINE))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("PATH", "/quelque/part")
    env = essai.environnement()
    assert "CLAUDE_PROJECT_DIR" not in env
    assert "CLAUDECODE" not in env
    assert env["PATH"] == "/quelque/part"


def test_lance_tire_le_resultat_et_les_appels_du_flux(essai, tmp_path, monkeypatch):
    """Sans la trace des appels, « rien d'écrit et aucun refus » resterait ambigu."""
    lignes = [
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}),
        "du bruit non-JSON",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "je m'y mets"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "cp a b"}},
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "is_error": True, "content": "refusé"}]
                },
            }
        ),
        json.dumps({"type": "result", "result": "FINI", "total_cost_usd": 0.02}),
    ]
    monkeypatch.setattr(
        essai.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="\n".join(lignes), stderr="", returncode=0),
    )
    resultat, outils = essai.lance("faux", tmp_path, ["Read"], "consigne", "m", "0.5", 10)
    assert resultat["result"] == "FINI"
    assert outils == ["Bash cp a b", "  ↳ ERREUR refusé"]


def test_lance_passe_le_mode_et_monte_le_hook_dans_les_reglages(essai, tmp_path, monkeypatch):
    """#614 : le régime voyage par l'argv et par `--settings`, pas par une variable d'env."""
    vus: dict = {}

    def faux_run(argv, **k):
        vus["argv"] = argv
        return SimpleNamespace(
            stdout=json.dumps({"type": "result", "result": "FINI"}), stderr="", returncode=0
        )

    monkeypatch.setattr(essai.subprocess, "run", faux_run)
    essai.lance(
        "faux",
        tmp_path,
        ["Read"],
        "consigne",
        "m",
        "0.5",
        10,
        mode="bypassPermissions",
        hook=essai.HOOK_DENY,
    )
    argv = vus["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    reglages = json.loads(argv[argv.index("--settings") + 1])
    commande = reglages["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Chemin ABSOLU vers le projet jetable : `environnement()` retire les `CLAUDE_PROJECT*`,
    # donc un `$CLAUDE_PROJECT_DIR` comme dans `settings.run.json` ne se résoudrait pas ici.
    assert "$CLAUDE_PROJECT_DIR" not in commande
    assert (tmp_path / "hook.py").as_posix() in commande
    # La sentinelle est substituée à l'écriture : sans ça le hook ne reconnaîtrait rien.
    assert essai.SENTINELLE in (tmp_path / "hook.py").read_text(encoding="utf-8")
    assert "@SENTINELLE@" not in (tmp_path / "hook.py").read_text(encoding="utf-8")


def test_sans_hook_les_reglages_n_en_portent_aucun(essai, tmp_path, monkeypatch):
    """Les cinq variantes de #238 doivent rester mesurées dans LEUR cadre, sans hook."""
    vus: dict = {}

    def faux_run(argv, **k):
        vus["argv"] = argv
        return SimpleNamespace(
            stdout=json.dumps({"type": "result", "result": "FINI"}), stderr="", returncode=0
        )

    monkeypatch.setattr(essai.subprocess, "run", faux_run)
    essai.lance("faux", tmp_path, ["Read"], "consigne", "m", "0.5", 10)
    argv = vus["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "hooks" not in json.loads(argv[argv.index("--settings") + 1])
    assert not (tmp_path / "hook.py").exists()


# --- Les deux hooks, prouvés avant de servir de sonde ---------------------------------------------
#
# Un hook qui ne tirerait pas ferait rendre au banc « le garde-fou tient » sur une question
# jamais posée — le pire des verdicts, et exactement ce que la méthode du dépôt interdit
# (#366, #537 : prouver la sonde sur un échantillon fautif avant de balayer). On les joue donc
# pour de vrai, en sous-processus, sur des charges synthétiques.


def _joue_hook(essai, tmp_path, source, charge):
    """Écrit le hook, l'exécute sur `charge`, rend (code, stdout, stderr)."""
    chemin = tmp_path / "hook.py"
    chemin.write_text(source.replace("@SENTINELLE@", essai.SENTINELLE), encoding="utf-8")
    fini = subprocess.run(
        [sys.executable, str(chemin)],
        input=json.dumps(charge),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return fini.returncode, fini.stdout, fini.stderr


def test_le_hook_qui_autorise_rend_un_allow_sur_claude_et_se_tait_ailleurs(essai, tmp_path):
    charge = {"tool_name": "Write", "tool_input": {"file_path": "x/.claude/s/S.md"}}
    code, sortie, _ = _joue_hook(essai, tmp_path, essai.HOOK_ALLOW, charge)
    assert code == 0
    decision = json.loads(sortie)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "allow"

    # Hors `.claude/`, il ne se prononce pas : un `allow` global mesurerait deux choses.
    ailleurs = {"tool_name": "Write", "tool_input": {"file_path": "temoin.txt"}}
    code, sortie, _ = _joue_hook(essai, tmp_path, essai.HOOK_ALLOW, ailleurs)
    assert code == 0 and sortie.strip() == ""


def test_le_hook_qui_autorise_reconnait_un_chemin_windows(essai, tmp_path):
    """Le banc tourne sous Windows : un `file_path` y arrive en contre-obliques."""
    code, sortie, _ = _joue_hook(
        essai,
        tmp_path,
        essai.HOOK_ALLOW,
        {"tool_name": "Edit", "tool_input": {"file_path": r"C:\p\.claude\skills\essai\SKILL.md"}},
    )
    assert code == 0
    assert json.loads(sortie)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_le_hook_qui_refuse_tire_sur_la_sentinelle_et_pas_sur_le_reste(essai, tmp_path):
    """Même contrat que `guard.sh` : code 2 et motif sur stderr."""
    code, _, erreur = _joue_hook(
        essai,
        tmp_path,
        essai.HOOK_DENY,
        {"tool_name": "Bash", "tool_input": {"command": f"printf {essai.SENTINELLE} > sonde.txt"}},
    )
    assert code == 2 and "hook" in erreur

    code, _, _ = _joue_hook(
        essai, tmp_path, essai.HOOK_DENY, {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    )
    assert code == 0

    # Et il ne juge que `Bash` : un `Write` portant la sentinelle passe, sans quoi il barrerait
    # les trois écritures que la variante « bypass » est justement là pour mesurer.
    code, _, _ = _joue_hook(
        essai,
        tmp_path,
        essai.HOOK_DENY,
        {"tool_name": "Write", "tool_input": {"file_path": f"{essai.SENTINELLE}.md"}},
    )
    assert code == 0


def test_lance_refuse_un_flux_sans_objet_result(essai, tmp_path, monkeypatch):
    """Une session morte ne doit pas passer pour une mesure : mieux vaut lever."""
    monkeypatch.setattr(
        essai.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="boum", returncode=1),
    )
    with pytest.raises(RuntimeError, match="aucun objet"):
        essai.lance("faux", tmp_path, ["Read"], "consigne", "m", "0.5", 10)


# --- Le verdict -----------------------------------------------------------------------------------


def _joue(essai, monkeypatch, capsys, variantes, ecrit):
    """Joue `main` avec un `lance` postiche. `ecrit(nom, projet)` simule la session."""
    monkeypatch.setenv("MAESTRO_CLAUDE_BIN", "faux-claude")

    def faux_lance(exe, projet, allow, consigne, modele, budget, delai, **cadre):
        # `cadre` porte `mode`/`hook` (#614). Les postiches qui ne s'y intéressent pas gardent
        # leur signature à trois arguments — c'est ce qui a permis d'ajouter deux régimes sans
        # réécrire les sept scénarios de verdict déjà là.
        return ecrit(projet, allow, consigne)

    monkeypatch.setattr(essai, "lance", faux_lance)
    argv = [arg for nom in variantes for arg in ("--variante", nom)]
    code = essai.main(argv)
    return code, capsys.readouterr().out


def test_verdict_le_garde_fou_tient(essai, monkeypatch, capsys):
    """Le résultat réel du 2026-08-05 : aucune règle de chemin n'ouvre `.claude/`."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        refus = {"tool_name": "Write", "tool_input": {"file_path": str(projet / ".claude" / "a")}}
        return {"permission_denials": [refus], "total_cost_usd": 0.03}, ["Write .claude/a"]

    code, sortie = _joue(essai, monkeypatch, capsys, ["nu", "cible", "absolue"], session)
    assert code == 3
    assert "le garde-fou TIENT" in sortie


def test_verdict_le_garde_fou_se_leve(essai, monkeypatch, capsys):
    """Le scénario « si oui » du ticket : une variante ciblée écrit pour de bon."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        if any(regle.startswith("Write(") for regle in allow):
            cible = projet / ".claude" / "skills" / "essai" / "NOUVEAU.md"
            cible.write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, []

    code, sortie = _joue(essai, monkeypatch, capsys, ["nu", "cible"], session)
    assert code == 0
    assert "SE LÈVE" in sortie and "cible" in sortie


def test_un_regime_qui_ouvre_refus_intacts_vaut_zero(essai, monkeypatch, capsys):
    """« bypass » ouvre `.claude/`, la commande sonde a été TENTÉE et bloquée : exploitable."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        cible = projet / ".claude" / "skills" / "essai" / "NOUVEAU.md"
        cible.write_text(essai.APRES, encoding="utf-8")
        # Pas de `sonde.txt`, mais l'appel a bien eu lieu : c'est le hook qui l'a arrêté.
        return {"permission_denials": []}, ["Bash printf SONDE-614 > sonde.txt"]

    code, sortie = _joue(essai, monkeypatch, capsys, ["bypass"], session)
    assert code == 0
    assert "refus intacts" in sortie and "SE LÈVE" in sortie


def test_un_regime_qui_ouvre_sans_sonder_le_hook_ne_conclut_rien(essai, monkeypatch, capsys):
    """Le piège du premier essai réel : `sonde.txt` absent parce que RIEN n'a été tenté.

    Le lire « le hook a tiré » certifierait les refus sur une question jamais posée — et le
    verdict porterait alors sur un régime dont on ignore le prix. On rejoue plutôt que de conclure.
    """

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        cible = projet / ".claude" / "skills" / "essai" / "NOUVEAU.md"
        cible.write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, ["Write temoin.txt"]  # aucun appel `Bash`

    code, sortie = _joue(essai, monkeypatch, capsys, ["bypass"], session)
    assert code == 1
    assert "n'a pas été interrogé" in sortie
    assert "refus intacts" not in sortie and "SE LÈVE" not in sortie


def test_un_regime_qui_ouvre_en_eteignant_les_refus_ne_vaut_pas_zero(essai, monkeypatch, capsys):
    """Le cas que le ticket refuse de taire : la porte n'est pas ouverte, le mur a disparu."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        cible = projet / ".claude" / "skills" / "essai" / "NOUVEAU.md"
        cible.write_text(essai.APRES, encoding="utf-8")
        # Le hook n'a pas tiré : la commande sonde est passée jusqu'au disque.
        (projet / "sonde.txt").write_text(essai.SENTINELLE, encoding="utf-8")
        return {"permission_denials": []}, ["Bash printf SONDE-614 > sonde.txt"]

    code, sortie = _joue(essai, monkeypatch, capsys, ["bypass"], session)
    assert code == 4, "un régime qui éteint les refus ne doit jamais se lire comme un succès"
    assert "le hook ne tire plus" in sortie
    assert "SE LÈVE" not in sortie


def test_un_regime_qui_n_ouvre_pas_laisse_le_verdict_a_trois(essai, monkeypatch, capsys):
    """Le résultat attendu si le garde-fou tient : le régime ne le déplace pas."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, []

    code, sortie = _joue(essai, monkeypatch, capsys, ["hook", "bypass"], session)
    assert code == 3
    assert "le garde-fou TIENT" in sortie
    assert sortie.count("n'ouvre pas `.claude/`") == 2


def test_un_temoin_non_ecrit_annule_la_mesure(essai, monkeypatch, capsys):
    """Sans témoin, rien ne distingue un garde-fou d'une session qui n'a rien fait."""

    def session(projet, allow, consigne):
        return {"permission_denials": []}, []

    code, sortie = _joue(essai, monkeypatch, capsys, ["nu", "cible"], session)
    assert code == 1
    assert "NON CONDUIT" in sortie


def test_le_repli_ne_pese_pas_sur_le_verdict(essai, monkeypatch, capsys):
    """« script » aboutit sans rien dire du `allow` : le verdict doit rester « tient »."""

    def session(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        if any(regle.startswith("Bash(") for regle in allow):
            skill = projet / ".claude" / "skills" / "essai" / "SKILL.md"
            skill.write_text(essai.APRES, encoding="utf-8")
            return {"permission_denials": []}, ["Bash bash appliquer.sh a b"]
        return {"permission_denials": []}, []

    code, sortie = _joue(essai, monkeypatch, capsys, ["cible", "script"], session)
    assert code == 3
    assert "ABOUTIT" in sortie
    assert "le garde-fou TIENT" in sortie


def test_un_repli_bloque_se_distingue_d_un_repli_non_tente(essai, monkeypatch, capsys):
    """Deux issues que rien ne sépare sans la trace des appels."""

    def bloque(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, ["Bash cp a b", "  ↳ ERREUR refusé"]

    code, sortie = _joue(essai, monkeypatch, capsys, ["bash"], bloque)
    assert code == 3
    assert "bloqué lui aussi" in sortie

    def renonce(projet, allow, consigne):
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, ["Write temoin.txt"]

    code, sortie = _joue(essai, monkeypatch, capsys, ["bash"], renonce)
    assert code == 3
    assert "non mesuré" in sortie


def test_une_variante_en_echec_ne_fait_pas_tomber_les_autres(essai, monkeypatch, capsys):
    """Une session perdue coûte sa variante, pas l'essai."""

    def session(projet, allow, consigne):
        if any(regle.startswith("Write(") for regle in allow):
            raise RuntimeError("session perdue")
        (projet / "temoin.txt").write_text(essai.APRES, encoding="utf-8")
        return {"permission_denials": []}, []

    code, sortie = _joue(essai, monkeypatch, capsys, ["nu", "cible"], session)
    assert code == 3
    assert "le garde-fou TIENT" in sortie
