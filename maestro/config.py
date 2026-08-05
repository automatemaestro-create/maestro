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
    #: Adresse gRPC du serveur Temporal (`TEMPORAL_ADDRESS`) — moteur des workflows
    #: durables (#94/#95). Défaut : l'instance locale du docker-compose
    #: (localhost:7233, cf. infra/README.md).
    temporal_address: str = "localhost:7233"
    #: Fournisseur de modèles de l'exécution (`MAESTRO_PROVIDER`) : nom d'un
    #: fournisseur configurable (cf. `maestro.providers.factory`). Défaut : `claude`.
    provider: str = "claude"
    #: Modèle unique imposé à tous les rôles (`MAESTRO_MODEL`), ou None : chaque
    #: rôle garde alors son modèle par défaut (catalogue/profils du POC).
    model: str | None = None
    #: Racine du stockage versionné des playbooks (`MAESTRO_PLAYBOOKS_DIR`), ou
    #: None : le dossier `core/playbooks/` du dépôt (cf. maestro.agents.playbooks, #76).
    playbooks_dir: str | None = None
    #: Racine du dépôt des agents personnalisés (`MAESTRO_AGENTS_DIR`), ou
    #: None : le dossier `core/agents/` du dépôt (cf. maestro.agents.store, #72).
    agents_dir: str | None = None
    #: Racine des fils de chat utilisateur ↔ agent (`MAESTRO_CHAT_DIR`), ou
    #: None : le dossier `core/chat/` du dépôt (cf. maestro.controltower.chat, #84).
    chat_dir: str | None = None
    #: Racine du dépôt des capacités d'agents (`MAESTRO_CAPACITE_DIR`), ou
    #: None : le dossier `core/capacite/` du dépôt (cf. maestro.agents.capacity, #86).
    capacite_dir: str | None = None
    #: Racine du dépôt des serveurs MCP déclarés par agent (`MAESTRO_MCP_DIR`), ou
    #: None : le dossier `core/mcp/` du dépôt (cf. maestro.agents.mcp, #104).
    mcp_dir: str | None = None
    #: Racine du coffre des secrets par agent (`MAESTRO_SECRETS_DIR`), ou
    #: None : le dossier `core/secrets/` du dépôt (cf. maestro.agents.secrets,
    #: #109 — jamais versionné, scoping actif dès que la racine existe).
    secrets_dir: str | None = None
    #: Clé maîtresse de chiffrement au repos des secrets du coffre
    #: (`MAESTRO_SECRETS_KEY`, clé Fernet urlsafe base64), ou None : le coffre
    #: gère une clé locale auto-générée (`<racine>/.cle`, gitignorée) — le repli
    #: du POC (cf. maestro.agents.chiffrement, #132). Côté serveur, hors du dépôt.
    secrets_key: str | None = None
    #: Racine du dépôt des politiques de permissions par agent
    #: (`MAESTRO_PERMISSIONS_DIR`), ou None : le dossier `core/permissions/` du
    #: dépôt (cf. maestro.agents.permissions, #110 — allow/deny par outil).
    permissions_dir: str | None = None
    #: Racine du dépôt des projets déclarés (`MAESTRO_PROJETS_DIR`), ou None :
    #: le dossier `core/projets/` du dépôt (cf. maestro.projets.store, #221 —
    #: un fichier <id>.json par projet : racine sur le disque, origine, vcs,
    #: périmètre). Ce sont les projets de l'utilisateur, jamais versionnés.
    projets_dir: str | None = None
    #: Mode d'isolation des exécutions outillées (`MAESTRO_ISOLATION`), ou None :
    #: exécution directe sur l'hôte (défaut). Seule valeur reconnue : `conteneur`.
    #: Interprété et validé par la couche sandbox (maestro.sandbox.container, #108).
    isolation: str | None = None
    #: Image Docker du mode isolé (`MAESTRO_ISOLATION_IMAGE`), ou None : l'image
    #: dédiée par défaut (`maestro-sandbox:latest`, construite depuis infra/sandbox/).
    isolation_image: str | None = None
    #: Réseau Docker du mode isolé (`MAESTRO_ISOLATION_RESEAU`), ou None : `bridge`
    #: (sortant seul, aucun port publié). `none` coupe tout réseau (diagnostic).
    isolation_reseau: str | None = None
    #: Canal Slack des notifications de supervision (`MAESTRO_SLACK_CANAL`), ou
    #: None : le notificateur de run (#105, maestro.supervision) refuse alors de
    #: se construire — pas de canal, pas de notification.
    slack_canal: str | None = None
    #: Clé API de l'endpoint compatible OpenAI (`OPENAI_API_KEY`), ou None :
    #: aucune en-tête d'auth (endpoints locaux type Ollama/vLLM).
    openai_api_key: str | None = None
    #: Racine de l'endpoint compatible OpenAI (`OPENAI_BASE_URL`) — l'endpoint
    #: OpenAI officiel par défaut, remplaçable par tout endpoint au même dialecte.
    openai_base_url: str = "https://api.openai.com/v1"
    #: Clés du projet Langfuse (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`), ou
    #: None : export de traces désactivé — l'intégration est optionnelle (#81).
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    #: Hôte Langfuse (`LANGFUSE_HOST`) — le cloud par défaut, remplaçable par une
    #: instance auto-hébergée.
    langfuse_host: str = "https://cloud.langfuse.com"

    @classmethod
    def from_env(cls) -> Settings:
        raw_mode = os.getenv("CLAUDE_AUTH_MODE")
        raw_provider = os.getenv("MAESTRO_PROVIDER")
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5"),
            claude_auth_mode=raw_mode.strip().lower() if raw_mode and raw_mode.strip() else None,
            claude_oauth_token=os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or None,
            database_url=os.getenv("DATABASE_URL") or None,
            redis_url=os.getenv("REDIS_URL") or None,
            temporal_address=(os.getenv("TEMPORAL_ADDRESS") or "").strip() or "localhost:7233",
            provider=(raw_provider.strip().lower() if raw_provider else "") or "claude",
            model=(os.getenv("MAESTRO_MODEL") or "").strip() or None,
            playbooks_dir=(os.getenv("MAESTRO_PLAYBOOKS_DIR") or "").strip() or None,
            agents_dir=(os.getenv("MAESTRO_AGENTS_DIR") or "").strip() or None,
            chat_dir=(os.getenv("MAESTRO_CHAT_DIR") or "").strip() or None,
            capacite_dir=(os.getenv("MAESTRO_CAPACITE_DIR") or "").strip() or None,
            mcp_dir=(os.getenv("MAESTRO_MCP_DIR") or "").strip() or None,
            secrets_dir=(os.getenv("MAESTRO_SECRETS_DIR") or "").strip() or None,
            secrets_key=(os.getenv("MAESTRO_SECRETS_KEY") or "").strip() or None,
            permissions_dir=(os.getenv("MAESTRO_PERMISSIONS_DIR") or "").strip() or None,
            projets_dir=(os.getenv("MAESTRO_PROJETS_DIR") or "").strip() or None,
            isolation=(os.getenv("MAESTRO_ISOLATION") or "").strip().lower() or None,
            isolation_image=(os.getenv("MAESTRO_ISOLATION_IMAGE") or "").strip() or None,
            isolation_reseau=(os.getenv("MAESTRO_ISOLATION_RESEAU") or "").strip().lower()
            or None,
            slack_canal=(os.getenv("MAESTRO_SLACK_CANAL") or "").strip() or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1",
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY") or None,
            langfuse_host=os.getenv("LANGFUSE_HOST", "").strip()
            or "https://cloud.langfuse.com",
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
