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
from maestro.providers.base import Epreuve, ModelProvider
from maestro.providers.claude import ClaudeProvider
from maestro.providers.openai_compat import OpenAICompatProvider
from maestro.providers.registry import UnknownProviderError

#: Fournisseurs sélectionnables par `MAESTRO_PROVIDER` : nom → construction depuis
#: la config (chaque fournisseur sait dériver ses credentials de ses variables).
_FABRIQUES: Mapping[str, Callable[[Settings], ModelProvider]] = {
    ClaudeProvider.name: ClaudeProvider.from_settings,
    OpenAICompatProvider.name: OpenAICompatProvider.from_settings,
}

#: Les mêmes, par leur classe : leurs accès et leur gamme se lisent dessus sans
#: rien construire (#1609).
_CLASSES: Mapping[str, type[ModelProvider]] = {
    ClaudeProvider.name: ClaudeProvider,
    OpenAICompatProvider.name: OpenAICompatProvider,
}


def classes_configurables() -> Mapping[str, type[ModelProvider]]:
    """Les fournisseurs qu'on peut choisir comme fournisseur par défaut, par nom (#1609)."""
    return _CLASSES


async def eprouver_reglages(settings: Settings, modele: str) -> Epreuve:
    """Construit le fournisseur de `settings` et l'éprouve sur `modele` (#1609).

    Les réglages sont **explicites** : ce sont ceux d'un choix qu'on n'a pas
    encore enregistré, pas ceux du poste. Une configuration que le fournisseur
    refuse de construire (clé absente pour un accès qui en demande une) est un
    verdict, pas une panne.
    """
    try:
        fournisseur = provider_from_settings(settings)
    except ConfigError as exc:
        return Epreuve("authentification", type(exc).__name__)
    return await fournisseur.eprouver(modele)


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
    fournisseur = fabrique(settings)
    # Le modèle imposé voyage avec le fournisseur (#1173) : un canal qui le résout
    # sait quel modèle demander sans relire la configuration.
    fournisseur.modele_configure = settings.model or None
    return fournisseur


def fournisseur_de_l_appel(
    injecte: ModelProvider | None, defaut_modele: str
) -> tuple[ModelProvider, str]:
    """Le fournisseur d'un appel et le modèle à lui demander (#1609).

    Un fournisseur **injecté** (un double, un câblage explicite) sert tel quel,
    avec `defaut_modele`. Sinon c'est celui du poste, **résolu à chaque appel** et
    jamais gardé par l'appelant : un fournisseur choisi dans les Paramètres sert
    donc l'appel suivant, sans redémarrage, et un réglage corrigé aussi. Le
    modèle suit le fournisseur (`modele_du_canal`, #1173).

    Lève ce que lève la résolution (`ConfigError`, `UnknownProviderError`) : à
    l'appelant d'en dire la cause, à sa façon.
    """
    if injecte is not None:
        return injecte, defaut_modele
    fournisseur = provider_from_settings()
    return fournisseur, modele_du_canal(defaut_modele, fournisseur)


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


def modele_du_canal(defaut_claude: str, fournisseur: ModelProvider) -> str:
    """Le modèle d'un appel **que personne n'a réglé** — fil, assistant, génération d'agent (#1173).

    Ces canaux portent un défaut écrit dans le code, et ce défaut est un nom
    Claude (`claude-sonnet-5`). Il était envoyé tel quel au fournisseur configuré,
    quel qu'il soit : avec un endpoint OpenAI-compatible, le fil — seule porte
    d'entrée du produit — demandait `claude-sonnet-5` à un Ollama, et lisait le
    refus comme une indisponibilité passagère. Les runs, eux, suivaient déjà
    `MAESTRO_MODEL` (#69) : seuls ces appels-là y échappaient.

    La règle est celle de `default_model`, lue sur le **fournisseur** et non sur
    la configuration, parce que la fabrique y a posé ce qu'elle imposait :
    `MAESTRO_MODEL` fait foi quand il est posé ; sinon le défaut du canal chez
    Claude, seul fournisseur à savoir ce qu'il désigne ; chez un autre fournisseur
    **connu de la fabrique**, la même erreur de configuration, qui dit quoi
    renseigner. Un fournisseur qu'elle ne connaît pas — un double, un câblage à la
    main — garde le défaut : rien ne permet d'en juger.
    """
    impose = getattr(fournisseur, "modele_configure", None)
    if impose:
        return str(impose)
    nom = getattr(fournisseur, "name", "")
    if nom in _FABRIQUES and nom != ClaudeProvider.name:
        raise ConfigError(
            f"MAESTRO_MODEL est requis avec MAESTRO_PROVIDER={nom!r} "
            "(pas de modèle par défaut pour ce fournisseur). Renseignez un modèle "
            "servi par votre endpoint."
        )
    return defaut_claude
