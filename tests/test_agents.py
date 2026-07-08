"""Tests du catalogue d'agents exécutants (ticket #6).

Vérifie les invariants dont dépend un routage déterministe : noms uniques et
compétences deux à deux disjointes (aucune compétence portée par deux agents), plus
la complétude minimale de chaque fiche (modèle + prompt système).
"""

from maestro.agents import DEFAULT_AGENTS


def test_catalogue_non_vide():
    assert DEFAULT_AGENTS


def test_noms_uniques():
    noms = [agent.nom for agent in DEFAULT_AGENTS]
    assert len(noms) == len(set(noms))


def test_competences_deux_a_deux_disjointes():
    # Aucune compétence ne doit être portée par deux agents : sinon le départage des
    # ex æquo (ordre du catalogue) deviendrait le seul arbitre, ce qu'on veut éviter.
    vues: set[str] = set()
    for agent in DEFAULT_AGENTS:
        assert not (agent.competences & vues), f"compétence dupliquée chez {agent.nom}"
        vues |= agent.competences


def test_chaque_agent_a_modele_prompt_et_competences():
    for agent in DEFAULT_AGENTS:
        assert agent.modele
        assert agent.prompt_systeme.strip()
        assert agent.competences


def test_couverture_compte_les_competences_communes():
    dev = next(agent for agent in DEFAULT_AGENTS if agent.nom == "developpeur")
    assert dev.couverture(frozenset({"backend", "api", "sql"})) == 2
    assert dev.couverture(frozenset({"sql"})) == 0
