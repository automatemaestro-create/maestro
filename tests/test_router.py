"""Tests du routeur par règles de compétences (ticket #6).

Couvre : choix du meilleur recouvrement, routage par domaine, départage
déterministe des ex æquo (ordre du catalogue), et les deux cas d'erreur (aucun
agent compétent → `RoutingError` ; catalogue vide → `ValueError`).
"""

import pytest

from maestro.agents import DEFAULT_AGENTS
from maestro.agents.catalog import Agent
from maestro.orchestrator import Task
from maestro.router import RoutingError, assign


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


def test_assign_choisit_le_meilleur_recouvrement():
    result = assign(_task(competences_requises=("sql", "migration")), DEFAULT_AGENTS)
    assert result.agent.nom == "bdd"
    assert result.score == 2


def test_assign_route_chaque_domaine_vers_son_agent():
    assert assign(_task(competences_requises=("ui",)), DEFAULT_AGENTS).agent.nom == "designer"
    assert assign(_task(competences_requises=("tests",)), DEFAULT_AGENTS).agent.nom == "qa"
    assert assign(_task(competences_requises=("deploy",)), DEFAULT_AGENTS).agent.nom == "devops"
    assert assign(_task(competences_requises=("api",)), DEFAULT_AGENTS).agent.nom == "developpeur"


def test_assign_sans_agent_competent_leve_routing_error():
    # 'planning' est une compétence du Chef de projet, absente des exécutants.
    with pytest.raises(RoutingError):
        assign(_task(competences_requises=("planning",)), DEFAULT_AGENTS)


def test_assign_sans_agents_leve_value_error():
    with pytest.raises(ValueError):
        assign(_task(), [])


def test_egalite_departagee_par_ordre_du_catalogue():
    a = Agent(nom="a", role="A", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    b = Agent(nom="b", role="B", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    task = _task(competences_requises=("x",))
    assert assign(task, [a, b]).agent.nom == "a"
    assert assign(task, [b, a]).agent.nom == "b"
