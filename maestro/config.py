"""Configuration centrale de Maestro — lecture des variables d'environnement.

Charge le fichier `.env` local (non versionné) puis expose les réglages sous
forme d'un objet `Settings`. Aucun secret n'est écrit en dur ici : la clé API
Anthropic est toujours lue depuis l'environnement (cf. .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Charge .env s'il existe, sans écraser une variable déjà présente dans le shell.
load_dotenv(override=False)


class ConfigError(RuntimeError):
    """Levée quand un réglage requis est absent de l'environnement."""


@dataclass(frozen=True)
class Settings:
    """Réglages dérivés de l'environnement. Immuable.

    `Settings` ne fait que *lire* l'environnement : la politique d'authentification
    (bascule des modes, précédence, validation) vit dans la couche fournisseur
    (`maestro.providers.claude.ClaudeProvider.from_settings`), qui consomme ces
    champs bruts. Cela garde la config sans dépendance sur `maestro.providers`.
    """

    anthropic_api_key: str | None
    anthropic_model: str
    #: Sélecteur brut du mode d'auth Claude (`CLAUDE_AUTH_MODE`), ou None si absent.
    #: Interprété par la couche fournisseur ; None ⇒ déduction (cf. précédence).
    claude_auth_mode: str | None
    #: Token OAuth d'abonnement Claude Code (`CLAUDE_CODE_OAUTH_TOKEN`), pour la CI.
    claude_oauth_token: str | None
    database_url: str | None
    redis_url: str | None

    @classmethod
    def from_env(cls) -> Settings:
        raw_mode = os.getenv("CLAUDE_AUTH_MODE")
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8"),
            claude_auth_mode=raw_mode.strip().lower() if raw_mode and raw_mode.strip() else None,
            claude_oauth_token=os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or None,
            database_url=os.getenv("DATABASE_URL") or None,
            redis_url=os.getenv("REDIS_URL") or None,
        )

    def require_api_key(self) -> str:
        """Renvoie la clé API ou lève `ConfigError` si elle est absente."""
        if not self.anthropic_api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY est absente. Copiez .env.example vers .env "
                "et renseignez votre clé (ne jamais committer le .env)."
            )
        return self.anthropic_api_key


def load_settings() -> Settings:
    """Point d'entrée pratique : `from maestro.config import load_settings`."""
    return Settings.from_env()
