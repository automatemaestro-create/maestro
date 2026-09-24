import pytest

from maestro.config import ConfigError, Settings
from maestro.familles_claude import derniere_version


def test_require_api_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Settings.from_env().require_api_key() == "sk-test"


def test_require_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env().require_api_key()


@pytest.mark.parametrize("valeur", [None, "", "   "])
def test_sans_modele_le_chef_de_projet_prend_la_derniere_opus(monkeypatch, valeur):
    # Absent comme vide (le `.env.example` le livre vide, #1270) : la dernière version
    # d'Opus, lue dans `familles-claude.tsv` — jamais un identifiant figé ici.
    if valeur is None:
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_MODEL", valeur)
    assert Settings.from_env().anthropic_model == derniere_version("opus")


def test_un_nom_de_famille_se_resout_comme_pour_les_runs(monkeypatch):
    # `ANTHROPIC_MODEL=sonnet` comme `run.sh --modele sonnet` (#1269) : la famille suit
    # le fichier, sans égard à la casse.
    monkeypatch.setenv("ANTHROPIC_MODEL", "Sonnet")
    assert Settings.from_env().anthropic_model == derniere_version("sonnet")


def test_un_identifiant_complet_reste_un_choix_epingle(monkeypatch):
    # Une version antérieure nommée en toutes lettres n'est pas « corrigée » : c'est
    # un choix, et il tient même quand la famille a avancé.
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    assert Settings.from_env().anthropic_model == "claude-opus-4-8"
