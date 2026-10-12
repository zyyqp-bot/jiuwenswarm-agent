"""P0-C (part 4): citation validation.

PROJECT.md's "citation support rate" and its rule that unverified conclusions
must not be presented as fact both rest on this check. The case that motivated
it is the joined one at the end: evidence the context builder trimmed away is
still in the corpus, and a result citing it is citing something the member
never saw.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0c/citations_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.citations import CitationIssueCode, CitationValidator  # noqa: E402
from app.context import ContextBudget, ContextBuilder  # noqa: E402
from app.models import Claim, ClaimKind, TaskOutcome, TaskResult  # noqa: E402
from checks.fixtures import evidence as _evidence  # noqa: E402
from checks.harness import Checks, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "citations_results.json"


def main() -> int:
    checks = Checks()
    validator = CitationValidator()
    available = {"ev-1", "ev-2"}

    report = validator.validate(
        [Claim(text="接口存在差异", kind=ClaimKind.FACT, evidence_ids=("ev-1",))],
        available_evidence_ids=available,
    )
    checks.add("a fact citing evidence the task received is valid", report.valid, f"issues={report.codes()}")

    report = validator.validate(
        [Claim(text="接口存在差异", kind=ClaimKind.FACT)],
        available_evidence_ids=available,
    )
    checks.add(
        "a fact that cites nothing is rejected",
        not report.valid and report.codes() == [CitationIssueCode.UNCITED_FACT],
        f"issues={report.codes()}",
    )

    report = validator.validate(
        [Claim(text="接口存在差异", kind=ClaimKind.FACT, evidence_ids=("ev-9",))],
        available_evidence_ids=available,
    )
    checks.add(
        "a fact citing evidence the task never received is rejected",
        not report.valid and CitationIssueCode.UNKNOWN_EVIDENCE in report.codes(),
        f"issues={report.codes()}",
    )

    for kind in (ClaimKind.INFERENCE, ClaimKind.ASSUMPTION):
        report = validator.validate(
            [Claim(text="我推断如此", kind=kind)],
            available_evidence_ids=available,
        )
        checks.add(
            f"an unsupported {kind.value} is allowed when it is labelled as such",
            report.valid,
            f"issues={report.codes()}",
        )

    report = validator.validate(
        [Claim(text="   ", kind=ClaimKind.INFERENCE)],
        available_evidence_ids=available,
    )
    checks.add(
        "a claim with no text is rejected",
        not report.valid and report.codes() == [CitationIssueCode.BLANK_CLAIM],
        f"issues={report.codes()}",
    )

    report = validator.validate([], available_evidence_ids=available)
    checks.add("a result with no claims is valid", report.valid, "")

    report = validator.validate(
        [
            Claim(text="事实一", kind=ClaimKind.FACT, evidence_ids=("ev-1",)),
            Claim(text="事实二", kind=ClaimKind.FACT),
            Claim(text="事实三", kind=ClaimKind.FACT, evidence_ids=("ev-1", "ev-3")),
        ],
        available_evidence_ids=available,
    )
    checks.add(
        "every problem is reported, with the claim that has it",
        set(report.codes()) == {CitationIssueCode.UNCITED_FACT, CitationIssueCode.UNKNOWN_EVIDENCE}
        and {issue.claim_index for issue in report.issues} == {1, 2},
        f"indexes={sorted({i.claim_index for i in report.issues})}",
    )

    # --- the joined case: evidence dropped by the context builder ----------
    budget = ContextBudget(context_window=70, max_output_tokens=0, safety_margin=0)
    bundle = ContextBuilder().build(
        budget=budget,
        task_instruction="核对接口",
        evidence=[_evidence(1, "甲" * 10), _evidence(2, "乙" * 60)],
    )
    checks.add(
        "the context builder dropped the oversized evidence (precondition)",
        [item.evidence_id for item in bundle.evidence] == ["ev-1"] and "ev-2" in bundle.dropped_evidence_ids,
        f"kept={[i.evidence_id for i in bundle.evidence]} dropped={bundle.dropped_evidence_ids}",
    )

    received = {item.evidence_id for item in bundle.evidence}
    report = validator.validate(
        [Claim(text="依据第二份资料", kind=ClaimKind.FACT, evidence_ids=("ev-2",))],
        available_evidence_ids=received,
    )
    checks.add(
        "citing evidence this task's budget trimmed away is rejected, even though it is in the corpus",
        not report.valid and report.codes() == [CitationIssueCode.UNKNOWN_EVIDENCE],
        f"issues={report.codes()}",
    )

    # --- blank ids are not citations ---------------------------------------
    report = validator.validate(
        [Claim(text="事实", kind=ClaimKind.FACT, evidence_ids=("", "  "))],
        available_evidence_ids=available,
    )
    checks.add(
        "a fact citing only blank ids is uncited, not merely unknown",
        CitationIssueCode.UNCITED_FACT in report.codes(),
        f"issues={report.codes()}",
    )
    report = validator.validate(
        [Claim(text="事实", kind=ClaimKind.FACT, evidence_ids=("",))],
        available_evidence_ids={"", "ev-1"},
    )
    checks.add(
        "a blank id present in the available set still does not make a fact cited",
        not report.valid and report.codes() == [CitationIssueCode.UNCITED_FACT],
        f"issues={report.codes()}",
    )

    # --- the single-string footgun -----------------------------------------
    ok, detail = raises(lambda: validator.validate([], available_evidence_ids="ev-1"), expected=TypeError)
    checks.add("passing a single id string is rejected instead of split into characters", ok, detail)

    # --- issues carry the offending ids as data ----------------------------
    report = validator.validate(
        [Claim(text="事实", kind=ClaimKind.FACT, evidence_ids=("ev-1", "ev-3", "ev-4"))],
        available_evidence_ids=available,
    )
    issue = next(item for item in report.issues if item.code is CitationIssueCode.UNKNOWN_EVIDENCE)
    checks.add(
        "the issue names the missing ids as a field, not only in prose",
        issue.evidence_ids == ("ev-3", "ev-4"),
        f"evidence_ids={issue.evidence_ids}",
    )

    # --- a result carries its own claims -----------------------------------
    result = TaskResult(
        run_id="run-1",
        task_id="task-research",
        execution_id="exec-1",
        producer="research",
        outcome=TaskOutcome.SUCCESS,
        artifact_refs=("artifact://facts",),
        claims=(Claim(text="接口存在差异", kind=ClaimKind.FACT, evidence_ids=("ev-1",)),),
    )
    checks.add(
        "a TaskResult carries its claims, so a submission reaches the validator",
        len(result.claims) == 1
        and validator.validate(result.claims, available_evidence_ids=available).valid,
        f"claims={len(result.claims)}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure citation validation; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
