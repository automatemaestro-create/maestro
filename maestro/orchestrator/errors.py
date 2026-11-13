"""Orchestrator errors (#3, #318).

A flat hierarchy under `OrchestratorError`, so that a caller can catch any orchestration
failure with a single `except`, or tell parsing apart from validation.

The brief (#318) has its **own** errors rather than reusing those of the plan: the two
steps expect different shapes (an object, an array), so a message about a "JSON array of
tasks" where a brief was expected sends the reader looking in the wrong place. They stay
under the same base: a caller who only wants "the orchestrator failed" still has a single
`except` to write.
"""

from __future__ import annotations


class OrchestratorError(RuntimeError):
    """Base of every orchestrator failure."""


class PlanParsingError(OrchestratorError):
    """The model's response could not be decoded into a JSON array of tasks."""


class TaskValidationError(OrchestratorError):
    """A task (or the plan) breaks the schema or the cross-task rules."""


class BriefParsingError(OrchestratorError):
    """The model's response could not be decoded into a JSON brief object (#318)."""


class BriefValidationError(OrchestratorError):
    """The brief breaks the shared JSON Schema `brief.schema.json` (#318)."""


class RecoveryParsingError(OrchestratorError):
    """The model's response could not be decoded into a JSON recovery object (#1178)."""


class RecoveryValidationError(OrchestratorError):
    """The proposed recovery breaks what execution requires (#1178).

    Replaying unchanged a failure that is not transient, retrying without changing
    anything, adding a granted act, abandoning without an answer from the user: the message
    says which, and it is recorded in the journal.
    """
