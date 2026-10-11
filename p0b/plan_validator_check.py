"""P0-B (part 5): the plan validator.

PROJECT.md section 12 acceptance scenario 6: "a key task without a reviewer must
not be accepted by the plan validator". A task is key when another task depends
on its deliverable. This script covers that and the rest of the structural plan
contract -- acyclic DAG, existing dependencies, real roles, declared acceptance.

Verified on the pure application object -- no framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0b/plan_validator_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.plan import PlanIssueCode, PlanTask, PlanValidator  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "plan_validator_results.json"

RESEARCH = "task-research"
ENGINEERING = "task-engineering"


def _task(task_id: str, **overrides) -> PlanTask:
    payload = {
        "task_id": task_id,
        "role": "teammate",
        "instruction": f"do {task_id}",
        "acceptance_ids": (f"acc-{task_id}",),
        "reviewers": ("review-team",),
    }
    payload.update(overrides)
    return PlanTask(**payload)


def main() -> int:
    checks = Checks()
    validator = PlanValidator()

    # --- the expected shape -------------------------------------------------
    plan = [_task(RESEARCH), _task(ENGINEERING, depends_on=(RESEARCH,))]
    report = validator.validate(plan)
    checks.add(
        "a research -> engineering plan with a reviewed key task is valid",
        report.valid,
        f"issues={report.codes()}",
    )

    # --- structure ----------------------------------------------------------
    report = validator.validate([])
    checks.add(
        "an empty plan is rejected with a structural code",
        not report.valid and report.codes() == [PlanIssueCode.EMPTY_PLAN],
        f"issues={report.codes()}",
    )

    report = validator.validate([_task("  ")])
    checks.add(
        "a blank task id is rejected",
        not report.valid and PlanIssueCode.INVALID_TASK_ID in report.codes(),
        f"issues={report.codes()}",
    )

    report = validator.validate([_task("t1"), _task("t1")])
    checks.add(
        "a duplicate task id is rejected",
        not report.valid and PlanIssueCode.DUPLICATE_TASK_ID in report.codes(),
        f"issues={report.codes()}",
    )

    duplicate_with_problems = [
        _task("t1"),
        _task("t1", role="nobody", acceptance_ids=(), reviewers=(), depends_on=("nope",)),
    ]
    report = validator.validate(duplicate_with_problems)
    checks.add(
        "a duplicated task still has its own problems reported",
        set(report.codes())
        >= {
            PlanIssueCode.DUPLICATE_TASK_ID,
            PlanIssueCode.UNKNOWN_ROLE,
            PlanIssueCode.MISSING_ACCEPTANCE,
            PlanIssueCode.UNKNOWN_DEPENDENCY,
        },
        f"issues={report.codes()}",
    )

    report = validator.validate([_task("t1", depends_on=("nope",))])
    checks.add(
        "a dependency on a task that is not in the plan is rejected",
        not report.valid and PlanIssueCode.UNKNOWN_DEPENDENCY in report.codes(),
        f"issues={report.codes()}",
    )

    report = validator.validate([_task("t1", depends_on=("t1",))])
    checks.add(
        "a self-dependency is reported as such, and not as a cycle",
        not report.valid
        and PlanIssueCode.SELF_DEPENDENCY in report.codes()
        and PlanIssueCode.CYCLIC_DEPENDENCY not in report.codes()
        and PlanIssueCode.UNKNOWN_DEPENDENCY not in report.codes(),
        f"issues={report.codes()}",
    )

    report = validator.validate([_task("a", depends_on=("b",)), _task("b", depends_on=("a",))])
    cycle_issue = next((i for i in report.issues if i.code is PlanIssueCode.CYCLIC_DEPENDENCY), None)
    checks.add(
        "a dependency cycle is rejected and named",
        not report.valid
        and cycle_issue is not None
        and "a" in cycle_issue.detail
        and "b" in cycle_issue.detail,
        cycle_issue.detail if cycle_issue else f"issues={report.codes()}",
    )

    report = validator.validate(
        [_task("a", depends_on=("b",)), _task("b", depends_on=("c",)), _task("c", depends_on=("b",))]
    )
    checks.add(
        "a cycle deeper in the graph is still found",
        PlanIssueCode.CYCLIC_DEPENDENCY in report.codes(),
        f"issues={report.codes()}",
    )
    checks.add(
        "a diamond (shared prerequisite) is not a cycle",
        validator.validate(
            [
                _task("root"),
                _task("left", depends_on=("root",)),
                _task("right", depends_on=("root",)),
                _task("join", depends_on=("left", "right")),
            ]
        ).valid,
        "",
    )

    long_chain = [_task(f"t{i}", depends_on=((f"t{i - 1}",) if i else ())) for i in range(2000)]
    report = validator.validate(long_chain)
    checks.add(
        "a long dependency chain returns a report instead of raising",
        report.valid,
        f"issues={report.codes()[:3]}",
    )

    # --- roles --------------------------------------------------------------
    report = validator.validate([_task("t1", role="research")])
    checks.add(
        "a role outside the roster is rejected",
        not report.valid and PlanIssueCode.UNKNOWN_ROLE in report.codes(),
        f"issues={report.codes()}",
    )
    checks.add(
        "a custom roster is honoured",
        PlanValidator(roles=("research", "engineering")).validate([_task("t1", role="research")]).valid,
        "",
    )

    # --- acceptance ---------------------------------------------------------
    report = validator.validate([_task("t1", acceptance_ids=())])
    checks.add(
        "a task with no acceptance condition is rejected",
        not report.valid and PlanIssueCode.MISSING_ACCEPTANCE in report.codes(),
        f"issues={report.codes()}",
    )
    report = validator.validate([_task("t1", acceptance_ids=("   ",))])
    checks.add(
        "a blank acceptance id does not count as an acceptance condition",
        not report.valid and PlanIssueCode.MISSING_ACCEPTANCE in report.codes(),
        f"issues={report.codes()}",
    )

    # --- reviewers on key tasks (acceptance scenario 6) ---------------------
    key_without_reviewer = [_task(RESEARCH, reviewers=()), _task(ENGINEERING, depends_on=(RESEARCH,))]
    report = validator.validate(key_without_reviewer)
    checks.add(
        "a key task without a reviewer is rejected (scenario 6)",
        not report.valid
        and PlanIssueCode.MISSING_REVIEWER in report.codes()
        and any(i.task_id == RESEARCH for i in report.issues if i.code is PlanIssueCode.MISSING_REVIEWER),
        f"issues={report.codes()}",
    )

    checks.add(
        "a task nobody depends on does not need a reviewer",
        validator.validate([_task("solo", reviewers=())]).valid,
        "",
    )

    report = validator.validate(
        [_task("upstream-task", reviewers=(), requires_success=False), _task("consumer", depends_on=("upstream-task",))]
    )
    checks.add(
        "being depended on makes a task key even when it does not require its own prerequisites",
        not report.valid and PlanIssueCode.MISSING_REVIEWER in report.codes(),
        f"issues={report.codes()}",
    )

    report = validator.validate([_task(RESEARCH, reviewers=("  ",)), _task(ENGINEERING, depends_on=(RESEARCH,))])
    checks.add(
        "a blank reviewer name does not satisfy the reviewer requirement",
        not report.valid and PlanIssueCode.MISSING_REVIEWER in report.codes(),
        f"issues={report.codes()}",
    )

    # --- reporting ----------------------------------------------------------
    report = validator.validate([_task("t1", role="research", acceptance_ids=(), reviewers=())])
    checks.add(
        "every problem is reported, not just the first",
        len(report.issues) == 2 and set(report.codes()) == {
            PlanIssueCode.UNKNOWN_ROLE,
            PlanIssueCode.MISSING_ACCEPTANCE,
        },
        f"issues={report.codes()} (t1 is not key, so no reviewer issue)",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure plan validator; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
