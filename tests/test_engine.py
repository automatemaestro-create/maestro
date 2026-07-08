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
   **repli** quand le fournisseur n'a pas d'exécution outillée ;
et ceux du ticket #8 :
⑤ le **coût par tâche** (tokens, coût, durée) est visible (TaskResult, synthèse,
   rapport structuré) et traçable (journal JSON par étape, relié par le run_id) ;
⑥ les logs ne contiennent **aucun secret** ;
et ceux du ticket #7 :
⑦ au moins 2 agents s'exécutent **simultanément** sans collision (rendez-vous forcé :
   séquentiel ⇒ échec net) — y compris en aval d'une dépendance commune ;
⑧ les résultats parallèles sont **correctement rassemblés** : ordre du plan dans le
   rapport, livrable et usage attribués à la bonne tâche, sans fuite entre contextes.
Plus la résilience : un échec de routage est consigné sans interrompre la boucle.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
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
    assert "Usage total (planification incluse)" in synthese
    assert synthese.count("coût 0.0100 $") == 3


def test_chaque_etape_est_consignee_dans_le_journal():
    journal = RunJournal(run_id="run-42")
    report = asyncio.run(
        _engine(exec_provider=MeteredProvider()).run("Objectif", journal=journal)
    )

    # Une trace par étape — la planification d'abord, puis les tâches dans l'ordre.
    assert [r.etape for r in journal.records] == [
        "planification", "schema-bdd", "api-taches", "tests-api",
    ]
    assert all(r.run_id == "run-42" for r in journal.records)
    assert report.run_id == "run-42"

    # Chaque trace porte l'entrée, la sortie et l'usage de l'étape.
    tache = journal.records[1]
    assert "Schéma BDD" in tache.entree
    assert tache.sortie == "LIVRABLE mesuré"
    assert tache.usage.cout_usd == pytest.approx(0.01)


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
    # Le journal, lui, consigne dans l'ordre d'achèvement, chaque trace reliée par `etape`.
    assert [rec.etape for rec in journal.records] == [
        "planification",
        "tests-api",
        "schema-bdd",
    ]


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
