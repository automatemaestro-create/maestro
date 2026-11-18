"""After decomposition, the orchestrator confronts the team with the plan (#1227).

The defect, measured on 2026-09-22 on project `p1`: the team proposed to the empty
project was a single developer — ✅ for the request of the time. Then the person
asks for « une petite animation du logo Maestro ». The run decomposes into four
tasks (stylised logo, animation script, launch test, documentation) and all four
go to `dev`. *« Aucune modification de l'équipe n'a été suggérée, du coup un seul
agent développeur a tout fait. »*

Two halves, and both were needed — that is what is kept here:

① **decomposition plans for the need.** The Project lead's playbook forbade any
   tag that no role in place carried (« tu n'emploies que les compétences de
   l'équipe ci-dessous »): the plan was therefore cut to the team, never to the
   need, and the missing role was not even named;
② **the orchestrator confronts, then proposes.** Between the plan and the first
   task, the gap is computed, recorded, and proposed to a person. Accepting
   completes the team and the run goes on with it; declining or not answering
   lets the run continue, **saying so**.

What #1227 overturns, and what it does not touch: the last two bullets of
[docs/37 §3](../docs/37-decision-equipe-sur-mesure.md) — « recruter reste un
geste validé hors du run », « l'équipe ne se forme pas dans un run » — no longer
hold. Nothing is recruited without consent (#1040), an **agent** never recruits
([docs/31 §3.5](../docs/31-decision-surface-ecriture-agents.md): here the
orchestrator proposes and a person decides), and the brief is validated before
decomposition (D5).

No network and no real model call: a **fake model provider**, throwaway
repositories, in-memory bus. The trial's plan is replayed as is — an animation
goal, a project that has only one developer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_AGENT_ACTIVITE
from maestro.engine.executor import STATUT_ROLE_MANQUANT
from maestro.engine.loop import OrchestrationEngine
from maestro.engine.renfort import (
    STATUT_RENFORT_DECLINE,
    STATUT_RENFORT_RECRUTE,
    STATUT_RENFORT_SANS_REPONSE,
    DecisionRenfort,
    DemandeRenfort,
)
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.prompt import orchestrator_prompt
from maestro.orchestrator.schema import Task
from maestro.outillage.modele import Constats, Langage
from maestro.outillage.recommandation import recommander
from maestro.providers.base import ModelProvider
from maestro.team import missing_role, plan_gap, propose_reinforcement
from maestro.telemetry import STEP_TEAM, RunJournal

PROJECT = "prj-p1"

#: The trial's goal, word for word.
GOAL = "une petite animation du logo Maestro"

#: The plan the trial produced — four tasks —, but **written for the need** as the
#: playbook now asks: the animation and the logo require `ui`, which the
#: single-developer team does not cover.
PLAN = [
    {
        "id": "logo-stylise",
        "titre": "Dessiner le logo stylisé",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["ui", "design-system"],
        "format_sortie": "SVG",
        "dependances": [],
    },
    {
        "id": "script-animation",
        "titre": "Écrire le script d'animation",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["ui"],
        "format_sortie": "JS",
        "dependances": ["logo-stylise"],
    },
    {
        "id": "test-lancement",
        "titre": "Vérifier le lancement",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["frontend"],
        "format_sortie": "rapport",
        "dependances": ["script-animation"],
    },
]


def _agent(name: str, *competencies: str, role: str = "Rôle") -> Agent:
    return Agent(
        nom=name,
        role=role,
        competences=frozenset(competencies),
        modele="m",
        prompt_systeme="…",
    )


def _task(title: str, *competencies: str) -> Task:
    return Task(
        id=title.lower().replace(" ", "-"),
        title=title,
        description=f"Faire {title}.",
        required_competencies=competencies,
        output_format="markdown",
    )


def _definition(name: str, *competencies: str) -> AgentDefinition:
    return AgentDefinition(
        nom=name,
        role="Développeur",
        competences=competencies,
        playbook="Tu écris le code de p1.",
    )


def _findings() -> Constats:
    """The trial's project: Python, and nothing that designates a designer."""
    return Constats(
        langages=(Langage(nom="Python", fichiers=12, part=1.0, exemple="src/app.py"),)
    )


# --- ① Decomposition names the need, even outside the team -------------------


def test_playbook_offers_the_trades_maestro_can_recruit() -> None:
    """The plan was cut to the team: a single developer, hence never any `ui`.

    The faulty sample is the guard from before this lot — « tu n'emploies que les
    compétences de l'équipe ci-dessous » —, which must no longer read as is, and
    the list of recruitable trades that must have replaced it.
    """
    dev = next(a for a in GABARITS_DU_CODE if a.nom == "developpeur")

    prompt = orchestrator_prompt([dev])

    assert "Découpe pour le besoin, pas pour l'équipe" in prompt
    assert "nomme-le quand même" in prompt
    # The trades the team does not have are there, with their role template.
    assert "ui" in prompt
    assert "gabarit `interface`" in prompt
    # …and the former guard no longer reads there.
    assert "eux seuls, pour `competences_requises`" not in prompt


def test_playbook_proposes_no_reinforcement_to_a_complete_team() -> None:
    """The control: a team covering the whole catalogue has nothing to recruit.

    And the document **says so** rather than leaving a blank — a hole in a system
    prompt reads like a forgotten placeholder.
    """
    prompt = orchestrator_prompt(GABARITS_DU_CODE)

    assert "l'équipe couvre déjà tous les métiers" in prompt


def test_playbook_says_what_becomes_of_a_named_trade() -> None:
    """Naming an absent trade is not free: it is a question asked.

    The playbook must say both: that the gap is **proposed** (otherwise the model
    has no reason to name a tag nobody takes), and that a tag added as a
    precaution costs a human wait.
    """
    prompt = orchestrator_prompt([_agent("dev", "backend")])

    assert "confronte l'équipe à ce que tu as demandé" in prompt
    assert "par précaution" in prompt


def test_a_plan_naming_an_absent_trade_is_valid_and_keeps_its_tags() -> None:
    """The output contract does not bound the tags, and it must not start to.

    Fake model provider: it returns the trial's plan, written for the need. What is
    checked is that `ui` passes schema validation — a tag outside the team is
    **information**, not a form error.
    """
    plan = asyncio.run(
        Orchestrator(_ScriptedPlan(), model="m").plan(GOAL, team=[_agent("dev", "backend")])
    )

    assert [t.id for t in plan] == ["logo-stylise", "script-animation", "test-lancement"]
    assert "ui" in plan[0].required_competencies


# --- ① bis The plan's gap: computed, named, and only one ----------------------


def test_plan_gap_names_the_position_and_the_tasks_awaiting_it() -> None:
    """The raw fact of #1227: the union of what the plan requires, minus the team.

    The named tasks are those **this role** would take, never all those requiring
    something absent: the retained role template does not necessarily cover the
    whole gap, and too broad a list would sell the recruitment for more than it
    is.
    """
    tasks = [
        _task("Dessiner le logo", "ui", "design-system"),
        _task("Animer le logo", "ui"),
        _task("Vérifier le lancement", "backend"),
    ]

    gap = plan_gap(tasks, [_agent("dev", "backend")])

    assert gap is not None
    assert gap.gap.role_template == "interface"
    assert gap.gap.role == "Designer"
    assert gap.recruitable
    assert gap.tasks == ("Dessiner le logo", "Animer le logo")


def test_gap_keeps_only_the_tasks_of_the_proposed_role() -> None:
    """Two trades are missing, a single position is proposed — and its tasks with it.

    `sql` is not covered either, but the designer will not take it: its task
    therefore does not enter what the card announces.
    """
    tasks = [
        _task("Dessiner le logo", "ui"),
        _task("Écrire la migration", "sql", "migration"),
    ]

    gap = plan_gap(tasks, [_agent("dev", "backend")])

    assert gap is not None
    assert gap.tasks in (("Dessiner le logo",), ("Écrire la migration",))
    # A single position, and its tasks are its own: never both lists.
    assert len(gap.tasks) == 1


@pytest.mark.parametrize(
    ("tasks", "team"),
    [
        pytest.param(
            [_task("Ajouter la pagination", "backend")],
            [_agent("dev", "backend")],
            id="tout-est-couvert",
        ),
        pytest.param(
            [_task("Dessiner le logo", "ui")],
            [],
            id="equipe-vide",
        ),
        pytest.param([], [_agent("dev", "backend")], id="plan-vide"),
    ],
)
def test_nothing_is_missing_when_nothing_is_missing(
    tasks: list[Task], team: list[Agent]
) -> None:
    """Three abstentions, and the second is the most important.

    An **empty team** is not an incomplete team: that project is offered its whole
    team before even opening a run (#1146), and deducing « a role is missing »
    here would propose a single position where a team is needed.
    """
    assert plan_gap(tasks, team) is None


def test_a_gap_without_role_template_is_named_but_not_proposed() -> None:
    """Maestro does not know how to recruit for `comptabilite`: it says so, without inventing.

    A made-up role would read as a catalogue role, and what we would propose to
    create would have neither playbook nor competencies.
    """
    gap = plan_gap(
        [_task("Tenir les comptes", "comptabilite")], [_agent("dev", "backend")]
    )

    assert gap is not None
    assert not gap.recruitable
    assert "comptabilite" in gap.sentence()


def test_journal_sentence_names_the_tasks_and_the_reason_does_not_repeat_them() -> None:
    """Two texts, two readers: the journal tells, the card displays separately.

    The reason goes into the chat card, which lists the tasks in its own block;
    repeating them in the sentence would read the same thing as two facts.
    """
    gap = plan_gap([_task("Dessiner le logo", "ui")], [_agent("dev", "backend")])

    assert gap is not None
    assert "Dessiner le logo" in gap.sentence()
    assert "Dessiner le logo" not in gap.reason()


# --- ① ter The proposed role is a full-fledged role --------------------------


def test_reinforcement_proposes_a_single_role_justified_by_the_plan() -> None:
    """A designer is justified by no finding of a Python project.

    That is why it cannot come out of the ordinary derivation: that one would
    exclude it, and rightly so. Its justification is the **plan**, which only the
    caller knows.
    """
    findings = _findings()
    gap = missing_role(("ui", "ux"))
    assert gap is not None

    proposal = propose_reinforcement(
        findings,
        recommander(findings),
        gap,
        "le plan de ce travail demande ui, ux",
        project_id=PROJECT,
        taken_names=("dev",),
    )

    assert [r.role_template for r in proposal.roles] == ["designer"]
    assert proposal.roles[0].reason == "le plan de ce travail demande ui, ux"
    # No evidence: no file of the project designates this role, and inventing one
    # would be worse than having none.
    assert proposal.roles[0].justification is None
    # Nothing is created, and the proposal says so like the others.
    assert proposal.to_dict()["cree"] is False


def test_reinforcement_role_carries_playbook_permissions_and_intent() -> None:
    """Not a cut-rate definition: everything a proposed role carries, it carries."""
    findings = _findings()
    gap = missing_role(("ui",))
    assert gap is not None

    role = propose_reinforcement(
        findings, recommander(findings), gap, "le plan le demande", project_id=PROJECT
    ).roles[0]

    assert role.playbook.strip()
    assert role.playbook_origin
    assert role.intent.strip()
    assert role.competencies


def test_a_reinforcement_without_role_template_is_refused_rather_than_invented() -> None:
    findings = _findings()
    gap = missing_role(("comptabilite",))
    assert gap is not None and gap.role_template is None

    with pytest.raises(ValueError, match="aucun gabarit ne couvre"):
        propose_reinforcement(findings, recommander(findings), gap, "raison")


def test_a_reinforcement_excludes_the_orchestrator_and_nobody_else() -> None:
    """The roles already in place have not been examined: naming them excluded would
    be false. The orchestrator, for its part, is, whatever the moment (docs/37 §4.2)."""
    findings = _findings()
    gap = missing_role(("ui",))
    assert gap is not None

    proposal = propose_reinforcement(
        findings, recommander(findings), gap, "raison", project_id=PROJECT
    )

    assert [e.name for e in proposal.excluded] == ["orchestrateur"]


# --- ② The loop confronts, records, and goes on whatever happens -------------


class _ScriptedPlan(ModelProvider):
    """Fake provider: it returns **the trial's plan**, written for the need.

    A single double for the whole lot, and it holds the three roles the loop asks
    of it: decompose (the plan above), **classify** an ambiguous routing — the
    router's fallback onto an active agent, the one the trial suffered — and
    produce some deliverable. That is what allows playing the whole loop without
    network and without a real model call.

    The classifier is made **conclusive** on purpose: without it, a task nobody
    covers would leave as « to assign », and the test would no longer show what
    motivated the ticket — an animation handed to a developer.
    """

    name = "plan-scripte"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json
        import re

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(PLAN)
        candidates = re.findall(r"^- (\S+) \(", prompt, flags=re.MULTILINE)
        if "Agents candidats" in prompt and candidates:
            # The first candidate, that is the order of the team received: the
            # deterministic tiebreak the real classifier would return in language.
            return json.dumps({"agent": candidates[0], "confiance": 0.6})
        return "LIVRABLE"


class _SpyArbiter:
    """An `ArbitreRenfort` that notes the request received and returns the wanted decision.

    It **really recruits** when `agents` is passed to it: it writes the definition
    into the project's repository, exactly as the real creation (#1040) does.
    That is what allows checking the second criterion end to end — the tasks go to
    the new role — without mounting the API.
    """

    def __init__(
        self,
        decision: DecisionRenfort,
        *,
        agents: AgentStore | None = None,
        definition: AgentDefinition | None = None,
    ) -> None:
        self.requests: list[DemandeRenfort] = []
        self._decision = decision
        self._agents = agents
        self._definition = definition

    async def __call__(self, request: DemandeRenfort) -> DecisionRenfort:
        self.requests.append(request)
        if self._agents is not None and self._definition is not None:
            self._agents.pour_projet(request.projet_id).ecrire(self._definition)
        return self._decision


class _HesitantScriptedPlan(_ScriptedPlan):
    """The classifier **of the real stack** (#1260): it abstains when nobody has the trade.

    Replayed from the 2026-09-24 close-out, trial 2: « le classifieur n'a pas
    départagé dev avec assez de confiance (0.10 < seuil 0.60) ». It is the honest
    answer to the question it is usually asked — *who is competent?* — when no
    candidate has the trade, and it is what sent « to assign » the tasks of a run
    the person had nonetheless let continue.

    To the **closest** question, on the other hand, it designates: `preferred` if
    it is a candidate, otherwise the first. That is what allows seeing, on a team
    of several roles, that the choice does come from the model and not from the
    order.
    """

    def __init__(self, preferred: str = "") -> None:
        self._preferred = preferred
        self.closest_questions = 0

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json
        import re

        from maestro.router.classifier import CLASSIFIER_CLOSEST_SYSTEM_PROMPT

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(PLAN)
        candidates = re.findall(r"^- (\S+) \(", prompt, flags=re.MULTILINE)
        if "Agents candidats" in prompt and candidates:
            if system_prompt == CLASSIFIER_CLOSEST_SYSTEM_PROMPT:
                self.closest_questions += 1
                chosen = self._preferred if self._preferred in candidates else candidates[0]
                return json.dumps({"agent": chosen, "confiance": 0.3})
            return json.dumps({"agent": None, "confiance": 0.1})
        return "LIVRABLE"


def _engine(
    tmp_path: Path,
    *,
    team: AgentDefinition | None = None,
    arbiter: Any = None,
    provider: ModelProvider | None = None,
    reinforcements: tuple[AgentDefinition, ...] = (),
) -> tuple[OrchestrationEngine, AgentStore]:
    """The trial's engine: a project that has only one developer."""
    agents = AgentStore(tmp_path / "agents")
    if team is not None:
        agents.pour_projet(PROJECT).ecrire(team)
    for definition in reinforcements:
        agents.pour_projet(PROJECT).ecrire(definition)
    provider = provider if provider is not None else _ScriptedPlan()
    engine = OrchestrationEngine(
        provider,
        Orchestrator(provider, model="m"),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
        arbitre_renfort=arbiter,
    )
    return engine, agents


def _team_lines(journal: RunJournal) -> list[Any]:
    """The journal's `equipe` lines — the gap, then the outcome of the request."""
    return [r for r in journal.records if r.etape == STEP_TEAM]


def test_request_carries_the_role_its_reason_and_the_tasks_it_would_take(
    tmp_path: Path,
) -> None:
    """The first criterion, end to end: plan → gap → proposal.

    Fake model provider, project with a single `dev`, animation goal. What the
    arbiter receives is what the chat card will display.
    """
    arbiter = _SpyArbiter(DecisionRenfort(approuve=False, detail="non merci"))
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert len(arbiter.requests) == 1
    request = arbiter.requests[0]
    assert request.projet_id == PROJECT
    assert request.objectif == GOAL
    assert request.run_id == journal.run_id
    assert request.manque.gap.role == "Designer"
    assert request.manque.tasks == (
        "Dessiner le logo stylisé",
        "Écrire le script d'animation",
    )
    assert "ui" in request.manque.reason()
    # The bound travels on the request, and the arbiter is the one holding it.
    assert request.attente_s > 0


def test_confrontation_happens_before_the_first_task(tmp_path: Path) -> None:
    """A recruitment decided after routing would have been useless.

    The proof is taken from the journal: the gap line precedes every task line,
    and the arbiter was called before any task was executed.
    """
    seen: list[str] = []

    async def arbiter(request: DemandeRenfort) -> DecisionRenfort:
        seen.append("demande")
        return DecisionRenfort(approuve=False, detail="non")

    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    steps = [r.etape for r in journal.records]
    first_task = next(i for i, e in enumerate(steps) if e.startswith("logo-stylise"))
    assert steps.index(STEP_TEAM) < first_task
    assert seen == ["demande"]


def test_gap_is_recorded_even_with_nobody_to_propose_it_to(
    tmp_path: Path,
) -> None:
    """« Aucune modification de l'équipe n'a été suggérée » was true all the way into
    the journal: without an arbiter, the gap is **said** anyway, and only once.

    Zero usage: confronting tags with definitions solicits no model.
    """
    engine, _ = _engine(tmp_path, team=_definition("dev", "backend", "frontend"))
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    lines = _team_lines(journal)
    assert len(lines) == 1
    assert lines[0].statut == STATUT_ROLE_MANQUANT
    assert lines[0].agent == "orchestrateur"
    assert lines[0].projet_id == PROJECT
    assert "Dessiner le logo stylisé" in lines[0].sortie
    assert lines[0].usage.calls == 0


def test_accepting_recruits_and_the_tasks_go_to_the_new_role(tmp_path: Path) -> None:
    """The second criterion: the completed team takes the tasks that called for it.

    Nothing is rewritten in the plan — routing rereads the agent repository at
    each task, so that a role born during the wait takes its work without a line
    of the plan changing.
    """
    arbiter = _SpyArbiter(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        definition=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    by_task = {r.task_id: r.agent for r in report.resultats}
    assert by_task["logo-stylise"] == "interface"
    assert by_task["script-animation"] == "interface"
    # The task the team already covered has not changed hands.
    assert by_task["test-lancement"] == "dev"
    outcome = _team_lines(journal)[-1]
    assert outcome.statut == STATUT_RENFORT_RECRUTE
    assert "l'équipe complétée" in outcome.sortie


def test_declining_lets_the_run_continue_and_says_so(tmp_path: Path) -> None:
    """Declining cancels nothing: the run finishes, with the team we have."""
    arbiter = _SpyArbiter(
        DecisionRenfort(approuve=False, detail="Entendu : je ne recrute personne")
    )
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert len(report.resultats) == 3
    outcome = _team_lines(journal)[-1]
    assert outcome.statut == STATUT_RENFORT_DECLINE
    assert "l'équipe actuelle" in outcome.sortie
    # The tasks went to the only role in place, without its trade.
    assert {r.agent for r in report.resultats} == {"dev"}


def test_not_answering_is_not_a_refusal(tmp_path: Path) -> None:
    """Three words and not a single « refused »: what one rereads of a run is *who
    decided*, and nobody watched this one go by."""
    arbiter = _SpyArbiter(
        DecisionRenfort(approuve=False, detail="personne n'a répondu", sans_reponse=True)
    )
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert len(report.resultats) == 3
    assert _team_lines(journal)[-1].statut == STATUT_RENFORT_SANS_REPONSE


def test_a_failing_arbiter_does_not_doom_the_plan(tmp_path: Path) -> None:
    """Fail-safe in the useful direction: what is at stake is an **improvement** of
    the team, not a sensitive act. A closed bus does not cost a run."""

    async def arbiter(request: DemandeRenfort) -> DecisionRenfort:
        raise RuntimeError("bus refermé")

    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert len(report.resultats) == 3
    outcome = _team_lines(journal)[-1]
    assert outcome.statut == STATUT_RENFORT_SANS_REPONSE
    assert "bus refermé" in outcome.sortie


# --- ② ter Without reinforcement, the work goes to the closest role (#1260) --
#
# The 2026-09-24 close-out replayed the trial on the real stack: the design task
# left « to assign » — the classifier, asked *who is competent*, honestly
# abstained —, the next two stayed blocked, and the run ended in failure 0/3.
# The tests of #1227 did not see it: their classifier always designated someone.
# These replay the real classifier, which abstains.


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param(DecisionRenfort(approuve=False, detail="non merci"), id="decliner"),
        pytest.param(
            DecisionRenfort(approuve=False, detail="personne", sans_reponse=True),
            id="sans-reponse",
        ),
    ],
)
def test_without_reinforcement_tasks_go_to_the_closest_role_instead_of_failing(
    tmp_path: Path, decision: DecisionRenfort
) -> None:
    """The criterion of #1260: the « decline / no answer » path makes no task
    fail. The trial 2 run ended 0/3; it must end 3/3, all to the only role in
    place — and the journal says so."""
    engine, _ = _engine(
        tmp_path,
        team=_definition("dev", "backend", "frontend"),
        arbiter=_SpyArbiter(decision),
        provider=_HesitantScriptedPlan(),
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert [r.erreur for r in report.resultats if not r.ok] == []
    assert {r.task_id: r.agent for r in report.resultats} == {
        "logo-stylise": "dev",
        "script-animation": "dev",
        "test-lancement": "dev",
    }
    assert "rôle le plus proche" in _team_lines(journal)[-1].sortie


def test_with_nobody_to_propose_to_the_work_also_goes_to_the_closest(
    tmp_path: Path,
) -> None:
    """Without an arbiter (a command-line run), the gap is said and the run
    continues with the current team: it is the same decision, taken by default."""
    engine, _ = _engine(
        tmp_path,
        team=_definition("dev", "backend", "frontend"),
        provider=_HesitantScriptedPlan(),
    )

    report = asyncio.run(engine.run(GOAL, journal=RunJournal(), projet_id=PROJECT))

    assert all(r.ok for r in report.resultats)
    assert {r.agent for r in report.resultats} == {"dev"}


def test_closest_is_designated_by_the_model_not_by_the_order(
    tmp_path: Path,
) -> None:
    """Two roles in place, neither has the trade: the classifier says which one
    comes closest, to a question that does not admit abstention. The writer is
    **second** in the repository: taking it is not an order default."""
    provider = _HesitantScriptedPlan(preferred="redaction")
    engine, _ = _engine(
        tmp_path,
        team=_definition("dev", "backend", "frontend"),
        reinforcements=(
            AgentDefinition(
                nom="redaction",
                role="Rédacteur",
                competences=("documentation",),
                playbook="Tu écris la documentation de p1.",
            ),
        ),
        arbiter=_SpyArbiter(DecisionRenfort(approuve=False, detail="non")),
        provider=provider,
    )

    report = asyncio.run(engine.run(GOAL, journal=RunJournal(), projet_id=PROJECT))

    by_task = {r.task_id: r.agent for r in report.resultats}
    assert by_task["logo-stylise"] == "redaction"
    assert by_task["script-animation"] == "redaction"
    # The task the team covers does not go through this question.
    assert by_task["test-lancement"] == "dev"
    assert provider.closest_questions == 2


class _MixedPlan(_ScriptedPlan):
    """The close-out's plan, run `2f7aae8437f4`: **one** task, requiring the absent
    trade (`ui`) and a trade the developer has (`frontend`).

    The recruited Designer covers one, the developer the other: a tie, which the
    classifier settled for the developer — the first candidate, like this double.
    The chat had just promised « les tâches qui demandaient ces compétences iront
    au nouveau rôle ».
    """

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(
                [
                    {
                        "id": "logo-anime",
                        "titre": "Créer logo-anime.svg",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["ui", "frontend"],
                        "format_sortie": "SVG",
                        "dependances": [],
                    }
                ]
            )
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


def test_accepting_gives_the_new_role_the_tasks_that_required_its_trade(
    tmp_path: Path,
) -> None:
    """The chat's promise, kept even when the developer covers part of the task:
    the role recruited **for this plan** takes the tasks that required its trade.
    Seen one time out of two on the real stack (S6, 2026-09-24)."""
    arbiter = _SpyArbiter(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        definition=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    engine, _ = _engine(
        tmp_path,
        team=_definition("dev", "backend", "frontend"),
        arbiter=arbiter,
        provider=_MixedPlan(),
    )

    report = asyncio.run(engine.run(GOAL, journal=RunJournal(), projet_id=PROJECT))

    assert {r.task_id: r.agent for r in report.resultats} == {"logo-anime": "interface"}


class _TwoTradesPlan(_HesitantScriptedPlan):
    """The plan of run `2e7f7278a991` (S6, 2026-09-24): **two** absent trades.

    The visual direction requires design, the validation requires QA. #1227
    proposes only one position per run — the one covering the most, here the
    Designer —, and the person is never asked for QA. The classifier is the real
    stack's one: it abstains on the ordinary question.
    """

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(
                [
                    {
                        "id": "direction-visuelle",
                        "titre": "Définir la direction visuelle",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["ui", "design-system"],
                        "format_sortie": "note",
                        "dependances": [],
                    },
                    {
                        "id": "svg-anime",
                        "titre": "Produire logo-anime.svg",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["frontend"],
                        "format_sortie": "SVG",
                        "dependances": ["direction-visuelle"],
                    },
                    {
                        "id": "validation",
                        "titre": "Valider le SVG dans un navigateur",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["tests", "qa"],
                        "format_sortie": "rapport",
                        "dependances": ["svg-anime"],
                    },
                ]
            )
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


def test_what_the_accepted_reinforcement_does_not_cover_goes_to_the_closest_and_is_said(
    tmp_path: Path,
) -> None:
    """Accepting the Designer must not make the never-proposed QA fail.

    Run `2e7f7278a991` ended 2/3: Designer recruited and at work, but the
    validation left « to assign ». Nobody was asked about that second trade —
    it is the default decision of « nobody to propose to », and it has the same
    consequence: the closest role. The journal says so, next to the recruitment.
    """
    arbiter = _SpyArbiter(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        definition=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    engine, _ = _engine(
        tmp_path,
        team=_definition("dev", "backend", "frontend"),
        arbiter=arbiter,
        provider=_TwoTradesPlan(),
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert [r.erreur for r in report.resultats if not r.ok] == []
    by_task = {r.task_id: r.agent for r in report.resultats}
    assert by_task["direction-visuelle"] == "interface"
    assert by_task["svg-anime"] == "dev"
    assert by_task["validation"] in {"dev", "interface"}
    outcome = _team_lines(journal)[-1]
    assert outcome.statut == STATUT_RENFORT_RECRUTE
    assert "qa, tests" in outcome.sortie
    assert "rôle le plus proche" in outcome.sortie


@pytest.mark.parametrize(
    ("team", "project"),
    [
        pytest.param(None, PROJECT, id="projet-sans-agent"),
        pytest.param(_definition("dev", "backend", "frontend"), None, id="hors-projet"),
    ],
)
def test_no_confrontation_without_a_project_team(
    tmp_path: Path, team: AgentDefinition | None, project: str | None
) -> None:
    """Two abstentions, and they are not confused with a gap.

    A project **without an agent** is offered its whole team before opening a run
    (#1146); a run **outside a project** is routed on the wiring catalogue, whose
    team is not recruited. Neither has a reinforcement to propose, and neither
    records a gap.
    """
    arbiter = _SpyArbiter(DecisionRenfort(approuve=False))
    engine, _ = _engine(tmp_path, team=team, arbiter=arbiter)
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=project))

    assert arbiter.requests == []
    assert _team_lines(journal) == []


def test_a_complete_team_raises_no_question(tmp_path: Path) -> None:
    """The control: when the plan fits in the team, nothing is proposed or written."""
    arbiter = _SpyArbiter(DecisionRenfort(approuve=False))
    engine, _ = _engine(
        tmp_path,
        team=_definition("polyvalent", "backend", "frontend", "ui", "design-system"),
        arbiter=arbiter,
    )
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert arbiter.requests == []
    assert _team_lines(journal) == []


# --- ② bis The run's lines make no task card ---------------------------------


def test_team_step_lines_are_run_activities(tmp_path: Path) -> None:
    """A **run** step, like planning and the brief: it concerns no task. Without
    this declaration, the bridge's default rule would make a ghost Kanban card
    named « equipe » (#924)."""
    arbiter = _SpyArbiter(DecisionRenfort(approuve=False, detail="non"))
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    for line in _team_lines(journal):
        events = evenements_depuis_step(line.to_dict())
        assert [e.type for e in events] == [EVENEMENT_AGENT_ACTIVITE]
        assert events[0].tache_id == ""
        assert events[0].etape_run == STEP_TEAM


def test_waiting_for_reinforcement_creates_no_ledger_entry(
    tmp_path: Path,
) -> None:
    """Zero usage, and above all **no ghost task**: the ledger of a run suspended
    on a recruitment counts exactly its tasks."""
    arbiter = _SpyArbiter(DecisionRenfort(approuve=False, detail="non"))
    engine, _ = _engine(
        tmp_path, team=_definition("dev", "backend", "frontend"), arbiter=arbiter
    )
    journal = RunJournal()

    report = asyncio.run(engine.run(GOAL, journal=journal, projet_id=PROJECT))

    assert {t.task_id for t in report.grand_livre.tasks} == {
        "logo-stylise",
        "script-animation",
        "test-lancement",
    }


# --- ③ The chat: the request is relayed there, the decision returns to the run -
#
# Since #1260, two halves on either side of the bus: the **arbiter** lives with
# the engine (in one host or the other) and knows only the bus; the **relay**
# lives in the API, the chat's only writer. What is kept here is what sets them
# apart from their three elders: the request ends up **in the chat** — where
# #1146 already put the team card and the action that validates it —, and the
# bound is held on both sides, on the same date.


def _reinforcement_request(run_id: str = "run-1", *, wait_s: float = 30.0) -> DemandeRenfort:
    """The engine's request: the trial's gap, on the trial's project."""
    gap = plan_gap(
        [
            _task("Dessiner le logo stylisé", "ui", "design-system"),
            _task("Vérifier le lancement", "frontend"),
        ],
        [_agent("dev", "backend", "frontend")],
    )
    assert gap is not None
    return DemandeRenfort(
        run_id=run_id,
        projet_id=PROJECT,
        objectif=GOAL,
        manque=gap,
        attente_s=wait_s,
    )


class _DraftingResponder:
    """The chat's voice, reduced to what the relay expects of it (#1262).

    The relay no longer composes a sentence: it passes on **facts**, and the
    chat's responder is the one who drafts (`RepondeurChat.rediger`). This double
    notes the facts received and returns a sentence no template contains — so that
    `contenu == "Message rédigé n°1."` proves that no text from the code slipped
    in.
    """

    def __init__(self) -> None:
        self.facts: list[str] = []

    async def rediger(self, agent: Any, fil: Any, *, faits: str) -> str:
        self.facts.append(faits)
        return f"Message rédigé n°{len(self.facts)}."


def _orchestration_chat(tmp_path: Path, bus: Any, responder: Any = None) -> Any:
    """A `ServiceChat` of the global chat, on a throwaway repository and the given bus."""
    from maestro.controltower.chat import ChatStore, RepondeurScripte, ServiceChat
    from maestro.messaging import InMemoryMailbox

    return ServiceChat(
        store=ChatStore(tmp_path / "chat"),
        repondeur=responder if responder is not None else RepondeurScripte(),
        mailbox=InMemoryMailbox(),
        bus=bus,
    )


def test_arbiter_publishes_the_request_with_what_the_chat_will_show(tmp_path: Path) -> None:
    """The request crosses the bus **already composed**: the relay has nothing to judge.

    What is needed to decide travels on the request the card will read — the
    role, why, the tasks, and what was asked — and the bound as a date, the same
    one the arbiter holds on its side. `detail` no longer carries a chat sentence
    (#1262): it is the gap in one line, for the journal.
    """
    from maestro.controltower.events import EVENEMENT_RENFORT_DEMANDE, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower

    bus = InMemoryEventBus()
    arbiter = ArbitreRenfortControlTower(bus)

    async def scenario() -> tuple[list[Any], DecisionRenfort]:
        seen: list[Any] = []
        stream = bus.subscribe()

        async def listen() -> None:
            async for event in stream:
                seen.append(event)

        listening = asyncio.create_task(listen())
        await asyncio.sleep(0)
        decision = await arbiter(_reinforcement_request(wait_s=0.05))
        listening.cancel()
        return seen, decision

    seen, decision = asyncio.run(scenario())

    requests = [e for e in seen if e.type == EVENEMENT_RENFORT_DEMANDE]
    assert len(requests) == 1
    published = requests[0]
    assert published.run_id == "run-1"
    assert published.projet_id == PROJECT
    assert published.titre == "Designer"
    assert published.recrutement is not None
    assert published.recrutement["run_id"] == "run-1"
    assert published.recrutement["gabarit"] == "interface"
    assert published.recrutement["taches"] == ["Dessiner le logo stylisé"]
    assert published.recrutement["objectif"] == GOAL
    assert published.detail == _reinforcement_request().manque.sentence()
    assert published.echeance
    # Nobody answered: the run resumes, and it is not a refusal.
    assert decision.sans_reponse and not decision.approuve


def test_relay_posts_the_request_in_the_chat_then_says_the_deadline(tmp_path: Path) -> None:
    """The message carries the request — the card of #1146 sees it — and the chat's voice.

    Nobody answers: at the deadline, the chat **says so**, and the request is not
    posted there again — a « Créer l'équipe » button would promise to resume a run
    that has already left.

    Since #1262, both messages are **drafted by the chat's responder** from the
    facts the relay passes it: what the chat says is what it drafted, to the
    letter, and it is the one that received the role, the reason, the tasks, the
    deadline.
    """
    from maestro.controltower.chat import FAIT_DE_LA_DEMANDE, recrutement_en_attente
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande

    bus = InMemoryEventBus()
    responder = _DraftingResponder()
    chat = _orchestration_chat(tmp_path, bus, responder)
    relay = RelaisRenfort(bus, chat, AGENT_ORCHESTRATION)

    asyncio.run(relay.relayer(evenement_demande(_reinforcement_request(wait_s=0.05))))

    messages = chat.fil(NOM_ORCHESTRATION)
    posted = messages[0]
    assert posted.recrutement is not None
    assert posted.recrutement.run_id == "run-1"
    assert posted.recrutement.projet_id == PROJECT
    assert posted.recrutement.role == "Designer"
    assert posted.recrutement.gabarit == "interface"
    assert posted.recrutement.taches == ("Dessiner le logo stylisé",)
    assert posted.contenu == "Message rédigé n°1."
    proposed, expired = responder.facts
    assert GOAL in proposed
    assert "Designer" in proposed
    assert "Dessiner le logo stylisé" in proposed
    assert len(messages) == 2
    assert messages[1].contenu == "Message rédigé n°2."
    assert "Designer" in expired
    assert "avant l'échéance" in expired
    assert messages[1].recrutement is None
    assert recrutement_en_attente(messages) is None
    # The request waits under the first message, with its actions: the responder knows
    # it, and does not repeat them (#1339). Under the second, nothing waits any more.
    assert FAIT_DE_LA_DEMANDE in proposed
    assert FAIT_DE_LA_DEMANDE not in expired


def test_relay_posts_the_request_in_the_conversation_that_started_the_run(
    tmp_path: Path,
) -> None:
    """« In the chat that started the run » — and not in the most recent
    conversation. The person who opened another one in the meantime must find the
    question where they asked for the work (#268, `conversation_du_run`)."""
    from maestro.controltower.chat import ChatStore, MessageChat
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande

    bus = InMemoryEventBus()
    chat = _orchestration_chat(tmp_path, bus)
    store = ChatStore(tmp_path / "chat")
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="origine",
            auteur=NOM_ORCHESTRATION,
            contenu="C'est parti.",
            run_id="run-1",
        )
    )
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="plus-recente",
            auteur="utilisateur",
            contenu="Autre chose.",
        )
    )
    relay = RelaisRenfort(bus, chat, AGENT_ORCHESTRATION)

    asyncio.run(relay.relayer(evenement_demande(_reinforcement_request(wait_s=0.02))))

    origin = chat.fil(NOM_ORCHESTRATION, "origine")
    assert [m.recrutement is not None for m in origin] == [False, True, False]
    assert len(chat.fil(NOM_ORCHESTRATION, "plus-recente")) == 1


def test_a_decision_before_the_deadline_makes_the_chat_say_nothing_more(
    tmp_path: Path,
) -> None:
    """The action has already written its trace (`POST …/recrutement`): the relay stays silent."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande
    from maestro.controltower.state import RENFORT_DECLINE

    bus = InMemoryEventBus()
    chat = _orchestration_chat(tmp_path, bus)
    relay = RelaisRenfort(bus, chat, AGENT_ORCHESTRATION)

    async def scenario() -> None:
        relaying = asyncio.create_task(
            relay.relayer(evenement_demande(_reinforcement_request(wait_s=5.0)))
        )
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(type=EVENEMENT_RENFORT_DECISION, run_id="run-1", statut=RENFORT_DECLINE)
        )
        await asyncio.wait_for(relaying, timeout=2.0)

    asyncio.run(scenario())

    assert len(chat.fil(NOM_ORCHESTRATION)) == 1


def test_decision_published_on_the_bus_restarts_the_run(tmp_path: Path) -> None:
    """What comes back over the bus is the decision, and the key is the `run_id` — a
    run has only one request in flight."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower
    from maestro.controltower.state import RENFORT_ACCORDE

    bus = InMemoryEventBus()
    arbiter = ArbitreRenfortControlTower(bus)

    async def scenario() -> DecisionRenfort:
        waiting = asyncio.create_task(arbiter(_reinforcement_request()))
        # Let the request leave, then decide — like a human action.
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(
                type=EVENEMENT_RENFORT_DECISION,
                run_id="run-1",
                statut=RENFORT_ACCORDE,
                detail="Équipe créée : Designer.",
            )
        )
        return await waiting

    decision = asyncio.run(scenario())

    assert decision.approuve
    assert decision.detail == "Équipe créée : Designer."


def test_a_decision_for_another_run_is_not_read(tmp_path: Path) -> None:
    """The filter is the run: two suspended runs do not steal each other's decision."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower
    from maestro.controltower.state import RENFORT_ACCORDE

    bus = InMemoryEventBus()
    arbiter = ArbitreRenfortControlTower(bus)

    async def scenario() -> DecisionRenfort:
        waiting = asyncio.create_task(arbiter(_reinforcement_request("run-1", wait_s=0.2)))
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(
                type=EVENEMENT_RENFORT_DECISION,
                run_id="run-2",
                statut=RENFORT_ACCORDE,
                detail="pas pour vous",
            )
        )
        return await waiting

    decision = asyncio.run(scenario())

    assert decision.sans_reponse and not decision.approuve


def test_request_travels_intact_on_a_serialising_bus() -> None:
    """The real bus is Redis: the request goes through it as JSON, and must come back
    as is — otherwise the chat card would read an empty request."""
    from maestro.controltower.chat import DemandeRecrutement
    from maestro.controltower.events import Event
    from maestro.controltower.renfort import evenement_demande

    published = evenement_demande(_reinforcement_request())
    reread = Event.from_json(published.to_json())

    assert reread.recrutement == published.recrutement
    assert DemandeRecrutement.from_dict(reread.recrutement or {}).pendant_un_run
    assert reread.echeance == published.echeance


# --- ④ The API: the chat's action, and the proposed role ---------------------
#
# The two routes this lot touches, on the real app. Nothing new on the contract
# side: `POST …/recrutement` is the one of #1146 — what is added there is the
# **publication of the decision** when a run is waiting; `POST …/equipe/proposition`
# is the one of #1039 — what is added there is `renfort`.


@pytest.fixture()
def workshop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder where projects are born, under a fake user folder.

    Same reason as in #221: under Windows pytest's `tmp_path` lives in
    `AppData/Local/Temp`, which `validate_root` rightly refuses.
    """
    home_dir = tmp_path / "maison"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))
    workshop = tmp_path / "atelier"
    workshop.mkdir()
    return workshop


class _OfflineGenerator:
    """A playbook generator that never answers — zero model calls.

    The route catches any failure and has the role fall back on its role
    template's playbook: it is therefore also the nominal path of a machine
    without quota, and it is what keeps these tests away from the machine's
    provider (#782).
    """

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


class _OfflineComposer:
    """A team composer that never answers (#1159) — zero model calls.

    The ordinary proposal then falls back on the role templates' rules, stated as
    such: it is the team these tests confront with the reinforcement, and the
    machine's provider stays out of reach (#782).
    """

    async def ecrire(self, prompt: str) -> str:
        raise RuntimeError("hors ligne (test)")


def _project_client(tmp_path: Path, workshop: Path) -> Any:
    """The real app, projects bounded to the workshop, offline generator."""
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.projets import ServiceProjets
    from maestro.projects import ProjectStore

    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            projets=ServiceProjets(
                ProjectStore(tmp_path / "depot"), racines_exploration=(workshop,)
            ),
            agents_store=AgentStore(tmp_path / "agents"),
            generateur_agent=_OfflineGenerator(),
            compositeur_equipe=_OfflineComposer(),
        )
    )


def _python_project(workshop: Path) -> Path:
    """The trial's project: Python, and nothing that designates a designer."""
    root = workshop / "p1"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "p1"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root


def test_route_proposes_the_single_reinforcement_role_and_justifies_it_by_the_plan(
    tmp_path: Path, workshop: Path
) -> None:
    """Without `renfort`, this project calls for no designer — and that is indeed so.

    The faulty sample is the ordinary proposal: the designer is **excluded** there,
    with its reason. It is the plan that requires it, and the plan alone.
    """
    root = _python_project(workshop)
    with _project_client(tmp_path, workshop) as client:
        project = client.post(
            "/api/projets",
            json={"nom": "p1", "racine": str(root), "origine": "existant"},
        ).json()["id"]

        ordinary = client.post(f"/api/projets/{project}/equipe/proposition").json()
        reinforcement = client.post(
            f"/api/projets/{project}/equipe/proposition",
            json={
                "renfort": {
                    "gabarit": "interface",
                    "raison": "le plan de ce travail demande ui, ux",
                }
            },
        )

    assert "designer" not in {r["gabarit"] for r in ordinary["roles"]}
    assert "designer" in {e["nom"] for e in ordinary["ecartes"]} or "interface" in {
        e["nom"] for e in ordinary["ecartes"]
    }
    assert reinforcement.status_code == 200, reinforcement.text
    body = reinforcement.json()
    assert [r["gabarit"] for r in body["roles"]] == ["designer"]
    assert body["roles"][0]["raison"] == "le plan de ce travail demande ui, ux"
    assert body["cree"] is False
    assert [e["nom"] for e in body["ecartes"]] == ["orchestrateur"]


def test_an_unknown_reinforcement_role_template_is_refused(
    tmp_path: Path, workshop: Path
) -> None:
    """A reasoned 422, never an invented role: what would be born would have neither
    playbook nor competencies, and would read as a catalogue role."""
    root = _python_project(workshop)
    with _project_client(tmp_path, workshop) as client:
        project = client.post(
            "/api/projets",
            json={"nom": "p1", "racine": str(root), "origine": "existant"},
        ).json()["id"]

        response = client.post(
            f"/api/projets/{project}/equipe/proposition",
            json={"renfort": {"gabarit": "comptable", "raison": "au hasard"}},
        )

    assert response.status_code == 422, response.text
    assert "comptable" in response.text


class _SpyBus:
    """The app's in-memory bus, which **notes** what is published on it.

    A wrapper and not a subscriber: a subscription would require an asyncio loop
    alongside the `TestClient`, which is synchronous. What is checked is that an
    event was published — not that it crosses a transport, for which the bus has
    its own tests.
    """

    def __init__(self) -> None:
        from maestro.controltower.events import InMemoryEventBus

        self._real = InMemoryEventBus()
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)
        await self._real.publish(event)

    def subscribe(self) -> Any:
        return self._real.subscribe()

    async def close(self) -> None:
        await self._real.close()

    def decisions(self) -> list[Any]:
        """Only the published `renfort.decision` events, in order."""
        from maestro.controltower.events import EVENEMENT_RENFORT_DECISION

        return [e for e in self.published if e.type == EVENEMENT_RENFORT_DECISION]


class _DraftingModel(ModelProvider):
    """The chat's provider for these routes: it only serves to draft the reply to the action.

    Since #1262, the recruitment action makes the model speak; without an injected
    provider, the responder would resolve the machine's one (#782). What is kept
    here is the decision published to the run, never the words.
    """

    name = "modele-qui-redige"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        return "Rédigé par le modèle."


def _post_request(store: Any, *, run_id: str) -> None:
    """Writes into the chat the request a suspended run would have posted there.

    Direct write into the repository: what is tested here is the **route**, not
    the arbiter (it has its own section). Mounting a run to arrange the state of a
    chat would cost an engine to check a `publish`.
    """
    from maestro.controltower.chat import DemandeRecrutement, MessageChat
    from maestro.controltower.orchestration import NOM_ORCHESTRATION

    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Le plan appelle un Designer. Je le recrute ?",
            recrutement=DemandeRecrutement(
                objectif=GOAL,
                projet_id=PROJECT,
                run_id=run_id,
                role="Designer",
                gabarit="interface",
                raison="le plan de ce travail demande ui",
                taches=("Dessiner le logo stylisé",),
            ),
        )
    )


@pytest.mark.parametrize(
    ("approved", "expected_status", "expected_detail"),
    [
        pytest.param(True, "recrute", "recruté : Designer — 1 agent", id="accepter"),
        pytest.param(False, "decline", "", id="decliner"),
    ],
)
def test_chat_action_publishes_the_decision_to_the_waiting_run(
    tmp_path: Path, approved: bool, expected_status: str, expected_detail: str
) -> None:
    """The second criterion, API side: the run learns what was decided about it.

    The order matters and it is that of the whole module: the chat first (the two
    messages of the action), the broadcast next — a run that would restart before
    its trace is written would leave the chat behind what is happening.

    The `detail` is the **fact** — what was recruited —, and not the words of the
    reply (#1262): it ends up in the run's journal, where one rereads what was
    recruited, not what the orchestrator said about it.
    """
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.chat import ChatStore
    from maestro.controltower.orchestration import NOM_ORCHESTRATION, RepondeurOrchestration

    async def recruit(project_id: str, roles: Any, proposal_id: str = "") -> dict[str, Any]:
        return {
            "projet_id": project_id,
            "agents": [{"nom": r.name, "role": r.role, "instances": r.instances} for r in roles],
            "instances_total": sum(r.instances for r in roles),
        }

    bus = _SpyBus()
    store = ChatStore(tmp_path / "chat")
    _post_request(store, run_id="run-42")

    app = create_app(
        bus=bus,
        chat_store=store,
        orchestration_repondeur=RepondeurOrchestration(
            recruteur=recruit, provider=_DraftingModel()
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/recrutement",
            json={
                "approuve": approved,
                "roles": (
                    [
                        {
                            "nom": "interface",
                            "role": "Designer",
                            "competences": ["ui"],
                            "playbook": "Tu dessines.",
                            "instances": 1,
                        }
                    ]
                    if approved
                    else []
                ),
            },
        )

    assert response.status_code == 201, response.text
    decisions = bus.decisions()
    assert len(decisions) == 1
    assert decisions[0].run_id == "run-42"
    assert decisions[0].statut == expected_status
    assert decisions[0].projet_id == PROJECT
    assert decisions[0].detail == expected_detail
    assert response.json()["messages"][1]["contenu"] == "Rédigé par le modèle."


def test_a_recruitment_without_a_run_publishes_no_decision(tmp_path: Path) -> None:
    """The control: the request of #1146 suspends no run, so it notifies none.

    Publishing there would make a decision concerning nobody appear on the channel
    a run listens to — and on a durable bus, it would be replayed.
    """
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.chat import ChatStore, DemandeRecrutement, MessageChat
    from maestro.controltower.orchestration import NOM_ORCHESTRATION, RepondeurOrchestration

    bus = _SpyBus()
    store = ChatStore(tmp_path / "chat")
    store.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Ce projet n'a aucun agent. Je vous propose son équipe.",
            recrutement=DemandeRecrutement(objectif=GOAL, projet_id=PROJECT),
        )
    )

    app = create_app(
        bus=bus,
        chat_store=store,
        orchestration_repondeur=RepondeurOrchestration(provider=_DraftingModel()),
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json={"approuve": False}
        )

    assert response.status_code == 201, response.text
    assert bus.decisions() == []
