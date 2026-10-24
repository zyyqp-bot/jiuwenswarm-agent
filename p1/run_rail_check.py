"""P1: the graph is submitted after build_team, once; the request surface is filtered.

Two mechanisms are pinned here.

The bootstrap rail used to fire before ``build_team``; the team backend had no
database yet, ``add_graph`` raised, and the rail chain swallowed it, leaving an
empty board with no error. The rail now waits for the backend and submits in
``after_tool_call`` when ``build_team`` returns.

The team configurator injects a ``SYS_OPERATION`` rail regardless of
``enable_sys_operation``, so shell/file tools still reach the model. The guard
rail filters the actual request list and records what survived.

Offline: fakes only; no team, no runner, no model.

Run: .venv/Scripts/python.exe p1/run_rail_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.plan import PlanTask  # noqa: E402
from app.run_bridge import RunBridge  # noqa: E402
import app.run_rail as run_rail_module  # noqa: E402
from app.run_rail import RunBootstrapRail  # noqa: E402
from app.tool_guard import ToolSurfaceGuardRail  # noqa: E402
from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.schema.task import TaskGraphResult  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "run_rail_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="collect evidence",
        acceptance_ids=("a1",),
        assignee="research",
    )
]


class _Manager:
    def __init__(self, result: TaskGraphResult) -> None:
        self.result = result
        self.calls: list[list] = []
        self.db = None
        self.team_name = "team-1"

    async def add_graph(self, specs: list) -> TaskGraphResult:
        self.calls.append(list(specs))
        return self.result


class _MemberDao:
    async def member_exists(self, member_name: str, team_name: str) -> bool:
        return True


class _Db:
    member = _MemberDao()


class _Backend:
    def __init__(self, result: TaskGraphResult, *, db: object | None = None) -> None:
        self.task_manager = _Manager(result)
        self.task_manager.db = db
        self.db = db
        self.team_name = "team-1"


class TeamToolRail:
    def __init__(self, backend: _Backend) -> None:
        self._team_backend = backend


class _Agent:
    def __init__(self, rail: _TeamRail | None) -> None:
        self._rail = rail

    def find_rail_by_name(self, name: str):
        return self._rail if name == "TeamToolRail" else None

    def configured_rails(self):
        return [self._rail] if self._rail is not None else []


class _Ctx:
    def __init__(self, agent: _Agent, *, tool_name: str = "") -> None:
        self.agent = agent
        self.inputs = type("Inputs", (), {"tool_name": tool_name})()


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _ModelInputs:
    def __init__(self, tools: list) -> None:
        self.tools = tools


async def _run(checks: Checks) -> None:
    run_rail_module.SUBMIT_ATTEMPTS = 2
    run_rail_module.SUBMIT_RETRY_SECONDS = 0.0
    # --- waits for the backend ---------------------------------------------
    backend = _Backend(TaskGraphResult.success(tasks=[]), db=None)
    bridge = RunBridge(run_id="run-1", plan=PLAN)
    rail = RunBootstrapRail(bridge, leader_name="team_leader")
    await rail.before_task_iteration(_Ctx(_Agent(TeamToolRail(backend))))
    checks.add(
        "no submission before build_team completes",
        not backend.task_manager.calls and bridge.bootstrapped is False,
        f"calls={len(backend.task_manager.calls)} bootstrapped={bridge.bootstrapped}",
    )

    # --- submits after build_team ------------------------------------------
    backend.db = _Db()
    backend.task_manager.db = backend.db
    await rail.after_tool_call(_Ctx(_Agent(TeamToolRail(backend)), tool_name="build_team"))
    await rail.after_tool_call(_Ctx(_Agent(TeamToolRail(backend)), tool_name="build_team"))
    checks.add(
        "the graph is submitted exactly once after build_team",
        len(backend.task_manager.calls) == 1,
        f"calls={len(backend.task_manager.calls)}",
    )
    checks.add("the bridge records that it bootstrapped", bridge.bootstrapped is True, f"bootstrapped={bridge.bootstrapped}")

    # --- a rejected graph is loud on the explicit attempt -------------------
    failed = _Backend(TaskGraphResult.fail("cycle"), db=_Db())
    failing = RunBootstrapRail(RunBridge(run_id="run-2", plan=PLAN), leader_name="team_leader")
    raised = ""
    try:
        await failing.after_tool_call(_Ctx(_Agent(TeamToolRail(failed)), tool_name="build_team"))
    except RuntimeError as exc:
        raised = str(exc)
    checks.add("a rejected graph raises", "cycle" in raised, raised or "no error")

    # --- no team rail, no submission ---------------------------------------
    quiet = _Backend(TaskGraphResult.success(tasks=[]), db=_Db())
    quiet_bridge = RunBridge(run_id="run-3", plan=PLAN)
    quiet_rail = RunBootstrapRail(quiet_bridge, leader_name="team_leader")
    await quiet_rail.before_task_iteration(_Ctx(_Agent(None)))
    checks.add(
        "without a team rail nothing is submitted",
        not quiet.task_manager.calls and quiet_bridge.bootstrapped is False,
        f"calls={len(quiet.task_manager.calls)} bootstrapped={quiet_bridge.bootstrapped}",
    )

    # --- the request surface is filtered ------------------------------------
    guard_backend = _Backend(TaskGraphResult.success(tasks=[]), db=_Db())
    guard_bridge = RunBridge(run_id="run-4", plan=PLAN)
    guard = ToolSurfaceGuardRail(guard_bridge)
    inputs = _ModelInputs([
        _Tool("submit_task_result"),
        _Tool("create_task"),
        _Tool("read_file"),
        _Tool("bash"),
        _Tool("spawn_passive_human"),
    ])
    await guard.before_model_call(type("Ctx", (), {"inputs": inputs})())
    checks.add(
        "forbidden tools are removed from the request",
        [tool.name for tool in inputs.tools] == ["submit_task_result", "create_task"]
        and set(guard.removed) == {"read_file", "bash", "spawn_passive_human"},
        f"kept={[tool.name for tool in inputs.tools]} removed={guard.removed}",
    )
    checks.add(
        "the guard records the model-visible surface",
        guard_bridge.tool_names == ["submit_task_result", "create_task"],
        f"tool_names={guard_bridge.tool_names}",
    )
    checks.add("the guard backend is untouched", not guard_backend.task_manager.calls, "no graph call from the guard")


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="rail and guard unit checks only; no team, no runner, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
