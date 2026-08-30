"""Registre des fournisseurs de modèles (ticket #32).

Fait le lien `nom de fournisseur → implémentation`. Ajouter un fournisseur se
résume à l'enregistrer ici (idéalement, à ce qu'il s'enregistre lui-même à
l'import) : le moteur d'agents, lui, ne manipule que `resolve_provider` et reste
inchangé — c'est ce qui rend la couche réellement agnostique.

Depuis #253 le registre répond aussi à la question de l'UI — « qu'est-ce qui
existe ? » — par `catalogue_fournisseurs()` : fournisseurs, modèles annoncés et
efforts admis, servis tels quels par `GET /api/fournisseurs`. Le même geste
suffit donc pour les deux moitiés : un fournisseur inscrit ici devient
*résolvable* **et** *proposable*, sans qu'aucune liste ne soit recopiée dans le
front.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from maestro.providers.base import (
    Credentials,
    FournisseurDisponible,
    ModelProvider,
    ModelSpec,
)

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


def catalogue_fournisseurs() -> tuple[FournisseurDisponible, ...]:
    """Le catalogue servi par l'API (#253) : chaque fournisseur enregistré et sa gamme.

    C'est **la** source de `GET /api/fournisseurs`, et la raison pour laquelle
    aucune liste de fournisseurs ni de modèles n'est recopiée dans le front :
    inscrire un fournisseur au registre suffit à l'y faire apparaître. La gamme
    est lue sur la fabrique enregistrée quand celle-ci sait la déclarer — c'est
    le cas nominal, les fournisseurs s'enregistrant par leur **classe**
    (`register(ClaudeProvider.name, ClaudeProvider)`), et `ModelProvider.catalogue`
    répond alors sans construire quoi que ce soit : ni credentials, ni réseau, ni
    configuration.

    Une fabrique qui ne déclare rien — une fermeture posée par un test, un
    fournisseur branché à la volée — est rendue **avec une gamme vide** plutôt
    qu'omise : elle est enregistrée, donc résolvable par `ModelSpec`, donc elle
    existe pour qui appelle. Taire un fournisseur utilisable serait le seul vrai
    mensonge de cette vue.

    Trié par nom, comme `available_providers` : l'ordre d'enregistrement dépend
    de l'ordre des imports, et une liste servie à une UI ne doit pas en dépendre.
    """
    fiches: list[FournisseurDisponible] = []
    for nom in available_providers():
        fabrique = _REGISTRY[nom]
        lecture = getattr(fabrique, "catalogue", None)
        fiche = lecture() if callable(lecture) else None
        if not isinstance(fiche, FournisseurDisponible):
            fiche = FournisseurDisponible(nom=nom)
        # Le registre fait foi sur le NOM : une fabrique enregistrée sous un autre
        # nom que celui de sa classe est résolue par sa clé, et c'est cette clé
        # qu'un client devra écrire dans `fournisseur`.
        fiches.append(fiche if fiche.nom == nom else replace(fiche, nom=nom))
    return tuple(fiches)


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
