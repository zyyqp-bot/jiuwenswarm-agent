"""P0-D (part 4): the rework policy.

PROJECT.md section 6 step 10: at most one business rework, and it is a
*targeted* one. Section 10: if the conflict survives it, the result is marked
partial/unknown rather than retried again.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0d/rework_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.execution import ExecutionLedger, ExecutionState, StaleExecutionError  # noqa: E402
from app.rework import ReworkBlockReason, ReworkPolicy, ReworkReason  # noqa: E402
from checks.harness import Checks, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "rework_results.json"

RUN = "run-1"
TASK = "task-engineering"


def main() -> int:
    checks = Checks()
    policy = ReworkPolicy()

    checks.add(
        "the default allows exactly one rework, as section 6 specifies",
        policy.max_attempts == 1,
        str(policy.max_attempts),
    )

    # --- the first rework ---------------------------------------------------
    decision = policy.evaluate(
        reworks_so_far=0, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True
    )
    checks.add(
        "a failed acceptance allows the first rework",
        decision.allowed and decision.attempt == 1,
        f"allowed={decision.allowed} attempt={decision.attempt}",
    )

    for reason in (ReworkReason.UNSUPPORTED_CLAIM, ReworkReason.EVIDENCE_CONFLICT):
        decision = policy.evaluate(reworks_so_far=0, reason=reason, budget_has_room=True)
        checks.add(
            f"a {reason.value} also allows the first rework",
            decision.allowed and decision.attempt == 1,
            f"allowed={decision.allowed} attempt={decision.attempt}",
        )

    # --- and only the first -------------------------------------------------
    decision = policy.evaluate(
        reworks_so_far=1, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True
    )
    checks.add(
        "a second rework is refused",
        not decision.allowed and decision.reason is ReworkBlockReason.ATTEMPTS_EXHAUSTED,
        f"reason={decision.reason.value if decision.reason else None}",
    )

    # --- a prerequisite failure is not this task's defect -------------------
    decision = policy.evaluate(
        reworks_so_far=0, reason=ReworkReason.DEPENDENCY_FAILED, budget_has_room=True
    )
    checks.add(
        "a failed prerequisite is not reworked (re-running could not change it)",
        not decision.allowed and decision.reason is ReworkBlockReason.NOT_A_TASK_DEFECT,
        f"reason={decision.reason.value if decision.reason else None}",
    )
    decision = policy.evaluate(
        reworks_so_far=5, reason=ReworkReason.DEPENDENCY_FAILED, budget_has_room=False
    )
    checks.add(
        "the prerequisite check comes first, so it is not reported as exhausted or unfunded",
        decision.reason is ReworkBlockReason.NOT_A_TASK_DEFECT,
        f"reason={decision.reason.value if decision.reason else None}",
    )

    # A reason that arrived as a plain string -- from JSON, config, or an event
    # payload -- must be subject to exactly the same guards.
    decision = policy.evaluate(reworks_so_far=0, reason="dependency_failed", budget_has_room=True)
    checks.add(
        "a reason supplied as a plain string is recognised, not bypassed",
        not decision.allowed and decision.reason is ReworkBlockReason.NOT_A_TASK_DEFECT,
        f"reason={decision.reason.value if decision.reason else None}",
    )
    decision = policy.evaluate(reworks_so_far=1, reason="acceptance_failed", budget_has_room=True)
    checks.add(
        "the ceiling applies to string reasons too",
        not decision.allowed and decision.reason is ReworkBlockReason.ATTEMPTS_EXHAUSTED,
        f"reason={decision.reason.value if decision.reason else None}",
    )
    ok, detail = raises(
        lambda: policy.evaluate(reworks_so_far=0, reason="bogus", budget_has_room=True),
        expected=ValueError,
    )
    checks.add("an unrecognised reason is refused rather than treated as reworkable", ok, detail)

    # --- budget -------------------------------------------------------------
    decision = policy.evaluate(
        reworks_so_far=0, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=False
    )
    checks.add(
        "a rework the run cannot pay for is refused",
        not decision.allowed and decision.reason is ReworkBlockReason.NO_BUDGET,
        f"reason={decision.reason.value if decision.reason else None}",
    )

    # --- the counting unit --------------------------------------------------
    ok, detail = raises(
        lambda: policy.evaluate(reworks_so_far=-1, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True),
        expected=ValueError,
    )
    checks.add("a negative rework count is refused", ok, detail)

    generous = ReworkPolicy(max_attempts=2)
    checks.add(
        "the ceiling is configurable and honoured",
        generous.evaluate(reworks_so_far=1, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True).allowed
        and not generous.evaluate(
            reworks_so_far=2, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True
        ).allowed,
        "two reworks allowed, a third refused",
    )
    none_allowed = ReworkPolicy(max_attempts=0)
    decision = none_allowed.evaluate(
        reworks_so_far=0, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True
    )
    checks.add(
        "a policy of zero reworks refuses even the first",
        not decision.allowed and decision.reason is ReworkBlockReason.ATTEMPTS_EXHAUSTED,
        f"reason={decision.reason.value if decision.reason else None}",
    )
    ok, detail = raises(lambda: ReworkPolicy(max_attempts=-1), expected=ValueError)
    checks.add("a negative rework ceiling is refused at construction", ok, detail)

    # --- the approved rework is what opens the new execution ----------------
    ledger = ExecutionLedger()
    first = ledger.open(RUN, TASK, "exec-1")
    decision = policy.evaluate(
        reworks_so_far=0, reason=ReworkReason.ACCEPTANCE_FAILED, budget_has_room=True
    )
    second = ledger.open(RUN, TASK, f"exec-{decision.attempt + 1}")

    checks.add(
        "the rework the policy approved is the attempt that opens a new execution",
        decision.allowed and decision.attempt == 1 and second.execution_seq == first.execution_seq + 1,
        f"attempt={decision.attempt} seq={first.execution_seq}->{second.execution_seq}",
    )
    checks.add(
        "opening it supersedes the attempt being replaced",
        ledger.history(RUN, TASK)[0].state is ExecutionState.SUPERSEDED,
        "",
    )
    ok, detail = raises(ledger.validate_submission, RUN, TASK, "exec-1", expected=StaleExecutionError)
    checks.add(
        "the superseded attempt can no longer submit, so its result cannot win later",
        ok,
        detail,
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure rework policy; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
