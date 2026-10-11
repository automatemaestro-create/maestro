"""Le banc sur `main` intégrée : quand le jouer, et à quelles PR rattacher un rouge (#1462).

Ce que `maestro.scenarios.sur_main` décide, sur de vrais dépôts Git jetables et des
lignes d'historique écrites à la main — aucun réseau, aucun modèle :

① **décider** — jouer quand `main` a bougé sur le chemin depuis le dernier passage
  complet sur `main`, s'abstenir en le disant sinon ; un passage empêché, incomplet,
  joué sur une branche ou sur un arbre modifié ne compte pas ;
② **le verdict** — un rouge nomme les PR mergées entre son dernier vert sur `main` et
  le sha joué (celles du chemin d'abord), un empêchement est une abstention jamais
  rattachée, et une abstention n'est jamais un vert ;
③ **la ligne de commande** — ce que `scripts/orchestrate/banc-main.sh` lit.

Le passage lui-même (copie dédiée, stack, pilote) est éprouvé dans
`tests/test_orchestrate.py`, le banc doublé.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from maestro.scenarios import historique, sur_main
from maestro.scenarios.sur_main import (
    CODE_ABSTENTION,
    CODE_ROUGE,
    CODE_USAGE,
    CODE_VERT,
    ETAT_ABSTENTION,
    ETAT_ROUGE,
    ETAT_VERT,
    Depot,
    decider,
    en_texte,
    verdict,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git introuvable")

_IDENTITE = ("-c", "user.name=banc", "-c", "user.email=banc@example.invalid")
CHEMINS = ("maestro/", "core/")
CATALOGUE = ("S1", "S2", "S3")


def _git(racine: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *_IDENTITE, "-C", str(racine), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


class Main:
    """Un `main` jetable : chaque PR est un commit de squash, comme sur GitHub."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        racine.mkdir(parents=True)
        _git(racine, "init", "-q", "-b", "main")
        self.pr("README.md", "base", pr=None)

    def pr(self, fichier: str, sujet: str, *, pr: int | None, ticket: int | None = None) -> str:
        chemin = self.racine / fichier
        chemin.parent.mkdir(parents=True, exist_ok=True)
        ancien = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
        chemin.write_text(ancien + f"{sujet}\n", encoding="utf-8")
        _git(self.racine, "add", "-A")
        message = f"{sujet} (#{pr})" if pr is not None else sujet
        corps = ["-m", f"Closes #{ticket}"] if ticket is not None else []
        _git(self.racine, "commit", "-q", "-m", message, *corps)
        return _git(self.racine, "rev-parse", "HEAD")

    def branche(self, nom: str, fichier: str) -> str:
        """Un commit de branche de ticket : jamais un ancêtre de `main` (le squash le remplace)."""
        _git(self.racine, "checkout", "-q", "-b", nom)
        sha = self.pr(fichier, f"travail de {nom}", pr=None)
        _git(self.racine, "checkout", "-q", "main")
        return sha

    @property
    def depot(self) -> Depot:
        return Depot(self.racine)


def _passage(
    passage: str,
    sha: str | None,
    verdicts: dict[str, str],
    *,
    modifiee: bool | None = False,
    empeches: Sequence[str] = (),
    branche: str | None = None,
) -> dict[str, Any]:
    return {
        "version": historique.VERSION,
        "source": historique.SOURCE_PASSAGE,
        "passage": passage,
        "sha": sha,
        "branche": branche,
        "iid": None,
        "copie": "/copie",
        "modifiee": modifiee,
        "scenarios": [
            {
                "id": i,
                "verdict": v,
                "cout_usd": 1.0,
                "duree_s": 60.0,
                "rejoue": False,
                "empechement": i in empeches,
                "run_id": "",
            }
            for i, v in verdicts.items()
        ],
    }


def _tous(verdict_: str = "vert") -> dict[str, str]:
    return {i: verdict_ for i in CATALOGUE}


def _rapport(passage: str, verdicts: dict[str, str], *, empeches: Sequence[str] = ()) -> dict:
    return {
        "horodatage": passage,
        "scenarios": [
            {"id": i, "verdict": v, "motif": f"motif de {i}", "empechement": i in empeches}
            for i, v in verdicts.items()
        ],
    }


@pytest.fixture
def main(tmp_path: Path) -> Main:
    return Main(tmp_path / "depot")


# ── ① Décider ────────────────────────────────────────────────────────────────


def test_sans_passage_complet_consigne_le_premier_passage_par_lot_se_joue(main: Main) -> None:
    cible = main.pr("maestro/a.py", "feat: a", pr=10)
    decision = decider([], cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert decision.jouer
    assert "aucun passage complet" in decision.motif


def test_main_qui_n_a_pas_bouge_sur_le_chemin_s_abstient_en_le_disant(main: Main) -> None:
    joue = main.pr("maestro/a.py", "feat: a", pr=10)
    cible = main.pr("docs/notes.md", "docs: notes", pr=11)
    lignes = [_passage("20261010-100000", joue, _tous())]
    decision = decider(lignes, cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert not decision.jouer
    assert "n'a pas bougé sur le chemin des scénarios" in decision.motif
    assert "20261010-100000" in decision.motif
    assert joue[:7] in decision.motif and cible[:7] in decision.motif


def test_main_deja_jouee_au_meme_sha_s_abstient(main: Main) -> None:
    cible = main.pr("maestro/a.py", "feat: a", pr=10)
    lignes = [_passage("20261010-100000", cible, _tous("rouge"))]
    decision = decider(lignes, cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert not decision.jouer
    assert "déjà son passage complet" in decision.motif


def test_main_qui_a_bouge_sur_le_chemin_se_joue_et_nomme_les_fichiers(main: Main) -> None:
    joue = main.pr("README.md", "docs: base", pr=10)
    main.pr("maestro/a.py", "feat: a", pr=11)
    cible = main.pr("core/agents/dev.md", "feat: dev", pr=12)
    lignes = [_passage("20261010-100000", joue, _tous())]
    decision = decider(lignes, cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert decision.jouer
    assert "2 fichier(s) du chemin" in decision.motif
    assert "maestro/a.py" in decision.motif and "core/agents/dev.md" in decision.motif


@pytest.mark.parametrize(
    "ce_qui_ne_compte_pas",
    ["incomplet", "empeche", "arbre-modifie", "modification-inconnue", "branche", "sans-sha"],
)
def test_un_passage_qui_n_a_pas_tout_verifie_sur_main_ne_dispense_pas_de_jouer(
    main: Main, ce_qui_ne_compte_pas: str
) -> None:
    """Seul un passage COMPLET, sans empêchement, sur un état propre de `main` dispense."""
    joue = main.pr("maestro/a.py", "feat: a", pr=10)
    cible = main.pr("docs/notes.md", "docs: notes", pr=11)
    ligne = {
        "incomplet": _passage("20261010-100000", joue, {"S1": "vert", "S2": "vert"}),
        "empeche": _passage("20261010-100000", joue, _tous(), empeches=("S3",)),
        "arbre-modifie": _passage("20261010-100000", joue, _tous(), modifiee=True),
        "modification-inconnue": _passage("20261010-100000", joue, _tous(), modifiee=None),
        "branche": _passage(
            "20261010-100000", main.branche("feat/12-x", "maestro/b.py"), _tous(),
            branche="feat/12-x",
        ),
        "sans-sha": _passage("20261010-100000", None, _tous()),
    }[ce_qui_ne_compte_pas]
    decision = decider([ligne], cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert decision.jouer, decision.motif
    assert "aucun passage complet" in decision.motif


def test_le_passage_de_reference_est_le_plus_recent_des_complets(main: Main) -> None:
    ancien = main.pr("README.md", "docs: base", pr=9)
    recent = main.pr("maestro/a.py", "feat: a", pr=10)
    cible = main.pr("docs/notes.md", "docs: notes", pr=11)
    lignes = [
        _passage("20261009-100000", ancien, _tous()),
        _passage("20261010-100000", recent, _tous()),
        _passage("20261011-100000", recent, {"S1": "vert"}),  # plus récent, mais incomplet
    ]
    decision = decider(lignes, cible, main.depot, chemins=CHEMINS, catalogue=CATALOGUE)
    assert not decision.jouer
    assert "20261010-100000" in decision.motif


# ── ② Le verdict ─────────────────────────────────────────────────────────────


def test_un_rouge_nomme_les_pr_mergees_depuis_son_dernier_vert_sur_main(main: Main) -> None:
    vert = main.pr("maestro/a.py", "feat: a", pr=10, ticket=5)
    main.pr("maestro/b.py", "fix(engine): b", pr=11, ticket=6)
    main.pr("docs/c.md", "docs: c", pr=12, ticket=7)
    cible = main.pr("core/d.md", "feat(core): d", pr=13, ticket=8)
    lignes = [
        _passage("20261009-100000", vert, _tous()),
        # Un vert plus récent, mais sur une branche : il ne borne rien sur main.
        _passage("20261010-100000", main.branche("feat/9-y", "maestro/y.py"), _tous(),
                 branche="feat/9-y"),
    ]
    rendu = verdict(
        _rapport("20261011-100000", {"S1": "vert", "S2": "rouge", "S3": "vert"}),
        lignes, cible, main.depot, chemins=CHEMINS,
    )
    assert rendu.etat == ETAT_ROUGE and rendu.code == CODE_ROUGE
    (rouge,) = rendu.rouges
    assert rouge.dernier_vert is not None and rouge.dernier_vert["passage"] == "20261009-100000"
    assert [c.pr for c in rouge.commits or ()] == [11, 12, 13]

    texte = en_texte(rendu)
    assert texte.splitlines()[0].startswith(f"Banc sur main {cible[:7]} : rouge (S2)")
    assert "dernier vert sur main : 20261009-100000" in texte
    assert "3 PR mergée(s) depuis" in texte
    assert "#11 (ticket #6) fix(engine): b" in texte
    assert "#13 (ticket #8) feat(core): d" in texte
    assert "hors du chemin des scénarios (1) : #12" in texte
    assert "#10" not in texte, "la PR du dernier vert n'est pas dans l'intervalle"


def test_un_rouge_sans_vert_sur_main_le_dit_au_lieu_de_rattacher(main: Main) -> None:
    cible = main.pr("maestro/a.py", "feat: a", pr=10)
    rendu = verdict(_rapport("20261011-100000", {"S1": "rouge"}), [], cible, main.depot)
    texte = en_texte(rendu)
    assert rendu.etat == ETAT_ROUGE
    assert "aucun vert sur main consigné" in texte


def test_un_rouge_vert_au_meme_sha_est_dit_non_deterministe(main: Main) -> None:
    cible = main.pr("maestro/a.py", "feat: a", pr=10)
    lignes = [_passage("20261010-100000", cible, {"S1": "vert"})]
    texte = en_texte(
        verdict(_rapport("20261011-100000", {"S1": "rouge"}), lignes, cible, main.depot)
    )
    assert "au même sha" in texte and "non déterministe" in texte


@pytest.mark.parametrize(
    ("verdicts", "empeches", "etat"),
    [
        ({"S1": "vert", "S2": "vert"}, (), ETAT_VERT),
        ({"S1": "vert", "S2": "rouge"}, ("S2",), ETAT_ABSTENTION),
        ({}, (), ETAT_ABSTENTION),
        ({"S1": "rouge", "S2": "rouge"}, ("S2",), ETAT_ROUGE),
    ],
)
def test_un_empechement_est_une_abstention_jamais_un_vert(
    main: Main, verdicts: dict[str, str], empeches: tuple[str, ...], etat: str
) -> None:
    cible = main.pr("maestro/a.py", "feat: a", pr=10)
    rendu = verdict(_rapport("20261011-100000", verdicts, empeches=empeches), [], cible,
                    main.depot)
    assert rendu.etat == etat
    assert all(r.scenario not in empeches for r in rendu.rouges), "un empêché n'est pas rattaché"
    if empeches:
        assert "empêché (abstention)" in en_texte(rendu)


# ── ③ La ligne de commande ───────────────────────────────────────────────────


def _cli(*args: str, catalogue: Sequence[str] = CATALOGUE) -> tuple[int, str, str]:
    sortie, erreur = io.StringIO(), io.StringIO()
    code = sur_main.main(list(args), catalogue=catalogue, sortie=sortie, erreur=erreur)
    return code, sortie.getvalue(), erreur.getvalue()


def _historique(lignes: Sequence[dict[str, Any]]) -> None:
    fichier = historique.chemin_historique()
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text("".join(json.dumps(ligne) + "\n" for ligne in lignes), encoding="utf-8")


def test_decider_rend_son_motif_sur_une_ligne_et_son_code(main: Main) -> None:
    joue = main.pr("maestro/a.py", "feat: a", pr=10)
    cible = main.pr("docs/n.md", "docs: n", pr=11)
    _historique([_passage("20261010-100000", joue, _tous())])
    code, sortie, _ = _cli(
        "decider", "--cible", cible, "--chemins", "maestro/ core/", "--depot", str(main.racine)
    )
    assert code == CODE_ABSTENTION
    etat, motif = sortie.rstrip("\n").split("\t")
    assert etat == "abstention" and "n'a pas bougé" in motif

    cible = main.pr("maestro/b.py", "feat: b", pr=12)
    code, sortie, _ = _cli(
        "decider", "--cible", cible, "--chemins", "maestro/ core/", "--depot", str(main.racine)
    )
    assert code == 0 and sortie.startswith("jouer\t")


def test_verdict_lit_le_rapport_et_rend_le_code_du_passage(main: Main, tmp_path: Path) -> None:
    vert = main.pr("maestro/a.py", "feat: a", pr=10)
    cible = main.pr("maestro/b.py", "feat: b", pr=11, ticket=4)
    _historique([_passage("20261010-100000", vert, _tous())])
    rapport = tmp_path / "20261011-100000" / "rapport.json"
    rapport.parent.mkdir()
    rapport.write_text(json.dumps(_rapport("20261011-100000", {"S1": "rouge"})), encoding="utf-8")
    code, sortie, _ = _cli(
        "verdict", "--cible", cible, "--rapport", str(rapport), "--depot", str(main.racine),
        "--chemins", "maestro/",
    )
    assert code == CODE_ROUGE
    assert "#11 (ticket #4) feat: b" in sortie
    assert "rapport.md" in sortie

    rapport.write_text(json.dumps(_rapport("20261011-100000", {"S1": "vert"})), encoding="utf-8")
    code, sortie, _ = _cli("verdict", "--cible", cible, "--rapport", str(rapport),
                           "--depot", str(main.racine))
    assert code == CODE_VERT and sortie.startswith(f"Banc sur main {cible[:7]} : vert")


def test_un_rapport_illisible_est_refuse_en_le_nommant(tmp_path: Path) -> None:
    rapport = tmp_path / "rapport.json"
    rapport.write_text("{pas du json", encoding="utf-8")
    code, _, erreur = _cli("verdict", "--cible", "abc", "--rapport", str(rapport))
    assert code == sur_main.CODE_ILLISIBLE and str(rapport) in erreur


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("lire",),
        ("decider", "--cible", "abc"),
        ("decider", "--chemins", "maestro/"),
        ("verdict", "--cible", "abc"),
        ("verdict", "--cible", "abc", "--rapport"),
    ],
)
def test_un_usage_fautif_est_refuse(args: tuple[str, ...]) -> None:
    code, _, erreur = _cli(*args)
    assert code == CODE_USAGE and "Usage" in erreur
