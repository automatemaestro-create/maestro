"""The orchestrator: goal → structured brief, then → task list (#3, #318).

It sends the goal to the model **through the provider abstraction** (`ModelProvider`, #32),
extracts the JSON from the response, then validates it against the shared schema.

Three actions of the same orchestrator (the Chef de projet on screen):

- `brief` (#318) — **frame**: the restated intent, its scope, what lies out of scope, its
  acceptance criteria, its assumptions and its questions, validated against
  `packages/shared/schemas/brief.schema.json`. A person approves it **before** any paid
  execution;
- `plan` (#3) — **split**: the task array validated against `task.schema.json`;
- `recovery` (#1178) — **recover**: judge a failed task and decide what comes next.

`brief` and `plan` are independent: `plan` takes a raw goal as well as a brief, and nothing
here forces a run through the brief. Holding a run on its brief is the caller's job (#320).

The core depends only on `ModelProvider`, so it stays **provider-agnostic**. The
`Orchestrator.default` shortcut resolves provider and model from the config
(`MAESTRO_PROVIDER`/`MAESTRO_MODEL`, #69), outside the core.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from maestro.agents.catalog import Agent
from maestro.config import Settings, load_settings
from maestro.orchestrator.errors import (
    BriefParsingError,
    PlanParsingError,
    RecoveryParsingError,
)
from maestro.orchestrator.prompt import (
    BRIEF_SYSTEM_PROMPT,
    build_brief_user_prompt,
    build_recovery_user_prompt,
    build_user_prompt,
    orchestrator_prompt,
    recovery_prompt,
)
from maestro.orchestrator.recovery import Recovery, TaskFailure, validate_recovery
from maestro.orchestrator.schema import (
    Brief,
    Clarification,
    Task,
    validate_brief,
    validate_plan,
)
from maestro.providers.base import ModelProvider

# Markdown code block that may surround the JSON (```json … ``` or ``` … ```).
_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


class Orchestrator:
    """Turns a natural-language goal into a list of validated `Task`."""

    def __init__(self, provider: ModelProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    @property
    def model(self) -> str:
        """The orchestrator's model, which the run's acceptance check also judges with (#1530)."""
        return self._model

    @classmethod
    def default(cls, settings: Settings | None = None) -> Orchestrator:
        """Default orchestrator: provider and model come from the config (#69).

        The factory is imported here, not at the top of the module, so the provider-agnostic
        core is not bound to a concrete provider: the choice lives in the config
        (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`), not in the code.
        """
        from maestro.providers.factory import default_model, provider_from_settings

        settings = settings or load_settings()
        return cls(provider_from_settings(settings), model=default_model(settings))

    async def plan(
        self,
        objective: str,
        *,
        team: Sequence[Agent] | None = None,
        sources_context: str = "",
        tooling: str = "",
        request: str = "",
    ) -> list[Task]:
        """Produce the task plan for `objective`.

        `team` (#1041) is the **project's** team the work is spread across: its roles and
        competencies enter the system prompt (`maestro.orchestrator.prompt.orchestrator_prompt`).
        Omitted — or empty — the split falls back on the code's templates, which is the plan
        of a goal without a project: the `maestro-plan` CLI and the durable activities.

        `sources_context` (#1172): the sources of a run **without a brief**, already framed by
        `markdown_context` (see `build_user_prompt`).

        `tooling` (#1507): the instructions of the project's tooling — what it provides for is
        handed to a task (see `build_user_prompt`). Empty outside a project.

        `request` (#1580): when `objective` is a brief, the approved request it was born
        from — the plan reads it alongside (see `build_user_prompt`). Empty for a run without
        a brief, whose goal is the request.

        Raises `ValueError` if the goal is empty, `PlanParsingError` if the model's response
        is not a usable JSON array, `TaskValidationError` if the plan breaks the schema or the
        inter-task rules.
        """
        if not objective or not objective.strip():
            raise ValueError("L'objectif est vide.")

        response = await self._provider.generate(
            build_user_prompt(objective, sources_context, tooling, request),
            model=self._model,
            system_prompt=orchestrator_prompt(team),
        )
        raw_tasks = _extract_task_array(response)
        return validate_plan(raw_tasks)

    async def brief(
        self,
        goal: str,
        sources_context: str = "",
        clarifications: Sequence[Clarification] = (),
        *,
        last_round: bool = False,
        tooling: str = "",
    ) -> Brief:
        """Write the **structured brief** of `goal`, validated against its schema (#318).

        The step before the split: the intent is restated, framed and made **reviewable by a
        person** before a single task is paid for (EF-40, docs/24 §3.3). The run stops on it
        for approval (#320).

        `sources_context` is the sources' content **already framed** by
        `maestro.sources.extraction.markdown_context` (#316) — the only path that frames it as
        data rather than as an instruction (ENF-13). It arrives framed rather than as a reading
        report (#1172): a run travels to a host, and `Reading.to_dict` leaves the content out
        (by design), so a serialised report would arrive empty. It is the form the chat already
        persists (`MessageChat.contexte`). Empty, the brief works **on the text alone**, which
        is a nominal case, not a missing input.

        `clarifications` (#321) are the round trips **already played**, accumulated since the
        first round. Passing them **regenerates** the brief instead of patching it: the method
        stays stateless, a clarification round is one more call with a richer input, and the
        returned brief is always a whole brief, validated against the same schema.
        `last_round` tells the model the cap is reached (see `build_brief_user_prompt`); the
        cap itself is enforced by the caller.

        `tooling` (#1507): the instructions of the project's tooling, which the brief brings
        into its scope and its criteria. Empty outside a project.

        Raises `ValueError` if the goal is empty, `BriefParsingError` if the model's response
        is not a usable JSON object, `BriefValidationError` if the brief breaks the shared
        schema.
        """
        if not goal or not goal.strip():
            raise ValueError("L'objectif est vide.")

        response = await self._provider.generate(
            build_brief_user_prompt(
                goal,
                sources_context,
                clarifications,
                last_round=last_round,
                tooling=tooling,
            ),
            model=self._model,
            system_prompt=BRIEF_SYSTEM_PROMPT,
        )
        data = _extract_brief_object(response)
        validate_brief(data)
        return Brief.from_dict(data)

    async def recovery(
        self,
        failure: TaskFailure,
        *,
        team: Sequence[Agent] | None = None,
    ) -> Recovery:
        """Judge a task's failure and decide what comes next (#1178).

        The orchestrator's third action, after framing and splitting: a task of its plan has
        failed; it reads the cause (`failure.attempts`), says what happened — transient,
        configuration or approach — and what changes before retrying. `team` is the
        project's, for the same reason as in the split: switching agents means naming the
        competencies of someone who is there.

        The method is stateless, like `brief`: a new round — after a new failure, or after
        the user's answer — is one more call with a longer history.

        Raises `RecoveryParsingError` if the response is not a usable JSON object,
        `RecoveryValidationError` if it breaks what execution requires (`validate_recovery`).
        """
        response = await self._provider.generate(
            build_recovery_user_prompt(failure),
            model=self._model,
            system_prompt=recovery_prompt(team),
        )
        return validate_recovery(_extract_recovery_object(response), failure)


def _extract_task_array(text: str) -> list[dict[str, Any]]:
    """Extract the task array from the model's raw response.

    Tolerates the usual wrappings: plain JSON, a Markdown code block, an envelope object
    (`{"taches": [...]}`), or prose around the array. Returns a list of objects; raises
    `PlanParsingError` when nothing usable is found.
    """
    payload = _loads_first_json(text, error=PlanParsingError)
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
    """Extract the brief object from the model's raw response (#318).

    The **same** tolerant parsing as `_extract_task_array`, and deliberately the same code:
    plain JSON, a Markdown code block, prose around the JSON, or an envelope object
    (`{"brief": {…}}`). Only the shape expected at the end differs — an object, where the
    plan expects an array.

    Raises `BriefParsingError` when nothing is usable, with a message that names the brief:
    reusing `PlanParsingError` would send the reader looking for a task array in a response
    where nobody expected one.
    """
    payload = _loads_first_json(text, error=BriefParsingError)
    brief = _unwrap_brief(payload)
    if not isinstance(brief, dict):
        raise BriefParsingError("La réponse du modèle ne contient pas un objet JSON de brief.")
    return brief


def _extract_recovery_object(text: str) -> dict[str, Any]:
    """Extract the recovery object from the model's raw response (#1178).

    Same tolerant parsing as the plan and the brief. The object is recognised by its
    required `geste` key; failing that, an envelope is looked for (`rattrapage` key, or a
    single object value) — the same rule as `_unwrap_brief` with its `objectif` key.
    """
    payload = _loads_first_json(text, error=RecoveryParsingError)
    if isinstance(payload, dict) and "geste" not in payload:
        if isinstance(payload.get("rattrapage"), dict):
            payload = payload["rattrapage"]
        else:
            objects = [v for v in payload.values() if isinstance(v, dict)]
            if len(objects) == 1:
                payload = objects[0]
    if not isinstance(payload, dict):
        raise RecoveryParsingError(
            "La réponse du modèle ne contient pas un objet JSON de rattrapage."
        )
    return payload


def _loads_first_json(text: str, *, error: type[Exception]) -> Any:
    """Decode the first JSON document found in `text` (direct, fence, or substring).

    `error` is the class raised when nothing decodes: the plan and the brief share the
    tolerance but not the vocabulary of the failure (#318).
    """
    if not text or not text.strip():
        raise error("Réponse du modèle vide : aucun JSON à analyser.")

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
    raise error("Impossible de décoder un JSON valide depuis la réponse du modèle.")


def _unwrap_tasks(payload: Any) -> Any:
    """Unwrap a task array from a possible envelope object.

    Accepts a direct array, or an object where a usual key (`taches`, `tâches`, `tasks`,
    `plan`) — or the single array value — carries the tasks.
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
    """Unwrap a brief from a possible envelope object (#318).

    Counterpart of `_unwrap_tasks`, with a difficulty it does not have: a brief **is** an
    object, so "object" no longer tells the content from its wrapping. The brief is
    recognised by its `objectif` key, which the schema requires — and only failing that is
    an envelope looked for (usual key, or single object value).
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


def _first_json_substring(text: str) -> str | None:  # noqa: C901
    """Isolate the first balanced `[...]` or `{...}` substring, outside JSON strings."""
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
    """Index of the first character of `text` that belongs to `chars`, or None."""
    indices = [text.find(c) for c in chars]
    found = [i for i in indices if i != -1]
    return min(found) if found else None
