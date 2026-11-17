"""Work is distributed over the **project's team** (#1041), born from a validation.

Final lot of the #1021 effort (#1043) — lot 5 had shipped without tests, by the
splitting convention ([docs/10 §5.1](../docs/10-workflow-git.md)).

A team created in a project (#1040) is useless as long as the work does not
reach it. What links the two holds in four points, and they are what is guarded
here:

① **one rule, two readers** (`catalogue_du_projet`): the executor, which routes
   task by task, and the loop, which has the goal decomposed. A plan proposed
   on one team and executed on another would send all its tasks to the "to
   assign" fallback without anything saying so;
② **the catalog is replaced for the call**, never sorted: the router's catalog
   is frozen at wiring time, and an agent recruited for a project is born after
   it and elsewhere. The received order breaks ties, it is not reordered;
③ **what nobody covers is a fact observed at routing** — the only place that
   knows at once the requested competencies, the active agents and the team
   received for that very call. `maestro.team.gap` turns it into a **named
   position**, because a signal saying "nobody covers `sql`, `migration`" would
   ask whoever reads it to translate it themselves;
④ **an agent does not recruit during a run** (§3.5 of
   [docs/31](../docs/31-decision-surface-ecriture-agents.md)): the gap is
   signalled in the chat, and the task stays "to assign".

⚠ The fourth was **refined** by #1227, not withdrawn: the *orchestrator* now
proposes completing the team right after decomposition, and a person accepts or
not (`tests/test_equipe_au_plan.py`). What holds here stays true — at
**routing**, it is too late: the task leaves "to assign", and that is what is
guarded. The last two bullets of docs/37 §3, on the other hand, are overturned.

No network and no real model call: fake provider, throwaway stores.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from maestro.agents.catalog import Agent
from maestro.agents.store import AgentDefinition, AgentStore, catalogue_du_projet
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_TACHE_BLOCAGE
from maestro.engine.executor import (
    STATUT_ROLE_MANQUANT,
    SUFFIXE_ETAPE_MANQUE,
    LocalExecutor,
)
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT, orchestrator_prompt
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.router import Router, assign
from maestro.team import MissingRole, missing_role, uncovered_competencies
from maestro.telemetry import RunJournal

PROJECT = "prj-depensio"
OTHER = "prj-autre"


class _Constant(ModelProvider):
    """Fake provider — no real execution is expected here."""

    name = "constant"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "LIVRABLE"


def _agent(name: str, *competencies: str, role: str = "Rôle") -> Agent:
    return Agent(
        nom=name,
        role=role,
        competences=frozenset(competencies),
        modele="m",
        prompt_systeme="…",
    )


def _task(id_: str = "t1", *competencies: str, project_id: str | None = None) -> Task:
    return Task(
        id=id_,
        title=f"Tâche {id_}",
        description=f"Faire {id_}.",
        required_competencies=list(competencies),
        output_format="markdown",
        dependencies=[],
        project_id=project_id,
    )


def _definition(name: str, *competencies: str) -> AgentDefinition:
    return AgentDefinition(
        nom=name,
        role="Rôle du projet",
        competences=competencies,
        playbook="Tu travailles sur ce projet.",
    )


# --- ② The call's catalog: the project's team --------------------------------


def test_a_project_task_is_routed_to_that_project_team() -> None:
    """The router's catalog is frozen at wiring time; a project's agents are born
    after it, and not in the same folder."""
    wiring = [_agent("cablage", "backend")]
    team = [_agent("dev-du-projet", "backend")]

    decision = asyncio.run(
        Router(wiring).route(_task("t1", "backend"), agents=team)
    )

    assert decision.agent is not None
    assert decision.agent.nom == "dev-du-projet"


def test_the_received_team_order_breaks_ties() -> None:
    """The received order is authoritative and must not be sorted here: it is
    what makes the pure rule deterministic — on an equal score, the first of the
    team wins."""
    team = [_agent("premier", "backend"), _agent("second", "backend")]

    assert assign(_task("t1", "backend"), team).agent.nom == "premier"
    assert assign(_task("t1", "backend"), team[::-1]).agent.nom == "second"


def test_a_tie_without_classifier_falls_back_rather_than_at_random() -> None:
    """Combined routing does not break a tie on its own: without a classifier,
    the task is marked "to assign" — never routed at random."""
    team = [_agent("premier", "backend"), _agent("second", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=team
    ))

    assert decision.needs_assignment
    assert "premier, second" in decision.reason


def test_a_disabled_agent_does_not_take_its_project_task() -> None:
    team = [_agent("titulaire", "backend"), _agent("remplacant", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=team, excluded={"titulaire"}
    ))

    assert decision.agent is not None
    assert decision.agent.nom == "remplacant"


def test_a_fully_disabled_team_falls_back_without_raising() -> None:
    """An explicit fallback rather than a wrong routing — and the team has the
    role: it is switched off, not missing, so nothing is signalled as missing."""
    team = [_agent("titulaire", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=team, excluded={"titulaire"}
    ))

    assert decision.needs_assignment
    assert decision.uncovered == ()


def test_an_empty_team_is_not_an_omission() -> None:
    """It used to be, and a project without any agent was then routed on the
    wiring's catalog: that is what made the code's five roles work in a project
    that had recruited nobody (#1042). `()` means "this project has no agent",
    and the task goes to fallback — the right answer to *a project is born
    without agents*."""
    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=()
    ))

    assert decision.needs_assignment
    assert decision.agent is None
    assert "l'équipe reste à recruter" in decision.reason


def test_the_two_causes_of_a_fallback_without_candidate_stay_distinct() -> None:
    """They are not fixed the same way at all: re-enable an agent, or recruit
    one."""
    switched_off = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=[_agent("titulaire", "backend")],
        excluded={"titulaire"},
    ))
    empty = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=()
    ))

    assert "désactivés" in switched_off.reason
    assert "désactivés" not in empty.reason


# --- ① One rule, two readers -------------------------------------------------


def test_without_project_there_is_no_team_to_give(tmp_path: Path) -> None:
    """`None` says "I have no team to give you": the caller sticks to its
    wiring's catalog."""
    agents = AgentStore(tmp_path / "agents")

    assert catalogue_du_projet(agents, None) is None


def test_without_wired_store_there_is_no_team_to_give() -> None:
    """Tests and wirings without a Control Tower: the question does not arise."""
    assert catalogue_du_projet(None, PROJECT) is None


def test_an_unreadable_store_does_not_send_every_task_to_fallback(
    tmp_path: Path,
) -> None:
    """A storage incident is not an empty team: the wiring's catalog is kept
    rather than routing every task nowhere."""
    agents = AgentStore(tmp_path / "agents")

    assert catalogue_du_projet(agents, "prj/../evade") is None


def test_a_project_without_agent_returns_an_empty_team_not_an_absence(
    tmp_path: Path,
) -> None:
    """The empty tuple is none of the three `None`s (#1042): it is the
    difference between "I do not know" and "there is nobody", and it is what
    makes a task of a project that recruited nobody wait for someone instead of
    going to the code's role templates."""
    agents = AgentStore(tmp_path / "agents")

    assert catalogue_du_projet(agents, PROJECT) == ()


def test_a_project_team_holds_that_project_agents(tmp_path: Path) -> None:
    agents = AgentStore(tmp_path / "agents")
    agents.pour_projet(PROJECT).ecrire(_definition("dev-du-projet", "backend"))
    agents.pour_projet(OTHER).ecrire(_definition("dev-du-voisin", "backend"))

    here = catalogue_du_projet(agents, PROJECT)

    assert here is not None
    names = {agent.nom for agent in here}
    assert "dev-du-projet" in names
    assert "dev-du-voisin" not in names


def test_decomposition_uses_the_project_team() -> None:
    """The rule's second reader: the roles and competencies passed to the
    orchestrator are those of the team that will execute. That is what makes a
    tag proposed at decomposition a tag someone knows how to take."""
    team = [_agent("dev-du-projet", "comptabilite", role="Comptable")]

    prompt = orchestrator_prompt(team)

    assert "comptabilite" in prompt
    assert "dev-du-projet" in prompt
    assert prompt != ORCHESTRATOR_SYSTEM_PROMPT


# --- ③ What nobody covers ----------------------------------------------------


def test_uncovered_competencies_are_the_raw_routing_fact() -> None:
    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "sql", "migration"), agents=[_agent("dev", "backend")]
    ))

    assert decision.needs_assignment
    assert decision.uncovered == ("migration", "sql")


def test_a_task_someone_takes_signals_no_gap() -> None:
    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend"), agents=[_agent("dev", "backend")]
    ))

    assert decision.agent is not None
    assert decision.uncovered == ()


def test_an_unbroken_tie_is_not_a_gap() -> None:
    """The ambiguity is the tiebreak's, not that of a missing role: the task is
    routable, someone covers it."""
    team = [_agent("un", "backend", "api"), _agent("deux", "backend", "api")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "backend", "api"), agents=team
    ))

    assert decision.needs_assignment
    assert decision.uncovered == ()


def test_a_disabled_agent_covers_nothing() -> None:
    """Observed on the **active** candidates: counting the competencies of a
    disabled agent would silence the signal right when it is true."""
    team = [_agent("titulaire", "sql"), _agent("dev", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _task("t1", "sql"), agents=team, excluded={"titulaire"}
    ))

    assert decision.uncovered == ("sql",)


def test_uncovered_competencies_are_sorted() -> None:
    """`Agent.competences` is a `frozenset`: a signal whose order moves from one
    run to the next is not comparable."""
    uncovered = uncovered_competencies(
        ("sql", "monitoring", "migration"), [_agent("dev", "backend", "sql")]
    )

    assert uncovered == ("migration", "monitoring")


# --- ③ bis A named gap is a position, not a list of tags ---------------------


def test_the_gap_names_the_role_template_that_covers_the_most() -> None:
    gap = missing_role(("sql", "migration"))

    assert gap is not None
    assert gap.role_template == "donnees"
    assert gap.role == "Base de données"


def test_a_gap_no_role_template_covers_returns_the_competencies_as_they_are() -> None:
    """A made-up role would read as an existing role: proposing "Designer" for
    a task that requires `comptabilite` would be a recruitment at random."""
    gap = missing_role(("comptabilite",))

    assert gap is not None
    assert gap.role_template is None
    assert gap.role == "comptabilite"


def test_nothing_to_fill_names_no_position() -> None:
    assert missing_role(()) is None


def test_the_gap_sentence_says_the_three_things_needed_to_act() -> None:
    """What is missing, the position it designates, and that recruiting does not
    happen here — without the last one, whoever reads a run may believe Maestro
    will take care of it, and the task would stay "to assign" with nobody
    moving.

    ⚠ The sentence also said "recruiting happens outside the run" until #1227.
    It no longer does, because that became false: the orchestrator proposes
    completing the team **within** the run, before executing, and a sentence
    pointing elsewhere would have the reader look for a screen while an action
    waits in the chat. What did not move is what mattered — an agent does not
    recruit (docs/31 §3.5), and nothing is created without consent (#1040)."""
    sentence = MissingRole(
        competencies=("sql",), role="Expert BDD", role_template="donnees"
    ).sentence()

    assert "sql" in sentence
    assert "gabarit `donnees`" in sentence
    assert "hors du run" not in sentence


# --- ④ The gap reaches the chat, and the task stays to assign ----------------


def _project_executor(tmp_path: Path, *competencies: str) -> LocalExecutor:
    """An executor whose project team covers only `competencies`."""
    agents = AgentStore(tmp_path / "agents")
    agents.pour_projet(PROJECT).ecrire(_definition("dev-du-projet", *competencies))
    return LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
    )


def test_a_task_nobody_can_take_signals_its_position(tmp_path: Path) -> None:
    journal = RunJournal()
    executor = _project_executor(tmp_path, "backend")

    result = asyncio.run(
        executor.execute(_task("t1", "comptabilite", project_id=PROJECT), [], journal)
    )

    assert result.statut == "echec"
    assert result.agent == "—"
    assert "à assigner" in (result.erreur or "")
    assert "aucun rôle de l'équipe ne couvre" in (result.erreur or "")


def test_the_signal_is_recorded_under_the_orchestrator_and_costs_nothing(
    tmp_path: Path,
) -> None:
    """It is the one that distributes the work and the one that recruits, and
    the task by definition has no agent to name. Observing a gap spends
    nothing: routing failed before any model call."""
    journal = RunJournal()
    executor = _project_executor(tmp_path, "backend")

    asyncio.run(executor.execute(_task("t1", "comptabilite", project_id=PROJECT), [], journal))

    gap = next(r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_MANQUE))
    assert gap.agent == "orchestrateur"
    assert gap.statut == STATUT_ROLE_MANQUANT
    assert (gap.usage.calls, gap.usage.input_tokens, gap.usage.output_tokens) == (
        0,
        0,
        0,
    )
    assert "aucun rôle de l'équipe ne couvre" in gap.sortie


def test_a_task_of_a_project_without_agent_waits_for_someone(tmp_path: Path) -> None:
    """The end-to-end criterion: a project that recruited nobody does not make
    the code's role templates work in its place. The task goes to fallback, and
    that is a fact to show, not to fill."""
    agents = AgentStore(tmp_path / "agents")
    executor = LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
    )

    result = asyncio.run(
        executor.execute(_task("t1", "backend", project_id=PROJECT), [], RunJournal())
    )

    assert result.statut == "echec"
    assert "l'équipe reste à recruter" in (result.erreur or "")


def test_outside_a_project_the_wired_catalog_does_the_work(tmp_path: Path) -> None:
    """The complement: without a project there is no team to look in, so the
    wiring's catalog stays the one that works — `maestro-run` has no project
    and never had one."""
    agents = AgentStore(tmp_path / "agents")
    executor = LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
    )

    result = asyncio.run(executor.execute(_task("t1", "backend"), [], RunJournal()))

    assert result.agent == "cablage"


def test_a_team_that_covers_the_task_signals_no_gap(tmp_path: Path) -> None:
    journal = RunJournal()
    executor = _project_executor(tmp_path, "comptabilite")

    asyncio.run(executor.execute(_task("t1", "comptabilite", project_id=PROJECT), [], journal))

    assert all(not r.etape.endswith(SUFFIXE_ETAPE_MANQUE) for r in journal.records)


def test_the_gap_reaches_the_chat_without_creating_a_ghost_card() -> None:
    """It joins `tache.blocage` rather than opening a type: the fact is the
    same — it cannot move forward, and someone is needed. What sets it apart is
    the `statut`, which travels as is."""
    (event,) = evenements_depuis_step(
        {
            "run_id": "run-1",
            "etape": f"t1{SUFFIXE_ETAPE_MANQUE}",
            "nom": "Rôle manquant — Tâche t1",
            "agent": "orchestrateur",
            "role": "Chef de projet",
            "statut": STATUT_ROLE_MANQUANT,
            "entree": "",
            "sortie": "aucun rôle de l'équipe ne couvre : sql — il manque un rôle …",
            "erreur": None,
        }
    )

    assert event.type == EVENEMENT_TACHE_BLOCAGE
    assert event.tache_id == "t1"
    assert event.statut == STATUT_ROLE_MANQUANT


@pytest.mark.parametrize("competence", ["sql", "comptabilite"])
def test_the_engine_never_recruits_on_its_own(tmp_path: Path, competence: str) -> None:
    """The boundary of docs/37 §3.5, taken where it would hold badly: after a
    signalled gap, the project's team is **unchanged**."""
    agents = AgentStore(tmp_path / "agents")
    agents.pour_projet(PROJECT).ecrire(_definition("dev-du-projet", "backend"))
    executor = LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
    )

    asyncio.run(
        executor.execute(_task("t1", competence, project_id=PROJECT), [], RunJournal())
    )

    assert agents.pour_projet(PROJECT).noms() == ("dev-du-projet",)
