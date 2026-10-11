"""P0-B (part 4): the completion gate.

The P0-B acceptance criterion "a bad result must not complete a task" is what
this gate enforces. ``enable_task_verification`` is only a prompt switch and a
framework task reaching ``completed`` proves nothing, so acceptance has to be
decided from the application's own result, artifact and acceptance records.

Identity checks read ``TaskResult.producer`` and ``AcceptanceCheck.verifier`` --
never an argument supplied at check time.

Verified on the pure application objects -- no framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0b/completion_gate_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.completion import CompletionBlockReason, CompletionGate  # noqa: E402
from app.execution import ExecutionLedger  # noqa: E402
from app.models import AcceptanceCheck, AcceptanceResult, TaskOutcome, TaskResult  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "completion_gate_results.json"

RUN = "run-1"
OTHER_RUN = "run-2"
TASK = "task-engineering"
PRODUCER = "engineering"
REVIEWER = "review-team"
PROGRAM = "program:structural"

ACCEPT_SCHEMA = "acc-schema"
ACCEPT_EVIDENCE = "acc-evidence"


def _result(
    *,
    task_id: str = TASK,
    execution_id: str = "exec-1",
    outcome: TaskOutcome = TaskOutcome.SUCCESS,
    artifacts: tuple[str, ...] = ("artifact://plan",),
    run_id: str = RUN,
    producer: str = PRODUCER,
) -> TaskResult:
    return TaskResult(
        run_id=run_id,
        task_id=task_id,
        execution_id=execution_id,
        producer=producer,
        outcome=outcome,
        artifact_refs=artifacts,
    )


def _check(
    acceptance_id: str,
    result: AcceptanceResult,
    verifier: str = REVIEWER,
    *,
    filed_as: str | None = None,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        acceptance_id=filed_as if filed_as is not None else acceptance_id,
        result=result,
        verifier=verifier,
    )


def main() -> int:
    checks = Checks()

    ledger = ExecutionLedger()
    ledger.open(RUN, TASK, "exec-1")
    gate = CompletionGate(ledger)

    def evaluate(**overrides):
        payload = {
            "run_id": RUN,
            "task_id": TASK,
            "execution_id": "exec-1",
            "result": _result(),
            "reviewers": (REVIEWER,),
            "require_reviewer": True,
            "required_acceptance_ids": (ACCEPT_SCHEMA,),
            "acceptance": {ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS)},
        }
        payload.update(overrides)
        return gate.evaluate(**payload)

    decision = evaluate()
    checks.add(
        "a valid reviewed result with passing acceptance completes the task",
        decision.allowed,
        decision.detail or "",
    )

    decision = evaluate(result=None)
    checks.add(
        "a missing result cannot complete the task",
        not decision.allowed and decision.reason is CompletionBlockReason.RESULT_MISSING,
        f"reason={decision.reason}",
    )

    # --- the result must belong to this task, run and execution -------------
    for label, override in (
        ("another task", {"task_id": "task-other"}),
        ("another run", {"run_id": OTHER_RUN}),
        ("a different execution", {"execution_id": "exec-other"}),
    ):
        decision = evaluate(result=_result(**override))
        checks.add(
            f"a result belonging to {label} cannot complete this one",
            not decision.allowed and decision.reason is CompletionBlockReason.RESULT_MISMATCHED,
            decision.detail or "",
        )

    # --- execution state ----------------------------------------------------
    decision = evaluate(execution_id="exec-bogus", result=_result(execution_id="exec-bogus"))
    checks.add(
        "an execution that was never opened is reported as unknown, not stale",
        not decision.allowed and decision.reason is CompletionBlockReason.UNKNOWN_EXECUTION,
        decision.detail or "",
    )

    ledger.open(RUN, TASK, "exec-2")
    decision = evaluate()
    checks.add(
        "a superseded execution cannot complete the task",
        not decision.allowed and decision.reason is CompletionBlockReason.STALE_EXECUTION,
        decision.detail or "",
    )
    fresh = _result(execution_id="exec-2")
    decision = evaluate(execution_id="exec-2", result=fresh)
    checks.add("the new execution can complete it once ready", decision.allowed, decision.detail or "")

    never_opened = CompletionGate(ExecutionLedger())
    decision = never_opened.evaluate(
        run_id=RUN, task_id=TASK, execution_id="exec-1", result=_result()
    )
    checks.add(
        "a task with no open execution cannot be completed",
        not decision.allowed and decision.reason is CompletionBlockReason.UNKNOWN_EXECUTION,
        f"reason={decision.reason}",
    )

    # --- bad results --------------------------------------------------------
    for outcome in (TaskOutcome.PARTIAL, TaskOutcome.ERROR):
        decision = evaluate(execution_id="exec-2", result=_result(execution_id="exec-2", outcome=outcome))
        checks.add(
            f"a {outcome.value} outcome cannot complete the task",
            not decision.allowed and decision.reason is CompletionBlockReason.OUTCOME_NOT_SUCCESS,
            decision.detail or "",
        )

    for label, artifacts in (("no artifact", ()), ("a blank artifact", ("  ",))):
        decision = evaluate(execution_id="exec-2", result=_result(execution_id="exec-2", artifacts=artifacts))
        checks.add(
            f"a result with {label} cannot complete the task",
            not decision.allowed and decision.reason is CompletionBlockReason.MISSING_ARTIFACT,
            decision.detail or "",
        )

    # --- review independence -------------------------------------------------
    decision = evaluate(execution_id="exec-2", result=fresh, reviewers=())
    checks.add(
        "a task that requires a reviewer cannot complete without one",
        not decision.allowed and decision.reason is CompletionBlockReason.REVIEWER_MISSING,
        decision.detail or "",
    )
    decision = evaluate(execution_id="exec-2", result=fresh, reviewers=("   ",))
    checks.add(
        "a blank reviewer name does not satisfy the reviewer requirement",
        not decision.allowed and decision.reason is CompletionBlockReason.REVIEWER_MISSING,
        decision.detail or "",
    )
    decision = evaluate(execution_id="exec-2", result=fresh, reviewers=(PRODUCER,))
    checks.add(
        "a reviewer who produced the result cannot complete the task",
        not decision.allowed and decision.reason is CompletionBlockReason.REVIEWER_IS_AUTHOR,
        decision.detail or "",
    )
    decision = evaluate(execution_id="exec-2", result=fresh, reviewers=(REVIEWER, PRODUCER))
    checks.add(
        "a producing reviewer blocks even when others are present",
        not decision.allowed and decision.reason is CompletionBlockReason.REVIEWER_IS_AUTHOR,
        decision.detail or "",
    )
    checks.add(
        "a task that does not require a reviewer needs none",
        evaluate(execution_id="exec-2", result=fresh, reviewers=(), require_reviewer=False).allowed,
        "",
    )

    # --- acceptance ----------------------------------------------------------
    decision = evaluate(execution_id="exec-2", result=fresh, acceptance={})
    checks.add(
        "a required acceptance condition with no verdict blocks the task",
        not decision.allowed and decision.reason is CompletionBlockReason.ACCEPTANCE_NOT_PASSED,
        decision.detail or "",
    )
    for verdict in (AcceptanceResult.FAIL, AcceptanceResult.UNKNOWN):
        decision = evaluate(
            execution_id="exec-2",
            result=fresh,
            acceptance={ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, verdict)},
        )
        checks.add(
            f"a required acceptance condition that is {verdict.value} blocks the task",
            not decision.allowed and decision.reason is CompletionBlockReason.ACCEPTANCE_NOT_PASSED,
            decision.detail or "",
        )

    mismatched = {ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS, filed_as=ACCEPT_EVIDENCE)}
    decision = evaluate(execution_id="exec-2", result=fresh, acceptance=mismatched)
    checks.add(
        "a passing verdict filed under the wrong condition does not satisfy it",
        not decision.allowed and decision.reason is CompletionBlockReason.ACCEPTANCE_NOT_PASSED,
        decision.detail or "",
    )

    for label, verifier in (("its own producer", PRODUCER), ("nobody", "   ")):
        decision = evaluate(
            execution_id="exec-2",
            result=fresh,
            acceptance={ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS, verifier)},
        )
        checks.add(
            f"an acceptance condition signed by {label} does not count",
            not decision.allowed and decision.reason is CompletionBlockReason.ACCEPTANCE_SELF_VERIFIED,
            decision.detail or "",
        )

    decision = evaluate(
        execution_id="exec-2",
        result=fresh,
        acceptance={ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS, PROGRAM)},
    )
    checks.add(
        "a structural acceptance judged by a program does satisfy the condition",
        decision.allowed,
        decision.detail or "",
    )

    decision = evaluate(
        execution_id="exec-2",
        result=fresh,
        required_acceptance_ids=(ACCEPT_SCHEMA, ACCEPT_EVIDENCE),
        acceptance={
            ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS),
            ACCEPT_EVIDENCE: _check(ACCEPT_EVIDENCE, AcceptanceResult.FAIL),
        },
    )
    checks.add(
        "only the unmet required conditions are named",
        not decision.allowed
        and ACCEPT_EVIDENCE in (decision.detail or "")
        and ACCEPT_SCHEMA not in (decision.detail or ""),
        decision.detail or "",
    )
    decision = evaluate(
        execution_id="exec-2",
        result=fresh,
        acceptance={
            ACCEPT_SCHEMA: _check(ACCEPT_SCHEMA, AcceptanceResult.PASS),
            "acc-advisory": _check("acc-advisory", AcceptanceResult.FAIL),
        },
    )
    checks.add(
        "an advisory condition that fails does not block the task",
        decision.allowed,
        decision.detail or "",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure completion gate; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
