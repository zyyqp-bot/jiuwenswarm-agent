"""P2: the final report keeps claim kinds, citations and limitations.

The report is what a judge reads, so it must not smooth over a missing task or
drop the evidence ids. The Aggregator owns the verdict; this module owns the
deterministic Markdown and the two files the API serves.

Offline: fixtures only.

Run: .venv/Scripts/python.exe p1/report_check.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import Claim, ClaimKind, TaskOutcome, TaskResult  # noqa: E402
from app.plan import PlanTask  # noqa: E402
from app.report import build_report, report_markdown, write_report  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "report_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="collect",
        acceptance_ids=("a1",),
        assignee="research",
    ),
    PlanTask(
        task_id="engineering",
        role="teammate",
        instruction="propose",
        acceptance_ids=("a2",),
        assignee="engineering",
    ),
]

RESULTS = [
    TaskResult(
        run_id="run-1",
        task_id="research",
        execution_id="run-1:research:1",
        producer="research",
        outcome=TaskOutcome.SUCCESS,
        artifact_refs=("claims:research",),
        claims=(
            Claim(text="政策要求进行安全评估", kind=ClaimKind.FACT, evidence_ids=("ev-1",)),
            Claim(text="因此需要建立台账", kind=ClaimKind.INFERENCE, evidence_ids=("ev-2",)),
        ),
    ),
    TaskResult(
        run_id="run-1",
        task_id="engineering",
        execution_id="run-1:engineering:1",
        producer="engineering",
        outcome=TaskOutcome.SUCCESS,
        artifact_refs=("claims:engineering",),
        claims=(Claim(text="上线前完成评估", kind=ClaimKind.FACT, evidence_ids=("ev-3",)),),
    ),
]


def main() -> int:
    checks = Checks()

    complete = build_report(run_id="run-1", results=RESULTS, plan=PLAN)
    checks.add(
        "both tasks and both acceptances yield a complete run",
        complete.outcome.value == "complete",
        f"outcome={complete.outcome.value} limitations={complete.limitations}",
    )
    checks.add(
        "the answer keeps claim kinds",
        "[fact]" in complete.answer and "[inference]" in complete.answer,
        complete.answer,
    )
    checks.add(
        "the evidence from every successful task is kept",
        set(complete.evidence_refs) == {"ev-1", "ev-2", "ev-3"},
        f"evidence={complete.evidence_refs}",
    )

    # Acceptance is structural, so it must reject claims the task cannot
    # support -- not sign every declared id as soon as a result exists.
    uncited = build_report(
        run_id="run-2",
        results=[
            TaskResult(
                run_id="run-2",
                task_id="research",
                execution_id="run-2:research:1",
                producer="research",
                outcome=TaskOutcome.SUCCESS,
                claims=(Claim(text="凭空的事实", kind=ClaimKind.FACT, evidence_ids=()),),
            )
        ],
        plan=PLAN[:1],
        evidence_by_task={"research": ["ev-1"]},
    )
    checks.add(
        "a fact without evidence fails acceptance",
        uncited.outcome.value != "complete"
        and any(check.result.value == "fail" for check in uncited.acceptance_checks),
        f"outcome={uncited.outcome.value} checks={[(c.acceptance_id, c.result.value) for c in uncited.acceptance_checks]}",
    )

    foreign = build_report(
        run_id="run-3",
        results=[
            TaskResult(
                run_id="run-3",
                task_id="research",
                execution_id="run-3:research:1",
                producer="research",
                outcome=TaskOutcome.SUCCESS,
                claims=(
                    Claim(text="引用了别的任务的证据", kind=ClaimKind.FACT, evidence_ids=("ev-9",)),
                ),
            )
        ],
        plan=PLAN[:1],
        evidence_by_task={"research": ["ev-1"]},
    )
    checks.add(
        "a fact citing evidence the task never retrieved fails acceptance",
        foreign.outcome.value != "complete"
        and any(check.result.value == "fail" for check in foreign.acceptance_checks),
        f"outcome={foreign.outcome.value} checks={[(c.acceptance_id, c.result.value) for c in foreign.acceptance_checks]}",
    )

    empty = build_report(
        run_id="run-4",
        results=[
            TaskResult(
                run_id="run-4",
                task_id="research",
                execution_id="run-4:research:1",
                producer="research",
                outcome=TaskOutcome.SUCCESS,
            )
        ],
        plan=PLAN[:1],
    )
    checks.add(
        "a result with no claims fails acceptance",
        empty.outcome.value != "complete"
        and any(check.result.value == "fail" for check in empty.acceptance_checks),
        f"outcome={empty.outcome.value}",
    )

    failed_task = build_report(
        run_id="run-5",
        results=[
            TaskResult(
                run_id="run-5",
                task_id="research",
                execution_id="run-5:research:1",
                producer="research",
                outcome=TaskOutcome.ERROR,
                claims=(Claim(text="x", kind=ClaimKind.INFERENCE),),
            )
        ],
        plan=PLAN[:1],
    )
    checks.add(
        "a failed task result fails acceptance",
        failed_task.outcome.value != "complete"
        and any(check.result.value == "fail" for check in failed_task.acceptance_checks),
        f"outcome={failed_task.outcome.value}",
    )

    partial = build_report(run_id="run-1", results=RESULTS[:1], plan=PLAN)
    checks.add(
        "a missing task makes the run partial",
        partial.outcome.value == "partial",
        f"outcome={partial.outcome.value}",
    )
    checks.add(
        "the missing acceptance is named as a limitation",
        any("a2" in item for item in partial.limitations),
        f"limitations={partial.limitations}",
    )

    empty = build_report(run_id="run-1", results=[], plan=PLAN)
    checks.add(
        "no results is a failed run, not a crash",
        empty.outcome.value == "failed" and any("no task result" in item for item in empty.limitations),
        f"outcome={empty.outcome.value} limitations={empty.limitations}",
    )

    markdown = report_markdown(complete, task_results=RESULTS)
    checks.add(
        "the Markdown has the required sections",
        all(section in markdown for section in ("## 结论", "## 限制", "## 证据")),
        markdown[:200],
    )
    checks.add(
        "the Markdown keeps the claim text, kind and evidence id",
        "政策要求进行安全评估" in markdown
        and "[fact]" in markdown
        and "ev-1" in markdown
        and "ev-3" in markdown,
        markdown[:300],
    )

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        write_report(directory, complete, task_results=RESULTS)
        written_json = directory / "report.json"
        written_md = directory / "report.md"
        loaded = json.loads(written_json.read_text(encoding="utf-8"))
        checks.add(
            "report.json and report.md are written atomically",
            written_json.is_file()
            and written_md.is_file()
            and not list(directory.glob("*.tmp"))
            and loaded["outcome"] == "complete",
            f"files={sorted(p.name for p in directory.iterdir())}",
        )
        checks.add(
            "report.md on disk matches the returned Markdown",
            written_md.read_text(encoding="utf-8") == markdown,
            "markdown round-trip",
        )

    return checks.report(
        RESULTS_PATH,
        scope="report assembly with fixtures; no team, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
