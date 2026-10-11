"""Completion gate: a finished framework task is not an accepted result.

PROJECT.md section 3 records that ``enable_task_verification`` is only a prompt
switch -- it does not guarantee a task has a reviewer -- and section 6 requires
that a result only completes a task when it carries a valid artifact and its
required acceptance conditions pass. A framework task reaching ``completed``
therefore proves nothing on its own.

This gate is the application-side check run before a task is accepted. It
verifies:

- a result exists and belongs to this task, run and execution,
- that execution is still the task's current one,
- the business outcome is SUCCESS,
- at least one usable artifact was produced,
- no reviewer is the producer of the result, and
- every required acceptance condition passed, judged by someone other than the
  producer.

Identity comes from the records themselves -- ``TaskResult.producer`` and
``AcceptanceCheck.verifier`` -- never from an argument the caller supplies at
check time. Otherwise the caller would choose the string the independence
checks compare against, and the guarantees would be self-certified.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.execution import ExecutionLedger, StaleExecutionError, UnknownExecutionError
from app.models import AcceptanceCheck, AcceptanceResult, TaskOutcome, TaskResult, usable_artifacts

__all__ = [
    "CompletionBlockReason",
    "CompletionDecision",
    "CompletionGate",
]


class CompletionBlockReason(str, Enum):
    """Why a task may not be accepted as complete."""

    RESULT_MISSING = "result_missing"
    """No result was submitted for the task."""

    RESULT_MISMATCHED = "result_mismatched"
    """The result does not belong to the task/run/execution being completed."""

    UNKNOWN_EXECUTION = "unknown_execution"
    """The task has no open execution, or this execution was never opened."""

    STALE_EXECUTION = "stale_execution"
    """The execution has been superseded by a newer attempt."""

    OUTCOME_NOT_SUCCESS = "outcome_not_success"
    """The business outcome is PARTIAL or ERROR."""

    MISSING_ARTIFACT = "missing_artifact"
    """The result carries no usable artifact."""

    REVIEWER_MISSING = "reviewer_missing"
    """The task requires a reviewer and none was recorded."""

    REVIEWER_IS_AUTHOR = "reviewer_is_author"
    """A reviewer is the producer of the result being reviewed."""

    ACCEPTANCE_NOT_PASSED = "acceptance_not_passed"
    """A required acceptance condition is missing, mismatched or did not pass."""

    ACCEPTANCE_SELF_VERIFIED = "acceptance_self_verified"
    """A required acceptance condition was signed by its own producer, or by nobody."""


class CompletionDecision(BaseModel):
    """The gate's verdict for one task."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: CompletionBlockReason | None = None
    detail: str | None = None


def _named(values: Sequence[str]) -> list[str]:
    """Return the entries that actually name someone or something."""
    return [value for value in values if value.strip()]


class CompletionGate:
    """Decide whether a task's result may complete it."""

    def __init__(self, ledger: ExecutionLedger) -> None:
        self._ledger = ledger

    def evaluate(
        self,
        *,
        run_id: str,
        task_id: str,
        execution_id: str,
        result: TaskResult | None,
        reviewers: Sequence[str] = (),
        require_reviewer: bool = False,
        required_acceptance_ids: Sequence[str] = (),
        acceptance: Mapping[str, AcceptanceCheck] | None = None,
    ) -> CompletionDecision:
        """Return whether the task may be completed.

        ``require_reviewer`` marks a task as requiring independent review
        (PROJECT.md section 6: key tasks must have one). ``required_acceptance_ids``
        are the conditions that must pass; conditions not listed are advisory.
        """
        if result is None:
            return _block(CompletionBlockReason.RESULT_MISSING, f"no result submitted for task {task_id!r}")

        if result.run_id != run_id or result.task_id != task_id or result.execution_id != execution_id:
            return _block(
                CompletionBlockReason.RESULT_MISMATCHED,
                f"result belongs to run={result.run_id!r} task={result.task_id!r} "
                f"execution={result.execution_id!r}",
            )

        try:
            self._ledger.validate_submission(run_id, task_id, execution_id)
        except UnknownExecutionError as exc:
            return _block(CompletionBlockReason.UNKNOWN_EXECUTION, str(exc))
        except StaleExecutionError as exc:
            return _block(CompletionBlockReason.STALE_EXECUTION, str(exc))

        if result.outcome is not TaskOutcome.SUCCESS:
            return _block(
                CompletionBlockReason.OUTCOME_NOT_SUCCESS,
                f"business outcome is {result.outcome.value!r}",
            )

        if not usable_artifacts(result.artifact_refs):
            return _block(CompletionBlockReason.MISSING_ARTIFACT, "the result carries no usable artifact")

        named_reviewers = _named(reviewers)
        if require_reviewer and not named_reviewers:
            return _block(CompletionBlockReason.REVIEWER_MISSING, "the task requires a reviewer")

        if result.producer in named_reviewers:
            return _block(
                CompletionBlockReason.REVIEWER_IS_AUTHOR,
                f"reviewer {result.producer!r} produced the result being reviewed",
            )

        conditions = acceptance or {}
        unmet: list[str] = []
        self_verified: list[str] = []
        for acceptance_id in required_acceptance_ids:
            check = conditions.get(acceptance_id)
            if check is None or check.acceptance_id != acceptance_id or check.result is not AcceptanceResult.PASS:
                unmet.append(acceptance_id)
                continue
            # Structural acceptance may be judged by a program, so the rule is
            # "not the producer", not "must be a human reviewer".
            if not check.verifier.strip() or check.verifier.strip() == result.producer:
                self_verified.append(acceptance_id)

        if unmet:
            return _block(
                CompletionBlockReason.ACCEPTANCE_NOT_PASSED,
                "acceptance not passed: " + ", ".join(unmet),
            )
        if self_verified:
            return _block(
                CompletionBlockReason.ACCEPTANCE_SELF_VERIFIED,
                "acceptance signed by the producer or unverifiable: " + ", ".join(self_verified),
            )

        return CompletionDecision(allowed=True)


def _block(reason: CompletionBlockReason, detail: str) -> CompletionDecision:
    return CompletionDecision(allowed=False, reason=reason, detail=detail)
