"""Couche d'abstraction fournisseur — l'interface `ModelProvider` (ticket #32).

Cette frontière isole le *moteur d'agents* du *fournisseur d'IA* : le moteur ne
connaît que `ModelProvider` et un `ModelSpec` (`fournisseur + modèle`). Les
secrets d'authentification passent par le slot `Credentials`, point d'injection
défini ici et alimenté par le ticket #30 pour Claude.

Ajouter un fournisseur = sous-classer `ModelProvider` puis l'enregistrer dans le
registre (voir `maestro.providers.registry`) — sans toucher au moteur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar


class UnsupportedCapability(RuntimeError):
    """Levée quand un fournisseur ne sait pas honorer une capacité *optionnelle*.

    Le POC n'exige de tout fournisseur que `generate` (texte). L'exécution
    *agentique outillée* (`run_agent`) est une capacité **native de l'Agent SDK**
    (donc de Claude) : un fournisseur qui n'en dispose pas la refuse explicitement
    plutôt que de la simuler. Le moteur peut ainsi *tester* la capacité sans présumer
    du fournisseur.
    """


class AuthMode(StrEnum):
    """Mode d'authentification d'un fournisseur — la « bascule » du ticket #30.

    - ``SUBSCRIPTION`` (défaut) : authentification par **abonnement** (OAuth), sans
      clé API. Pour Claude, c'est l'abonnement Claude Code — point de départ du POC.
    - ``API_KEY`` : authentification par **clé API** (facturation à l'usage).
    """

    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True)
class Credentials:
    """Point d'injection des secrets d'authentification d'un fournisseur.

    #32 a défini le *slot* (la frontière) ; #30 y branche la **bascule** entre les
    deux `AuthMode` et la règle de précédence (côté config → cf.
    `claude.ClaudeProvider.from_settings`). Le mode `SUBSCRIPTION` ne requiert
    aucune clé : le fournisseur Claude s'authentifie alors via l'abonnement Claude
    Code (OAuth de l'Agent SDK) ou un `oauth_token` explicite (utile en CI). Le
    mode `API_KEY` exige `api_key` — l'invariant est vérifié à la construction.
    """

    auth_mode: AuthMode = AuthMode.SUBSCRIPTION
    api_key: str | None = None
    oauth_token: str | None = None

    def __post_init__(self) -> None:
        if self.auth_mode is AuthMode.API_KEY and not self.api_key:
            raise ValueError(
                "Mode d'authentification 'api_key' sans clé : renseignez `api_key`, "
                "ou choisissez le mode 'subscription'."
            )


@dataclass(frozen=True)
class ModelSpec:
    """Sélection `fournisseur + modèle` d'un agent (partie versionnable du contrat).

    Volontairement dépourvu de secret : un `ModelSpec` peut être stocké et
    versionné (cf. playbooks en base) sans jamais transporter de credentials —
    ceux-ci passent par `Credentials`, injecté séparément.
    """

    provider: str
    model: str


class ModelProvider(ABC):
    """Frontière entre le moteur d'agents et un fournisseur d'IA.

    Le moteur ne dépend que de cette interface : il résout un fournisseur par son
    nom (`ModelSpec.provider`), lui confie un modèle + un prompt, et récupère le
    texte de la réponse. Sont câblés : Claude (`claude.ClaudeProvider`) et tout
    endpoint compatible OpenAI (`openai_compat.OpenAICompatProvider`, #69).
    """

    #: Nom stable du fournisseur, tel que référencé par `ModelSpec.provider`.
    name: ClassVar[str]

    @abstractmethod
    def supports(self, model: str) -> bool:
        """Le fournisseur sait-il servir ce modèle ?"""
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        """Exécute un appel modèle et renvoie le texte assemblé de la réponse.

        C'est la couture minimale du POC ; elle grandira (streaming, outils,
        sous-agents) sans changer la nature de la frontière.
        """
        raise NotImplementedError

    async def run_agent(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        workspace: Path,
        tools: Sequence[str],
    ) -> str:
        """Exécution *agentique outillée* : renvoie le compte-rendu final de l'agent.

        Le modèle dispose des outils `tools` (fichiers, shell…) et travaille dans
        `workspace`, son **répertoire de travail isolé** (cf. `maestro.sandbox`), où
        il produit un livrable concret (des fichiers). Là où `generate` rend du texte,
        `run_agent` *agit* dans un espace dédié.

        Capacité **optionnelle** : la base la refuse (`UnsupportedCapability`) ; un
        fournisseur outillé (Claude via l'Agent SDK) la surcharge. Le moteur reste
        agnostique — il teste la capacité, il ne présume pas du fournisseur.
        """
        raise UnsupportedCapability(
            f"Le fournisseur {self.name!r} n'expose pas d'exécution agentique outillée."
        )
