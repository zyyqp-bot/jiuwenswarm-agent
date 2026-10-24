"""Turn a validated plan into the native task graph.

The plan has already been checked (acyclic, roster, acceptance, reviewer
coverage) by ``PlanValidator``. This module adds nothing to that judgement: it
only renames fields and submits the batch. Any rule repeated here would create
a second authority for the same decision.

The reviewer shape is the native one: ``{"reviewer_id": <member>, "type":
"verifier"}``, which the scheduler uses to pick the reviewer prompt and to
match votes.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openjiuwen.agent_teams.schema.task import TaskGraphResult, TaskGraphSpec

from app.plan import PlanTask


def _reviewer_entries(names: Sequence[str]) -> tuple[dict[str, str], ...]:
    return tuple({"reviewer_id": name, "type": "verifier"} for name in names if name.strip())


def plan_to_graph(plan: Sequence[PlanTask]) -> list[TaskGraphSpec]:
    """Convert plan tasks to native specs, preserving order and every edge."""
    return [
        TaskGraphSpec(
            task_id=task.task_id,
            title=task.task_id,
            content=task.instruction,
            depends_on=tuple(task.depends_on),
            assignee=task.assignee or None,
            reviewer=_reviewer_entries(task.reviewers),
        )
        for task in plan
    ]


async def submit_graph(backend: Any, plan: Sequence[PlanTask]) -> TaskGraphResult:
    """Submit the graph through the backend's native task manager.

    Returns the native result unchanged, including its failure reason: the
    caller decides what a failed graph means for the run.
    """
    return await backend.task_manager.add_graph(plan_to_graph(plan))
