"""Tests of the orchestrator: goal → list of JSON tasks (#3).

No network call: the orchestrator runs against a fake `ModelProvider` that returns a
canned answer. The suite proves that:

① a goal yields a plan of valid, well-formed JSON tasks;
② the task schema is documented and enforced (validation against the shared JSON Schema,
plus the cross-task rules);
③ the orchestrator's **document** tells the two kinds of goal apart — build, or act
(#1149, [docs/40 §4](../docs/40-decision-rythme-et-scenarios-de-reference.md)). It is a
system prompt, so what is tested is what it **says**, never the plan a model would draw
from it: there is no floor on the number of tasks, an action is planned as one task that
acts, and the act the goal names is not approved again. The expected sentences are short
and taken from the normalised text — rewording stays possible, removing the rule shows;
④ it splits a goal into **capabilities the size of a session**, tests included, with no
task-count range: no tests-only task, no upstream task without a deliverable of its own,
no task per element when the elements hold together (#1531,
[docs/50 §§ 2 and 4.1](../docs/50-ecart-face-a-un-agent-seul.md)).
"""

import asyncio
import json
import re
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from maestro.orchestrator import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    Orchestrator,
    PlanParsingError,
    Task,
    TaskValidationError,
    load_task_schema,
    orchestrator_prompt,
    validate_plan,
    validate_task,
)
from maestro.orchestrator.orchestrator import _extract_task_array
from maestro.providers.base import ModelProvider

# --- Fake provider (no network) -------------------------------------------------------


class FakeProvider(ModelProvider):
    """Returns a preprogrammed answer and records the call arguments."""

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


def _canned_action_plan_json():
    """The plan of an **action** goal: one task, which acts (#1149).

    What run `8a15f78f45d3` should have returned, where it planned a draining utility,
    its tests and its approval. The `format_sortie` is an **observed state**, not a file.
    """
    return json.dumps(
        [
            {
                "id": "vider-la-racine",
                "titre": "Vider le dossier du projet",
                "description": (
                    "Objectif : supprimer le contenu de la racine du projet. Périmètre "
                    "et limites : le périmètre exclu du projet (.git, .env, les secrets "
                    "et les exclusions declarees) n'est pas touche. Latitude de "
                    "decision : le moyen t'appartient. Criteres de reussite : la racine "
                    "ne contient plus que les chemins exclus."
                ),
                "competences_requises": ["backend"],
                "format_sortie": "La racine du projet vide, hors périmètre exclu",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )


# --- Criterion ①: a goal yields a plan of valid tasks ---------------------------------


def test_plan_produces_tasks():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Créer une API REST de gestion de tâches"))

    assert all(isinstance(t, Task) for t in tasks)
    assert [t.id for t in tasks] == ["migration-taches", "api-taches", "tests-api"]
    # The declared dependencies are resolved in the plan.
    assert tasks[1].dependencies == ("migration-taches",)


def test_an_action_yields_a_single_task_plan():
    """A one-task plan goes through the whole orchestrator — extraction, shared schema,
    cross-task invariants — and nothing requires a minimum count (#1149)."""
    provider = FakeProvider(_canned_action_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Vide le dossier du projet"))

    assert len(tasks) == 1
    assert tasks[0].id == "vider-la-racine"
    assert tasks[0].dependencies == ()
    # The deliverable of an action is a state, not a file to produce.
    assert "vide" in tasks[0].output_format.lower()


def test_plan_forwards_system_prompt_and_model():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-sonnet-5")

    asyncio.run(orchestrator.plan("Un objectif"))

    call = provider.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert "Chef de projet" in call["system_prompt"]
    assert "Un objectif" in call["prompt"]


def test_plan_rejects_empty_goal():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")
    with pytest.raises(ValueError):
        asyncio.run(orchestrator.plan("   "))


# --- Criterion ②: the schema is valid, documented and enforces validation -------------


def test_shared_schema_is_a_valid_json_schema():
    # The shared file packages/shared/schemas/task.schema.json is a well-formed
    # draft 2020-12 JSON Schema.
    Draft202012Validator.check_schema(load_task_schema())


def test_validate_task_accepts_well_formed_task():
    validate_task(_valid_task())  # does not raise


@pytest.mark.parametrize(
    "mutation",
    [
        {"titre": ""},  # empty field
        {"competences_requises": []},  # required array must not be empty
        {"id": "Bad_ID"},  # invalid slug (uppercase + underscore)
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


def test_the_resumed_task_travels_to_the_worker_and_never_comes_from_a_plan():
    """`resumes_from` (wire key `reprend`, #1396) travels through the queue like the rest
    of the task, schema included.

    The worker revalidates the task against the shared schema before running it: a field
    the schema ignored would be refused there, and the resplit would start again from the
    base. But only the engine sets it — a plan that wrote it would not make a task start
    from another task's branch.
    """
    resumed = replace(Task.from_dict(_valid_task()), resumes_from="socle")
    raw = json.loads(json.dumps(resumed.to_dict()))

    validate_task(raw)
    assert Task.from_dict(raw).resumes_from == "socle"
    assert "reprend" not in Task.from_dict(_valid_task()).to_dict()
    (planned,) = validate_plan([raw])
    assert planned.resumes_from == ""


# --- Tolerant JSON extraction ---------------------------------------------------------


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


# --- Criterion ③: the playbook tells building and acting apart (#1149) ----------------

#: A section title of the document, whatever its level.
_SECTION = re.compile(r"^#{2,3} (.+)$", re.MULTILINE)


def _normalise(text: str) -> str:
    """The text without its Markdown formatting or its line breaks.

    Same normalisation as `tests/test_playbooks_defaut.py`: the document wraps at
    ~95 columns and emphasises inline, so an expected sentence crosses a line end and
    carries `**` in the middle. Searching for the raw sentence would fail on the layout,
    not on the substance.
    """
    without_emphasis = text.replace("**", "").replace("`", "")
    return " ".join(without_emphasis.split())


def _playbook() -> str:
    """The orchestrator's effective playbook — placeholders substituted, normalised."""
    return _normalise(ORCHESTRATOR_SYSTEM_PROMPT)


def test_the_playbook_names_both_kinds_of_goal():
    """The distinction carries everything else: without it, "an action is one task"
    would be just one more exception in a document that already has some."""
    titles = [m.group(1).strip() for m in _SECTION.finditer(ORCHESTRATOR_SYSTEM_PROMPT)]

    assert any("construire, ou agir" in title for title in titles), titles
    text = _playbook()
    # Both kinds, and how to recognise them without a lexicon: what must exist, or
    # what must change.
    assert "Construire" in text and "Agir" in text
    assert "vider un dossier" in text
    assert "renommer ou déplacer des fichiers" in text
    assert "lancer une commande" in text


def test_an_action_is_planned_as_one_task_that_acts():
    """The three tasks of run `8a15f78f45d3` are named and ruled out one by one: the
    utility, its tests, its approval. A single general rule would cover them poorly —
    these three are the ones the plan actually produced."""
    text = _playbook()

    assert "une seule tâche" in text
    assert "aucun utilitaire" in text
    assert "aucune tâche de tests" in text
    assert "aucune tâche de validation" in text
    assert "aucun plancher" in text


def test_no_task_count_range_is_targeted():
    """The playbook aims at no task-count range, and no count placeholder is left to
    substitute (#1149, #1531). A range, even framed as a signal to reread, pulled p5 —
    which fits in one session — towards a count the goal did not call for (docs/50 § 2:
    9 tasks on 4 levels). What is reread is the size of each task."""
    text = _playbook()
    raw = orchestrator_prompt()

    assert not re.search(r"\b\d+ à \d+ tâches\b", text), "une fourchette est revenue"
    assert "{{min_taches}}" not in raw and "{{max_taches}}" not in raw
    assert "pas un plancher" in text
    assert "reste le plancher" not in text
    # The number of tasks stays a consequence, never a quota.
    assert "jamais un quota à remplir" in text
    assert "Ce qui se relit est la taille de chaque tâche, jamais leur nombre" in text


def test_the_number_of_tasks_is_never_bounded_by_the_code():
    """The rule is the model's judgement (docs/41), never a quota: the C2 plan, nine tasks
    on four levels, passes validation like a one-task plan. What the playbook asks never
    becomes a schema refusal."""
    c2_plan = [
        _valid_task(id=task_id, dependances=list(dependencies))
        for task_id, dependencies in _C2_PLAN
    ]

    tasks = validate_plan(c2_plan)

    assert len(tasks) == _C2_TASKS
    assert _levels(tasks) == _C2_LEVELS


def test_an_act_the_goal_names_is_not_approved_again():
    """The consent given at framing counts as a human decision (docs/40 §4). The "get it
    approved and run it" task is named so as to forbid it, and the "irreversible act"
    family does not disappear: it falls back on what the goal does not name."""
    text = _playbook()

    assert "approuvé" in text
    assert "faire valider" in text
    assert "ne nomme pas" in text
    # What needs a human stays a family of the document: the redundant task goes,
    # never the escalation.
    assert "un acte irréversible" in text
    # And the redundancy is gone down to the execution: the latitude written on the task
    # tells the agent the named act is granted, instead of making it ask again at the
    # moment it acts.
    assert "est déjà accordé et se fait" in text


def test_the_playbook_says_what_stays_fixed_around_the_act():
    """The guardrails docs/40 §4 lists under "what does not move", **minus one**.

    The project perimeter and the agent's policy still hold, and the plan does not hold
    them itself: it **writes them into the task**, the only place where the acting agent
    reads them.

    The exception is the approval of the act the goal **names** (#1198): asking at the
    moment of the act meant asking the person again at **every command** (S1, 2026-09-22:
    five requests dismissed at 240 s and a folder left intact). Escalation stays whole on
    what the goal did not name — and that half is what the text keeps saying.
    """
    text = _playbook()

    assert "suspend l'appel d'outil le temps qu'un humain tranche" in text
    assert "Ce que l'objectif n'a pas nommé" in text
    assert ".git" in text and ".env" in text
    assert "exclusions déclarées" in text


def test_the_consent_given_by_the_goal_is_written_into_the_task():
    """The consent given at framing travels as a **key**, not as prose (#1198).

    The description speaks to the agent; the `acte_accorde` key (`Task.granted_act`)
    speaks to the execution, which reads no prose. Without the key, the consent stops at
    the plan and the approval just removed from it comes back once per command.

    The playbook must state the three edges, or the key would become a free pass: only
    the task that commits the act, only an act the goal names, and the act written **as
    it was granted**.
    """
    text = _playbook()

    assert "acte_accorde" in text
    assert "à chaque commande" in text
    assert "tel qu'il a été accordé" in text
    # A task that builds never carries it — that is what keeps it narrow.
    assert "une tâche qui construit ne la porte jamais" in text
    # And it is announced in the output contract, with the other keys.
    assert '"acte_accorde" :' in text


def test_the_output_format_of_an_action_is_a_state():
    """The output contract does not change by a single key — what changes is what goes
    into `format_sortie` when the task acts."""
    text = _playbook()

    assert "état constaté" in text
    assert "jamais un fichier à produire" in text


def test_the_plan_brings_out_the_work_done_in_parallel_across_capabilities():
    """Independent capabilities run in parallel (#1299, #1531).

    "Link only the real dependencies" is not enough to make independence **emerge** —
    both runs of `p3` had a width of 1. The positive rule: independent capabilities run in
    parallel, with no dependency between them, even for a single role; what they share
    comes from a capability they depend on or is written into each one, never through a
    chain; what joins them, when there is something to produce, depends on all of them —
    and is not a task that would only check (#1530, `tests/test_recette_du_run.py`).

    Parallelism is gained across capabilities, never by cutting up a capability that fits
    in a session: splitting per element made p5 nine tasks on four levels for a goal that
    fits in one session. Parallelism stays due to a goal that exceeds a session (#1513).
    """
    text = _playbook()

    assert "Le travail qui se fait de front" in text
    assert "même confiées au même rôle" in text
    assert "ne les enchaîne jamais pour qu'elles se le transmettent" in text
    assert "elle dépend alors d'elles toutes, et elle produit" in text
    assert "Le front se gagne entre capacités" in text
    assert "jamais en coupant en morceaux une capacité qui tient dans une session" in text
    # The overturned rule survives nowhere in the document.
    assert "est quatre tâches" not in text
    assert "fais une tâche par élément" not in text
    assert "se décide dans une tâche amont dont ils dépendent tous" not in text


def test_a_task_is_a_capability_the_size_of_a_session():
    """Criterion ① of #1531: the repository's own chain splits this way (docs/10 § 5.1,
    #1150), and p5, which fits in one session, would be one ticket there (docs/50 § 3,
    point 2).

    The three shapes C2 took and the rule rules out are named one by one —
    `tests-logique`, `charte-ui` and `socle-projet`, the four `page-*` —, because a
    general rule on the "right granularity" already covered them poorly. And an element
    that gets its own task justifies it in writing: size, trade or delivery.
    """
    text = _playbook()

    assert "Une tâche est une capacité, à la taille d'une session" in text
    assert "capacités visibles" in text
    assert "dans une seule session de travail" in text
    assert "ses tests" in text
    # What a task costs on top of its work: the reason for the rule.
    assert "chaque niveau du plan ajoute ce coût au chemin" in text
    # The three shapes ruled out.
    assert "une tâche de tests seuls" in text
    assert "une tâche amont sans livrable propre" in text
    assert "une tâche par élément quand les éléments tiennent ensemble dans une session" in text
    # The three reasons that make an element its own task, and the written justification.
    assert "sa taille" in text and "son métier" in text and "sa livraison" in text
    assert "écrit pourquoi dans sa description" in text
    # The plan review and the "done" criteria say it again.
    assert "Relis-le contre le point 4" in text
    assert "Chaque tâche est une capacité visible qui tient dans une session" in text


#: The C2 plan, reread in the journal of run `fea601a5c629` (docs/50 § 2): nine tasks,
#: thirteen links, four levels — foundation, then style guide, recipe set and tests
#: alone, then four pages, then acceptance.
_C2_PLAN = (
    ("socle-projet", ()),
    ("charte-ui", ("socle-projet",)),
    ("jeu-recettes", ("socle-projet",)),
    ("page-accueil", ("charte-ui",)),
    ("page-liste", ("charte-ui",)),
    ("page-detail", ("charte-ui",)),
    ("page-favoris", ("charte-ui",)),
    ("tests-logique", ("socle-projet",)),
    (
        "recette-finale",
        (
            "jeu-recettes",
            "page-accueil",
            "page-liste",
            "page-detail",
            "page-favoris",
            "tests-logique",
        ),
    ),
)
_C2_TASKS = 9
_C2_LEVELS = 4


def _levels(tasks) -> int:
    """The number of levels of a plan, by the product's own measure (`maestro.plan_run`)."""
    from maestro.plan_run import niveaux_topologiques, noeuds_du_plan

    return 1 + max(niveaux_topologiques(noeuds_du_plan(tasks)).values(), default=-1)


def _p5_plan_in_capabilities_json() -> str:
    """A **p5-like** plan split into capabilities: what the playbook asks for.

    Two visible capabilities, each with its tests: browsing the recipes — foundation,
    style guide, data and the four pages, which hold together in one session — then
    cooking a recipe, which depends on it and says so. No tests-only task, no upstream
    style guide, no acceptance task in the plan.
    """
    return json.dumps(
        [
            {
                "id": "parcourir-les-recettes",
                "titre": "Livrer le livre de recettes consultable",
                "description": (
                    "Objectif : à la racine du projet, l'application Next.js où l'on "
                    "parcourt, recherche, filtre et consulte les recettes, et garde ses "
                    "favoris. Périmètre et limites : socle TypeScript, charte visuelle, "
                    "jeu de recettes, pages accueil, liste, détail et favoris, et leurs "
                    "tests Vitest — une seule tâche, parce que ces pages partagent leur "
                    "charte et tiennent ensemble dans une session. Latitude de décision : "
                    "l'agent tranche la structure et les bibliothèques. Critères de "
                    "réussite : npm run build et npm test passent sur un clone neuf."
                ),
                "competences_requises": ["frontend", "tests"],
                "format_sortie": "Application Next.js et ses tests Vitest",
                "dependances": [],
            },
            {
                "id": "cuisiner-une-recette",
                "titre": "Suivre une recette pas à pas",
                "description": (
                    "Objectif : le mode cuisine — étapes une à une, portions ajustées, "
                    "minuteurs. Périmètre et limites : une tâche à part pour sa taille, "
                    "l'application entière ne tenant plus dans une session ; elle part de "
                    "la page détail livrée par parcourir-les-recettes. Latitude de "
                    "décision : l'agent tranche l'interaction. Critères de réussite : "
                    "passer de 2 à 4 portions double les quantités, ses tests le prouvent."
                ),
                "competences_requises": ["frontend", "tests"],
                "format_sortie": "Mode cuisine et ses tests Vitest",
                "dependances": ["parcourir-les-recettes"],
            },
        ],
        ensure_ascii=False,
    )


def test_a_p5_like_plan_is_planned_in_capabilities():
    """Criterion ① of #1531, on the doubled model: the p5 goal — the comparative bench
    message (`maestro/scenarios/objectifs/p5.toml`), word for word — goes to the model
    under the capabilities playbook, and the plan it returns goes through the whole
    orchestrator with fewer tasks **and** fewer levels than C2.

    The double returns what the playbook asks; what is tested is the path: the
    instruction the model receives, then the measure of levels by the product
    (`maestro.plan_run`), the same as the graph's and the parallelism cap's.
    """
    from maestro.scenarios.comparatif import charger_objectif

    p5 = charger_objectif("p5")
    provider = FakeProvider(_p5_plan_in_capabilities_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan(p5.message))

    call = provider.calls[0]
    system = _normalise(call["system_prompt"])
    assert "Une tâche est une capacité, à la taille d'une session" in system
    assert "une tâche de tests seuls" in system
    assert "Construis l'application entière." in call["prompt"]
    assert len(tasks) < _C2_TASKS
    assert _levels(tasks) < _C2_LEVELS
    # Tests go with the capability they prove: no task is tests only.
    assert all(set(t.required_competencies) != {"tests"} for t in tasks)
    # The separate task says why, in its description.
    assert "pour sa taille" in tasks[1].description


def test_the_plan_names_the_project_root_never_its_folder_name():
    """The planner designates the project root as such, never by the project's name nor
    its folder's (#1399): an agent working in a copy that does not bear that name looked
    for a `p5` subfolder that did not exist.

    The planner receives neither name nor path, but it knows the agent's directory **is**
    the project root ("Agir", above): that is how it designates it. The rule lives in its
    **inputs**, where it learns what it does not receive — and no rewriting of the text
    it returns replaces it."""
    text = _playbook()

    assert "est la racine du projet" in text
    assert "désigne-le par « la racine du projet »" in text
    assert "jamais par le nom du projet ni par celui de son dossier" in text


def test_the_playbook_served_for_a_team_stays_the_document():
    """Non-regression guard on the loader (#1041): the document served to a given team
    carries the same rules as the code's, placeholders substituted."""
    framed = _normalise(orchestrator_prompt())

    assert "construire, ou agir" in framed.lower()
    assert "{{" not in framed
