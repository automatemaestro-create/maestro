"""Tests de la relance automatique des échecs transitoires (ticket #91, ENF-06).

Aucun appel réseau : la planification et l'exécution sont pilotées par des
`ModelProvider` factices (backoff à 0 pour des tests instantanés). Couvre les
critères d'acceptation du ticket #91 :

① la **classification** : un aléa fournisseur (exception quelconque de la
   réalisation) est transitoire ; plafond de coût (`PlafondDepenseDepasse`),
   plafond de tours (`TurnLimitReached`) et capacité absente
   (`UnsupportedCapability`) ne le sont pas — et time-out d'échéance ferme
   comme refus de validation humaine ne sont **jamais relancés** ;
② le **succès à la 2e tentative** : la tâche aboutit, la relance est tracée
   (journal, étape `<tache>:relance`, raison portée) et le grand livre agrège
   coût/tokens de **toutes** les tentatives ;
③ l'**épuisement des tentatives** : échec propre (cause et nombre de tentatives
   dans l'erreur), l'aval est bloqué avec raison, le rapport est honnête.
"""

import asyncio
import inspect
import json

import pytest

from maestro.engine import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    OrchestrationEngine,
    PolitiqueRelance,
)
from maestro.engine.executor import SUFFIXE_ETAPE_RELANCE
from maestro.engine.guardrails import Guardrails
from maestro.engine.retry import est_transitoire
from maestro.orchestrator import Orchestrator
from maestro.providers.base import (
    ModelProvider,
    TurnLimitReached,
    UnsupportedCapability,
)
from maestro.telemetry import (
    PlafondDepenseDepasse,
    RunJournal,
    StepUsage,
    report_usage,
)


class ConstantProvider(ModelProvider):
    """Planificateur factice : renvoie toujours le même plan JSON."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class FlakyProvider(ModelProvider):
    """Exécutant factice : échoue (transitoirement) `pannes` fois, puis livre.

    Reproduit l'aléa fournisseur mesuré en démo V1 (docs/13 §4.3) : erreur
    immédiate du sous-processus SDK, sans usage rapporté (0 $).
    """

    name = "flaky"

    def __init__(self, pannes: int, *, usage_par_appel: StepUsage | None = None) -> None:
        self._pannes = pannes
        self._usage = usage_par_appel
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        if self._usage is not None:
            report_usage(self._usage)
        if self.appels <= self._pannes:
            raise RuntimeError("aléa SDK simulé (crash du sous-processus).")
        return "LIVRABLE après relance"


class ErreurFixeProvider(ModelProvider):
    """Exécutant factice : lève toujours la même exception (transitoire ou non)."""

    name = "erreur-fixe"

    def __init__(self, erreur: Exception) -> None:
        self._erreur = erreur
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        raise self._erreur


class LentProvider(ModelProvider):
    """Exécutant factice trop lent : sert à déclencher le time-out (#64)."""

    name = "lent"

    def __init__(self, delai_s: float) -> None:
        self._delai_s = delai_s
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        await asyncio.sleep(self._delai_s)
        return "trop tard"


def _tache(id: str, *, dependances: tuple[str, ...] = (), description: str = "") -> dict:
    return {
        "id": id,
        "titre": f"Tâche {id}",
        "description": description or f"Réaliser la tâche {id}.",
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _plan_json(*taches: dict) -> str:
    return json.dumps(list(taches) or [_tache("t1")], ensure_ascii=False)


def _engine(
    *,
    exec_provider: ModelProvider,
    plan_json: str | None = None,
    relance: PolitiqueRelance | None = None,
    guardrails: Guardrails | None = None,
) -> OrchestrationEngine:
    planner = ConstantProvider(plan_json or _plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    return OrchestrationEngine(
        exec_provider, orchestrator, relance=relance, guardrails=guardrails
    )


def _relances(journal: RunJournal):
    """Les étapes de relance consignées au journal, dans l'ordre."""
    return [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_RELANCE)]


# --- Critère ① : classification transitoire / non transitoire --------------------------


def test_la_classification_distingue_transitoire_et_non_transitoire():
    # Aléa fournisseur (exception quelconque de la réalisation) : transitoire.
    assert est_transitoire(RuntimeError("error result: success"))
    assert est_transitoire(ConnectionError("coupure réseau"))
    # Causes déterministes : jamais relancées.
    assert not est_transitoire(PlafondDepenseDepasse("plafond de dépense dépassé"))
    assert not est_transitoire(TurnLimitReached("plafond de tours atteint"))
    assert not est_transitoire(UnsupportedCapability("pas d'exécution outillée"))


def test_la_politique_valide_ses_bornes_et_deroule_son_backoff():
    with pytest.raises(ValueError):
        PolitiqueRelance(max_tentatives=0)
    with pytest.raises(ValueError):
        PolitiqueRelance(backoff_s=-1)
    with pytest.raises(ValueError):
        PolitiqueRelance(facteur=0.5)
    politique = PolitiqueRelance(max_tentatives=4, backoff_s=1.0, facteur=2.0)
    assert politique.attente_s(1) == pytest.approx(1.0)
    assert politique.attente_s(2) == pytest.approx(2.0)
    assert politique.attente_s(3) == pytest.approx(4.0)


def test_un_plafond_de_tours_n_est_jamais_relance():
    provider = ErreurFixeProvider(TurnLimitReached("plafond de tours atteint (max_turns)."))
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider, relance=PolitiqueRelance(max_tentatives=3, backoff_s=0)
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    assert "plafond de tours" in (resultat.erreur or "")
    assert provider.appels == 1
    assert _relances(journal) == []


def test_un_depassement_du_plafond_de_cout_n_est_jamais_relance():
    # Le fournisseur rapporte une dépense au-delà du plafond : le garde-fou lève
    # PlafondDepenseDepasse pendant l'appel — échec immédiat, aucune relance.
    provider = FlakyProvider(
        pannes=99, usage_par_appel=StepUsage(appels=1, cout_usd=0.5)
    )
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider,
        relance=PolitiqueRelance(max_tentatives=3, backoff_s=0),
        guardrails=Guardrails(plafond_cout_usd=0.1),
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    assert "plafond de dépense" in (resultat.erreur or "")
    assert provider.appels == 1
    assert _relances(journal) == []


def test_un_refus_de_validation_humaine_n_est_jamais_relance():
    # Tâche sensible refusée : stoppée avant toute exécution, un seul passage
    # devant le validateur, aucune relance.
    decisions: list[object] = []

    def validateur(demande):
        decisions.append(demande)
        return False

    provider = FlakyProvider(pannes=0)
    journal = RunJournal()
    plan = _plan_json(_tache("deploiement", description="Déployer l'API en production."))
    engine = _engine(
        exec_provider=provider,
        plan_json=plan,
        relance=PolitiqueRelance(max_tentatives=3, backoff_s=0),
        guardrails=Guardrails(validateur=validateur),
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    assert "action sensible" in (resultat.erreur or "")
    assert len(decisions) == 1
    assert provider.appels == 0
    assert _relances(journal) == []


def test_le_timeout_d_echeance_ferme_n_est_jamais_relance():
    # L'échéance ferme (#64) borne la réalisation relances comprises : à
    # l'échéance, l'échec est consigné sans nouvelle tentative.
    provider = LentProvider(delai_s=5.0)
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider,
        relance=PolitiqueRelance(max_tentatives=3, backoff_s=0),
        guardrails=Guardrails(timeout_s=0.2),
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    assert "time-out" in (resultat.erreur or "")
    assert provider.appels == 1
    assert _relances(journal) == []


# --- Critère ② : succès à la 2e tentative, relance tracée, usage agrégé ----------------


def test_un_echec_transitoire_est_relance_et_reussit_a_la_2e_tentative():
    provider = FlakyProvider(pannes=1)
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider, relance=PolitiqueRelance(max_tentatives=3, backoff_s=0)
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.ok
    assert resultat.sortie == "LIVRABLE après relance"
    assert provider.appels == 2

    # La relance est tracée au journal avec sa raison — c'est ce que le pont
    # Control Tower rediffuse au fil temps réel.
    (relance,) = _relances(journal)
    assert relance.etape == f"{resultat.task_id}{SUFFIXE_ETAPE_RELANCE}"
    assert relance.statut == "relance"
    assert "aléa SDK simulé" in relance.entree
    assert "tentative 1/3" in relance.sortie
    assert relance.agent == resultat.agent


def test_le_grand_livre_agrege_l_usage_de_toutes_les_tentatives():
    # Chaque tentative rapporte son usage : la tâche (donc le grand livre)
    # additionne les deux appels — l'échec transitoire n'est pas « gratuit ».
    provider = FlakyProvider(
        pannes=1,
        usage_par_appel=StepUsage(appels=1, tokens_entree=10, tokens_sortie=5, cout_usd=0.01),
    )
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider, relance=PolitiqueRelance(max_tentatives=3, backoff_s=0)
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.ok
    assert resultat.usage.appels == 2
    assert resultat.usage.tokens_entree == 20
    assert resultat.usage.cout_usd == pytest.approx(0.02)
    # L'étape de relance porte un usage nul : pas de double compte au journal.
    (relance,) = _relances(journal)
    assert relance.usage == StepUsage()
    assert journal.usage_totale.cout_usd == pytest.approx(0.02)


def test_une_reponse_vide_est_un_echec_transitoire_relance():
    class VideUneFoisProvider(ModelProvider):
        name = "vide-une-fois"

        def __init__(self) -> None:
            self.appels = 0

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            self.appels += 1
            return "" if self.appels == 1 else "LIVRABLE"

    provider = VideUneFoisProvider()
    journal = RunJournal()
    engine = _engine(
        exec_provider=provider, relance=PolitiqueRelance(max_tentatives=2, backoff_s=0)
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.ok
    assert provider.appels == 2
    (relance,) = _relances(journal)
    assert "réponse vide" in relance.entree


# --- Critère ③ : épuisement des tentatives → échec propre, aval bloqué -----------------


def test_l_epuisement_des_tentatives_donne_un_echec_propre_et_bloque_l_aval():
    provider = FlakyProvider(pannes=99)
    journal = RunJournal()
    plan = _plan_json(_tache("t1"), _tache("t2", dependances=("t1",)))
    engine = _engine(
        exec_provider=provider,
        plan_json=plan,
        relance=PolitiqueRelance(max_tentatives=2, backoff_s=0),
    )
    report = asyncio.run(engine.run("Objectif", journal=journal))

    racine, aval = report.resultats
    # Échec propre : la cause d'origine et l'épuisement sont dans l'erreur.
    assert racine.statut == STATUT_ECHEC
    assert "aléa SDK simulé" in (racine.erreur or "")
    assert "2 tentatives" in (racine.erreur or "")
    assert provider.appels == 2
    # Une seule relance tracée (la 2e tentative), avec sa raison.
    (relance,) = _relances(journal)
    assert "tentative 1/2" in relance.sortie
    # Aval bloqué avec raison — rapport honnête.
    assert aval.statut == STATUT_BLOQUEE
    assert "t1" in (aval.erreur or "")
    assert report.echouees == (racine,)
    assert report.bloquees == (aval,)


def test_sans_politique_le_comportement_historique_est_inchange():
    # `relance=None` (défaut du constructeur) : un seul essai, message d'origine.
    provider = FlakyProvider(pannes=99)
    journal = RunJournal()
    engine = _engine(exec_provider=provider, relance=None)
    report = asyncio.run(engine.run("Objectif", journal=journal))

    (resultat,) = report.resultats
    assert resultat.statut == STATUT_ECHEC
    assert resultat.erreur == "aléa SDK simulé (crash du sous-processus)."
    assert provider.appels == 1
    assert _relances(journal) == []


def test_la_relance_est_armee_par_defaut_sur_le_moteur_par_defaut():
    # `OrchestrationEngine.default()` — celui des vrais runs (maestro-run, démo) —
    # arme la politique par défaut ; le constructeur direct reste historique (None).
    parametres = inspect.signature(OrchestrationEngine.default).parameters
    assert parametres["relance"].default == PolitiqueRelance()
    parametres_init = inspect.signature(OrchestrationEngine.__init__).parameters
    assert parametres_init["relance"].default is None
