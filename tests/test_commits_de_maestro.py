"""The commits Maestro writes itself follow the project's convention (#1507).

When the person chose a convention, the commits Maestro makes on its own — a task's
work, an applied diff — are written in it, not in a fixed wording of Maestro's. These
tests guard the half of that remedy that falls to Maestro: its own messages
(`maestro.projects.commits`), drafted by the model in the stated convention, checked,
and never a cause of failure.

No network and no model: a provider double that remembers what it was told.
"""

from __future__ import annotations

import asyncio

import pytest

from maestro.projects.commits import (
    COMMITS_SHOWN,
    SUBJECT_MAX,
    SYSTEM,
    CommitDrafter,
    message_prompt,
    readable_subject,
)
from maestro.providers.base import ModelProvider


class _Model(ModelProvider):
    """A doubled model: returns its dictated response, or raises — and records each call."""

    name = "modele-double"

    def __init__(self, response: str = "", *, failure: Exception | None = None) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[tuple[str, str, str | None]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.calls.append((prompt, model, system_prompt))
        if self.failure is not None:
            raise self.failure
        return self.response


def _message(provider: _Model, **kwargs: object) -> str:
    drafter = CommitDrafter(provider, model="modele-du-test")
    arguments = {
        "convention": "Conventional Commits",
        "change": "fusionne la page des favoris",
        **kwargs,
    }
    return asyncio.run(drafter.message(**arguments))  # type: ignore[arg-type]


def test_the_subject_is_drafted_in_the_convention_by_the_model() -> None:
    provider = _Model("feat(favoris): intègre la page des favoris")

    assert _message(provider) == "feat(favoris): intègre la page des favoris"
    ((prompt, model, system),) = provider.calls
    assert system == SYSTEM
    assert model == "modele-du-test"
    assert "<convention>Conventional Commits</convention>" in prompt
    assert "<commit>fusionne la page des favoris</commit>" in prompt


def test_the_branch_commits_tell_the_form_and_the_language() -> None:
    """The model sees what the agent wrote: same language, same form."""
    provider = _Model("feat: merge the favorites page")
    subjects = [f"feat: commit {rank}" for rank in range(COMMITS_SHOWN + 5)]

    _message(provider, commits=subjects)

    ((prompt, _, _),) = provider.calls
    assert "feat: commit 0" in prompt
    assert f"feat: commit {COMMITS_SHOWN - 1}" in prompt
    assert f"feat: commit {COMMITS_SHOWN}" not in prompt


def test_without_a_convention_the_model_is_not_called() -> None:
    """Nothing changes without a convention: Maestro's message, to the character."""
    provider = _Model("feat: x")

    assert _message(provider, convention="  ") == ""
    assert provider.calls == []


def test_a_failing_model_returns_empty_without_raising() -> None:
    """A commit never fails for want of a message: the caller keeps its own."""
    assert _message(_Model(failure=RuntimeError("fournisseur injoignable"))) == ""


@pytest.mark.parametrize(
    ("response", "subject"),
    [
        ("feat: ajoute la page", "feat: ajoute la page"),
        ("```\nfeat: ajoute la page\n```", "feat: ajoute la page"),
        ("« feat: ajoute la page »", "feat: ajoute la page"),
        ("`feat: ajoute la page`", "feat: ajoute la page"),
        ("feat: ajoute la page\n\nCe commit ajoute la page.", "feat: ajoute la page"),
        ("", ""),
        ("   \n  ", ""),
        ("x" * (SUBJECT_MAX + 1), ""),
    ],
)
def test_only_a_bounded_one_line_subject_is_kept(response: str, subject: str) -> None:
    assert readable_subject(response) == subject


def test_the_prompt_does_not_show_an_empty_branch() -> None:
    prompt = message_prompt("gitmoji", "ajoute AGENTS.md", ["", "  "])
    assert "<commits>" not in prompt
