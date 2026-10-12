"""Rework policy: at most one targeted re-run, and only when it can help.

PROJECT.md section 6 step 10 allows **one** business rework, and section 10
repeats it: "at most one targeted rework; if the conflict persists, mark it
partial/unknown". Two words carry the weight:

- *at most one* -- a task that failed twice is not retried a third time. Without
  a ceiling, a task that cannot succeed consumes the run;
- *targeted* -- a re-run is only worth it when the failure is something this
  task's own attempt could fix. A failed prerequisite is not a defect in the
  dependent task, so re-running the dependent would produce the same result and
  burn budget to prove it.

A re-run must open a **new execution**. That is what makes the previous
attempt's result unusable, and it comes for free from the execution ledger:
opening a new execution supersedes the old one, so a late result from the first
attempt is refused.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = ["ReworkBlockReason", "ReworkDecision", "ReworkPolicy", "ReworkReason"]

DEFAULT_MAX_ATTEMPTS = 1
"""PROJECT.md section 6: one business rework, not a retry loop."""


class ReworkReason(str, Enum):
    """Why a re-run was requested."""

    ACCEPTANCE_FAILED = "acceptance_failed"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    EVIDENCE_CONFLICT = "evidence_conflict"
    DEPENDENCY_FAILED = "dependency_failed"
    """A prerequisite failed. Not a defect in this task."""


class ReworkBlockReason(str, Enum):
    """Why a re-run was refused."""

    NOT_A_TASK_DEFECT = "not_a_task_defect"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    NO_BUDGET = "no_budget"


class ReworkDecision(BaseModel):
    """Whether a task may be re-run, and which rework this would be."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: ReworkBlockReason | None = None
    attempt: int = 0
    """Which rework this is: 1 for the first. 0 when refused."""


class ReworkPolicy:
    """Decide whether a task's failure warrants another attempt."""

    def __init__(self, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        if max_attempts < 0:
            raise ValueError("max_attempts cannot be negative")
        self._max_attempts = max_attempts

    @property
    def max_attempts(self) -> int:
        """How many reworks are allowed after the first attempt.

        Not a total attempt count: with the default of 1 a task runs once, and
        may be re-run once.
        """
        return self._max_attempts

    def evaluate(
        self,
        *,
        reworks_so_far: int,
        reason: ReworkReason | str,
        budget_has_room: bool,
    ) -> ReworkDecision:
        """Return whether another attempt is allowed.

        ``reworks_so_far`` counts *previous reworks*, not total attempts: a task
        on its first attempt passes 0.

        ``budget_has_room`` has no default on purpose. A default of True would
        quietly disable the budget gate for a caller that forgot to pass it,
        which is exactly the case the gate exists for.

        Raises:
            ValueError: ``reworks_so_far`` is negative, or ``reason`` is not a
                known reason.
        """
        if reworks_so_far < 0:
            raise ValueError("reworks_so_far cannot be negative")
        # Coerce rather than compare with ``is``: a reason that arrived as a
        # plain string from config or JSON is the same reason, and comparing
        # identities would let it slip past the guards below.
        try:
            resolved = ReworkReason(reason)
        except ValueError as exc:
            raise ValueError(f"unknown rework reason {reason!r}") from exc

        # The checks answer different questions, so the order is part of the
        # rule: whether re-running could help at all, then whether another
        # attempt is still permitted, then whether the run can afford it.
        if resolved is ReworkReason.DEPENDENCY_FAILED:
            return ReworkDecision(allowed=False, reason=ReworkBlockReason.NOT_A_TASK_DEFECT)
        if reworks_so_far >= self._max_attempts:
            return ReworkDecision(allowed=False, reason=ReworkBlockReason.ATTEMPTS_EXHAUSTED)
        if not budget_has_room:
            return ReworkDecision(allowed=False, reason=ReworkBlockReason.NO_BUDGET)
        return ReworkDecision(allowed=True, attempt=reworks_so_far + 1)
