"""Tests of the automatic assignment router (tickets #6 and #42).

Covers both routing signals and their combination:

- pure competency rules (`assign`, #6): best overlap, routing by domain,
  deterministic tiebreak between tied agents, error cases;
- lightweight classifier (`TaskClassifier`, #42): prompt, tolerant parsing, abstention;
- combined routing (`Router.route`, #42): clear case without a model call, tiebreak
  of ambiguous cases by the classifier, **explicit "à assigner" fallback** (low
  confidence, unreadable answer, classifier missing or failing);
- evaluation on the **versioned dataset** (MVP criterion 3): ≥ 10 cases, precision ≥ 9/10.
"""

import asyncio

import pytest

from maestro.agents import GABARITS_DU_CODE
from maestro.agents.catalog import Agent
from maestro.orchestrator import Task
from maestro.providers.base import ModelProvider
from maestro.router import (
    CLASSIFIER_CLOSEST_SYSTEM_PROMPT,
    CLASSIFIER_MODEL,
    METHOD_CLASSIFIER,
    METHOD_CLOSEST,
    METHOD_COMPETENCIES,
    METHOD_FALLBACK,
    Router,
    RoutingError,
    TaskClassifier,
    assign,
    evaluate,
    load_dataset,
)
from maestro.router.classifier import _parse_classification


def _task(**overrides) -> Task:
    data = {
        "id": "t",
        "titre": "Une tâche",
        "description": "Description.",
        "competences_requises": ("sql", "migration"),
        "format_sortie": "Un livrable",
        "dependances": (),
    }
    data.update(overrides)
    return Task(**data)


class ScriptedProvider(ModelProvider):
    """Fake provider: replays scripted answers and records the calls."""

    name = "scripted"

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return self._responses.pop(0) if self._responses else '{"agent": null, "confiance": 0.0}'


class BrokenProvider(ModelProvider):
    """Broken fake provider: every model call raises."""

    name = "broken"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


def _router(provider: ModelProvider | None = None, **kwargs) -> Router:
    classifier = TaskClassifier(provider) if provider is not None else None
    return Router(GABARITS_DU_CODE, classifier=classifier, **kwargs)


# --- Pure competency rules (#6) ---------------------------------------------------------


def test_assign_picks_the_best_overlap():
    result = assign(_task(competences_requises=("sql", "migration")), GABARITS_DU_CODE)
    assert result.agent.nom == "bdd"
    assert result.score == 2


def test_assign_routes_each_domain_to_its_agent():
    assert assign(_task(competences_requises=("ui",)), GABARITS_DU_CODE).agent.nom == "designer"
    assert assign(_task(competences_requises=("tests",)), GABARITS_DU_CODE).agent.nom == "qa"
    assert assign(_task(competences_requises=("deploy",)), GABARITS_DU_CODE).agent.nom == "devops"
    assert assign(_task(competences_requises=("api",)), GABARITS_DU_CODE).agent.nom == "developpeur"


def test_assign_without_a_competent_agent_raises_routing_error():
    # 'planning' is a competency of the orchestrator, which no executing agent has.
    with pytest.raises(RoutingError):
        assign(_task(competences_requises=("planning",)), GABARITS_DU_CODE)


def test_assign_without_agents_raises_value_error():
    with pytest.raises(ValueError):
        assign(_task(), [])


def test_tie_broken_by_catalog_order():
    a = Agent(nom="a", role="A", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    b = Agent(nom="b", role="B", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    task = _task(competences_requises=("x",))
    assert assign(task, [a, b]).agent.nom == "a"
    assert assign(task, [b, a]).agent.nom == "b"


# --- Lightweight classifier: parsing the answer (#42) -----------------------------------


def test_parse_accepts_plain_json_and_fence():
    names = frozenset({"devops", "bdd"})
    direct = _parse_classification('{"agent": "devops", "confiance": 0.9}', names)
    assert (direct.agent, direct.confidence) == ("devops", 0.9)
    fence = _parse_classification('```json\n{"agent": "bdd", "confiance": 0.7}\n```', names)
    assert (fence.agent, fence.confidence) == ("bdd", 0.7)
    prose = _parse_classification('Je choisis : {"agent": "devops", "confiance": 1}.', names)
    assert prose.agent == "devops"


def test_parse_unreadable_answer_is_an_abstention():
    verdict = _parse_classification("je ne sais pas trop…", frozenset({"devops"}))
    assert verdict.agent is None
    assert verdict.confidence == 0.0


def test_parse_agent_outside_candidates_is_an_abstention():
    verdict = _parse_classification(
        '{"agent": "chef-de-projet", "confiance": 0.95}', frozenset({"devops", "bdd"})
    )
    assert verdict.agent is None
    assert verdict.confidence == 0.0


def test_parse_clamps_the_confidence():
    verdict = _parse_classification('{"agent": "devops", "confiance": 42}', frozenset({"devops"}))
    assert verdict.confidence == 1.0


# --- Combined routing (#42) -------------------------------------------------------------


def test_route_clear_case_assigns_without_calling_the_classifier():
    provider = ScriptedProvider()
    decision = asyncio.run(
        _router(provider).route(_task(competences_requises=("sql", "migration")))
    )
    assert decision.agent is not None and decision.agent.nom == "bdd"
    assert decision.method == METHOD_COMPETENCIES
    assert decision.confidence == 1.0
    assert provider.calls == []  # clear rule: no model cost


def test_route_tie_broken_by_the_classifier_among_the_tied_candidates_only():
    provider = ScriptedProvider('{"agent": "devops", "confiance": 0.9}')
    task = _task(
        titre="Déployer le schéma en staging",
        description="Déployer le schéma de base sur l'environnement de staging.",
        competences_requises=("deploy", "schema"),  # devops / bdd tie
    )
    decision = asyncio.run(_router(provider).route(task))

    assert decision.agent is not None and decision.agent.nom == "devops"
    assert decision.method == METHOD_CLASSIFIER
    assert decision.confidence == 0.9
    # The classifier is asked on the lightweight model, about the tied candidates only.
    (call,) = provider.calls
    assert call["model"] == CLASSIFIER_MODEL
    assert "devops" in call["prompt"] and "bdd" in call["prompt"]
    assert "designer" not in call["prompt"]


def test_route_without_overlap_asks_about_the_whole_catalog():
    provider = ScriptedProvider('{"agent": "designer", "confiance": 0.8}')
    task = _task(
        titre="Audit d'accessibilité",
        description="Vérifier contrastes et navigation clavier des écrans.",
        competences_requises=("accessibilite",),  # no agent declares it
    )
    decision = asyncio.run(_router(provider).route(task))

    assert decision.agent is not None and decision.agent.nom == "designer"
    (call,) = provider.calls
    for agent in GABARITS_DU_CODE:
        assert agent.nom in call["prompt"]


def test_route_low_confidence_marks_needs_assignment():
    provider = ScriptedProvider('{"agent": "devops", "confiance": 0.3}')
    task = _task(competences_requises=("deploy", "schema"))
    decision = asyncio.run(_router(provider).route(task))

    assert decision.needs_assignment
    assert decision.agent is None
    assert decision.method == METHOD_FALLBACK
    assert "à assigner" in decision.reason


def test_route_unreadable_answer_marks_needs_assignment():
    provider = ScriptedProvider("aucune idée")
    decision = asyncio.run(_router(provider).route(_task(competences_requises=("inconnu",))))
    assert decision.needs_assignment
    assert "à assigner" in decision.reason


def test_route_without_classifier_does_not_route_at_random():
    # Ambiguous and no classifier: explicit fallback, no arbitrary assignment.
    decision = asyncio.run(_router().route(_task(competences_requises=("deploy", "schema"))))
    assert decision.needs_assignment
    assert decision.method == METHOD_FALLBACK


def test_route_failing_classifier_marks_needs_assignment_without_raising():
    decision = asyncio.run(
        _router(BrokenProvider()).route(_task(competences_requises=("inconnu",)))
    )
    assert decision.needs_assignment
    assert "indisponible" in decision.reason


def test_router_without_agents_raises_value_error():
    with pytest.raises(ValueError):
        Router([])


# --- MVP criterion 3: ≥ 9/10 on the versioned assignment dataset (#42) ------------------


class StubClassifierModel(ModelProvider):
    """Simulates the lightweight model: decides from the prompt text alone, as Haiku does.

    Deliberately simple and transparent rules: the stub sees only the prompt
    composed by `build_classifier_prompt`, never what the dataset expects.
    """

    name = "stub-classifieur"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        text = prompt.lower()
        if "déployer" in text or "staging" in text:
            return '{"agent": "devops", "confiance": 0.9}'
        if "supervision" in text or "alerte" in text:
            return '{"agent": "devops", "confiance": 0.9}'
        if "accessibilité" in text:
            return '{"agent": "designer", "confiance": 0.85}'
        if "onboarding" in text or "wireframe" in text:
            return '{"agent": "designer", "confiance": 0.85}'
        return '{"agent": null, "confiance": 0.2}'


def test_assignment_dataset_is_versioned_and_varied():
    dataset = load_dataset()  # validates each task against the shared JSON Schema
    assert len(dataset) >= 10  # acceptance criterion: ≥ 10 varied tasks
    # Varied: each of the five agents of the catalog is expected at least once…
    expected = {case.expected_agent for case in dataset}
    assert {a.nom for a in GABARITS_DU_CODE} <= expected
    # …and the dataset holds at least one "à assigner" fallback case.
    assert None in expected


def test_routing_reaches_9_out_of_10_on_the_dataset():
    result = asyncio.run(evaluate(_router(StubClassifierModel())))
    assert result.precision >= 0.9, result.summary()  # MVP criterion 3


def test_out_of_scope_case_ends_up_to_be_assigned_rather_than_misrouted():
    result = asyncio.run(evaluate(_router(StubClassifierModel())))
    fallbacks = [d for d in result.details if d.case.expected_agent is None]
    assert fallbacks, "the dataset must hold a fallback case"
    for detail in fallbacks:
        assert detail.decision.needs_assignment
        assert "à assigner" in detail.decision.reason


# --- An empty team is not an omission (#1042) -------------------------------------------


def test_an_empty_team_leaves_the_task_to_be_assigned():
    """On the routing side of #1042: a project without agents routes nothing.

    That is what makes "a project is born without agents" observable instead of
    silently made up for: `()` is not an omission, so the task does not go to the
    five roles of the code. And the cause is named: a team still to recruit is not
    fixed the way a disabled agent is reactivated.
    """
    decision = asyncio.run(_router().route(_task(), agents=()))

    assert decision.needs_assignment
    assert "à recruter" in decision.reason


def test_no_team_given_falls_back_on_the_wiring_catalog():
    """`None` is the omission: a task outside a project is routed on the wiring's catalog."""
    decision = asyncio.run(_router().route(_task(), agents=None))

    assert decision.agent is not None and decision.agent.nom == "bdd"


# --- Closest: the team stays as it is (#1260) -------------------------------------------
#
# The "à assigner" fallback yields to one decision only: doing the work with the
# current team, when the trade is missing and no one recruited it. These cases
# exercise the router alone; the full path (loop, executor) lives in
# `tests/test_equipe_au_plan.py`.


def _team_without_designer() -> tuple[Agent, ...]:
    """Two roles in place, neither covers `ui`: the trial run's case, widened."""
    return (
        Agent(
            nom="dev",
            role="Développeur",
            competences=frozenset({"backend"}),
            modele="m",
            prompt_systeme="…",
        ),
        Agent(
            nom="redaction",
            role="Rédacteur",
            competences=frozenset({"documentation"}),
            modele="m",
            prompt_systeme="…",
        ),
    )


def test_without_the_decision_a_task_no_one_covers_stays_to_be_assigned():
    """The #42 control: without `closest`, a classifier that abstains leaves the
    task to be assigned, so ordinary routing does not depend on `closest`."""
    provider = ScriptedProvider('{"agent": null, "confiance": 0.1}')
    router = Router(_team_without_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(router.route(_task(competences_requises=("ui",))))

    assert decision.needs_assignment
    assert decision.method == METHOD_FALLBACK
    assert decision.uncovered == ("ui",)


def test_closest_asks_the_question_that_admits_no_abstention():
    """Several candidates: the model designates one on the second question, and
    the catalog order never overrides its answer."""
    provider = ScriptedProvider('{"agent": "redaction", "confiance": 0.3}')
    router = Router(_team_without_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), closest=True)
    )

    assert decision.agent is not None and decision.agent.nom == "redaction"
    assert decision.method == METHOD_CLOSEST
    assert decision.uncovered == ("ui",)
    assert provider.calls[0]["system_prompt"] == CLASSIFIER_CLOSEST_SYSTEM_PROMPT
    assert "classifieur désigne redaction" in decision.reason


def test_closest_with_a_single_candidate_asks_the_model_nothing():
    """The trial run's case: a project with a single developer. There is no tie to break."""
    provider = ScriptedProvider()
    router = Router(_team_without_designer()[:1], classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), closest=True)
    )

    assert decision.agent is not None and decision.agent.nom == "dev"
    assert provider.calls == []
    assert "seul rôle en place" in decision.reason


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(None, id="no-classifier"),
        pytest.param(BrokenProvider(), id="broken-classifier"),
        pytest.param(
            ScriptedProvider('{"agent": "inconnu", "confiance": 0.9}'), id="outside-candidates"
        ),
    ],
)
def test_closest_without_designation_the_order_breaks_the_tie_and_says_so(provider):
    """An outage does not fail a task the decision hands over: the catalog order
    breaks the tie, and the reason says that it is not a judgement."""
    router = Router(
        _team_without_designer(),
        classifier=TaskClassifier(provider) if provider is not None else None,
    )

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), closest=True)
    )

    assert decision.agent is not None and decision.agent.nom == "dev"
    assert "n'a désigné personne" in decision.reason


def test_closest_leaves_a_covered_competency_to_the_competency_rule():
    """A task that someone covers stays with the competency rule."""
    provider = ScriptedProvider()
    router = Router(_team_without_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("documentation",)), closest=True)
    )

    assert decision.agent is not None and decision.agent.nom == "redaction"
    assert decision.method == METHOD_COMPETENCIES
    assert provider.calls == []


def _completed_team() -> tuple[Agent, ...]:
    """The trial run's developer, then the designer recruited for the plan."""
    return (
        Agent(
            nom="dev",
            role="Développeur",
            competences=frozenset({"backend", "frontend", "api"}),
            modele="m",
            prompt_systeme="…",
        ),
        Agent(
            nom="interface",
            role="Designer",
            competences=frozenset({"ui", "ux"}),
            modele="m",
            prompt_systeme="…",
        ),
    )


def test_the_recruited_role_takes_the_task_that_required_its_trade():
    """Run `2f7aae8437f4`: a `ui` + `frontend` + `api` task, of which the developer
    covers two and the recruited designer one. Without `recruited` the developer
    wins; with it, the role recruited for this plan takes it, as the chat promised."""
    task = _task(competences_requises=("ui", "frontend", "api"))
    router = Router(_completed_team())

    without_recruited = asyncio.run(router.route(task))
    with_recruited = asyncio.run(router.route(task, recruited={"ui"}))

    assert without_recruited.agent is not None and without_recruited.agent.nom == "dev"
    assert with_recruited.agent is not None and with_recruited.agent.nom == "interface"
    assert with_recruited.method == METHOD_COMPETENCIES


def test_recruited_competencies_leave_alone_a_task_that_does_not_require_them():
    task = _task(competences_requises=("backend",))

    decision = asyncio.run(Router(_completed_team()).route(task, recruited={"ui"}))

    assert decision.agent is not None and decision.agent.nom == "dev"


def test_a_disabled_recruited_role_hands_back_to_the_ordinary_rule():
    """No one covers the recruited trade any more: the ordinary rule resumes,
    without routing to a disabled agent nor falling back on a task the developer
    can take."""
    task = _task(competences_requises=("ui", "frontend"))

    decision = asyncio.run(
        Router(_completed_team()).route(task, excluded={"interface"}, recruited={"ui"})
    )

    assert decision.agent is not None and decision.agent.nom == "dev"


def test_closest_makes_up_no_one_when_all_are_disabled():
    """No one close is no one: a fully disabled catalog stays to be assigned."""
    router = Router(_team_without_designer())

    decision = asyncio.run(
        router.route(
            _task(competences_requises=("ui",)),
            excluded={"dev", "redaction"},
            closest=True,
        )
    )

    assert decision.needs_assignment
