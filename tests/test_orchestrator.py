"""Tests de l'orchestrateur : objectif → liste de tâches JSON (ticket #3).

Aucun appel réseau : l'orchestrateur est exercé avec un `ModelProvider` factice
qui renvoie une réponse canned. Couvre les deux critères d'acceptation :
① un objectif produit un plan de tâches JSON valides et bien formées ;
② le schéma de tâche est documenté et validé (validation contre la JSON Schema
partagée + règles inter-tâches).

Et, depuis #1149 ([docs/40 §4](../docs/40-decision-rythme-et-scenarios-de-reference.md)),
③ le **document** du Chef de projet distingue les deux natures d'objectif —
construire, ou agir. C'est un prompt système, donc ce qui se teste est ce qu'il
**dit**, jamais le plan qu'un modèle en tirerait : la fourchette n'est plus un
plancher, une action se planifie en une tâche qui agit, et l'acte que l'objectif
nomme ne se refait pas valider. Les phrases attendues sont courtes et prises au
texte normalisé — reformuler reste possible, retirer la règle doit se voir.
"""

import asyncio
import json
import re

import pytest
from jsonschema import Draft202012Validator

from maestro.orchestrator import (
    MAX_TASKS,
    MIN_TASKS,
    ORCHESTRATOR_SYSTEM_PROMPT,
    Orchestrator,
    PlanParsingError,
    Task,
    TaskValidationError,
    load_task_schema,
    prompt_orchestrateur,
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


def _canned_action_plan_json():
    """Le plan d'un objectif d'**action** : une tâche, qui agit (#1149).

    Ce que le run `8a15f78f45d3` aurait dû rendre le 2026-09-21 — là où il a
    rendu « implémenter un utilitaire de vidage », « le tester », « faire valider
    et exécuter ». Le `format_sortie` est un **état constaté**, pas un fichier.
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


# --- Critère ① : un objectif produit un plan de tâches valides ------------------------


def test_plan_produces_tasks_within_configured_range():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Créer une API REST de gestion de tâches"))

    # Un objectif de **construction** : la fourchette visée reste ce qu'elle guide.
    assert MIN_TASKS <= len(tasks) <= MAX_TASKS
    assert all(isinstance(t, Task) for t in tasks)
    assert [t.id for t in tasks] == ["migration-taches", "api-taches", "tests-api"]
    # Les dépendances déclarées sont bien résolues dans le plan.
    assert tasks[1].dependances == ("migration-taches",)


def test_une_action_rend_un_plan_d_une_seule_tache():
    """#1149 : `MIN_TASKS` n'a jamais été une borne de validation, et ne le devient
    pas. Un plan d'**une** tâche traverse l'orchestrateur entier — extraction,
    schéma partagé, invariants inter-tâches — sans que rien n'exige un compte."""
    provider = FakeProvider(_canned_action_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Vide le dossier du projet"))

    assert len(tasks) == 1
    assert tasks[0].id == "vider-la-racine"
    assert tasks[0].dependances == ()
    # Le livrable d'une action est un état, pas un fichier à produire.
    assert "vide" in tasks[0].format_sortie.lower()


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


# --- Critère ③ : le playbook distingue construire et agir (#1149) ---------------------

#: Un titre de section du document, quel que soit son niveau.
_SECTION = re.compile(r"^#{2,3} (.+)$", re.MULTILINE)


def _normalise(texte: str) -> str:
    """Le texte sans sa mise en forme Markdown ni ses retours à la ligne.

    Même normalisation que `tests/test_playbooks_defaut.py` : le document est
    enveloppé à ~95 colonnes et emphatise au fil du texte, si bien qu'une phrase
    attendue traverse une fin de ligne et porte des `**` au milieu. Chercher la
    phrase brute échouerait sur la mise en page, pas sur le fond.
    """
    sans_emphase = texte.replace("**", "").replace("`", "")
    return " ".join(sans_emphase.split())


def _playbook() -> str:
    """Le playbook effectif du Chef de projet — marqueurs substitués, normalisé."""
    return _normalise(ORCHESTRATOR_SYSTEM_PROMPT)


def test_le_playbook_nomme_les_deux_natures_d_objectif():
    """La distinction est ce qui porte tout le reste : sans elle, « une action est
    une tâche » ne serait qu'une exception de plus dans un document qui en a déjà."""
    titres = [m.group(1).strip() for m in _SECTION.finditer(ORCHESTRATOR_SYSTEM_PROMPT)]

    assert any("construire, ou agir" in titre for titre in titres), titres
    texte = _playbook()
    # Les deux natures, et de quoi les reconnaitre sans lexique : ce qui doit
    # exister, ou ce qui doit changer.
    assert "Construire" in texte and "Agir" in texte
    assert "vider un dossier" in texte
    assert "renommer ou déplacer des fichiers" in texte
    assert "lancer une commande" in texte


def test_une_action_se_planifie_en_une_tache_qui_agit():
    """Les trois tâches du run `8a15f78f45d3`, nommées et écartées une à une :
    l'utilitaire, ses tests, sa validation. Une seule règle générale les couvrirait
    mal — c'est chacune de ces trois-là que le plan a réellement produites."""
    texte = _playbook()

    assert "une seule tâche" in texte
    assert "aucun utilitaire" in texte
    assert "aucune tâche de tests" in texte
    assert "aucune tâche de validation" in texte
    assert "aucun plancher" in texte


def test_la_fourchette_de_taches_n_est_plus_un_plancher():
    """#1149 renverse le critère du ticket #6 (`MIN_TASKS`, docs/06 §Phase 0). La
    fourchette reste **visée** pour un objectif de construction — elle est
    substituée depuis les constantes, jamais recopiée dans le texte."""
    texte = _playbook()

    assert f"{MIN_TASKS} à {MAX_TASKS} tâches" in texte
    assert "pas un plancher" in texte
    # La formulation renversée ne doit pas survivre quelque part dans le document.
    assert "reste le plancher" not in texte
    # Et le nombre de tâches reste une conséquence, jamais un quota.
    assert "jamais un quota à remplir" in texte


def test_un_acte_nomme_par_l_objectif_ne_se_refait_pas_valider():
    """L'accord donné au cadrage vaut décision humaine (docs/40 §4). La tâche
    « faire valider et exécuter » est nommée pour être interdite, et la famille
    « acte irréversible » ne disparaît pas : elle se replie sur ce que l'objectif
    ne nomme pas."""
    texte = _playbook()

    assert "approuvé" in texte
    assert "faire valider" in texte
    assert "ne nomme pas" in texte
    # Ce qui demande un humain reste une famille du document : on retire la tâche
    # redondante, jamais l'escalade.
    assert "un acte irréversible" in texte
    # Et la redondance est retirée jusque dans l'exécution : la latitude écrite sur
    # la tâche dit à l'agent que l'acte nommé est accordé, au lieu de le lui faire
    # redemander au moment de le faire.
    assert "est déjà accordé et se fait" in texte


def test_le_playbook_dit_ce_qui_ne_bouge_pas_autour_de_l_acte():
    """Les deux garde-fous que docs/40 §4 range dans « ce qui ne bouge pas » :
    l'arbitrage au moment de l'acte, et le périmètre du projet. Le plan ne les
    tient pas lui-même — il les **écrit dans la tâche**, seul endroit où l'agent
    qui agit les lira."""
    texte = _playbook()

    assert "lève la main au moment de l'acte" in texte
    assert ".git" in texte and ".env" in texte
    assert "exclusions déclarées" in texte


def test_le_format_de_sortie_d_une_action_est_un_etat():
    """Le contrat de sortie ne change pas d'une clé — ce qui change est ce qu'on
    écrit dans `format_sortie` quand la tâche agit."""
    texte = _playbook()

    assert "état constaté" in texte
    assert "jamais un fichier à produire" in texte


def test_le_playbook_cadre_sur_l_equipe_reste_celui_du_document():
    """Garde de non-régression sur le chargeur (#1041) : le document servi à une
    équipe donnée porte les mêmes règles que celui du code, marqueurs substitués."""
    cadre = _normalise(prompt_orchestrateur())

    assert "construire, ou agir" in cadre.lower()
    assert "{{" not in cadre
