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

Et, depuis #1531 ([docs/50 §§ 2 et 4.1](../docs/50-ecart-face-a-un-agent-seul.md)),
④ il découpe en **capacités à la taille d'une session**, tests compris, sans
fourchette de tâches : ni tâche de tests seuls, ni tâche amont sans livrable
propre, ni une tâche par élément quand les éléments tiennent ensemble.
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


def test_plan_produces_tasks():
    provider = FakeProvider(_canned_plan_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    tasks = asyncio.run(orchestrator.plan("Créer une API REST de gestion de tâches"))

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


def test_la_tache_reprise_voyage_jusqu_au_worker_et_ne_vient_jamais_d_un_plan():
    """`reprend` (#1396) traverse la file comme le reste de la tâche, schéma compris.

    Le worker revalide la tâche contre le schéma partagé avant de l'exécuter : un
    champ que le schéma ignorerait y serait refusé, et le redécoupage repartirait
    de la base. Mais c'est le moteur seul qui le pose — un plan qui l'écrirait ne
    ferait pas partir une tâche de la branche d'une autre.
    """
    reprise = replace(Task.from_dict(_valid_task()), reprend="socle")
    brut = json.loads(json.dumps(reprise.to_dict()))

    validate_task(brut)
    assert Task.from_dict(brut).reprend == "socle"
    assert "reprend" not in Task.from_dict(_valid_task()).to_dict()
    (planifiee,) = validate_plan([brut])
    assert planifiee.reprend == ""


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


def test_aucune_fourchette_de_taches_n_est_visee():
    """#1149 avait retiré le plancher du ticket #6 (`MIN_TASKS`, docs/06 §Phase 0) ;
    #1531 retire la fourchette elle-même. « 3 à 5 tâches », même présentée comme un
    signal à relire, tirait p5 — qui tient en une session — vers un compte que
    l'objectif n'appelait pas (docs/50 § 2 : 9 tâches sur 4 niveaux). Ce qui se
    relit est la taille de chaque tâche, et aucun marqueur de compte ne reste à
    substituer."""
    texte = _playbook()
    brut = prompt_orchestrateur()

    assert not re.search(r"\b\d+ à \d+ tâches\b", texte), "une fourchette est revenue"
    assert "{{min_taches}}" not in brut and "{{max_taches}}" not in brut
    assert "pas un plancher" in texte
    assert "reste le plancher" not in texte
    # Le nombre de tâches reste une conséquence, jamais un quota.
    assert "jamais un quota à remplir" in texte
    assert "Ce qui se relit est la taille de chaque tâche, jamais leur nombre" in texte


def test_le_nombre_de_taches_n_est_jamais_borne_par_le_code():
    """La règle est un jugement du modèle (docs/41), jamais un quota : le plan de C2,
    neuf tâches sur quatre niveaux, traverse la validation comme celui d'une tâche.
    Ce que le playbook demande ne devient pas un refus de schéma."""
    plan_c2 = [
        _valid_task(id=identifiant, dependances=list(dependances))
        for identifiant, dependances in _PLAN_C2
    ]

    taches = validate_plan(plan_c2)

    assert len(taches) == _C2_TACHES
    assert _niveaux(taches) == _C2_NIVEAUX


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
    """Les garde-fous que docs/40 §4 range dans « ce qui ne bouge pas », **moins un**.

    Le périmètre du projet et la politique de l'agent tiennent toujours, et le plan
    ne les tient pas lui-même : il les **écrit dans la tâche**, seul endroit où
    l'agent qui agit les lira.

    Ce qui a bougé est l'arbitrage sur l'acte que l'objectif **nomme** (#1198).
    #1149 promettait qu'« il lève la main au moment de l'acte » suffirait ; mesuré
    le 2026-09-22 sur S1, cela voulait dire une personne redemandée à **chaque
    commande**, cinq demandes écartées à 240 s et un dossier intact. L'escalade
    reste entière sur ce que l'objectif n'a pas nommé — et c'est cette moitié-là
    que le texte doit continuer de dire.
    """
    texte = _playbook()

    assert "suspend l'appel d'outil le temps qu'un humain tranche" in texte
    assert "Ce que l'objectif n'a pas nommé" in texte
    assert ".git" in texte and ".env" in texte
    assert "exclusions déclarées" in texte


def test_l_accord_de_l_objectif_s_ecrit_dans_la_tache():
    """L'accord donné au cadrage voyage par une **clé**, pas par de la prose (#1198).

    La description s'adresse à l'agent ; `acte_accorde` s'adresse à l'exécution,
    qui ne lit aucune prose. Sans la clé, l'accord s'arrête au plan et la
    validation qu'on vient d'en retirer revient une fois par commande.

    Le playbook doit dire les trois bords, faute de quoi la clé deviendrait un
    laissez-passer : seulement la tâche qui commet l'acte, seulement un acte que
    l'objectif nomme, et l'acte écrit **tel qu'il a été accordé**.
    """
    texte = _playbook()

    assert "acte_accorde" in texte
    assert "à chaque commande" in texte
    assert "tel qu'il a été accordé" in texte
    # Une tâche qui construit ne la porte jamais — c'est ce qui la garde étroite.
    assert "une tâche qui construit ne la porte jamais" in texte
    # Et elle est annoncée au contrat de sortie, avec les autres clés.
    assert '"acte_accorde" :' in texte


def test_le_format_de_sortie_d_une_action_est_un_etat():
    """Le contrat de sortie ne change pas d'une clé — ce qui change est ce qu'on
    écrit dans `format_sortie` quand la tâche agit."""
    texte = _playbook()

    assert "état constaté" in texte
    assert "jamais un fichier à produire" in texte


def test_le_plan_degage_le_travail_qui_se_fait_de_front_entre_capacites():
    """#1299 : « ne relier que les dépendances réelles » ne suffisait pas à faire
    **dégager** l'indépendance — les deux runs de `p3` avaient une largeur de 1.

    La règle positive reste : des capacités indépendantes tournent de front, sans
    dépendance entre elles, même pour un seul rôle ; ce qu'elles partagent vient
    d'une capacité dont elles dépendent ou s'écrit dans chacune, jamais par une
    chaîne ; ce qui les réunit, quand il y a quelque chose à produire, dépend
    d'elles toutes — et n'est plus une tâche qui ne ferait que vérifier (#1530,
    `tests/test_recette_du_run.py`).

    Ce que #1531 renverse : le front se gagnait **par élément** (« maquetter les
    quatre sections du site est quatre tâches »), et sur p5 cela a fait neuf tâches
    sur quatre niveaux pour un objectif qui tient en une session. Il se gagne
    désormais entre capacités, jamais en coupant une capacité qui tient dans une
    session — le parallélisme reste dû à un objectif qui la dépasse (#1513).
    """
    texte = _playbook()

    assert "Le travail qui se fait de front" in texte
    assert "même confiées au même rôle" in texte
    assert "ne les enchaîne jamais pour qu'elles se le transmettent" in texte
    assert "elle dépend alors d'elles toutes, et elle produit" in texte
    assert "Le front se gagne entre capacités" in texte
    assert "jamais en coupant en morceaux une capacité qui tient dans une session" in texte
    # La règle renversée ne survit nulle part dans le document.
    assert "est quatre tâches" not in texte
    assert "fais une tâche par élément" not in texte
    assert "se décide dans une tâche amont dont ils dépendent tous" not in texte


def test_une_tache_est_une_capacite_a_la_taille_d_une_session():
    """Critère ① de #1531 : la chaîne du dépôt découpe ainsi (docs/10 § 5.1, #1150),
    et p5, qui tient en une session, y serait un ticket (docs/50 § 3, point 2).

    Les trois formes que C2 a prises et que la règle écarte sont nommées une à une
    — `tests-logique`, `charte-ui` et `socle-projet`, les quatre `page-*` —, parce
    qu'une règle générale sur la « bonne granularité » les couvrait déjà mal. Et un
    élément qui fait sa tâche le justifie par écrit : taille, métier ou livraison.
    """
    texte = _playbook()

    assert "Une tâche est une capacité, à la taille d'une session" in texte
    assert "capacités visibles" in texte
    assert "dans une seule session de travail" in texte
    assert "ses tests" in texte
    # Ce qu'une tâche coûte en plus de son travail : la raison de la règle.
    assert "chaque niveau du plan ajoute ce coût au chemin" in texte
    # Les trois formes écartées.
    assert "une tâche de tests seuls" in texte
    assert "une tâche amont sans livrable propre" in texte
    assert "une tâche par élément quand les éléments tiennent ensemble dans une session" in texte
    # Les trois raisons qui font une tâche d'un élément, et la justification écrite.
    assert "sa taille" in texte and "son métier" in texte and "sa livraison" in texte
    assert "écrit pourquoi dans sa description" in texte
    # La relecture du plan et les critères de « terminé » le redisent.
    assert "Relis-le contre le point 4" in texte
    assert "Chaque tâche est une capacité visible qui tient dans une session" in texte


#: Le plan de C2, relu dans le journal du run `fea601a5c629` (docs/50 § 2) : neuf
#: tâches, treize enchaînements, quatre niveaux — socle, puis charte, jeu de
#: recettes et tests seuls, puis quatre pages, puis la recette.
_PLAN_C2 = (
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
_C2_TACHES = 9
_C2_NIVEAUX = 4


def _niveaux(taches) -> int:
    """Le nombre de niveaux d'un plan, par la mesure du produit (`maestro.plan_run`)."""
    from maestro.plan_run import niveaux_topologiques, noeuds_du_plan

    return 1 + max(niveaux_topologiques(noeuds_du_plan(taches)).values(), default=-1)


def _plan_p5_en_capacites_json() -> str:
    """Un plan de **type p5** découpé en capacités : ce que le playbook demande.

    Deux capacités visibles, chacune avec ses tests : parcourir les recettes —
    socle, charte, données et les quatre pages, qui tiennent ensemble dans une
    session — puis cuisiner une recette, qui en dépend et le dit. Aucune tâche de
    tests seuls, aucune charte en amont, aucune recette au plan.
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


def test_un_plan_de_type_p5_se_planifie_en_capacites():
    """Critère ① de #1531, sur le modèle doublé : l'objectif de p5 — le message du
    banc comparatif (`maestro/scenarios/objectifs/p5.toml`), mot pour mot — part au
    modèle sous le playbook des capacités, et le plan qu'il rend traverse
    l'orchestrateur entier avec moins de tâches **et** moins de niveaux que C2.

    Le double rend ce que le playbook demande ; ce qui est éprouvé est le chemin :
    la consigne que le modèle reçoit, puis la mesure des niveaux par le produit
    (`maestro.plan_run`), la même que celle du graphe et du plafond de front.
    """
    from maestro.scenarios.comparatif import charger_objectif

    p5 = charger_objectif("p5")
    provider = FakeProvider(_plan_p5_en_capacites_json())
    orchestrator = Orchestrator(provider, model="claude-opus-4-8")

    taches = asyncio.run(orchestrator.plan(p5.message))

    appel = provider.calls[0]
    systeme = _normalise(appel["system_prompt"])
    assert "Une tâche est une capacité, à la taille d'une session" in systeme
    assert "une tâche de tests seuls" in systeme
    assert "Construis l'application entière." in appel["prompt"]
    assert len(taches) < _C2_TACHES
    assert _niveaux(taches) < _C2_NIVEAUX
    # Les tests vont avec la capacité qu'ils prouvent : aucune tâche n'est que tests.
    assert all(set(t.competences_requises) != {"tests"} for t in taches)
    # La tâche séparée dit pourquoi, dans sa description.
    assert "pour sa taille" in taches[1].description


def test_le_plan_designe_la_racine_du_projet_jamais_le_nom_de_son_dossier():
    """#1399 : la tâche `socle-nextjs` de p5 disait « Installer dans le dossier p5 »,
    « Commence par inspecter p5 » — et l'agent, qui travaille dans une copie qui ne
    porte pas ce nom, cherchait un sous-dossier `p5` qui n'existait pas.

    Le planificateur ne reçoit ni nom ni chemin, mais il sait que le répertoire de
    l'agent **est** la racine du projet (« Agir », plus haut) : c'est ainsi qu'il la
    désigne. La règle vit dans ses **entrées**, là où il apprend ce qu'il ne reçoit
    pas — et aucune réécriture du texte qu'il rend ne la remplace."""
    texte = _playbook()

    assert "est la racine du projet" in texte
    assert "désigne-le par « la racine du projet »" in texte
    assert "jamais par le nom du projet ni par celui de son dossier" in texte


def test_le_playbook_cadre_sur_l_equipe_reste_celui_du_document():
    """Garde de non-régression sur le chargeur (#1041) : le document servi à une
    équipe donnée porte les mêmes règles que celui du code, marqueurs substitués."""
    cadre = _normalise(prompt_orchestrateur())

    assert "construire, ou agir" in cadre.lower()
    assert "{{" not in cadre
