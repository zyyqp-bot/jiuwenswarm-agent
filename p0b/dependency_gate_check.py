"""P0-B (part 3): the dependency gate.

The P0-B acceptance criterion "a cancelled prerequisite must not trigger
downstream business execution" is what this gate enforces. The framework
resolves the dependency edge on cancellation just as it does on completion, so
the gate has to look past "the edge is resolved" at the prerequisite's actual
business result.

Verified on the pure application objects -- no framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0b/dependency_gate_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dependency import BlockReason, DependencyGate  # noqa: E402
from app.execution import ExecutionLedger  # noqa: E402
from app.models import TaskOutcome, TaskResult  # noqa: E402
from checks.harness import Checks, call_ok  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "dependency_gate_results.json"

RUN = "run-1"
OTHER_RUN = "run-2"
RESEARCH = "task-research"
ENGINEERING = "task-engineering"


def _result(
    task_id: str,
    execution_id: str,
    outcome: TaskOutcome,
    artifacts: tuple[str, ...] = (),
    *,
    run_id: str = RUN,
) -> TaskResult:
    return TaskResult(
        run_id=run_id,
        task_id=task_id,
        execution_id=execution_id,
        outcome=outcome,
        artifact_refs=artifacts,
    )


def main() -> int:
    checks = Checks()

    ledger = ExecutionLedger()
    ledger.open(RUN, RESEARCH, "exec-research-1")
    gate = DependencyGate(ledger)

    def evaluate(depends_on, requires_success, results):
        return gate.evaluate(
            run_id=RUN,
            task_id=ENGINEERING,
            depends_on=depends_on,
            requires_success=requires_success,
            results=results,
        )

    # --- the happy path -----------------------------------------------------
    checks.add("a task with no prerequisites may start", evaluate([], True, {}).allowed, "")

    done = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.SUCCESS, ("artifact://facts",))}
    checks.add(
        "a successful prerequisite with an artifact lets the task start",
        evaluate([RESEARCH], True, done).allowed,
        "",
    )

    # --- not finished -------------------------------------------------------
    decision = evaluate([RESEARCH], True, {})
    checks.add(
        "a prerequisite with no result blocks the task as pending",
        not decision.allowed
        and decision.reason is BlockReason.DEPENDENCY_PENDING
        and decision.blocked_by == RESEARCH,
        f"reason={decision.reason} blocked_by={decision.blocked_by}",
    )

    # --- failed, including the cancelled case -------------------------------
    failed = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.ERROR)}
    decision = evaluate([RESEARCH], True, failed)
    checks.add(
        "a failed prerequisite blocks the task (a cancelled edge resolves the same way)",
        not decision.allowed and decision.reason is BlockReason.DEPENDENCY_FAILED,
        f"reason={decision.reason} blocked_by={decision.blocked_by}",
    )

    partial = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.PARTIAL, ("artifact://facts",))}
    decision = evaluate([RESEARCH], True, partial)
    checks.add(
        "a partial prerequisite does not satisfy a success-critical dependency",
        not decision.allowed and decision.reason is BlockReason.DEPENDENCY_FAILED,
        f"reason={decision.reason}",
    )

    # --- succeeded but unusable --------------------------------------------
    empty = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.SUCCESS)}
    decision = evaluate([RESEARCH], True, empty)
    checks.add(
        "a successful prerequisite with no artifact blocks the task",
        not decision.allowed and decision.reason is BlockReason.MISSING_ARTIFACT,
        f"reason={decision.reason}",
    )

    blank = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.SUCCESS, ("   ",))}
    decision = evaluate([RESEARCH], True, blank)
    checks.add(
        "a blank artifact reference does not count as a deliverable",
        not decision.allowed and decision.reason is BlockReason.MISSING_ARTIFACT,
        f"reason={decision.reason}",
    )

    # --- the result must belong to the prerequisite it is filed under -------
    foreign = {RESEARCH: _result("some-other-task", "exec-research-1", TaskOutcome.SUCCESS, ("a",))}
    decision = evaluate([RESEARCH], True, foreign)
    checks.add(
        "a result filed under the wrong prerequisite is rejected",
        not decision.allowed and decision.reason is BlockReason.MISMATCHED_RESULT,
        f"reason={decision.reason}",
    )

    cross_run = {RESEARCH: _result(RESEARCH, "exec-research-1", TaskOutcome.SUCCESS, ("a",), run_id=OTHER_RUN)}
    decision = evaluate([RESEARCH], True, cross_run)
    checks.add(
        "a result produced in another run is rejected",
        not decision.allowed and decision.reason is BlockReason.MISMATCHED_RESULT,
        f"reason={decision.reason}",
    )

    decision = evaluate([ENGINEERING], True, {})
    checks.add(
        "a task listing itself as its own prerequisite is rejected",
        not decision.allowed and decision.reason is BlockReason.SELF_DEPENDENCY,
        f"reason={decision.reason} blocked_by={decision.blocked_by}",
    )

    # --- a result from a superseded execution -------------------------------
    ledger.open(RUN, RESEARCH, "exec-research-2")
    decision = evaluate([RESEARCH], True, done)
    checks.add(
        "a result from a superseded execution blocks the task as stale",
        not decision.allowed and decision.reason is BlockReason.DEPENDENCY_STALE,
        f"reason={decision.reason} blocked_by={decision.blocked_by}",
    )

    fresh = {RESEARCH: _result(RESEARCH, "exec-research-2", TaskOutcome.SUCCESS, ("artifact://facts-2",))}
    checks.add(
        "the re-executed prerequisite's fresh result unblocks the task",
        evaluate([RESEARCH], True, fresh).allowed,
        "",
    )

    # --- requires_success=False --------------------------------------------
    # RESEARCH was re-executed above, so a non-stale failed result has to name
    # its current execution id; reusing the older one would be rejected as stale.
    failed_now = {RESEARCH: _result(RESEARCH, "exec-research-2", TaskOutcome.ERROR)}
    checks.add(
        "a failed prerequisite does not block a task that only needs it finished",
        evaluate([RESEARCH], False, failed_now).allowed,
        "",
    )
    checks.add(
        "even then a missing result still blocks the task",
        not evaluate([RESEARCH], False, {}).allowed,
        "",
    )
    decision = evaluate([RESEARCH], False, done)
    checks.add(
        "a stale result blocks even a non-success-critical dependency",
        not decision.allowed and decision.reason is BlockReason.DEPENDENCY_STALE,
        f"reason={decision.reason}",
    )

    # --- multiple prerequisites --------------------------------------------
    # Both prerequisites are unmet on purpose: with only one unmet the check
    # could not tell "reports the first" from "reports any".
    decision = evaluate(["task-a", "task-b"], True, {})
    checks.add(
        "the first unmet prerequisite is the one reported",
        not decision.allowed and decision.blocked_by == "task-a",
        f"blocked_by={decision.blocked_by} reason={decision.reason}",
    )
    partially_met = {"task-a": _result("task-a", "exec-a", TaskOutcome.SUCCESS, ("a",))}
    ledger.open(RUN, "task-a", "exec-a")
    decision = evaluate(["task-a", "task-b"], True, partially_met)
    checks.add(
        "the first *unmet* prerequisite is reported, not merely the first",
        not decision.allowed and decision.blocked_by == "task-b",
        f"blocked_by={decision.blocked_by} reason={decision.reason}",
    )

    # --- results are immutable once produced --------------------------------
    def mutate_outcome(result: TaskResult) -> None:
        result.outcome = TaskOutcome.ERROR

    def mutate_artifacts(result: TaskResult) -> None:
        result.artifact_refs += ("injected",)

    checks.add(
        "a TaskResult cannot be retuned after the fact",
        not call_ok(mutate_outcome, fresh[RESEARCH]) and not call_ok(mutate_artifacts, fresh[RESEARCH]),
        "outcome and artifact_refs are frozen",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure dependency gate; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
