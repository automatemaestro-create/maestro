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
from maestro.providers.base import ModelProvider, TurnLimitReached
from maestro.telemetry import collect_usage


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
        isolation = None  # mode isolé (#108) non demandé : exécution sur l'hôte

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


# --- Ticket #8 : remontée de l'usage (tokens, coût, durée, outils) ---------------------


class FakeTextBlock:
    def __init__(self, text):
        self.text = text


class FakeToolUseBlock:
    def __init__(self, name):
        self.name = name


class FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


class FakeResultMessage:
    """Reflet minimal du ResultMessage du SDK : tokens, coût, durée API, tours."""

    def __init__(self, *, usage=None, total_cost_usd=None, duration_api_ms=0, num_turns=1):
        self.usage = usage
        self.total_cost_usd = total_cost_usd
        self.duration_api_ms = duration_api_ms
        self.num_turns = num_turns


def _patch_sdk(monkeypatch, fake_query):
    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(claude_mod, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_mod, "ResultMessage", FakeResultMessage)


def test_claude_generate_signale_l_usage_au_collecteur(monkeypatch):
    # Le ResultMessage final (tokens, coût, durée API) remonte via collect_usage ;
    # les tokens d'entrée agrègent le prompt direct et le cache.
    async def fake_query(*, prompt, options):
        yield FakeAssistantMessage([FakeTextBlock("Réponse")])
        yield FakeResultMessage(
            usage={
                "input_tokens": 100,
                "cache_creation_input_tokens": 15,
                "cache_read_input_tokens": 5,
                "output_tokens": 30,
            },
            total_cost_usd=0.042,
            duration_api_ms=1200,
            num_turns=1,
        )

    _patch_sdk(monkeypatch, fake_query)
    provider = ClaudeProvider(Credentials())

    with collect_usage() as recolte:
        result = asyncio.run(provider.generate("Salut", model="claude-opus-4-8"))

    assert result == "Réponse"
    assert recolte.total.appels == 1
    assert recolte.total.tokens_entree == 120
    assert recolte.total.tokens_sortie == 30
    assert recolte.total.cout_usd == pytest.approx(0.042)
    assert recolte.total.duree_api_ms == 1200
    assert recolte.total.tours == 1


def test_claude_run_agent_releve_les_outils_utilises(monkeypatch):
    # Les noms d'outils des ToolUseBlock remontent, dédupliqués, dans l'usage.
    async def fake_query(*, prompt, options):
        yield FakeAssistantMessage(
            [FakeToolUseBlock("Write"), FakeTextBlock("j'écris"), FakeToolUseBlock("Bash")]
        )
        yield FakeAssistantMessage([FakeToolUseBlock("Write"), FakeTextBlock(" puis livré.")])
        yield FakeResultMessage(usage={"output_tokens": 10}, num_turns=2)

    _patch_sdk(monkeypatch, fake_query)
    provider = ClaudeProvider(Credentials())

    with collect_usage() as recolte:
        result = asyncio.run(
            provider.run_agent(
                "Code ceci", model="claude-sonnet-5",
                workspace=Path("/tmp/ws-xyz"), tools=("Write", "Bash"),
            )
        )

    assert result == "j'écris puis livré."
    assert recolte.total.outils == ("Write", "Bash")
    assert recolte.total.tours == 2
    # Coût non rapporté par le SDK → inconnu (None), pas zéro.
    assert recolte.total.cout_usd is None


# --- Ticket #91 : le plafond de tours est mué en erreur typée de la couche -------------


def test_claude_mue_le_plafond_de_tours_en_erreur_typee(monkeypatch):
    # Le CLI rend un résultat `error_max_turns`, que le SDK relève en exception
    # générique : la frontière la mue en TurnLimitReached — reconnaissable par le
    # moteur (jamais relancée, #91) sans lire d'erreur propre au SDK.
    async def fake_query(*, prompt, options):
        raise Exception("Claude Code returned an error result: error_max_turns")
        yield  # jamais atteint : fait de fake_query un générateur asynchrone

    _patch_sdk(monkeypatch, fake_query)
    provider = ClaudeProvider(Credentials())

    with pytest.raises(TurnLimitReached, match="plafond de tours"):
        asyncio.run(provider.generate("Salut", model="claude-opus-4-8"))


def test_claude_laisse_passer_les_autres_erreurs_sdk_inchangees(monkeypatch):
    # Un crash quelconque du sous-processus n'est PAS un plafond de tours : il
    # remonte tel quel (c'est l'aléa transitoire que la relance #91 cible).
    async def fake_query(*, prompt, options):
        raise Exception("Fatal error in message reader")
        yield  # jamais atteint : fait de fake_query un générateur asynchrone

    _patch_sdk(monkeypatch, fake_query)
    provider = ClaudeProvider(Credentials())

    with pytest.raises(Exception, match="Fatal error in message reader") as excinfo:
        asyncio.run(provider.generate("Salut", model="claude-opus-4-8"))
    assert not isinstance(excinfo.value, TurnLimitReached)
