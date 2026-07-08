"""Couche d'abstraction fournisseur de Maestro (ticket #32).

Importer ce paquet enregistre les fournisseurs câblés (au POC : Claude) et
expose l'interface publique. Le moteur d'agents ne dépend que de ces symboles,
jamais d'un SDK fournisseur en direct :

    from maestro.providers import Credentials, ModelSpec, resolve_provider

    spec = ModelSpec(provider="claude", model="claude-opus-4-8")
    provider = resolve_provider(spec, Credentials())
    texte = await provider.generate("Bonjour", model=spec.model)

Ajouter un fournisseur (OpenAI, Google, modèle local…) = créer une classe qui
implémente `ModelProvider` et l'enregistrer via `register` — sans toucher au
moteur.
"""

from __future__ import annotations

from maestro.providers.base import Credentials, ModelProvider, ModelSpec
from maestro.providers.claude import ClaudeProvider
from maestro.providers.registry import (
    ProviderFactory,
    UnknownProviderError,
    available_providers,
    register,
    resolve_provider,
    unregister,
)

__all__ = [
    "ClaudeProvider",
    "Credentials",
    "ModelProvider",
    "ModelSpec",
    "ProviderFactory",
    "UnknownProviderError",
    "available_providers",
    "register",
    "resolve_provider",
    "unregister",
]
