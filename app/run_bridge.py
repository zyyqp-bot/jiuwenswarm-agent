"""The run bridge: one authoritative store for a run's validated task results.

Every teammate submits through the same bridge. It enforces the four rules the
application relies on and that the framework alone cannot:

- the producer is the task's assignee, not whoever calls the tool;
- a result for one execution is accepted once;
- a fact must cite evidence this task actually received;
- the completion gate must accept the result before the native board is moved.

The bridge is process-local, which is sound because members run in-process
(``spawn_mode="inprocess"``). A member running in another process would need a
different carrier; the supervisor never builds one.

The bridge performs no I/O and calls no model.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.citations import CitationValidator
from app.completion import CompletionGate
from app.execution import ExecutionLedger
from app.models import AcceptanceCheck, Claim, TaskOutcome, TaskResult, non_blank, usable_artifacts
from app.plan import PlanTask


@dataclass(frozen=True)
class SubmitOutcome:
    """The bridge's verdict on one submission."""

    ok: bool
    error: str = ""


class RunBridge:
    """Hold one run's plan, executions and accepted results."""

    def __init__(
        self,
        *,
        run_id: str,
        plan: Sequence[PlanTask],
        evidence_by_task: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.run_id = run_id
        self._plan: dict[str, PlanTask] = {task.task_id: task for task in plan}
        self._evidence: dict[str, set[str]] = {
            task_id: set(ids) for task_id, ids in (evidence_by_task or {}).items()
        }
        self._ledger = ExecutionLedger()
        self._citations = CitationValidator()
        self._completion = CompletionGate(self._ledger)
        self._executions: dict[str, str] = {}
        self._results: dict[str, TaskResult] = {}
        self._submitted: set[tuple[str, str]] = set()
        self._complete: Callable[[str], Awaitable[None]] | None = None
        self._lock = threading.Lock()
        self.bootstrapped = False
        self.tool_names: list[str] = []
        self.errors: list[str] = []

    # -- wiring -------------------------------------------------------------
    def bind_native_complete(self, complete: Callable[[str], Awaitable[None]]) -> None:
        """Bind the callback that completes the native task after acceptance."""
        self._complete = complete

    def execution_for(self, task_id: str) -> str:
        """Return this task's execution id, opening the first one when needed."""
        with self._lock:
            if task_id not in self._plan:
                raise KeyError(f"task {task_id!r} is not in the plan for run {self.run_id!r}")
            execution_id = self._executions.get(task_id)
            if execution_id is None:
                execution_id = f"{self.run_id}:{task_id}:1"
                self._ledger.open(self.run_id, task_id, execution_id)
                self._executions[task_id] = execution_id
            return execution_id

    def set_evidence(self, task_id: str, evidence_ids: Iterable[str]) -> None:
        """Replace the evidence ids this task's execution received."""
        with self._lock:
            self._evidence[task_id] = set(evidence_ids)

    def mark_bootstrapped(self) -> bool:
        """Return True the first time it is called, False afterwards."""
        with self._lock:
            if self.bootstrapped:
                return False
            self.bootstrapped = True
            return True

    def record_tool_names(self, names: Sequence[str]) -> None:
        """Record the tool names sent to the model on the latest request."""
        with self._lock:
            self.tool_names = [str(name) for name in names]

    # -- submission ---------------------------------------------------------
    async def submit(
        self,
        *,
        producer: str,
        task_id: str,
        execution_id: str,
        outcome: TaskOutcome,
        claims: Sequence[Claim],
        artifact_refs: Sequence[str] = (),
        acceptance: Mapping[str, AcceptanceCheck] | None = None,
    ) -> SubmitOutcome:
        """Validate and store one result, then complete the native task."""
        from app.execution import StaleExecutionError, UnknownExecutionError

        plan_task = self._plan.get(task_id)
        if plan_task is None:
            return SubmitOutcome(False, f"task {task_id!r} is not in the plan for run {self.run_id!r}")
        if producer != plan_task.assignee:
            return SubmitOutcome(
                False,
                f"{producer!r} is not the assignee of {task_id!r} "
                f"(assigned to {plan_task.assignee!r})",
            )

        key = (task_id, execution_id)
        if key in self._submitted:
            return SubmitOutcome(False, f"execution {execution_id!r} already submitted a result for {task_id!r}")

        artifacts = tuple(non_blank(artifact_refs))
        named_claims = tuple(claim for claim in claims if claim.text.strip())
        if not artifacts and not named_claims:
            return SubmitOutcome(False, "empty result: no claims and no artifacts")
        if not artifacts:
            artifacts = (f"claims:{task_id}:{execution_id}",)

        if not usable_artifacts(artifacts):
            return SubmitOutcome(False, "empty result: no claims and no artifacts")

        report = self._citations.validate(
            named_claims, available_evidence_ids=self._evidence.get(task_id, set())
        )
        if not report.valid:
            codes = ",".join(code.value for code in report.codes())
            return SubmitOutcome(False, f"citation check failed: {codes}")

        result = TaskResult(
            run_id=self.run_id,
            task_id=task_id,
            execution_id=execution_id,
            producer=producer,
            outcome=outcome,
            artifact_refs=artifacts,
            claims=named_claims,
        )

        try:
            self._ledger.validate_submission(self.run_id, task_id, execution_id)
        except (UnknownExecutionError, StaleExecutionError) as exc:
            return SubmitOutcome(False, f"execution check failed: {exc}")

        decision = self._completion.evaluate(
            run_id=self.run_id,
            task_id=task_id,
            execution_id=execution_id,
            result=result,
            reviewers=plan_task.reviewers,
            require_reviewer=False,
            required_acceptance_ids=plan_task.acceptance_ids,
            acceptance=acceptance,
        )
        if not decision.allowed:
            reason = decision.reason.value if decision.reason else "rejected"
            return SubmitOutcome(False, f"completion gate blocked: {reason}: {decision.detail or ''}".strip())

        with self._lock:
            self._results[task_id] = result
            self._submitted.add(key)

        if self._complete is not None:
            try:
                await self._complete(task_id)
            except Exception as exc:  # noqa: BLE001 - reported, the result is kept
                return SubmitOutcome(
                    False,
                    f"result accepted but native completion failed: {type(exc).__name__}: {exc}",
                )
        return SubmitOutcome(True)

    # -- observation --------------------------------------------------------
    def results(self) -> dict[str, TaskResult]:
        with self._lock:
            return dict(self._results)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "bootstrapped": self.bootstrapped,
                "tool_names": list(self.tool_names),
                "errors": list(self.errors),
                "results": {
                    task_id: {
                        "producer": result.producer,
                        "outcome": result.outcome.value,
                        "claims": [
                            {
                                "text": claim.text,
                                "kind": claim.kind.value,
                                "evidence_ids": list(claim.evidence_ids),
                            }
                            for claim in result.claims
                        ],
                        "artifact_refs": list(result.artifact_refs),
                        "execution_id": result.execution_id,
                    }
                    for task_id, result in self._results.items()
                },
            }
