"""Tests du mode isolé — conteneur durci via le shim `cli_path` (ticket #108, lot #107).

Aucun démon Docker requis : la commande `docker run` est construite et vérifiée
sans être lancée, le shim est invoqué sur un `subprocess.run` factice. Fait
partie du lot final « tests + doc » du renforcement sécurité (#102) — couvre le
critère « démarrage du mode isolé » côté câblage (le smoke test **réel** —
conteneur effectivement lancé — exige Docker, absent des runners CI : la
procédure manuelle est documentée dans docs/19, §Vérification) :

① **config** (`IsolationConfig.from_settings`) : mode absent → None (exécution
   sur l'hôte, défaut) ; mode ou réseau inconnus, shim introuvable → erreur de
   config explicite **au câblage**, pas au milieu d'une exécution ;
② **commande docker durcie** (`commande_docker`) : racine en lecture seule,
   seuls workspace et tmpfs inscriptibles, réseau restreint, privilèges
   retirés, plafonds de ressources, environnement minimal (les seules
   variables d'auth `ENV_TRANSMISES` — jamais l'environnement hôte entier) ;
③ **smoke test du shim** (`maestro-sandbox-shim`, exclu de la métrique de
   couverture comme les autres points d'entrée — #89) : protocole absent →
   sortie 2 avec l'explication ; protocole présent → la commande docker est
   lancée, arguments du CLI relayés, code de sortie remonté inchangé ;
④ **câblage fournisseur Claude** : en mode isolé, `run_agent` pointe le SDK
   sur le shim (`cli_path`) et pose le protocole `MAESTRO_SANDBOX_*` ; hors
   mode isolé, rien ne change ; `from_settings` valide l'isolation au câblage.
"""

import asyncio
from pathlib import Path

import pytest

from maestro.config import ConfigError, Settings
from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod
from maestro.sandbox import container
from maestro.sandbox import shim as shim_mod
from maestro.sandbox.container import (
    ENV_IMAGE,
    ENV_RESEAU,
    ENV_TRANSMISES,
    ENV_WORKSPACE,
    IMAGE_DEFAUT,
    IsolationConfig,
    commande_docker,
)

# --- Aides ------------------------------------------------------------------------------


def _settings(**surcharges) -> Settings:
    """`Settings` factice : seuls les réglages d'isolation varient dans ces tests."""
    champs = {
        "anthropic_api_key": None,
        "anthropic_model": "claude-opus-4-8",
        "claude_auth_mode": None,
        "claude_oauth_token": None,
        "database_url": None,
        "redis_url": None,
    }
    champs.update(surcharges)
    return Settings(**champs)


def _protocole(workspace: str = "C:/espace/tache") -> dict[str, str]:
    """L'environnement minimal du protocole fournisseur → shim."""
    return {
        ENV_IMAGE: "maestro-sandbox:latest",
        ENV_RESEAU: "bridge",
        ENV_WORKSPACE: workspace,
    }


@pytest.fixture()
def shim_resolu(monkeypatch):
    """Résout le shim sur un chemin factice : la présence réelle n'est pas le sujet."""
    monkeypatch.setattr(container, "_chemin_shim", lambda: Path("maestro-sandbox-shim"))


# --- ① Config : validation au câblage ---------------------------------------------------


def test_sans_mode_l_execution_reste_sur_l_hote():
    assert IsolationConfig.from_settings(_settings()) is None


def test_mode_inconnu_refuse_au_cablage():
    # Pas d'isolation « silencieusement absente » : une valeur inconnue casse tout de suite.
    with pytest.raises(ConfigError, match="MAESTRO_ISOLATION"):
        IsolationConfig.from_settings(_settings(isolation="micro-vm"))


def test_reseau_inconnu_refuse_au_cablage(shim_resolu):
    with pytest.raises(ConfigError, match="MAESTRO_ISOLATION_RESEAU"):
        IsolationConfig.from_settings(
            _settings(isolation="conteneur", isolation_reseau="host")
        )


def test_config_valide_avec_les_defauts(shim_resolu):
    config = IsolationConfig.from_settings(_settings(isolation="conteneur"))

    assert config.image == IMAGE_DEFAUT
    assert config.reseau == "bridge"
    assert config.shim == Path("maestro-sandbox-shim")


def test_image_et_reseau_surchargables(shim_resolu):
    config = IsolationConfig.from_settings(
        _settings(
            isolation="conteneur",
            isolation_image="maestro-sandbox:essai",
            isolation_reseau="none",
        )
    )

    assert (config.image, config.reseau) == ("maestro-sandbox:essai", "none")


def test_shim_introuvable_refuse_au_cablage(monkeypatch, tmp_path):
    # Sans l'exécutable, le mode isolé ne peut pas fonctionner : erreur explicite
    # avec le remède (réinstaller le paquet), plutôt qu'un échec en cours de run.
    monkeypatch.setattr(container.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(container.shutil, "which", lambda nom: None)

    with pytest.raises(ConfigError, match="maestro-sandbox-shim"):
        IsolationConfig.from_settings(_settings(isolation="conteneur"))


def test_le_shim_reel_est_installe_dans_le_venv():
    # Garde d'installation (pyproject [project.scripts]) : le point d'entrée
    # existe à côté de l'interpréteur — c'est lui que `cli_path` pointera.
    assert container._chemin_shim().exists()


def test_env_sandbox_porte_le_protocole(shim_resolu):
    config = IsolationConfig.from_settings(_settings(isolation="conteneur"))

    env = config.env_sandbox(Path("C:/espace/tache"))

    assert env == {
        ENV_IMAGE: IMAGE_DEFAUT,
        ENV_RESEAU: "bridge",
        ENV_WORKSPACE: str(Path("C:/espace/tache")),
    }


# --- ② La commande docker est durcie ----------------------------------------------------


def test_protocole_absent_refuse():
    # Le shim ne s'invoque que via le mode isolé du fournisseur, pas à la main.
    with pytest.raises(ConfigError, match="MAESTRO_SANDBOX"):
        commande_docker({}, [])


def test_la_commande_borne_fichiers_reseau_privileges_et_ressources():
    commande = commande_docker(_protocole(), ["--version"])

    # Conteneur jetable, un par exécution : rien ne survit ni ne se partage.
    assert "--rm" in commande
    # Système de fichiers borné : racine en lecture seule, seuls le workspace
    # de la tâche et deux tmpfs jetables sont inscriptibles.
    assert "--read-only" in commande
    assert "C:/espace/tache:/workspace" in commande
    assert any(arg.startswith("/tmp:") for arg in commande)
    assert any(arg.startswith("/home/agent:") for arg in commande)
    # Réseau restreint (sortant seul — aucun port n'est jamais publié).
    assert commande[commande.index("--network") + 1] == "bridge"
    assert "--publish" not in commande and "-p" not in commande
    # Privilèges retirés et plafonds anti-emballement.
    assert commande[commande.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in commande
    assert "--pids-limit" in commande
    assert "--memory" in commande
    assert "--cpus" in commande
    # Le CLI de l'image reçoit les arguments du SDK, relayés tels quels.
    assert commande[-3:] == ["maestro-sandbox:latest", "claude", "--version"]


def test_le_reseau_none_coupe_tout(monkeypatch):
    environ = _protocole() | {ENV_RESEAU: "none"}
    commande = commande_docker(environ, [])
    assert commande[commande.index("--network") + 1] == "none"


def test_seules_les_variables_d_auth_entrent_dans_le_conteneur():
    # L'environnement hôte entier ne traverse JAMAIS : seules les variables
    # d'auth énumérées entrent — y compris vides (la neutralisation des
    # credentials concurrents posée par le fournisseur est préservée).
    environ = _protocole() | {
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abcdef",
        "GITLAB_TOKEN": "glpat-jamais-transmis",
        "PATH": "C:/hote/bin",
    }

    commande = commande_docker(environ, [])

    transmises = [commande[i + 1] for i, arg in enumerate(commande) if arg == "--env"]
    assert "ANTHROPIC_API_KEY=" in transmises
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abcdef" in transmises
    assert not any(v.startswith(("GITLAB_TOKEN", "PATH")) for v in transmises)
    # La liste des accès accordés est fermée et documentée (docs/17 §3).
    assert set(ENV_TRANSMISES) == {
        "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    }


# --- ③ Smoke test du shim (point d'entrée du mode isolé) --------------------------------


def _purge_protocole(monkeypatch):
    for variable in (ENV_IMAGE, ENV_RESEAU, ENV_WORKSPACE):
        monkeypatch.delenv(variable, raising=False)


def test_shim_sans_protocole_sort_en_2_avec_l_explication(monkeypatch, capsys):
    _purge_protocole(monkeypatch)

    assert shim_mod.main(["--version"]) == 2
    err = capsys.readouterr().err
    assert "maestro-sandbox-shim" in err
    assert "MAESTRO_SANDBOX" in err


def test_shim_nominal_lance_docker_et_remonte_le_code_de_sortie(monkeypatch):
    # Démarrage du mode isolé sans démon Docker : on capture la commande que le
    # shim lance et le code de sortie qu'il relaie — le durcissement de la
    # commande elle-même est couvert en ②.
    _purge_protocole(monkeypatch)
    for variable, valeur in _protocole().items():
        monkeypatch.setenv(variable, valeur)
    lancee: dict[str, object] = {}

    class _Issue:
        returncode = 7

    def _run_factice(commande, check):
        lancee["commande"] = commande
        lancee["check"] = check
        return _Issue()

    monkeypatch.setattr(shim_mod.subprocess, "run", _run_factice)

    assert shim_mod.main(["--print-mode", "stream-json"]) == 7

    commande = lancee["commande"]
    assert commande[:2] == ["docker", "run"]
    # Les arguments que le SDK destinait au CLI traversent tels quels.
    assert commande[-4:] == ["maestro-sandbox:latest", "claude", "--print-mode", "stream-json"]
    assert lancee["check"] is False


# --- ④ Câblage fournisseur : cli_path + protocole, validation from_settings -------------


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


def _run_agent_capture_options(monkeypatch, provider, workspace):
    """Lance `run_agent` sur un `query` factice et capture les options SDK."""
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["cli_path"] = options.cli_path
        vu["env"] = dict(options.env)
        yield _FakeAssistantMessage([_FakeTextBlock("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", _FakeTextBlock)
    asyncio.run(
        provider.run_agent(
            "Fais", model="claude-sonnet-5", workspace=workspace, tools=("Read",)
        )
    )
    return vu


def test_en_mode_isole_le_sdk_pointe_le_shim_et_recoit_le_protocole(monkeypatch, tmp_path):
    isolation = IsolationConfig(
        image="maestro-sandbox:latest", reseau="bridge", shim=Path("maestro-sandbox-shim")
    )
    provider = ClaudeProvider(Credentials(), isolation=isolation)

    vu = _run_agent_capture_options(monkeypatch, provider, tmp_path)

    assert vu["cli_path"] == Path("maestro-sandbox-shim")
    assert vu["env"][ENV_IMAGE] == "maestro-sandbox:latest"
    assert vu["env"][ENV_RESEAU] == "bridge"
    assert vu["env"][ENV_WORKSPACE] == str(tmp_path)


def test_hors_mode_isole_rien_ne_change(monkeypatch, tmp_path):
    provider = ClaudeProvider(Credentials())

    vu = _run_agent_capture_options(monkeypatch, provider, tmp_path)

    assert vu["cli_path"] is None
    assert ENV_IMAGE not in vu["env"]


def test_from_settings_valide_l_isolation_au_cablage():
    with pytest.raises(ConfigError, match="MAESTRO_ISOLATION"):
        ClaudeProvider.from_settings(_settings(isolation="gvisor"))
