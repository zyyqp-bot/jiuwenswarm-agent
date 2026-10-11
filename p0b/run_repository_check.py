"""P0-B (part 6): the run repository.

The P0-B execution criterion has to survive a restart, or recovery cannot
reconcile against it (PROJECT.md section 10). This script drives the durable
store over a real SQLite file and re-opens it mid-test to prove the invariants
are persisted, not just held in memory -- including on the *read* side, where a
re-dispatched task must not look complete because its superseded execution still
has a stored result.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0b/run_repository_check.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.execution import (  # noqa: E402
    DuplicateExecutionError,
    ExecutionState,
    StaleExecutionError,
    UnknownExecutionError,
)
from app.models import TaskOutcome, TaskResult  # noqa: E402
from app.repository import RunRepository, UnknownRunError  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "run_repository_results.json"

RUN = "run-1"
TASK = "task-research"


def _result(
    execution_id: str,
    *,
    task_id: str = TASK,
    outcome: TaskOutcome = TaskOutcome.SUCCESS,
    artifacts: tuple[str, ...] = ("artifact://facts",),
    producer: str = "research",
) -> TaskResult:
    return TaskResult(
        run_id=RUN,
        task_id=task_id,
        execution_id=execution_id,
        producer=producer,
        outcome=outcome,
        artifact_refs=artifacts,
    )


async def raises_async(fn, *args, expected: type[Exception]) -> tuple[bool, str]:
    """Async twin of checks.harness.raises."""
    try:
        await fn(*args)
    except expected as exc:
        return True, str(exc)
    except Exception as exc:  # a different failure is not the guard we claim
        return False, f"wrong error {type(exc).__name__}: {exc}"
    return False, "no error raised"


async def _async_checks(checks: Checks, db_path: Path) -> None:
    repository = await RunRepository.open(db_path)

    unknown_run = await raises_async(repository.open_execution, "typo-run", TASK, "e1", expected=UnknownRunError)
    checks.add("an execution cannot be opened for a run that was never registered", unknown_run[0], unknown_run[1])

    await repository.register_run(
        RUN, session_id="sess-1", team_name="jiuwenswarm_mvp", original_prompt="do the thing", prompt_hash="h1"
    )
    await repository.register_run(RUN)  # idempotent
    checks.add(
        "registering a run is idempotent",
        await repository.registered_runs() == [RUN],
        f"runs={await repository.registered_runs()}",
    )

    record = await repository.open_execution(RUN, TASK, "exec-1")
    checks.add(
        "the first execution gets sequence 1",
        record.execution_seq == 1 and record.state is ExecutionState.ACTIVE,
        f"seq={record.execution_seq}",
    )

    await repository.submit_result(_result("exec-1"))
    stored = await repository.latest_result(RUN, TASK)
    checks.add(
        "a result from the current execution is stored and read back intact",
        stored is not None
        and stored.outcome is TaskOutcome.SUCCESS
        and stored.artifact_refs == ("artifact://facts",)
        and stored.producer == "research",
        f"stored={stored!r}",
    )

    ok, detail = await raises_async(repository.submit_result, _result("exec-1"), expected=DuplicateExecutionError)
    checks.add("an execution cannot submit a second result", ok, detail)

    ok, detail = await raises_async(
        repository.open_execution, RUN, TASK, "exec-1", expected=DuplicateExecutionError
    )
    checks.add("reopening an execution id is rejected", ok, detail)

    # --- the read side must follow the current execution --------------------
    await repository.open_execution(RUN, TASK, "exec-2")
    checks.add(
        "opening a second execution supersedes the first",
        (await repository.current_execution(RUN, TASK)).execution_id == "exec-2"
        and (await repository.execution_history(RUN, TASK))[0].state is ExecutionState.SUPERSEDED,
        "",
    )
    checks.add(
        "a re-dispatched task does not look complete off its superseded execution's result",
        await repository.latest_result(RUN, TASK) is None,
        f"latest={await repository.latest_result(RUN, TASK)!r}",
    )
    checks.add(
        "results_for_run omits tasks whose current execution has not reported",
        await repository.results_for_run(RUN) == {},
        f"results={await repository.results_for_run(RUN)}",
    )

    ok, detail = await raises_async(repository.submit_result, _result("exec-1"), expected=StaleExecutionError)
    checks.add("a superseded execution cannot submit (the P0-B criterion)", ok, detail)

    ok, detail = await raises_async(repository.submit_result, _result("never-opened"), expected=UnknownExecutionError)
    checks.add("an execution that was never opened is rejected", ok, detail)

    await repository.submit_result(_result("exec-2", artifacts=("artifact://facts-2",)))
    checks.add(
        "the fresh execution can submit and becomes the readable result",
        (await repository.latest_result(RUN, TASK)).execution_id == "exec-2",
        "",
    )

    await repository.close()

    # --- the invariants must survive a restart ------------------------------
    reopened = await RunRepository.open(db_path)
    checks.add(
        "runs and executions are still there after reopening the database",
        await reopened.registered_runs() == [RUN]
        and (await reopened.current_execution(RUN, TASK)).execution_id == "exec-2",
        f"runs={await reopened.registered_runs()}",
    )
    checks.add(
        "the stored result survives reopening",
        (await reopened.latest_result(RUN, TASK)).artifact_refs == ("artifact://facts-2",),
        "",
    )
    ok, detail = await raises_async(reopened.submit_result, _result("exec-1"), expected=StaleExecutionError)
    checks.add("a stale submission is still rejected after reopening", ok, detail)

    # --- the schema holds the single-ACTIVE rule on its own -----------------
    enforced = False
    try:
        await reopened._db.execute(
            "INSERT INTO executions (run_id, task_id, execution_seq, execution_id, state) VALUES (?, ?, ?, ?, ?)",
            (RUN, TASK, 99, "exec-manual", ExecutionState.ACTIVE.value),
        )
        await reopened._db.commit()
    except sqlite3.IntegrityError:
        enforced = True
    checks.add(
        "the schema itself forbids a second active execution for a task",
        enforced,
        "a direct insert of a second ACTIVE row is rejected",
    )

    await reopened.open_execution(RUN, "task-engineering", "exec-eng-1")
    await reopened.submit_result(_result("exec-eng-1", task_id="task-engineering", producer="engineering"))
    results = await reopened.results_for_run(RUN)
    checks.add(
        "results_for_run returns the current result of every task, keyed by task id",
        set(results) == {TASK, "task-engineering"} and results[TASK].execution_id == "exec-2",
        f"tasks={sorted(results)}",
    )

    await reopened.close()


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="p0b-runs-") as tmp:
        asyncio.run(_async_checks(checks, Path(tmp) / "runs.sqlite"))

    return checks.report(
        RESULTS_PATH,
        scope="durable run repository over SQLite; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
