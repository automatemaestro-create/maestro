"""Tests de l'orchestrateur : objectif → liste de tâches JSON (ticket #3).

Aucun appel réseau : l'orchestrateur est exercé avec un `ModelProvider` factice
qui renvoie une réponse canned. Couvre les deux critères d'acceptation :
① un objectif produit 2 à 3 tâches JSON valides et bien formées ;
② le schéma de tâche est documenté et validé (validation contre la JSON Schema
partagée + règles inter-tâches).
"""

import asyncio
import json

import pytest
from jsonschema import Draft202012Validator

from maestro.orchestrator import (
    MAX_TASKS,
    MIN_TASKS,
    Orchestrator,
    PlanParsingError,
    Task,
    TaskValidationError,
    load_task_schema,
    validate_plan,
    validate_task,
)
from maestro.orchestrator.orchestrator import _extract_task_array
from maestro.providers.base import ModelProvider

# --- Provider factice (pas de réseau) -------------------------------------------------


class FakeProvider(ModelProvider):
    """Renvoie une réponse préprogrammée et enregistre les arguments d'appel."""

    name = "fake"

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return self._response


def _valid_task(**overrides):
    task = {
        "id": "migration-taches",
        "titre": "Créer la migration de la table taches",
        "description": "Définir le schéma SQL de la table des tâches et sa migration.",
        "competences_requises": ["sql", "migration"],
        "format_sortie": "Fichier SQL de migration",
        "dependances": [],
    }
    task.update(overrides)
    return task


def _canned_plan_json():
    return json.dumps(
        [
            _valid_task(),
            {
                "id": "api-taches",
                "titre": "Exposer l'API REST des tâches",
                "description": "Endpoints CRUD sur les tâches, adossés au schéma.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API + doc des endpoints",
                "dependances": ["migration-taches"],
            },
            {
                "id": "tests-api",
                "titre": "Tester l'API des tâches",
                "description": "Tests d'intégration des endpoints CRUD.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests + rapport",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


# --- Critère ① : un objectif produit 2 à 3 tâches valides -----------------------------


def test_plan_produces_two_to_three_valid_tasks():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Créer une API REST de gestion de tâches"))

    assert MIN_TASKS <= len(tasks) <= MAX_TASKS
    assert all(isinstance(t, Task) for t in tasks)
    assert [t.id for t in tasks] == ["migration-taches", "api-taches", "tests-api"]
    # Les dépendances déclarées sont bien résolues dans le plan.
    assert tasks[1].dependances == ("migration-taches",)


def test_plan_forwards_system_prompt_and_model():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-sonnet-5")

    asyncio.run(orchestrator.plan("Un objectif"))

    call = provider.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert "Chef de projet" in call["system_prompt"]
    assert "Un objectif" in call["prompt"]


def test_plan_rejects_empty_objective():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")
    with pytest.raises(ValueError):
        asyncio.run(orchestrator.plan("   "))


# --- Critère ② : le schéma est valide, documenté et applique la validation ------------


def test_shared_schema_is_a_valid_json_schema():
    # Le fichier partagé packages/shared/schemas/task.schema.json est une JSON Schema
    # draft 2020-12 bien formée.
    Draft202012Validator.check_schema(load_task_schema())


def test_validate_task_accepts_well_formed_task():
    validate_task(_valid_task())  # ne lève pas


@pytest.mark.parametrize(
    "mutation",
    [
        {"titre": ""},  # champ vide
        {"competences_requises": []},  # tableau requis non vide
        {"id": "Bad_ID"},  # slug invalide (majuscule + underscore)
    ],
)
def test_validate_task_rejects_malformed(mutation):
    with pytest.raises(TaskValidationError):
        validate_task(_valid_task(**mutation))


def test_validate_task_rejects_missing_required_field():
    task = _valid_task()
    del task["format_sortie"]
    with pytest.raises(TaskValidationError):
        validate_task(task)


def test_validate_task_rejects_additional_property():
    with pytest.raises(TaskValidationError):
        validate_task(_valid_task(priorite="haute"))


def test_validate_plan_rejects_empty_plan():
    with pytest.raises(TaskValidationError):
        validate_plan([])


def test_validate_plan_rejects_duplicate_ids():
    with pytest.raises(TaskValidationError):
        validate_plan([_valid_task(), _valid_task()])


def test_validate_plan_rejects_unknown_dependency():
    with pytest.raises(TaskValidationError):
        validate_plan([_valid_task(dependances=["fantome"])])


def test_validate_plan_rejects_self_dependency():
    with pytest.raises(TaskValidationError):
        validate_plan([_valid_task(dependances=["migration-taches"])])


def test_validate_plan_rejects_cycle():
    a = _valid_task(id="a", dependances=["b"])
    b = _valid_task(id="b", dependances=["a"])
    with pytest.raises(TaskValidationError):
        validate_plan([a, b])


def test_validate_plan_returns_tasks():
    tasks = validate_plan(json.loads(_canned_plan_json()))
    assert [t.id for t in tasks] == ["migration-taches", "api-taches", "tests-api"]


def test_task_dict_roundtrip():
    task = Task.from_dict(_valid_task(dependances=["autre"]))
    again = Task.from_dict(task.to_dict())
    assert again == task


# --- Extraction JSON tolérante --------------------------------------------------------


def test_extract_direct_array():
    tasks = _extract_task_array(_canned_plan_json())
    assert len(tasks) == 3


def test_extract_from_markdown_fence():
    text = f"Voici le plan :\n```json\n{_canned_plan_json()}\n```\nVoilà."
    tasks = _extract_task_array(text)
    assert len(tasks) == 3


def test_extract_from_wrapped_object():
    wrapped = json.dumps({"taches": json.loads(_canned_plan_json())}, ensure_ascii=False)
    tasks = _extract_task_array(wrapped)
    assert len(tasks) == 3


def test_extract_from_surrounding_prose():
    text = f"Bien sûr, voici les tâches : {_canned_plan_json()} — bon courage !"
    tasks = _extract_task_array(text)
    assert len(tasks) == 3


def test_extract_rejects_non_json():
    with pytest.raises(PlanParsingError):
        _extract_task_array("désolé, je ne peux pas produire de plan")


def test_extract_rejects_empty_response():
    with pytest.raises(PlanParsingError):
        _extract_task_array("   ")


def test_extract_rejects_array_of_non_objects():
    with pytest.raises(PlanParsingError):
        _extract_task_array("[1, 2, 3]")
