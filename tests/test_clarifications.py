"""Questions de clarification : le run attend les réponses (#321, lot 7/9 de #314).

Les tests de la Phase 8 sont différés au lot 9 (#323). Ceux-ci font exception, et
la règle qui l'autorise en dit le motif : « un lot intermédiaire n'en porte que si
sa logique est critique » (CLAUDE.md, docs/10 §5.1). Elle l'est ici sur un point
précis — **la borne**. Une boucle « tant que le brief pose des questions » dont la
sortie dépendrait de la docilité d'un modèle est une boucle qui peut ne jamais
sortir, et un run qui ne sort pas d'un aller-retour tient une session, un worktree
et l'attention de quelqu'un sans que rien ne le dise. Ce fichier vérifie donc
d'abord ce que le lot ne doit **jamais** faire, puis ce qu'il doit faire.

Trois sections, une par critère d'acceptation :

① la boucle — les questions sont posées, les réponses **régénèrent** le brief ;
② la borne — le plafond tient face à un modèle qui repose ses questions à chaque
  tour, et les zones d'ombre restantes partent en **hypothèses explicites** ;
③ l'attente — visible (statut, ancienneté, questions) et **annulable**, parce
  qu'une attente muette est indiscernable d'un run planté.

Aucun réseau, aucun quota : le fournisseur est scripté, le bus est en mémoire.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    create_app,
)
from maestro.controltower.brief import (
    ArbitreClarificationControlTower,
    arbitre_clarification_redis,
    evenement_questions_brief,
)
from maestro.controltower.events import EVENEMENT_BRIEF_QUESTIONS, EVENEMENT_BRIEF_REPONSES
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
)
from maestro.engine import MODE_BRIEF_HUMAIN, OrchestrationEngine
from maestro.engine.brief import (
    TOURS_CLARIFICATION_DEFAUT,
    DecisionBrief,
    DemandeClarification,
    apparie_reponses,
    motif_sans_reponse,
    tours_clarification_valide,
)
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.prompt import build_brief_user_prompt
from maestro.orchestrator.schema import Brief, Clarification
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

#: Plafond d'attente des scénarios asynchrones — jamais atteint quand tout va bien.
DELAI_ATTENTE_S = 5.0


# ------------------------------------------------------------------ doubles


def _brief_json(questions: list[str], *, objectif: str = "Un CRM minimal") -> str:
    """Un brief conforme au schéma partagé, paramétré par ses seules questions."""
    return json.dumps(
        {
            "objectif": objectif,
            "perimetre": ["Fiches client", "Recherche"],
            "hors_perimetre": ["Facturation"],
            "contraintes": [],
            "criteres_acceptation": ["Une fiche se crée et se relit"],
            "hypotheses": [],
            "questions": questions,
        },
        ensure_ascii=False,
    )


def _plan_json() -> str:
    """Un plan minimal : trois tâches en chaîne, de quoi finir un run."""
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma.",
                "competences_requises": ["sql", "schema"],
                "format_sortie": "Fichier SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API",
                "dependances": ["schema-bdd"],
            },
            {
                "id": "tests-api",
                "titre": "Tests",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


class ProviderScripte(ModelProvider):
    """Fournisseur scripté du cadrage : les briefs dans l'ordre, puis le plan.

    Il **dispatche sur le prompt** plutôt que sur un simple compteur d'appels, et
    ce n'est pas de la coquetterie : le nombre d'appels de brief est précisément ce
    que les tests de la borne mesurent, donc le double ne doit pas en dépendre pour
    savoir quoi répondre — un scénario qui boucle une fois de trop recevrait sinon
    le plan à la place d'un brief, et échouerait sur un `BriefParsingError` au lieu
    de dire ce qui ne va pas.

    Le **dernier brief est répété** une fois la liste épuisée : c'est ainsi qu'on
    simule un modèle qui repose indéfiniment ses questions, le cas que la borne
    existe pour tenir.
    """

    name = "scripte"

    def __init__(self, briefs: list[str], plan: str | None = None) -> None:
        self._briefs = list(briefs)
        self._plan = plan if plan is not None else _plan_json()
        self.prompts_brief: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if not prompt.startswith("Rédige le brief"):
            return self._plan
        self.prompts_brief.append(prompt)
        if len(self._briefs) > 1:
            return self._briefs.pop(0)
        return self._briefs[0]


class ArbitreQuestionsEnregistreur:
    """Arbitre de clarification factice : répond toujours la même chose, et compte.

    `demandes` garde chaque `DemandeClarification` reçue — c'est par elle qu'on
    vérifie le rang du tour et le plafond annoncé, sans fouiller les internes du
    moteur.
    """

    def __init__(self, reponse: str = "Les employés internes seulement.") -> None:
        self.reponse = reponse
        self.demandes: list[DemandeClarification] = []

    async def __call__(self, demande: DemandeClarification) -> tuple[Clarification, ...]:
        self.demandes.append(demande)
        return apparie_reponses(
            demande.brief, [self.reponse] * len(demande.brief.questions)
        )


class ArbitreBriefApprobateur:
    """Arbitre de brief factice : approuve tel quel, et garde ce qu'on lui a soumis."""

    def __init__(self) -> None:
        self.soumis: list[Brief] = []

    async def __call__(self, demande) -> DecisionBrief:
        self.soumis.append(demande.brief)
        return DecisionBrief(approuve=True)


def _moteur(briefs: list[str], *, tours: int | None = None, arbitre_questions=None):
    """Un moteur en mode « humain » dont le cadrage est entièrement scripté."""
    planificateur = ProviderScripte(briefs)
    orchestrateur = Orchestrator(planificateur, model="claude-opus-4-8")
    arbitre_brief = ArbitreBriefApprobateur()
    moteur = OrchestrationEngine(
        ProviderScripte(["LIVRABLE"], plan="LIVRABLE"),
        orchestrateur,
        arbitre_brief=arbitre_brief,
        arbitre_clarification=arbitre_questions,
        tours_clarification=tours,
    )
    return moteur, arbitre_brief, planificateur


# --------------------------------------------- ① la boucle de clarification


def test_les_questions_sont_posees_et_les_reponses_regenerent_le_brief():
    """Critère n°1 : le run publie ses questions, attend, puis régénère le brief."""
    questions = ArbitreQuestionsEnregistreur("Les employés internes seulement.")
    moteur, validation, planificateur = _moteur(
        [
            _brief_json(["L'authentification vise-t-elle les clients ?"]),
            _brief_json([]),  # une fois répondu, plus de zone d'ombre
        ],
        arbitre_questions=questions,
    )

    rapport = asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-1"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    # Un seul aller-retour a suffi : le brief régénéré ne pose plus rien.
    assert len(questions.demandes) == 1
    assert questions.demandes[0].tour == 1
    assert rapport.tours_clarification == 1
    # La régénération est bien un **second** appel modèle, et il porte la réponse.
    prompt_regeneration = planificateur.prompts_brief[1]
    assert "L'authentification vise-t-elle les clients ?" in prompt_regeneration
    assert "Les employés internes seulement." in prompt_regeneration
    # C'est le brief régénéré — sans question — qui part en validation.
    assert validation.soumis[-1].questions == ()
    assert rapport.brief is not None and rapport.brief.questions == ()


def test_les_clarifications_sont_cumulees_d_un_tour_a_l_autre():
    """Le brief étant réécrit en entier, un tour ne doit pas perdre le précédent."""
    questions = ArbitreQuestionsEnregistreur("Réponse.")
    moteur, _, planificateur = _moteur(
        [
            _brief_json(["Question A ?"]),
            _brief_json(["Question B ?"]),
            _brief_json([]),
        ],
        arbitre_questions=questions,
    )

    asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-2"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    # Le prompt du 3e appel (2e régénération) porte **les deux** questions déjà
    # traitées : sans le cumul, le modèle reperdrait ce que le tour 1 avait levé.
    dernier = planificateur.prompts_brief[2]
    assert "Question A ?" in dernier
    assert "Question B ?" in dernier


def test_sans_arbitre_les_questions_partent_telles_quelles_en_validation():
    """Sans clarification câblée, le comportement est **exactement** celui de #320."""
    moteur, validation, planificateur = _moteur(
        [_brief_json(["Une question restée ouverte ?"])], arbitre_questions=None
    )

    rapport = asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-3"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    assert rapport.tours_clarification == 0
    assert validation.soumis[-1].questions == ("Une question restée ouverte ?",)
    # Un seul appel de brief : aucune régénération n'a eu lieu.
    assert len(planificateur.prompts_brief) == 1


def test_plafond_a_zero_desarme_la_clarification_sans_retirer_l_arbitre():
    """`tours_clarification=0` coupe l'aller-retour — et l'arbitre n'est pas appelé."""
    questions = ArbitreQuestionsEnregistreur()
    moteur, validation, _ = _moteur(
        [_brief_json(["Une question ?"])], tours=0, arbitre_questions=questions
    )

    rapport = asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-4"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    assert questions.demandes == []
    assert rapport.tours_clarification == 0
    assert validation.soumis[-1].questions == ("Une question ?",)


# ------------------------------------------------------------- ② la borne


def test_un_modele_qui_repose_ses_questions_ne_fait_pas_boucler_le_run():
    """Critère n°2, et le cœur du lot : la borne tient **quoi qu'il arrive**.

    Le fournisseur rend un brief porteur de questions à *chaque* appel — le cas
    exact où une boucle « tant qu'il reste des questions » ne s'arrêterait jamais.
    """
    questions = ArbitreQuestionsEnregistreur()
    moteur, validation, _ = _moteur(
        [_brief_json(["Toujours la même question ?"])],  # répétée à l'infini
        tours=2,
        arbitre_questions=questions,
    )

    rapport = asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-5"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    assert len(questions.demandes) == 2  # pas 3, pas l'infini
    assert rapport.tours_clarification == 2
    # Le plafond a été **annoncé** à chaque tour, et le dernier se sait dernier.
    assert [d.tour for d in questions.demandes] == [1, 2]
    assert all(d.tours_max == 2 for d in questions.demandes)
    assert questions.demandes[-1].dernier_tour is True


def test_au_plafond_les_zones_d_ombre_partent_en_hypotheses_explicites():
    """Critère n°2 (suite) : ce qui reste est **inscrit**, pas silencieusement perdu."""
    questions = ArbitreQuestionsEnregistreur()
    moteur, validation, _ = _moteur(
        [_brief_json(["Question jamais levée ?"])], tours=1, arbitre_questions=questions
    )

    rapport = asyncio.run(
        moteur.run("Un CRM", journal=RunJournal(run_id="r-6"), mode_brief=MODE_BRIEF_HUMAIN)
    )

    soumis = validation.soumis[-1]
    assert soumis.questions == ()  # le brief part en validation sans question
    assert any("Question jamais levée ?" in h for h in soumis.hypotheses)
    assert any(motif_sans_reponse(1) in h for h in soumis.hypotheses)
    # La synthèse **annonce** le nombre d'allers-retours joués (troisième lecture
    # de « borné et annoncé » : celle de qui relit un run terminé).
    assert "1 tour(s) de clarification" in rapport.synthese()


def test_conversion_en_hypotheses_est_deterministe_et_sans_perte():
    """Unité : la garantie du plafond ne passe pas par le modèle mais par Python."""
    brief = Brief(
        objectif="o",
        perimetre=("p",),
        hors_perimetre=(),
        criteres_acceptation=("c",),
        hypotheses=("déjà là",),
        questions=("q1 ?", "q2 ?"),
    )

    converti = brief.questions_en_hypotheses(motif_sans_reponse(2))

    assert converti.questions == ()
    assert converti.hypotheses[0] == "déjà là"  # rien n'est écrasé
    assert converti.hypotheses[1:] == (
        "Sans réponse après 2 tour(s) de clarification : q1 ?",
        "Sans réponse après 2 tour(s) de clarification : q2 ?",
    )
    # Sans question, l'objet est rendu tel quel (et reste un brief valide).
    assert converti.questions_en_hypotheses("x") is converti


def test_appariement_des_reponses_tolere_le_trop_et_le_pas_assez():
    """Unité : en plein run, une liste mal dimensionnée ne doit pas coûter le run."""
    brief = Brief(
        objectif="o",
        perimetre=("p",),
        hors_perimetre=(),
        criteres_acceptation=("c",),
        questions=("q1 ?", "q2 ?"),
    )

    manquante = apparie_reponses(brief, ["seule"])
    assert manquante == (Clarification("q1 ?", "seule"), Clarification("q2 ?", ""))
    assert manquante[1].sans_reponse is True

    en_trop = apparie_reponses(brief, ["a", "b", "c"])
    assert [c.reponse for c in en_trop] == ["a", "b"]  # la 3e ne vise rien


@pytest.mark.parametrize(
    ("entree", "attendu"), [(None, TOURS_CLARIFICATION_DEFAUT), (0, 0), (5, 5)]
)
def test_plafond_normalise(entree, attendu):
    assert tours_clarification_valide(entree) == attendu


def test_plafond_negatif_refuse_avant_le_premier_appel_modele():
    """Un plafond absurde ne se découvre pas au milieu d'un run déjà payé."""
    with pytest.raises(ValueError, match="négatif"):
        tours_clarification_valide(-1)


# ---------------------------------------- ③ l'attente : visible et annulable


class MoteurQuiQuestionne:
    """Moteur double dont le run se suspend sur de vraies questions de clarification.

    Il ne planifie rien : il appelle le **vrai** arbitre Control Tower, ce qui est
    tout ce que cette section observe — la suspension, ce qu'elle rend visible, et
    ce qui la lève.
    """

    def __init__(self, bus, brief: Brief) -> None:
        self._arbitre = ArbitreClarificationControlTower(bus)
        self._brief = brief
        self.reponses: tuple[Clarification, ...] = ()
        self.annule = False

    def __call__(self, **reglages):
        return self

    async def run(
        self,
        objectif,
        *,
        journal=None,
        ticket=None,
        projet_id=None,
        mode_brief="",
        porte=None,
    ):
        run_id = journal.run_id if journal is not None else ""
        try:
            self.reponses = await self._arbitre(
                DemandeClarification(
                    run_id=run_id,
                    objectif=objectif,
                    brief=self._brief,
                    tour=1,
                    tours_max=2,
                )
            )
        except asyncio.CancelledError:
            self.annule = True
            raise
        # Le run ne va pas plus loin : la section n'observe que l'attente.
        from maestro.engine import RunReport

        return RunReport(objectif=objectif, resultats=())


@pytest.fixture()
def brief_a_questions() -> Brief:
    return Brief(
        objectif="Un CRM minimal",
        perimetre=("Fiches client",),
        hors_perimetre=(),
        criteres_acceptation=("Une fiche se crée",),
        questions=("Interne ou public ?", "Quel volume ?"),
    )


def _client_qui_attend(brief: Brief):
    """App réelle + moteur qui se suspend : rend le client, l'état et le moteur.

    **Un seul bus** pour les deux, et c'est tout le montage : l'arbitre publie ses
    questions et attend les réponses là où l'API les poste. Deux bus distincts
    donneraient un run qui attend pour toujours — l'exacte panne que ce lot doit
    éviter, et qu'un test mal câblé sait très bien reproduire.
    """
    bus = InMemoryEventBus()
    state = ControlTowerState()
    moteur = MoteurQuiQuestionne(bus, brief)
    client = TestClient(create_app(bus=bus, state=state, fabrique_moteur=moteur))
    return client, state, moteur


def _attendre_statut(client: TestClient, run_id: str, statut: str) -> dict:
    fin = time.monotonic() + DELAI_ATTENTE_S
    while time.monotonic() < fin:
        resume = client.get(f"/api/executions/{run_id}").json()
        if resume["statut"] == statut:
            return resume
        time.sleep(0.01)
    raise AssertionError(f"le run n'a pas atteint « {statut} » avant le time-out")


def test_l_attente_est_visible_statut_anciennete_et_questions(brief_a_questions):
    """Critère n°3 : une attente muette est indiscernable d'un run planté."""
    client, _, _ = _client_qui_attend(brief_a_questions)
    with client:
        run_id = client.post(
            "/api/executions", json={"objectif": "Un CRM", "brief": MODE_BRIEF_HUMAIN}
        ).json()["run_id"]

        resume = _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_REPONSES)

        # Le statut dit *quelle* attente, l'ancienneté *depuis quand*…
        assert resume["statut"] == EXECUTION_EN_ATTENTE_REPONSES
        assert resume["attente_depuis"]
        # …et le plafond est annoncé, pas seulement subi.
        assert resume["tour_clarification"] == 1
        assert resume["tours_clarification_max"] == 2
        # …et les questions en cours sont lisibles (dans le détail, pas la liste).
        assert client.get(f"/api/executions/{run_id}").json()["brief"]["questions"] == [
            "Interne ou public ?",
            "Quel volume ?",
        ]


def test_le_run_reste_annulable_pendant_l_attente(brief_a_questions):
    """Critère n°3 : `en_attente_reponses` est non terminal — donc interruptible."""
    client, _, moteur = _client_qui_attend(brief_a_questions)
    with client:
        run_id = client.post(
            "/api/executions", json={"objectif": "Un CRM", "brief": MODE_BRIEF_HUMAIN}
        ).json()["run_id"]
        _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_REPONSES)

        reponse = client.post(f"/api/executions/{run_id}/annuler")

        assert reponse.status_code == 200
        assert reponse.json()["statut"] == EXECUTION_ANNULEE
        # L'ancienneté ne survit pas au run : « en attente depuis 3 h » sur un run
        # arrêté serait un mensonge de plus, pas une information.
        assert reponse.json()["attente_depuis"] is None
        assert _attendre_statut(client, run_id, EXECUTION_ANNULEE)
        assert moteur.annule is True


def test_les_reponses_relancent_le_run_et_lui_parviennent(brief_a_questions):
    """Critère n°1 côté API : `POST …/brief/reponses` lève l'attente."""
    client, _, moteur = _client_qui_attend(brief_a_questions)
    with client:
        run_id = client.post(
            "/api/executions", json={"objectif": "Un CRM", "brief": MODE_BRIEF_HUMAIN}
        ).json()["run_id"]
        _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_REPONSES)

        reponse = client.post(
            f"/api/executions/{run_id}/brief/reponses",
            json={"reponses": ["Interne", ""]},
        )

        assert reponse.status_code == 200
        assert reponse.json()["statut"] == EXECUTION_EN_COURS
        assert reponse.json()["attente_depuis"] is None
        fin = time.monotonic() + DELAI_ATTENTE_S
        while not moteur.reponses and time.monotonic() < fin:
            time.sleep(0.01)
        # Appariées **par position**, réponse vide comprise (elle vaut « je ne sais
        # pas » et partira en hypothèse, jamais en question reposée).
        assert moteur.reponses == (
            Clarification("Interne ou public ?", "Interne"),
            Clarification("Quel volume ?", ""),
        )


def test_reponses_de_la_mauvaise_longueur_refusees_en_422(brief_a_questions):
    """L'appariement est positionnel : une liste décalée ne doit pas passer en silence."""
    client, _, _ = _client_qui_attend(brief_a_questions)
    with client:
        run_id = client.post(
            "/api/executions", json={"objectif": "Un CRM", "brief": MODE_BRIEF_HUMAIN}
        ).json()["run_id"]
        _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_REPONSES)

        reponse = client.post(
            f"/api/executions/{run_id}/brief/reponses", json={"reponses": ["une seule"]}
        )

        assert reponse.status_code == 422
        assert "2 question(s)" in reponse.json()["detail"]


def test_reponses_a_un_run_qui_n_attend_pas_refusees_en_409(brief_a_questions):
    """Jamais répondu deux fois, jamais un run soldé ramené en vol."""
    client, _, _ = _client_qui_attend(brief_a_questions)
    with client:
        run_id = client.post(
            "/api/executions", json={"objectif": "Un CRM", "brief": MODE_BRIEF_HUMAIN}
        ).json()["run_id"]
        _attendre_statut(client, run_id, EXECUTION_EN_ATTENTE_REPONSES)
        client.post(f"/api/executions/{run_id}/brief/reponses", json={"reponses": ["a", "b"]})

        rejoue = client.post(
            f"/api/executions/{run_id}/brief/reponses", json={"reponses": ["a", "b"]}
        )

        assert rejoue.status_code == 409


def test_reponses_a_un_run_inconnu_404(brief_a_questions):
    client, _, _ = _client_qui_attend(brief_a_questions)
    with client:
        reponse = client.post(
            "/api/executions/inconnu/brief/reponses", json={"reponses": []}
        )
    assert reponse.status_code == 404


def test_l_evenement_de_questions_annonce_le_plafond(brief_a_questions):
    """L'annonce voyage sur le bus : c'est par elle que l'UI sait ce qui reste."""
    event = evenement_questions_brief(
        DemandeClarification(
            run_id="r-9",
            objectif="Un CRM",
            brief=brief_a_questions,
            tour=2,
            tours_max=2,
        )
    )

    assert event.type == EVENEMENT_BRIEF_QUESTIONS
    assert event.tour == 2 and event.tours_max == 2
    assert "tour 2 sur 2" in event.detail
    assert "hypothèses" in event.detail  # le dernier tour se dit dernier
    assert event.brief is not None and len(event.brief.questions) == 2
    # Aller-retour JSON : les nouveaux champs survivent au bus Redis.
    relu = Event.from_dict(event.to_dict())
    assert relu.tour == 2 and relu.tours_max == 2


def test_les_reponses_survivent_a_l_aller_retour_json():
    """Le canal Redis sérialise en JSON : les réponses doivent s'y retrouver."""
    event = Event(type=EVENEMENT_BRIEF_REPONSES, run_id="r-10", reponses=["a", ""])

    relu = Event.from_dict(event.to_dict())

    assert relu.reponses == ["a", ""]
    # Absentes → None (« cet événement n'en dit rien »), jamais [] par défaut.
    assert Event.from_dict({"type": EVENEMENT_BRIEF_REPONSES}).reponses is None


# ------------------------------------- ④ l'arbitre seul : ce qui lève l'attente
#
# Différé au lot final (#323). La section ③ observe l'attente **par l'API** ;
# celle-ci prend l'arbitre isolément, pour les fins d'attente qu'aucun écran ne
# produit — un run annulé, un bus tombé, des réponses qui visent quelqu'un
# d'autre. Chacune peut laisser une écoute derrière elle, et une écoute
# abandonnée ne se voit de nulle part.


async def _laisse_l_arbitre_s_abonner() -> None:
    """Rend la main assez de tours pour que l'abonnement soit posé (cf. test_brief)."""
    for _ in range(5):
        await asyncio.sleep(0)


def _demande(brief: Brief) -> DemandeClarification:
    return DemandeClarification(
        run_id="r-11", objectif="Un CRM", brief=brief, tour=1, tours_max=2
    )


def test_l_arbitre_ignore_les_reponses_visant_un_autre_run(brief_a_questions):
    """Deux runs peuvent attendre en même temps : chacun ne prend que les siennes."""
    bus = InMemoryEventBus()

    async def _scenario() -> tuple[Clarification, ...]:
        arbitre = ArbitreClarificationControlTower(bus)
        attente = asyncio.create_task(arbitre(_demande(brief_a_questions)))
        await _laisse_l_arbitre_s_abonner()
        await bus.publish(
            Event(type=EVENEMENT_BRIEF_REPONSES, run_id="un-autre", reponses=["x", "y"])
        )
        await _laisse_l_arbitre_s_abonner()
        assert not attente.done()
        await bus.publish(
            Event(type=EVENEMENT_BRIEF_REPONSES, run_id="r-11", reponses=["interne", "50"])
        )
        return await asyncio.wait_for(attente, timeout=5)

    reponses = asyncio.run(_scenario())
    assert [clarification.reponse for clarification in reponses] == ["interne", "50"]


def test_une_question_sans_reponse_est_annoncee_au_lieu_d_etre_tue():
    """Taire une question sans réponse la ferait reposer à l'identique.

    Le tour suivant serait dépensé pour rien : le modèle ne peut pas savoir qu'il
    l'a déjà posée si le prompt ne la lui rappelle pas. La dire, c'est lui
    demander d'en faire une hypothèse et d'avancer — le pendant côté prompt de ce
    que `questions_en_hypotheses` garantit côté Python.
    """
    prompt = build_brief_user_prompt(
        "Un CRM minimal",
        clarifications=[
            Clarification(question="Interne ou public ?", reponse="Interne"),
            Clarification(question="Quel volume ?", reponse=""),
        ],
    )
    assert "Question : Interne ou public ?" in prompt
    assert "Réponse : Interne" in prompt
    assert "Question : Quel volume ?" in prompt
    assert "Ne la repose pas" in prompt


def test_le_dernier_tour_se_dit_dans_le_prompt_et_ferme_les_questions():
    """Une invitation, pas la garantie — celle-ci est tenue en Python, jamais par docilité."""
    clarifications = [Clarification(question="Quel volume ?", reponse="50")]
    ni_dernier = build_brief_user_prompt("Un CRM", clarifications=clarifications)
    dernier = build_brief_user_prompt(
        "Un CRM", clarifications=clarifications, dernier_tour=True
    )
    assert "DERNIER tour" in dernier
    assert "DERNIER tour" not in ni_dernier


def test_les_reponses_passent_apres_les_sources_donc_au_rang_le_plus_fort():
    """Ce qui est en dernier pèse le plus : les réponses de l'utilisateur font autorité.

    Une source est une **donnée** non fiable (ENF-13, docs/19 §2) ; une réponse
    est une **consigne** de la personne qui lance le run. L'ordre du prompt est
    ce qui porte cette différence — l'inverser laisserait un document téléversé
    donner le ton par-dessus l'utilisateur.
    """
    prompt = build_brief_user_prompt(
        "Un CRM",
        "## Source lue\n\ncontenu du document",
        [Clarification(question="Quel volume ?", reponse="50")],
    )
    assert prompt.index("contenu du document") < prompt.index("Réponse : 50")


class BusQuiSeReferme(InMemoryEventBus):
    """Un bus dont le flux se tarit : personne ne répondra jamais."""

    async def subscribe(self):
        return
        yield  # pragma: no cover - fait de `subscribe` un générateur asynchrone


def test_un_bus_referme_sans_reponses_leve_au_lieu_de_poursuivre(brief_a_questions):
    """Jamais un tour silencieusement sauté.

    Le brief partirait sinon en validation **avec ses questions intactes**, en
    laissant croire qu'on les a posées — le pire des deux mondes : le coût d'un
    tour de clarification, et l'ignorance d'avant.
    """

    async def _scenario():
        arbitre = ArbitreClarificationControlTower(BusQuiSeReferme())
        return await arbitre(_demande(brief_a_questions))

    with pytest.raises(RuntimeError) as capture:
        asyncio.run(_scenario())
    assert "r-11" in str(capture.value)


class BusQuiCompteSesAbonnes(InMemoryEventBus):
    """Un bus qui sait combien d'abonnements sont encore ouverts (cf. test_brief)."""

    def __init__(self) -> None:
        super().__init__()
        self.abonnements_ouverts = 0

    async def subscribe(self):
        self.abonnements_ouverts += 1
        try:
            async for event in super().subscribe():
                yield event
        finally:
            self.abonnements_ouverts -= 1


def test_un_run_annule_pendant_l_attente_des_reponses_referme_son_abonnement(
    brief_a_questions,
):
    """L'annulation est déjà exercée par l'API (§③) ; ici, ce qu'elle laisse derrière."""
    bus = BusQuiCompteSesAbonnes()

    async def _scenario() -> int:
        arbitre = ArbitreClarificationControlTower(bus)
        attente = asyncio.create_task(arbitre(_demande(brief_a_questions)))
        await _laisse_l_arbitre_s_abonner()
        assert bus.abonnements_ouverts == 1
        attente.cancel()
        with suppress(asyncio.CancelledError):
            await attente
        return bus.abonnements_ouverts

    assert asyncio.run(_scenario()) == 0


class BusQuiRefusePublier(BusQuiCompteSesAbonnes):
    """Un bus indisponible au moment de publier les questions."""

    async def publish(self, event: Event) -> None:
        raise RuntimeError("bus indisponible")


def test_un_bus_indisponible_ne_laisse_pas_l_ecoute_derriere_lui(brief_a_questions):
    """Les questions ne sont jamais parties : l'écoute qui les attendait n'a plus d'objet.

    C'est le seul chemin par lequel l'écoute survit à la sortie du `try` — une
    annulation du run, elle, l'a déjà emportée avec elle. Sans ce filet, un bus
    qui retombe laisse une tâche à l'écoute pour toujours, et rien ne la nomme.
    """
    bus = BusQuiRefusePublier()

    async def _scenario() -> int:
        arbitre = ArbitreClarificationControlTower(bus)
        with pytest.raises(RuntimeError):
            await arbitre(_demande(brief_a_questions))
        return bus.abonnements_ouverts

    assert asyncio.run(_scenario()) == 0


def test_l_arbitre_de_production_ne_se_connecte_pas_en_le_construisant():
    """Pendant exact d'`arbitre_brief_redis` : la connexion Redis est paresseuse.

    C'est ce qui permet à `maestro-run --publier` de se doter d'un arbitre sans
    exiger un Redis joignable — et ce que le réseau débranché de
    `tests/conftest.py` (#195) permet de vérifier ici.
    """
    assert isinstance(
        arbitre_clarification_redis("redis://localhost:6379/0"),
        ArbitreClarificationControlTower,
    )
