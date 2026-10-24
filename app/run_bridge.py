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
from app.models import (
    AcceptanceCheck,
    AcceptanceResult,
    Claim,
    TaskOutcome,
    TaskResult,
    non_blank,
    usable_artifacts,
)
from app.plan import PlanTask

APP_VERIFIER = "app.structural"
"""Who signs the application's structural acceptance checks.

The producer cannot sign its own acceptance: ``CompletionGate`` compares this
name against ``TaskResult.producer``. Semantic acceptance (does the report
actually satisfy the task?) belongs to the reviewer flow in Plan 2; this name
covers only what the application itself checked — a non-empty result whose
fact citations resolved against evidence the task received.
"""


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
        self._reported_acceptance: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self.bootstrapped = False
        self._tasks: list[dict[str, Any]] = []
        self.tool_names: list[str] = []
        self.tool_requests: list[dict[str, list[str]]] = []
        self.blocked_tools: list[str] = []
        self.tool_events: list[str] = []
        self.errors: list[str] = []
        self.fatal_errors: list[str] = []

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

    def plan_tasks(self) -> list[PlanTask]:
        """Return the plan in declaration order (the order the graph must keep)."""
        with self._lock:
            return list(self._plan.values())

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

    def record_task_board(self, tasks: Sequence[Mapping[str, Any]]) -> None:
        """Replace the projected native task board."""
        with self._lock:
            self._tasks = [dict(task) for task in tasks]

    def record_tool_event(self, name: str) -> None:
        """Record one completed tool call, for run diagnostics."""
        if not name:
            return
        with self._lock:
            self.tool_events.append(str(name))
            if len(self.tool_events) > 100:
                del self.tool_events[:-100]

    def record_tool_names(self, names: Sequence[str]) -> None:
        """Record the tool names sent to the model on one request."""
        self.record_tool_request(names)

    def record_tool_request(self, names: Sequence[str], *, removed: Sequence[str] = ()) -> None:
        """Append one request's tool list; the history is never overwritten.

        A single request is not evidence about a run: an early request that
        leaked ``bash`` would disappear if only the latest list were kept.
        """
        request = {"names": [str(name) for name in names], "removed": [str(name) for name in removed]}
        with self._lock:
            self.tool_names = request["names"]
            self.tool_requests.append(request)
            if len(self.tool_requests) > 100:
                del self.tool_requests[:-100]

    def record_blocked_tool(self, name: str) -> None:
        """Record a model-emitted call that the execution gate refused."""
        if not name:
            return
        with self._lock:
            self.blocked_tools.append(str(name))
            if len(self.blocked_tools) > 100:
                del self.blocked_tools[:-100]

    def fail(self, message: str) -> None:
        """Record a run-fatal error (as opposed to a rejected submission)."""
        with self._lock:
            self.fatal_errors.append(str(message))
            self.errors.append(str(message))

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
        complete: Callable[[str], Awaitable[Any]] | None = None,
    ) -> SubmitOutcome:
        """Validate and store one result, recording every rejection for diagnostics."""
        verdict = await self._submit(
            producer=producer,
            task_id=task_id,
            execution_id=execution_id,
            outcome=outcome,
            claims=claims,
            artifact_refs=artifact_refs,
            acceptance=acceptance,
            complete=complete,
        )
        if not verdict.ok:
            with self._lock:
                self.errors.append(verdict.error)
        return verdict

    async def _submit(
        self,
        *,
        producer: str,
        task_id: str,
        execution_id: str,
        outcome: TaskOutcome,
        claims: Sequence[Claim],
        artifact_refs: Sequence[str] = (),
        acceptance: Mapping[str, AcceptanceCheck] | None = None,
        complete: Callable[[str], Awaitable[Any]] | None = None,
    ) -> SubmitOutcome:
        """Validate a result, move the native task, and only then store it.

        The native completion is the member's own ``TeamTaskManager.complete``
        (bound at build time from the member's ``TeamBackend``). Its returned
        ``TaskOpResult`` is checked: the framework reports a self-only refusal
        by returning ``ok=False``, not by raising. Storing the result first
        would tell the member it succeeded while the board had not moved.
        """
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

        cited = tuple(sorted({eid for claim in named_claims for eid in claim.evidence_ids}))
        app_acceptance = {
            acceptance_id: AcceptanceCheck(
                acceptance_id=acceptance_id,
                result=AcceptanceResult.PASS,
                verifier=APP_VERIFIER,
                evidence_refs=cited,
            )
            for acceptance_id in plan_task.acceptance_ids
        }
        decision = self._completion.evaluate(
            run_id=self.run_id,
            task_id=task_id,
            execution_id=execution_id,
            result=result,
            reviewers=plan_task.reviewers,
            require_reviewer=False,
            required_acceptance_ids=plan_task.acceptance_ids,
            acceptance=app_acceptance,
        )
        if not decision.allowed:
            reason = decision.reason.value if decision.reason else "rejected"
            return SubmitOutcome(False, f"completion gate blocked: {reason}: {decision.detail or ''}".strip())

        if complete is not None:
            try:
                native = await complete(task_id)
            except Exception as exc:  # noqa: BLE001 - reported, the execution stays open
                return SubmitOutcome(False, f"native completion failed: {type(exc).__name__}: {exc}")
            if native is not None and not getattr(native, "ok", True):
                reason = getattr(native, "reason", "") or "rejected"
                return SubmitOutcome(False, f"native completion rejected: {reason}")

        with self._lock:
            if key in self._submitted:
                return SubmitOutcome(
                    False, f"execution {execution_id!r} already submitted a result for {task_id!r}"
                )
            self._results[task_id] = result
            self._submitted.add(key)
            if acceptance:
                self._reported_acceptance[task_id] = {
                    str(acceptance_id): check.result.value
                    for acceptance_id, check in acceptance.items()
                }
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
                "tasks": [dict(task) for task in self._tasks],
                "tool_names": list(self.tool_names),
                "tool_requests": [dict(request) for request in self.tool_requests],
                "blocked_tools": list(self.blocked_tools),
                "tool_events": list(self.tool_events),
                "errors": list(self.errors),
                "fatal_errors": list(self.fatal_errors),
                "reported_acceptance": dict(self._reported_acceptance),
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
