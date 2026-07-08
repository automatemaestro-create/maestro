"""Tests de la télémétrie (ticket #8) : mesure d'usage, collecteur, rédaction, journal.

Aucun appel réseau. Couvre les critères d'acceptation : le coût par étape est
mesuré, agrégé et traçable (run_id) ; les textes journalisés sont expurgés des
secrets (valeurs d'environnement comme motifs de clés).
"""

import json
import logging

import pytest

from maestro.telemetry import (
    LOGGER_NAME,
    MARQUEUR_SECRET,
    RunJournal,
    StepUsage,
    collect_usage,
    redact_secrets,
    report_usage,
)

# --- StepUsage : agrégation ------------------------------------------------------------


def test_fusion_somme_les_compteurs_et_unit_les_outils():
    a = StepUsage(
        appels=1, tokens_entree=100, tokens_sortie=10, cout_usd=0.01,
        duree_api_ms=1000, tours=2, outils=("Read", "Write"),
    )
    b = StepUsage(
        appels=1, tokens_entree=50, tokens_sortie=5, cout_usd=0.02,
        duree_api_ms=500, tours=1, outils=("Write", "Bash"),
    )
    total = a.fusion(b)

    assert total.appels == 2
    assert total.tokens_entree == 150
    assert total.tokens_sortie == 15
    assert total.tokens_total == 165
    assert total.cout_usd == pytest.approx(0.03)
    assert total.duree_api_ms == 1500
    assert total.tours == 3
    assert total.outils == ("Read", "Write", "Bash")


def test_fusion_preserve_l_absence_de_cout():
    # Un fournisseur qui ne rapporte pas de coût laisse cout_usd à None (inconnu ≠ 0).
    assert StepUsage().fusion(StepUsage()).cout_usd is None
    assert StepUsage(cout_usd=0.5).fusion(StepUsage()).cout_usd == 0.5


def test_avec_duree_pose_la_duree_horloge():
    usage = StepUsage(appels=1).avec_duree(1234)
    assert usage.duree_ms == 1234
    assert usage.appels == 1  # le reste de la mesure est préservé


def test_resume_court_avec_et_sans_usage_fournisseur():
    sans = StepUsage().avec_duree(2000).resume_court()
    assert "aucun usage fournisseur rapporté" in sans
    assert "2.0 s" in sans

    avec = StepUsage(appels=1, tokens_entree=10, tokens_sortie=5, cout_usd=0.0123)
    resume = avec.avec_duree(500).resume_court()
    assert "15 tokens" in resume
    assert "0.0123 $" in resume

    # Sans coût rapporté, le résumé l'affiche comme inconnu, pas comme nul.
    assert "coût n/d" in StepUsage(appels=1).resume_court()


# --- Collecteur de contexte ------------------------------------------------------------


def test_report_usage_hors_collecteur_est_sans_effet():
    report_usage(StepUsage(appels=1))  # ne doit pas lever


def test_collect_usage_recolte_puis_referme_le_canal():
    with collect_usage() as recolte:
        report_usage(StepUsage(appels=1, tokens_entree=10, cout_usd=0.01))
        report_usage(StepUsage(appels=1, tokens_sortie=5, cout_usd=0.02))

    assert recolte.total.appels == 2
    assert recolte.total.tokens_total == 15
    assert recolte.total.cout_usd == pytest.approx(0.03)

    # Hors du bloc, plus rien n'est récolté.
    report_usage(StepUsage(appels=1))
    assert recolte.total.appels == 2


def test_collect_usage_imbrique_masque_le_collecteur_englobant():
    # Pas de double comptage : un bloc imbriqué capte seul les mesures de son étape.
    with collect_usage() as externe:
        with collect_usage() as interne:
            report_usage(StepUsage(appels=1))
        assert interne.total.appels == 1
        assert externe.total.appels == 0


# --- Rédaction des secrets -------------------------------------------------------------


def test_redact_masque_les_valeurs_d_environnement(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-tres-secrete-123")
    texte = redact_secrets("la clé est cle-tres-secrete-123, la voilà")
    assert "cle-tres-secrete-123" not in texte
    assert MARQUEUR_SECRET in texte


def test_redact_masque_les_motifs_de_cles_hors_environnement(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    texte = redact_secrets("export ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij")
    assert "sk-ant-api03-abcdefghij" not in texte
    assert MARQUEUR_SECRET in texte


def test_redact_laisse_le_texte_ordinaire_intact():
    assert redact_secrets("bonjour le monde") == "bonjour le monde"
    assert redact_secrets("") == ""


# --- Journal d'exécution ---------------------------------------------------------------


def _consigne(journal, **surcharges):
    """Consigne une étape par défaut, surchargée champ à champ par le test."""
    champs = {
        "etape": "t1",
        "nom": "Tâche 1",
        "agent": "dev",
        "role": "Développeur",
        "statut": "terminee",
        "entree": "fais X",
        "sortie": "fait",
        "usage": StepUsage(),
    }
    champs.update(surcharges)
    return journal.consigne(**champs)


def test_journal_emet_une_ligne_json_par_etape(caplog):
    journal = RunJournal(run_id="run-test")
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _consigne(journal, usage=StepUsage(appels=1, cout_usd=0.01).avec_duree(10))

    assert len(caplog.messages) == 1
    ligne = json.loads(caplog.messages[0])
    assert ligne["run_id"] == "run-test"
    assert ligne["etape"] == "t1"
    assert ligne["statut"] == "terminee"
    assert ligne["horodatage"]
    assert ligne["usage"]["cout_usd"] == 0.01
    assert ligne["usage"]["duree_ms"] == 10


def test_journal_expurge_les_secrets(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-super-secrete-42")
    journal = RunJournal()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        record = _consigne(
            journal,
            statut="echec",
            entree="utilise cle-super-secrete-42 pour appeler l'API",
            sortie="clé sk-ant-api03-abcdef123456 employée",
            erreur="rejet de cle-super-secrete-42",
        )

    assert "cle-super-secrete-42" not in record.entree
    assert "sk-ant-api03-abcdef123456" not in record.sortie
    assert "cle-super-secrete-42" not in (record.erreur or "")
    assert "cle-super-secrete-42" not in caplog.text
    assert "sk-ant-api03-abcdef123456" not in caplog.text
    assert MARQUEUR_SECRET in caplog.text


def test_usage_totale_agrege_les_etapes():
    journal = RunJournal()
    _consigne(journal, etape="t1", usage=StepUsage(appels=1, cout_usd=0.01))
    _consigne(journal, etape="t2", usage=StepUsage(appels=2, cout_usd=0.02))

    assert [r.etape for r in journal.records] == ["t1", "t2"]
    assert journal.usage_totale.appels == 3
    assert journal.usage_totale.cout_usd == pytest.approx(0.03)


def test_chaque_journal_recoit_un_run_id_distinct():
    assert RunJournal().run_id != RunJournal().run_id
