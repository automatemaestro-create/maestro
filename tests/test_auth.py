"""Tests des deux modes d'authentification Claude et de leur bascule (ticket #30).

Couvre la règle de précédence (config → mode), la validation, l'invariant du slot
`Credentials`, et la traduction du mode en variables d'environnement du CLI. Aucun
appel réseau : la vérification live de la bascule relève d'un test manuel documenté
dans la MR (critère ④).
"""

import pytest

from maestro.config import ConfigError, Settings
from maestro.providers import AuthMode, ClaudeProvider, Credentials


def _settings(*, api_key=None, mode=None, oauth=None) -> Settings:
    """Construit un `Settings` minimal pour exercer la dérivation des credentials."""
    return Settings(
        anthropic_api_key=api_key,
        anthropic_model="claude-opus-4-8",
        claude_auth_mode=mode,
        claude_oauth_token=oauth,
        database_url=None,
        redis_url=None,
    )


# --- Règle de précédence (critère ①) ---------------------------------------


def test_defaults_to_subscription_without_key():
    # Défaut du POC : ni mode explicite ni clé ⇒ abonnement.
    creds = ClaudeProvider.from_settings(_settings()).credentials
    assert creds.auth_mode is AuthMode.SUBSCRIPTION


def test_derives_api_key_when_key_present():
    # Sans mode explicite, la présence d'une clé bascule en mode 'api_key'.
    creds = ClaudeProvider.from_settings(_settings(api_key="sk-test")).credentials
    assert creds.auth_mode is AuthMode.API_KEY
    assert creds.api_key == "sk-test"


def test_explicit_subscription_overrides_present_key():
    # Le mode explicite l'emporte sur la déduction : abonnement même si une clé traîne.
    creds = ClaudeProvider.from_settings(
        _settings(api_key="sk-test", mode="subscription")
    ).credentials
    assert creds.auth_mode is AuthMode.SUBSCRIPTION


def test_explicit_api_key_without_key_raises():
    with pytest.raises(ConfigError):
        ClaudeProvider.from_settings(_settings(mode="api_key"))


def test_invalid_mode_raises():
    with pytest.raises(ConfigError):
        ClaudeProvider.from_settings(_settings(mode="oauth-somehow"))


def test_oauth_token_passed_through_in_subscription():
    creds = ClaudeProvider.from_settings(_settings(oauth="oauth-xyz")).credentials
    assert creds.auth_mode is AuthMode.SUBSCRIPTION
    assert creds.oauth_token == "oauth-xyz"


def test_mode_normalised_from_env(monkeypatch):
    # `CLAUDE_AUTH_MODE` est normalisé (trim + minuscules) à la lecture de l'env.
    monkeypatch.setenv("CLAUDE_AUTH_MODE", "  API_KEY  ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    creds = ClaudeProvider.from_settings(Settings.from_env()).credentials
    assert creds.auth_mode is AuthMode.API_KEY


# --- Invariant du slot Credentials (critère ②) -----------------------------


def test_credentials_reject_api_key_mode_without_key():
    with pytest.raises(ValueError):
        Credentials(auth_mode=AuthMode.API_KEY)


# --- Traduction mode → variables d'environnement du CLI (critères ①/④) ------


def test_auth_env_subscription_neutralises_ambient_credentials():
    # Un environnement ambiant (clé API / bearer) ne doit pas détourner l'abonnement :
    # on force ces variables à vide (== non défini pour le CLI).
    provider = ClaudeProvider(Credentials(auth_mode=AuthMode.SUBSCRIPTION))
    env = provider._auth_env()
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_auth_env_subscription_forwards_oauth_token():
    provider = ClaudeProvider(
        Credentials(auth_mode=AuthMode.SUBSCRIPTION, oauth_token="oauth-xyz")
    )
    env = provider._auth_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-xyz"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_auth_env_api_key_sets_key_and_neutralises_bearer():
    provider = ClaudeProvider(Credentials(auth_mode=AuthMode.API_KEY, api_key="sk-test"))
    env = provider._auth_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    # Le bearer (rang supérieur) est neutralisé pour que la clé (rang 3) l'emporte.
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
