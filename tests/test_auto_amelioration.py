"""Auto-amélioration des playbooks — analyse post-run des échecs (ticket #139).

Couvre le moteur d'analyse (`AnalyseurEchecs`, `echecs_du_run`) et l'endpoint
`POST /api/playbooks/{agent}/propositions` avec un **fournisseur factice** (aucun
réseau, aucune clé). Le scénario end-to-end complet (run réel → proposition →
application manuelle) et la documentation restent le lot #137.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.controltower.app import create_app
from maestro.controltower.auto_amelioration import (
    MARQUEUR_PLAYBOOK,
    AnalyseurEchecs,
    RevisionIndisponible,
    echecs_du_run,
)
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_TACHE_STATUT,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.state import ControlTowerState, EtatExecution
from maestro.providers.base import ModelProvider

_RATIONALE = "Le timeout revient : j'ajoute une consigne de patience."
_REPONSE = f"{_RATIONALE}\n{MARQUEUR_PLAYBOOK}\nPlaybook révisé."


class FournisseurScript(ModelProvider):
    """Fournisseur factice : enregistre chaque appel et rend une réponse scriptée."""

    name = "script"

    def __init__(self, reponse: str = _REPONSE) -> None:
        self.reponse = reponse
        self.appels: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return self.reponse


class FournisseurEnPanne(ModelProvider):
    """Fournisseur factice qui échoue à chaque appel (simule un fournisseur indisponible)."""

    name = "panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


def _agent(nom="developpeur", role="Développeur", modele="claude-sonnet-5",
           prompt="Playbook du code."):
    """Fiche catalogue factice, de quoi analyser sans charger le vrai catalogue."""
    return Agent(nom=nom, role=role, competences=frozenset({"backend"}),
                 modele=modele, prompt_systeme=prompt)


def _echec(tache_id="t1", *, agent="developpeur", titre="Écrire l'API",
           detail="Outil X en timeout.", run_id="run-1"):
    """Événement `tache.statut` au statut « echec » pour peupler une exécution."""
    return Event(type=EVENEMENT_TACHE_STATUT, run_id=run_id, tache_id=tache_id,
                 titre=titre, agent=agent, statut="echec", detail=detail)


# ------------------------------------------------------------- extraction des échecs


def test_echecs_du_run_ne_retient_que_les_echecs_de_l_agent():
    """Seuls les `tache.statut` « echec » du bon agent sortent, dans l'ordre, avec leur raison."""
    execution = EtatExecution(
        run_id="run-1",
        evenements=[
            _echec("t1", titre="Écrire l'API", detail="Outil X en timeout."),
            Event(type=EVENEMENT_TACHE_STATUT, run_id="run-1", tache_id="t2",
                  titre="Tester", agent="developpeur", statut="terminee"),  # pas un échec
            _echec("t3", agent="qa", detail="Assertion KO"),  # autre agent
            _echec("t4", titre="Migrer le schéma", detail="Colonne absente."),
            Event(type=EVENEMENT_AGENT_ACTIVITE, run_id="run-1", agent="developpeur",
                  statut="echec", detail="pas une tâche"),  # mauvais type d'événement
        ],
    )

    echecs = echecs_du_run(execution, "developpeur")

    assert [(e.tache_id, e.titre, e.raison) for e in echecs] == [
        ("t1", "Écrire l'API", "Outil X en timeout."),
        ("t4", "Migrer le schéma", "Colonne absente."),
    ]


def test_echecs_du_run_vide_quand_aucun_echec_pour_l_agent():
    execution = EtatExecution(run_id="run-1", evenements=[_echec("t1", agent="qa")])
    assert echecs_du_run(execution, "developpeur") == ()


# ------------------------------------------------------------- moteur d'analyse


def test_proposer_revision_enregistre_un_brouillon_reference_les_echecs(tmp_path):
    """Happy path : proposition « proposition » stockée à part, justification tracée à sa source."""
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    proposition = asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    # Un brouillon versionné à part, provenance « proposition », contenu = après le marqueur.
    assert proposition.provenance == "proposition"
    assert proposition.contenu == "Playbook révisé."
    assert depot.numeros_propositions("developpeur") == (1,)
    assert depot.numeros("developpeur") == ()  # jamais la version courante
    # La justification référence les échecs analysés (déterministe) puis le motif du modèle.
    assert "run-1" in proposition.justification
    assert "Outil X en timeout." in proposition.justification
    assert "j'ajoute une consigne de patience" in proposition.justification
    # L'appel modèle passe par la couche fournisseur, au modèle de l'agent, avec le cadre système.
    (appel,) = fournisseur.appels
    assert appel["model"] == "claude-sonnet-5"
    assert "expert en conception de playbooks" in appel["system_prompt"]
    assert "Playbook du code." in appel["prompt"]  # la base révisée = le playbook courant
    assert "Outil X en timeout." in appel["prompt"]


def test_proposer_revision_part_du_playbook_courant_edite(tmp_path):
    """La base révisée est la version courante éditée (#76), pas le prompt du code."""
    depot = PlaybookStore(tmp_path / "pb")
    depot.ecrire("developpeur", "Consignes éditées maison.")
    fournisseur = FournisseurScript()
    analyseur = AnalyseurEchecs(provider=fournisseur, playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    (appel,) = fournisseur.appels
    assert "Consignes éditées maison." in appel["prompt"]
    assert "Playbook du code." not in appel["prompt"]


def test_proposer_revision_sans_echec_refuse(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurScript(), playbooks=depot)
    with pytest.raises(ValueError):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", []))


@pytest.mark.parametrize(
    "reponse",
    ["Une justification sans marqueur ni playbook.", f"Justif seule.\n{MARQUEUR_PLAYBOOK}\n   "],
)
def test_proposer_revision_reponse_inexploitable_leve_et_n_ecrit_rien(tmp_path, reponse):
    """Marqueur absent ou playbook vide : `RevisionIndisponible`, aucun brouillon écrit."""
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurScript(reponse), playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    with pytest.raises(RevisionIndisponible):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))
    assert depot.numeros_propositions("developpeur") == ()


def test_proposer_revision_fournisseur_en_panne_leve_et_n_ecrit_rien(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    analyseur = AnalyseurEchecs(provider=FournisseurEnPanne(), playbooks=depot)
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    with pytest.raises(RevisionIndisponible):
        asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))
    assert depot.numeros_propositions("developpeur") == ()


def test_sans_fournisseur_resout_celui_de_la_config(tmp_path, monkeypatch):
    """Sans fournisseur injecté, l'analyseur résout paresseusement celui de la config (#69)."""
    depot = PlaybookStore(tmp_path / "pb")
    fournisseur = FournisseurScript()
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: fournisseur
    )
    analyseur = AnalyseurEchecs(playbooks=depot)  # aucun provider explicite
    echecs = echecs_du_run(EtatExecution("run-1", [_echec()]), "developpeur")

    asyncio.run(analyseur.proposer_revision(_agent(), "run-1", echecs))

    assert fournisseur.appels  # le fournisseur de la config a bien été appelé
    assert depot.numeros_propositions("developpeur") == (1,)


# ------------------------------------------------------------- endpoint (à la demande)


def _client(depot, fournisseur, *, run_id="run-1", agent="developpeur", avec_echec=True):
    """TestClient avec un état peuplé d'un run (échec optionnel) et l'analyseur injecté."""
    state = ControlTowerState()
    if avec_echec:
        state.appliquer(_echec(agent=agent, run_id=run_id))
    else:
        state.appliquer(Event(type=EVENEMENT_TACHE_STATUT, run_id=run_id, tache_id="t1",
                              titre="Écrire l'API", agent=agent, statut="terminee"))
    app = create_app(
        bus=InMemoryEventBus(),
        state=state,
        playbooks=depot,
        analyseur=AnalyseurEchecs(provider=fournisseur, playbooks=depot),
    )
    return TestClient(app)


def test_endpoint_genere_une_proposition_a_la_demande(tmp_path):
    """POST → 200, proposition rendue et retrouvée dans la liste des propositions."""
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})

        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["provenance"] == "proposition"
        assert corps["contenu"] == "Playbook révisé."
        assert "Outil X en timeout." in corps["justification"]
        # La proposition est listée à part et le playbook courant reste au défaut du code.
        listees = client.get("/api/playbooks/developpeur/propositions").json()
        assert len(listees) == 1 and listees[0]["provenance"] == "proposition"
        assert client.get("/api/playbooks/developpeur").json()["source"] == "defaut"


def test_endpoint_run_inconnu_404(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "absent"})
    assert reponse.status_code == 404
    assert depot.numeros_propositions("developpeur") == ()


def test_endpoint_run_sans_echec_pour_l_agent_422(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript(), avec_echec=False) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 422
    assert depot.numeros_propositions("developpeur") == ()


def test_endpoint_playbook_inconnu_404(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurScript()) as client:
        reponse = client.post("/api/playbooks/stagiaire/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 404


def test_endpoint_fournisseur_en_panne_502(tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    with _client(depot, FournisseurEnPanne()) as client:
        reponse = client.post("/api/playbooks/developpeur/propositions", json={"run_id": "run-1"})
    assert reponse.status_code == 502
    assert depot.numeros_propositions("developpeur") == ()
