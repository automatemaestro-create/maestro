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

Ce même geste le rend **proposable** (#253) : `catalogue_fournisseurs()` rend la
gamme déclarée par chaque fournisseur enregistré — modèles, et pour chacun les
niveaux d'effort admis —, servie telle quelle par `GET /api/fournisseurs`. Une
classe qui déclare ses `MODELES` apparaît donc dans le formulaire d'agent sans
qu'aucune liste ne soit recopiée dans le front. Importer ce paquet suffit à
peupler le registre : c'est ce qui rend la vue complète où qu'on l'appelle.
"""

from __future__ import annotations

from maestro.providers.base import (
    MENTION_STDERR_VIDE,
    AuthMode,
    CollecteurStderr,
    Credentials,
    FournisseurDisponible,
    McpServerUnavailable,
    ModeleDisponible,
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
    catalogue_fournisseurs,
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
    "FournisseurDisponible",
    "McpServerUnavailable",
    "ModelProvider",
    "ModelSpec",
    "ModeleDisponible",
    "OpenAICompatError",
    "OpenAICompatProvider",
    "ProviderFactory",
    "TurnLimitReached",
    "UnknownProviderError",
    "UnsupportedCapability",
    "attache_stderr",
    "available_providers",
    "catalogue_fournisseurs",
    "default_model",
    "provider_from_settings",
    "register",
    "resolve_provider",
    "stderr_de",
    "unregister",
]
