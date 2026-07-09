"""Classifieur léger d'assignation : le modèle rapide qui tranche les cas ambigus (ticket #42).

Second signal du routage (docs/01 §3.2) : quand les règles de compétences ne
départagent pas (ex æquo, ou aucun recouvrement), un appel à un **modèle léger**
lit la tâche (titre, description, compétences requises) et choisit parmi les
**candidats** proposés — ou s'abstient s'il hésite.

L'appel passe par la couche fournisseur (`ModelProvider`, #32) : aucun fournisseur
n'est câblé en dur, seul le nom du modèle par défaut (Haiku, docs/01 §3.2) vit ici
et reste remplaçable à la construction. La réponse attendue est un petit objet
JSON `{"agent": <nom ou null>, "confiance": 0..1}` ; toute réponse illisible ou
hors des candidats vaut abstention (confiance nulle) — jamais un mauvais routage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.catalog import Agent
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider

#: Modèle léger par défaut du classifieur (docs/01 §3.2 : « un modèle rapide,
#: ex. Haiku »). Indépendant du rôle : remplaçable sans toucher au routage.
MODELE_CLASSIFIEUR = "claude-haiku-4-5"

CLASSIFIER_SYSTEM_PROMPT = """\
Tu es le routeur de Maestro : on te soumet UNE tâche et une liste d'agents \
candidats, tu désignes le candidat le plus compétent pour la réaliser.

Réponds UNIQUEMENT par un objet JSON de la forme :
{"agent": "<nom d'un candidat>", "confiance": <nombre entre 0 et 1>}

- "agent" doit être exactement l'un des noms candidats ; si aucun candidat ne \
convient vraiment, mets null.
- "confiance" exprime ta certitude : proche de 1 si l'évidence est nette, \
basse si tu hésites.
- En cas de doute réel, préfère "agent": null avec une confiance basse : mieux \
vaut laisser la tâche à assigner qu'un mauvais routage.

Aucun texte hors de l'objet JSON."""

# Bloc de code Markdown éventuel autour du JSON (même tolérance que l'orchestrateur).
_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    """Verdict du classifieur : le candidat retenu (ou None) et sa confiance [0, 1]."""

    agent: str | None
    confiance: float


class TaskClassifier:
    """Interroge un modèle léger, via `ModelProvider`, pour départager des candidats."""

    def __init__(self, provider: ModelProvider, *, model: str = MODELE_CLASSIFIEUR) -> None:
        self._provider = provider
        self._model = model

    async def classify(self, task: Task, candidats: Sequence[Agent]) -> Classification:
        """Classe `task` parmi `candidats` et renvoie le verdict du modèle.

        Une réponse illisible, ou désignant un agent hors des candidats, vaut
        abstention (`agent=None`, confiance nulle). Les erreurs du fournisseur
        remontent telles quelles : c'est au routeur de décider du repli.
        """
        reponse = await self._provider.generate(
            build_classifier_prompt(task, candidats),
            model=self._model,
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
        )
        return _parse_classification(reponse, frozenset(a.nom for a in candidats))


def build_classifier_prompt(task: Task, candidats: Sequence[Agent]) -> str:
    """Compose le message du classifieur : la tâche, puis les candidats et leurs compétences."""
    lignes = [
        "Tâche à assigner :",
        f"- Titre : {task.titre}",
        f"- Description : {task.description}",
        f"- Compétences requises : {', '.join(task.competences_requises) or 'aucune'}",
        "",
        "Agents candidats :",
    ]
    lignes += [
        f"- {agent.nom} ({agent.role}) — compétences : {', '.join(sorted(agent.competences))}"
        for agent in candidats
    ]
    return "\n".join(lignes)


def _parse_classification(text: str, noms_valides: frozenset[str]) -> Classification:
    """Décode la réponse du modèle en `Classification`, avec abstention par défaut.

    Tolère les enrobages courants (JSON pur, bloc de code, prose autour de
    l'objet). La confiance est bornée à [0, 1]. Un agent hors des candidats est
    traité comme une réponse invalide (abstention, confiance nulle).
    """
    payload = _premier_objet_json(text or "")
    if not isinstance(payload, dict):
        return Classification(agent=None, confiance=0.0)

    brut = payload.get("confiance", 0.0)
    confiance = float(brut) if isinstance(brut, (int, float)) else 0.0
    confiance = min(max(confiance, 0.0), 1.0)

    agent = payload.get("agent")
    if isinstance(agent, str) and agent in noms_valides:
        return Classification(agent=agent, confiance=confiance)
    # null explicite : abstention assumée (la confiance porte sur ce choix) ;
    # tout autre contenu : réponse invalide, confiance forcée à zéro.
    return Classification(agent=None, confiance=confiance if agent is None else 0.0)


def _premier_objet_json(text: str) -> Any:
    """Décode le premier objet JSON trouvé dans `text` (direct, fence, ou sous-chaîne).

    Version resserrée de l'extraction de l'orchestrateur : la réponse attendue est
    un petit objet plat, sans accolade imbriquée dans ses chaînes. Renvoie None si
    rien d'exploitable.
    """
    candidates = [text.strip()]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group("body").strip())
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None
