"""Aggregator: merge accepted results into one deliverable, without overclaiming.

PROJECT.md section 4 fixes the aggregator's role -- "structural tidying and a
coverage report; it must not quietly resolve a conflict into a fact" -- and
section 6 step 11 fixes the verdict rule: ``complete`` requires every mandatory
acceptance condition to pass, otherwise the run reports ``partial`` or
``failed`` and names what is missing. Section 10 requires the fallback to be a
**deterministic template**, not a model call, so this module never asks a model
to write the summary.

Three things are therefore deliberate:

- the answer is assembled from the results' own claims, each labelled with how
  it is grounded, so an inference cannot be read as a fact;
- a claim whose citation did not survive validation is **relabelled** rather
  than merely listed as a limitation -- a reader skimming the deliverable must
  not see "fact" on something the run could not support, and its evidence is
  kept out of the deliverable's evidence list;
- anything that could not be established -- an unmet condition, an unsupported
  claim -- is carried into ``limitations`` rather than dropped.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.models import (
    AcceptanceCheck,
    AcceptanceResult,
    ClaimKind,
    TaskOutcome,
    TaskResult,
    usable_artifacts,
)

__all__ = ["Aggregator", "FinalOutcome", "FinalResult", "Usage", "UNVERIFIED_MARKER"]

UNVERIFIED_MARKER = "[unverified]"
"""Replaces a claim's own marker when its citation failed validation."""

_CLAIM_MARKERS: dict[ClaimKind, str] = {
    ClaimKind.FACT: "[fact]",
    ClaimKind.INFERENCE: "[inference]",
    ClaimKind.ASSUMPTION: "[assumption]",
}
_UNLABELLED_MARKER = "[unlabelled]"
"""Used for a claim kind this module does not know about, so a new ClaimKind
degrades to "not labelled" instead of aborting the whole aggregation."""


class FinalOutcome(str, Enum):
    """The verdict for a whole run."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Usage(BaseModel):
    """What the run consumed, and how trustworthy that number is.

    PROJECT.md section 10 forbids recording a call with no provider usage as a
    zero-cost success, so ``quality`` is explicit: ``provider`` when measured,
    ``estimated`` when derived, ``unknown`` when neither.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_calls: int = 0
    tokens: int = 0
    quality: str = "unknown"


class FinalResult(BaseModel):
    """The run's deliverable, with what it could not establish."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    outcome: FinalOutcome
    answer: str
    acceptance_checks: tuple[AcceptanceCheck, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    usage: Usage | None = None
    conflicts: tuple[dict[str, Any], ...] = ()
    """Candidate conflicts across sources: shown, never resolved."""


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """Drop blanks and de-duplicate, preserving first-seen order."""
    return tuple(dict.fromkeys(value for value in values if value.strip()))


class Aggregator:
    """Merge task results into a final result."""

    def merge(
        self,
        *,
        run_id: str,
        results: Sequence[TaskResult],
        required_acceptance_ids: Sequence[str] = (),
        acceptance: Mapping[str, AcceptanceCheck] | None = None,
        unverified_claim_texts: Iterable[str] = (),
        unavailable_evidence_ids: Iterable[str] = (),
        cancelled: bool = False,
        usage: Usage | None = None,
    ) -> FinalResult:
        """Return the run's final result.

        Args:
            results: Every accepted task result.
            required_acceptance_ids: Conditions that must pass for ``complete``.
            acceptance: Every acceptance check recorded for this run, required
                or advisory; all of them appear in the coverage report.
            unverified_claim_texts: Claims whose citations did not survive
                validation. They are relabelled in the answer, reported as
                limitations, and their evidence is kept out of ``evidence_refs``.
            unavailable_evidence_ids: Evidence the run wanted but could not
                obtain, e.g. what the context budget trimmed.
            cancelled: The run was cancelled (PROJECT.md section 10). The
                deliverable is frozen as-is and reported as ``cancelled`` rather
                than being forced into a failure or success verdict.
            usage: What the run consumed, with its measurement quality.
        """
        checks_map = dict(acceptance or {})
        unverified = {text for text in unverified_claim_texts}

        ordered_ids = list(dict.fromkeys(required_acceptance_ids))
        conditions = tuple(
            checks_map[acceptance_id]
            for acceptance_id in ordered_ids
            # A check filed under the wrong id is not evidence for this
            # condition, exactly as the completion gate treats it.
            if (check := checks_map.get(acceptance_id)) is not None and check.acceptance_id == acceptance_id
        )
        advisory = tuple(
            checks_map[acceptance_id]
            for acceptance_id in sorted(checks_map)
            if acceptance_id not in ordered_ids and checks_map[acceptance_id].acceptance_id == acceptance_id
        )

        limitations: list[str] = []
        if cancelled:
            limitations.append("the run was cancelled; its produced artifacts are frozen")

        unmet = [
            acceptance_id
            for acceptance_id in ordered_ids
            if (check := checks_map.get(acceptance_id)) is None
            or check.acceptance_id != acceptance_id
            or check.result is not AcceptanceResult.PASS
        ]

        if not results:
            return FinalResult(
                run_id=run_id,
                outcome=FinalOutcome.CANCELLED if cancelled else FinalOutcome.FAILED,
                answer="",
                acceptance_checks=conditions + advisory,
                limitations=tuple([*limitations, "no task result was produced"]),
                usage=usage,
            )

        succeeded = [result for result in results if result.outcome is TaskOutcome.SUCCESS]

        if cancelled:
            outcome = FinalOutcome.CANCELLED
        elif not succeeded:
            # No task produced a successful result, so the run cannot be
            # complete no matter which conditions happened to be recorded.
            outcome = FinalOutcome.FAILED
        elif unmet:
            outcome = FinalOutcome.PARTIAL
        else:
            outcome = FinalOutcome.COMPLETE

        for acceptance_id in unmet:
            check = checks_map.get(acceptance_id)
            state = check.result.value if check is not None else "missing"
            limitations.append(f"acceptance {acceptance_id} is {state}")
        for text in unverified_claim_texts:
            limitations.append(f"unsupported claim: {text}")
        for evidence_id in unavailable_evidence_ids:
            limitations.append(f"evidence not available: {evidence_id}")
        for result in results:
            if result.outcome is not TaskOutcome.SUCCESS:
                limitations.append(f"task {result.task_id} finished with outcome {result.outcome.value}")

        return FinalResult(
            run_id=run_id,
            outcome=outcome,
            answer=self._render(results, unverified),
            acceptance_checks=conditions + advisory,
            # Unverified claims are excluded rather than silently supporting the
            # deliverable with evidence the run flagged as not validating.
            evidence_refs=_unique(
                evidence_id
                for result in succeeded
                for claim in result.claims
                if claim.text not in unverified
                for evidence_id in claim.evidence_ids
            ),
            artifact_refs=_unique(
                artifact for result in succeeded for artifact in usable_artifacts(result.artifact_refs)
            ),
            limitations=tuple(limitations),
            usage=usage,
        )

    @staticmethod
    def _render(results: Sequence[TaskResult], unverified: set[str]) -> str:
        """Render the answer from the results' own claims, deterministically.

        Every claim keeps the label it was submitted with, except a claim whose
        citation failed validation: it is marked unverified, so an unsupported
        conclusion never reaches the reader wearing a fact label.
        """
        lines: list[str] = []
        for result in results:
            lines.append(f"## {result.task_id} ({result.outcome.value})")
            if not result.claims:
                lines.append("- (no claims)")
                continue
            for claim in result.claims:
                if claim.text in unverified:
                    marker = UNVERIFIED_MARKER
                else:
                    marker = _CLAIM_MARKERS.get(claim.kind, _UNLABELLED_MARKER)
                lines.append(f"- {marker} {claim.text}")
        return "\n".join(lines)
