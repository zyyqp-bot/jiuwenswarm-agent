"""Run repository: the durable record of runs, executions and task results.

PROJECT.md section 5 keeps the application's own ledger separate from the
framework's team/task tables, and section 3 explains why it has to exist: the
framework's task status cannot distinguish a re-executed task's late result from
a fresh one. The ExecutionLedger enforces that in memory; this repository makes
it survive a restart, which is what PROJECT.md section 10 requires recovery to
reconcile against ("reconcile native tasks, checkpoints, and application
executions/artifacts; do not blindly re-dispatch").

The same three invariants as the in-memory ledger hold here:

- ``(run_id, task_id, execution_id)`` is unique,
- at most one ACTIVE execution per task, always the highest sequence,
- a result is only accepted from, and only ever read from, the task's current
  execution.

The last one matters on the read side as much as the write side: a re-dispatched
task whose new execution has not submitted yet must not look complete because
its superseded execution still has a stored result.

The constraints are enforced by the schema (unique keys plus a partial unique
index for the single-ACTIVE rule) and by the methods, so a direct write or a
future code path cannot quietly break them.

It starts no runtime and calls no model.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

from app.execution import (
    DuplicateExecutionError,
    ExecutionRecord,
    ExecutionState,
    validate_submission_record,
)
from app.models import TaskOutcome, TaskResult

__all__ = ["RunRepository", "UnknownRunError"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    session_id      TEXT,
    team_name       TEXT,
    original_prompt TEXT,
    prompt_hash     TEXT,
    submitted_at    TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    run_id        TEXT    NOT NULL,
    task_id       TEXT    NOT NULL,
    execution_seq INTEGER NOT NULL,
    execution_id  TEXT    NOT NULL,
    state         TEXT    NOT NULL,
    PRIMARY KEY (run_id, task_id, execution_seq),
    UNIQUE (run_id, task_id, execution_id)
);

-- At most one ACTIVE execution per task. Without this the method-level
-- supersede-then-insert is the only thing holding the invariant.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_execution
    ON executions (run_id, task_id) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS task_results (
    run_id        TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    execution_id  TEXT NOT NULL,
    producer      TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    artifact_refs TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id, execution_id)
);
"""


class UnknownRunError(RuntimeError):
    """An execution or result was written for a run that was never registered."""


class RunRepository:
    """Durable run/execution/result store backed by SQLite."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    # -- lifecycle ----------------------------------------------------------
    @classmethod
    async def open(cls, db_path: str | Path) -> "RunRepository":
        """Open (creating if needed) the repository at ``db_path``."""
        connection = await aiosqlite.connect(str(db_path))
        connection.row_factory = aiosqlite.Row
        await connection.executescript(_SCHEMA)
        await connection.commit()
        return cls(connection)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> "RunRepository":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    # -- runs ---------------------------------------------------------------
    async def register_run(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        team_name: str | None = None,
        original_prompt: str | None = None,
        prompt_hash: str | None = None,
        submitted_at: str | None = None,
    ) -> None:
        """Register a run. Registering the same id twice is a no-op."""
        await self._db.execute(
            "INSERT OR IGNORE INTO runs "
            "(run_id, session_id, team_name, original_prompt, prompt_hash, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, session_id, team_name, original_prompt, prompt_hash, submitted_at),
        )
        await self._db.commit()

    async def registered_runs(self) -> list[str]:
        async with self._db.execute("SELECT run_id FROM runs ORDER BY run_id") as cursor:
            return [row["run_id"] for row in await cursor.fetchall()]

    # -- executions ---------------------------------------------------------
    async def open_execution(self, run_id: str, task_id: str, execution_id: str) -> ExecutionRecord:
        """Open an execution, superseding the task's current one.

        Raises:
            UnknownRunError: the run was never registered.
            DuplicateExecutionError: this execution id was already opened.
        """
        await self._require_run(run_id)

        await self._db.execute(
            "UPDATE executions SET state = ? WHERE run_id = ? AND task_id = ? AND state = ?",
            (ExecutionState.SUPERSEDED.value, run_id, task_id, ExecutionState.ACTIVE.value),
        )
        async with self._db.execute(
            "SELECT COALESCE(MAX(execution_seq), 0) + 1 AS next_seq FROM executions "
            "WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ) as cursor:
            row = await cursor.fetchone()
            sequence = int(row["next_seq"])

        # The unique key is the real arbiter: a concurrent open can slip past a
        # prior SELECT, so the insert is what has to reject the duplicate.
        try:
            await self._db.execute(
                "INSERT INTO executions (run_id, task_id, execution_seq, execution_id, state) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, task_id, sequence, execution_id, ExecutionState.ACTIVE.value),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateExecutionError(
                f"execution {execution_id!r} was already opened for task {task_id!r} in run {run_id!r}"
            ) from exc

        await self._db.commit()
        return ExecutionRecord(
            run_id=run_id,
            task_id=task_id,
            execution_id=execution_id,
            execution_seq=sequence,
        )

    async def current_execution(self, run_id: str, task_id: str) -> ExecutionRecord | None:
        async with self._db.execute(
            "SELECT * FROM executions WHERE run_id = ? AND task_id = ? AND state = ? "
            "ORDER BY execution_seq DESC LIMIT 1",
            (run_id, task_id, ExecutionState.ACTIVE.value),
        ) as cursor:
            row = await cursor.fetchone()
        return self._to_record(row) if row is not None else None

    async def execution_history(self, run_id: str, task_id: str) -> tuple[ExecutionRecord, ...]:
        async with self._db.execute(
            "SELECT * FROM executions WHERE run_id = ? AND task_id = ? ORDER BY execution_seq",
            (run_id, task_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(self._to_record(row) for row in rows)

    # -- results ------------------------------------------------------------
    async def submit_result(self, result: TaskResult) -> None:
        """Store a result, accepting it only from the task's current execution.

        Raises:
            UnknownExecutionError: the execution was never opened.
            StaleExecutionError: the execution has been superseded.
            DuplicateExecutionError: this execution already submitted a result.
        """
        execution = validate_submission_record(
            await self.execution_history(result.run_id, result.task_id),
            run_id=result.run_id,
            task_id=result.task_id,
            execution_id=result.execution_id,
        )

        try:
            await self._db.execute(
                "INSERT INTO task_results (run_id, task_id, execution_id, producer, outcome, artifact_refs) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result.run_id,
                    result.task_id,
                    execution.execution_id,
                    result.producer,
                    result.outcome.value,
                    json.dumps(list(result.artifact_refs)),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateExecutionError(
                f"execution {result.execution_id!r} already submitted a result for task {result.task_id!r}"
            ) from exc

        await self._db.commit()

    async def latest_result(self, run_id: str, task_id: str) -> TaskResult | None:
        """Return the current execution's result for a task, or None.

        A superseded execution's result is deliberately not returned: the task
        was re-dispatched, and its new attempt has not produced anything yet.
        """
        async with self._db.execute(
            "SELECT r.* FROM task_results r "
            "JOIN executions e ON e.run_id = r.run_id AND e.task_id = r.task_id "
            "AND e.execution_id = r.execution_id "
            "WHERE r.run_id = ? AND r.task_id = ? AND e.state = ?",
            (run_id, task_id, ExecutionState.ACTIVE.value),
        ) as cursor:
            row = await cursor.fetchone()
        return self._to_result(row) if row is not None else None

    async def results_for_run(self, run_id: str) -> dict[str, TaskResult]:
        """Return the current result of every task in a run, keyed by task id."""
        async with self._db.execute(
            "SELECT r.* FROM task_results r "
            "JOIN executions e ON e.run_id = r.run_id AND e.task_id = r.task_id "
            "AND e.execution_id = r.execution_id "
            "WHERE r.run_id = ? AND e.state = ?",
            (run_id, ExecutionState.ACTIVE.value),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["task_id"]: self._to_result(row) for row in rows}

    # -- helpers ------------------------------------------------------------
    async def _require_run(self, run_id: str) -> None:
        async with self._db.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)) as cursor:
            if await cursor.fetchone() is None:
                raise UnknownRunError(f"run {run_id!r} is not registered")

    @staticmethod
    def _to_record(row: aiosqlite.Row) -> ExecutionRecord:
        return ExecutionRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            execution_id=row["execution_id"],
            execution_seq=int(row["execution_seq"]),
            state=ExecutionState(row["state"]),
        )

    @staticmethod
    def _to_result(row: aiosqlite.Row) -> TaskResult:
        return TaskResult(
            run_id=row["run_id"],
            task_id=row["task_id"],
            execution_id=row["execution_id"],
            producer=row["producer"],
            outcome=TaskOutcome(row["outcome"]),
            artifact_refs=tuple(json.loads(row["artifact_refs"])),
        )
