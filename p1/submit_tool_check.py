"""P1: the run bridge accepts exactly one well-formed result per execution.

The bridge is where "who wrote this", "did they cite evidence they actually
received", "does the completion gate accept it", and "did the native board
actually move" meet. Each case here is a way a result could otherwise slip
past a gate: wrong author, unknown evidence, a replay, an empty shell, or a
native completion that was rejected while the member was told it succeeded.

The native completion callback is the member's own task manager. The bridge
must check its returned ``TaskOpResult``, and a rejected completion must leave
the execution unsubmitted so the member can retry.

Offline: no team, no runner, no model.

Run: .venv/Scripts/python.exe p1/submit_tool_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.hooks import register_bridge, unregister_bridge  # noqa: E402
from app.models import AcceptanceCheck, AcceptanceResult, Claim, ClaimKind, TaskOutcome  # noqa: E402
from app.plan import PlanTask  # noqa: E402
from app.run_bridge import RunBridge  # noqa: E402
from app.submit_tool import make_submit_tool  # noqa: E402
from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.schema.task import TaskOpResult  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "submit_tool_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="x",
        acceptance_ids=("a1",),
        assignee="research_member",
    )
]
REVIEW_PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="x",
        acceptance_ids=("a1",),
        assignee="research_member",
        reviewers=("team_leader",),
    )
]
ACCEPTANCE = {
    "a1": AcceptanceCheck(
        acceptance_id="a1",
        result=AcceptanceResult.PASS,
        verifier="program",
        evidence_refs=("ev-1",),
    )
}


def _bridge(evidence: dict[str, list[str]] | None = None) -> RunBridge:
    return RunBridge(run_id="run-1", plan=PLAN, evidence_by_task=evidence or {"research": ["ev-1"]})


def _claim(**overrides) -> Claim:
    values = {"text": "a supported fact", "kind": ClaimKind.FACT, "evidence_ids": ("ev-1",)}
    values.update(overrides)
    return Claim(**values)


async def _run(checks: Checks) -> None:
    # --- case 1: the assignee, a supported fact, no artifact refs ----------
    bridge = _bridge()
    completed: list[str] = []
    execution = bridge.execution_for("research")
    outcome = await bridge.submit(
        producer="research_member",
        task_id="research",
        execution_id=execution,
        outcome=TaskOutcome.SUCCESS,
        claims=[_claim()],
        acceptance=ACCEPTANCE,
        complete=_complete_ok(completed),
    )
    stored = bridge.results().get("research")
    checks.add("the assignee's supported result is accepted", outcome.ok, outcome.error or "accepted")
    checks.add(
        "claims become the artifact when no file artifact exists",
        stored is not None and stored.artifact_refs == (f"claims:research:{execution}",),
        f"artifact_refs={stored.artifact_refs if stored else None}",
    )
    checks.add("native completion ran once", completed == ["research"], f"completed={completed}")

    # --- case 2: a different member cannot submit this task ----------------
    other = _bridge()
    execution = other.execution_for("research")
    outcome = await other.submit(
        producer="team_leader",
        task_id="research",
        execution_id=execution,
        outcome=TaskOutcome.SUCCESS,
        claims=[_claim()],
        acceptance=ACCEPTANCE,
    )
    checks.add(
        "another member cannot submit the task",
        not outcome.ok and "not the assignee" in outcome.error,
        outcome.error,
    )

    # --- case 3: evidence the task never received --------------------------
    third = _bridge()
    execution = third.execution_for("research")
    outcome = await third.submit(
        producer="research_member",
        task_id="research",
        execution_id=execution,
        outcome=TaskOutcome.SUCCESS,
        claims=[_claim(evidence_ids=("ev-9",))],
        acceptance=ACCEPTANCE,
    )
    checks.add(
        "evidence outside the task's context is rejected",
        not outcome.ok and "unknown_evidence" in outcome.error,
        outcome.error,
    )

    # --- case 4: replay of the same execution ------------------------------
    replay = _bridge()
    execution = replay.execution_for("research")
    first = await replay.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
    )
    second = await replay.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
    )
    checks.add(
        "the same execution cannot submit twice",
        first.ok and not second.ok,
        f"first={first.ok} second={second.error}",
    )

    # --- case 5: an empty shell -------------------------------------------
    empty = _bridge()
    execution = empty.execution_for("research")
    outcome = await empty.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[], artifact_refs=(),
        acceptance=ACCEPTANCE,
    )
    checks.add(
        "an empty submission is rejected",
        not outcome.ok and "empty result" in outcome.error,
        outcome.error,
    )

    # --- case 6: the native board rejects the completion -------------------
    rejected = _bridge()
    execution = rejected.execution_for("research")
    outcome = await rejected.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
        complete=_complete_rejected(),
    )
    checks.add(
        "a native completion rejection fails the submission",
        not outcome.ok and "native completion rejected" in outcome.error,
        outcome.error,
    )
    checks.add(
        "a rejected completion leaves the execution unsubmitted",
        rejected.results() == {},
        f"results={list(rejected.results())}",
    )

    # --- case 7: the native board raises -----------------------------------
    broken = _bridge()
    execution = broken.execution_for("research")
    outcome = await broken.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
        complete=_complete_raises(),
    )
    checks.add(
        "a native completion exception fails the submission",
        not outcome.ok and "native completion failed" in outcome.error,
        outcome.error,
    )
    checks.add(
        "an exception leaves the execution unsubmitted",
        broken.results() == {},
        f"results={list(broken.results())}",
    )

    # --- case 8: acceptance is judged by the app, not self-reported --------
    app_judged = _bridge()
    execution = app_judged.execution_for("research")
    outcome = await app_judged.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()],
        acceptance={},
        complete=_complete_ok([]),
    )
    checks.add(
        "the app's structural acceptance needs no model-supplied verdict",
        outcome.ok,
        outcome.error or "accepted",
    )

    # --- case 9: a reviewed task waits for the native verdict --------------
    reviewed = RunBridge(
        run_id="run-r", plan=REVIEW_PLAN, evidence_by_task={"research": ["ev-1"]}
    )
    execution = reviewed.execution_for("research")
    outcome = await reviewed.submit(
        producer="research_member", task_id="research", execution_id=execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
        complete=_complete_ok([]),
    )
    checks.add(
        "a reviewed submission is accepted but not yet a result",
        outcome.ok and reviewed.results() == {},
        f"ok={outcome.ok} results={list(reviewed.results())}",
    )
    checks.add(
        "the pending review is visible in the snapshot",
        reviewed.snapshot().get("pending_reviews") == ["research"],
        f"pending={reviewed.snapshot().get('pending_reviews')}",
    )
    checks.add(
        "a completed native task accepts the pending result",
        reviewed.settle_review("research", "completed") == "accepted"
        and "research" in reviewed.results()
        and reviewed.snapshot().get("pending_reviews") == [],
        f"results={list(reviewed.results())}",
    )

    # --- case 10: a rejected review reopens the execution ------------------
    reworked = RunBridge(
        run_id="run-w", plan=REVIEW_PLAN, evidence_by_task={"research": ["ev-1"]}
    )
    first_execution = reworked.execution_for("research")
    await reworked.submit(
        producer="research_member", task_id="research", execution_id=first_execution,
        outcome=TaskOutcome.SUCCESS, claims=[_claim()], acceptance=ACCEPTANCE,
        complete=_complete_ok([]),
    )
    checks.add(
        "a rejected review discards the pending result",
        reworked.settle_review("research", "in_progress") == "rework"
        and reworked.results() == {},
        f"results={list(reworked.results())}",
    )
    checks.add(
        "a rejected review opens a new execution",
        reworked.execution_for("research") != first_execution,
        f"first={first_execution} next={reworked.execution_for('research')}",
    )
    checks.add(
        "settling an unknown task is ignored",
        reworked.settle_review("engineering", "completed") == "ignored",
        "unknown task",
    )

    # --- case 11: the producer is bound at build time, never by the model ---
    bound = RunBridge(run_id="run-factory", plan=PLAN)
    register_bridge("run-factory", bound)
    try:
        tool = make_submit_tool(
            {"run_id": "run-factory"},
            type("Ctx", (), {"member_name": "research_member"})(),
        )
        checks.add("the factory binds the build context's member", tool.producer == "research_member", tool.producer)
        missing = ""
        try:
            make_submit_tool({"run_id": "run-factory"}, type("Ctx", (), {})())
        except RuntimeError as exc:
            missing = str(exc)
        checks.add(
            "a context without a member name refuses to build the tool",
            "member_name" in missing,
            missing or "no error",
        )
    finally:
        unregister_bridge("run-factory")


def _complete_ok(completed: list[str]):
    async def complete(task_id: str) -> TaskOpResult:
        completed.append(task_id)
        return TaskOpResult.success()

    return complete


def _complete_rejected():
    async def complete(task_id: str) -> TaskOpResult:
        return TaskOpResult.fail("task is assigned to another member")

    return complete


def _complete_raises():
    async def complete(task_id: str) -> TaskOpResult:
        raise RuntimeError("native board unavailable")

    return complete


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="bridge unit checks only; no team, no runner, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
