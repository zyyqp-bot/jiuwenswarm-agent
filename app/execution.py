"""Execution ledger: only the current execution of a task may submit a result.

PROJECT.md section 3 ("重要限制") records why the framework's own state machine
is not enough. ``task_dao`` resolves downstream dependencies on both
``COMPLETED`` and ``CANCELLED``, and a re-executed task reuses the same
``in_progress`` state, so a late result from a superseded execution is
indistinguishable from a fresh one by task status alone. The application must
therefore key result submission on ``execution_id`` and reject anything that is
not the current execution.

The ledger holds no framework objects, starts no runtime and calls no model.

Invariants:

- ``execution_id`` is unique within a ``(run_id, task_id)``. Opening a duplicate
  raises, because a reused id would make "which attempt is this?" unanswerable
  from the id alone.
- At most one record per ``(run_id, task_id)`` is ACTIVE, and it is always the
  most recently appended one.
- Records are immutable once handed out, so a caller cannot promote a superseded
  record back to ACTIVE and defeat the stale check.

``execution_seq`` is a per-task monotonic counter, used for ordering and for the
diagnostic that names which generation superseded a rejected one.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DuplicateExecutionError",
    "ExecutionLedger",
    "ExecutionRecord",
    "ExecutionState",
    "StaleExecutionError",
    "UnknownExecutionError",
]


class ExecutionState(str, Enum):
    """Lifecycle of one task execution."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"


class StaleExecutionError(RuntimeError):
    """A result was submitted by an execution that has been superseded."""


class UnknownExecutionError(RuntimeError):
    """A result was submitted by an execution that was never opened."""


class DuplicateExecutionError(RuntimeError):
    """An execution id was opened twice for the same task."""


class ExecutionRecord(BaseModel):
    """One execution attempt for a task.

    Frozen: the ledger replaces list entries instead of mutating them, so a
    record a caller is holding never changes state underneath it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    task_id: str
    execution_id: str
    execution_seq: int
    state: ExecutionState = ExecutionState.ACTIVE


class ExecutionLedger:
    """Track the current execution of every ``(run_id, task_id)``.

    Opening a new execution supersedes the previous one, so a result that
    arrives late from an old execution is rejected by
    :meth:`validate_submission` even though the framework may still report the
    task as ``in_progress``.
    """

    def __init__(self) -> None:
        self._history: dict[tuple[str, str], list[ExecutionRecord]] = {}

    def open(self, run_id: str, task_id: str, execution_id: str) -> ExecutionRecord:
        """Open a new execution and supersede the task's current one.

        Raises:
            DuplicateExecutionError: this ``execution_id`` was already opened for
                the task. Reusing an id would make a late result from the earlier
                attempt indistinguishable from the new one.
        """
        history = self._history.setdefault((run_id, task_id), [])
        if any(record.execution_id == execution_id for record in history):
            raise DuplicateExecutionError(
                f"execution {execution_id!r} was already opened for task {task_id!r} in run {run_id!r}"
            )

        # Records are frozen, so supersede by replacing the entry.
        for index, record in enumerate(history):
            if record.state is ExecutionState.ACTIVE:
                history[index] = record.model_copy(update={"state": ExecutionState.SUPERSEDED})

        seq = history[-1].execution_seq + 1 if history else 1
        opened = ExecutionRecord(
            run_id=run_id,
            task_id=task_id,
            execution_id=execution_id,
            execution_seq=seq,
        )
        history.append(opened)
        return opened

    def current(self, run_id: str, task_id: str) -> ExecutionRecord | None:
        """Return the active execution of a task, or None when none exists.

        ``open`` guarantees the active record is the last one appended.
        """
        history = self._history.get((run_id, task_id))
        if not history:
            return None
        last = history[-1]
        return last if last.state is ExecutionState.ACTIVE else None

    def history(self, run_id: str, task_id: str) -> tuple[ExecutionRecord, ...]:
        """Return every execution recorded for a task, oldest first."""
        return tuple(self._history.get((run_id, task_id), []))

    def validate_submission(self, run_id: str, task_id: str, execution_id: str) -> ExecutionRecord:
        """Return the record when this execution may submit a result.

        Raises:
            UnknownExecutionError: the execution was never opened for this task.
            StaleExecutionError: the execution has been superseded.
        """
        records = self._history.get((run_id, task_id), [])
        if not records:
            raise UnknownExecutionError(
                f"no execution recorded for task {task_id!r} in run {run_id!r}"
            )

        for record in records:
            if record.execution_id != execution_id:
                continue
            if record.state is ExecutionState.ACTIVE:
                return record
            superseded_by = next(
                (later.execution_seq for later in records if later.execution_seq > record.execution_seq),
                None,
            )
            raise StaleExecutionError(
                f"execution {execution_id!r} of task {task_id!r} (sequence {record.execution_seq}) "
                f"was superseded by sequence {superseded_by}"
            )

        raise UnknownExecutionError(
            f"execution {execution_id!r} was never opened for task {task_id!r}"
        )
