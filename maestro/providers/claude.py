"""Fournisseur Claude — seule implémentation câblée au POC (ticket #32).

Adapte l'interface `ModelProvider` au **Claude Agent SDK** (`claude_agent_sdk`),
runtime des agents Claude. L'authentification est laissée au SDK (abonnement
Claude Code / clé API) ; le ticket #30 en pilotera la bascule via le slot
`Credentials`.
"""

from __future__ import annotations

from typing import ClassVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

from maestro.config import Settings
from maestro.providers.base import Credentials, ModelProvider
from maestro.providers.registry import register


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

        Au POC, la clé API est optionnelle (auth possible par abonnement Claude
        Code). #30 enrichira cette dérivation avec le mode et la précédence.
        """
        return cls(Credentials(api_key=settings.anthropic_api_key))

    def supports(self, model: str) -> bool:
        return model.startswith(self._MODEL_PREFIX)

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        options = ClaudeAgentOptions(model=model, system_prompt=system_prompt)
        parts: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                parts.extend(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
        return "".join(parts)


# Auto-enregistrement : importer ce module suffit à rendre « claude » résolvable.
register(ClaudeProvider.name, ClaudeProvider)
