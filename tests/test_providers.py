"""Tests de la couche d'abstraction fournisseur (ticket #32).

Aucun appel réseau : l'adaptateur Claude est exercé avec le SDK monkeypatché.
L'authentification réelle et l'appel live relèvent du ticket #30.
"""

import asyncio
from pathlib import Path

import pytest

from maestro.providers import (
    AuthMode,
    ClaudeProvider,
    Credentials,
    ModelSpec,
    UnknownProviderError,
    UnsupportedCapability,
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
    # La clé API reste optionnelle : sans clé ni mode explicite, on retombe sur le
    # mode 'subscription' (défaut POC). La bascule détaillée est couverte par test_auth.
    class _Settings:
        anthropic_api_key = None
        claude_auth_mode = None
        claude_oauth_token = None

    provider = ClaudeProvider.from_settings(_Settings())
    assert provider.credentials.api_key is None
    assert provider.credentials.auth_mode is AuthMode.SUBSCRIPTION


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


def test_claude_generate_n_expose_aucun_outil(monkeypatch):
    # Critère #35 : generate est *texte seul* — tools=[] retire au CLI sous-jacent
    # jusqu'à ses outils par défaut (l'exécution outillée passe par run_agent).
    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["tools"] = options.tools
        yield FakeAssistantMessage([FakeTextBlock("texte")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", FakeTextBlock)

    provider = ClaudeProvider(Credentials())
    asyncio.run(provider.generate("Salut", model="claude-opus-4-8"))
    assert vu["tools"] == []


def test_run_agent_est_optionnel_et_refuse_par_defaut():
    # Capacité optionnelle (ticket #4) : un fournisseur qui ne l'implémente pas la refuse.
    class TextOnly(ModelProvider):
        name = "text-only"

        def supports(self, model):
            return True

        async def generate(self, prompt, *, model, system_prompt=None):
            return "texte"

    with pytest.raises(UnsupportedCapability):
        asyncio.run(
            TextOnly().run_agent(
                "fais", model="m", workspace=Path("."), tools=("Read",)
            )
        )


def test_claude_run_agent_cable_le_workspace_les_outils_et_assemble_le_texte(monkeypatch):
    # La frontière fixe cwd/tools/permission pour une exécution outillée isolée, sans réseau.
    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["prompt"] = prompt
        vu["cwd"] = options.cwd
        vu["tools"] = options.tools
        vu["allowed_tools"] = options.allowed_tools
        vu["permission_mode"] = options.permission_mode
        yield FakeAssistantMessage([FakeTextBlock("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", FakeTextBlock)

    provider = ClaudeProvider(Credentials())
    ws = Path("/tmp/ws-xyz")
    result = asyncio.run(
        provider.run_agent(
            "Code ceci", model="claude-sonnet-5", workspace=ws, tools=("Read", "Write")
        )
    )

    assert result == "Livré."
    assert vu["cwd"] == ws
    assert vu["tools"] == ["Read", "Write"]
    assert vu["allowed_tools"] == ["Read", "Write"]
    assert vu["permission_mode"] == "bypassPermissions"
