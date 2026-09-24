"""Tests du routeur d'auto-assignation (tickets #6 et #42).

Couvre les deux signaux du routage et leur combinaison :

- règles de compétences pures (`assign`, #6) : meilleur recouvrement, routage par
  domaine, départage déterministe des ex æquo, cas d'erreur ;
- classifieur léger (`TaskClassifier`, #42) : prompt, parsing tolérant, abstention ;
- routage combiné (`Router.route`, #42) : cas net sans appel modèle, départage des
  ambigus par le classifieur, **repli explicite « à assigner »** (confiance faible,
  réponse illisible, classifieur absent ou en échec) ;
- évaluation sur le **jeu versionné** (critère MVP n°3) : ≥ 10 cas, précision ≥ 9/10.
"""

import asyncio

import pytest

from maestro.agents import GABARITS_DU_CODE
from maestro.agents.catalog import Agent
from maestro.orchestrator import Task
from maestro.providers.base import ModelProvider
from maestro.router import (
    CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT,
    METHODE_CLASSIFIEUR,
    METHODE_COMPETENCES,
    METHODE_PLUS_PROCHE,
    METHODE_REPLI,
    MODELE_CLASSIFIEUR,
    Router,
    RoutingError,
    TaskClassifier,
    assign,
    charger_jeu,
    evaluer,
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
    """Fournisseur factice : rejoue des réponses scriptées et enregistre les appels."""

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
    """Fournisseur factice en panne : tout appel modèle lève."""

    name = "broken"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


def _router(provider: ModelProvider | None = None, **kwargs) -> Router:
    classifier = TaskClassifier(provider) if provider is not None else None
    return Router(GABARITS_DU_CODE, classifier=classifier, **kwargs)


# --- Règles de compétences pures (#6) ---------------------------------------------------


def test_assign_choisit_le_meilleur_recouvrement():
    result = assign(_task(competences_requises=("sql", "migration")), GABARITS_DU_CODE)
    assert result.agent.nom == "bdd"
    assert result.score == 2


def test_assign_route_chaque_domaine_vers_son_agent():
    assert assign(_task(competences_requises=("ui",)), GABARITS_DU_CODE).agent.nom == "designer"
    assert assign(_task(competences_requises=("tests",)), GABARITS_DU_CODE).agent.nom == "qa"
    assert assign(_task(competences_requises=("deploy",)), GABARITS_DU_CODE).agent.nom == "devops"
    assert assign(_task(competences_requises=("api",)), GABARITS_DU_CODE).agent.nom == "developpeur"


def test_assign_sans_agent_competent_leve_routing_error():
    # 'planning' est une compétence du Chef de projet, absente des exécutants.
    with pytest.raises(RoutingError):
        assign(_task(competences_requises=("planning",)), GABARITS_DU_CODE)


def test_assign_sans_agents_leve_value_error():
    with pytest.raises(ValueError):
        assign(_task(), [])


def test_egalite_departagee_par_ordre_du_catalogue():
    a = Agent(nom="a", role="A", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    b = Agent(nom="b", role="B", competences=frozenset({"x"}), modele="m", prompt_systeme="p")
    task = _task(competences_requises=("x",))
    assert assign(task, [a, b]).agent.nom == "a"
    assert assign(task, [b, a]).agent.nom == "b"


# --- Classifieur léger : parsing de la réponse (#42) ------------------------------------


def test_parse_accepte_json_pur_et_fence():
    noms = frozenset({"devops", "bdd"})
    direct = _parse_classification('{"agent": "devops", "confiance": 0.9}', noms)
    assert (direct.agent, direct.confiance) == ("devops", 0.9)
    fence = _parse_classification('```json\n{"agent": "bdd", "confiance": 0.7}\n```', noms)
    assert (fence.agent, fence.confiance) == ("bdd", 0.7)
    prose = _parse_classification('Je choisis : {"agent": "devops", "confiance": 1}.', noms)
    assert prose.agent == "devops"


def test_parse_reponse_illisible_vaut_abstention():
    verdict = _parse_classification("je ne sais pas trop…", frozenset({"devops"}))
    assert verdict.agent is None
    assert verdict.confiance == 0.0


def test_parse_agent_hors_candidats_vaut_abstention():
    verdict = _parse_classification(
        '{"agent": "chef-de-projet", "confiance": 0.95}', frozenset({"devops", "bdd"})
    )
    assert verdict.agent is None
    assert verdict.confiance == 0.0


def test_parse_borne_la_confiance():
    verdict = _parse_classification('{"agent": "devops", "confiance": 42}', frozenset({"devops"}))
    assert verdict.confiance == 1.0


# --- Routage combiné (#42) ---------------------------------------------------------------


def test_route_cas_net_assigne_sans_appel_au_classifieur():
    provider = ScriptedProvider()
    decision = asyncio.run(
        _router(provider).route(_task(competences_requises=("sql", "migration")))
    )
    assert decision.agent is not None and decision.agent.nom == "bdd"
    assert decision.methode == METHODE_COMPETENCES
    assert decision.confiance == 1.0
    assert provider.calls == []  # règle nette : aucun coût modèle


def test_route_ex_aequo_departage_par_le_classifieur_entre_les_seuls_candidats():
    provider = ScriptedProvider('{"agent": "devops", "confiance": 0.9}')
    task = _task(
        titre="Déployer le schéma en staging",
        description="Déployer le schéma de base sur l'environnement de staging.",
        competences_requises=("deploy", "schema"),  # ex æquo devops / bdd
    )
    decision = asyncio.run(_router(provider).route(task))

    assert decision.agent is not None and decision.agent.nom == "devops"
    assert decision.methode == METHODE_CLASSIFIEUR
    assert decision.confiance == 0.9
    # Le classifieur a été consulté sur le modèle léger, avec les seuls ex æquo.
    (appel,) = provider.calls
    assert appel["model"] == MODELE_CLASSIFIEUR
    assert "devops" in appel["prompt"] and "bdd" in appel["prompt"]
    assert "designer" not in appel["prompt"]


def test_route_sans_recouvrement_interroge_tout_le_catalogue():
    provider = ScriptedProvider('{"agent": "designer", "confiance": 0.8}')
    task = _task(
        titre="Audit d'accessibilité",
        description="Vérifier contrastes et navigation clavier des écrans.",
        competences_requises=("accessibilite",),  # aucun agent ne la déclare
    )
    decision = asyncio.run(_router(provider).route(task))

    assert decision.agent is not None and decision.agent.nom == "designer"
    (appel,) = provider.calls
    for agent in GABARITS_DU_CODE:
        assert agent.nom in appel["prompt"]


def test_route_confiance_faible_marque_a_assigner():
    provider = ScriptedProvider('{"agent": "devops", "confiance": 0.3}')
    task = _task(competences_requises=("deploy", "schema"))
    decision = asyncio.run(_router(provider).route(task))

    assert decision.a_assigner
    assert decision.agent is None
    assert decision.methode == METHODE_REPLI
    assert "à assigner" in decision.raison


def test_route_reponse_illisible_marque_a_assigner():
    provider = ScriptedProvider("aucune idée")
    decision = asyncio.run(_router(provider).route(_task(competences_requises=("inconnu",))))
    assert decision.a_assigner
    assert "à assigner" in decision.raison


def test_route_sans_classifieur_ne_route_pas_au_hasard():
    # Ambigu et pas de classifieur : repli explicite, pas d'assignation arbitraire.
    decision = asyncio.run(_router().route(_task(competences_requises=("deploy", "schema"))))
    assert decision.a_assigner
    assert decision.methode == METHODE_REPLI


def test_route_classifieur_en_echec_marque_a_assigner_sans_lever():
    decision = asyncio.run(
        _router(BrokenProvider()).route(_task(competences_requises=("inconnu",)))
    )
    assert decision.a_assigner
    assert "indisponible" in decision.raison


def test_router_sans_agents_leve_value_error():
    with pytest.raises(ValueError):
        Router([])


# --- Critère MVP n°3 : ≥ 9/10 sur le jeu d'assignation versionné (#42) -------------------


class StubClassifierModel(ModelProvider):
    """Simule le modèle léger : décide depuis le seul texte du prompt, comme Haiku.

    Règles volontairement simples et transparentes — le stub ne voit que le
    prompt composé par `build_classifier_prompt`, jamais l'attendu du jeu.
    """

    name = "stub-classifieur"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        texte = prompt.lower()
        if "déployer" in texte or "staging" in texte:
            return '{"agent": "devops", "confiance": 0.9}'
        if "supervision" in texte or "alerte" in texte:
            return '{"agent": "devops", "confiance": 0.9}'
        if "accessibilité" in texte:
            return '{"agent": "designer", "confiance": 0.85}'
        if "onboarding" in texte or "wireframe" in texte:
            return '{"agent": "designer", "confiance": 0.85}'
        return '{"agent": null, "confiance": 0.2}'


def test_jeu_assignation_versionne_et_varie():
    jeu = charger_jeu()  # valide chaque tâche contre la JSON Schema partagée
    assert len(jeu) >= 10  # critère d'acceptation : ≥ 10 tâches variées
    # Varié : les cinq agents du catalogue sont attendus au moins une fois…
    attendus = {cas.agent_attendu for cas in jeu}
    assert {a.nom for a in GABARITS_DU_CODE} <= attendus
    # …et le jeu contient au moins un cas de repli « à assigner ».
    assert None in attendus


def test_routage_atteint_9_sur_10_sur_le_jeu():
    resultat = asyncio.run(evaluer(_router(StubClassifierModel())))
    assert resultat.precision >= 0.9, resultat.resume()  # critère MVP n°3


def test_le_cas_hors_perimetre_finit_a_assigner_plutot_que_mal_route():
    resultat = asyncio.run(evaluer(_router(StubClassifierModel())))
    replis = [d for d in resultat.details if d.cas.agent_attendu is None]
    assert replis, "le jeu doit contenir un cas de repli"
    for detail in replis:
        assert detail.decision.a_assigner
        assert "à assigner" in detail.decision.raison


# --- Une équipe vide n'est pas une omission (#1042) -------------------------------------


def test_une_equipe_vide_laisse_la_tache_a_assigner():
    """Le pivot de #1042 côté routage : un projet sans agent ne route rien.

    C'est ce qui rend « un projet naît sans agent » observable au lieu d'être
    silencieusement rattrapé : avant, `()` valait omission et la tâche partait
    aux cinq rôles du code. Et la cause est nommée — on ne corrige pas une équipe
    à recruter comme on réactive un agent désactivé.
    """
    decision = asyncio.run(_router().route(_task(), agents=()))

    assert decision.a_assigner
    assert "à recruter" in decision.raison


def test_aucune_equipe_donnee_retombe_sur_le_catalogue_du_cablage():
    """`None` reste l'omission : une tâche hors projet se route comme avant."""
    decision = asyncio.run(_router().route(_task(), agents=None))

    assert decision.agent is not None and decision.agent.nom == "bdd"


# --- Au plus proche : l'équipe reste telle qu'elle est (#1260) ------------------------
#
# Le repli « à assigner » cède sur une seule décision : faire le travail avec
# l'équipe actuelle, quand le métier manque et que personne ne l'a recruté. Ces
# cas éprouvent le routeur seul ; le chemin complet (boucle, exécuteur) vit dans
# `tests/test_equipe_au_plan.py`.


def _equipe_sans_designer() -> tuple[Agent, ...]:
    """Deux rôles en place, aucun ne couvre `ui` — le cas de l'essai, élargi."""
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


def test_sans_la_decision_une_tache_que_personne_ne_couvre_reste_a_assigner():
    """Le témoin de #42 : sans `au_plus_proche`, le classifieur qui s'abstient
    laisse la tâche à assigner — rien de ce lot ne change le routage ordinaire."""
    provider = ScriptedProvider('{"agent": null, "confiance": 0.1}')
    router = Router(_equipe_sans_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(router.route(_task(competences_requises=("ui",))))

    assert decision.a_assigner
    assert decision.methode == METHODE_REPLI
    assert decision.non_couvertes == ("ui",)


def test_au_plus_proche_pose_la_question_qui_n_admet_pas_l_abstention():
    """Plusieurs candidats : le modèle désigne, à la seconde question — jamais
    l'ordre du catalogue quand il a répondu."""
    provider = ScriptedProvider('{"agent": "redaction", "confiance": 0.3}')
    router = Router(_equipe_sans_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), au_plus_proche=True)
    )

    assert decision.agent is not None and decision.agent.nom == "redaction"
    assert decision.methode == METHODE_PLUS_PROCHE
    assert decision.non_couvertes == ("ui",)
    assert provider.calls[0]["system_prompt"] == CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT
    assert "classifieur désigne redaction" in decision.raison


def test_au_plus_proche_un_seul_candidat_ne_demande_rien_au_modele():
    """L'essai : un projet qui n'a qu'un développeur. Il n'y a rien à départager."""
    provider = ScriptedProvider()
    router = Router(_equipe_sans_designer()[:1], classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), au_plus_proche=True)
    )

    assert decision.agent is not None and decision.agent.nom == "dev"
    assert provider.calls == []
    assert "seul rôle en place" in decision.raison


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(None, id="sans-classifieur"),
        pytest.param(BrokenProvider(), id="classifieur-en-panne"),
        pytest.param(
            ScriptedProvider('{"agent": "inconnu", "confiance": 0.9}'), id="hors-candidats"
        ),
    ],
)
def test_au_plus_proche_sans_designation_l_ordre_departage_et_le_dit(provider):
    """Ce qui a été décidé ne fait pas échouer la tâche pour une panne : l'ordre
    du catalogue départage, et la raison dit que ce n'est pas un jugement."""
    router = Router(
        _equipe_sans_designer(),
        classifier=TaskClassifier(provider) if provider is not None else None,
    )

    decision = asyncio.run(
        router.route(_task(competences_requises=("ui",)), au_plus_proche=True)
    )

    assert decision.agent is not None and decision.agent.nom == "dev"
    assert "n'a désigné personne" in decision.raison


def test_au_plus_proche_ne_touche_pas_une_competence_couverte():
    """Une tâche que quelqu'un couvre reste à la règle de compétences."""
    provider = ScriptedProvider()
    router = Router(_equipe_sans_designer(), classifier=TaskClassifier(provider))

    decision = asyncio.run(
        router.route(_task(competences_requises=("documentation",)), au_plus_proche=True)
    )

    assert decision.agent is not None and decision.agent.nom == "redaction"
    assert decision.methode == METHODE_COMPETENCES
    assert provider.calls == []


def _equipe_completee() -> tuple[Agent, ...]:
    """Le développeur de l'essai, puis le designer recruté pour le plan."""
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


def test_le_role_recrute_prend_la_tache_qui_demandait_son_metier():
    """Run `2f7aae8437f4` : une tâche `ui` + `frontend` + `api` — le développeur en
    couvre deux, le designer recruté une. Sans `recrutees`, le développeur gagne ;
    avec, c'est le rôle recruté pour ce plan, comme le fil l'a promis."""
    tache = _task(competences_requises=("ui", "frontend", "api"))
    router = Router(_equipe_completee())

    sans = asyncio.run(router.route(tache))
    avec = asyncio.run(router.route(tache, recrutees={"ui"}))

    assert sans.agent is not None and sans.agent.nom == "dev"
    assert avec.agent is not None and avec.agent.nom == "interface"
    assert avec.methode == METHODE_COMPETENCES


def test_les_competences_recrutees_ne_touchent_pas_une_tache_qui_ne_les_demande_pas():
    tache = _task(competences_requises=("backend",))

    decision = asyncio.run(Router(_equipe_completee()).route(tache, recrutees={"ui"}))

    assert decision.agent is not None and decision.agent.nom == "dev"


def test_un_role_recrute_desactive_rend_la_main_a_la_regle_ordinaire():
    """Personne ne couvre plus le métier recruté : la règle ordinaire reprend, sans
    router vers un désactivé ni replier une tâche que le développeur peut prendre."""
    tache = _task(competences_requises=("ui", "frontend"))

    decision = asyncio.run(
        Router(_equipe_completee()).route(tache, exclus={"interface"}, recrutees={"ui"})
    )

    assert decision.agent is not None and decision.agent.nom == "dev"


def test_au_plus_proche_ne_fabrique_personne_quand_tous_sont_desactives():
    """Personne de proche n'est personne : un catalogue tout désactivé reste à assigner."""
    router = Router(_equipe_sans_designer())

    decision = asyncio.run(
        router.route(
            _task(competences_requises=("ui",)),
            exclus={"dev", "redaction"},
            au_plus_proche=True,
        )
    )

    assert decision.a_assigner
