import pytest

from maestro.config import ConfigError, Settings


def test_require_api_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Settings.from_env().require_api_key() == "sk-test"


def test_require_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env().require_api_key()


def test_default_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert Settings.from_env().anthropic_model == "claude-opus-4-8"
