"""L'orchestrateur : objectif → liste de tâches JSON validées (ticket #3).

Pièce maîtresse qui relie les trois autres modules : elle envoie l'objectif au
modèle **via la couche d'abstraction fournisseur** (`ModelProvider`, ticket #32),
extrait le tableau JSON de la réponse, puis le valide contre le schéma partagé.

Le cœur (`plan`) ne dépend que de `ModelProvider` : il reste **agnostique du
fournisseur**. Le raccourci `Orchestrator.default` résout fournisseur et modèle
depuis la config (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`, #69), sans polluer le cœur.
"""

from __future__ import annotations

import json
import re
from typing import Any

from maestro.config import Settings, load_settings
from maestro.orchestrator.errors import PlanParsingError
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT, build_user_prompt
from maestro.orchestrator.schema import Task, validate_plan
from maestro.providers.base import ModelProvider

# Bloc de code Markdown éventuel autour du JSON (```json … ``` ou ``` … ```).
_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


class Orchestrator:
    """Transforme un objectif en langage naturel en une liste de `Task` validées."""

    def __init__(self, provider: ModelProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    @classmethod
    def default(cls, settings: Settings | None = None) -> Orchestrator:
        """Orchestrateur par défaut : fournisseur et modèle issus de la config (#69).

        Importe la fabrique ici (et non en tête de module) pour ne pas lier le cœur
        agnostique à un fournisseur concret : le choix vit dans la config
        (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`), plus dans le code.
        """
        from maestro.providers.factory import default_model, provider_from_settings

        settings = settings or load_settings()
        return cls(provider_from_settings(settings), model=default_model(settings))

    async def plan(self, objective: str) -> list[Task]:
        """Produit le plan de tâches pour `objective`.

        Lève `ValueError` si l'objectif est vide, `PlanParsingError` si la réponse
        du modèle n'est pas un tableau JSON exploitable, `TaskValidationError` si le
        plan enfreint le schéma ou les règles inter-tâches.
        """
        if not objective or not objective.strip():
            raise ValueError("L'objectif est vide.")

        response = await self._provider.generate(
            build_user_prompt(objective),
            model=self._model,
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        )
        raw_tasks = _extract_task_array(response)
        return validate_plan(raw_tasks)


def _extract_task_array(text: str) -> list[dict[str, Any]]:
    """Extrait le tableau de tâches de la réponse brute du modèle.

    Tolérant aux enrobages courants : JSON pur, bloc de code Markdown, objet
    enveloppe (`{"taches": [...]}`), ou prose entourant le tableau. Renvoie une
    liste d'objets ; lève `PlanParsingError` si rien d'exploitable n'est trouvé.
    """
    payload = _loads_first_json(text)
    tasks = _unwrap_tasks(payload)
    if not isinstance(tasks, list):
        raise PlanParsingError(
            "La réponse du modèle ne contient pas un tableau JSON de tâches."
        )
    for item in tasks:
        if not isinstance(item, dict):
            raise PlanParsingError(
                "Le tableau de tâches contient un élément qui n'est pas un objet JSON."
            )
    return tasks


def _loads_first_json(text: str) -> Any:
    """Décode le premier document JSON trouvé dans `text` (direct, fence, ou sous-chaîne)."""
    if not text or not text.strip():
        raise PlanParsingError("Réponse du modèle vide : aucun JSON à analyser.")

    candidates: list[str] = [text.strip()]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group("body").strip())
    substring = _first_json_substring(text)
    if substring is not None:
        candidates.append(substring)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise PlanParsingError("Impossible de décoder un JSON valide depuis la réponse du modèle.")


def _unwrap_tasks(payload: Any) -> Any:
    """Déballe un tableau de tâches d'une éventuelle enveloppe objet.

    Accepte un tableau direct, ou un objet dont une clé usuelle (`taches`, `tasks`,
    `plan`) — ou l'unique valeur tableau — porte les tâches.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("taches", "tâches", "tasks", "plan"):
            if isinstance(payload.get(key), list):
                return payload[key]
        array_values = [v for v in payload.values() if isinstance(v, list)]
        if len(array_values) == 1:
            return array_values[0]
    return payload


def _first_json_substring(text: str) -> str | None:
    """Isole la première sous-chaîne `[...]` ou `{...}` équilibrée, hors chaînes JSON."""
    start = _first_index(text, "[{")
    if start is None:
        return None
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _first_index(text: str, chars: str) -> int | None:
    """Index du premier caractère de `text` appartenant à `chars`, ou None."""
    indices = [text.find(c) for c in chars]
    found = [i for i in indices if i != -1]
    return min(found) if found else None
