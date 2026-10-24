"""Leader-mounted rail that submits the validated graph before the first turn.

The application owns the plan: it validated the DAG, assigned members, and
decided the reviewers. The native board therefore has to be populated from the
app, not from the model's own ``create_task`` calls — otherwise validation
could only happen after creation.

``before_task_iteration`` runs before the leader's first model call and after
the team (including its ``TeamToolRail``) is built, which is the earliest point
the native task manager exists and the latest point before the leader could
plan around an empty board.

Exactly one submission per run: the guard lives on the bridge, not in module
state, so two rails or a retried iteration cannot duplicate the graph.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from app.hooks import get_bridge
from app.run_bridge import RunBridge
from app.task_bridge import submit_graph


class RunBootstrapRail(DeepAgentRail):
    """Submit the app's task graph once, then stay out of the way."""

    def __init__(self, bridge: RunBridge, *, leader_name: str) -> None:
        super().__init__()
        self.bridge = bridge
        self.leader_name = leader_name

    async def before_task_iteration(self, ctx: Any) -> None:
        agent = getattr(ctx, "agent", None)
        if agent is None:
            return
        finder = getattr(agent, "find_rail_by_name", None)
        if not callable(finder):
            return
        team_rail = finder("TeamToolRail")
        if team_rail is None:
            return
        backend = getattr(team_rail, "_team_backend", None)
        if backend is None:
            return

        # Guard first: a second call must not submit anything, even while the
        # first submission is still in flight.
        if not self.bridge.mark_bootstrapped():
            return

        self.bridge.bind_native_complete(
            lambda task_id: backend.task_manager.complete(task_id=task_id)
        )
        result = await submit_graph(backend, self.bridge.plan_tasks())
        if not result.ok:
            raise RuntimeError(f"native task graph was rejected: {result.reason}")


def make_bootstrap_rail(params: dict[str, Any], context: Any) -> RunBootstrapRail:
    """Provider factory: resolve the run bridge and remember which member mounted it."""
    bridge = get_bridge(params["run_id"])
    member_name = getattr(context, "member_name", None) or "leader"
    return RunBootstrapRail(bridge, leader_name=str(member_name))
