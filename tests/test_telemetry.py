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

Et, depuis #989, **le temps** que ces deux objets rapportent :

- la durée d'une tâche est son **temps de travail** — l'horloge moins ses
  attentes (arbitrage #584, créneau d'agent #86, atelier de projet #839), qui se
  lisent à part et gardent chacune son nom ;
- la durée d'un **run** est l'**union** des intervalles de ses étapes, jamais
  leur somme : deux tâches menées de front ne l'occupent qu'une fois.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

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
    intervalle_depuis,
    redact_secrets,
    report_usage,
    resume_controle_depense,
    union_ms,
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


def test_redact_masque_un_bloc_de_cle_privee_entier():
    # Une clé SSH ou TLS ne se reconnaît pas à un préfixe mais à son bloc : depuis
    # que la lecture des sources se fait par le contenu (#1163), un `id_rsa` sans
    # extension entre comme du texte, et c'est ce motif qui l'arrête (#1264).
    cle = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAsecret1264\nligne2secrete\n"
        "-----END RSA PRIVATE KEY-----"
    )
    texte = redact_secrets(f"avant\n{cle}\naprès")
    assert "secret1264" not in texte
    assert "ligne2secrete" not in texte
    assert texte == f"avant\n{MARQUEUR_SECRET}\naprès"


def test_redact_masque_une_cle_privee_coupee_avant_sa_fin():
    # Une lecture tronquée peut couper le bloc avant sa ligne `END` : ce qui suit
    # l'en-tête est masqué jusqu'au bout plutôt que laissé en clair.
    texte = redact_secrets("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE-coupee")
    assert "b3BlbnNzaC1rZXktdjE" not in texte
    assert MARQUEUR_SECRET in texte


def test_redact_laisse_un_certificat_et_une_cle_publique_intacts():
    # Un certificat ou une clé publique n'est pas un secret : les masquer ferait
    # disparaître du contexte ce qu'un livrable publie à dessein.
    public = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYI\n-----END PUBLIC KEY-----"
    certificat = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmg\n-----END CERTIFICATE-----"
    assert redact_secrets(public) == public
    assert redact_secrets(certificat) == certificat


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


# --- #989 : la durée d'une tâche est son temps de travail ------------------------------


def test_la_duree_de_travail_retire_les_trois_attentes():
    # Le cas du retex du 2026-09-11 (G4), chiffres compris : 24 min 17 s
    # annoncées, dont 13 min à attendre son tour — il reste ~11 min de travail.
    usage = StepUsage().avec_duree(
        1_457_000,
        arbitrage_ms=12_000,
        attente_creneau_ms=48_000,
        attente_atelier_ms=780_000,
    )

    assert usage.duree_attente_ms == 840_000
    assert usage.duree_execution_ms == 617_000


def test_une_attente_non_mesuree_ne_vaut_pas_zero():
    # La distinction de `cout_usd`, et elle vaut pour la même raison : une
    # mesure absente n'est pas une mesure nulle. Un appelant qui ne mesure
    # aucune attente (files, boucle de planification) ne déclare pas qu'il n'y
    # en a pas eu, et sa durée de travail reste son horloge.
    sans_mesure = StepUsage().avec_duree(5_000)
    assert sans_mesure.duree_attente_ms is None
    assert sans_mesure.duree_execution_ms == 5_000

    # Mesurée à zéro, en revanche, l'attente est un fait : on l'a regardée.
    mesuree = StepUsage().avec_duree(5_000, attente_creneau_ms=0, attente_atelier_ms=0)
    assert mesuree.duree_attente_ms == 0
    assert mesuree.duree_execution_ms == 5_000


def test_la_duree_de_travail_ne_devient_jamais_negative():
    # Les mesures viennent d'horloges qui ne partent pas au même instant :
    # bornée à zéro plutôt que de rendre une durée négative.
    usage = StepUsage().avec_duree(1_000, attente_atelier_ms=4_000)
    assert usage.duree_execution_ms == 0


def test_les_attentes_voyagent_en_json_avec_leurs_derivees():
    usage = StepUsage().avec_duree(
        10_000, arbitrage_ms=1_000, attente_creneau_ms=2_000, attente_atelier_ms=3_000
    )
    forme = usage.to_dict()

    assert forme["duree_attente_creneau_ms"] == 2_000
    assert forme["duree_attente_atelier_ms"] == 3_000
    # Les dérivées voyagent calculées, comme `tokens_total` : l'écran ne
    # réécrit pas la règle « ce qui est du travail, ce qui est de l'attente ».
    assert forme["duree_attente_ms"] == 6_000
    assert forme["duree_execution_ms"] == 4_000
    # …et sont ignorées au retour : l'aller-retour reste fidèle.
    assert StepUsage.from_dict(forme) == usage


def test_une_ligne_de_journal_d_avant_989_se_relit_sans_attente_mesuree():
    # Inconnu, pas zéro : rien ne permet après coup de dire ce qu'une tâche a
    # attendu, et sa durée de travail reste donc son horloge.
    relu = StepUsage.from_dict({"duree_ms": 4_000})
    assert relu.duree_attente_creneau_ms is None
    assert relu.duree_attente_ms is None
    assert relu.duree_execution_ms == 4_000


def test_le_resume_court_nomme_chaque_attente_et_tait_les_nulles():
    resume = StepUsage(appels=1).avec_duree(
        20_000, arbitrage_ms=0, attente_creneau_ms=0, attente_atelier_ms=8_000
    ).resume_court()

    assert "d'atelier de projet" in resume
    # Les deux attentes nulles ne sont pas annoncées : « dont 0,0 s » sur
    # chacune des tâches d'un run apprendrait à ne plus lire la mention (#584).
    assert "d'arbitrage" not in resume
    assert "de file d'agent" not in resume


# --- #989 : la durée d'un run est l'union de ses intervalles ---------------------------


def _intervalle(debut_s: float, fin_s: float) -> tuple[datetime, datetime]:
    """Un intervalle en secondes depuis une origine fixe — de quoi lire le test."""
    origine = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    return (origine + timedelta(seconds=debut_s), origine + timedelta(seconds=fin_s))


def test_l_union_ne_compte_pas_deux_fois_un_recouvrement():
    # L'erreur que le produit répétait et que l'outillage avait corrigée (#497) :
    # 47 min annoncées pour 43,5 min de mur. Deux tâches de 60 s qui se
    # recouvrent de 30 s occupent 90 s, pas 120.
    assert union_ms([_intervalle(0, 60), _intervalle(30, 90)]) == 90_000


def test_l_union_additionne_les_intervalles_disjoints():
    # L'autre moitié : sans recouvrement, l'union **est** la somme — le trou
    # entre les deux n'est pas du temps occupé.
    assert union_ms([_intervalle(0, 10), _intervalle(40, 50)]) == 20_000


def test_l_union_absorbe_un_intervalle_entierement_contenu():
    # Une tâche courte pendant une longue n'ajoute rien, et ne raccourcit rien.
    assert union_ms([_intervalle(0, 100), _intervalle(20, 30)]) == 100_000


def test_l_union_ignore_l_ordre_dans_lequel_les_etapes_sont_consignees():
    # `RunJournal.records` suit l'ordre d'**achèvement** : des tâches menées de
    # front y arrivent dans le désordre de leurs débuts.
    desordre = [_intervalle(30, 90), _intervalle(0, 60)]
    assert union_ms(desordre) == 90_000


def test_l_union_sans_intervalle_est_inconnue_et_non_nulle():
    assert union_ms([]) is None


def test_un_intervalle_se_deduit_de_l_horodatage_et_de_la_duree():
    debut, fin = intervalle_depuis("2026-09-20T10:00:40+00:00", 40_000)
    assert fin - debut == timedelta(seconds=40)
    assert debut == datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "horodatage, duree_ms",
    [
        ("2026-09-20T10:00:40+00:00", None),  # rien de mesuré
        ("2026-09-20T10:00:40+00:00", 0),  # durée nulle : aucun temps occupé
        ("2026-09-20T10:00:40+00:00", -5),  # horloges désaccordées
        ("pas une date", 40_000),  # horodatage illisible
        ("", 40_000),  # horodatage absent
    ],
)
def test_une_etape_sans_intervalle_lisible_ne_fausse_pas_l_union(horodatage, duree_ms):
    assert intervalle_depuis(horodatage, duree_ms) is None


def test_le_grand_livre_rend_l_union_et_non_la_somme_des_durees():
    # De bout en bout, sur un vrai journal : deux tâches d'une minute
    # consignées coup sur coup se recouvrent presque entièrement. La somme
    # annoncerait deux minutes ; l'occupation du run en vaut une.
    journal = RunJournal()
    _consigne(journal, etape="t1", usage=StepUsage(appels=1).avec_duree(60_000))
    _consigne(journal, etape="t2", usage=StepUsage(appels=1).avec_duree(60_000))
    cout = RunCost.depuis_journal(journal)

    # Les compteurs, eux, se somment bien : deux tâches de front coûtent deux fois.
    assert cout.total.appels == 2
    # La durée, non. Une seconde de marge : les deux consignations peuvent
    # tomber de part et d'autre d'un tic d'horloge (horodatage à la seconde).
    assert cout.duree_mur_ms is not None
    assert 60_000 <= cout.duree_mur_ms <= 61_000
    assert cout.total.duree_ms == cout.duree_mur_ms


def test_le_total_du_run_ne_porte_aucune_attente():
    # Une somme d'attentes de tâches parallèles n'est pas une attente du run,
    # et la retrancher d'une union donnerait un « travail du run » qui ne veut
    # rien dire. Au niveau du run, la durée est une seule mesure.
    journal = RunJournal()
    _consigne(
        journal,
        etape="t1",
        usage=StepUsage(appels=1).avec_duree(60_000, attente_atelier_ms=30_000),
    )
    total = RunCost.depuis_journal(journal).total

    assert total.duree_attente_ms is None
    assert total.duree_execution_ms == total.duree_ms


def test_une_etape_de_reprise_n_occupe_pas_le_run():
    # Marqueur de run (#96), rattaché à aucune tâche : il ne compte pas au grand
    # livre, il n'occupe donc rien non plus.
    journal = RunJournal()
    _consigne(journal, etape="reprise", usage=StepUsage().avec_duree(90_000))
    assert RunCost.depuis_journal(journal).duree_mur_ms is None
