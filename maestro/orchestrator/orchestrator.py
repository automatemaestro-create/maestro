"""L'orchestrateur : objectif → brief structuré, puis → liste de tâches (#3, #318).

Pièce maîtresse qui relie les trois autres modules : elle envoie l'objectif au
modèle **via la couche d'abstraction fournisseur** (`ModelProvider`, ticket #32),
extrait le JSON de la réponse, puis le valide contre le schéma partagé.

Deux gestes du même Chef de projet, dans cet ordre :

- `brief` (#318) — **cadrer** : l'intention reformulée, son périmètre, son
  hors-périmètre, ses critères d'acceptation, ses hypothèses et ses questions,
  validés contre `packages/shared/schemas/brief.schema.json`. C'est ce qu'un humain
  approuve **avant** toute exécution payante ;
- `plan` (#3) — **découper** : le tableau de tâches validé contre
  `task.schema.json`.

Les deux sont indépendants et le restent : `plan` continue de partir de l'objectif
brut, et rien ici n'oblige à passer par le brief. C'est le lot 6 de la Phase 8
(#320) qui arrêtera le run sur le brief ; ce module ne fait que rendre l'étape
possible.

Le cœur ne dépend que de `ModelProvider` : il reste **agnostique du fournisseur**.
Le raccourci `Orchestrator.default` résout fournisseur et modèle depuis la config
(`MAESTRO_PROVIDER`/`MAESTRO_MODEL`, #69), sans polluer le cœur.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from maestro.agents.catalog import Agent
from maestro.config import Settings, load_settings
from maestro.orchestrator.errors import BriefParsingError, PlanParsingError
from maestro.orchestrator.prompt import (
    BRIEF_SYSTEM_PROMPT,
    build_brief_user_prompt,
    build_user_prompt,
    prompt_orchestrateur,
)
from maestro.orchestrator.schema import (
    Brief,
    Clarification,
    Task,
    validate_brief,
    validate_plan,
)
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

    async def plan(
        self,
        objective: str,
        *,
        equipe: Sequence[Agent] | None = None,
        contexte_sources: str = "",
    ) -> list[Task]:
        """Produit le plan de tâches pour `objective`.

        `equipe` (#1041) est l'équipe **du projet** sur laquelle le travail sera
        réparti : ses rôles et ses compétences entrent dans le prompt système à la
        place de la liste que le playbook portait en dur
        (`maestro.orchestrator.prompt.prompt_orchestrateur`). Omise — ou vide —,
        le découpage retombe sur les gabarits du code, ce qui est le plan d'un
        objectif sans projet : la CLI `maestro-plan` et les activités durables
        gardent ainsi exactement le prompt d'avant.

        `contexte_sources` (#1172) : les sources d'un run **sans brief**, déjà
        encadrées par `contexte_markdown` (cf. `build_user_prompt`).

        Lève `ValueError` si l'objectif est vide, `PlanParsingError` si la réponse
        du modèle n'est pas un tableau JSON exploitable, `TaskValidationError` si le
        plan enfreint le schéma ou les règles inter-tâches.
        """
        if not objective or not objective.strip():
            raise ValueError("L'objectif est vide.")

        response = await self._provider.generate(
            build_user_prompt(objective, contexte_sources),
            model=self._model,
            system_prompt=prompt_orchestrateur(equipe),
        )
        raw_tasks = _extract_task_array(response)
        return validate_plan(raw_tasks)

    async def brief(
        self,
        objectif: str,
        contexte_sources: str = "",
        clarifications: Sequence[Clarification] = (),
        *,
        dernier_tour: bool = False,
    ) -> Brief:
        """Rédige le **brief structuré** de `objectif`, validé contre son schéma (#318).

        L'étape qui précède la décomposition : l'intention est reformulée, cadrée et
        rendue **relisable par un humain** avant qu'une seule tâche soit payée
        (EF-40, docs/24 §3.3). C'est le lot 6 (#320) qui arrête le run dessus.

        `contexte_sources` est le contenu des sources **déjà encadré** par
        `maestro.sources.extraction.contexte_markdown` (#316) — le seul chemin qui
        l'encadre comme donnée et non comme consigne (ENF-13). Il arrive encadré et
        non en rapport de lecture depuis #1172 : un run part chez un hôte, et
        `Lecture.to_dict` n'emporte pas le contenu (à dessein), si bien qu'un rapport
        sérialisé arrivait vide. C'est la forme que le chat persiste déjà
        (`MessageChat.contexte`). Vide, le brief travaille **sur le texte seul**, ce
        qui est un cas nominal et non une entrée manquante.

        `clarifications` (#321) sont les allers-retours **déjà joués**, cumulés depuis
        le premier tour. Les passer **régénère** le brief au lieu de le rapiécer : la
        méthode reste sans état, un tour de clarification est un appel de plus avec
        une entrée plus riche, et le brief rendu est toujours un brief entier, validé
        contre le même schéma. `dernier_tour` annonce au modèle que le plafond est
        atteint (cf. `build_brief_user_prompt`) ; la borne, elle, est tenue par
        l'appelant.

        Lève `ValueError` si l'objectif est vide, `BriefParsingError` si la réponse du
        modèle n'est pas un objet JSON exploitable, `BriefValidationError` si le brief
        enfreint le schéma partagé.
        """
        if not objectif or not objectif.strip():
            raise ValueError("L'objectif est vide.")

        response = await self._provider.generate(
            build_brief_user_prompt(
                objectif, contexte_sources, clarifications, dernier_tour=dernier_tour
            ),
            model=self._model,
            system_prompt=BRIEF_SYSTEM_PROMPT,
        )
        donnees = _extract_brief_object(response)
        validate_brief(donnees)
        return Brief.from_dict(donnees)


def _extract_task_array(text: str) -> list[dict[str, Any]]:
    """Extrait le tableau de tâches de la réponse brute du modèle.

    Tolérant aux enrobages courants : JSON pur, bloc de code Markdown, objet
    enveloppe (`{"taches": [...]}`), ou prose entourant le tableau. Renvoie une
    liste d'objets ; lève `PlanParsingError` si rien d'exploitable n'est trouvé.
    """
    payload = _loads_first_json(text, erreur=PlanParsingError)
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


def _extract_brief_object(text: str) -> dict[str, Any]:
    """Extrait l'objet brief de la réponse brute du modèle (#318).

    Le **même** parsing tolérant que `_extract_task_array`, et volontairement le
    même code : JSON pur, bloc de code Markdown, prose autour du JSON, ou objet
    enveloppe (`{"brief": {…}}`). Seule change la forme attendue au bout — un
    objet, là où le plan attend un tableau.

    Lève `BriefParsingError` quand rien n'est exploitable, avec un message qui
    nomme le brief : réemployer `PlanParsingError` enverrait chercher un tableau
    de tâches dans une réponse où personne n'en attendait.
    """
    payload = _loads_first_json(text, erreur=BriefParsingError)
    brief = _unwrap_brief(payload)
    if not isinstance(brief, dict):
        raise BriefParsingError("La réponse du modèle ne contient pas un objet JSON de brief.")
    return brief


def _loads_first_json(text: str, *, erreur: type[Exception]) -> Any:
    """Décode le premier document JSON trouvé dans `text` (direct, fence, ou sous-chaîne).

    `erreur` est la classe levée quand rien n'est décodable : le plan et le brief
    partagent la tolérance mais pas le vocabulaire de l'échec (#318).
    """
    if not text or not text.strip():
        raise erreur("Réponse du modèle vide : aucun JSON à analyser.")

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
    raise erreur("Impossible de décoder un JSON valide depuis la réponse du modèle.")


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


def _unwrap_brief(payload: Any) -> Any:
    """Déballe un brief d'une éventuelle enveloppe objet (#318).

    Pendant de `_unwrap_tasks`, avec une difficulté qu'il n'a pas : un brief **est**
    un objet, donc « objet » ne suffit plus à distinguer le contenu de son
    emballage. On le reconnaît à sa clé `objectif`, qui est requise par le schéma —
    et ce n'est qu'à défaut qu'on cherche une enveloppe (clé usuelle, ou unique
    valeur objet).
    """
    if not isinstance(payload, dict):
        return payload
    if "objectif" in payload:
        return payload
    for key in ("brief", "brief_structure", "brief_structuré"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    object_values = [v for v in payload.values() if isinstance(v, dict)]
    if len(object_values) == 1:
        return object_values[0]
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
