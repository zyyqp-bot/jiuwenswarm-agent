"""Enforce the public tool surface on the request **and** at execution.

Two layers, and only the second is a boundary.

The team configurator injects a ``SYS_OPERATION`` rail for every leader and
teammate, independently of ``DeepAgentSpec.enable_sys_operation``. The built
leader therefore still registers ``read_file``/``bash``/``powershell`` —
measured on a real request.

``before_model_call`` filters ``ctx.inputs.tools`` so the model is not
*advertised* a shell or filesystem tool. That is not enough: the dispatcher
executes any model-emitted name present in ``ability_manager._tools``
(``if tool_name in self._tools``), advertised or not. A prompt-injected
document can name ``bash`` and it will run.

``before_tool_call`` is the execution gate. It sets ``_skip_tool`` and an
error ToolMessage, the same mechanism ``AgentModeRail._reject_tool`` uses, so
the tool's ``invoke`` is never called.

The surface is an allowlist, not a denylist. Ownership follows the same rule:
a member cannot complete its own task outside ``submit_task_result``, and the
leader cannot mutate the board the application submitted.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.harness.rails.base import DeepAgentRail

from app.hooks import get_bridge
from app.request_probe import _tool_name
from app.run_bridge import RunBridge

# Deny by default. A denylist only covers names someone remembered; a future
# harness tool (or one not observed in the last request) would be both
# advertised and executable. These are the only tools the product needs:
#
# - submit_task_result: the member's sole way to turn work into a checked
#   result (it calls the member's own task manager);
# - retrieve: the workspace corpus, mounted only when one exists;
# - view_task / async_tasks_list / async_task_output: read-only coordination.
#
# Deliberately absent: every shell/file/spawn/upload tool, member_complete_task
# (the app owns completion), create_task/update_task (the app owns the board),
# build_team (no nested teams), verify_task (the current plans have no
# reviewers), shutdown_member, and anything added to the harness later.
ALLOWED_TOOL_NAMES = frozenset(
    {
        "submit_task_result",
        "retrieve",
        "view_task",
        "async_tasks_list",
        "async_task_output",
    }
)


def is_allowed(name: str) -> bool:
    return name in ALLOWED_TOOL_NAMES


def _call_name(inputs: Any) -> str:
    """Return the tool name from a BEFORE_TOOL_CALL context."""
    tool_call = getattr(inputs, "tool_call", None)
    if tool_call is not None:
        name = getattr(tool_call, "name", None)
        if isinstance(name, str) and name:
            return name
    name = getattr(inputs, "tool_name", None)
    return name if isinstance(name, str) else ""


class ToolSurfaceGuardRail(DeepAgentRail):
    """Filter the request surface and refuse denied tools at execution."""

    def __init__(self, bridge: RunBridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.removed: list[str] = []

    async def before_model_call(self, ctx: Any) -> None:
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not isinstance(tools, list):
            return

        kept: list[Any] = []
        removed: list[str] = []
        for tool in tools:
            name = _tool_name(tool)
            if not name:
                continue
            if not is_allowed(name):
                removed.append(name)
                continue
            kept.append(tool)

        inputs.tools = kept
        self.removed = removed
        self.bridge.record_tool_request(
            [_tool_name(tool) for tool in kept if _tool_name(tool)],
            removed=removed,
        )

    async def before_tool_call(self, ctx: Any) -> None:
        inputs = getattr(ctx, "inputs", None)
        name = _call_name(inputs)
        if not name or is_allowed(name):
            return

        tool_call = getattr(inputs, "tool_call", None)
        message = f"tool '{name}' is not available in this workbench"
        ctx.extra["_skip_tool"] = True
        inputs.tool_result = {"error": message}
        inputs.tool_msg = ToolMessage(content=message, tool_call_id=getattr(tool_call, "id", "") or "")
        self.bridge.record_blocked_tool(name)


def make_tool_guard(params: dict[str, Any], context: Any) -> ToolSurfaceGuardRail:
    """Provider factory for the ``app.tool_surface_guard`` rail."""
    return ToolSurfaceGuardRail(get_bridge(params["run_id"]))
