"""Enforce the public tool surface at the last point that matters: the request.

The team configurator injects a ``SYS_OPERATION`` rail for every leader and
teammate (the worktree shell guard), independently of
``DeepAgentSpec.enable_sys_operation``. The built leader therefore still
registers ``read_file``/``bash``/``powershell`` — measured on a real request.

This rail runs in ``before_model_call``, after every rail has registered its
tools and immediately before the ReAct agent assembles the request. It filters
``ctx.inputs.tools`` in place and records what remains, so the model never sees
a shell or filesystem tool even though the resource exists.

Residual risk, recorded rather than hidden: a rail that calls such a tool
itself (without the model) is not blocked by this filter. Every current tool
invocation in this run is model-driven.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from app.hooks import get_bridge
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
    }
)
FORBIDDEN_PREFIXES = ("spawn_",)


def is_forbidden(name: str) -> bool:
    return name in FORBIDDEN_NAMES or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    card = getattr(tool, "card", None)
    if card is not None:
        card_name = getattr(card, "name", None)
        if isinstance(card_name, str) and card_name:
            return card_name
    return ""


class ToolSurfaceGuardRail(DeepAgentRail):
    """Remove forbidden tools from the model request and record the result."""

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
        self.bridge.record_tool_names([_tool_name(tool) for tool in kept if _tool_name(tool)])


def make_tool_guard(params: dict[str, Any], context: Any) -> ToolSurfaceGuardRail:
    """Provider factory for the ``app.tool_surface_guard`` rail."""
    return ToolSurfaceGuardRail(get_bridge(params["run_id"]))
