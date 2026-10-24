"""P1: a plan task must name the member that will execute it.

``scheduled`` dispatch starts a task only when it carries an assignee, and both
specialists share one ``teammate`` agent template, so ``assignee`` is the only
thing that distinguishes them. A plan without it is not executable no matter
how valid its DAG is.

``members=None`` keeps the P0-B callers working: they validate structure, not
assignment.

Offline: no team, no runner, no model.

Run: .venv/Scripts/python.exe p1/plan_assignee_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.plan import PlanIssueCode, PlanTask, PlanValidator  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "plan_assignee_results.json"
MEMBERS = ("research_member", "engineering_member")


def _task(**overrides) -> PlanTask:
    values = {
        "task_id": "research",
        "role": "teammate",
        "instruction": "collect evidence",
        "acceptance_ids": ("a1",),
    }
    values.update(overrides)
    return PlanTask(**values)


def main() -> int:
    checks = Checks()

    report = PlanValidator(members=MEMBERS).validate([_task(assignee="")])
    checks.add(
        "a blank assignee is rejected when members are known",
        not report.valid and PlanIssueCode.MISSING_ASSIGNEE in report.codes(),
        f"codes={[code.value for code in report.codes()]}",
    )

    report = PlanValidator(members=MEMBERS).validate([_task(assignee="ghost")])
    checks.add(
        "an assignee outside the roster is rejected",
        not report.valid and PlanIssueCode.UNKNOWN_ASSIGNEE in report.codes(),
        f"codes={[code.value for code in report.codes()]}",
    )

    report = PlanValidator(members=MEMBERS).validate([_task(assignee="research_member")])
    checks.add(
        "a roster member is accepted",
        report.valid,
        f"issues={[issue.detail for issue in report.issues]}",
    )

    report = PlanValidator().validate([_task(assignee="")])
    checks.add(
        "legacy callers without a member list are unaffected",
        report.valid,
        f"issues={[issue.detail for issue in report.issues]}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="plan validation only; no team, no runner, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
