"""Leader-mounted rail that submits the validated graph once the team exists.

The application owns the plan: it validated the DAG, assigned members, and
decided the reviewers. The native board therefore has to be populated from the
app, not from the model's own ``create_task`` calls — otherwise validation
could only happen after creation.

Finding the native task manager is the hard part, and it was measured, not
guessed:

- the callback context's ``agent`` is the inner ``ReActAgent``, which exposes
  no rails and no team backend;
- the ``TeamToolRail`` that does own the backend lives on the outer deep agent;
- the member rows become visible only after ``build_team`` commits.

So this rail resolves the task manager two ways — the outer agent's
``TeamToolRail`` when reachable, otherwise a real tool instance
(``view_task`` / ``create_task`` / ``build_team``) whose manager carries the
database — and submits after ``build_team`` returns, retrying briefly while the
commit lands. Quiet hooks (before an iteration/model call) try once and never
sleep: they fire before ``build_team``, and a sleep loop there would block the
leader's own turn.
"""
from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from app.hooks import get_bridge
from app.run_bridge import RunBridge
from app.task_bridge import plan_to_graph

SUBMIT_ATTEMPTS = 10
SUBMIT_RETRY_SECONDS = 1.0


class RunBootstrapRail(DeepAgentRail):
    """Submit the app's task graph once the native task manager is ready."""

    def __init__(self, bridge: RunBridge, *, leader_name: str) -> None:
        super().__init__()
        self.bridge = bridge
        self.leader_name = leader_name

    # -- hook wiring --------------------------------------------------------
    async def before_task_iteration(self, ctx: Any) -> None:
        await self._try_bootstrap(ctx, loud=False, attempts=1)

    async def before_model_call(self, ctx: Any) -> None:
        # After build_team returns, the next model call is the first hook that
        # certainly runs post-commit; give it a short window.
        await self._try_bootstrap(ctx, loud=False, attempts=3)

    async def after_tool_call(self, ctx: Any) -> None:
        inputs = getattr(ctx, "inputs", None)
        name = str(getattr(inputs, "tool_name", "") or "")
        self.bridge.record_tool_event(name)
        await self._try_bootstrap(
            ctx,
            loud=name == "build_team",
            attempts=SUBMIT_ATTEMPTS if name == "build_team" else 1,
        )

    # -- the attempt --------------------------------------------------------
    async def _try_bootstrap(self, ctx: Any, *, loud: bool, attempts: int) -> None:
        if self.bridge.bootstrapped:
            return

        last_error = ""
        manager: Any = None
        for attempt in range(1, attempts + 1):
            manager = self._task_manager(ctx)
            if manager is not None and getattr(manager, "db", None) is not None:
                self.bridge.bind_native_complete(
                    lambda task_id, ready=manager: ready.complete(task_id=task_id)
                )
                try:
                    result = await manager.add_graph(plan_to_graph(self.bridge.plan_tasks()))
                except Exception as exc:  # noqa: BLE001 - recorded, re-raised when loud
                    last_error = f"graph submission failed: {type(exc).__name__}: {exc}"
                    result = None
                else:
                    if result.ok:
                        self.bridge.mark_bootstrapped()
                        return
                    last_error = f"native task graph was rejected: {result.reason}"
            else:
                last_error = "native task manager not ready"

            if attempt < attempts:
                await asyncio.sleep(SUBMIT_RETRY_SECONDS)

        if not loud:
            return

        detail = await self._diagnose(manager) if manager is not None else self._agent_shape(ctx)
        message = f"{last_error}; {detail}"
        self.bridge.errors.append(message)
        raise RuntimeError(message)

    # -- resolution ---------------------------------------------------------
    @staticmethod
    def _candidate_agents(ctx: Any) -> list[Any]:
        """Return the callback agent and any inner/deep agent it wraps."""
        candidates: list[Any] = []
        seen: set[int] = set()

        def add(agent: Any) -> None:
            if agent is None or id(agent) in seen:
                return
            seen.add(id(agent))
            candidates.append(agent)

        top = getattr(ctx, "agent", None)
        add(top)
        if top is not None:
            add(getattr(top, "inner_agent", None))
            harness = getattr(top, "harness", None)
            add(harness)
            add(getattr(harness, "inner_agent", None))
        return candidates

    @classmethod
    def _task_manager(cls, ctx: Any) -> Any:
        """Return a TaskManager whose database is initialized.

        First the rails on any reachable agent; then real tool instances, which
        carry the same manager. The tool path is what works in production today:
        the callback context only exposes the inner ReAct agent.
        """
        fallback: Any = None
        agents = cls._candidate_agents(ctx)
        for agent in agents:
            configured = getattr(agent, "configured_rails", None)
            rails = list(configured()) if callable(configured) else []
            for rail in rails:
                if type(rail).__name__ != "TeamToolRail":
                    continue
                backend = getattr(rail, "_team_backend", None)
                manager = getattr(backend, "task_manager", None)
                if manager is None:
                    continue
                if getattr(manager, "db", None) is not None:
                    return manager
                fallback = fallback or manager

        for agent in agents:
            ability = getattr(agent, "ability_manager", None)
            cards = getattr(ability, "_tools", None)
            if not isinstance(cards, dict):
                continue
            for card in cards.values():
                name = getattr(card, "name", "")
                if name not in ("view_task", "create_task", "build_team"):
                    continue
                tool = cls._tool_instance(card)
                manager = getattr(tool, "task_manager", None) or getattr(
                    getattr(tool, "team", None), "task_manager", None
                )
                if manager is None:
                    continue
                if getattr(manager, "db", None) is not None:
                    return manager
                fallback = fallback or manager
        return fallback

    @staticmethod
    def _tool_instance(card: Any) -> Any:
        try:
            from openjiuwen.core.runner.runner import Runner

            return Runner.resource_mgr.get_tool(tool_id=getattr(card, "id", None))
        except Exception:  # noqa: BLE001 - resolution is best effort
            return None

    @classmethod
    def _agent_shape(cls, ctx: Any) -> str:
        shapes = []
        for agent in cls._candidate_agents(ctx):
            configured = getattr(agent, "configured_rails", None)
            rails = [type(rail).__name__ for rail in (configured() if callable(configured) else [])]
            ability = getattr(agent, "ability_manager", None)
            cards = list((getattr(ability, "_tools", None) or {}).keys())
            shapes.append(f"{type(agent).__name__}:rails={rails}:tools={cards}")
        return "agents=" + "; ".join(shapes)

    @staticmethod
    async def _diagnose(manager: Any) -> str:
        db = getattr(manager, "db", None)
        if db is None:
            return "manager.db is None"
        team_name = getattr(manager, "team_name", None)
        roster: dict[str, Any] = {}
        for member in ("team_leader", "research", "engineering"):
            try:
                roster[member] = await db.member.member_exists(member, team_name)
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                roster[member] = f"{type(exc).__name__}: {exc}"
        return (
            f"team={team_name!r} roster={roster} "
            f"initialized={getattr(db, '_initialized', None)!r}"
        )


def make_bootstrap_rail(params: dict[str, Any], context: Any) -> RunBootstrapRail:
    """Provider factory: resolve the run bridge and remember which member mounted it."""
    bridge = get_bridge(params["run_id"])
    member_name = getattr(context, "member_name", None) or "leader"
    return RunBootstrapRail(bridge, leader_name=str(member_name))
