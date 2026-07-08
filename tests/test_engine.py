"""Tests du moteur d'orchestration : la boucle complète (tickets #6 puis #35).

Aucun appel réseau : la **planification** et l'**exécution** sont pilotées par des
`ModelProvider` factices. Couvre les critères d'acceptation du ticket #6 :
① au moins 3 tâches sont assignées et exécutées par les **bons agents** (ordre des
   dépendances respecté, résultats des dépendances transmis) ;
② les résultats sont **agrégés** (RunReport : synthèse + rapport structuré) ;
et ceux du ticket #35 :
③ une tâche routée vers `developpeur`/`bdd` s'exécute via le **runtime outillé** dans
   un workspace isolé, et les fichiers produits remontent dans le RunReport
   (`to_dict()` et `synthese()`) ;
④ les rôles sans runtime outillé livrent leur texte via `generate()` — y compris en
   **repli** quand le fournisseur n'a pas d'exécution outillée.
Plus la résilience : un échec de routage est consigné sans interrompre la boucle.
"""

import asyncio
import json
from pathlib import Path

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
    """Exécutant factice texte-seul : enregistre chaque appel et renvoie un livrable unique."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.calls.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return f"LIVRABLE[{model}] pour l'appel #{len(self.calls)}"


class ToolingProvider(ModelProvider):
    """Exécutant factice complet : `run_agent` écrit des fichiers, `generate` rend du texte."""

    name = "tooling"

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files
        self.run_calls: list[dict[str, object]] = []
        self.generate_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.generate_calls.append({"prompt": prompt, "model": model})
        return f"TEXTE #{len(self.generate_calls)}"

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools):
        self.run_calls.append(
            {"prompt": prompt, "model": model, "workspace": str(workspace), "tools": tuple(tools)}
        )
        for chemin, contenu in self._files.items():
            (Path(workspace) / chemin).write_text(contenu, encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls)}"


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


# --- Critères ③/④ (#35) : routage vers les runtimes outillés --------------------------


def test_taches_dev_et_bdd_passent_par_le_runtime_outille():
    # Plan : bdd → developpeur → qa. Avec un fournisseur outillé, bdd et developpeur
    # s'exécutent via run_agent (workspace isolé) ; qa, sans runtime, via generate.
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    report = asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert len(provider.run_calls) == 2  # schema-bdd puis api-taches
    assert len(provider.generate_calls) == 1  # tests-api (qa, texte)

    # Chaque exécution outillée a reçu son propre workspace isolé, hors cwd, nettoyé.
    workspaces = [str(c["workspace"]) for c in provider.run_calls]
    assert len(set(workspaces)) == 2
    for ws in workspaces:
        assert Path(ws) != Path.cwd()
        assert not Path(ws).exists()

    # Les fichiers produits remontent sur les tâches outillées, pas sur la tâche texte.
    bdd, dev, qa = report.resultats
    assert [f.chemin for f in bdd.fichiers] == ["livrable.txt"]
    assert [f.chemin for f in dev.fichiers] == ["livrable.txt"]
    assert qa.fichiers == ()


def test_fichiers_produits_remontent_dans_le_rapport():
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    report = asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    # to_dict : les fichiers (chemin + contenu) figurent dans le rapport structuré.
    data = report.to_dict()
    assert data["resultats"][0]["fichiers"] == [
        {"chemin": "livrable.txt", "contenu": "contenu"}
    ]
    assert data["resultats"][2]["fichiers"] == []

    # synthese : les fichiers produits sont listés sous la tâche.
    synthese = report.synthese()
    assert "Fichiers produits (1) :" in synthese
    assert "`livrable.txt`" in synthese


def test_le_runtime_outille_recoit_le_tableau_noir_et_le_format():
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    # api-taches (2e exécution outillée) reçoit le livrable de schema-bdd…
    prompt_dev = str(provider.run_calls[1]["prompt"])
    assert "OUTILLE #1" in prompt_dev
    # …et le format de sortie de la tâche est transmis au runtime.
    assert "Module d'API" in prompt_dev
    # La tâche qa (texte) reçoit à son tour le compte-rendu du developpeur.
    assert "OUTILLE #2" in str(provider.generate_calls[0]["prompt"])


def test_fournisseur_texte_seul_replie_les_roles_outilles_sur_generate():
    # Critère ④ : sans exécution outillée chez le fournisseur (UnsupportedCapability),
    # developpeur et bdd livrent leur texte via generate() au lieu d'échouer.
    provider = RecordingProvider()
    report = asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert len(provider.calls) == 3  # les 3 tâches sont passées par generate
    assert all(r.fichiers == () for r in report.resultats)


def test_runtimes_injectes_remplacent_le_cablage_par_defaut():
    # `runtimes={}` désactive le chemin outillé : tout passe par generate, même avec
    # un fournisseur outillé — le câblage est injectable (tests, configurations).
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    engine = OrchestrationEngine(provider, orchestrator, runtimes={})

    report = asyncio.run(engine.run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert provider.run_calls == []
    assert len(provider.generate_calls) == 3
