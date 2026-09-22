"""Smoke tests des points d'entrée CLI/démo (ticket #89).

Ces six fichiers sont exclus de la métrique de couverture (`[tool.coverage.run]
omit` dans pyproject.toml) : fines couches d'invocation autour du code métier,
elles n'ont pas vocation à être couvertes ligne à ligne. En contrepartie, chaque
point d'entrée a ici un test d'invocation minimale — `--help`, appel mal formé,
parcours nominal court sur dépendances factices — pour qu'une régression
grossière (module qui ne s'importe plus, option renommée, câblage cassé) soit
détectée même hors métrique. Aucun appel réseau, aucun serveur réellement lancé.
"""

import asyncio
import re
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from maestro import check_env, temporal_demo
from maestro.agents import runtime_cli
from maestro.config import Settings
from maestro.controltower import cli as controltower_cli
from maestro.controltower import demo as controltower_demo
from maestro.controltower.app import create_app
from maestro.controltower.chat import ChatStore
from maestro.controltower.events import (
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
)
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.state import EXECUTION_EN_COURS, ControlTowerState
from maestro.engine import (
    MODE_BRIEF_SANS,
    STATUT_TERMINEE,
    OrchestrationEngine,
    RunReport,
    TaskResult,
)
from maestro.engine import cli as engine_cli
from maestro.orchestrator import cli as orchestrator_cli
from maestro.orchestrator.orchestrator import Orchestrator


def _settings(*, api_key=None, mode=None) -> Settings:
    """`Settings` factice : le smoke test ne dépend pas de l'environnement réel."""
    return Settings(
        anthropic_api_key=api_key,
        anthropic_model="claude-opus-4-8",
        claude_auth_mode=mode,
        claude_oauth_token=None,
        database_url=None,
        redis_url=None,
    )


# --- maestro-check-env (maestro/check_env.py) -------------------------------------------


def test_check_env_sort_en_0_quand_tout_est_vert(monkeypatch, capsys):
    monkeypatch.setattr(check_env, "load_settings", lambda: _settings(api_key="sk-test"))

    assert check_env.main() == 0
    assert "Environnement prêt." in capsys.readouterr().out


def test_check_env_sort_en_1_si_l_auth_est_mal_configuree(monkeypatch, capsys):
    # Mode 'api_key' explicite sans clé : ConfigError attrapée, verdict FAIL.
    monkeypatch.setattr(check_env, "load_settings", lambda: _settings(mode="api_key"))

    assert check_env.main() == 1
    assert "Environnement incomplet" in capsys.readouterr().out


# --- maestro-orchestrate (maestro/orchestrator/cli.py) -----------------------------------


class _TachePlanifiee:
    def to_dict(self):
        return {"id": "t1", "titre": "Tâche factice"}


class _OrchestrateurFactice:
    async def plan(self, objectif):
        return [_TachePlanifiee()]


def test_orchestrate_help_sort_en_0(capsys):
    assert orchestrator_cli.main(["--help"]) == 0
    assert "Usage" in capsys.readouterr().err


def test_orchestrate_sans_objectif_sort_en_2(capsys):
    assert orchestrator_cli.main([]) == 2
    assert "Usage" in capsys.readouterr().err


def test_orchestrate_nominal_imprime_le_plan_json(monkeypatch, capsys):
    monkeypatch.setattr(Orchestrator, "default", staticmethod(lambda: _OrchestrateurFactice()))

    assert orchestrator_cli.main(["Prototyper un mini-CRM"]) == 0
    assert '"Tâche factice"' in capsys.readouterr().out


# --- maestro-run (maestro/engine/cli.py) --------------------------------------------------


class _MoteurFactice:
    def __init__(self, report):
        self._report = report
        self.modes_brief: list[str] = []
        self.projets: list[str | None] = []

    async def run(
        self, objectif, *, journal=None, mode_brief=MODE_BRIEF_SANS, projet_id=None
    ):
        # `mode_brief` (#320) fait partie de la signature du vrai moteur : la CLI le
        # lui passe pour dire sous quel régime de brief tourner. Le factice la
        # reproduit et retient ce qu'il a reçu, sans jouer le régime — il n'appelle
        # aucun modèle, donc aucun brief n'est rédigé. `projet_id` (#222, #1042) de
        # même : c'est lui qui décide de l'équipe qui prend les tâches.
        self.modes_brief.append(mode_brief)
        self.projets.append(projet_id)
        return self._report


def _rapport_ok() -> RunReport:
    return RunReport(
        objectif="Objectif",
        resultats=(
            TaskResult(
                task_id="t1",
                titre="t1",
                agent="developpeur",
                role="Développeur",
                competences_requises=(),
                score=1,
                statut=STATUT_TERMINEE,
                sortie="Livrable",
                erreur=None,
                fichiers=(),
            ),
        ),
    )


def test_run_help_sort_en_0(capsys):
    assert engine_cli.main(["-h"]) == 0
    assert "Usage" in capsys.readouterr().err


def test_run_sans_objectif_sort_en_2():
    assert engine_cli.main([]) == 2


def test_run_refuse_un_flag_sans_valeur(capsys):
    assert engine_cli.main(["--plafond-cout"]) == 2
    assert "valeur numérique" in capsys.readouterr().err


def test_run_refuse_queue_avec_timeout(capsys):
    assert engine_cli.main(["--queue", "--timeout", "5", "Objectif"]) == 2
    assert "--queue" in capsys.readouterr().err


def test_run_nominal_imprime_la_synthese(monkeypatch, capsys):
    monkeypatch.setattr(
        OrchestrationEngine, "default", staticmethod(lambda **_: _MoteurFactice(_rapport_ok()))
    )

    assert engine_cli.main(["Prototyper un mini-CRM"]) == 0
    assert capsys.readouterr().out  # la synthèse Markdown est bien imprimée


def test_run_restitution_expurgee_des_secrets_servis(monkeypatch, capsys):
    # #115 : un secret servi pendant le run (canal Figma, token…) peut ressurgir
    # dans l'objectif cité ou un livrable — la restitution (--json comme la
    # synthèse) est expurgée, au même titre que la trace.
    from maestro.telemetry import MARQUEUR_SECRET, enregistre_secret

    canal = "canal-cli-smoke-9z8y7x"
    enregistre_secret(canal)
    rapport = _rapport_ok()
    rapport = replace(
        rapport,
        resultats=(replace(rapport.resultats[0], sortie=f"Canal rejoint : {canal}."),),
    )
    monkeypatch.setattr(
        OrchestrationEngine, "default", staticmethod(lambda **_: _MoteurFactice(rapport))
    )

    assert engine_cli.main(["--json", "Objectif"]) == 0
    sortie = capsys.readouterr().out
    assert canal not in sortie
    assert MARQUEUR_SECRET in sortie


def test_run_refuse_un_parallele_invalide(capsys):
    # Le plafond global (#100) est un entier ≥ 1 : zéro et fractions sont refusés.
    assert engine_cli.main(["--parallele", "0", "Objectif"]) == 2
    assert "--parallele" in capsys.readouterr().err
    assert engine_cli.main(["--parallele", "1.5", "Objectif"]) == 2
    assert "--parallele" in capsys.readouterr().err


def test_run_transmet_le_plafond_global_au_moteur(monkeypatch, capsys):
    recus = {}

    def _default(**kwargs):
        recus.update(kwargs)
        return _MoteurFactice(_rapport_ok())

    monkeypatch.setattr(OrchestrationEngine, "default", staticmethod(_default))

    assert engine_cli.main(["--parallele", "2", "Objectif"]) == 0
    assert recus["max_parallele"] == 2
    assert capsys.readouterr().out


# --- maestro-api (maestro/controltower/cli.py) --------------------------------------------


def test_api_help_sort_en_0(capsys):
    assert controltower_cli.main(["-h"]) == 0
    assert "Usage" in capsys.readouterr().err


def test_api_refuse_un_port_non_entier(capsys):
    assert controltower_cli.main(["--port", "abc"]) == 2
    assert "entier" in capsys.readouterr().err


def test_api_refuse_un_flag_inconnu(capsys):
    assert controltower_cli.main(["--nawak"]) == 2
    assert "Usage" in capsys.readouterr().err


def test_api_nominal_lance_uvicorn_sur_l_ecoute_demandee(monkeypatch):
    # `import uvicorn` est local à main() : on shadow le module pour ne rien servir.
    appels = {}
    factice = types.ModuleType("uvicorn")
    factice.run = lambda app, **kwargs: appels.update(app=app, **kwargs)
    monkeypatch.setitem(sys.modules, "uvicorn", factice)

    assert controltower_cli.main(["--hote", "0.0.0.0", "--port", "9000"]) == 0
    assert appels["app"] == "maestro.controltower.app:create_default_app"
    assert appels["factory"] is True
    assert (appels["host"], appels["port"]) == ("0.0.0.0", 9000)


# --- maestro-controltower-demo (maestro/controltower/demo.py) -----------------------------


def test_demo_controltower_help_sort_par_systemexit_0(capsys):
    with pytest.raises(SystemExit) as excinfo:
        controltower_demo.main(["--help"])
    assert excinfo.value.code == 0
    assert "maestro-controltower-demo" in capsys.readouterr().out


def test_demo_controltower_refuse_un_port_non_entier():
    with pytest.raises(SystemExit) as excinfo:
        controltower_demo.main(["--port", "abc"])
    assert excinfo.value.code == 2


def test_demo_controltower_nominal_sert_sur_l_ecoute_demandee(monkeypatch):
    # On coupe au niveau de `_servir` : le scénario et uvicorn sont bloquants.
    appels = {}

    async def _servir_factice(hote, port, scenario):
        appels.update(hote=hote, port=port, scenario=scenario)
        return 0

    monkeypatch.setattr(controltower_demo, "_servir", _servir_factice)

    assert controltower_demo.main(["--port", "9100"]) == 0
    assert appels == {
        "hote": controltower_demo.HOTE_DEFAUT,
        "port": 9100,
        "scenario": controltower_demo.SCENARIO_NOMINAL,
    }


# Les états limites de la démo (#978, lot 3 de #972) : la relecture visuelle reconnaissait ne pas
# savoir atteindre une file vide ou une API en panne — c'est là que le rendu casse. Ce qui se garde
# ici est qu'ils sont ATTEIGNABLES et qu'ils servent ce qu'ils annoncent ; leur montage par le
# lanceur est gardé dans test_controltower_mode_reel.py. La relecture ne les monte plus : elle
# regarde la vraie stack depuis #1165 (test_relecture_visuelle.py).


@pytest.mark.parametrize("scenario", controltower_demo.SCENARIOS)
def test_demo_controltower_sert_le_scenario_demande(monkeypatch, scenario):
    recus = {}

    async def _servir_factice(hote, port, scenario):
        recus["scenario"] = scenario
        return 0

    monkeypatch.setattr(controltower_demo, "_servir", _servir_factice)
    assert controltower_demo.main(["--scenario", scenario]) == 0
    assert recus == {"scenario": scenario}


def test_demo_controltower_refuse_un_scenario_inconnu():
    with pytest.raises(SystemExit) as excinfo:
        controltower_demo.main(["--scenario", "plein"])
    assert excinfo.value.code == 2


def test_demo_controltower_declare_ses_scenarios_sous_la_forme_que_les_scripts_lisent():
    """`start.sh` LIT les noms dans le source (#830), par une ligne
    `SCENARIO_<NOM> = "<nom>"` en début de ligne. Une constante annotée ou calculée serait un état
    que la démo sert et qu'aucun script ne sait demander."""
    motif = re.compile(r'^SCENARIO_[A-Z_]* *= *"([^"]*)"', re.M)
    fautif = 'SCENARIO_PLEIN: str = "plein"\n'
    assert not motif.findall(fautif), "motif trop large : l'écart ci-dessous ne se verrait pas"
    source = Path(controltower_demo.__file__).read_text(encoding="utf-8")
    assert tuple(motif.findall(source)) == controltower_demo.SCENARIOS


class _ServeurFactice:
    """`uvicorn.Server` réduit à ce que `_servir` attend : démarré d'emblée, rendu aussitôt."""

    def __init__(self, config):
        self.started = True

    async def serve(self):
        return None


@pytest.mark.parametrize(
    ("scenario", "publie", "en_panne"),
    [
        ("nominal", "nominal", False),
        ("vide", None, False),
        ("erreur", None, True),
        ("charge", "charge", False),
        ("decomposition", "decomposition", False),
    ],
)
def test_demo_controltower_chaque_scenario_sert_ce_qu_il_annonce(
    monkeypatch, tmp_path, scenario, publie, en_panne
):
    """« vide » et « erreur » ne publient RIEN — un état vide est l'absence d'événement, une API en
    panne n'a rien à montrer derrière sa panne ; seule « erreur » branche la panne ; le nominal
    reste celui d'avant #978, dont `captures.sh` et les parcours filmés dépendent."""
    publies = []
    apps = []

    def _publication(nom):
        def publier(bus):
            publies.append(nom)
            return asyncio.sleep(0)

        return publier

    vrai_create_app = controltower_demo.create_app

    def _create_app(**kwargs):
        apps.append(vrai_create_app(**kwargs))
        return apps[-1]

    rangs = iter(range(100))

    def _mkdtemp(prefix):
        # Les dépôts éphémères de la démo sous tmp_path : rien ne reste dans le /tmp du poste.
        dossier = tmp_path / f"{prefix}{next(rangs)}"
        dossier.mkdir()
        return str(dossier)

    monkeypatch.setattr(controltower_demo.tempfile, "mkdtemp", _mkdtemp)
    factice = types.ModuleType("uvicorn")
    factice.Config = lambda app, **kwargs: types.SimpleNamespace(app=app)
    factice.Server = _ServeurFactice
    monkeypatch.setitem(sys.modules, "uvicorn", factice)
    monkeypatch.setattr(controltower_demo, "create_app", _create_app)
    monkeypatch.setattr(controltower_demo, "_scenario", _publication("nominal"))
    monkeypatch.setattr(controltower_demo, "_scenario_charge", _publication("charge"))
    monkeypatch.setattr(
        controltower_demo, "_scenario_decomposition", _publication("decomposition")
    )

    assert asyncio.run(controltower_demo._servir("127.0.0.1", 0, scenario)) == 0
    assert publies == ([publie] if publie else [])
    branche = [m for m in apps[0].user_middleware if m.cls is controltower_demo._ApiEnErreur]
    assert bool(branche) is en_panne


def test_demo_controltower_erreur_repond_en_panne_sous_le_cors():
    """La panne passe SOUS le CORS : sans ses en-têtes, le navigateur cacherait la réponse au code,
    qui ne verrait qu'un « Failed to fetch » — une API coupée, pas une API en erreur. Et deux routes
    répondent, sans quoi le shell resterait à sa porte : on ne verrait qu'une erreur, la sienne."""
    origine = {"Origin": "http://localhost:3000"}
    with TestClient(create_app()) as client:
        assert client.get("/api/taches?projet=tous", headers=origine).status_code == 200, (
            "témoin : sans le scénario, la même route répond"
        )

    app = create_app()
    controltower_demo._brancher_erreur(app)
    with TestClient(app) as client:
        panne = client.get("/api/taches?projet=tous", headers=origine)
        assert panne.status_code == controltower_demo.STATUT_ERREUR_SIMULEE
        assert panne.json()["detail"] == controltower_demo.MESSAGE_ERREUR_SIMULEE
        assert panne.headers.get("access-control-allow-origin") == "*"
        for route in controltower_demo.ROUTES_EPARGNEES_PAR_L_ERREUR:
            assert client.get(route, headers=origine).status_code != (
                controltower_demo.STATUT_ERREUR_SIMULEE
            ), f"{route} doit rester épargnée par la panne"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/evenements") as socket:
                socket.receive_text()


class _BusQuiRetient:
    """Le bus réduit à ce que la charge emploie : elle publie, et c'est tout ce qu'on compte."""

    def __init__(self):
        self.evenements = []

    async def publish(self, event):
        self.evenements.append(event)


def test_demo_controltower_charge_depasse_ce_qu_un_ecran_affiche(tmp_path):
    """Les volumes annoncés sont ceux publiés, et les TROIS formes de texte long sont là — un nom,
    une phrase, un jeton sans espace : elles ne cassent pas le rendu de la même façon."""
    d = controltower_demo
    bus = _BusQuiRetient()
    asyncio.run(d._scenario_charge(bus))
    par_type = {}
    for event in bus.evenements:
        par_type.setdefault(event.type, []).append(event)

    taches = [e for e in par_type[EVENEMENT_TACHE_STATUT] if e.run_id == d.RUN_CHARGE]
    assert len(taches) == d.CHARGE_TACHES
    assert len({e.run_id for e in bus.evenements}) == d.CHARGE_RUNS
    assert len(par_type[EVENEMENT_VALIDATION_DEMANDE]) == d.CHARGE_VALIDATIONS
    assert len(par_type[EVENEMENT_RUN_PLAN][0].plan) == d.CHARGE_TACHES
    assert len(d.AGENT_LONG) >= 80 and d.AGENT_LONG in {e.agent for e in taches}
    assert " " not in d.JETON_LONG and any(d.JETON_LONG in e.titre for e in taches)
    assert any(e.description == d.TEXTE_LONG for e in taches)

    store = ChatStore(tmp_path)
    d._peupler_chat_charge(store)
    conversations = store.conversations(NOM_ORCHESTRATION)
    assert len([c for c in conversations if c.messages]) == d.CHARGE_CONVERSATIONS
    assert conversations[0].messages == d.CHARGE_MESSAGES_CHAT, (
        "la plus récente porte le fil long : c'est celle que l'écran ouvre d'office"
    )


def _sans_attendre(bus, attentes):
    """`asyncio.sleep` réduit à un compteur : le scénario se déroule d'un trait, mais dit combien
    de temps il aurait attendu et **où il en était** (le nombre d'événements déjà publiés), de quoi
    rattacher chaque attente à sa phase. Substitué **dans le module de démo seul** — remplacer
    `asyncio` du process déborderait sur `asyncio.run` ci-dessous."""

    async def dormir(delai):
        attentes.append((len(bus.evenements), delai))

    return types.SimpleNamespace(sleep=dormir)


def test_demo_controltower_decomposition_travaille_sans_tache_puis_publie_son_plan(monkeypatch):
    """Le critère de #1109 (constat G11), lu là où l'écran le lit : pendant `DUREE_DECOMPOSITION_S`,
    le run existe, n'est pas soldé et n'a **aucune tâche** — la conjonction exacte de
    `estEnDecomposition` (`apps/web/lib/execution.ts`) —, puis son plan paraît d'un coup."""
    d = controltower_demo
    attentes = []
    bus = _BusQuiRetient()
    monkeypatch.setattr(d, "asyncio", _sans_attendre(bus, attentes))

    asyncio.run(d._un_passage_de_decomposition(bus, 1))

    types_publies = [e.type for e in bus.evenements]
    rang_plan = types_publies.index(EVENEMENT_RUN_PLAN)
    avant_le_plan = bus.evenements[:rang_plan]
    assert EVENEMENT_TACHE_STATUT not in types_publies[:rang_plan], (
        "une seule tâche publiée avant le plan et l'écran ne dirait plus « décomposition »"
    )
    attendu_avant_le_plan = sum(delai for vus, delai in attentes if vus <= rang_plan)
    assert attendu_avant_le_plan == d.DUREE_DECOMPOSITION_S
    assert d.DUREE_DECOMPOSITION_S >= 60, "une durée lisible : de quoi ouvrir deux écrans"

    # Le verdict se rend sur la projection, pas sur la liste d'événements : c'est elle que
    # `GET /api/executions` sert, et `nb_taches` est le compte unique de #924.
    etat = ControlTowerState()
    for event in avant_le_plan:
        etat.appliquer(event)
    run = etat.execution(avant_le_plan[0].run_id)
    assert run is not None
    assert run.statut == EXECUTION_EN_COURS
    assert run.nb_taches == 0
    assert run.objectif == d.OBJECTIF_DECOMPOSITION
    assert run.cout_usd is not None and run.cout_usd > 0, (
        "le coût monte pendant que rien ne bouge — c'est la moitié du constat G11"
    )

    etat.appliquer(bus.evenements[rang_plan])
    assert run.nb_taches == len(d.PLAN_DEMO), "le compte bascule une fois, quand le plan arrive"
    soldes = [
        e
        for e in bus.evenements[rang_plan:]
        if e.type == EVENEMENT_TACHE_STATUT and e.statut == STATUT_TERMINEE
    ]
    assert len(soldes) == len(d.PLAN_DEMO), "l'après de la transition se regarde aussi"


def test_demo_controltower_decomposition_donne_ses_propres_taches_a_chaque_passage():
    """Deux passages ne partagent ni run ni tâche : des identifiants communs feraient rejouer sous
    les yeux le pipeline du passage précédent (le cas de relance que `taches_vues` documente)."""
    d = controltower_demo
    premier = d._plan_du_passage(f"{d.RUN_DECOMPOSITION}-01")
    second = d._plan_du_passage(f"{d.RUN_DECOMPOSITION}-02")

    assert not {n.id for n in premier} & {n.id for n in second}
    assert [n.titre for n in premier] == [n.titre for n in d.PLAN_DEMO]
    # Les arêtes suivent les identifiants réécrits : un plan dont les dépendances pointeraient
    # encore vers `demo-t1` serait un graphe sans une seule arête.
    connus = {noeud.id for noeud in premier}
    assert {dep for noeud in premier for dep in noeud.dependances} <= connus
    assert sum(len(noeud.dependances) for noeud in premier) == sum(
        len(noeud.dependances) for noeud in d.PLAN_DEMO
    )


# --- maestro-temporal-demo (maestro/temporal_demo.py) -------------------------------------


def test_temporal_demo_signale_le_serveur_injoignable(monkeypatch, capsys):
    # Serveur Temporal absent : le bridge lève (RPCError/RuntimeError), main() rend 1
    # et indique comment démarrer le serveur — pas de trace nue.
    async def _connect_ko(*_args, **_kwargs):
        raise RuntimeError("Failed client connect")

    monkeypatch.setattr(temporal_demo.Client, "connect", _connect_ko)

    assert temporal_demo.main() == 1
    sortie = capsys.readouterr().out
    assert "injoignable" in sortie
    assert "docker compose" in sortie


def test_temporal_demo_nominal_imprime_le_resultat(monkeypatch, capsys):
    # On coupe au niveau de `_executer` (worker + workflow bloquants) : main() se
    # réduit alors à son câblage — adresse résolue, résultat imprimé, code 0.
    async def _executer_factice(adresse):
        return "Bonjour, Maestro !"

    monkeypatch.setattr(temporal_demo, "_executer", _executer_factice)

    assert temporal_demo.main() == 0
    assert "Bonjour, Maestro !" in capsys.readouterr().out


# --- maestro-dev/bdd/qa/devops/designer (maestro/agents/runtime_cli.py) -------------------

_ENTREES_ROLES = [
    ("maestro-dev", runtime_cli.main_dev),
    ("maestro-bdd", runtime_cli.main_bdd),
    ("maestro-qa", runtime_cli.main_qa),
    ("maestro-devops", runtime_cli.main_devops),
    ("maestro-designer", runtime_cli.main_designer),
]


class _IssueFactice:
    workspace = "/tmp/espace-factice"
    a_produit = True

    def synthese(self):
        return "# Compte-rendu\n1 fichier produit"

    def to_dict(self):
        return {"fichiers": ["livrable.md"]}


class _RuntimeFactice:
    async def execute(self, description, *, keep_workspace=False):
        return _IssueFactice()


@pytest.mark.parametrize(("prog", "entree"), _ENTREES_ROLES)
def test_roles_help_sort_en_0(prog, entree, capsys):
    assert entree(["-h"]) == 0
    assert prog in capsys.readouterr().err


def test_role_sans_tache_sort_en_2(capsys):
    assert runtime_cli.main_dev([]) == 2
    assert "Usage" in capsys.readouterr().err


@pytest.mark.parametrize(("prog", "entree"), _ENTREES_ROLES)
def test_roles_nominal_impriment_le_compte_rendu(prog, entree, monkeypatch, capsys):
    # Chaque rôle passe par le même runtime générique : on vérifie surtout que le
    # câblage profil → runtime → sortie tient pour chacun des cinq binômes.
    monkeypatch.setattr(
        runtime_cli.AgentRuntime, "default", staticmethod(lambda profile: _RuntimeFactice())
    )

    assert entree(["Réaliser la tâche de démonstration"]) == 0
    assert "Compte-rendu" in capsys.readouterr().out
