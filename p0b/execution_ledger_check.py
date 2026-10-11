"""P0-B (part 2): the execution ledger.

The P0-B acceptance criterion "an old execution cannot complete a new task" is
the reason this ledger exists. Verified here on the pure application object --
no framework, no Runner, no database, no model.

Run: .venv/Scripts/python.exe p0b/execution_ledger_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.execution import (  # noqa: E402
    DuplicateExecutionError,
    ExecutionLedger,
    ExecutionState,
    StaleExecutionError,
    UnknownExecutionError,
)
from checks.harness import Checks, call_ok, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "execution_ledger_results.json"

RUN = "run-1"
TASK = "task-1"


def main() -> int:
    checks = Checks()

    ledger = ExecutionLedger()
    ledger.open(RUN, TASK, "exec-1")
    checks.add(
        "the first execution gets sequence 1 and is active",
        ledger.current(RUN, TASK).execution_seq == 1,
        f"current={ledger.current(RUN, TASK).execution_id!r}",
    )
    checks.add(
        "the active execution can submit a result",
        ledger.validate_submission(RUN, TASK, "exec-1").execution_id == "exec-1",
        "",
    )

    ledger.open(RUN, TASK, "exec-2")
    states = {r.execution_id: r.state for r in ledger.history(RUN, TASK)}
    checks.add(
        "opening a second execution bumps the sequence and supersedes the first",
        ledger.current(RUN, TASK).execution_seq == 2
        and states["exec-1"] is ExecutionState.SUPERSEDED
        and states["exec-2"] is ExecutionState.ACTIVE,
        f"states={ {k: v.value for k, v in states.items()} }",
    )
    checks.add(
        "current() reports the newest execution",
        (current := ledger.current(RUN, TASK)) is not None and current.execution_id == "exec-2",
        f"current={getattr(current, 'execution_id', None)!r}",
    )

    ok, detail = raises(ledger.validate_submission, RUN, TASK, "exec-1", expected=StaleExecutionError)
    checks.add(
        "a superseded execution cannot submit (the core P0-B criterion)",
        ok,
        detail,
    )
    checks.add(
        "the new execution can still submit",
        ledger.validate_submission(RUN, TASK, "exec-2").execution_id == "exec-2",
        "",
    )

    ok, detail = raises(ledger.validate_submission, RUN, TASK, "never-opened", expected=UnknownExecutionError)
    checks.add("an execution id that was never opened is rejected", ok, detail)
    ok, detail = raises(ledger.validate_submission, RUN, "other-task", "exec-1", expected=UnknownExecutionError)
    checks.add("an unknown task is rejected even with a known execution id", ok, detail)

    # An id must not be reusable: with a reused id, a late result from the
    # earlier attempt would be indistinguishable from the new one.
    ok, detail = raises(ledger.open, RUN, TASK, "exec-1", expected=DuplicateExecutionError)
    checks.add("reopening an execution id for the same task is rejected", ok, detail)

    # Records handed out must not be mutable, or a caller could promote a
    # superseded record back to ACTIVE and defeat the stale check.
    def promote(record) -> None:
        record.state = ExecutionState.ACTIVE

    checks.add(
        "records handed out by history() are immutable",
        not call_ok(promote, ledger.history(RUN, TASK)[0]),
        "assignment to a frozen record raises",
    )

    # cross-run isolation
    cross = ExecutionLedger()
    cross.open(RUN, TASK, "same-id")
    cross.open("run-2", TASK, "same-id")
    checks.add(
        "the same execution id in another run is tracked independently",
        cross.current(RUN, TASK).execution_seq == 1 and cross.current("run-2", TASK).execution_seq == 1,
        "",
    )

    # history ordering and older generations
    chain = ExecutionLedger()
    for index in range(3):
        chain.open(RUN, TASK, f"exec-{index}")
    ordered = chain.history(RUN, TASK)
    seqs = [r.execution_seq for r in ordered]
    states = [r.state for r in ordered]
    checks.add(
        "history is oldest-first and only the last entry stays active",
        seqs == [1, 2, 3]
        and states
        == [ExecutionState.SUPERSEDED, ExecutionState.SUPERSEDED, ExecutionState.ACTIVE],
        f"seqs={seqs} states={[s.value for s in states]}",
    )
    ok, message = raises(chain.validate_submission, RUN, TASK, "exec-0", expected=StaleExecutionError)
    checks.add("an execution two generations back is still rejected", ok, message)
    checks.add(
        "the stale diagnostic names the generation that superseded it, not the newest",
        ok and "sequence 2" in message,
        message,
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure application ledger; no framework, no Runner, no database, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
