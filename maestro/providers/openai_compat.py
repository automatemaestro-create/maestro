"""Fournisseur compatible OpenAI — premier fournisseur non-Anthropic (ticket #69).

Parle le dialecte « chat completions » de l'API OpenAI (`POST {base}/chat/completions`),
devenu l'interface de fait d'une large part de l'écosystème (OpenAI, Mistral, Groq,
OpenRouter, vLLM, Ollama…) : un seul adaptateur couvre tout endpoint compatible,
choisi par configuration (`OPENAI_BASE_URL`). C'est la contrepartie exécutable de
l'agnosticisme modèle (objectif O7, ENF-11) : la bascule d'un agent sur ce
fournisseur se fait par la config seule, sans toucher au moteur ni à la logique
d'agent.

Capacités : `generate` (texte) seulement — l'exécution *agentique outillée*
(`run_agent`) reste refusée (`UnsupportedCapability`, comportement de la base) et
le moteur replie alors sur le livrable texte. L'authentification passe par le slot
`Credentials` : une clé API envoyée en `Bearer` si fournie, sinon aucune en-tête —
le cas des endpoints locaux sans auth (Ollama, vLLM). Un endpoint qui exige une
clé absente répond 401, signalé tel quel.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, ClassVar

import httpx2

from maestro.config import Settings
from maestro.providers.base import AuthMode, Credentials, ModelProvider
from maestro.providers.registry import register
from maestro.telemetry import StepUsage, report_usage

#: Endpoint OpenAI officiel — tout autre fournisseur compatible se choisit par
#: `OPENAI_BASE_URL` (cf. `from_settings`).
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Délai maximal d'un appel (connexion + réponse complète) : une génération longue
#: dépasse largement le défaut du client HTTP (5 s).
_TIMEOUT_S = 120.0

#: Longueur maximale du corps d'erreur cité dans les exceptions (lisible sans noyer).
_EXTRAIT_ERREUR = 300


class OpenAICompatError(RuntimeError):
    """Levée quand l'endpoint répond en erreur ou hors du dialecte chat completions."""


class OpenAICompatProvider(ModelProvider):
    """Adaptateur générique des endpoints compatibles OpenAI, derrière l'abstraction."""

    name: ClassVar[str] = "openai"

    def __init__(self, credentials: Credentials, *, base_url: str = DEFAULT_BASE_URL) -> None:
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")

    @property
    def credentials(self) -> Credentials:
        """Credentials injectés à la construction (même slot que les autres fournisseurs)."""
        return self._credentials

    @property
    def base_url(self) -> str:
        """Racine de l'endpoint interrogé (sans `/` final)."""
        return self._base_url

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatProvider:
        """Construit le fournisseur depuis la config (`OPENAI_API_KEY`, `OPENAI_BASE_URL`).

        La clé est optionnelle : sans elle, l'appel part sans en-tête d'auth —
        l'usage nominal d'un endpoint local (Ollama, vLLM). Pas de mode
        « abonnement » ici : ce dialecte ne connaît que la clé API (ou rien).
        """
        if settings.openai_api_key:
            credentials = Credentials(auth_mode=AuthMode.API_KEY, api_key=settings.openai_api_key)
        else:
            credentials = Credentials()
        return cls(credentials, base_url=settings.openai_base_url)

    def supports(self, model: str) -> bool:
        # Le dialecte fédère des fournisseurs aux nommages hétéroclites (`gpt-*`,
        # `mistral-*`, `llama3:8b`, `org/modele`…) : impossible d'en préjuger ici.
        # Tout nom non vide est accepté — l'endpoint fait foi et refusera le reste.
        return bool(model.strip())

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        """Appel modèle texte : un tour de chat completions, sans outil ni streaming."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers: dict[str, str] = {}
        if self._credentials.api_key:
            headers["Authorization"] = f"Bearer {self._credentials.api_key}"

        debut = perf_counter()
        async with httpx2.AsyncClient(timeout=_TIMEOUT_S) as client:
            try:
                reponse = await client.post(
                    f"{self._base_url}/chat/completions",
                    json={"model": model, "messages": messages},
                    headers=headers,
                )
                reponse.raise_for_status()
            except httpx2.HTTPStatusError as exc:
                extrait = exc.response.text[:_EXTRAIT_ERREUR]
                raise OpenAICompatError(
                    f"L'endpoint {self._base_url} a répondu {exc.response.status_code} "
                    f"pour le modèle {model!r} : {extrait}"
                ) from exc
            except httpx2.HTTPError as exc:
                raise OpenAICompatError(
                    f"Endpoint {self._base_url} injoignable : {exc}"
                ) from exc
        duree_ms = int((perf_counter() - debut) * 1000)

        try:
            payload = reponse.json()
        except ValueError as exc:
            raise OpenAICompatError(
                f"L'endpoint {self._base_url} n'a pas répondu en JSON : "
                f"{reponse.text[:_EXTRAIT_ERREUR]}"
            ) from exc
        texte = _premier_contenu(payload, self._base_url)
        report_usage(_usage_depuis(payload, duree_ms))
        return texte


def _premier_contenu(payload: Any, base_url: str) -> str:
    """Extrait le texte du premier choix, ou lève si la forme n'est pas celle attendue."""
    try:
        contenu = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAICompatError(
            f"Réponse de {base_url} hors du dialecte chat completions "
            "(pas de `choices[0].message.content`)."
        ) from exc
    if not isinstance(contenu, str):
        raise OpenAICompatError(
            f"Réponse de {base_url} sans contenu textuel (content={type(contenu).__name__})."
        )
    return contenu


def _usage_depuis(payload: Any, duree_ms: int) -> StepUsage:
    """Traduit le bloc `usage` de la réponse en mesure d'usage d'un appel modèle.

    Le dialecte ne rapporte pas de coût : `cout_usd` reste None (inconnu, pas nul).
    `duree_api_ms` est la durée mesurée de l'aller-retour HTTP — le proxy le plus
    proche du temps API, qu'aucun champ de réponse ne porte ici.
    """
    usage = payload.get("usage") or {} if isinstance(payload, dict) else {}
    return StepUsage(
        appels=1,
        tokens_entree=int(usage.get("prompt_tokens") or 0),
        tokens_sortie=int(usage.get("completion_tokens") or 0),
        cout_usd=None,
        duree_api_ms=duree_ms,
        tours=1,
    )


# Auto-enregistrement : importer ce module suffit à rendre « openai » résolvable.
# La fabrique du registre vise l'endpoint OpenAI officiel ; un autre endpoint
# compatible se choisit par la config (`from_settings` → OPENAI_BASE_URL).
register(OpenAICompatProvider.name, OpenAICompatProvider)
