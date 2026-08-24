"""Tests du moteur d'orchestration : la boucle complète (tickets #6 puis #35).

Aucun appel réseau : la **planification** et l'**exécution** sont pilotées par des
`ModelProvider` factices. Couvre les critères d'acceptation du ticket #6 :
① au moins 3 tâches sont assignées et exécutées par les **bons agents** (ordre des
   dépendances respecté, résultats des dépendances transmis) ;
② les résultats sont **agrégés** (RunReport : synthèse + rapport structuré) ;
et ceux du ticket #35 (étendus au QA par #45, au DevOps par #67, au Designer par #68) :
③ une tâche routée vers `developpeur`/`bdd`/`qa`/`devops`/`designer` s'exécute via le
   **runtime outillé** dans un workspace isolé, et les fichiers produits remontent dans
   le RunReport (`to_dict()` et `synthese()`) ;
④ les rôles sans runtime outillé livrent leur texte via `generate()` — y compris en
   **repli** quand le fournisseur n'a pas d'exécution outillée ;
et ceux du ticket #8 :
⑤ le **coût par tâche** (tokens, coût, durée) est visible (TaskResult, synthèse,
   rapport structuré) et traçable (journal JSON par étape, relié par le run_id) ;
⑥ les logs ne contiennent **aucun secret** ;
et ceux du ticket #7 :
⑦ au moins 2 agents s'exécutent **simultanément** sans collision (rendez-vous forcé :
   séquentiel ⇒ échec net) — y compris en aval d'une dépendance commune ;
⑧ les résultats parallèles sont **correctement rassemblés** : ordre du plan dans le
   rapport, livrable et usage attribués à la bonne tâche, sans fuite entre contextes ;
et ceux du ticket #43 (DAG) :
⑨ une tâche n'est **transmise à l'exécuteur** (donc mise en file, en distribué) que
   lorsque toutes ses dépendances sont terminées avec succès ;
⑩ un **échec de dépendance bloque** les tâches aval : statut explicite `bloquee`,
   aucune exécution orpheline, cause citée, blocage propagé en cascade ;
⑪ un **cycle de dépendances** est rejeté à la validation du plan (avant toute
   exécution).
Plus la résilience : un échec de routage est consigné sans interrompre la boucle.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from maestro.agents import default_runtimes
from maestro.engine import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
    OrchestrationEngine,
    TaskExecutor,
    TaskResult,
)
from maestro.orchestrator import Orchestrator, TaskValidationError
from maestro.providers.base import ModelProvider
from maestro.telemetry import (
    LOGGER_NAME,
    MARQUEUR_SECRET,
    RunJournal,
    StepUsage,
    report_usage,
)


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

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_activite=None, on_etapes=None,
        plafond_tours=None, projet=None,
    ):
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


def _engine(*, exec_provider=None, plan_json=None, max_parallele=None):
    planner = ConstantProvider(plan_json or _plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    execu = exec_provider if exec_provider is not None else RecordingProvider()
    return OrchestrationEngine(execu, orchestrator, max_parallele=max_parallele)


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
    # Repli explicite (#42) : la tâche est marquée « à assigner », pas mal routée
    # (le classifieur, servi par le fournisseur factice, ne rend rien d'exploitable).
    assert echec.role == "à assigner"
    assert "à assigner" in (echec.erreur or "")


def test_reponse_vide_de_l_agent_marque_la_tache_en_echec():
    # Plan en chaîne : seule la racine s'exécute (et échoue — réponse vide) ; ses
    # tâches aval sont bloquées sans exécution (#43), pas marquées « echec ».
    report = asyncio.run(_engine(exec_provider=ConstantProvider("   ")).run("Objectif"))

    assert len(report.reussies) == 0
    racine, *aval = report.resultats
    assert racine.statut == STATUT_ECHEC
    assert "vide" in (racine.erreur or "")
    assert all(r.statut == STATUT_BLOQUEE for r in aval)


# --- Critères ③/④ (#35, QA outillé par #45) : routage vers les runtimes outillés ------


def test_taches_dev_bdd_et_qa_passent_par_le_runtime_outille():
    # Plan : bdd → developpeur → qa. Avec un fournisseur outillé, les trois rôles
    # s'exécutent via run_agent (workspace isolé) — le QA compris (#45) : il exécute
    # sa tâche de bout en bout dans une exécution d'orchestration complète.
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    report = asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    assert len(provider.run_calls) == 3  # schema-bdd, api-taches, tests-api
    assert provider.generate_calls == []

    # Chaque exécution outillée a reçu son propre workspace isolé, hors cwd, nettoyé.
    workspaces = [str(c["workspace"]) for c in provider.run_calls]
    assert len(set(workspaces)) == 3
    for ws in workspaces:
        assert Path(ws) != Path.cwd()
        assert not Path(ws).exists()

    # Les fichiers produits remontent sur chacune des tâches outillées.
    for resultat in report.resultats:
        assert [f.chemin for f in resultat.fichiers] == ["livrable.txt"]


def test_fichiers_produits_remontent_dans_le_rapport():
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    report = asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    # to_dict : les fichiers (chemin + contenu) figurent dans le rapport structuré,
    # pour chaque tâche outillée (qa compris, #45).
    data = report.to_dict()
    for resultat in data["resultats"]:
        assert resultat["fichiers"] == [{"chemin": "livrable.txt", "contenu": "contenu"}]

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
    # La tâche qa (outillée elle aussi, #45) reçoit à son tour le compte-rendu du
    # developpeur sur son tableau noir : la matière de sa revue.
    prompt_qa = str(provider.run_calls[2]["prompt"])
    assert "OUTILLE #2" in prompt_qa
    assert "Suite de tests" in prompt_qa


def test_tache_devops_s_execute_de_bout_en_bout_via_le_runtime_outille():
    # Critère ③ étendu par #67 : une tâche routée vers `devops` (compétences de son
    # domaine) s'exécute via le runtime outillé dans un workspace isolé, et son
    # livrable (fichiers produits) remonte dans le RunReport — résultat exploitable.
    plan = json.dumps(
        [
            {
                "id": "pipeline-ci",
                "titre": "Pipeline CI",
                "description": "Mettre en place le pipeline de lint et de tests.",
                "competences_requises": ["ci-cd", "docker"],
                "format_sortie": "Configuration CI",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    provider = ToolingProvider(files={".gitlab-ci.yml": "stages: [lint, test]"})
    report = asyncio.run(_engine(exec_provider=provider, plan_json=plan).run("Objectif"))

    (resultat,) = report.resultats
    assert resultat.ok
    assert resultat.agent == "devops"
    assert provider.generate_calls == []

    # Exécution outillée dans un workspace isolé (hors cwd), nettoyé après capture,
    # avec le format de sortie de la tâche transmis au runtime.
    (appel,) = provider.run_calls
    ws = Path(str(appel["workspace"]))
    assert ws != Path.cwd()
    assert not ws.exists()
    assert "Configuration CI" in str(appel["prompt"])

    # Le livrable remonte dans le rapport : fichiers produits + synthèse exploitable.
    assert [f.chemin for f in resultat.fichiers] == [".gitlab-ci.yml"]
    data = report.to_dict()
    assert data["resultats"][0]["fichiers"] == [
        {"chemin": ".gitlab-ci.yml", "contenu": "stages: [lint, test]"}
    ]
    assert ".gitlab-ci.yml" in report.synthese()


def test_tache_designer_s_execute_de_bout_en_bout_via_le_runtime_outille():
    # Critère ③ étendu par #68 : une tâche routée vers `designer` (compétences de son
    # domaine) s'exécute via le runtime outillé dans un workspace isolé, et son
    # livrable (fichiers produits) remonte dans le RunReport — résultat exploitable.
    plan = json.dumps(
        [
            {
                "id": "maquette-connexion",
                "titre": "Maquette de l'écran de connexion",
                "description": "Concevoir la maquette de l'écran de connexion selon la charte.",
                "competences_requises": ["ui", "figma"],
                "format_sortie": "Spec d'écran",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    provider = ToolingProvider(files={"ecran-connexion.md": "# Écran de connexion"})
    report = asyncio.run(_engine(exec_provider=provider, plan_json=plan).run("Objectif"))

    (resultat,) = report.resultats
    assert resultat.ok
    assert resultat.agent == "designer"
    assert provider.generate_calls == []

    # Exécution outillée dans un workspace isolé (hors cwd), nettoyé après capture,
    # avec le format de sortie de la tâche transmis au runtime.
    (appel,) = provider.run_calls
    ws = Path(str(appel["workspace"]))
    assert ws != Path.cwd()
    assert not ws.exists()
    assert "Spec d'écran" in str(appel["prompt"])

    # Le livrable remonte dans le rapport : fichiers produits + synthèse exploitable.
    assert [f.chemin for f in resultat.fichiers] == ["ecran-connexion.md"]
    data = report.to_dict()
    assert data["resultats"][0]["fichiers"] == [
        {"chemin": "ecran-connexion.md", "contenu": "# Écran de connexion"}
    ]
    assert "ecran-connexion.md" in report.synthese()


def test_role_sans_runtime_livre_son_texte_meme_avec_fournisseur_outille():
    # Critère ④ : un rôle sans runtime outillé reste sur le chemin texte (generate)
    # même quand le fournisseur sait exécuter des agents outillés. Les cinq rôles du
    # catalogue étant tous outillés depuis #68, on matérialise le cas en retirant le
    # designer du câblage — le comportement qu'aurait tout agent ajouté au catalogue
    # avant de recevoir son profil.
    plan = json.dumps(
        [
            {
                "id": "maquette",
                "titre": "Maquette de l'écran",
                "description": "Proposer la maquette.",
                "competences_requises": ["ui"],
                "format_sortie": "Spec d'écran",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    provider = ToolingProvider(files={"livrable.txt": "contenu"})
    runtimes = default_runtimes(provider)
    del runtimes["designer"]
    orchestrator = Orchestrator(ConstantProvider(plan), model="claude-opus-4-8")
    engine = OrchestrationEngine(provider, orchestrator, runtimes=runtimes)

    report = asyncio.run(engine.run("Objectif"))

    (resultat,) = report.resultats
    assert resultat.ok
    assert resultat.agent == "designer"
    assert provider.run_calls == []
    assert len(provider.generate_calls) == 1
    assert resultat.fichiers == ()


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


# --- Critères ⑤/⑥ (#8) : coût par tâche visible et traçable, logs sans secret ----------


class MeteredProvider(ModelProvider):
    """Exécutant texte factice qui signale l'usage de chaque appel (ticket #8)."""

    name = "metered"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        report_usage(
            StepUsage(
                appels=1, tokens_entree=100, tokens_sortie=20,
                cout_usd=0.01, duree_api_ms=50, tours=1,
            )
        )
        return "LIVRABLE mesuré"


def test_le_cout_par_tache_est_visible_dans_le_rapport():
    report = asyncio.run(_engine(exec_provider=MeteredProvider()).run("Objectif"))

    assert report.run_id  # l'exécution est traçable dans le journal via ce run_id
    for r in report.resultats:
        assert r.usage.appels == 1
        assert r.usage.tokens_total == 120
        assert r.usage.cout_usd == pytest.approx(0.01)
        assert r.usage.duree_ms is not None  # durée horloge toujours mesurée

    # Le total agrège les 3 tâches (le planificateur factice ne rapporte rien).
    assert report.usage_totale.cout_usd == pytest.approx(0.03)

    # Visible dans le rapport structuré…
    data = report.to_dict()
    assert data["run_id"] == report.run_id
    assert data["resultats"][0]["usage"]["cout_usd"] == 0.01
    assert data["usage_totale"]["cout_usd"] == pytest.approx(0.03)

    # …comme dans la synthèse Markdown (par tâche et en total).
    synthese = report.synthese()
    # Le libellé nomme les deux étapes de run comptées dans le total depuis #320 :
    # le cadrage (brief) autant que la planification.
    assert "Usage total (cadrage et planification inclus)" in synthese
    assert synthese.count("coût 0.0100 $") == 3


def test_chaque_etape_est_consignee_dans_le_journal():
    journal = RunJournal(run_id="run-42")
    report = asyncio.run(
        _engine(exec_provider=MeteredProvider()).run("Objectif", journal=journal)
    )

    # Une trace par étape — la planification d'abord, puis chaque tâche dans
    # l'ordre : son début (#98) puis son issue.
    assert [r.etape for r in journal.records] == [
        "planification",
        "schema-bdd:debut", "schema-bdd",
        "api-taches:debut", "api-taches",
        "tests-api:debut", "tests-api",
    ]
    assert all(r.run_id == "run-42" for r in journal.records)
    assert report.run_id == "run-42"

    # Chaque trace porte l'entrée, la sortie et l'usage de l'étape.
    tache = journal.records[2]
    assert "Schéma BDD" in tache.entree
    assert tache.sortie == "LIVRABLE mesuré"
    assert tache.usage.cout_usd == pytest.approx(0.01)


def test_le_debut_de_chaque_tache_est_consigne_avant_son_issue():
    """Kanban vivant (#98) : chaque tâche ouvre par une étape `<id>:debut` —
    statut `en_cours`, agent élu, usage nul — la matière de la colonne
    « En cours » de la Control Tower (agent, heure de début)."""
    journal = RunJournal(run_id="run-98")
    asyncio.run(_engine().run("Objectif", journal=journal))

    debut = next(r for r in journal.records if r.etape == "schema-bdd:debut")
    issue = next(r for r in journal.records if r.etape == "schema-bdd")
    assert debut.statut == STATUT_EN_COURS
    assert debut.nom == "Schéma BDD"
    # L'agent et le rôle sont ceux de l'issue : le routage a déjà tranché.
    assert debut.agent and (debut.agent, debut.role) == (issue.agent, issue.role)
    assert "démarrage" in debut.sortie
    # Usage nul : rien n'entre au grand livre avant l'issue de la tâche.
    assert debut.usage == StepUsage()
    assert debut.horodatage


def test_l_echec_de_routage_est_consigne_au_journal_avec_sa_duree():
    plan = json.dumps(
        [
            {
                "id": "strategie",
                "titre": "Stratégie produit",
                "description": "Cadrer la stratégie.",
                "competences_requises": ["planning"],  # aucun exécutant ne la couvre
                "format_sortie": "Note",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )
    journal = RunJournal()
    asyncio.run(_engine(plan_json=plan).run("Objectif", journal=journal))

    trace = journal.records[-1]
    assert trace.etape == "strategie"
    assert trace.statut == "echec"
    assert trace.erreur
    assert trace.usage.duree_ms is not None


# --- Critères ⑦/⑧ (#7) : exécution parallèle des tâches indépendantes ------------------


def _plan_paralleles_json():
    # 2 tâches **sans dépendance**, routées vers deux agents distincts (bdd, qa).
    return json.dumps(
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
                "id": "tests-api",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests"],
                "format_sortie": "Suite de tests",
                "dependances": [],
            },
        ],
        ensure_ascii=False,
    )


class RendezvousProvider(ModelProvider):
    """Exécutant qui force la simultanéité : chaque appel attend `attendus` appels en vol.

    En exécution séquentielle, le premier appel attendrait seul indéfiniment : le
    timeout borne l'attente et transforme une régression (retour au séquentiel) en
    échec de tâche net plutôt qu'en suite de tests suspendue. `usage`, si fourni,
    est signalé par chaque appel *pendant* la simultanéité — pour vérifier que les
    collecteurs de contextes concurrents ne se contaminent pas.
    """

    name = "rendezvous"

    def __init__(self, attendus: int, *, usage: StepUsage | None = None) -> None:
        self._attendus = attendus
        self._usage = usage
        self._en_vol = 0
        self._complet = asyncio.Event()
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        if self._en_vol >= self._attendus:
            self._complet.set()
        await asyncio.wait_for(self._complet.wait(), timeout=5)
        if self._usage is not None:
            report_usage(self._usage)
        self._en_vol -= 1
        return f"LIVRABLE[{model}]"


def test_deux_agents_s_executent_simultanement_sans_collision():
    provider = RendezvousProvider(attendus=2)
    report = asyncio.run(
        _engine(exec_provider=provider, plan_json=_plan_paralleles_json()).run("Objectif")
    )

    # Le rendez-vous n'aboutit que si les deux appels sont en vol en même temps.
    assert provider.pic >= 2
    assert all(r.ok for r in report.resultats)
    # Deux agents distincts ont travaillé simultanément, chacun sur sa tâche.
    assert {r.agent for r in report.resultats} == {"bdd", "qa"}


class TraceurProvider(ModelProvider):
    """Livrable propre à chaque tâche ; la 1re du plan finit volontairement après la 2e."""

    name = "traceur"

    def __init__(self) -> None:
        self.acheves: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if "Schéma BDD" in prompt:
            await asyncio.sleep(0.05)  # ne finit après tests-api que si l'exécution est parallèle
            cle = "schema-bdd"
        else:
            cle = "tests-api"
        self.acheves.append(cle)
        return f"LIVRABLE {cle}"


def test_les_resultats_paralleles_sont_rassembles_dans_l_ordre_du_plan():
    provider = TraceurProvider()
    journal = RunJournal()
    report = asyncio.run(
        _engine(exec_provider=provider, plan_json=_plan_paralleles_json()).run(
            "Objectif", journal=journal
        )
    )

    # tests-api finit la première (schema-bdd temporise) : achèvement inversé…
    assert provider.acheves == ["tests-api", "schema-bdd"]
    # …mais le rapport rassemble chaque livrable à sa place, dans l'ordre du plan.
    assert [r.task_id for r in report.resultats] == ["schema-bdd", "tests-api"]
    assert [r.sortie for r in report.resultats] == [
        "LIVRABLE schema-bdd",
        "LIVRABLE tests-api",
    ]
    # Le journal, lui, consigne dans l'ordre d'achèvement, chaque trace reliée
    # par `etape` — et chaque début (#98) précède l'issue de sa tâche (l'ordre
    # relatif des débuts entre tâches parallèles, lui, n'est pas garanti).
    etapes = [rec.etape for rec in journal.records]
    assert [e for e in etapes if not e.endswith(":debut")] == [
        "planification",
        "tests-api",
        "schema-bdd",
    ]
    for tache_id in ("schema-bdd", "tests-api"):
        assert etapes.index(f"{tache_id}:debut") < etapes.index(tache_id)


class DiamantProvider(ModelProvider):
    """Le socle (sans dépendance) répond immédiatement ; ses deux dépendantes se donnent
    rendez-vous — prouve que le parallélisme opère aussi *en aval* d'une dépendance."""

    name = "diamant"

    def __init__(self) -> None:
        self._en_vol = 0
        self._complet = asyncio.Event()
        self.pic = 0
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts.append(prompt)
        if "Résultats des tâches" not in prompt:  # le socle : seul prompt sans tableau noir
            return "SOCLE POSÉ"
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        if self._en_vol >= 2:
            self._complet.set()
        await asyncio.wait_for(self._complet.wait(), timeout=5)
        self._en_vol -= 1
        return "DÉPENDANTE FAITE"


def test_les_dependances_restent_respectees_en_parallele():
    plan = json.dumps(
        [
            {
                "id": "socle",
                "titre": "Socle BDD",
                "description": "Définir le schéma.",
                "competences_requises": ["sql"],
                "format_sortie": "SQL",
                "dependances": [],
            },
            {
                "id": "api",
                "titre": "API des tâches",
                "description": "Endpoints CRUD.",
                "competences_requises": ["backend"],
                "format_sortie": "Module d'API",
                "dependances": ["socle"],
            },
            {
                "id": "tests",
                "titre": "Tests de l'API",
                "description": "Tests d'intégration.",
                "competences_requises": ["tests"],
                "format_sortie": "Suite de tests",
                "dependances": ["socle"],
            },
        ],
        ensure_ascii=False,
    )
    provider = DiamantProvider()
    report = asyncio.run(_engine(exec_provider=provider, plan_json=plan).run("Objectif"))

    assert all(r.ok for r in report.resultats)
    # Les deux dépendantes ont tourné en même temps, après le socle…
    assert provider.pic == 2
    # …et chacune a bien reçu le livrable du socle sur son tableau noir.
    dependants = [p for p in provider.prompts if "Résultats des tâches" in p]
    assert len(dependants) == 2
    assert all("SOCLE POSÉ" in p for p in dependants)


def test_pas_de_fuite_d_usage_entre_executions_paralleles():
    # Chaque appel signale son usage *pendant* la simultanéité : si les collecteurs
    # de contexte fuyaient entre tâches asyncio, une tâche compterait l'usage des deux.
    usage = StepUsage(appels=1, tokens_entree=100, tokens_sortie=20, cout_usd=0.01)
    provider = RendezvousProvider(attendus=2, usage=usage)
    report = asyncio.run(
        _engine(exec_provider=provider, plan_json=_plan_paralleles_json()).run("Objectif")
    )

    assert provider.pic >= 2
    for r in report.resultats:
        assert r.usage.appels == 1
        assert r.usage.tokens_total == 120
        assert r.usage.cout_usd == pytest.approx(0.01)
    assert report.usage_totale.cout_usd == pytest.approx(0.02)


class PicProvider(ModelProvider):
    """Mesure le pic d'appels simultanés, chaque appel cédant brièvement la main."""

    name = "pic"

    def __init__(self) -> None:
        self._en_vol = 0
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        await asyncio.sleep(0.01)
        self._en_vol -= 1
        return "LIVRABLE"


def test_max_parallele_borne_le_nombre_d_executions_simultanees():
    provider = PicProvider()
    report = asyncio.run(
        _engine(
            exec_provider=provider, plan_json=_plan_paralleles_json(), max_parallele=1
        ).run("Objectif")
    )

    assert all(r.ok for r in report.resultats)
    assert provider.pic == 1  # jamais 2 en vol malgré des tâches indépendantes


def test_max_parallele_invalide_est_refuse():
    with pytest.raises(ValueError):
        _engine(max_parallele=0)


def test_le_journal_n_expose_aucun_secret(monkeypatch, caplog):
    # Critère ⑥ : un secret présent dans l'environnement (et recraché par un
    # livrable) n'atteint jamais les lignes du journal.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-api-ultra-secrete")
    provider = ConstantProvider("Voici cle-api-ultra-secrete et sk-ant-api03-abcdef12345")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        asyncio.run(_engine(exec_provider=provider).run("Objectif"))

    assert caplog.messages  # le journal a bien émis des lignes
    assert "cle-api-ultra-secrete" not in caplog.text
    assert "sk-ant-api03-abcdef12345" not in caplog.text
    assert MARQUEUR_SECRET in caplog.text


# --- Critères ⑨/⑩/⑪ (#43) : DAG — transmission conditionnée, blocage aval, cycles -----


class ScriptedExecutor(TaskExecutor):
    """Exécuteur factice : issue scriptée par tâche, appels enregistrés.

    C'est la frontière même du #41 (exécution locale ou mise en file) : ce que la
    boucle ne lui transmet pas n'est ni exécuté ni mis en file. `executees` trace
    les tâches transmises (dans l'ordre), `dependances_recues` le tableau noir vu
    par chacune.
    """

    def __init__(self, echecs: frozenset[str] = frozenset()) -> None:
        self._echecs = echecs
        self.executees: list[str] = []
        self.dependances_recues: dict[str, tuple[TaskResult, ...]] = {}

    async def execute(self, task, dependances, journal):
        self.executees.append(task.id)
        self.dependances_recues[task.id] = tuple(dependances)
        en_echec = task.id in self._echecs
        return TaskResult(
            task_id=task.id,
            titre=task.titre,
            agent="factice",
            role="Factice",
            competences_requises=task.competences_requises,
            score=1,
            statut=STATUT_ECHEC if en_echec else STATUT_TERMINEE,
            sortie="" if en_echec else f"LIVRABLE {task.id}",
            erreur="panne scriptée" if en_echec else None,
        )


def _engine_scripte(plan_json: str, *, echecs: frozenset[str] = frozenset()):
    """Boucle branchée sur un `ScriptedExecutor` (la planification reste factice)."""
    planner = ConstantProvider(plan_json)
    executor = ScriptedExecutor(echecs)
    engine = OrchestrationEngine(
        planner, Orchestrator(planner, model="claude-opus-4-8"), executor=executor
    )
    return engine, executor


def _tache_dag(id: str, *, dependances: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "id": id,
        "titre": f"Tâche {id}",
        "description": f"Réaliser la tâche {id}.",
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _plan_diamant_json() -> str:
    # socle ─┬─> gauche ─┬─> pointe (gauche échouera dans les tests de blocage)
    #        └─> droite ──┘
    return json.dumps(
        [
            _tache_dag("socle"),
            _tache_dag("gauche", dependances=("socle",)),
            _tache_dag("droite", dependances=("socle",)),
            _tache_dag("pointe", dependances=("gauche", "droite")),
        ],
        ensure_ascii=False,
    )


def test_une_tache_n_est_transmise_qu_apres_ses_dependances_terminees():
    # Critère ⑨ : quand une tâche atteint l'exécuteur (= la mise en file, en
    # distribué), toutes ses dépendances sont déjà terminées avec succès.
    engine, executor = _engine_scripte(_plan_diamant_json())
    report = asyncio.run(engine.run("Objectif"))

    assert all(r.ok for r in report.resultats)
    ordre = executor.executees
    assert ordre.index("socle") < ordre.index("gauche")
    assert ordre.index("socle") < ordre.index("droite")
    assert ordre.index("pointe") == 3
    for deps in executor.dependances_recues.values():
        assert all(d.statut == STATUT_TERMINEE for d in deps)
    assert {d.task_id for d in executor.dependances_recues["pointe"]} == {
        "gauche",
        "droite",
    }


def test_un_echec_de_dependance_bloque_l_aval_sans_execution_orpheline():
    # Critère ⑩ : `gauche` échoue → `pointe` n'atteint jamais l'exécuteur et porte
    # un statut explicite ; `droite`, indépendante de l'échec, s'exécute normalement.
    engine, executor = _engine_scripte(_plan_diamant_json(), echecs=frozenset({"gauche"}))
    report = asyncio.run(engine.run("Objectif"))

    assert sorted(executor.executees) == ["droite", "gauche", "socle"]  # jamais « pointe »
    par_id = {r.task_id: r for r in report.resultats}
    assert par_id["droite"].ok
    assert par_id["gauche"].statut == STATUT_ECHEC
    pointe = par_id["pointe"]
    assert pointe.statut == STATUT_BLOQUEE
    # La cause est citée : la dépendance en échec, et elle seule.
    assert "gauche (echec)" in (pointe.erreur or "")
    assert "droite" not in (pointe.erreur or "")
    assert report.echouees == (par_id["gauche"],)
    assert report.bloquees == (pointe,)


def test_le_blocage_se_propage_en_cascade():
    # racine (échec) → milieu (bloquée, cause : racine) → feuille (bloquée, cause :
    # milieu — le blocage hérité est lui-même une dépendance non satisfaite).
    plan = json.dumps(
        [
            _tache_dag("racine"),
            _tache_dag("milieu", dependances=("racine",)),
            _tache_dag("feuille", dependances=("milieu",)),
        ],
        ensure_ascii=False,
    )
    engine, executor = _engine_scripte(plan, echecs=frozenset({"racine"}))
    report = asyncio.run(engine.run("Objectif"))

    assert executor.executees == ["racine"]
    par_id = {r.task_id: r for r in report.resultats}
    assert par_id["milieu"].statut == STATUT_BLOQUEE
    assert "racine (echec)" in (par_id["milieu"].erreur or "")
    assert par_id["feuille"].statut == STATUT_BLOQUEE
    assert "milieu (bloquee)" in (par_id["feuille"].erreur or "")


def test_le_blocage_est_consigne_au_journal_et_visible_dans_le_rapport():
    plan = json.dumps(
        [_tache_dag("racine"), _tache_dag("aval", dependances=("racine",))],
        ensure_ascii=False,
    )
    engine, _ = _engine_scripte(plan, echecs=frozenset({"racine"}))
    journal = RunJournal(run_id="run-dag")
    report = asyncio.run(engine.run("Objectif", journal=journal))

    # Journal : l'étape bloquée est consignée avec son statut explicite, sans usage.
    trace = next(rec for rec in journal.records if rec.etape == "aval")
    assert trace.statut == STATUT_BLOQUEE
    assert "jamais exécutée ni mise en file" in (trace.erreur or "")
    # Rapport : la synthèse distingue le blocage de l'échec, le dict le chiffre.
    synthese = report.synthese()
    assert "[bloquée] Tâche aval" in synthese
    assert "- Bloquée : dépendance(s) non satisfaite(s)" in synthese
    assert report.to_dict()["bloquees"] == 1
    # Aucun agent ni worker n'a été sollicité pour la tâche bloquée.
    bloquee = report.bloquees[0]
    assert bloquee.worker == ""
    assert bloquee.usage.appels == 0


def test_un_plan_cyclique_est_rejete_a_la_validation():
    # Critère ⑪ : le cycle est refusé avant toute exécution — l'exécuteur n'est
    # jamais sollicité (la validation du plan vit dans `validate_plan`, cf. #3).
    plan = json.dumps(
        [
            _tache_dag("a", dependances=("b",)),
            _tache_dag("b", dependances=("a",)),
        ],
        ensure_ascii=False,
    )
    engine, executor = _engine_scripte(plan)

    with pytest.raises(TaskValidationError, match="[Cc]ycle"):
        asyncio.run(engine.run("Objectif"))
    assert executor.executees == []
