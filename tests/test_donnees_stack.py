"""Les données d'une stack, et ce qu'elle en annonce au démarrage (#1164).

`maestro.controltower.donnees` nomme ensemble l'espace Redis d'une stack et ses
dépôts de fichiers. Trois choses sont gardées ici :

① **Le tableau des dépôts dit vrai** — chaque variable qu'il nomme déplace bien
   le dépôt que l'API ouvre (`default()` de chaque dépôt, lu tel quel) : une entrée
   qui nommerait une autre variable poserait le banc sur des dossiers que l'API ne
   lit pas, et l'état rouvert serait vide sans que rien ne le dise.

② **L'annonce dit la séparation** — l'espace et d'où il vient, où vivent fils et
   projets, et **ce qui est partagé** quand une variable les a déplacés.

③ **Le banc est un jeu à part** — son espace, ses dépôts sous `.maestro/banc/`,
   idempotent, et posé par `maestro-api --etat-banc` avant de servir.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import uvicorn

from maestro.config import Settings
from maestro.controltower import cli
from maestro.controltower.donnees import (
    DEPOTS,
    DOSSIER_BANC,
    ORIGINE_BANC,
    SUFFIXE_BANC,
    Depot,
    annonce,
    donnees_de_la_stack,
    donnees_du_banc,
)
from maestro.espace import COMMUN, VARIABLE_ESPACE, racine_de_la_copie
from tests.redis_factice import brancher


@pytest.fixture()
def sans_reglage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun dépôt déplacé : la stack d'une copie telle qu'elle naît."""
    for depot in DEPOTS:
        monkeypatch.delenv(depot.variable, raising=False)


# ── ① Le tableau des dépôts dit vrai ─────────────────────────────────────────


@pytest.mark.parametrize("depot", DEPOTS, ids=lambda d: d.nom)
def test_chaque_variable_deplace_le_depot_que_l_api_ouvre(
    depot: Depot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(depot.variable, str(tmp_path / "ailleurs"))
    assert depot.racine(Settings.from_env()) == tmp_path / "ailleurs"


def test_sans_reglage_les_depots_vivent_sous_le_core_de_la_copie(sans_reglage: None) -> None:
    donnees = donnees_de_la_stack()
    core = racine_de_la_copie() / "core"
    assert donnees.racines["chat"] == core / "chat"
    assert donnees.racines["projets"] == core / "projets"
    assert all(racine.is_relative_to(core) for racine in donnees.racines.values())
    assert donnees.regles == frozenset()


# ── ② L'annonce dit la séparation ────────────────────────────────────────────


def test_l_annonce_du_clone_principal_dit_ses_noms_sans_prefixe(sans_reglage: None) -> None:
    lignes = annonce(donnees_de_la_stack())
    assert f"espace « {COMMUN} »" in lignes[0]
    assert "sans préfixe" in lignes[1]
    assert "#" not in "".join(lignes), "l'annonce parle au poste, pas au dépôt"
    assert any(ligne.split()[0] == "fils" and "core/chat" in ligne for ligne in lignes)
    assert any(ligne.split()[0] == "projets" and "core/projets" in ligne for ligne in lignes)


def test_l_annonce_d_un_worktree_dit_son_prefixe(
    sans_reglage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE_ESPACE, "1164-une-stack")
    lignes = annonce(donnees_de_la_stack())
    assert "espace « 1164-une-stack »" in lignes[0]
    assert "« 1164-une-stack:maestro.* »" in lignes[1]
    assert "aucune autre copie" in lignes[1]


def test_un_depot_deplace_est_annonce_comme_partageable(
    sans_reglage: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Deux copies réglées sur le même dossier le partagent : l'annonce le dit."""
    monkeypatch.setenv("MAESTRO_CHAT_DIR", str(tmp_path / "fils-partages"))
    donnees = donnees_de_la_stack()
    assert donnees.regles == frozenset({"chat"})
    ligne = next(ligne for ligne in annonce(donnees) if ligne.split()[0] == "fils")
    assert str(tmp_path / "fils-partages") in ligne
    assert "déplacé par MAESTRO_CHAT_DIR" in ligne


def test_le_preflight_de_l_api_annonce_les_donnees(
    sans_reglage: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`start.sh` rend ce que le préflight imprime : c'est là que la séparation se dit."""
    brancher(monkeypatch)
    assert cli.verifier_redis() == 0
    sortie = capsys.readouterr().out
    assert "Redis joignable" in sortie
    assert "Données de cette stack — espace « commun »" in sortie


# ── ③ Le banc est un jeu à part ──────────────────────────────────────────────


def test_le_banc_a_son_espace_et_ses_depots(tmp_path: Path) -> None:
    banc = donnees_du_banc(racine=tmp_path)
    assert banc.espace.nom == f"{COMMUN}{SUFFIXE_BANC}"
    assert banc.espace.origine == ORIGINE_BANC
    assert banc.banc
    assert {d.nom: banc.racines[d.nom] for d in DEPOTS} == {
        d.nom: tmp_path / DOSSIER_BANC / d.nom for d in DEPOTS
    }
    env = banc.environnement()
    assert env[VARIABLE_ESPACE] == banc.espace.nom
    assert {d.variable for d in DEPOTS} <= set(env)


def test_le_banc_est_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dans un process déjà posé sur le banc, le banc est lui-même — jamais `.banc.banc`."""
    banc = donnees_du_banc(racine=tmp_path)
    for variable, valeur in banc.environnement().items():
        monkeypatch.setenv(variable, valeur)
    assert donnees_du_banc(racine=tmp_path) == banc


def test_sur_le_banc_l_annonce_ne_prend_pas_ses_dossiers_pour_des_reglages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    banc = donnees_du_banc()
    for variable, valeur in banc.environnement().items():
        monkeypatch.setenv(variable, valeur)
    donnees = donnees_de_la_stack()
    assert donnees.espace == banc.espace
    assert donnees.regles == frozenset()
    texte = "\n".join(annonce(donnees))
    assert ORIGINE_BANC in texte
    assert (DOSSIER_BANC / "chat").as_posix() in texte
    assert "déplacé" not in texte


def test_l_api_se_pose_sur_le_banc_avant_de_servir(monkeypatch: pytest.MonkeyPatch) -> None:
    """`maestro-api --etat-banc` : l'environnement est posé avant la fabrique d'uvicorn,
    donc avant `create_default_app` — et hérité par les hôtes détachés."""
    banc = donnees_du_banc()
    for variable in banc.environnement():
        monkeypatch.setenv(variable, os.environ.get(variable, ""))
    vu: dict[str, str] = {}

    def servir(*_args: object, **_kwargs: object) -> None:
        vu.update({v: os.environ.get(v, "") for v in banc.environnement()})

    monkeypatch.setattr(uvicorn, "run", servir)
    assert cli.main(["--port", "18097", "--etat-banc"]) == 0
    assert vu == banc.environnement()
