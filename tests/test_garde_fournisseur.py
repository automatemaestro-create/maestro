"""La garde du fournisseur du poste (#782) — éprouvée sur un cas fautif avant de balayer.

`tests/conftest.py` refuse à tout test la résolution du fournisseur de modèle configuré
sur le poste. Cette suite ne recopie pas sa logique : elle **rejoue le conftest lui-même**
— copié tel quel — sur une suite intérieure qui porte le cas fautif du ticket (un test qui
monte `create_app()` et poste dans un fil **sans injecter de répondeur**), dans un
sous-processus pytest, puis lit ce qu'il en est sorti. Sans cette moitié, la garde
rendrait un ✓ sur une question jamais posée : c'est le test fautif qui prouve son motif.

Ce que la suite intérieure établit, en une seule passe :

- le cas du ticket **passe sa phase d'appel** (201, réponse de repli — les répondeurs
  avalent l'échec de résolution) et c'est la garde qui le rougit à sa sortie : sans elle,
  ce test serait vert, et sur un poste configuré il aurait appelé le modèle ;
- une résolution **directe** échoue par `FournisseurDuPosteRefuse`, une seule fois — la
  garde ne redit pas à la sortie une cause que le test vient de nommer ;
- ce qui est refusé le dit **par sa cause** — « ce test allait appeler un vrai modèle » —
  et jamais par l'erreur d'authentification que le fournisseur aurait fini par lever ;
- des `Settings` explicites, le marqueur `fournisseur_du_poste` et un double injecté
  **passent** : la garde ne vise que ce que le poste a configuré.

Un seul sous-processus pour toute la suite (fixture de module) : ce qui coûte est le
démarrage de l'app et l'import du SDK, pas les cinq cas.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (  # le conftest du dossier, sur le sys.path de pytest
    CAUSE_FOURNISSEUR_DU_POSTE,
    MARQUEUR_FOURNISSEUR_DU_POSTE,
    FournisseurDuPosteRefuse,
)

RACINE = Path(__file__).resolve().parents[1]
CONFTEST = RACINE / "tests" / "conftest.py"

#: Les mots de l'erreur que le fournisseur aurait levée bien plus tard — ceux du SDK et du
#: CLI, en anglais. Aucun ne doit apparaître : la garde parle avant lui, et de sa cause.
MOTS_D_AUTHENTIFICATION = ("ANTHROPIC_API_KEY", "authentication", "Authentication", "api key")

#: La suite intérieure : le cas fautif du ticket, sa forme directe, et les trois formes qui
#: doivent passer. Elle est écrite dans un dossier jetable **à côté d'une copie du conftest
#: réel** — c'est lui qui est jugé, pas une réécriture de sa règle.
SUITE_INTERIEURE = '''
import pytest
from fastapi.testclient import TestClient

from maestro.config import Settings
from maestro.controltower.app import create_app
from maestro.controltower.assistance import NOM_ASSISTANCE
from maestro.controltower.chat import ChatStore
from maestro.controltower.events import InMemoryEventBus
from maestro.providers.factory import provider_from_settings
from maestro.providers.registry import UnknownProviderError

QUESTION = "Où est le bouton pour relancer un run bloqué ?"


def _client(tmp_path):
    return TestClient(create_app(bus=InMemoryEventBus(), chat_store=ChatStore(tmp_path / "chat")))


def _settings(provider):
    return Settings(
        anthropic_api_key=None,
        anthropic_model="claude-test",
        claude_auth_mode=None,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
        provider=provider,
    )


def test_fautif_poste_dans_un_fil_sans_repondeur(tmp_path):
    """Le cas du ticket : rien n'est injecté, et le test est vert par lui-même."""
    with _client(tmp_path) as client:
        reponse = client.post(f"/api/chat/{NOM_ASSISTANCE}/messages", json={"contenu": QUESTION})
    assert reponse.status_code == 201


def test_fautif_resolution_directe():
    """La forme nue : la résolution elle-même, hors de tout répondeur."""
    provider_from_settings()


def test_des_settings_explicites_passent_par_la_vraie_fabrique():
    with pytest.raises(UnknownProviderError):
        provider_from_settings(_settings("inconnu-782"))


@pytest.mark.fournisseur_du_poste
def test_le_marqueur_laisse_lire_le_poste(monkeypatch):
    monkeypatch.setenv("MAESTRO_PROVIDER", "inconnu-782")
    with pytest.raises(UnknownProviderError):
        provider_from_settings()


def test_un_double_injecte_ne_declenche_rien(tmp_path, monkeypatch):
    def sans_fournisseur():
        raise KeyError("aucun fournisseur configuré")

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", sans_fournisseur)
    with _client(tmp_path) as client:
        reponse = client.post(f"/api/chat/{NOM_ASSISTANCE}/messages", json={"contenu": QUESTION})
    assert reponse.status_code == 201
'''

FICHIER_INTERIEUR = "test_interieur.py"


@pytest.fixture(scope="module")
def verdict(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    """Une passe de la suite intérieure sous le conftest réel, dans un sous-processus.

    `PYTHONPATH` porte la racine du dépôt : dans le conteneur du filet CI, `maestro` n'a
    d'autre source que le dépôt monté, et le sous-processus ne part pas de sa racine.
    `COLUMNS` élargit le résumé court, que pytest tronque à la largeur du terminal.
    """
    dossier = tmp_path_factory.mktemp("garde-fournisseur")
    shutil.copy(CONFTEST, dossier / "conftest.py")
    (dossier / FICHIER_INTERIEUR).write_text(SUITE_INTERIEURE, encoding="utf-8")
    environnement = {
        **os.environ,
        "PYTHONPATH": str(RACINE),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "COLUMNS": "300",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-rA",
            "--tb=short",
            "-q",
            FICHIER_INTERIEUR,
        ],
        cwd=dossier,
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )


def _lignes(verdict: subprocess.CompletedProcess[str], statut: str) -> list[str]:
    """Les lignes du résumé court de pytest (`-rA`) portant `statut` — PASSED, FAILED, ERROR."""
    return [
        ligne
        for ligne in verdict.stdout.splitlines()
        if re.match(rf"^{statut} {re.escape(FICHIER_INTERIEUR)}::", ligne)
    ]


def _nommes(lignes: list[str]) -> set[str]:
    return {re.sub(r"^[A-Z]+ [^:]+::([^ ]+).*$", r"\1", ligne) for ligne in lignes}


def _comptes(verdict: subprocess.CompletedProcess[str]) -> dict[str, int]:
    """Les compteurs de la ligne de bilan — ce qui s'est passé, et rien d'autre."""
    bilan = verdict.stdout.strip().splitlines()[-1]
    return {mot: int(nombre) for nombre, mot in re.findall(r"(\d+) (passed|failed|error)", bilan)}


def test_le_cas_fautif_du_ticket_passe_sa_phase_d_appel_et_la_garde_le_rougit(verdict) -> None:
    """Le motif, prouvé : sans la garde ce test serait vert — il l'est par lui-même.

    Les répondeurs avalent l'échec de résolution en réponse de repli (201) ; le test ne
    tombe donc pas de lui-même, et c'est à sa **sortie** que la garde le rougit, en nommant
    ce qu'il allait faire. Un `ERROR` et non un `FAILED` : la phase d'appel a bien passé.
    """
    assert "test_fautif_poste_dans_un_fil_sans_repondeur" in _nommes(_lignes(verdict, "PASSED"))
    assert "test_fautif_poste_dans_un_fil_sans_repondeur" not in _nommes(_lignes(verdict, "FAILED"))
    (erreur,) = [
        ligne
        for ligne in _lignes(verdict, "ERROR")
        if "test_fautif_poste_dans_un_fil_sans_repondeur" in ligne
    ]
    assert "1 résolution(s) du fournisseur du poste pendant ce test" in erreur, verdict.stdout
    assert "ce test allait appeler un vrai modèle" in erreur


def test_une_resolution_directe_echoue_par_sa_cause_et_une_seule_fois(verdict) -> None:
    """Hors répondeur, rien n'avale : l'exception de la garde fait échouer le test.

    Et elle ne l'échoue qu'**une** fois — pas d'`ERROR` à la sortie pour la cause que la
    phase d'appel vient de nommer : deux échecs pour le même fait se liraient comme deux
    défauts.
    """
    (echec,) = [
        ligne for ligne in _lignes(verdict, "FAILED") if "test_fautif_resolution_directe" in ligne
    ]
    assert FournisseurDuPosteRefuse.__name__ in echec, verdict.stdout
    assert "test_fautif_resolution_directe" not in _nommes(_lignes(verdict, "ERROR"))
    # Le texte complet de la cause, dans la trace — le résumé court peut le tronquer.
    assert CAUSE_FOURNISSEUR_DU_POSTE in verdict.stdout


def test_ce_qui_est_refuse_le_dit_par_sa_cause_et_non_par_l_authentification(verdict) -> None:
    """Critère 3 : la cause avant la panne — le fournisseur n'a pas été atteint.

    Sur un poste configuré, il aurait **répondu** (43 s, #764) ; sur un poste nu, il aurait
    fini par lever une erreur d'authentification, qui ne dit rien de ce que le test aurait
    dû faire. Ni l'un ni l'autre : ce qui sort nomme le geste manquant et sa réparation.
    """
    assert "ce test allait appeler un vrai modèle" in verdict.stdout
    assert "injecte un répondeur" in CAUSE_FOURNISSEUR_DU_POSTE.lower()
    assert f"@pytest.mark.{MARQUEUR_FOURNISSEUR_DU_POSTE}" in CAUSE_FOURNISSEUR_DU_POSTE
    for mot in MOTS_D_AUTHENTIFICATION:
        assert mot not in verdict.stdout, mot
        assert mot not in verdict.stderr, mot


def test_ce_qui_n_est_pas_le_poste_passe(verdict) -> None:
    """La garde distingue « le poste » d'un fournisseur voulu, et ne rougit rien d'autre.

    Trois formes, trois raisons : des `Settings` construits par le test ne sont pas ceux du
    poste ; le marqueur est la façon de **dire** qu'on veut le poste — et il l'obtient, la
    vraie fabrique lisant alors `MAESTRO_PROVIDER` ; un double injecté n'arrive jamais à la
    fabrique.
    """
    passes = _nommes(_lignes(verdict, "PASSED"))
    erreurs = _nommes(_lignes(verdict, "ERROR"))
    for nom in (
        "test_des_settings_explicites_passent_par_la_vraie_fabrique",
        "test_le_marqueur_laisse_lire_le_poste",
        "test_un_double_injecte_ne_declenche_rien",
    ):
        assert nom in passes, verdict.stdout
        assert nom not in erreurs, verdict.stdout


def test_rien_d_autre_ne_s_est_passe(verdict) -> None:
    """Le bilan exact — pour qu'une erreur de collecte ou un cas oublié ne passe pas."""
    assert _comptes(verdict) == {"passed": 4, "failed": 1, "error": 1}, verdict.stdout
    assert verdict.returncode != 0


@pytest.mark.fournisseur_du_poste
def test_dans_cette_session_le_marqueur_efface_la_garde(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le marqueur, joué sous le conftest de **cette** session et non sur sa copie.

    Le fournisseur configuré est rendu inconnu : la vraie fabrique lit bien le poste (c'est
    elle qui refuse le nom), et rien n'est appelé.
    """
    from maestro.providers.factory import provider_from_settings
    from maestro.providers.registry import UnknownProviderError

    monkeypatch.setenv("MAESTRO_PROVIDER", "inconnu-782")

    with pytest.raises(UnknownProviderError):
        provider_from_settings()
