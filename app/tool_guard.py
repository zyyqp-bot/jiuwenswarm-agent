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

The denylist also carries the app's ownership decisions: a member may not
complete its own task outside ``submit_task_result`` (``member_complete_task``),
and the leader may not mutate the board the application submitted
(``create_task`` / ``update_task``).
"""
from __future__ import annotations

from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.harness.rails.base import DeepAgentRail

from app.hooks import get_bridge
from app.request_probe import _tool_name
from app.run_bridge import RunBridge

# Exact names observed on the real request plus their close relatives.
FORBIDDEN_NAMES = frozenset(
    {
        "read_file",
        "read_file_stream",
        "write_file",
        "write_file_stream",
        "edit_file",
        "glob",
        "list_files",
        "list_directories",
        "grep",
        "search_files",
        "bash",
        "powershell",
        "execute_cmd",
        "execute_cmd_stream",
        "execute_cmd_background",
        "execute_code",
        "execute_code_stream",
        "upload_file",
        "upload_file_stream",
        "download_file",
        "download_file_stream",
        # The app owns task completion and the task board.
        "member_complete_task",
        "create_task",
        "update_task",
        "org_dissolve_organization",
    }
)
FORBIDDEN_PREFIXES = ("spawn_",)


def is_forbidden(name: str) -> bool:
    return name in FORBIDDEN_NAMES or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


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
            if is_forbidden(name):
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
        if not name or not is_forbidden(name):
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
