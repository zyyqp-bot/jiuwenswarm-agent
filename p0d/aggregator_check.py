"""P0-D (part 1): the aggregator.

PROJECT.md section 6 step 11: ``complete`` requires every mandatory acceptance
condition to pass, otherwise the run reports partial or failed and names what
is missing. Section 4: the aggregator tidies and reports coverage, and must not
turn a conflict or an unsupported claim into a fact.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0d/aggregator_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.aggregator import Aggregator, FinalOutcome, Usage  # noqa: E402
from app.models import AcceptanceCheck, AcceptanceResult, Claim, ClaimKind, TaskOutcome, TaskResult  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "aggregator_results.json"

RESEARCH = "task-research"
ENGINEERING = "task-engineering"


def _result(task_id: str, *, outcome: TaskOutcome = TaskOutcome.SUCCESS, claims=(), artifacts=()) -> TaskResult:
    return TaskResult(
        run_id="run-1",
        task_id=task_id,
        execution_id=f"exec-{task_id}",
        producer=task_id,
        outcome=outcome,
        artifact_refs=artifacts,
        claims=claims,
    )


def _check(acceptance_id: str, result: AcceptanceResult) -> AcceptanceCheck:
    return AcceptanceCheck(acceptance_id=acceptance_id, result=result, verifier="review-team")


FACT = Claim(text="接口存在差异", kind=ClaimKind.FACT, evidence_ids=("ev-1",))
INFERENCE = Claim(text="因此需要重写适配层", kind=ClaimKind.INFERENCE)


def main() -> int:
    checks = Checks()
    aggregator = Aggregator()

    # --- everything passes --------------------------------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[
            _result(RESEARCH, claims=(FACT, FACT), artifacts=("artifact://facts",)),
            _result(ENGINEERING, claims=(INFERENCE,), artifacts=("artifact://facts", "artifact://plan")),
        ],
        required_acceptance_ids=("acc-1", "acc-1"),
        acceptance={"acc-1": _check("acc-1", AcceptanceResult.PASS)},
    )
    checks.add("all conditions passing yields complete", final.outcome is FinalOutcome.COMPLETE, final.outcome.value)
    checks.add(
        "artifacts and cited evidence are de-duplicated across repeated inputs",
        final.artifact_refs == ("artifact://facts", "artifact://plan") and final.evidence_refs == ("ev-1",),
        f"artifacts={final.artifact_refs} evidence={final.evidence_refs}",
    )
    checks.add(
        "a required condition listed twice is reported once",
        len(final.acceptance_checks) == 1,
        f"checks={[c.acceptance_id for c in final.acceptance_checks]}",
    )
    checks.add(
        "the answer keeps each claim's grounding label",
        "[fact] 接口存在差异" in final.answer and "[inference] 因此需要重写适配层" in final.answer,
        final.answer.replace("\n", " | "),
    )
    checks.add("a complete run has no limitations", final.limitations == (), f"{final.limitations}")

    # --- a mandatory condition that did not pass ----------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, claims=(FACT,), artifacts=("artifact://facts",))],
        required_acceptance_ids=("acc-1", "acc-2"),
        acceptance={
            "acc-1": _check("acc-1", AcceptanceResult.PASS),
            "acc-2": _check("acc-2", AcceptanceResult.FAIL),
        },
    )
    checks.add(
        "an unmet condition downgrades the run to partial",
        final.outcome is FinalOutcome.PARTIAL,
        final.outcome.value,
    )
    checks.add(
        "the unmet condition is named in the limitations",
        any("acc-2" in line for line in final.limitations),
        f"{final.limitations}",
    )

    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH)],
        required_acceptance_ids=("acc-missing",),
        acceptance={},
    )
    checks.add(
        "a required condition with no verdict counts as unmet",
        final.outcome is FinalOutcome.PARTIAL and any("acc-missing" in line for line in final.limitations),
        f"outcome={final.outcome.value} limitations={final.limitations}",
    )

    # --- nothing succeeded --------------------------------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, outcome=TaskOutcome.ERROR)],
        required_acceptance_ids=("acc-1",),
        acceptance={"acc-1": _check("acc-1", AcceptanceResult.FAIL)},
    )
    checks.add(
        "an unmet condition with no successful task yields failed",
        final.outcome is FinalOutcome.FAILED,
        final.outcome.value,
    )

    final = aggregator.merge(run_id="run-1", results=[])
    checks.add(
        "a run with no results is failed and says so",
        final.outcome is FinalOutcome.FAILED and final.answer == "" and bool(final.limitations),
        f"limitations={final.limitations}",
    )

    # --- what must not be swallowed ----------------------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, claims=(FACT,)), _result(ENGINEERING, outcome=TaskOutcome.PARTIAL)],
        required_acceptance_ids=(),
        unverified_claim_texts=("接口存在差异",),
        unavailable_evidence_ids=("ev-9",),
    )
    checks.add(
        "an unsupported claim is reported, not dropped",
        any("unsupported claim" in line and "接口存在差异" in line for line in final.limitations),
        f"{final.limitations}",
    )
    checks.add(
        "evidence the run could not obtain is reported",
        any("ev-9" in line for line in final.limitations),
        f"{final.limitations}",
    )
    checks.add(
        "a task that finished without success is reported",
        any(ENGINEERING in line and "partial" in line for line in final.limitations),
        f"{final.limitations}",
    )
    checks.add(
        "reporting limitations does not block the run from completing",
        final.outcome is FinalOutcome.COMPLETE,
        final.outcome.value,
    )

    # --- an unsupported claim is relabelled, not just mentioned -------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, claims=(FACT,))],
        unverified_claim_texts=("接口存在差异",),
    )
    checks.add(
        "a claim whose citation failed is relabelled in the answer",
        "[fact] 接口存在差异" not in final.answer and "[unverified] 接口存在差异" in final.answer,
        final.answer.replace("\n", " | "),
    )
    checks.add(
        "evidence behind an unsupported claim is kept out of the deliverable's evidence",
        final.evidence_refs == (),
        f"evidence_refs={final.evidence_refs}",
    )

    # --- a run where nothing succeeded --------------------------------------
    final = aggregator.merge(run_id="run-1", results=[_result(RESEARCH, outcome=TaskOutcome.ERROR)])
    checks.add(
        "a run whose every task failed is not complete, even with no unmet conditions",
        final.outcome is FinalOutcome.FAILED,
        final.outcome.value,
    )

    # --- cancellation -------------------------------------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, claims=(FACT,), artifacts=("artifact://facts",))],
        cancelled=True,
    )
    checks.add(
        "a cancelled run reports cancelled rather than a success or failure verdict",
        final.outcome is FinalOutcome.CANCELLED
        and any("cancel" in line for line in final.limitations),
        f"outcome={final.outcome.value}",
    )
    checks.add(
        "a cancelled run keeps the artifacts it did produce",
        final.artifact_refs == ("artifact://facts",),
        f"artifacts={final.artifact_refs}",
    )

    # --- advisory coverage and blank references -----------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH, artifacts=("", "artifact://facts"))],
        required_acceptance_ids=(),
        acceptance={"adv-1": _check("adv-1", AcceptanceResult.FAIL)},
    )
    checks.add(
        "a recorded advisory check still appears in the coverage report",
        [c.acceptance_id for c in final.acceptance_checks] == ["adv-1"],
        f"checks={[c.acceptance_id for c in final.acceptance_checks]}",
    )
    checks.add(
        "blank artifact references do not leak into the deliverable",
        final.artifact_refs == ("artifact://facts",),
        f"artifacts={final.artifact_refs}",
    )

    # --- usage --------------------------------------------------------------
    final = aggregator.merge(
        run_id="run-1",
        results=[_result(RESEARCH)],
        usage=Usage(model_calls=3, tokens=1200, quality="estimated"),
    )
    checks.add(
        "the deliverable can report usage with its measurement quality",
        final.usage is not None and final.usage.quality == "estimated" and final.usage.model_calls == 3,
        f"usage={final.usage}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure aggregation; no framework, no Runner, no model",
        note="the answer is a deterministic template, never a model call (section 10)",
    )


if __name__ == "__main__":
    sys.exit(main())
