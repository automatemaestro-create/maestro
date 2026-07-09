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

from maestro.agents import DEFAULT_AGENTS
from maestro.agents.catalog import Agent
from maestro.orchestrator import Task
from maestro.providers.base import ModelProvider
from maestro.router import (
    METHODE_CLASSIFIEUR,
    METHODE_COMPETENCES,
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
    return Router(DEFAULT_AGENTS, classifier=classifier, **kwargs)


# --- Règles de compétences pures (#6) ---------------------------------------------------


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
    for agent in DEFAULT_AGENTS:
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
        if "accessibilité" in texte:
            return '{"agent": "designer", "confiance": 0.85}'
        return '{"agent": null, "confiance": 0.2}'


def test_jeu_assignation_versionne_et_varie():
    jeu = charger_jeu()  # valide chaque tâche contre la JSON Schema partagée
    assert len(jeu) >= 10  # critère d'acceptation : ≥ 10 tâches variées
    # Varié : les cinq agents du catalogue sont attendus au moins une fois…
    attendus = {cas.agent_attendu for cas in jeu}
    assert {a.nom for a in DEFAULT_AGENTS} <= attendus
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
