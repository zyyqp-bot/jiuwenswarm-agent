"""P2: the planner returns a valid graph, or the deterministic fallback.

The demo must not fail because a model returned prose instead of JSON. Every
invalid shape — unknown assignee, cycle, exception, timeout — falls back to
the same two-task plan, which itself passes the plan validator.

Offline: the model call is injected.

Run: .venv/Scripts/python.exe p1/planner_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.planner import (  # noqa: E402
    PLAN_SCHEMA,
    PlanOutcome,
    fallback_plan,
    model_from_credentials,
    plan_goal,
    resolve_plan,
)
from app.plan import PlanValidator  # noqa: E402
from app.team_config import ASSIGNABLE_MEMBERS  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "planner_results.json"
GOAL = "列出一个面向公众的生成式 AI 客服上线前的合规动作"

VALID_PLAN = {
    "tasks": [
        {
            "task_id": "research",
            "instruction": "查找法规依据",
            "acceptance_ids": ["research_accept"],
            "depends_on": [],
            "assignee": "research",
        },
        {
            "task_id": "engineering",
            "instruction": "给出上线清单",
            "acceptance_ids": ["engineering_accept"],
            "depends_on": [],
            "assignee": "engineering",
        },
    ]
}


def _validate(tasks) -> bool:
    return PlanValidator(members=list(ASSIGNABLE_MEMBERS)).validate(tasks).valid


async def _run(checks: Checks) -> None:
    fallback = fallback_plan(GOAL)
    checks.add(
        "the fallback plan is valid for the fixed roster",
        len(fallback) == 2 and _validate(fallback),
        f"tasks={[task.task_id for task in fallback]}",
    )
    checks.add(
        "the fallback keeps the goal in the research instruction",
        GOAL in fallback[0].instruction,
        fallback[0].instruction[:80],
    )
    checks.add(
        "the fallback keeps the two tasks independent",
        fallback[1].depends_on == (),
        f"depends_on={fallback[1].depends_on}",
    )
    checks.add(
        "both fallback tasks retrieve for themselves",
        all(f"task_id={task.task_id}" in task.instruction for task in fallback),
        f"instructions={[task.instruction[:40] for task in fallback]}",
    )
    checks.add(
        "the fallback never assigns the leader",
        all(task.assignee in ASSIGNABLE_MEMBERS for task in fallback),
        f"assignees={[task.assignee for task in fallback]}",
    )

    # A dependency without a reviewer is rejected by PlanValidator, and the
    # model schema cannot express reviewers, so advertising depends_on would
    # make every dependent plan fall back. The planner asks for parallel tasks.
    task_schema = PLAN_SCHEMA["properties"]["tasks"]["items"]["properties"]
    checks.add(
        "the model schema does not advertise dependencies",
        "depends_on" not in task_schema,
        f"properties={sorted(task_schema)}",
    )

    async def valid_run(goal, schema):
        return VALID_PLAN

    outcome = await plan_goal(GOAL, model=object(), run=valid_run)
    checks.add(
        "a valid model plan is used and marked as tiny",
        isinstance(outcome, PlanOutcome)
        and outcome.source == "tiny"
        and [task.task_id for task in outcome.tasks] == ["research", "engineering"]
        and _validate(outcome.tasks),
        f"source={outcome.source} tasks={[task.task_id for task in outcome.tasks]}",
    )

    async def leader_run(goal, schema):
        return {"tasks": [dict(VALID_PLAN["tasks"][0], assignee="team_leader")]}

    leader = await plan_goal(GOAL, model=object(), run=leader_run)
    checks.add(
        "an unknown assignee falls back",
        leader.source == "fallback" and _validate(leader.tasks),
        f"source={leader.source}",
    )

    async def cycle_run(goal, schema):
        return {
            "tasks": [
                dict(VALID_PLAN["tasks"][0], depends_on=["engineering"]),
                dict(VALID_PLAN["tasks"][1], depends_on=["research"]),
            ]
        }

    cycle = await plan_goal(GOAL, model=object(), run=cycle_run)
    checks.add("a cycle falls back", cycle.source == "fallback", cycle.source)

    async def broken_run(goal, schema):
        raise RuntimeError("model unavailable")

    broken = await plan_goal(GOAL, model=object(), run=broken_run)
    checks.add("an exception falls back", broken.source == "fallback", broken.source)

    async def prose_run(goal, schema):
        return "I think you should research this."

    prose = await plan_goal(GOAL, model=object(), run=prose_run)
    checks.add("prose instead of a plan falls back", prose.source == "fallback", prose.source)

    async def slow_run(goal, schema):
        await asyncio.sleep(5)
        return VALID_PLAN

    slow = await plan_goal(GOAL, model=object(), run=slow_run, timeout_seconds=0.05)
    checks.add("a slow planner falls back", slow.source == "fallback", slow.source)

    # resolve_plan is the supervisor's entry point: an explicit payload plan
    # wins, otherwise the planner runs. This is what the API and the CLI both
    # go through, so it must not depend on the tiny agent being reachable.
    explicit = await resolve_plan(list(VALID_PLAN["tasks"]), GOAL, model=object())
    checks.add(
        "an explicit payload plan is used without calling the model",
        explicit.source == "payload"
        and [task.task_id for task in explicit.tasks] == ["research", "engineering"],
        f"source={explicit.source}",
    )

    planned = await resolve_plan([], GOAL, model=object(), run=valid_run)
    checks.add(
        "no payload plan plans from the goal",
        planned.source == "tiny" and _validate(planned.tasks),
        f"source={planned.source}",
    )

    wanted = await resolve_plan([], GOAL, model=object(), run=broken_run)
    checks.add(
        "a missing model still produces a valid plan",
        wanted.source == "fallback" and _validate(wanted.tasks),
        f"source={wanted.source}",
    )

    config = model_from_credentials(
        {
            "MODEL_NAME": "deepseek-v3",
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "test-key",
        }
    )
    checks.add(
        "the planner model config matches the run credentials",
        config.model_client_config.client_provider == "OpenAI"
        and config.model_client_config.api_base == "https://example.test/v1"
        and config.model_request_config.model_name == "deepseek-v3",
        f"model={config.model_request_config.model_name}",
    )


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="planner validation and fallback with an injected model; no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
