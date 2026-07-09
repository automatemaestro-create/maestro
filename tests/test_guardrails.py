"""Tests des garde-fous du POC (ticket #9) : plafond, time-out, validation humaine.

Aucun appel réseau : planification et exécution sont pilotées par des
`ModelProvider` factices, comme dans test_engine. Couvre les critères
d'acceptation du ticket #9 :

① une tâche dépassant le **plafond de dépense** est stoppée (y compris en cours de
   route : le travail postérieur au dépassement n'a pas lieu), son coût restant
   visible ; sous le plafond, rien ne change ;
② une tâche dépassant le **time-out** est stoppée, sans gêner les autres tâches ;
③ une **action sensible** déclenche une demande de validation humaine avant toute
   exécution : approuvée elle s'exécute, refusée elle est stoppée sans avoir rien
   lancé — et sans validateur configuré (ou validateur en panne), le refus est le
   défaut (fail-safe). La demande et la décision sont consignées au journal.
"""

import asyncio
import json

import pytest

from maestro.engine import DemandeValidation, Guardrails, OrchestrationEngine
from maestro.engine.guardrails import _normalise  # test ciblé de la normalisation
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage, report_usage


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur ou d'exécutant simple)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class RecordingProvider(ModelProvider):
    """Exécutant factice : enregistre chaque appel et renvoie un livrable unique."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model})
        return f"LIVRABLE #{len(self.calls)}"


def _tache(id, titre, description, competences, dependances=()):
    return {
        "id": id,
        "titre": titre,
        "description": description,
        "competences_requises": list(competences),
        "format_sortie": "Note",
        "dependances": list(dependances),
    }


def _plan_anodin():
    # Une seule tâche, sans mot sensible, routée vers qa.
    return json.dumps(
        [_tache("tests-api", "Tests de l'API", "Tests d'intégration.", ["tests"])],
        ensure_ascii=False,
    )


def _plan_sensible():
    # Une tâche de déploiement (sensible), routée vers devops — rôle sans runtime
    # outillé : l'exécution passe par generate, observable sur le fournisseur.
    return json.dumps(
        [
            _tache(
                "deploiement",
                "Déployer l'API",
                "Mettre la nouvelle API en production.",
                ["deploy"],
            )
        ],
        ensure_ascii=False,
    )


def _engine(*, exec_provider=None, plan_json=None, guardrails=None):
    planner = ConstantProvider(plan_json or _plan_anodin())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(execu, orchestrator, guardrails=guardrails)


# --- Critère ① : plafond de dépense par tâche ------------------------------------------


class DepensierProvider(ModelProvider):
    """Signale 0.006 $ deux fois par appel : le *cumul* (0.012 $) teste le plafond.

    `acheves` ne s'incrémente qu'après les signalements : s'il reste à zéro, la
    tâche a bien été stoppée *en cours de route* par le garde-fou, pas après coup.
    """

    name = "depensier"

    def __init__(self) -> None:
        self.acheves = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        report_usage(StepUsage(appels=1, cout_usd=0.006))
        self.acheves += 1
        return "LIVRABLE coûteux"


def test_le_plafond_de_depense_stoppe_la_tache_en_cours():
    provider = DepensierProvider()
    guardrails = Guardrails(plafond_cout_usd=0.01)
    report = asyncio.run(_engine(exec_provider=provider, guardrails=guardrails).run("Objectif"))

    tache = report.resultats[0]
    assert tache.statut == "echec"
    assert "plafond de dépense dépassé" in (tache.erreur or "")
    # Stoppée entre les deux signalements : le travail postérieur n'a pas eu lieu…
    assert provider.acheves == 0
    # …mais le coût déjà engagé reste visible sur la tâche.
    assert tache.usage.cout_usd == pytest.approx(0.012)


def test_sous_le_plafond_la_tache_s_execute_normalement():
    provider = DepensierProvider()
    guardrails = Guardrails(plafond_cout_usd=1.0)
    report = asyncio.run(_engine(exec_provider=provider, guardrails=guardrails).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert provider.acheves == 1
    assert report.usage_totale.cout_usd == pytest.approx(0.012)


# --- Critère ② : time-out par tâche -----------------------------------------------------


class LentProvider(ModelProvider):
    """Temporise sur la tâche « Schéma BDD » ; répond immédiatement sinon."""

    name = "lent"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if "Schéma BDD" in prompt:
            await asyncio.sleep(30)
        return "LIVRABLE"


def test_le_timeout_stoppe_la_tache_trop_longue_sans_gener_les_autres():
    plan = json.dumps(
        [
            _tache("schema-bdd", "Schéma BDD", "Définir le schéma.", ["sql"]),
            _tache("tests-api", "Tests de l'API", "Tests d'intégration.", ["tests"]),
        ],
        ensure_ascii=False,
    )
    guardrails = Guardrails(timeout_s=0.2)
    report = asyncio.run(
        _engine(exec_provider=LentProvider(), plan_json=plan, guardrails=guardrails).run(
            "Objectif"
        )
    )

    lente, rapide = report.resultats
    assert lente.statut == "echec"
    assert "time-out" in (lente.erreur or "")
    assert lente.usage.duree_ms is not None  # la durée de la tâche stoppée reste mesurée
    assert rapide.ok  # la boucle continue : l'autre tâche aboutit


def test_une_tache_dans_les_temps_n_est_pas_stoppee():
    guardrails = Guardrails(timeout_s=30)
    report = asyncio.run(_engine(guardrails=guardrails).run("Objectif"))

    assert all(r.ok for r in report.resultats)


# --- Critère ③ : validation humaine des actions sensibles -------------------------------


class ValidateurEnregistreur:
    """Validateur factice : enregistre chaque demande et rend la décision configurée."""

    def __init__(self, decision: bool) -> None:
        self._decision = decision
        self.demandes: list[DemandeValidation] = []

    def __call__(self, demande: DemandeValidation) -> bool:
        self.demandes.append(demande)
        return self._decision


def test_une_action_sensible_declenche_une_demande_de_validation():
    provider = RecordingProvider()
    validateur = ValidateurEnregistreur(decision=True)
    journal = RunJournal()
    report = asyncio.run(
        _engine(
            exec_provider=provider,
            plan_json=_plan_sensible(),
            guardrails=Guardrails(validateur=validateur),
        ).run("Objectif", journal=journal)
    )

    # La demande a été déclenchée, avec tout le contexte utile à la décision.
    assert len(validateur.demandes) == 1
    demande = validateur.demandes[0]
    assert demande.task_id == "deploiement"
    assert demande.agent == "devops"
    assert "deploy" in demande.raison
    # Approuvée : la tâche s'est exécutée normalement.
    assert report.resultats[0].ok
    assert len(provider.calls) == 1
    # La décision est consignée au journal, sur une étape dédiée.
    trace = next(r for r in journal.records if r.etape == "deploiement:validation")
    assert trace.statut == "approuve"


def test_une_action_sensible_refusee_est_stoppee_avant_execution():
    provider = RecordingProvider()
    validateur = ValidateurEnregistreur(decision=False)
    journal = RunJournal()
    report = asyncio.run(
        _engine(
            exec_provider=provider,
            plan_json=_plan_sensible(),
            guardrails=Guardrails(validateur=validateur),
        ).run("Objectif", journal=journal)
    )

    tache = report.resultats[0]
    assert tache.statut == "echec"
    assert "stoppée avant exécution" in (tache.erreur or "")
    # Refusée *avant* exécution : l'agent n'a jamais été appelé.
    assert provider.calls == []
    trace = next(r for r in journal.records if r.etape == "deploiement:validation")
    assert trace.statut == "refuse"


def test_sans_validateur_une_action_sensible_est_refusee_par_defaut():
    # Fail-safe : le moteur *par défaut* (aucun Guardrails injecté) refuse déjà
    # les actions sensibles — jamais d'exécution sensible sans accord explicite.
    provider = RecordingProvider()
    engine = _engine(exec_provider=provider, plan_json=_plan_sensible())
    report = asyncio.run(engine.run("Objectif"))

    tache = report.resultats[0]
    assert tache.statut == "echec"
    assert "aucun validateur" in (tache.erreur or "")
    assert provider.calls == []


def test_un_validateur_en_erreur_vaut_refus():
    def validateur_casse(demande: DemandeValidation) -> bool:
        raise RuntimeError("canal de validation indisponible")

    provider = RecordingProvider()
    report = asyncio.run(
        _engine(
            exec_provider=provider,
            plan_json=_plan_sensible(),
            guardrails=Guardrails(validateur=validateur_casse),
        ).run("Objectif")
    )

    tache = report.resultats[0]
    assert tache.statut == "echec"
    assert "refus par défaut" in (tache.erreur or "")
    assert provider.calls == []


def test_un_validateur_asynchrone_est_supporte():
    demandes: list[DemandeValidation] = []

    async def validateur(demande: DemandeValidation) -> bool:
        demandes.append(demande)
        return True

    report = asyncio.run(
        _engine(
            plan_json=_plan_sensible(), guardrails=Guardrails(validateur=validateur)
        ).run("Objectif")
    )

    assert len(demandes) == 1
    assert report.resultats[0].ok


def test_une_tache_anodine_ne_declenche_aucune_validation():
    validateur = ValidateurEnregistreur(decision=False)
    report = asyncio.run(
        _engine(guardrails=Guardrails(validateur=validateur)).run("Objectif")
    )

    assert validateur.demandes == []
    assert all(r.ok for r in report.resultats)


# --- Classification et configuration ----------------------------------------------------


def _task(titre, description="RAS."):
    return Task(
        id="t1",
        titre=titre,
        description=description,
        competences_requises=("tests",),
        format_sortie="Note",
    )


def test_la_detection_des_mots_sensibles_ignore_casse_et_accents():
    guardrails = Guardrails()
    assert guardrails.raison_sensible(_task("DÉPLOIEMENT en préproduction")) is not None
    assert guardrails.raison_sensible(_task("Nettoyage", "Supprimer les données.")) is not None
    assert guardrails.raison_sensible(_task("Écrire les tests unitaires")) is None
    assert _normalise("DÉPLOIEMENT") == "deploiement"


def test_les_mots_sensibles_sont_configurables():
    guardrails = Guardrails(mots_sensibles=("facturation",))
    assert guardrails.raison_sensible(_task("Recalcul de la facturation")) is not None
    assert guardrails.raison_sensible(_task("Déploiement en production")) is None
    # Tuple vide : détection désactivée.
    assert Guardrails(mots_sensibles=()).raison_sensible(_task("Déploiement")) is None


def test_les_garde_fous_invalides_sont_refuses():
    with pytest.raises(ValueError):
        Guardrails(plafond_cout_usd=0)
    with pytest.raises(ValueError):
        Guardrails(timeout_s=-1)
