"""P1: a validated plan becomes the native graph, with no rules invented.

The conversion is deliberately dumb: ids, instructions, dependencies,
assignees and reviewer names, nothing else. Every judgement (acyclic, roster,
acceptance, reviewer coverage) happened in ``PlanValidator``; repeating or
softening it here would give the run two authorities.

Offline: a fake task manager records the call; no team, no model.

Run: .venv/Scripts/python.exe p1/task_bridge_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.plan import PlanTask  # noqa: E402
from app.task_bridge import plan_to_graph, submit_graph  # noqa: E402
from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.schema.task import TaskGraphResult  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "task_bridge_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="collect evidence",
        acceptance_ids=("a1",),
        assignee="research_member",
        reviewers=("team_leader",),
    ),
    PlanTask(
        task_id="engineering",
        role="teammate",
        instruction="write the answer",
        acceptance_ids=("a2",),
        depends_on=("research",),
        assignee="engineering_member",
    ),
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


async def _run(checks: Checks) -> None:
    graph = plan_to_graph(PLAN)
    checks.add("the graph keeps the plan's order and size", [spec.task_id for spec in graph] == ["research", "engineering"],
               f"ids={[spec.task_id for spec in graph]}")
    checks.add("instructions become task content", graph[0].content == "collect evidence", graph[0].content)
    checks.add("the dependency edge is preserved", graph[1].depends_on == ("research",), f"depends_on={graph[1].depends_on}")
    checks.add("assignees are preserved", [spec.assignee for spec in graph] == ["research_member", "engineering_member"],
               f"assignees={[spec.assignee for spec in graph]}")
    checks.add(
        "reviewer names become native reviewer entries",
        graph[0].reviewer == ({"reviewer_id": "team_leader", "type": "verifier"},),
        f"reviewer={graph[0].reviewer}",
    )

    ok_backend = _Backend(TaskGraphResult.success(tasks=[]))
    result = await submit_graph(ok_backend, PLAN)
    checks.add(
        "submit_graph calls the native manager exactly once",
        result.ok and len(ok_backend.task_manager.calls) == 1,
        f"ok={result.ok} calls={len(ok_backend.task_manager.calls)}",
    )

    failed = _Backend(TaskGraphResult.fail("cycle"))
    result = await submit_graph(failed, PLAN)
    checks.add(
        "a native failure is returned unchanged",
        not result.ok and result.reason == "cycle",
        f"ok={result.ok} reason={result.reason!r}",
    )


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="plan-to-graph conversion only; a fake manager records the call",
    )


if __name__ == "__main__":
    sys.exit(main())
