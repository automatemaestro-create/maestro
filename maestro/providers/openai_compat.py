"""Fournisseur compatible OpenAI — premier fournisseur non-Anthropic (ticket #69).

Parle le dialecte « chat completions » de l'API OpenAI (`POST {base}/chat/completions`),
devenu l'interface de fait d'une large part de l'écosystème (OpenAI, Mistral, Groq,
OpenRouter, vLLM, Ollama…) : un seul adaptateur couvre tout endpoint compatible,
choisi par configuration (`OPENAI_BASE_URL`). C'est la contrepartie exécutable de
l'agnosticisme modèle (objectif O7, ENF-11) : la bascule d'un agent sur ce
fournisseur se fait par la config seule, sans toucher au moteur ni à la logique
d'agent.

Le **niveau d'effort** (#253) n'est pas non plus servi ici, et il se refuse
autrement : il n'est pas *refusé*, il est **absent du catalogue** — ce fournisseur
n'annonce aucun effort, l'appelant ne lui en passe donc aucun, et un agent réglé
sur un effort s'exécute ici sans erreur ni réglage. C'est la démonstration du
contrat de `ModelProvider.effort_admis` : ignorer proprement n'est pas une bonne
volonté d'implémentation, c'est ce que la déclaration produit.

Capacités : `generate` (texte), et depuis #1163 `generate_with_images` — les
images d'une source en parties `image_url` du dialecte, l'endpoint jugeant seul si
son modèle les voit. L'exécution *agentique outillée* (`run_agent`) reste refusée
(`UnsupportedCapability`, comportement de la base) et le moteur replie alors sur
le livrable texte. Sans boucle agentique, **aucun
plafond de tours ne s'applique ici** (#239) : le `tours=1` remonté à la télémétrie
mesure l'aller-retour HTTP (un appel = un tour), il ne borne rien.

La **génération par incréments** (`generate_stream`, #693) était héritée de la
frontière — un seul morceau, le texte entier — et #1222 la sert pour de vrai :
`stream: true`, lecture du SSE, `choices[0].delta.content` recollés. C'était bien
le bout de protocole annoncé, et il ne bouge que ce fichier, comme prévu. Ce qui
l'a rendu dû est que le fil de l'orchestrateur streame désormais lui aussi : tant
que seul le chat d'un agent le faisait, un utilisateur d'Ollama voyait une surface
sur trois écrire en direct ; maintenant, ne pas le servir ici ferait **de tous les
fils** de ce fournisseur des fils muets jusqu'au dernier mot.

⚠ `stream_options: {"include_usage": true}` accompagne la demande, et ce n'est pas
le `reasoning_effort` refusé plus haut : celui-ci est **dans le dialecte**, et
sans lui une réponse streamée ne rapporterait aucun token — streamer reviendrait à
rendre l'appel gratuit aux yeux du grand livre (`maestro.telemetry`). Un endpoint
qui ne le connaît pas et le refuse (400) est rattrapé : la demande est **rejouée
une fois sans lui**, ce qui est sûr parce que le refus tombe à l'ouverture du
flux, avant le premier incrément. L'appel nominal, lui, ne paie rien.

L'authentification passe par le slot
`Credentials` : une clé API envoyée en `Bearer` si fournie, sinon aucune en-tête —
le cas des endpoints locaux sans auth (Ollama, vLLM). Un endpoint qui exige une
clé absente répond 401, signalé tel quel.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from time import perf_counter
from typing import Any, ClassVar

import httpx2

from maestro.config import Settings
from maestro.providers.base import (
    AuthMode,
    Credentials,
    ImageJointe,
    ModeleDisponible,
    ModelProvider,
)
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

#: Le préfixe d'une trame SSE du dialecte, et la sentinelle qui clôt le flux.
#: Tout le reste (lignes vides, commentaires `:`, en-têtes d'événement) est ignoré
#: — un flux se lit sur ce qu'on y reconnaît, jamais sur ce qu'on y refuse.
_TRAME = "data:"
_FIN_DE_FLUX = "[DONE]"

#: Ce qui demande le décompte des tokens **sur un flux** (voir le module). Nommé
#: parce qu'il est exactement ce que le repli retire.
_USAGE_EN_FLUX: dict[str, Any] = {"include_usage": True}


class OpenAICompatError(RuntimeError):
    """Levée quand l'endpoint répond en erreur ou hors du dialecte chat completions."""


class _OptionDeFluxRefusee(Exception):
    """L'endpoint a refusé `stream_options` — signal interne, jamais remonté.

    Levée **avant** le premier incrément et nulle part ailleurs : c'est ce qui
    rend le rejeu sans l'option honnête, puisque rien n'a encore été rendu à
    l'appelant et qu'il ne verra donc jamais la même réponse deux fois.
    """


class OpenAICompatProvider(ModelProvider):
    """Adaptateur générique des endpoints compatibles OpenAI, derrière l'abstraction."""

    name: ClassVar[str] = "openai"

    #: Aucune gamme annoncée, et **des noms libres** (#253) : ce dialecte fédère
    #: des endpoints choisis par configuration (`OPENAI_BASE_URL`) dont les
    #: nommages n'ont rien de commun — `gpt-*` ici, `llama3:8b` sur un Ollama
    #: local, `org/modele` sur un routeur. Les deux attributs se lisent ensemble :
    #: gamme vide **et** libre veut dire « saisis le nom que sert ton endpoint »,
    #: là où une gamme vide et fermée voudrait dire « rien à proposer ».
    MODELES: ClassVar[tuple[ModeleDisponible, ...]] = ()
    MODELES_LIBRES: ClassVar[bool] = True

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
        effort: str | None = None,
    ) -> str:
        """Appel modèle texte : un tour de chat completions, sans outil ni streaming.

        `effort` (#253) est **accepté et ignoré**, et c'est le comportement voulu :
        ce fournisseur n'annonce aucun effort (`MODELES` vide), donc l'appelant ne
        lui en transmet jamais un — le paramètre n'est ici que pour honorer la
        signature de la frontière, et pour qu'un appel direct qui en passerait un
        n'échoue pas. Rien n'est ajouté au corps envoyé à l'endpoint : quelques
        implémentations du dialecte exposent un `reasoning_effort`, la plupart
        non, et un champ inconnu se solde par un 400 — envoyer au hasard
        casserait l'usage nominal (un Ollama local) pour servir l'exception.
        """
        return await self._appeler(self._corps(prompt, model, system_prompt), model)

    async def generate_with_images(
        self,
        prompt: str,
        *,
        images: Sequence[ImageJointe],
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        """`generate`, les images jointes au message : le dialecte les porte en `image_url` (#1163).

        Le contenu du message utilisateur devient une liste de parties — le texte,
        puis chaque image en URL `data:` —, forme que le dialecte chat completions
        définit pour la vision et que reprennent les endpoints compatibles qui la
        servent (Ollama, vLLM, OpenRouter…). **Aucune liste de modèles « qui
        voient »** n'est tenue ici : ce fournisseur fédère des endpoints dont il ne
        peut préjuger (cf. `supports`), et c'est l'endpoint qui juge. Un modèle
        qui ne voit pas répond en erreur, et cette erreur remonte telle quelle —
        la lecture des sources la nomme au rapport, avec l'extrait que l'endpoint
        a rendu.
        """
        corps = self._corps(prompt, model, system_prompt)
        corps["messages"][-1]["content"] = [
            {"type": "text", "text": prompt},
            *(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.type_media};base64,"
                        + base64.b64encode(image.octets).decode("ascii")
                    },
                }
                for image in images
            ),
        ]
        return await self._appeler(corps, model)

    async def _appeler(self, corps: Mapping[str, Any], model: str) -> str:
        """Un tour de chat completions sans flux : la réponse, et son usage rapporté."""
        debut = perf_counter()
        async with httpx2.AsyncClient(timeout=_TIMEOUT_S) as client:
            try:
                reponse = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=dict(corps),
                    headers=self._entetes(),
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

    async def generate_stream(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Le même appel, rendu **par incréments** : `stream: true` du dialecte (#1222).

        Les morceaux sont ceux que l'endpoint découpe — `choices[0].delta.content`
        des trames SSE, dans l'ordre où elles arrivent —, jamais un recoupage de
        notre fait : l'invariant de la frontière (leur concaténation *est* ce que
        `generate` aurait rendu) se tient en ne touchant à rien.

        Le décompte d'usage est demandé avec le flux et **rejoué sans lui** si
        l'endpoint le refuse (voir le module). Une trame illisible est ignorée
        plutôt que fatale : un flux à demi reçu vaut mieux qu'une réponse perdue
        sur une ligne de tenue de protocole, et ce qui casse vraiment — connexion,
        statut — lève comme sur `generate`.

        `effort` est accepté et ignoré, pour la raison qui vaut sur `generate`.
        """
        corps = self._corps(prompt, model, system_prompt) | {"stream": True}
        try:
            async for morceau in self._diffuser(
                corps | {"stream_options": dict(_USAGE_EN_FLUX)}, model, repliable=True
            ):
                yield morceau
            return
        except _OptionDeFluxRefusee:
            # L'endpoint ne connaît pas l'option : rien n'est encore parti, on
            # redemande le même flux sans elle. Les tokens seront inconnus, la
            # réponse non.
            pass
        async for morceau in self._diffuser(corps, model, repliable=False):
            yield morceau

    async def _diffuser(
        self, corps: Mapping[str, Any], model: str, *, repliable: bool
    ) -> AsyncIterator[str]:
        """Une tentative de flux : les incréments, puis l'usage rapporté.

        `repliable` dit si un refus à l'ouverture doit devenir
        `_OptionDeFluxRefusee` plutôt qu'une erreur : seul l'appel qui demande
        `stream_options` a un second essai qui l'attend.
        """
        debut = perf_counter()
        usage: Any = None
        async with httpx2.AsyncClient(timeout=_TIMEOUT_S) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=dict(corps),
                    headers=self._entetes(),
                ) as reponse:
                    if reponse.status_code >= 400:
                        # Le corps n'est pas lu par `stream` : sans cette lecture,
                        # l'extrait cité dans l'erreur serait vide.
                        await reponse.aread()
                        if repliable and reponse.status_code == 400:
                            raise _OptionDeFluxRefusee
                        reponse.raise_for_status()
                    async for ligne in reponse.aiter_lines():
                        trame = _trame_lue(ligne)
                        if trame is None:
                            continue
                        morceau, vu = _increment_et_usage(trame)
                        if vu is not None:
                            usage = vu
                        if morceau:
                            yield morceau
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
        report_usage(_usage_depuis({"usage": usage}, int((perf_counter() - debut) * 1000)))

    def _corps(
        self, prompt: str, model: str, system_prompt: str | None
    ) -> dict[str, Any]:
        """Le corps commun aux appels — un seul endroit où le dialecte s'écrit."""
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return {"model": model, "messages": messages}

    def _entetes(self) -> dict[str, str]:
        """L'en-tête d'auth, ou rien — le cas des endpoints locaux (Ollama, vLLM)."""
        if self._credentials.api_key:
            return {"Authorization": f"Bearer {self._credentials.api_key}"}
        return {}


def _trame_lue(ligne: str) -> Any:
    """L'objet d'une trame SSE, ou `None` s'il n'y a rien à en tirer.

    Tout ce qui n'est pas une `data:` porteuse d'un objet est ignoré : lignes
    vides de séparation, commentaires de maintien de connexion (`: ping`),
    sentinelle `[DONE]`, et trame illisible. C'est une lecture **positive** — on
    reconnaît ce qu'on sait lire —, ce qui laisse un endpoint enrichir son flux
    sans casser le nôtre.
    """
    nu = ligne.strip()
    if not nu.startswith(_TRAME):
        return None
    charge = nu[len(_TRAME) :].strip()
    if not charge or charge == _FIN_DE_FLUX:
        return None
    try:
        return json.loads(charge)
    except ValueError:
        return None


def _increment_et_usage(trame: Any) -> tuple[str, Any]:
    """Ce qu'une trame apporte : son morceau de texte, et son bloc `usage` éventuel.

    Les deux ensemble parce qu'une même trame peut porter l'un, l'autre ou les
    deux — la trame d'usage du dialecte arrive en dernier, souvent avec un
    `choices` vide. Un `delta` sans `content` (un rôle, un appel d'outil) ne rend
    rien : ce n'est pas du texte à afficher.
    """
    if not isinstance(trame, Mapping):
        return "", None
    usage = trame.get("usage")
    choix = trame.get("choices")
    if not isinstance(choix, list) or not choix:
        return "", usage
    delta = choix[0].get("delta") if isinstance(choix[0], Mapping) else None
    contenu = delta.get("content") if isinstance(delta, Mapping) else None
    return contenu if isinstance(contenu, str) else "", usage


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
