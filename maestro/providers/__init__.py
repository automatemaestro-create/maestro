"""Couche d'abstraction fournisseur de Maestro (tickets #32, #69).

Importer ce paquet enregistre les fournisseurs câblés (Claude, et tout endpoint
compatible OpenAI) et expose l'interface publique. Le moteur d'agents ne dépend
que de ces symboles, jamais d'un SDK fournisseur en direct :

    from maestro.providers import Credentials, ModelSpec, resolve_provider

    spec = ModelSpec(provider="claude", model="claude-opus-5")
    provider = resolve_provider(spec, Credentials())
    texte = await provider.generate("Bonjour", model=spec.model)

Le fournisseur de l'exécution se choisit par la config (`MAESTRO_PROVIDER`,
ticket #69) via `provider_from_settings` — c'est ce que consomment les raccourcis
`.default()` du moteur. Ajouter un fournisseur (Google, modèle local…) = créer
une classe qui implémente `ModelProvider`, l'enregistrer via `register`, et
l'inscrire dans la fabrique (`maestro.providers.factory`) — sans toucher au
moteur.
"""

from __future__ import annotations

from maestro.providers.base import (
    MENTION_STDERR_VIDE,
    AuthMode,
    CollecteurStderr,
    Credentials,
    McpServerUnavailable,
    ModelProvider,
    ModelSpec,
    TurnLimitReached,
    UnsupportedCapability,
    attache_stderr,
    stderr_de,
)
from maestro.providers.claude import ClaudeProvider
from maestro.providers.factory import default_model, provider_from_settings
from maestro.providers.openai_compat import OpenAICompatError, OpenAICompatProvider
from maestro.providers.registry import (
    ProviderFactory,
    UnknownProviderError,
    available_providers,
    register,
    resolve_provider,
    unregister,
)

__all__ = [
    "MENTION_STDERR_VIDE",
    "AuthMode",
    "ClaudeProvider",
    "CollecteurStderr",
    "Credentials",
    "McpServerUnavailable",
    "ModelProvider",
    "ModelSpec",
    "OpenAICompatError",
    "OpenAICompatProvider",
    "ProviderFactory",
    "TurnLimitReached",
    "UnknownProviderError",
    "UnsupportedCapability",
    "attache_stderr",
    "available_providers",
    "default_model",
    "provider_from_settings",
    "register",
    "resolve_provider",
    "stderr_de",
    "unregister",
]
