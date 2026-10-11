"""Application data models shared across the MVP modules.

These are the application-layer types from PROJECT.md section 5. They are not
framework SDK types: everything here is Pydantic v2 with ``extra="forbid"`` so
an unknown field is an error rather than a silently ignored one.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = ["TaskOutcome", "TaskResult"]


class TaskOutcome(str, Enum):
    """Business outcome of one task execution.

    Distinct from the framework's task status: a framework task may be
    ``completed`` while its business outcome is PARTIAL or ERROR.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


class TaskResult(BaseModel):
    """The immutable result of one task execution.

    Frozen, and ``artifact_refs`` is a tuple rather than a list, so a caller
    holding a submitted result cannot retune its outcome or append to its
    artifacts behind the gates that already evaluated it.

    ``run_id`` is carried alongside ``task_id``/``execution_id`` because
    execution ids are only unique per ``(run, task)`` in the ledger: without
    the run, a result from another run that happens to reuse the same execution
    id string would look current.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    task_id: str
    execution_id: str
    outcome: TaskOutcome
    artifact_refs: tuple[str, ...] = ()
