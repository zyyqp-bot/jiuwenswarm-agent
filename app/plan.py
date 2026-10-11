"""Plan validator: a task plan is checked before it creates any framework task.

PROJECT.md section 6 step 4 requires the leader's plan to be validated before
the runtime adapter maps it onto native ``create_task`` calls: the DAG must be
acyclic, every dependency must exist, roles must be real, acceptance must be
declared, and a key task must carry a reviewer. Section 12's acceptance scenario
6 spells out the last one -- "a key task without a reviewer must not be accepted
by the plan validator".

A task is *key* when another task depends on its deliverable. That is the
property section 4's dependency example relies on ("the final plan depends on a
validated fact artifact, not merely on a completion message"), and it is the
only reading under which scenario 6 protects anything: a task nobody depends on
cannot hold up the run.

This is a deterministic check, deliberately separate from anything a model says
about the plan (PROJECT.md D05). It holds no framework objects, starts no
runtime and calls no model, and it reports every problem it finds rather than
stopping at the first.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.models import non_blank

__all__ = [
    "PlanIssue",
    "PlanIssueCode",
    "PlanTask",
    "PlanValidationReport",
    "PlanValidator",
]

DEFAULT_ROLES = ("leader", "teammate")


class PlanIssueCode(str, Enum):
    """What is wrong with a plan."""

    EMPTY_PLAN = "empty_plan"
    INVALID_TASK_ID = "invalid_task_id"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    SELF_DEPENDENCY = "self_dependency"
    CYCLIC_DEPENDENCY = "cyclic_dependency"
    UNKNOWN_ROLE = "unknown_role"
    MISSING_ACCEPTANCE = "missing_acceptance"
    MISSING_REVIEWER = "missing_reviewer"


class PlanTask(BaseModel):
    """One task in a plan, before it becomes a framework task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    role: str
    instruction: str
    acceptance_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    requires_success: bool = True
    """Whether this task needs its prerequisites to have *succeeded*. It says
    nothing about whether this task is itself key -- see the module docstring."""

    reviewers: tuple[str, ...] = ()


class PlanIssue(BaseModel):
    """One problem found in a plan."""

    model_config = ConfigDict(extra="forbid")

    code: PlanIssueCode
    task_id: str | None = None
    detail: str = ""


class PlanValidationReport(BaseModel):
    """The validator's verdict."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: tuple[PlanIssue, ...] = ()

    def codes(self) -> list[PlanIssueCode]:
        """Return the distinct issue codes, for compact assertions."""
        return list(dict.fromkeys(issue.code for issue in self.issues))


def _find_cycle(tasks: dict[str, PlanTask]) -> list[str] | None:
    """Return one dependency cycle as a task-id path, or None when acyclic.

    Iterative on purpose: a plan is data, and a long dependency chain must not
    be able to raise ``RecursionError`` out of a validator whose whole job is to
    return a report.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(tasks, WHITE)

    for start in tasks:
        if colour[start] != WHITE:
            continue
        path = [start]
        colour[start] = GREY
        stack = [iter(tasks[start].depends_on)]

        while stack:
            advanced = False
            for dependency in stack[-1]:
                if dependency not in tasks:
                    continue  # reported separately as an unknown dependency
                if dependency == path[-1]:
                    continue  # a self edge is a self-dependency, not a cycle
                if colour[dependency] == GREY:
                    return path[path.index(dependency) :] + [dependency]
                if colour[dependency] == WHITE:
                    colour[dependency] = GREY
                    path.append(dependency)
                    stack.append(iter(tasks[dependency].depends_on))
                    advanced = True
                    break
            if not advanced:
                colour[path[-1]] = BLACK
                path.pop()
                stack.pop()

    return None


class PlanValidator:
    """Validate a structured task plan before it is submitted.

    ``roles`` are the role keys the roster accepts -- for the MVP's fixed
    leader + two teammates that is ``{"leader", "teammate"}``, because agent
    templates are keyed by role, not by member name.
    """

    def __init__(self, *, roles: Iterable[str] = DEFAULT_ROLES) -> None:
        self._roles = {role for role in roles}

    def validate(self, plan: Sequence[PlanTask]) -> PlanValidationReport:
        """Return a report describing every problem found, not just the first."""
        issues: list[PlanIssue] = []

        if not plan:
            return PlanValidationReport(
                valid=False,
                issues=(PlanIssue(code=PlanIssueCode.EMPTY_PLAN, detail="the plan declares no tasks"),),
            )

        # ``canonical`` holds the first declaration of each id and drives the
        # graph checks; ``declared`` keeps every task -- including duplicates and
        # tasks with a blank id -- so the per-task checks still see all of them.
        canonical: dict[str, PlanTask] = {}
        declared: list[PlanTask] = []
        for task in plan:
            if not task.task_id.strip():
                issues.append(PlanIssue(code=PlanIssueCode.INVALID_TASK_ID, detail="task id is blank"))
                continue
            if task.task_id in canonical:
                issues.append(
                    PlanIssue(
                        code=PlanIssueCode.DUPLICATE_TASK_ID,
                        task_id=task.task_id,
                        detail="task id declared more than once",
                    )
                )
            else:
                canonical[task.task_id] = task
            declared.append(task)

        # Checked over every declaration, not just the canonical map, so a
        # duplicated task's own dependency problems are reported too.
        for task in declared:
            for dependency in task.depends_on:
                if dependency == task.task_id:
                    issues.append(
                        PlanIssue(
                            code=PlanIssueCode.SELF_DEPENDENCY,
                            task_id=task.task_id,
                            detail="task depends on itself",
                        )
                    )
                elif dependency not in canonical:
                    issues.append(
                        PlanIssue(
                            code=PlanIssueCode.UNKNOWN_DEPENDENCY,
                            task_id=task.task_id,
                            detail=f"unknown prerequisite {dependency!r}",
                        )
                    )

        cycle = _find_cycle(canonical)
        if cycle is not None:
            issues.append(
                PlanIssue(
                    code=PlanIssueCode.CYCLIC_DEPENDENCY,
                    task_id=cycle[0],
                    detail="dependency cycle: " + " -> ".join(cycle),
                )
            )

        depended_on = {dependency for task in declared for dependency in task.depends_on}

        for task in declared:
            if task.role not in self._roles:
                issues.append(
                    PlanIssue(
                        code=PlanIssueCode.UNKNOWN_ROLE,
                        task_id=task.task_id,
                        detail=f"role {task.role!r} is not in the roster",
                    )
                )
            if not non_blank(task.acceptance_ids):
                issues.append(
                    PlanIssue(
                        code=PlanIssueCode.MISSING_ACCEPTANCE,
                        task_id=task.task_id,
                        detail="task declares no acceptance condition",
                    )
                )
            # Key task: another task depends on this one's deliverable.
            if task.task_id in depended_on and not non_blank(task.reviewers):
                issues.append(
                    PlanIssue(
                        code=PlanIssueCode.MISSING_REVIEWER,
                        task_id=task.task_id,
                        detail="a key task (depended on by another task) must declare a reviewer",
                    )
                )

        return PlanValidationReport(valid=not issues, issues=tuple(issues))
