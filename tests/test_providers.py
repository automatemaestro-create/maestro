"""Tests de la couche d'abstraction fournisseur (ticket #32).

Aucun appel réseau : l'adaptateur Claude est exercé avec le SDK monkeypatché.
L'authentification réelle et l'appel live relèvent du ticket #30.
"""

import asyncio

import pytest

from maestro.providers import (
    ClaudeProvider,
    Credentials,
    ModelSpec,
    UnknownProviderError,
    available_providers,
    register,
    resolve_provider,
    unregister,
)
from maestro.providers import claude as claude_mod
from maestro.providers.base import ModelProvider


def test_claude_is_registered():
    # Critère ② : le fournisseur Claude est câblé au POC.
    assert "claude" in available_providers()


def test_resolve_returns_claude_bound_to_credentials():
    # Critère ④ : les credentials injectés sont bien portés par le fournisseur.
    creds = Credentials(api_key="sk-test")
    spec = ModelSpec(provider="claude", model="claude-opus-4-8")
    provider = resolve_provider(spec, creds)
    assert isinstance(provider, ClaudeProvider)
    assert provider.credentials is creds


def test_claude_from_settings_reads_optional_api_key():
    # La clé API reste optionnelle au POC (auth possible par abonnement).
    class _Settings:
        anthropic_api_key = None

    provider = ClaudeProvider.from_settings(_Settings())
    assert provider.credentials.api_key is None


def test_unknown_provider_raises():
    spec = ModelSpec(provider="does-not-exist", model="whatever")
    with pytest.raises(UnknownProviderError):
        resolve_provider(spec, Credentials())


def test_claude_rejects_foreign_model():
    spec = ModelSpec(provider="claude", model="gpt-4o")
    with pytest.raises(ValueError):
        resolve_provider(spec, Credentials())


def test_claude_supports_only_claude_models():
    provider = ClaudeProvider(Credentials())
    assert provider.supports("claude-sonnet-5")
    assert not provider.supports("gpt-4o")


def test_new_provider_plugs_in_without_touching_engine():
    # Critère ③ : un fournisseur tiers s'ajoute par simple enregistrement,
    # sans modifier ni la couche ni le moteur.
    class DummyProvider(ModelProvider):
        name = "dummy"

        def __init__(self, credentials):
            self._credentials = credentials

        def supports(self, model):
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            return f"dummy:{model}:{prompt}"

    register("dummy", DummyProvider)
    try:
        spec = ModelSpec(provider="dummy", model="anything")
        provider = resolve_provider(spec, Credentials())
        assert isinstance(provider, DummyProvider)
        result = asyncio.run(provider.generate("hi", model="anything"))
        assert result == "dummy:anything:hi"
    finally:
        unregister("dummy")


def test_claude_generate_assembles_text(monkeypatch):
    # La frontière assemble le texte des blocs de la réponse, sans réseau.
    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    async def fake_query(*, prompt, options):
        assert options.model == "claude-opus-4-8"
        yield FakeAssistantMessage([FakeTextBlock("Bonjour"), FakeTextBlock(" le monde")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", FakeTextBlock)

    provider = ClaudeProvider(Credentials())
    result = asyncio.run(provider.generate("Salut", model="claude-opus-4-8"))
    assert result == "Bonjour le monde"
