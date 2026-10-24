"""Plan the task graph from the goal, with a deterministic fallback.

A model-produced plan is a proposal, not an authority: it is parsed, validated
against the fixed roster, and replaced by the two-task template on any invalid
shape, exception or timeout. The demo must not fail because the model returned
prose instead of JSON.

The planner runs before the team is built, so it uses a TinyAgent (no tools,
one structured-output call) rather than the team Leader.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.plan import PlanTask, PlanValidator
from app.team_config import ASSIGNABLE_MEMBERS

__all__ = [
    "PLAN_SCHEMA",
    "PlanOutcome",
    "fallback_plan",
    "model_from_credentials",
    "plan_goal",
    "resolve_plan",
]

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "instruction": {"type": "string"},
                    "acceptance_ids": {"type": "array", "items": {"type": "string"}},
                    "assignee": {"type": "string", "enum": ["research", "engineering"]},
                },
                "required": ["task_id", "instruction", "acceptance_ids", "assignee"],
            },
        }
    },
    "required": ["tasks"],
}

SYSTEM_PROMPT = (
    "你是任务规划器。把用户目标拆成 2-4 个可由两名成员并行执行的任务。"
    "assignee 只能是 research 或 engineering：research 负责检索资料并给出带出处的发现，"
    "engineering 负责在这些发现之上给出方案或清单。"
    "任务之间不要声明依赖；每个任务必须给出非空的 acceptance_ids。"
    "只通过结构化输出回答问题。"
)


@dataclass(frozen=True)
class PlanOutcome:
    """The plan the supervisor will use, and where it came from."""

    tasks: list[PlanTask]
    source: str  # "tiny" | "fallback"


def fallback_plan(goal: str) -> list[PlanTask]:
    """The two-task template used when the model plan is unusable."""
    target = goal.strip()
    return [
        PlanTask(
            task_id="research",
            role="teammate",
            instruction=(
                f"目标：{target}\n"
                "用 retrieve 工具检索本工作区语料（task_id=research）。"
                "检索词用 2-6 个字的关键词（如「安全评估」「内容标识」），"
                "不要拿整句目标去搜；可以分多次检索不同关键词。"
                "整理有出处的发现；每条 fact 必须引用 retrieve 返回的 evidence id，"
                "最后用 submit_task_result 提交（task_id=research, outcome=success）。"
            ),
            acceptance_ids=("research_accept",),
            assignee="research",
        ),
        PlanTask(
            task_id="engineering",
            role="teammate",
            instruction=(
                f"目标：{target}\n"
                "只使用本任务实际收到的证据，给出可执行的方案或清单；"
                "每条 fact 必须引用 evidence id，"
                "最后用 submit_task_result 提交（task_id=engineering, outcome=success）。"
            ),
            acceptance_ids=("engineering_accept",),
            # Deliberately independent: the plan validator requires a reviewer
            # for any task another task depends on, and this fixed roster has
            # no spare reviewer. The demo's two tasks are parallel for the same
            # reason the delegation nail used two independent tasks.
            assignee="engineering",
        ),
    ]


def model_from_credentials(credentials: Mapping[str, str]) -> Any:
    """Build the one model config the tiny planner needs.

    The planner runs before any team exists, so it cannot resolve a model
    name against the team pool; this mirrors the shape the team config uses
    (``client_provider=OpenAI``, same base URL and key).
    """
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
    from openjiuwen.harness.schema.deep_agent_spec import TeamModelConfig

    return TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key=credentials["MODEL_API_KEY"],
            api_base=credentials["MODEL_BASE_URL"],
        ),
        model_request_config=ModelRequestConfig(
            model=credentials["MODEL_NAME"],
            temperature=0.2,
        ),
    )


async def resolve_plan(
    raw: list[dict[str, Any]] | None,
    goal: str,
    *,
    model: Any = None,
    run: Callable[[str, Any], Awaitable[Any]] | None = None,
    timeout_seconds: float = 60.0,
) -> PlanOutcome:
    """The supervisor's entry point: an explicit plan wins, else plan the goal.

    A malformed explicit plan raises ``ValueError`` so the supervisor reports
    ``invalid_plan`` instead of silently substituting a different graph.
    """
    if raw:
        return PlanOutcome(
            [PlanTask(**{"role": "teammate", **item}) for item in raw], "payload"
        )
    if model is None:
        return PlanOutcome(fallback_plan(goal), "fallback")
    return await plan_goal(goal, model=model, run=run, timeout_seconds=timeout_seconds)


async def plan_goal(
    goal: str,
    *,
    model: Any,
    run: Callable[[str, Any], Awaitable[Any]] | None = None,
    timeout_seconds: float = 60.0,
) -> PlanOutcome:
    """Return a validated plan, or the fallback when the model output is unusable."""
    try:
        raw = await asyncio.wait_for(
            run(goal, PLAN_SCHEMA) if run is not None else _run_tiny(goal, model),
            timeout_seconds,
        )
    except Exception:  # noqa: BLE001 - any planner failure uses the template
        return PlanOutcome(fallback_plan(goal), "fallback")

    tasks = _parse(raw)
    if tasks is None:
        return PlanOutcome(fallback_plan(goal), "fallback")
    if not PlanValidator(members=list(ASSIGNABLE_MEMBERS)).validate(tasks).valid:
        return PlanOutcome(fallback_plan(goal), "fallback")
    return PlanOutcome(tasks, "tiny")


async def _run_tiny(goal: str, model: Any) -> Any:
    from openjiuwen.agent_teams.tiny_agent import create_tiny_agent

    agent = create_tiny_agent(
        system_prompt=SYSTEM_PROMPT,
        model_name="leader",
        model_resolver=lambda _: model,
        default_schema=PLAN_SCHEMA,
        language="cn",
        max_iterations=2,
    )
    try:
        return await agent.run(goal, schema=PLAN_SCHEMA)
    finally:
        close = getattr(agent, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - disposal must not mask the result
                pass


def _parse(raw: Any) -> list[PlanTask] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    entries = raw.get("tasks")
    if not isinstance(entries, list) or not entries:
        return None
    tasks: list[PlanTask] = []
    try:
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            tasks.append(
                PlanTask(
                    task_id=str(entry.get("task_id") or "").strip(),
                    role="teammate",
                    instruction=str(entry.get("instruction") or "").strip(),
                    acceptance_ids=tuple(str(item) for item in entry.get("acceptance_ids") or ()),
                    depends_on=tuple(str(item) for item in entry.get("depends_on") or ()),
                    assignee=str(entry.get("assignee") or "").strip(),
                )
            )
    except Exception:  # noqa: BLE001 - a malformed entry is a fallback, not a crash
        return None
    return tasks
