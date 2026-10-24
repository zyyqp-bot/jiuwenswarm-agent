"""Process-local registry connecting framework providers to the current run.

The framework resolves tool and rail providers by name at build time and
passes only ``params`` plus a ``BuildContext``; it has no notion of a run. The
supervisor registers the run's bridge here, the template's provider names carry
``{"run_id": ...}``, and the factories look the bridge up.

This works because the MVP builds members in-process (``spawn_mode``
``inprocess``). A cross-process member would need a serializable carrier; none
is built here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.run_bridge import RunBridge

_BRIDGES: dict[str, "RunBridge"] = {}
_PROVIDERS_REGISTERED = False


def register_bridge(run_id: str, bridge: "RunBridge") -> None:
    """Make ``bridge`` reachable by the provider factories for ``run_id``."""
    _BRIDGES[run_id] = bridge


def get_bridge(run_id: str) -> "RunBridge":
    """Return the bridge for ``run_id``; raise ``KeyError`` when it is not registered."""
    return _BRIDGES[run_id]


def unregister_bridge(run_id: str) -> None:
    """Drop the bridge for ``run_id``; idempotent."""
    _BRIDGES.pop(run_id, None)


def register_providers() -> None:
    """Register the app's tool providers with the framework. Idempotent.

    The upstream import is lazy: importing ``app.hooks`` must not require
    agent-core on the path (the bridge unit checks run against the app alone).
    """
    global _PROVIDERS_REGISTERED
    if _PROVIDERS_REGISTERED:
        return

    from openjiuwen.harness.schema.deep_agent_spec import (
        register_rail_provider,
        register_tool_provider,
    )

    from app.retrieval_tool import make_retrieval_tool
    from app.run_rail import make_bootstrap_rail
    from app.submit_tool import make_submit_tool
    from app.tool_guard import make_tool_guard

    register_tool_provider("app.submit_task_result", make_submit_tool)
    register_tool_provider("app.retrieve", make_retrieval_tool)
    register_rail_provider("app.run_bootstrap", make_bootstrap_rail)
    register_rail_provider("app.tool_surface_guard", make_tool_guard)
    _PROVIDERS_REGISTERED = True
