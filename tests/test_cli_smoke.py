"""Smoke tests des points d'entrée CLI/démo (ticket #89).

Ces six fichiers sont exclus de la métrique de couverture (`[tool.coverage.run]
omit` dans pyproject.toml) : fines couches d'invocation autour du code métier,
elles n'ont pas vocation à être couvertes ligne à ligne. En contrepartie, chaque
point d'entrée a ici un test d'invocation minimale — `--help`, appel mal formé,
parcours nominal court sur dépendances factices — pour qu'une régression
grossière (module qui ne s'importe plus, option renommée, câblage cassé) soit
détectée même hors métrique. Aucun appel réseau, aucun serveur réellement lancé.
"""

import sys
import types

import pytest

from maestro import check_env
from maestro.agents import runtime_cli
from maestro.config import Settings
from maestro.controltower import cli as controltower_cli
from maestro.controltower import demo as controltower_demo
from maestro.engine import STATUT_TERMINEE, OrchestrationEngine, RunReport, TaskResult
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

    async def run(self, objectif):
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

    async def _servir_factice(hote, port):
        appels.update(hote=hote, port=port)
        return 0

    monkeypatch.setattr(controltower_demo, "_servir", _servir_factice)

    assert controltower_demo.main(["--port", "9100"]) == 0
    assert appels == {"hote": controltower_demo.HOTE_DEFAUT, "port": 9100}


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
