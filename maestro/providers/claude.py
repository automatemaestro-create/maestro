"""Fournisseur Claude — seule implémentation câblée au POC (tickets #32 puis #30).

Adapte l'interface `ModelProvider` au **Claude Agent SDK** (`claude_agent_sdk`),
runtime des agents Claude. #30 y branche les **deux modes d'authentification** —
clé API ou abonnement Claude Code — sélectionnables par configuration, via le seul
slot `Credentials` (pas de mécanisme d'auth parallèle).

Le SDK lance le CLI Claude Code en sous-processus ; on pilote donc l'auth par les
variables d'environnement que le CLI reconnaît, injectées via `ClaudeAgentOptions.env`.
Ordre de précédence du CLI (du plus fort au plus faible) utilisé ici :

    2. ANTHROPIC_AUTH_TOKEN  (bearer, passerelles)
    3. ANTHROPIC_API_KEY     (clé API ; en mode non-interactif — celui du SDK —
                              toujours utilisée si présente)
    5. CLAUDE_CODE_OAUTH_TOKEN (token d'abonnement longue durée, CI)
    6. abonnement Claude Code via `claude` (défaut Pro/Max)

`options.env` étant *fusionné par-dessus* l'environnement hérité, il ne permet pas
de *supprimer* une variable : on neutralise donc explicitement les concurrents de
rang supérieur en les mettant à la chaîne vide (traitée comme « non définie » par
le CLI — vérifié), afin que le mode choisi l'emporte quel que soit l'environnement
ambiant.
"""

from __future__ import annotations

from typing import ClassVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

from maestro.config import ConfigError, Settings
from maestro.providers.base import AuthMode, Credentials, ModelProvider
from maestro.providers.registry import register


def _resolve_auth_mode(settings: Settings) -> AuthMode:
    """Applique la règle de précédence pour déterminer le mode d'authentification.

    1. `CLAUDE_AUTH_MODE` explicite (`api_key`/`subscription`) l'emporte.
    2. Sinon, déduction : une clé API présente ⇒ `api_key` ; sinon `subscription`
       (défaut du POC, où l'on part de l'abonnement Claude Code).
    """
    raw = settings.claude_auth_mode
    if raw:
        try:
            return AuthMode(raw)
        except ValueError as exc:
            valid = ", ".join(m.value for m in AuthMode)
            raise ConfigError(
                f"CLAUDE_AUTH_MODE invalide : {raw!r}. Valeurs acceptées : {valid}."
            ) from exc
    return AuthMode.API_KEY if settings.anthropic_api_key else AuthMode.SUBSCRIPTION


class ClaudeProvider(ModelProvider):
    """Runtime des agents Claude, derrière la couche d'abstraction."""

    name: ClassVar[str] = "claude"

    #: Préfixe des identifiants de modèles Claude (ex. `claude-opus-4-8`).
    _MODEL_PREFIX: ClassVar[str] = "claude-"

    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials

    @property
    def credentials(self) -> Credentials:
        """Credentials injectés à la construction (slot alimenté par #30)."""
        return self._credentials

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeProvider:
        """Construit le fournisseur en dérivant les credentials de la config.

        Applique la bascule des modes (`_resolve_auth_mode`) puis valide le mode
        retenu : `api_key` exige `ANTHROPIC_API_KEY` ; `subscription` n'exige rien
        (auth par abonnement Claude Code, ou `CLAUDE_CODE_OAUTH_TOKEN` en CI).
        """
        mode = _resolve_auth_mode(settings)
        if mode is AuthMode.API_KEY and not settings.anthropic_api_key:
            raise ConfigError(
                "Mode d'authentification 'api_key' sélectionné mais ANTHROPIC_API_KEY "
                "est absente. Renseignez la clé, ou passez CLAUDE_AUTH_MODE=subscription "
                "pour utiliser l'abonnement Claude Code."
            )
        return cls(
            Credentials(
                auth_mode=mode,
                api_key=settings.anthropic_api_key,
                oauth_token=settings.claude_oauth_token,
            )
        )

    def supports(self, model: str) -> bool:
        return model.startswith(self._MODEL_PREFIX)

    def _auth_env(self) -> dict[str, str]:
        """Traduit les `Credentials` en variables d'environnement pour le CLI.

        Neutralise (chaîne vide == non défini) les credentials concurrents de rang
        supérieur pour garantir le mode choisi malgré un environnement ambiant.
        """
        creds = self._credentials
        if creds.auth_mode is AuthMode.API_KEY:
            # On vise le rang 3 : neutraliser le bearer (rang 2), seul concurrent
            # supérieur plausiblement présent dans l'environnement.
            return {
                "ANTHROPIC_AUTH_TOKEN": "",
                "ANTHROPIC_API_KEY": creds.api_key or "",
            }
        # SUBSCRIPTION : neutraliser bearer (rang 2) et clé API (rang 3) pour laisser
        # le CLI retomber sur l'abonnement Claude Code (rang 6) — ou sur un token
        # OAuth explicite (rang 5, CI) s'il est fourni.
        env = {"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""}
        if creds.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = creds.oauth_token
        return env

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            env=self._auth_env(),
        )
        parts: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                parts.extend(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
        return "".join(parts)


# Auto-enregistrement : importer ce module suffit à rendre « claude » résolvable.
register(ClaudeProvider.name, ClaudeProvider)
