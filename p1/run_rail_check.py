"""P1: the graph is submitted once, and the real request tool list is recorded.

Two mechanisms are pinned here. The bootstrap rail is the only place the app
touches the native task board, so it must be idempotent (a retried iteration
must not duplicate the graph) and loud when the graph is rejected. The request
probe is how "the deployed agent has no shell/file tools" becomes a measurement
instead of an assumption: it reads the tool list the framework is about to
send to the model.

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
from app.request_probe import install_request_probe  # noqa: E402
from app.run_bridge import RunBridge  # noqa: E402
from app.run_rail import RunBootstrapRail  # noqa: E402
from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.schema.task import TaskGraphResult  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "run_rail_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="collect evidence",
        acceptance_ids=("a1",),
        assignee="research_member",
    )
]


class _Manager:
    def __init__(self, result: TaskGraphResult) -> None:
        self.result = result
        self.calls: list[list] = []

    async def add_graph(self, specs: list) -> TaskGraphResult:
        self.calls.append(list(specs))
        return self.result


class _Backend:
    def __init__(self, result: TaskGraphResult) -> None:
        self.task_manager = _Manager(result)


class _TeamRail:
    def __init__(self, backend: _Backend) -> None:
        self._team_backend = backend


class _Agent:
    def __init__(self, rail: _TeamRail | None) -> None:
        self._rail = rail

    def find_rail_by_name(self, name: str):
        return self._rail if name == "TeamToolRail" else None


class _Ctx:
    def __init__(self, agent: _Agent) -> None:
        self.agent = agent


async def _run(checks: Checks) -> None:
    # --- submits once -------------------------------------------------------
    backend = _Backend(TaskGraphResult.success(tasks=[]))
    bridge = RunBridge(run_id="run-1", plan=PLAN)
    rail = RunBootstrapRail(bridge, leader_name="team_leader")
    await rail.before_task_iteration(_Ctx(_Agent(_TeamRail(backend))))
    await rail.before_task_iteration(_Ctx(_Agent(_TeamRail(backend))))
    checks.add(
        "the graph is submitted exactly once",
        len(backend.task_manager.calls) == 1,
        f"calls={len(backend.task_manager.calls)}",
    )
    checks.add("the bridge records that it bootstrapped", bridge.bootstrapped is True, f"bootstrapped={bridge.bootstrapped}")

    # --- a rejected graph is loud ------------------------------------------
    failed = _Backend(TaskGraphResult.fail("cycle"))
    failing_bridge = RunBridge(run_id="run-2", plan=PLAN)
    failing_rail = RunBootstrapRail(failing_bridge, leader_name="team_leader")
    raised = ""
    try:
        await failing_rail.before_task_iteration(_Ctx(_Agent(_TeamRail(failed))))
    except RuntimeError as exc:
        raised = str(exc)
    checks.add("a rejected graph raises", "cycle" in raised, raised or "no error")

    # --- no team rail, no submission ---------------------------------------
    quiet = _Backend(TaskGraphResult.success(tasks=[]))
    quiet_bridge = RunBridge(run_id="run-3", plan=PLAN)
    quiet_rail = RunBootstrapRail(quiet_bridge, leader_name="team_leader")
    await quiet_rail.before_task_iteration(_Ctx(_Agent(None)))
    checks.add(
        "without a team rail nothing is submitted",
        not quiet.task_manager.calls and quiet_bridge.bootstrapped is False,
        f"calls={len(quiet.task_manager.calls)} bootstrapped={quiet_bridge.bootstrapped}",
    )

    # --- request probe ------------------------------------------------------
    from openjiuwen.core.single_agent.agents import react_agent

    seen: list[list[str]] = []
    original = react_agent.log_llm_request

    class _ToolNamed:
        name = "submit_task_result"

    class _ToolDict(dict):
        def __init__(self, name: str) -> None:
            super().__init__({"function": {"name": name}})

    class _Log:
        def info(self, *args, **kwargs) -> None:
            pass

    try:
        install_request_probe(lambda names: seen.append(list(names)))
        react_agent.log_llm_request(_Log(), [], [_ToolNamed(), _ToolDict("create_task")])
    finally:
        react_agent.log_llm_request = original

    checks.add(
        "the probe records the tools sent to the model",
        seen and seen[-1] == ["submit_task_result", "create_task"],
        f"seen={seen[-1] if seen else None}",
    )


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="rail and probe unit checks only; no team, no runner, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
