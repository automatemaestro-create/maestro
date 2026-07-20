"""Tests de la télémétrie (ticket #8) : mesure d'usage, collecteur, rédaction, journal.

Aucun appel réseau. Couvre les critères d'acceptation : le coût par étape est
mesuré, agrégé et traçable (run_id) ; les textes journalisés sont expurgés des
secrets (valeurs d'environnement comme motifs de clés).

Couvre aussi le suivi de coût par tâche (#49, tests différés → #59) :

- **comptabilité par tâche** (#55) : `RunCost.depuis_journal` attribue chaque
  ligne du journal à sa tâche (annexes comprises), une seule fois — le total
  retombe sur `RunJournal.usage_totale` — et s'exporte en JSON (la forme de
  l'API, #57) ;
- **plafond de dépense** (#56) : `PlafondDepense` relit ce grand livre à chaque
  vérification (aucun compteur parallèle) et lève `PlafondDepenseDepasse` au
  dépassement — relayé par `report_usage` chez l'appelant du fournisseur, la
  mesure fautive restant comptée (le coût reste visible).
"""

import json
import logging

import pytest

from maestro.telemetry import (
    ETAPE_PLANIFICATION,
    LOGGER_NAME,
    MARQUEUR_SECRET,
    PlafondDepense,
    PlafondDepenseDepasse,
    RunCost,
    RunJournal,
    StepUsage,
    collect_usage,
    redact_secrets,
    report_usage,
    resume_controle_depense,
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


# --- Comptabilité par tâche (#55) --------------------------------------------------------


def test_depuis_journal_attribue_chaque_ligne_a_sa_tache():
    journal = RunJournal(run_id="run-compta")
    _consigne(journal, etape=ETAPE_PLANIFICATION, nom="Planification",
              agent="orchestrateur", role="Orchestrateur",
              usage=StepUsage(appels=1, cout_usd=0.01))
    _consigne(journal, etape="t1", usage=StepUsage(
        appels=1, tokens_entree=100, tokens_sortie=10, cout_usd=0.02))
    _consigne(journal, etape="t2", nom="Tâche 2", agent="qa", role="QA / Testeur",
              usage=StepUsage(appels=1, cout_usd=0.04))
    # Étape annexe de t1 (validation humaine) : rattachée à sa tâche, pas une entrée à part.
    _consigne(journal, etape="t1:validation", statut="approuve",
              usage=StepUsage(cout_usd=0.005))

    cout = RunCost.depuis_journal(journal)

    assert cout.run_id == "run-compta"
    assert cout.planification.cout_usd == pytest.approx(0.01)
    # Une entrée par tâche, dans l'ordre de première apparition au journal.
    assert [t.tache_id for t in cout.taches] == ["t1", "t2"]
    t1, t2 = cout.taches
    assert t1.nom == "Tâche 1" and t1.agent == "dev"  # l'identité vient de l'étape de la tâche
    assert t1.usage.cout_usd == pytest.approx(0.025)  # étape + annexe fusionnées
    assert t2.usage.cout_usd == pytest.approx(0.04)
    # Chaque ligne comptée exactement une fois : le total retombe sur le journal.
    assert cout.total.cout_usd == pytest.approx(journal.usage_totale.cout_usd)
    assert cout.total.appels == journal.usage_totale.appels


def test_une_tache_connue_par_sa_seule_annexe_reste_sans_identite():
    # Comptabilité partielle en cours de run : une annexe peut précéder l'étape de la tâche.
    journal = RunJournal()
    _consigne(journal, etape="t1:message", statut="envoye",
              usage=StepUsage(appels=1, cout_usd=0.01))

    (tache,) = RunCost.depuis_journal(journal).taches

    assert tache.tache_id == "t1"
    assert tache.nom == "" and tache.agent == "" and tache.statut == ""
    assert tache.usage.cout_usd == pytest.approx(0.01)  # l'usage est déjà compté


def test_le_cout_inconnu_reste_inconnu_dans_la_comptabilite():
    # Un fournisseur qui ne rapporte pas de coût laisse la tâche à coût None (≠ 0).
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(appels=1))

    cout = RunCost.depuis_journal(journal)

    assert cout.taches[0].usage.cout_usd is None
    assert cout.total.cout_usd is None


def test_la_comptabilite_s_exporte_en_json():
    journal = RunJournal(run_id="run-json")
    _consigne(journal, etape=ETAPE_PLANIFICATION, nom="Planification",
              agent="orchestrateur", role="Orchestrateur",
              usage=StepUsage(appels=1, cout_usd=0.01))
    _consigne(journal, etape="t1", usage=StepUsage(appels=1, tokens_entree=7, cout_usd=0.02))

    forme = RunCost.depuis_journal(journal).to_dict()

    assert forme["run_id"] == "run-json"
    assert forme["planification"]["cout_usd"] == pytest.approx(0.01)
    assert forme["total"]["cout_usd"] == pytest.approx(0.03)
    (t1,) = forme["taches"]
    assert t1["tache_id"] == "t1" and t1["usage"]["tokens_entree"] == 7
    json.dumps(forme)  # la forme de l'API (#57) : JSON-sérialisable de bout en bout


# --- Plafond de dépense (#56) ------------------------------------------------------------


def test_le_plafond_relit_la_comptabilite_a_chaque_verification():
    journal = RunJournal()
    plafond = PlafondDepense(journal, plafond_cout_usd=0.03)
    plafond.verifie(StepUsage(cout_usd=0.02))  # 0.02 ≤ 0.03 : rien à signaler

    _consigne(journal, usage=StepUsage(cout_usd=0.02))

    # Même mesure en cours, mais le grand livre a bougé : le contrôle le voit
    # (aucun compteur interne — la télémétrie est la source unique du coût).
    with pytest.raises(PlafondDepenseDepasse) as exc:
        plafond.verifie(StepUsage(cout_usd=0.02))
    assert "plafond de dépense dépassé" in str(exc.value)


def test_atteindre_le_plafond_sans_le_depasser_ne_stoppe_rien():
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(cout_usd=0.01))
    PlafondDepense(journal, plafond_cout_usd=0.01).verifie(StepUsage())  # ne lève pas


def test_un_cout_inconnu_n_est_pas_plafonnable():
    # Fournisseur muet sur le coût : dépense inconnue, le plafond *en USD* n'a pas
    # prise (mais le plafond en tokens, lui, plafonne — cf. tests #113 plus bas).
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(appels=3))
    PlafondDepense(journal, plafond_cout_usd=0.0001).verifie(StepUsage(appels=1))


def test_un_plafond_invalide_est_refuse():
    for invalide in (0, -1.5):
        with pytest.raises(ValueError):
            PlafondDepense(RunJournal(), invalide)


# --- Plafond en tokens : opérant sans coût rapporté (#113) ----------------------------


def test_le_plafond_en_tokens_stoppe_un_fournisseur_sans_cout_rapporte():
    # Cœur du #113 : coût inconnu (None), mais les tokens sont toujours rapportés —
    # le plafond en tokens plafonne là où le plafond en USD serait sans prise.
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(appels=1, tokens_sortie=800, cout_usd=None))
    plafond = PlafondDepense(journal, plafond_tokens=1000)

    # 800 (déjà consigné) + 150 en cours ≤ 1000 : rien à signaler.
    plafond.verifie(StepUsage(tokens_entree=150))

    # 800 + 300 > 1000 : le garde-fou stoppe, alors même que le coût reste inconnu.
    with pytest.raises(PlafondDepenseDepasse) as exc:
        plafond.verifie(StepUsage(tokens_entree=300))
    assert "plafond de tokens dépassé" in str(exc.value)


def test_les_deux_plafonds_cohabitent_le_premier_creve_stoppe():
    # Coût et tokens armés ensemble : le coût connu tranche d'abord, sinon les tokens.
    journal = RunJournal()
    _consigne(journal, usage=StepUsage(appels=1, tokens_sortie=10, cout_usd=0.05))
    plafond = PlafondDepense(journal, plafond_cout_usd=0.04, plafond_tokens=10_000)
    with pytest.raises(PlafondDepenseDepasse) as exc:
        plafond.verifie(StepUsage())
    assert "plafond de dépense dépassé" in str(exc.value)  # le coût, pas les tokens


def test_un_plafond_en_tokens_invalide_est_refuse():
    for invalide in (0, -3):
        with pytest.raises(ValueError):
            PlafondDepense(RunJournal(), plafond_tokens=invalide)


def test_un_controle_sans_aucun_plafond_est_refuse():
    with pytest.raises(ValueError):
        PlafondDepense(RunJournal())


# --- resume_controle_depense : quel contrôle a réellement tenu (#113) -----------------


def test_resume_dit_cout_reel_quand_le_fournisseur_rapporte_un_cout():
    resume = resume_controle_depense(1.0, None, StepUsage(cout_usd=0.5, tokens_sortie=42))
    assert "coût réel" in resume and "0.5000/1.0000 $" in resume


def test_resume_signale_un_plafond_de_cout_sans_prise():
    # Le cas visé par #113 : plafond en USD armé, fournisseur muet sur le coût.
    resume = resume_controle_depense(1.0, None, StepUsage(appels=1, tokens_sortie=42))
    assert "SANS PRISE" in resume
    assert "--plafond-tokens" in resume


def test_resume_annonce_le_plafond_en_tokens_actif():
    resume = resume_controle_depense(None, 1000, StepUsage(appels=1, tokens_sortie=200))
    assert "tokens (200/1000)" in resume


def test_resume_expose_le_cout_inoperant_quand_les_tokens_prennent_le_relais():
    resume = resume_controle_depense(1.0, 1000, StepUsage(appels=1, tokens_sortie=200))
    assert "tokens (200/1000)" in resume
    assert "coût inopérant" in resume


def test_resume_sans_aucun_plafond():
    assert resume_controle_depense(None, None, StepUsage()) == "aucun plafond armé"


def test_report_usage_leve_chez_l_appelant_quand_le_plafond_creve():
    # Le canal de mesure relaie le garde-fou (#9) : le signalement fautif lève,
    # mais la mesure est déjà comptabilisée — le coût du dépassement reste visible.
    plafond = PlafondDepense(RunJournal(), plafond_cout_usd=0.01)
    with collect_usage(plafond=plafond) as recolte:
        report_usage(StepUsage(appels=1, cout_usd=0.008))
        with pytest.raises(PlafondDepenseDepasse):
            report_usage(StepUsage(appels=1, cout_usd=0.008))

    assert recolte.total.appels == 2
    assert recolte.total.cout_usd == pytest.approx(0.016)
