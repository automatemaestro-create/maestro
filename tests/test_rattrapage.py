"""Une tâche en échec se rattrape (ticket #1178).

Aucun appel réseau : le Chef de projet et les exécutants sont des `ModelProvider`
factices, et le Chef de projet rend des rattrapages **écrits d'avance** — ce que
ces tests éprouvent n'est pas la qualité du diagnostic d'un modèle (le banc des
scénarios le fait sur le réel) mais ce que Maestro **fait** de ce qu'il dit, et ce
qu'il **refuse** d'en faire.

Critère ① — une tâche en échec reçoit un diagnostic du modèle et une nouvelle
tentative **différente** (approche, agent, découpage, tâches aval ajustées) ; les
tâches aval repartent sans relancer le run ; une erreur non passagère n'est plus
rejouée à l'identique.

Critère ② — un échec que Maestro ne sait pas lever devient une question dans le
fil, avec la cause et les tentatives faites, et le run attend la réponse.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable

import pytest

from maestro.controltower.bridge import evenements_depuis_step
from maestro.engine import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    OrchestrationEngine,
    PolitiqueRelance,
)
from maestro.engine.executor import (
    STATUT_QUESTION_REPONDUE,
    STATUT_QUESTION_SANS_REPONSE,
    SUFFIXE_ETAPE_QUESTION,
    SUFFIXE_ETAPE_RELANCE,
)
from maestro.engine.guardrails import MOTS_SENSIBLES, Guardrails
from maestro.engine.questions import DemandeQuestion
from maestro.engine.rattrapage import (
    RATTRAPAGE_DEFAUT,
    SUFFIXE_ETAPE_RATTRAPAGE,
    PolitiqueRattrapage,
)
from maestro.orchestrator import Orchestrator, RattrapageValidationError, Task
from maestro.orchestrator.prompt import build_rattrapage_user_prompt, prompt_rattrapage
from maestro.orchestrator.rattrapage import (
    EchecDeTache,
    Tentative,
    valide_rattrapage,
)
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunCost, RunJournal, StepUsage, report_usage

#: La première ligne du playbook de rattrapage : c'est ainsi que le Chef de projet
#: factice sait qu'on lui demande un rattrapage plutôt qu'un plan.
_ENTETE_RATTRAPAGE = "# Playbook — Chef de projet : le rattrapage"

#: Ce que coûte un diagnostic, chez le Chef de projet factice.
_USAGE_DIAGNOSTIC = StepUsage(appels=1, tokens_entree=100, tokens_sortie=20, cout_usd=0.01)


class ChefDeProjet(ModelProvider):
    """Planificateur factice : rend le plan, puis les rattrapages écrits d'avance, dans l'ordre."""

    name = "chef"

    def __init__(self, plan: list[dict], rattrapages: list[dict | str] = ()) -> None:
        self._plan = json.dumps(plan, ensure_ascii=False)
        self._rattrapages = list(rattrapages)
        self.demandes: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt and system_prompt.startswith(_ENTETE_RATTRAPAGE):
            self.demandes.append(prompt)
            report_usage(_USAGE_DIAGNOSTIC)
            if not self._rattrapages:
                raise AssertionError(f"rattrapage inattendu :\n{prompt}")
            reponse = self._rattrapages.pop(0)
            return reponse if isinstance(reponse, str) else json.dumps(reponse, ensure_ascii=False)
        return self._plan


class Executant(ModelProvider):
    """Exécutant factice : son issue dépend du **marqueur** que porte la description.

    Chaque marqueur rend un livrable, lève une exception, ou rend une suite
    d'issues (une par appel). Un prompt qui n'en porte aucun est une erreur de test.
    """

    name = "executant"

    def __init__(self, issues: dict[str, object]) -> None:
        self._issues = issues
        self.appels: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def supports(self, model: str) -> bool:
        return True

    def compte(self, marqueur: str) -> int:
        return self.appels.count(marqueur)

    async def generate(self, prompt, *, model, system_prompt=None):
        for marqueur, issue in self._issues.items():
            if marqueur in prompt:
                self.appels.append(marqueur)
                self.prompts.setdefault(marqueur, []).append(prompt)
                if isinstance(issue, list):
                    issue = issue[min(self.compte(marqueur), len(issue)) - 1]
                if isinstance(issue, BaseException):
                    raise issue
                return issue
        raise AssertionError(f"prompt inattendu :\n{prompt}")


def _tache(
    ident: str,
    description: str,
    *,
    dependances: tuple[str, ...] = (),
    competences: tuple[str, ...] = ("backend",),
) -> dict:
    return {
        "id": ident,
        "titre": f"Tâche {ident}",
        "description": description,
        "competences_requises": list(competences),
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _plan_a_deux() -> list[dict]:
    """Une tâche qui échouera (`APPROCHE-A`), et une tâche aval qui l'attend."""
    return [
        _tache("t1", "Réaliser le module par APPROCHE-A."),
        _tache("t2", "Documenter le module (AVAL-T2).", dependances=("t1",)),
    ]


def _retenter(*taches: dict, nature: str = "configuration", aval: list[dict] | None = None) -> dict:
    verdict = {
        "nature": nature,
        "diagnostic": "L'accès à la ressource est refusé : rejouer n'y changera rien.",
        "geste": "retenter",
        "taches": list(taches),
    }
    if aval is not None:
        verdict["aval"] = aval
    return verdict


def _demander(question: str = "Pouvez-vous accorder l'accès au dépôt ?") -> dict:
    return {
        "nature": "configuration",
        "diagnostic": "Le jeton du projet n'a pas le droit d'écrire.",
        "geste": "demander",
        "question": question,
    }


#: Les politiques des tests : celle du rattrapage par défaut, et une relance à
#: trois tentatives sans attente — le juge de l'exécuteur n'est consulté que là où
#: une relance est possible.
_RATTRAPAGE = PolitiqueRattrapage()
_RELANCE = PolitiqueRelance(max_tentatives=3, backoff_s=0)


def _moteur(
    chef: ChefDeProjet,
    executant: Executant,
    *,
    rattrapage: PolitiqueRattrapage | None = _RATTRAPAGE,
    relance: PolitiqueRelance | None = _RELANCE,
    questionneur: Callable[[DemandeQuestion], Awaitable[str]] | None = None,
    attente_s: float = 5.0,
    guardrails: Guardrails | None = None,
) -> OrchestrationEngine:
    return OrchestrationEngine(
        executant,
        Orchestrator(chef, model="chef-modele"),
        relance=relance,
        rattrapage=rattrapage,
        questionneur=questionneur,
        bornes_question=BornesArbitrage(attente_s=attente_s),
        guardrails=guardrails,
    )


def _etapes(journal: RunJournal, suffixe: str):
    return [r for r in journal.records if r.etape.endswith(suffixe)]


# --- Critère ① : un diagnostic, et une tentative différente --------------------------------


def test_une_erreur_non_passagere_n_est_plus_rejouee_a_l_identique():
    """Le cas du ticket : un accès refusé échouait trois fois à l'identique.

    L'exception est une `RuntimeError` quelconque — `est_transitoire` la présume
    passagère, et c'est la relance aveugle que ce test attrape. Le juge du modèle
    la dit « configuration » : l'approche A n'est exécutée **qu'une fois**, la
    suivante est différente, et l'aval repart sur ce qu'elle a livré.
    """
    chef = ChefDeProjet(
        _plan_a_deux(),
        [_retenter(_tache("t1", "Réaliser le module par APPROCHE-B, sans la ressource refusée."))],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("403 : accès refusé à la ressource distante."),
            "APPROCHE-B": "livrable obtenu autrement",
            "AVAL-T2": "documentation écrite",
        }
    )
    journal = RunJournal()

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=journal))

    assert executant.compte("APPROCHE-A") == 1, executant.appels
    assert executant.compte("APPROCHE-B") == 1
    t1, t2 = rapport.resultats
    assert t1.ok and t1.task_id == "t1"
    assert t1.sortie == "livrable obtenu autrement"
    # L'aval repart **dans le même run**, sur le livrable de la nouvelle tentative.
    assert t2.ok
    assert "livrable obtenu autrement" in executant.prompts["AVAL-T2"][0]
    assert rapport.echouees == () and rapport.bloquees == ()
    # Aucune relance aveugle : le juge a dit « pas passager » avant la première.
    assert _etapes(journal, SUFFIXE_ETAPE_RELANCE) == []
    # Le diagnostic du modèle est au journal, sur la tâche, avec sa nature.
    (ligne,) = _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE)
    assert ligne.etape == f"t1{SUFFIXE_ETAPE_RATTRAPAGE}"
    assert "configuration" in ligne.sortie and "accès à la ressource est refusé" in ligne.sortie
    assert "403" in ligne.entree


def test_le_juge_lit_la_cause_et_l_erreur_encadree_comme_donnee():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [_retenter(_tache("t1", "Réaliser le module par APPROCHE-B."))],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("modèle inconnu : claude-inexistant"),
            "APPROCHE-B": "ok",
            "AVAL-T2": "ok",
        }
    )

    asyncio.run(_moteur(chef, executant).run("Construire le module", journal=RunJournal()))

    (prompt,) = chef.demandes
    assert "modèle inconnu : claude-inexistant" in prompt
    assert "Construire le module" in prompt
    # Ce qui attend la tâche est nommé : c'est ce qu'un ajustement d'aval vise.
    assert "`t2`" in prompt
    assert "DONNÉES" in prompt


def test_un_echec_passager_se_rejoue_a_l_identique_sans_nouveau_plan():
    """Le juge dit « passager » : la relance de l'exécuteur a lieu, comme avant."""
    chef = ChefDeProjet(
        _plan_a_deux(),
        [{"nature": "passager", "diagnostic": "Coupure réseau.", "geste": "rejouer"}],
    )
    executant = Executant(
        {
            "APPROCHE-A": [RuntimeError("connexion réinitialisée"), "livré au second essai"],
            "AVAL-T2": "ok",
        }
    )
    journal = RunJournal()

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=journal))

    assert executant.compte("APPROCHE-A") == 2
    assert [r.ok for r in rapport.resultats] == [True, True]
    (relance,) = _etapes(journal, SUFFIXE_ETAPE_RELANCE)
    assert "Coupure réseau" in relance.sortie


def test_changer_d_agent_se_fait_par_les_competences():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(
                _tache(
                    "t1",
                    "Écrire le schéma par APPROCHE-SQL.",
                    competences=("sql", "schema"),
                ),
                nature="approche",
            )
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("le module ne compile pas"),
            "APPROCHE-SQL": "schéma écrit",
            "AVAL-T2": "ok",
        }
    )

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=RunJournal()))

    t1, _ = rapport.resultats
    assert t1.ok
    assert t1.agent == "bdd"
    # Toujours la même tâche du plan : son identifiant et son titre ne bougent pas.
    assert (t1.task_id, t1.titre) == ("t1", "Tâche t1")


def test_un_redecoupage_remplace_la_tache_et_l_aval_repart():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(
                _tache("preparer", "Préparer le terrain : PREPARER."),
                _tache("reprendre", "Reprendre le module : REPRENDRE.", dependances=("preparer",)),
                nature="approche",
            )
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("la tâche est trop grosse pour un seul passage"),
            "PREPARER": "terrain prêt",
            "REPRENDRE": "module repris",
            "AVAL-T2": "ok",
        }
    )
    journal = RunJournal()

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=journal))

    assert executant.appels.index("PREPARER") < executant.appels.index("REPRENDRE")
    # La reprise reçoit le livrable de la préparation, comme une tâche du plan.
    assert "terrain prêt" in executant.prompts["REPRENDRE"][0]
    t1, t2 = rapport.resultats
    assert t1.ok and t1.task_id == "t1"
    assert "terrain prêt" in t1.sortie and "module repris" in t1.sortie
    assert t2.ok
    # Les tâches du redécoupage sont au journal sous la tâche qu'elles remplacent,
    # et la tâche du plan s'y solde « terminée » : sa carte change de colonne.
    etapes = {r.etape for r in journal.records}
    assert {"t1-r1-preparer", "t1-r1-reprendre"} <= etapes
    assert [r.statut for r in journal.records if r.etape == "t1"][-1] == "terminee"


def test_les_taches_aval_sont_ajustees_avant_de_repartir():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(
                _tache("t1", "Réaliser le module par APPROCHE-B, en GraphQL."),
                aval=[{"id": "t2", "description": "Documenter l'API GraphQL (AVAL-T2)."}],
            )
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("REST indisponible"),
            "APPROCHE-B": "ok",
            "AVAL-T2": "ok",
        }
    )

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=RunJournal()))

    assert rapport.resultats[1].ok
    assert "Documenter l'API GraphQL" in executant.prompts["AVAL-T2"][0]


def test_une_proposition_identique_a_l_echec_est_refusee_et_devient_une_question():
    """L'exécution vérifie ce que le modèle écrit : rien de changé, rien d'exécuté."""
    questions: list[DemandeQuestion] = []

    async def questionneur(demande: DemandeQuestion) -> str:
        questions.append(demande)
        return "Abandonnez cette tâche."

    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(_tache("t1", "Réaliser le module par APPROCHE-A.")),
            {
                "nature": "configuration",
                "diagnostic": "L'utilisateur demande d'abandonner.",
                "geste": "abandonner",
            },
        ],
    )
    executant = Executant({"APPROCHE-A": RuntimeError("403 : accès refusé"), "AVAL-T2": "ok"})
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(chef, executant, questionneur=questionneur).run("Objectif", journal=journal)
    )

    assert executant.compte("APPROCHE-A") == 1
    assert len(questions) == 1
    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and t2.statut == STATUT_BLOQUEE
    assert any("identique" in r.sortie for r in _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE))


def test_le_cout_des_diagnostics_et_des_tentatives_entre_au_rapport_comme_au_journal():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [_retenter(_tache("t1", "Réaliser le module par APPROCHE-B."))],
    )
    executant = Executant(
        {"APPROCHE-A": RuntimeError("403"), "APPROCHE-B": "ok", "AVAL-T2": "ok"}
    )
    journal = RunJournal()

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=journal))

    # Deux diagnostics au plus ici (le juge de l'exécuteur, puis celui de la
    # boucle, qui réemploie le premier) : un seul appel, un seul coût.
    assert len(chef.demandes) == 1
    assert rapport.usage_totale.cout_usd == pytest.approx(_USAGE_DIAGNOSTIC.cout_usd)
    grand_livre = RunCost.depuis_journal(journal)
    (t1,) = [t for t in grand_livre.taches if t.tache_id == "t1"]
    assert t1.usage.cout_usd == pytest.approx(_USAGE_DIAGNOSTIC.cout_usd)


# --- Ce qui ne se rattrape pas ------------------------------------------------------------


def test_un_refus_humain_ne_se_rattrape_pas():
    """Réécrire une tâche pour qu'elle passe là où un humain a dit non serait un contournement."""
    chef = ChefDeProjet([_tache("deploiement", "Déployer l'API en production (APPROCHE-A).")])
    executant = Executant({"APPROCHE-A": "déployé"})

    rapport = asyncio.run(
        _moteur(
            chef,
            executant,
            guardrails=Guardrails(validateur=lambda demande: False, mots_sensibles=MOTS_SENSIBLES),
        ).run("Objectif", journal=RunJournal())
    )

    (resultat,) = rapport.resultats
    assert resultat.statut == STATUT_ECHEC
    assert resultat.rattrapable is False
    assert chef.demandes == []


def test_un_budget_epuise_ne_se_rattrape_pas():
    chef = ChefDeProjet(_plan_a_deux())

    class Couteux(Executant):
        async def generate(self, prompt, *, model, system_prompt=None):
            report_usage(StepUsage(appels=1, cout_usd=1.0))
            return await super().generate(prompt, model=model, system_prompt=system_prompt)

    executant = Couteux({"APPROCHE-A": "livré", "AVAL-T2": "ok"})

    rapport = asyncio.run(
        _moteur(chef, executant, guardrails=Guardrails(plafond_cout_usd=0.5)).run(
            "Objectif", journal=RunJournal()
        )
    )

    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "plafond de dépense" in (t1.erreur or "")
    assert chef.demandes == []
    assert t2.statut == STATUT_BLOQUEE


def test_un_diagnostic_qui_depense_le_budget_ne_pose_pas_de_question_pour_rien():
    """Le diagnostic coûte, sous le même plafond : budget dépensé, rien de plus n'est engagé."""
    questions: list[DemandeQuestion] = []

    async def questionneur(demande: DemandeQuestion) -> str:
        questions.append(demande)
        return "réponse"

    chef = ChefDeProjet(
        _plan_a_deux(),
        [_retenter(_tache("t1", "APPROCHE-B.")) for _ in range(4)],
    )
    executant = Executant({"APPROCHE-A": RuntimeError("403"), "APPROCHE-B": "ok", "AVAL-T2": "ok"})

    rapport = asyncio.run(
        _moteur(
            chef,
            executant,
            questionneur=questionneur,
            # Sans relance, l'exécuteur ne juge rien : c'est le diagnostic de la
            # boucle, et lui seul, qui franchit le plafond.
            relance=None,
            guardrails=Guardrails(plafond_cout_usd=_USAGE_DIAGNOSTIC.cout_usd / 2),
        ).run("Objectif", journal=RunJournal())
    )

    t1, t2 = rapport.resultats
    assert len(chef.demandes) == 1
    assert t1.statut == STATUT_ECHEC and "budget" in (t1.erreur or "")
    assert executant.compte("APPROCHE-B") == 0
    assert questions == []
    assert t2.statut == STATUT_BLOQUEE


def test_sans_politique_le_comportement_historique_est_inchange():
    chef = ChefDeProjet(_plan_a_deux())
    executant = Executant({"APPROCHE-A": RuntimeError("403"), "AVAL-T2": "ok"})

    rapport = asyncio.run(
        _moteur(chef, executant, rattrapage=None).run("Objectif", journal=RunJournal())
    )

    assert executant.compte("APPROCHE-A") == 3
    assert chef.demandes == []
    assert [r.statut for r in rapport.resultats] == [STATUT_ECHEC, STATUT_BLOQUEE]


def test_le_rattrapage_est_arme_par_defaut_sur_le_moteur_des_vrais_runs():
    parametres = inspect.signature(OrchestrationEngine.default).parameters
    assert parametres["rattrapage"].default == RATTRAPAGE_DEFAUT
    assert inspect.signature(OrchestrationEngine.__init__).parameters["rattrapage"].default is None


# --- Critère ② : ce que Maestro ne sait pas lever devient une question ---------------------


def test_un_echec_que_maestro_ne_sait_pas_lever_devient_une_question_et_le_run_attend():
    reponse_donnee = asyncio.Event()
    questions: list[DemandeQuestion] = []

    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(_tache("t1", "Réaliser le module par APPROCHE-B.")),
            _demander("Pouvez-vous accorder le droit d'écriture au jeton du projet ?"),
            _retenter(_tache("t1", "Réaliser le module par APPROCHE-C, avec le jeton accordé.")),
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("403 : écriture refusée sur le dépôt"),
            "APPROCHE-B": RuntimeError("403 : écriture refusée, même par l'autre chemin"),
            "APPROCHE-C": "poussé",
            "AVAL-T2": "ok",
        }
    )

    async def questionneur(demande: DemandeQuestion) -> str:
        questions.append(demande)
        # Le run **attend** : rien de l'aval n'est parti pendant la question.
        assert executant.compte("AVAL-T2") == 0
        await asyncio.sleep(0.05)
        assert executant.compte("AVAL-T2") == 0
        reponse_donnee.set()
        return "C'est fait, le jeton peut écrire."

    journal = RunJournal()
    rapport = asyncio.run(
        _moteur(chef, executant, questionneur=questionneur).run("Objectif", journal=journal)
    )

    (question,) = questions
    # Dans le fil : la question du modèle, la cause, et les tentatives faites.
    assert question.question.startswith("Pouvez-vous accorder le droit d'écriture")
    assert "écriture refusée, même par l'autre chemin" in question.question
    assert "APPROCHE-A" not in question.hypothese
    assert "Tentatives" in question.question
    assert question.question.count("\n1. ") == 1 and "\n2. " in question.question
    assert question.tache_id == "t1" and question.agent == "orchestrateur"
    assert question.run_id == journal.run_id
    assert "t2" in question.hypothese or "Tâche t2" in question.hypothese
    # La réponse revient au Chef de projet, qui en tient compte.
    assert "C'est fait, le jeton peut écrire." in chef.demandes[-1]
    t1, t2 = rapport.resultats
    assert t1.ok and t1.sortie == "poussé"
    assert t2.ok
    (echange,) = _etapes(journal, SUFFIXE_ETAPE_QUESTION)
    assert echange.statut == STATUT_QUESTION_REPONDUE


def test_des_tentatives_epuisees_deviennent_une_question():
    questions: list[DemandeQuestion] = []

    async def questionneur(demande: DemandeQuestion) -> str:
        questions.append(demande)
        return "Laissez tomber."

    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(_tache("t1", "APPROCHE-B.")),
            # Jugé par l'exécuteur sur l'échec de B — mais la tentative ne part
            # pas : la seule permise a été faite. Son diagnostic va à la question.
            _retenter(_tache("t1", "APPROCHE-C."), nature="approche"),
            {"nature": "approche", "diagnostic": "Abandon demandé.", "geste": "abandonner"},
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("échec A"),
            "APPROCHE-B": RuntimeError("échec B"),
            "AVAL-T2": "ok",
        }
    )

    rapport = asyncio.run(
        _moteur(
            chef,
            executant,
            questionneur=questionneur,
            rattrapage=PolitiqueRattrapage(max_tentatives=1),
        ).run("Objectif", journal=RunJournal())
    )

    assert len(questions) == 1
    assert "échec B" in questions[0].question
    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "Laissez tomber" in (t1.erreur or "")
    assert t2.statut == STATUT_BLOQUEE


def test_sans_question_permise_l_issue_dit_ce_qui_a_ete_tente():
    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            _retenter(_tache("t1", "APPROCHE-B.")),
            _retenter(_tache("t1", "APPROCHE-C."), nature="approche"),
        ],
    )
    executant = Executant(
        {
            "APPROCHE-A": RuntimeError("échec A"),
            "APPROCHE-B": RuntimeError("échec B"),
            "AVAL-T2": "ok",
        }
    )

    rapport = asyncio.run(
        _moteur(
            chef,
            executant,
            rattrapage=PolitiqueRattrapage(max_tentatives=1, max_questions=0),
        ).run("Objectif", journal=RunJournal())
    )

    t1, t2 = rapport.resultats
    assert "1 tentative(s) sans succès" in (t1.erreur or "")
    assert "plus aucune question" in (t1.erreur or "")
    assert executant.compte("APPROCHE-C") == 0
    assert t2.statut == STATUT_BLOQUEE


def test_une_question_sans_reponse_laisse_la_tache_en_echec_et_le_dit():
    async def personne(demande: DemandeQuestion) -> str:
        await asyncio.Event().wait()
        return ""

    chef = ChefDeProjet(_plan_a_deux(), [_demander()])
    executant = Executant({"APPROCHE-A": RuntimeError("403"), "AVAL-T2": "ok"})
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(chef, executant, questionneur=personne, attente_s=0.05).run(
            "Objectif", journal=journal
        )
    )

    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "sans réponse" in (t1.erreur or "")
    assert t2.statut == STATUT_BLOQUEE
    (echange,) = _etapes(journal, SUFFIXE_ETAPE_QUESTION)
    assert echange.statut == STATUT_QUESTION_SANS_REPONSE


def test_sans_personne_a_qui_demander_l_echec_est_garde_et_dit_pourquoi():
    chef = ChefDeProjet(_plan_a_deux(), [_demander()])
    executant = Executant({"APPROCHE-A": RuntimeError("403"), "AVAL-T2": "ok"})

    rapport = asyncio.run(_moteur(chef, executant).run("Objectif", journal=RunJournal()))

    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "personne" in (t1.erreur or "")
    assert t2.statut == STATUT_BLOQUEE


def test_un_diagnostic_illisible_devient_une_question_plutot_qu_une_relance_aveugle():
    questions: list[DemandeQuestion] = []

    async def questionneur(demande: DemandeQuestion) -> str:
        questions.append(demande)
        return "Abandonnez."

    chef = ChefDeProjet(
        _plan_a_deux(),
        [
            "je ne sais pas",  # le juge de l'exécuteur : illisible → présomption
            "toujours pas de JSON",  # la boucle : illisible → question
            {"nature": "approche", "diagnostic": "Abandon.", "geste": "abandonner"},
        ],
    )
    executant = Executant({"APPROCHE-A": RuntimeError("403"), "AVAL-T2": "ok"})

    rapport = asyncio.run(
        _moteur(
            chef,
            executant,
            questionneur=questionneur,
            relance=PolitiqueRelance(max_tentatives=2, backoff_s=0),
        ).run("Objectif", journal=RunJournal())
    )

    # Juge injoignable à l'étage de l'exécuteur : on retombe sur la présomption
    # d'avant (une relance), jamais sur un échec inventé.
    assert executant.compte("APPROCHE-A") == 2
    assert len(questions) == 1
    assert rapport.resultats[0].statut == STATUT_ECHEC


# --- Le contrat du Chef de projet ----------------------------------------------------------


def _echec(**kwargs) -> EchecDeTache:
    tache = Task.from_dict(_tache("t1", "Réaliser le module par APPROCHE-A."))
    return EchecDeTache(
        tache=tache,
        tentatives=(
            Tentative(taches=(tache,), agent="developpeur", role="Développeur", erreur="403"),
        ),
        aval=(Task.from_dict(_tache("t2", "Documenter.", dependances=("t1",))),),
        **kwargs,
    )


def test_un_echec_non_passager_ne_se_rejoue_pas():
    with pytest.raises(RattrapageValidationError, match="ne se rejoue pas"):
        valide_rattrapage(
            {"nature": "configuration", "diagnostic": "403", "geste": "rejouer"}, _echec()
        )


def test_abandonner_n_appartient_qu_a_l_utilisateur():
    verdict = {"nature": "approche", "diagnostic": "…", "geste": "abandonner"}
    with pytest.raises(RattrapageValidationError, match="utilisateur"):
        valide_rattrapage(verdict, _echec())
    assert valide_rattrapage(verdict, _echec(reponse="abandonnez")).geste == "abandonner"


def test_un_rattrapage_n_ajoute_aucun_acte_accorde():
    tache = _tache("t1", "Autre approche.")
    tache["acte_accorde"] = "supprimer tout le contenu du dossier"
    with pytest.raises(RattrapageValidationError, match="acte accordé"):
        valide_rattrapage(_retenter(tache), _echec())


def test_un_redecoupage_se_valide_comme_un_plan():
    cycle = _retenter(
        _tache("a", "A", dependances=("b",)),
        _tache("b", "B", dependances=("a",)),
    )
    with pytest.raises(RattrapageValidationError, match="Cycle"):
        valide_rattrapage(cycle, _echec())


def test_un_ajustement_ne_vise_que_l_aval():
    with pytest.raises(RattrapageValidationError, match="n'attend pas"):
        valide_rattrapage(
            _retenter(_tache("t1", "Autre."), aval=[{"id": "t9", "description": "x"}]),
            _echec(),
        )


def test_le_prompt_de_rattrapage_porte_l_equipe_et_la_reponse():
    systeme = prompt_rattrapage()
    assert systeme.startswith(_ENTETE_RATTRAPAGE)
    assert "{{" not in systeme
    prompt = build_rattrapage_user_prompt(_echec(question="Accès ?", reponse="Accordé."))
    assert prompt.rstrip().endswith("Accordé.")
    assert "Accès ?" in prompt


def test_une_erreur_demesuree_est_bornee_par_la_fin():
    tache = Task.from_dict(_tache("t1", "X"))
    erreur = "PREMIERE-LIGNE " + "x" * 20_000 + " CAUSE FINALE"
    echec = EchecDeTache(
        tache=tache,
        tentatives=(Tentative(taches=(tache,), agent="a", role="r", erreur=erreur),),
    )
    prompt = build_rattrapage_user_prompt(echec)
    assert "CAUSE FINALE" in prompt and "PREMIERE-LIGNE" not in prompt
    assert len(prompt) < 10_000


# --- La Control Tower lit les lignes du rattrapage sur leur tâche -------------------------


def test_une_ligne_de_rattrapage_est_une_activite_de_sa_tache():
    (event,) = evenements_depuis_step(
        {
            "run_id": "r1",
            "etape": f"t1{SUFFIXE_ETAPE_RATTRAPAGE}",
            "nom": "Rattrapage — Tâche t1",
            "agent": "orchestrateur",
            "role": "Orchestrateur",
            "statut": "rattrapage_retenter",
            "entree": "403",
            "sortie": "configuration — …",
            "usage": StepUsage().to_dict(),
            "horodatage": "2026-09-27T10:00:00+00:00",
        }
    )
    assert event.type == "agent.activite"
    assert event.tache_id == "t1"
