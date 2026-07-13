"""Tests de l'intégration Langfuse (parent #79) : instrumentation et évaluation.

Aucun appel réseau sortant : le « Langfuse » des tests est un serveur HTTP local
factice qui reçoit l'API d'ingestion. Couvre les critères du lot final (#80) :

- **instrumentation** (#81, tests différés ici — docs/10 §5.1) : chaque ligne du
  journal (#8) devient une trace + une observation Langfuse (génération avec
  tokens/coût natifs #55, span sinon), les statuts portent les niveaux, et
  l'export — posé en handler logging — n'échoue jamais l'exécution qu'il
  observe ;
- **évaluation** (#80) : en fin d'exécution, des scores exploitables (réussite
  globale, taux de tâches réussies) partent sur la trace du run via la même API
  d'ingestion, dérivés de la comptabilité par tâche (#55) ;
- **mode dégradé sans Langfuse** : sans les deux clés, aucun handler n'est posé
  et aucun score n'est publié — fonctionnement strictement identique.
"""

import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2
import pytest

from maestro.config import Settings
from maestro.telemetry import (
    ETAPE_PLANIFICATION,
    LOGGER_NAME,
    RunJournal,
    StepUsage,
    activer_export_langfuse,
    evaluer_run_langfuse,
    scores_depuis_journal,
)
from maestro.telemetry.langfuse import (
    SCORE_RUN_REUSSI,
    SCORE_TAUX_REUSSITE,
    LangfuseExportHandler,
    evenements_depuis_step,
    publieur_langfuse,
)


def _settings(**surcharges) -> Settings:
    """Un `Settings` factice ; seuls les champs Langfuse importent ici."""
    defauts = dict(
        anthropic_api_key=None,
        anthropic_model="claude-opus-4-8",
        claude_auth_mode=None,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )
    defauts.update(surcharges)
    return Settings(**defauts)


def _ligne(**surcharges) -> dict:
    """Une ligne de journal (`StepRecord.to_dict`) prête à convertir."""
    ligne = {
        "run_id": "run-lf",
        "etape": "t1",
        "nom": "Tâche 1",
        "agent": "dev",
        "role": "Développeur",
        "statut": "terminee",
        "horodatage": "2026-07-13T10:00:10+00:00",
        "entree": "fais X",
        "sortie": "fait",
        "erreur": None,
        "usage": StepUsage().to_dict(),
    }
    ligne.update(surcharges)
    return ligne


def _consigne(journal: RunJournal, **surcharges):
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


# --- Conversion journal → événements d'ingestion (#81) ----------------------------------


def test_la_planification_upserte_la_trace_avec_objectif_et_horodatage():
    usage = StepUsage(appels=1, tokens_entree=100, tokens_sortie=10).avec_duree(10_000)
    ligne = _ligne(
        etape=ETAPE_PLANIFICATION, nom="Planification de l'objectif",
        agent="orchestrateur", role="Orchestrateur",
        entree="construire un mini-CRM", sortie="2 tâche(s) planifiée(s)",
        usage=usage.to_dict(),
    )

    trace, observation = evenements_depuis_step(ligne)

    assert trace["type"] == "trace-create"
    # Seule la planification pose l'entrée de la trace et son horodatage — le
    # début de l'étape, soit sa fin consignée moins la durée mesurée (#8).
    assert trace["body"]["id"] == "run-lf"
    assert trace["body"]["input"] == "construire un mini-CRM"
    assert trace["body"]["timestamp"] == "2026-07-13T10:00:00+00:00"
    assert observation["type"] == "generation-create"
    assert observation["body"]["traceId"] == "run-lf"
    # Enveloppes d'ingestion : id d'événement unique, horodatage porté.
    assert trace["id"] != observation["id"]
    assert trace["timestamp"] == "2026-07-13T10:00:10+00:00"


def test_une_tache_avec_appels_modele_devient_une_generation_chiffree():
    usage = StepUsage(
        appels=2, tokens_entree=100, tokens_sortie=25, cout_usd=0.0375, outils=("Read",)
    )
    ligne = _ligne(usage=usage.to_dict())

    trace, observation = evenements_depuis_step(ligne)

    # L'upsert de trace d'une étape ordinaire ne réécrit ni entrée ni horodatage.
    assert "input" not in trace["body"] and "timestamp" not in trace["body"]
    assert observation["type"] == "generation-create"
    corps = observation["body"]
    # Tokens et coût au format natif Langfuse (#55) : visibles par observation.
    assert corps["usage"] == {
        "input": 100, "output": 25, "unit": "TOKENS", "totalCost": 0.0375,
    }
    assert corps["name"] == "Tâche 1"
    assert corps["level"] == "DEFAULT"
    assert corps["metadata"]["agent"] == "dev"
    assert corps["metadata"]["outils"] == ["Read"]


def test_la_version_de_playbook_voyage_dans_les_metadonnees():
    # Traçabilité de la version utilisée (#78 → #75) : visible dans Langfuse aussi.
    _, observation = evenements_depuis_step(_ligne(playbook_version=3))
    assert observation["body"]["metadata"]["playbook_version"] == 3

    # Prompt du code (playbook jamais édité, ou ligne d'avant #78) : None, pas 0.
    _, sans_version = evenements_depuis_step(_ligne())
    assert sans_version["body"]["metadata"]["playbook_version"] is None


def test_une_etape_sans_appel_modele_devient_un_span():
    ligne = _ligne(etape="t1:validation", statut="approuve", usage=StepUsage().to_dict())

    _, observation = evenements_depuis_step(ligne)

    assert observation["type"] == "span-create"
    assert "usage" not in observation["body"]


def test_le_cout_inconnu_reste_absent_de_l_usage_natif():
    # Un fournisseur muet sur le coût (#55) : pas de totalCost, plutôt qu'un 0 faux.
    ligne = _ligne(usage=StepUsage(appels=1, tokens_entree=10).to_dict())

    _, observation = evenements_depuis_step(ligne)

    assert "totalCost" not in observation["body"]["usage"]


@pytest.mark.parametrize(
    ("statut", "niveau"),
    [("terminee", "DEFAULT"), ("echec", "ERROR"), ("bloquee", "WARNING"), ("refuse", "WARNING")],
)
def test_le_statut_de_l_etape_porte_le_niveau_langfuse(statut, niveau):
    erreur = "boum" if statut == "echec" else None
    _, observation = evenements_depuis_step(_ligne(statut=statut, erreur=erreur))

    assert observation["body"]["level"] == niveau
    assert observation["body"]["statusMessage"] == erreur


def test_sans_duree_ou_horodatage_illisible_l_observation_est_ponctuelle():
    sans_duree = _ligne(usage=StepUsage(appels=1).to_dict())
    _, obs = evenements_depuis_step(sans_duree)
    assert obs["body"]["startTime"] == obs["body"]["endTime"]

    illisible = _ligne(horodatage="pas-une-date", usage=StepUsage().avec_duree(500).to_dict())
    _, obs = evenements_depuis_step(illisible)
    assert obs["body"]["startTime"] == "pas-une-date"


def test_une_ligne_illisible_ne_produit_aucun_evenement():
    # L'export est un miroir du journal : une ligne étrangère ne fabrique rien.
    assert evenements_depuis_step({}) == ()
    assert evenements_depuis_step({"etape": "t1"}) == ()  # run_id absent
    assert evenements_depuis_step(_ligne(etape="")) == ()
    assert evenements_depuis_step(_ligne(run_id=42)) == ()


# --- Handler logging : le journal part vers Langfuse sans jamais le gêner ----------------


@pytest.fixture()
def logger_trace():
    """Le logger du journal, rendu au niveau INFO puis nettoyé de tout handler posé."""
    logger = logging.getLogger(LOGGER_NAME)
    handlers_avant = list(logger.handlers)
    logger.setLevel(logging.INFO)
    try:
        yield logger
    finally:
        for handler in list(logger.handlers):
            if handler not in handlers_avant:
                logger.removeHandler(handler)


def test_le_handler_publie_chaque_etape_consignee(logger_trace):
    lots: list[list[dict]] = []
    logger_trace.addHandler(LangfuseExportHandler(lambda evts: lots.append(list(evts))))

    journal = RunJournal(run_id="run-handler")
    _consigne(journal, usage=StepUsage(appels=1))
    _consigne(journal, etape="t2", statut="echec", erreur="boum")

    assert len(lots) == 2  # un lot (trace + observation) par étape consignée
    assert [e["type"] for e in lots[0]] == ["trace-create", "generation-create"]
    assert [e["type"] for e in lots[1]] == ["trace-create", "span-create"]
    # Tout se rattache à la trace du run : par `traceId` (observations) ou `id` (trace).
    assert all(e["body"].get("traceId", e["body"].get("id")) == "run-handler"
               for lot in lots for e in lot)


def test_une_ligne_non_journal_est_ignoree_sans_publication(logger_trace, monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", False)  # handleError silencieux
    lots: list[list[dict]] = []
    logger_trace.addHandler(LangfuseExportHandler(lambda evts: lots.append(list(evts))))

    logger_trace.info("pas du JSON")  # illisible : signalée via handleError, sans lever
    logger_trace.info('"du JSON, mais pas une étape"')

    assert lots == []


def test_un_echec_de_publication_ne_casse_pas_la_consignation(logger_trace, monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", False)

    def _publier_en_panne(evenements):
        raise ConnectionError("Langfuse injoignable")

    logger_trace.addHandler(LangfuseExportHandler(_publier_en_panne))
    journal = RunJournal()

    record = _consigne(journal)  # ne doit pas lever : l'export n'échoue jamais le run

    assert journal.records == (record,)


# --- Publication HTTP et bascule configurative -------------------------------------------


class _IngestionHandler(BaseHTTPRequestHandler):
    """Reçoit l'API d'ingestion Langfuse et archive chaque requête pour inspection."""

    def do_POST(self):
        longueur = int(self.headers.get("Content-Length", 0))
        corps = json.loads(self.rfile.read(longueur) or b"{}")
        self.server.requetes.append(
            {
                "chemin": self.path,
                "corps": corps,
                "autorisation": self.headers.get("Authorization"),
            }
        )
        # 207 : la réponse multi-statut nominale de l'ingestion Langfuse.
        brut = json.dumps({"successes": [], "errors": []}).encode("utf-8")
        self.send_response(self.server.statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(brut)))
        self.end_headers()
        self.wfile.write(brut)

    def log_message(self, *args):  # silencieux : pas de bruit dans la sortie des tests
        pass


@pytest.fixture()
def langfuse_local():
    """Langfuse factice sur un port libre ; `host` pointe dessus, `requetes` archive."""
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), _IngestionHandler)
    serveur.requetes = []
    serveur.statut = 207
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    serveur.host = f"http://127.0.0.1:{serveur.server_address[1]}"
    try:
        yield serveur
    finally:
        serveur.shutdown()
        serveur.server_close()


def test_le_publieur_poste_le_lot_authentifie_sur_l_api_d_ingestion(langfuse_local):
    settings = _settings(
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_host=langfuse_local.host + "/",  # le / final ne double pas le chemin
    )

    publieur_langfuse(settings)([{"type": "trace-create"}, {"type": "span-create"}])

    (requete,) = langfuse_local.requetes
    assert requete["chemin"] == "/api/public/ingestion"
    assert [e["type"] for e in requete["corps"]["batch"]] == ["trace-create", "span-create"]
    attendu = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    assert requete["autorisation"] == f"Basic {attendu}"


def test_le_publieur_leve_sur_une_reponse_en_erreur(langfuse_local):
    langfuse_local.statut = 500
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=langfuse_local.host
    )

    with pytest.raises(httpx2.HTTPStatusError):
        publieur_langfuse(settings)([{"type": "trace-create"}])


def test_sans_cles_aucun_handler_n_est_pose(logger_trace):
    # Mode dégradé : sans les deux clés, l'activation ne branche rien du tout.
    handlers_avant = list(logger_trace.handlers)

    assert activer_export_langfuse(_settings()) is None
    assert activer_export_langfuse(_settings(langfuse_public_key="pk")) is None
    assert activer_export_langfuse(_settings(langfuse_secret_key="sk")) is None
    assert logger_trace.handlers == handlers_avant


def test_avec_les_deux_cles_le_handler_est_pose_et_retirable(logger_trace):
    handler = activer_export_langfuse(_settings(langfuse_public_key="pk",
                                                langfuse_secret_key="sk"))

    assert handler is not None
    assert handler in logger_trace.handlers
    logger_trace.removeHandler(handler)
    assert handler not in logger_trace.handlers


# --- Évaluation des exécutions (#80) ------------------------------------------------------


def test_un_run_tout_reussi_score_1_sur_sa_trace():
    journal = RunJournal(run_id="run-eval")
    _consigne(journal, etape=ETAPE_PLANIFICATION, agent="orchestrateur", role="Orchestrateur")
    _consigne(journal, etape="t1")
    _consigne(journal, etape="t2", agent="qa", role="QA / Testeur")

    reussi, taux = scores_depuis_journal(journal)

    assert reussi["type"] == "score-create" and taux["type"] == "score-create"
    assert reussi["body"]["traceId"] == "run-eval"
    assert reussi["body"]["name"] == SCORE_RUN_REUSSI
    assert reussi["body"]["value"] == 1
    assert reussi["body"]["dataType"] == "BOOLEAN"
    assert taux["body"]["name"] == SCORE_TAUX_REUSSITE
    assert taux["body"]["value"] == 1.0
    assert taux["body"]["dataType"] == "NUMERIC"
    assert "2/2 tâche(s) réussie(s)" in taux["body"]["comment"]


def test_un_run_avec_echec_et_blocage_score_0_et_le_taux_reel():
    journal = RunJournal()
    _consigne(journal, etape="t1")
    _consigne(journal, etape="t2", statut="echec", erreur="boum")
    _consigne(journal, etape="t3", statut="bloquee", erreur="dépendance t2 en échec")

    reussi, taux = scores_depuis_journal(journal)

    assert reussi["body"]["value"] == 0
    assert taux["body"]["value"] == pytest.approx(1 / 3)
    assert "1 échec(s), 1 bloquée(s)" in reussi["body"]["comment"]


def test_les_annexes_ne_comptent_pas_comme_taches():
    # L'issue d'une tâche vient de sa propre étape ; ses annexes (#55) n'en créent pas.
    journal = RunJournal()
    _consigne(journal, etape="t1")
    _consigne(journal, etape="t1:validation", statut="approuve")

    reussi, taux = scores_depuis_journal(journal)

    assert reussi["body"]["value"] == 1
    assert "1/1 tâche(s) réussie(s)" in taux["body"]["comment"]


def test_sans_tache_rien_a_evaluer():
    # Aucun score plutôt qu'un score faux : journal vide, ou planification seule
    # (échouée avant tout plan — l'exécution n'a pas eu lieu).
    assert scores_depuis_journal(RunJournal()) == ()

    journal = RunJournal()
    _consigne(journal, etape=ETAPE_PLANIFICATION, statut="echec", erreur="plan illisible")
    assert scores_depuis_journal(journal) == ()


def test_evaluer_est_un_no_op_sans_cles():
    journal = RunJournal()
    _consigne(journal)

    # Mode dégradé : rien n'est publié, rien ne part sur le réseau.
    assert evaluer_run_langfuse(journal, _settings()) == ()


def test_evaluer_publie_les_scores_sur_l_api_d_ingestion(langfuse_local):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=langfuse_local.host
    )
    journal = RunJournal(run_id="run-publie")
    _consigne(journal)

    publies = evaluer_run_langfuse(journal, settings)

    (requete,) = langfuse_local.requetes
    lot = requete["corps"]["batch"]
    assert [e["body"]["name"] for e in lot] == [SCORE_RUN_REUSSI, SCORE_TAUX_REUSSITE]
    assert all(e["body"]["traceId"] == "run-publie" for e in lot)
    assert len(publies) == 2


def test_un_langfuse_injoignable_n_affecte_pas_l_execution_evaluee(caplog):
    # Résilience : la publication échoue (port fermé), l'évaluation l'avale et le signale.
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host="http://127.0.0.1:9"
    )
    journal = RunJournal()
    _consigne(journal)

    with caplog.at_level(logging.WARNING, logger="maestro.telemetry.langfuse"):
        assert evaluer_run_langfuse(journal, settings) == ()

    assert "Scores Langfuse non publiés" in caplog.text


def test_sans_rien_a_evaluer_aucune_publication_meme_avec_cles(langfuse_local):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=langfuse_local.host
    )

    assert evaluer_run_langfuse(RunJournal(), settings) == ()
    assert langfuse_local.requetes == []


# --- De bout en bout : traces au fil des étapes, scores en fin de run --------------------


def test_de_bout_en_bout_traces_puis_scores_sur_la_meme_trace(logger_trace, langfuse_local):
    settings = _settings(
        langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host=langfuse_local.host
    )
    handler = activer_export_langfuse(settings)
    try:
        journal = RunJournal(run_id="run-complet")
        _consigne(journal, etape=ETAPE_PLANIFICATION, agent="orchestrateur",
                  role="Orchestrateur", usage=StepUsage(appels=1, cout_usd=0.01))
        _consigne(journal, etape="t1", usage=StepUsage(appels=1, cout_usd=0.02))
        evaluer_run_langfuse(journal, settings)
    finally:
        logger_trace.removeHandler(handler)

    # Un lot par étape consignée (#81), puis le lot des scores (#80).
    types = [
        [e["type"] for e in requete["corps"]["batch"]] for requete in langfuse_local.requetes
    ]
    assert types == [
        ["trace-create", "generation-create"],
        ["trace-create", "generation-create"],
        ["score-create", "score-create"],
    ]
    # Tout se recoupe sur la même trace : celle du run_id.
    dernier = langfuse_local.requetes[-1]["corps"]["batch"]
    assert all(e["body"]["traceId"] == "run-complet" for e in dernier)
