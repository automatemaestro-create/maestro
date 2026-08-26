"""Tests des garde-fous du POC (ticket #9) : plafond, time-out, validation humaine.

Aucun appel réseau : planification et exécution sont pilotées par des
`ModelProvider` factices, comme dans test_engine. Couvre les critères
d'acceptation du ticket #9 :

① une tâche dépassant le **plafond de dépense** est stoppée (y compris en cours de
   route : le travail postérieur au dépassement n'a pas lieu), son coût restant
   visible ; sous le plafond, rien ne change. Depuis #56, le plafond est un budget
   de l'**exécution entière**, adossé à la comptabilité par tâche (#55) : le cumul
   des tâches compte, et une exécution au budget épuisé n'en démarre plus aucune
   (tests différés du parent #49 → #59). Depuis #113, un **plafond en tokens** double
   le plafond en USD : opérant même sur un fournisseur qui ne rapporte pas de coût,
   et le rapport dit lequel des deux contrôles a réellement tenu ;
② une tâche dépassant le **time-out** est stoppée, sans gêner les autres tâches —
   et depuis #64 l'échéance est **ferme** : elle reprend la main même si
   l'annulation de la réalisation reste suspendue (sous-processus SDK non
   coopératif), la tâche zombie étant détachée et l'aval bloqué proprement ;
③ une **action sensible** déclenche une demande de validation humaine avant toute
   exécution : approuvée elle s'exécute, refusée elle est stoppée sans avoir rien
   lancé — et sans validateur configuré (ou validateur en panne), le refus est le
   défaut (fail-safe). La demande et la décision sont consignées au journal ;
④ **qui décide** (#586, lot 7 de #573) : le cran porté par la demande route vers
   l'une des trois portes — `auto` accorde sans solliciter personne,
   `orchestrateur` fait trancher la machine seule, `humain` attend une personne,
   et c'est le défaut de tout ce qui n'en dit rien. Le fail-safe vaut porte par
   porte (canal absent ou en panne ⇒ refus), et surtout **l'orchestrateur ne peut
   pas approuver un acte classé `humain`** — éprouvé en le lui faisant tenter,
   sur la configuration exacte où ça compte : lui seul câblé, aucune personne
   pour dire non. Ce qui est vérifié n'est pas qu'on refuse son approbation,
   c'est qu'on ne la lui demande pas.

⚠ Depuis #585, la classification par mots-clés n'est **plus armée par défaut** :
les tests de ③ passent donc `mots_sensibles=MOTS_SENSIBLES` explicitement. Ce
n'est pas une formalité d'adaptation — ce qu'ils éprouvent est le **canal** de la
décision humaine et son fail-safe, qui ne dépendent d'aucun déclencheur en
particulier ; l'armer sur place est ce qui garde ces tests lisibles le jour où le
déclencheur nominal (l'acte, chantier #573) changera à son tour. Le nouveau
défaut, lui, est éprouvé pour lui-même plus bas.

④ ne l'arme jamais, et c'est cohérent avec la même idée : il éprouve le
**routage** d'une demande déjà née, quel que soit ce qui l'a déclenchée.
"""

import asyncio
import json

import pytest

from maestro.engine import DemandeValidation, Guardrails, OrchestrationEngine, executor
from maestro.engine.guardrails import (
    MOTS_SENSIBLES,
    _normalise,  # test ciblé de la normalisation
)
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


# --- Plafond en tokens : opérant sans coût rapporté (#113) -----------------------------


class SansCoutProvider(ModelProvider):
    """Rapporte des tokens à chaque signalement mais jamais de coût (cout_usd None).

    Reproduit le fournisseur `openai` du run réel #99 : le dialecte chat completions
    remonte prompt/completion_tokens sans prix. Deux signalements de 400 tokens par
    appel — le cumul (800) sert à tester le plafond en tokens.
    """

    name = "sans-cout"

    def __init__(self) -> None:
        self.acheves = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        report_usage(StepUsage(appels=1, tokens_sortie=400, cout_usd=None))
        report_usage(StepUsage(appels=1, tokens_sortie=400, cout_usd=None))
        self.acheves += 1
        return "LIVRABLE sans coût rapporté"


def test_le_plafond_en_tokens_stoppe_un_run_sans_cout_rapporte():
    # Cœur du #113 : sans coût rapporté, seul le plafond en tokens a prise. Il
    # stoppe la tâche en cours de route, comme le plafond en USD sur un coût connu.
    provider = SansCoutProvider()
    guardrails = Guardrails(plafond_tokens=500)
    report = asyncio.run(_engine(exec_provider=provider, guardrails=guardrails).run("Objectif"))

    tache = report.resultats[0]
    assert tache.statut == "echec"
    assert "plafond de tokens dépassé" in (tache.erreur or "")
    # Stoppée sur le second signalement (400 puis 800 de cumul > 500) : le travail
    # postérieur au dépassement n'a pas eu lieu…
    assert provider.acheves == 0
    # …mais les tokens engagés (et le coût, toujours inconnu) restent visibles.
    assert tache.usage.tokens_total == 800
    assert tache.usage.cout_usd is None


def test_le_rapport_dit_que_le_controle_en_tokens_a_tenu_sans_cout():
    # Critère ② du #113 : l'opérateur voit quel contrôle était actif, pas un
    # plafond silencieusement inopérant.
    provider = SansCoutProvider()
    guardrails = Guardrails(plafond_tokens=100_000)
    report = asyncio.run(_engine(exec_provider=provider, guardrails=guardrails).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert "tokens" in report.controle_depense
    assert "Contrôle de dépense : plafond actif — tokens" in report.synthese()
    assert report.to_dict()["plafond_tokens"] == 100_000


def test_le_rapport_signale_un_plafond_de_cout_sans_prise():
    # Le piège que le #113 rend visible : plafond en USD armé, mais le fournisseur
    # ne rapporte pas de coût — le rapport le dit au lieu de laisser croire à un filet.
    provider = SansCoutProvider()
    guardrails = Guardrails(plafond_cout_usd=5.0)
    report = asyncio.run(_engine(exec_provider=provider, guardrails=guardrails).run("Objectif"))

    assert all(r.ok for r in report.resultats)  # rien ne l'a stoppé : aucune prise
    assert "SANS PRISE" in report.controle_depense


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
            guardrails=Guardrails(validateur=validateur, mots_sensibles=MOTS_SENSIBLES),
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
            guardrails=Guardrails(validateur=validateur, mots_sensibles=MOTS_SENSIBLES),
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
    # Fail-safe : une action classée sensible sans canal de décision humaine est
    # refusée — jamais d'exécution sensible sans accord explicite.
    #
    # Le garde-fou éprouvé ici est celui du **validateur absent**, pas celui du
    # déclencheur : on arme donc la classification et on laisse `validateur` à
    # None, ce qui est exactement la situation à couvrir. Depuis #585, ne rien
    # injecter du tout ne classerait plus rien (`mots_sensibles` vide par défaut)
    # et le test passerait au vert sans avoir posé sa question.
    provider = RecordingProvider()
    engine = _engine(
        exec_provider=provider,
        plan_json=_plan_sensible(),
        guardrails=Guardrails(mots_sensibles=MOTS_SENSIBLES),
    )
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
            guardrails=Guardrails(
                validateur=validateur_casse, mots_sensibles=MOTS_SENSIBLES
            ),
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
            plan_json=_plan_sensible(),
            guardrails=Guardrails(
                validateur=validateur, mots_sensibles=MOTS_SENSIBLES
            ),
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
    guardrails = Guardrails(mots_sensibles=MOTS_SENSIBLES)
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


def test_par_defaut_la_classification_par_mots_cles_ne_se_declenche_pas():
    """#585 — `mots_sensibles` est vide par défaut : plus aucun mot ne déclenche.

    C'est le lot qui ferme le défaut du chantier #573 : le déclencheur n'est plus
    le texte de ce qu'on demande d'écrire. On éprouve les deux moitiés du contrat
    — plus rien ne se déclenche tout seul, et le régime d'avant reste **atteignable**
    en renseignant la liste — parce qu'une seule des deux laisserait passer la
    régression symétrique (désarmer en supprimant le mécanisme).
    """
    par_defaut = Guardrails()
    assert par_defaut.mots_sensibles == ()
    # Les radicaux d'origine, y compris celui mesuré sur #568.
    assert par_defaut.raison_sensible(_task("Déployer l'API en production")) is None
    assert par_defaut.raison_sensible(_task("Supprimer une note")) is None
    assert par_defaut.raison_sensible(_task("Migration destructive")) is None
    # Le mécanisme n'a pas été retiré : le renseigner rearme exactement l'ancien
    # régime, radical par radical.
    ancien = Guardrails(mots_sensibles=MOTS_SENSIBLES)
    assert ancien.raison_sensible(_task("Déployer l'API en production")) is not None
    assert ancien.raison_sensible(_task("Supprimer une note")) is not None


def test_un_objectif_qui_dit_supprimer_ne_rend_aucune_tache_sensible():
    """#585 — le cas mesuré sur #568, joué de bout en bout sur la boucle.

    Un objectif demandant « une sous-commande **supprimer** une note » rendait
    3 tâches sur 3 sensibles, « Rédiger le README » comprise, parce que le mot
    vient du brief et se propage à toutes les descriptions que la décomposition
    en tire. Le validateur est branché **et refuse** : s'il était consulté, les
    tâches échoueraient — leur succès est donc ce qui prouve qu'il ne l'a pas été.

    L'autre moitié du critère — « un agent qui appelle un outil classé `ask` en
    rend une » — se joue là où vit le déclencheur qui la produit :
    `tests/test_permissions.py::test_l_issue_d_un_arbitrage_est_consignee_sous_son_propre_statut`,
    sur un plan sans le moindre mot-clé. Depuis ce lot, elle prouve davantage
    qu'avant : `Guardrails` y prend le nouveau défaut, donc rien d'autre que
    l'outil `ask` ne peut y avoir déclenché la demande.
    """
    # Les deux tâches sont routées vers qa (« tests ») comme le plan anodin : ce
    # qu'on éprouve est le texte, pas l'aiguillage — un routage qui échoue rendrait
    # « echec » pour une raison étrangère au ticket, et masquerait le vrai verdict.
    plan = json.dumps(
        [
            _tache(
                "cli-supprimer",
                "Ajouter la sous-commande supprimer",
                "Implémenter `notes supprimer <id>` puis la couvrir de tests.",
                ["tests"],
            ),
            _tache(
                "readme",
                "Rédiger le README",
                "Documenter la sous-commande supprimer une note.",
                ["tests"],
            ),
        ],
        ensure_ascii=False,
    )
    validateur = ValidateurEnregistreur(decision=False)
    report = asyncio.run(
        _engine(plan_json=plan, guardrails=Guardrails(validateur=validateur)).run(
            "Ajouter une sous-commande supprimer une note"
        )
    )

    assert validateur.demandes == []
    assert len(report.resultats) == 2
    assert all(r.ok for r in report.resultats)


def test_les_garde_fous_invalides_sont_refuses():
    with pytest.raises(ValueError):
        Guardrails(plafond_cout_usd=0)
    with pytest.raises(ValueError):
        Guardrails(plafond_tokens=0)
    with pytest.raises(ValueError):
        Guardrails(timeout_s=-1)


# --- Qui décide : `auto`, `orchestrateur` ou `humain` (#586) ----------------------------
#
# La **logique critique** du lot 7 de #573, admise ici alors que le reste de la
# couverture du chantier est différé à #579 : c'est l'asymétrie du fail-safe
# (EF-08/ENF-04) — refuser est le défaut sûr, approuver ne l'est jamais.


def _demande(decideur: str | None = None) -> DemandeValidation:
    """Une demande d'arbitrage minimale, sur le cran demandé (défaut du champ sinon)."""
    champs = {} if decideur is None else {"decideur": decideur}
    return DemandeValidation(
        task_id="t1",
        titre="Nettoyer le dossier de build",
        description="RAS.",
        agent="dev",
        role="Développeur",
        raison="outil 'Bash' soumis à arbitrage",
        outil="Bash",
        arguments={"command": "rm -rf build/"},
        **champs,
    )


class _Mouchard:
    """Un canal de décision qui répond toujours pareil, et qui compte ses appels."""

    def __init__(self, reponse: bool) -> None:
        self.reponse = reponse
        self.vues: list[DemandeValidation] = []

    def __call__(self, demande: DemandeValidation) -> bool:
        self.vues.append(demande)
        return self.reponse


def test_un_cran_non_precise_escalade_vers_l_humain():
    # Le défaut n'est pas un détail de mise en œuvre : une demande qui ne dit
    # rien de son décideur ne s'auto-approuve pas, elle attend une personne.
    orchestrateur = _Mouchard(True)
    guardrails = Guardrails(orchestrateur=orchestrateur)

    approuve, detail = asyncio.run(guardrails.demande_validation(_demande()))

    assert approuve is False
    assert "aucun validateur humain configuré" in detail
    assert orchestrateur.vues == []


def test_le_cran_auto_accorde_sans_solliciter_personne():
    # Ce qui distingue `ask` + `auto` d'un `allow` : l'appel passe, mais il
    # laisse une trace — d'où un détail qui dit que personne n'a été dérangé.
    humain = _Mouchard(False)
    orchestrateur = _Mouchard(False)
    guardrails = Guardrails(validateur=humain, orchestrateur=orchestrateur)

    approuve, detail = asyncio.run(guardrails.demande_validation(_demande("auto")))

    assert approuve is True
    assert "auto" in detail
    assert humain.vues == [] and orchestrateur.vues == []


def test_l_orchestrateur_tranche_seul_le_cran_qui_lui_revient():
    # Aucun validateur humain configuré, et pourtant la décision est rendue :
    # c'est tout l'intérêt du cran du milieu dans un run que personne ne regarde.
    approbateur = _Mouchard(True)
    approuve, detail = asyncio.run(
        Guardrails(orchestrateur=approbateur).demande_validation(_demande("orchestrateur"))
    )
    assert approuve is True
    assert detail == "approuvée par l'orchestrateur"
    assert len(approbateur.vues) == 1

    # Et il refuse **seul** tout aussi bien : le refus est le sens sûr des deux.
    refuseur = _Mouchard(False)
    approuve, detail = asyncio.run(
        Guardrails(orchestrateur=refuseur).demande_validation(_demande("orchestrateur"))
    )
    assert approuve is False
    assert detail == "refusée par l'orchestrateur"


def test_l_orchestrateur_ne_peut_pas_approuver_un_acte_humain():
    """On lui fait **tenter** l'approbation, et elle n'atteint jamais l'acte.

    L'orchestrateur monté ici approuve tout ce qu'on lui soumet, et il est le
    seul canal câblé. Si son avis pouvait devenir une approbation, ce serait
    ici : il n'y a personne d'autre pour dire non, et un `deny` humain absent
    est la situation exacte d'un run autonome.

    Deux assertions et non une, parce qu'elles ne disent pas la même chose : la
    première dit que l'acte est **refusé**, la seconde qu'il l'est parce que
    l'orchestrateur n'a **pas été consulté**. Sans la seconde, le test
    passerait encore le jour où on lui demanderait son avis pour l'ignorer —
    un dispositif où l'approbation existe quelque part et n'est « pas retenue »
    est un dispositif à un `if` de la faute.
    """
    orchestrateur = _Mouchard(True)

    approuve, detail = asyncio.run(
        Guardrails(orchestrateur=orchestrateur).demande_validation(_demande("humain"))
    )

    assert approuve is False
    assert "aucun validateur humain configuré" in detail
    assert orchestrateur.vues == []


def test_sans_orchestrateur_le_cran_du_milieu_est_refuse_et_jamais_delegue():
    # Fail-safe porte par porte : ne trouver personne à qui demander n'a jamais
    # autorisé une action sensible. Et la demande ne **retombe pas** sur le
    # validateur humain — la déléguer reviendrait à réveiller quelqu'un pour un
    # acte dont la politique a dit qu'il ne lui revenait pas.
    humain = _Mouchard(True)

    approuve, detail = asyncio.run(
        Guardrails(validateur=humain).demande_validation(_demande("orchestrateur"))
    )

    assert approuve is False
    assert "aucun orchestrateur configuré" in detail
    assert humain.vues == []


def test_un_orchestrateur_en_panne_vaut_refus():
    def casse(demande: DemandeValidation) -> bool:
        raise RuntimeError("canal d'orchestration indisponible")

    approuve, detail = asyncio.run(
        Guardrails(orchestrateur=casse).demande_validation(_demande("orchestrateur"))
    )

    assert approuve is False
    assert "orchestrateur en erreur" in detail
    assert "canal d'orchestration indisponible" in detail


def test_un_cran_illisible_escalade_au_lieu_de_s_auto_approuver():
    # Relecture d'une valeur venue du dehors (journal rejoué, producteur d'une
    # autre version) : on ne sait pas la lire, donc on la traite comme `humain`.
    # Le repli inverse ferait d'une chaîne inconnue un laissez-passer.
    orchestrateur = _Mouchard(True)

    approuve, _ = asyncio.run(
        Guardrails(orchestrateur=orchestrateur).demande_validation(_demande("dieu"))
    )

    assert approuve is False
    assert orchestrateur.vues == []
