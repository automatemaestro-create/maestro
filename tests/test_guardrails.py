"""Tests des garde-fous du POC (ticket #9) : plafond, time-out, validation humaine.

Aucun appel réseau : planification et exécution sont pilotées par des
`ModelProvider` factices, comme dans test_engine. Couvre les critères
d'acceptation du ticket #9 :

① une tâche dépassant le **plafond de dépense** est stoppée (y compris en cours de
   route : le travail postérieur au dépassement n'a pas lieu), son coût restant
   visible ; sous le plafond, rien ne change. Depuis #56, le plafond est un budget
   de l'**exécution entière**, adossé à la comptabilité par tâche (#55) : le cumul
   des tâches compte, et une exécution au budget épuisé n'en démarre plus aucune
   (tests différés du parent #49 → #59) ;
② une tâche dépassant le **time-out** est stoppée, sans gêner les autres tâches —
   et depuis #64 l'échéance est **ferme** : elle reprend la main même si
   l'annulation de la réalisation reste suspendue (sous-processus SDK non
   coopératif), la tâche zombie étant détachée et l'aval bloqué proprement ;
③ une **action sensible** déclenche une demande de validation humaine avant toute
   exécution : approuvée elle s'exécute, refusée elle est stoppée sans avoir rien
   lancé — et sans validateur configuré (ou validateur en panne), le refus est le
   défaut (fail-safe). La demande et la décision sont consignées au journal.
"""

import asyncio
import json

import pytest

from maestro.engine import DemandeValidation, Guardrails, OrchestrationEngine, executor
from maestro.engine.guardrails import _normalise  # test ciblé de la normalisation
from maestro.engine.runner import run_borne
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


def _engine(*, exec_provider=None, plan_json=None, guardrails=None, max_parallele=None):
    planner = ConstantProvider(plan_json or _plan_anodin())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(
        execu, orchestrator, guardrails=guardrails, max_parallele=max_parallele
    )


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


def _plan_deux_taches_independantes():
    # Deux tâches sans dépendance (aucun blocage aval #43 possible), routées par
    # mots-clés vers qa — sérialisées dans les tests par max_parallele=1.
    return json.dumps(
        [
            _tache("tests-api", "Tests de l'API", "Tests d'intégration.", ["tests"]),
            _tache("tests-charge", "Tests de charge", "Campagne de charge.", ["tests"]),
        ],
        ensure_ascii=False,
    )


def test_le_plafond_est_un_budget_d_execution_pas_par_tache():
    # Chaque tâche (0.012 $) tient sous le plafond (0.02 $) ; leur cumul le crève :
    # la première aboutit, la seconde est stoppée en cours de route (#56).
    provider = DepensierProvider()
    guardrails = Guardrails(plafond_cout_usd=0.02)
    report = asyncio.run(
        _engine(
            exec_provider=provider,
            plan_json=_plan_deux_taches_independantes(),
            guardrails=guardrails,
            max_parallele=1,
        ).run("Objectif")
    )

    premiere, seconde = report.resultats
    assert premiere.ok
    assert premiere.usage.cout_usd == pytest.approx(0.012)
    assert seconde.statut == "echec"
    assert "plafond de dépense dépassé" in (seconde.erreur or "")
    # Stoppée entre ses deux signalements (0.018 $ puis 0.024 $ de cumul run) :
    # le travail postérieur n'a pas eu lieu, le coût engagé reste visible.
    assert provider.acheves == 1
    assert seconde.usage.cout_usd == pytest.approx(0.012)


def test_une_execution_au_budget_epuise_ne_demarre_plus_aucune_tache():
    # La première tâche crève le plafond ; la seconde, indépendante, est refusée
    # à l'entrée de l'exécution — avant routage et sans aucun appel modèle (#56).
    provider = DepensierProvider()
    guardrails = Guardrails(plafond_cout_usd=0.01)
    report = asyncio.run(
        _engine(
            exec_provider=provider,
            plan_json=_plan_deux_taches_independantes(),
            guardrails=guardrails,
            max_parallele=1,
        ).run("Objectif")
    )

    premiere, seconde = report.resultats
    assert premiere.statut == "echec"
    assert "plafond de dépense dépassé" in (premiere.erreur or "")
    assert seconde.statut == "echec"
    assert "plafond de dépense dépassé" in (seconde.erreur or "")
    # Jamais démarrée : ni agent élu, ni appel modèle, ni coût engagé.
    assert seconde.agent == "—" and seconde.role == "non exécutée"
    assert seconde.usage.appels == 0
    assert seconde.usage.cout_usd is None
    assert provider.acheves == 0


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


class InannulableProvider(ModelProvider):
    """Simule l'aléa du #64 : la réalisation avale l'annulation et ne s'éteint jamais.

    C'est le comportement observé du transport SDK : le time-out expire,
    l'annulation est délivrée… et reste suspendue. Seule une échéance ferme —
    qui n'exige aucune coopération — peut rendre la main.
    """

    name = "inannulable"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if "Schéma BDD" not in prompt:
            return "LIVRABLE"
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue  # annulation avalée, comme le sous-processus suspendu


def test_le_timeout_reprend_la_main_meme_si_l_annulation_reste_suspendue(monkeypatch):
    # Régression #64 : la réalisation ignore l'annulation — l'échéance ferme doit
    # quand même consigner l'échec, laisser la boucle continuer, bloquer l'aval et
    # rendre le rapport (`run_borne` : la zombie ne suspend pas non plus la
    # fermeture de la boucle, là où `asyncio.run` attendrait indéfiniment).
    monkeypatch.setattr(executor, "_GRACE_ANNULATION_S", 0.05)
    plan = json.dumps(
        [
            _tache("schema-bdd", "Schéma BDD", "Définir le schéma.", ["sql"]),
            _tache("tests-api", "Tests de l'API", "Tests d'intégration.", ["tests"]),
            _tache("revue", "Revue finale", "Relire le schéma.", ["tests"], ["schema-bdd"]),
        ],
        ensure_ascii=False,
    )
    guardrails = Guardrails(timeout_s=0.2)
    report = run_borne(
        _engine(
            exec_provider=InannulableProvider(), plan_json=plan, guardrails=guardrails
        ).run("Objectif"),
        grace_s=0.05,
    )

    par_id = {r.task_id: r for r in report.resultats}
    suspendue = par_id["schema-bdd"]
    assert suspendue.statut == "echec"
    assert "time-out" in (suspendue.erreur or "")
    assert "détachée" in (suspendue.erreur or "")  # l'annulation n'a pas coopéré
    assert par_id["tests-api"].ok  # la boucle continue : l'autre tâche aboutit
    assert par_id["revue"].statut == "bloquee"  # l'aval est bloqué proprement


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
