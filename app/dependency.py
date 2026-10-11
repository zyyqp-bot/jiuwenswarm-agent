"""Dependency gate: a resolved edge is not a successful prerequisite.

The framework resolves a task's downstream dependency edges on both
``COMPLETED`` and ``CANCELLED`` (PROJECT.md section 3, S07/P06). A cancelled
prerequisite therefore unblocks its dependents exactly like a successful one --
"the edge is resolved" says nothing about whether the work succeeded.

This gate is the application-side check that runs before a task's business
execution starts. For every prerequisite a task declares as success-critical it
requires:

- the prerequisite is not the task itself,
- a result exists and actually belongs to that prerequisite in this run,
- the result is from the prerequisite's *current* execution,
- its business outcome is SUCCESS, and
- it carries at least one non-blank artifact reference.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.execution import ExecutionLedger
from app.models import TaskOutcome, TaskResult, usable_artifacts

__all__ = ["BlockReason", "DependencyDecision", "DependencyGate"]


class BlockReason(str, Enum):
    """Why a task may not start."""

    SELF_DEPENDENCY = "self_dependency"
    """The task lists itself as its own prerequisite."""

    DEPENDENCY_PENDING = "dependency_pending"
    """The prerequisite has no result yet."""

    MISMATCHED_RESULT = "mismatched_result"
    """The result supplied under this prerequisite belongs to another task or run."""

    DEPENDENCY_STALE = "dependency_stale"
    """The prerequisite's result is not from its current execution."""

    DEPENDENCY_FAILED = "dependency_failed"
    """The prerequisite finished without success."""

    MISSING_ARTIFACT = "missing_artifact"
    """The prerequisite succeeded but produced no usable artifact to depend on."""


class DependencyDecision(BaseModel):
    """The gate's verdict for one task."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: BlockReason | None = None
    blocked_by: str | None = None
    """The prerequisite task id responsible for a block."""


class DependencyGate:
    """Decide whether a task may execute, given its prerequisites' results.

    ``requires_success=False`` marks a dependency the task merely needs to have
    *finished* -- a failed prerequisite does not block it. Even then the result
    must belong to the prerequisite's current execution in this run: a stale or
    foreign result means the task is waiting on work that is being redone, or
    on work that is not the work it named.
    """

    def __init__(self, ledger: ExecutionLedger) -> None:
        self._ledger = ledger

    def evaluate(
        self,
        *,
        run_id: str,
        task_id: str,
        depends_on: Sequence[str],
        requires_success: bool,
        results: Mapping[str, TaskResult],
    ) -> DependencyDecision:
        """Return whether ``task_id`` may start."""
        for prerequisite in depends_on:
            if prerequisite == task_id:
                return self._block(BlockReason.SELF_DEPENDENCY, prerequisite)

            result = results.get(prerequisite)
            if result is None:
                return self._block(BlockReason.DEPENDENCY_PENDING, prerequisite)

            # The mapping key is caller-supplied; the result must agree with it.
            if result.task_id != prerequisite or result.run_id != run_id:
                return self._block(BlockReason.MISMATCHED_RESULT, prerequisite)

            current = self._ledger.current(run_id, prerequisite)
            if current is None or current.execution_id != result.execution_id:
                return self._block(BlockReason.DEPENDENCY_STALE, prerequisite)

            if result.outcome is not TaskOutcome.SUCCESS:
                if requires_success:
                    return self._block(BlockReason.DEPENDENCY_FAILED, prerequisite)
                continue

            if requires_success and not usable_artifacts(result.artifact_refs):
                return self._block(BlockReason.MISSING_ARTIFACT, prerequisite)

        return DependencyDecision(allowed=True)

    @staticmethod
    def _block(reason: BlockReason, prerequisite: str) -> DependencyDecision:
        return DependencyDecision(allowed=False, reason=reason, blocked_by=prerequisite)
