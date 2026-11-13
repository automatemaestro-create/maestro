"""Lightweight assignment classifier: the fast model that settles ambiguous cases (#42).

The second routing signal (docs/01 §3.2): when the competency rules do not separate
the candidates (a tie, or no overlap), a call to a **lightweight model** reads the
task (title, description, required competencies) and picks among the proposed
**candidates**, or abstains when it hesitates.

The call goes through the provider layer (`ModelProvider`, #32): no provider is
hard-wired, only the name of the default model (Haiku, docs/01 §3.2) lives here and
stays replaceable at construction. The expected answer is a small JSON object
`{"agent": <name or null>, "confiance": 0..1}`; any unreadable answer, or one
outside the candidates, counts as an abstention (zero confidence), never as a
wrong routing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.catalog import Agent
from maestro.familles_claude import derniere_version
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider

#: The classifier's default lightweight model (docs/01 §3.2: "a fast model, e.g.
#: Haiku"): the latest version of the family, read from `familles-claude.tsv` like
#: the agents' (#1270). The classifier stays light: it routes, it does not do the
#: work. Independent of the role: replaceable without touching routing.
CLASSIFIER_MODEL = derniere_version("haiku")

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

#: The classifier's **second** question (#1260): no longer *who is competent?* but
#: *who comes closest?* It is only asked once the decision has been taken to do
#: this work with the team as it is: the plan called for a trade no candidate has,
#: and the person did not recruit (declined, did not answer, or no one to ask).
#: Abstaining there would miss the question: abstention is what sent the task "to
#: assign" and failed the run the person had let continue.
CLASSIFIER_CLOSEST_SYSTEM_PROMPT = """\
Tu es le routeur de Maestro. La tâche qu'on te soumet demande un métier \
qu'aucun des agents candidats n'a : l'équipe a été confrontée au plan, et la \
décision a été prise de faire ce travail avec l'équipe telle qu'elle est.

Désigne le candidat dont les compétences s'approchent le plus de ce que la \
tâche demande — celui qui la fera le moins mal.

Réponds UNIQUEMENT par un objet JSON de la forme :
{"agent": "<nom d'un candidat>", "confiance": <nombre entre 0 et 1>}

- "agent" doit être exactement l'un des noms candidats ; null n'est pas une \
réponse admise ici, la tâche sera faite par l'un d'eux.
- "confiance" exprime à quel point ce candidat est proche du métier demandé.

Aucun texte hors de l'objet JSON."""

# An optional Markdown code block around the JSON (the same tolerance as the
# orchestrator).
_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    """The classifier's verdict: the chosen candidate (or None) and its confidence [0, 1]."""

    agent: str | None
    confidence: float


class TaskClassifier:
    """Ask a lightweight model, through `ModelProvider`, to break a tie between candidates."""

    def __init__(self, provider: ModelProvider, *, model: str = CLASSIFIER_MODEL) -> None:
        self._provider = provider
        self._model = model

    async def classify(
        self, task: Task, candidates: Sequence[Agent], *, closest: bool = False
    ) -> Classification:
        """Classify `task` among `candidates` and return the model's verdict.

        An unreadable answer, or one naming an agent outside the candidates, counts
        as an abstention (`agent=None`, zero confidence). Provider errors pass
        through as is: the router decides on the fallback.

        `closest` (#1260) asks the second question (*who comes closest?*) about a
        task whose trade no one has, and that the decision to continue with the
        current team hands to one of them. Same answer contract, same reading: a
        name outside the candidates stays an abstention.
        """
        answer = await self._provider.generate(
            build_classifier_prompt(task, candidates),
            model=self._model,
            system_prompt=(
                CLASSIFIER_CLOSEST_SYSTEM_PROMPT if closest else CLASSIFIER_SYSTEM_PROMPT
            ),
        )
        return _parse_classification(answer, frozenset(a.nom for a in candidates))


def build_classifier_prompt(task: Task, candidates: Sequence[Agent]) -> str:
    """Compose the classifier's message: the task, then the candidates and their competencies."""
    lines = [
        "Tâche à assigner :",
        f"- Titre : {task.title}",
        f"- Description : {task.description}",
        f"- Compétences requises : {', '.join(task.required_competencies) or 'aucune'}",
        "",
        "Agents candidats :",
    ]
    lines += [
        f"- {agent.nom} ({agent.role}) — compétences : {', '.join(sorted(agent.competences))}"
        for agent in candidates
    ]
    return "\n".join(lines)


def _parse_classification(text: str, valid_names: frozenset[str]) -> Classification:
    """Decode the model's answer into a `Classification`, abstaining by default.

    Tolerates the usual wrappings (plain JSON, code block, prose around the
    object). The confidence is clamped to [0, 1]. An agent outside the candidates
    is treated as an invalid answer (abstention, zero confidence).
    """
    payload = _first_json_object(text or "")
    if not isinstance(payload, dict):
        return Classification(agent=None, confidence=0.0)

    raw = payload.get("confiance", 0.0)
    confidence = float(raw) if isinstance(raw, (int, float)) else 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    agent = payload.get("agent")
    if isinstance(agent, str) and agent in valid_names:
        return Classification(agent=agent, confidence=confidence)
    # An explicit null is a deliberate abstention (the confidence bears on that
    # choice); any other content is an invalid answer, confidence forced to zero.
    return Classification(agent=None, confidence=confidence if agent is None else 0.0)


def _first_json_object(text: str) -> Any:
    """Decode the first JSON object found in `text` (direct, fence, or substring).

    A tighter version of the orchestrator's extraction: the expected answer is a
    small flat object, with no brace nested in its strings. Returns None when
    nothing is usable.
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
