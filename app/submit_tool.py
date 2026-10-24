"""The only way a member can turn work into a checked result.

The framework's ``member_complete_task`` takes a ``task_id`` and a free-text
note; the task row stores the instruction, not a result. That is enough for a
human reading a board and not enough for citation validation, a completion
gate, or an evidence drawer.

This tool is mounted on both teammate templates. Its ``producer`` is bound at
build time from ``BuildContext.member_name`` — never from the arguments — so a
member cannot submit as somebody else. It validates the result through the run
bridge and only then completes the native task.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

from app.hooks import get_bridge
from app.models import AcceptanceCheck, AcceptanceResult, Claim, ClaimKind, TaskOutcome
from app.run_bridge import RunBridge

_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "The native task id this result belongs to (from the assignment message).",
        },
        "outcome": {
            "type": "string",
            "enum": ["success", "partial", "error"],
            "description": "The business outcome of this attempt.",
        },
        "claims": {
            "type": "array",
            "description": "The findings this task contributes. A fact must cite evidence.",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "inference", "assumption"],
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ids returned to this task by its retrieval tool.",
                    },
                },
                "required": ["text", "kind"],
            },
        },
        "artifact_refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional artifact references; claims alone are an acceptable deliverable.",
        },
        "acceptance": {
            "type": "array",
            "description": "Results of the acceptance conditions this task was given.",
            "items": {
                "type": "object",
                "properties": {
                    "acceptance_id": {"type": "string"},
                    "result": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                    "verifier": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["acceptance_id", "result", "verifier"],
            },
        },
    },
    "required": ["task_id", "outcome", "claims"],
}


class SubmitTaskResultTool(TeamTool):
    """Submit a structured, citation-checked result for the caller's task."""

    def __init__(
        self,
        bridge: RunBridge,
        *,
        producer: str,
        complete: Any = None,
    ) -> None:
        super().__init__(
            ToolCard(
                id="app.submit_task_result",
                name="submit_task_result",
                description=(
                    "Submit this task's result. Facts must cite evidence ids this task "
                    "received. The result is checked before the task can complete."
                ),
            )
        )
        self.bridge = bridge
        self.producer = producer
        self.complete = complete
        self.card.input_params = _SCHEMA

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            outcome = TaskOutcome(str(inputs.get("outcome", "")).strip())
        except ValueError:
            return ToolOutput(success=False, error="'outcome' must be one of success, partial, error")

        task_id = str(inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        try:
            claims = tuple(_parse_claim(raw) for raw in inputs.get("claims") or [])
            acceptance = _parse_acceptance(inputs.get("acceptance") or [])
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))

        try:
            execution_id = self.bridge.execution_for(task_id)
        except KeyError as exc:
            return ToolOutput(success=False, error=str(exc))

        verdict = await self.bridge.submit(
            producer=self.producer,
            task_id=task_id,
            execution_id=execution_id,
            outcome=outcome,
            claims=claims,
            artifact_refs=tuple(str(ref) for ref in inputs.get("artifact_refs") or []),
            acceptance=acceptance,
            complete=self.complete,
        )
        return ToolOutput(success=verdict.ok, error=verdict.error or None)


def _parse_claim(raw: Any) -> Claim:
    if not isinstance(raw, dict):
        raise ValueError("each claim must be an object")
    try:
        kind = ClaimKind(str(raw.get("kind", "")).strip())
    except ValueError:
        raise ValueError("claim kind must be one of fact, inference, assumption") from None
    return Claim(
        text=str(raw.get("text", "")),
        kind=kind,
        evidence_ids=tuple(str(item) for item in raw.get("evidence_ids") or []),
    )


def _parse_acceptance(raw_items: Any) -> dict[str, AcceptanceCheck]:
    checks: dict[str, AcceptanceCheck] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("each acceptance check must be an object")
        acceptance_id = str(raw.get("acceptance_id", "")).strip()
        if not acceptance_id:
            raise ValueError("acceptance_id is required")
        try:
            result = AcceptanceResult(str(raw.get("result", "")).strip())
        except ValueError:
            raise ValueError("acceptance result must be pass, fail or unknown") from None
        checks[acceptance_id] = AcceptanceCheck(
            acceptance_id=acceptance_id,
            result=result,
            verifier=str(raw.get("verifier", "")),
            evidence_refs=tuple(str(item) for item in raw.get("evidence_refs") or []),
        )
    return checks


def make_submit_tool(params: dict[str, Any], context: Any) -> SubmitTaskResultTool:
    """Provider factory: resolve the run bridge and bind the member's identity.

    The native completion is the **member's own** task manager, taken from the
    build context's team backend. The leader's manager cannot complete a
    teammate's task (``TeamTaskManager.complete`` is self-only), so binding it
    there — as the first implementation did — made every teammate completion a
    silent no-op.
    """
    producer = getattr(context, "member_name", None)
    if not producer:
        raise RuntimeError("submit_task_result needs a BuildContext.member_name")
    return SubmitTaskResultTool(
        get_bridge(params["run_id"]),
        producer=str(producer),
        complete=_member_complete(context),
    )


def _member_complete(context: Any) -> Any:
    """Return an async completion bound to this member's task manager, or None."""
    try:
        from openjiuwen.agent_teams.rails.team_context import get_team_backend
    except Exception:  # noqa: BLE001 - offline checks build the tool directly
        return None

    backend = get_team_backend(context)
    manager = getattr(backend, "task_manager", None) if backend is not None else None
    if manager is None:
        return None

    async def complete(task_id: str) -> Any:
        return await manager.complete(task_id=task_id)

    return complete
