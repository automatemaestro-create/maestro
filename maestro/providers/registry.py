"""Registre des fournisseurs de modèles (ticket #32).

Fait le lien `nom de fournisseur → implémentation`. Ajouter un fournisseur se
résume à l'enregistrer ici (idéalement, à ce qu'il s'enregistre lui-même à
l'import) : le moteur d'agents, lui, ne manipule que `resolve_provider` et reste
inchangé — c'est ce qui rend la couche réellement agnostique.
"""

from __future__ import annotations

from collections.abc import Callable

from maestro.providers.base import Credentials, ModelProvider, ModelSpec

#: Fabrique un fournisseur prêt à l'emploi à partir de ses credentials.
ProviderFactory = Callable[[Credentials], ModelProvider]

_REGISTRY: dict[str, ProviderFactory] = {}


class UnknownProviderError(KeyError):
    """Levée quand un `ModelSpec.provider` n'a pas d'implémentation enregistrée."""


def register(name: str, factory: ProviderFactory) -> None:
    """Enregistre (ou remplace) la fabrique d'un fournisseur sous `name`."""
    _REGISTRY[name] = factory


def unregister(name: str) -> None:
    """Retire un fournisseur du registre (sans erreur s'il est absent)."""
    _REGISTRY.pop(name, None)


def available_providers() -> list[str]:
    """Liste triée des noms de fournisseurs enregistrés."""
    return sorted(_REGISTRY)


def resolve_provider(spec: ModelSpec, credentials: Credentials) -> ModelProvider:
    """Instancie le fournisseur désigné par `spec`, muni de ses `credentials`.

    Lève `UnknownProviderError` si le fournisseur n'est pas enregistré, et
    `ValueError` si le fournisseur ne prend pas en charge le modèle demandé.
    """
    try:
        factory = _REGISTRY[spec.provider]
    except KeyError as exc:
        known = ", ".join(available_providers()) or "aucun"
        raise UnknownProviderError(
            f"Fournisseur inconnu : {spec.provider!r}. Enregistrés : {known}."
        ) from exc
    provider = factory(credentials)
    if not provider.supports(spec.model):
        raise ValueError(
            f"Le fournisseur {spec.provider!r} ne prend pas en charge le modèle "
            f"{spec.model!r}."
        )
    return provider
