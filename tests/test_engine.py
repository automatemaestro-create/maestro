"""Tests du moteur d'orchestration : la boucle complète (ticket #6).

Aucun appel réseau : la **planification** et l'**exécution** sont pilotées par des
`ModelProvider` factices. Couvre les deux critères d'acceptation du ticket :
① au moins 3 tâches sont assignées et exécutées par les **bons agents** (ordre des
   dépendances respecté, résultats des dépendances transmis) ;
② les résultats sont **agrégés** (RunReport : synthèse + rapport structuré).
Plus la résilience : un échec de routage est consigné sans interrompre la boucle.
"""

import asyncio
import json

from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers.base import ModelProvider


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur ou d'exécutant simple)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class RecordingProvider(ModelProvider):
    """Exécutant factice : enregistre chaque appel et renvoie un livrable unique."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return f"LIVRABLE[{model}] pour l'appel #{len(self.calls)}"


def _plan_json():
    # 3 tâches en chaîne : bdd -> developpeur -> qa (via les compétences requises).
    return json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma des tâches.",
                "competences_requises": ["sql", "schema"],
                "format_sortie": "Fichier SQL",
                "dependances": [],
            },
            {
                "id": "api-taches",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend", "api"],
                "format_sortie": "Module d'API",
                "dependances": ["schema-bdd"],
            },
            {
                "id": "tests-api",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests", "e2e"],
                "format_sortie": "Suite de tests",
                "dependances": ["api-taches"],
            },
        ],
        ensure_ascii=False,
    )


def _engine(*, exec_provider=None, plan_json=None):
    planner = ConstantProvider(plan_json or _plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(execu, orchestrator)


# --- Critère ① : ≥3 tâches assignées et exécutées par les bons agents -----------------


def test_run_assigne_et_execute_les_bons_agents():
    report = asyncio.run(_engine().run("Créer une API de gestion de tâches"))

    assert len(report.resultats) == 3
    # Ordre topologique (dépendances) → bdd, puis developpeur, puis qa.
    assert [r.agent for r in report.resultats] == ["bdd", "developpeur", "qa"]
    assert all(r.ok for r in report.resultats)


def test_run_respecte_l_ordre_des_dependances_et_transmet_les_resultats():
    exec_provider = RecordingProvider()
    asyncio.run(_engine(exec_provider=exec_provider).run("Objectif"))

    # api-taches (2e appel) reçoit le livrable de schema-bdd (1er appel)…
    assert "appel #1" in exec_provider.calls[1]["prompt"]
    # …et tests-api (3e appel) reçoit celui de api-taches (2e appel).
    assert "appel #2" in exec_provider.calls[2]["prompt"]


# --- Critère ② : les résultats sont agrégés -------------------------------------------


def test_run_agrege_les_resultats_dans_la_synthese():
    report = asyncio.run(_engine().run("Objectif"))
    synthese = report.synthese()

    assert "3/3 tâche(s) réussie(s)" in synthese
    assert "Base de données" in synthese
    assert "Développeur" in synthese
    assert "QA / Testeur" in synthese


def test_run_agrege_dans_le_rapport_structure():
    report = asyncio.run(_engine().run("Objectif"))
    data = report.to_dict()

    assert data["reussies"] == 3
    assert data["total"] == 3
    assert [r["agent"] for r in data["resultats"]] == ["bdd", "developpeur", "qa"]


# --- Résilience -----------------------------------------------------------------------


def test_echec_de_routage_consigne_sans_interrompre_la_boucle():
    plan = json.dumps(
        [
            {
                "id": "schema-bdd",
                "titre": "Schéma BDD",
                "description": "Définir le schéma.",
                "competences_requises": ["sql"],
                "format_sortie": "SQL",
                "dependances": [],
            },
            {
                "id": "strategie",
                "titre": "Stratégie produit",
                "description": "Cadrer la stratégie.",
                "competences_requises": ["planning"],  # aucun exécutant ne la couvre
                "format_sortie": "Note",
                "dependances": [],
            },
            {
                "id": "tests",
                "titre": "Tests",
                "description": "Écrire les tests.",
                "competences_requises": ["tests"],
                "format_sortie": "Suite de tests",
                "dependances": [],
            },
        ],
        ensure_ascii=False,
    )
    report = asyncio.run(_engine(plan_json=plan).run("Objectif"))

    assert len(report.echouees) == 1
    assert len(report.reussies) == 2
    echec = report.echouees[0]
    assert echec.task_id == "strategie"
    assert echec.statut == "echec"


def test_reponse_vide_de_l_agent_marque_la_tache_en_echec():
    report = asyncio.run(_engine(exec_provider=ConstantProvider("   ")).run("Objectif"))

    assert len(report.reussies) == 0
    assert all(r.statut == "echec" for r in report.resultats)
    assert all("vide" in (r.erreur or "") for r in report.resultats)
