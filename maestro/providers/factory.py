"""Fabrique du fournisseur par défaut — la bascule par configuration (ticket #69).

Traduit la config (`MAESTRO_PROVIDER`, `MAESTRO_MODEL`) en fournisseur prêt à
l'emploi : c'est LE point où les raccourcis `.default()` du moteur, de
l'orchestrateur et des runtimes résolvent leur fournisseur — plus aucun d'eux ne
connaît un fournisseur concret, le choix vit dans l'environnement (objectif O7,
ENF-11). Rendre un fournisseur sélectionnable par config = l'inscrire dans
`_FABRIQUES` ; le registre par `ModelSpec` (résolution nom → implémentation à
partir de `Credentials`) reste, lui, dans `maestro.providers.registry`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from maestro.config import ConfigError, Settings, load_settings
from maestro.providers.base import ModelProvider
from maestro.providers.claude import ClaudeProvider
from maestro.providers.openai_compat import OpenAICompatProvider
from maestro.providers.registry import UnknownProviderError

#: Fournisseurs sélectionnables par `MAESTRO_PROVIDER` : nom → construction depuis
#: la config (chaque fournisseur sait dériver ses credentials de ses variables).
_FABRIQUES: Mapping[str, Callable[[Settings], ModelProvider]] = {
    ClaudeProvider.name: ClaudeProvider.from_settings,
    OpenAICompatProvider.name: OpenAICompatProvider.from_settings,
}


def provider_from_settings(settings: Settings | None = None) -> ModelProvider:
    """Construit le fournisseur désigné par la config (`MAESTRO_PROVIDER`).

    Lève `UnknownProviderError` si le nom configuré n'a pas de fabrique, et
    propage les erreurs de config du fournisseur lui-même (`ConfigError`).
    """
    settings = settings if settings is not None else load_settings()
    try:
        fabrique = _FABRIQUES[settings.provider]
    except KeyError as exc:
        connus = ", ".join(sorted(_FABRIQUES))
        raise UnknownProviderError(
            f"MAESTRO_PROVIDER={settings.provider!r} inconnu. "
            f"Fournisseurs configurables : {connus}."
        ) from exc
    return fabrique(settings)


def default_model(settings: Settings) -> str:
    """Le modèle par défaut de l'exécution (orchestrateur), selon la config.

    `MAESTRO_MODEL` fait foi quand il est renseigné. Sinon, seul Claude a un
    défaut historique (`ANTHROPIC_MODEL`) ; les autres fournisseurs n'en ont pas
    de plausible (nommages hétéroclites d'un endpoint à l'autre) : `MAESTRO_MODEL`
    y est requis, et son absence est une erreur de config explicite.
    """
    if settings.model:
        return settings.model
    if settings.provider == ClaudeProvider.name:
        return settings.anthropic_model
    raise ConfigError(
        f"MAESTRO_MODEL est requis avec MAESTRO_PROVIDER={settings.provider!r} "
        "(pas de modèle par défaut pour ce fournisseur). Renseignez un modèle "
        "servi par votre endpoint."
    )
