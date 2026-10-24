"""P1: the run bridge accepts exactly one well-formed result per execution.

The bridge is where "who wrote this", "did they cite evidence they actually
received", and "does the completion gate accept it" meet. Each of the five
cases here is a way a result could otherwise slip past a gate: wrong author,
unknown evidence, a replay, or an empty shell.

Offline: no team, no runner, no model. The native completion callback is a
counter.

Run: .venv/Scripts/python.exe p1/submit_tool_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import AcceptanceCheck, AcceptanceResult, Claim, ClaimKind, TaskOutcome  # noqa: E402
from app.plan import PlanTask  # noqa: E402
from app.run_bridge import RunBridge  # noqa: E402
from checks.harness import Checks  # noqa: E402

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
    bridge.bind_native_complete(lambda task_id: _record(completed, task_id))
    execution = bridge.execution_for("research")
    outcome = await bridge.submit(
        producer="research_member",
        task_id="research",
        execution_id=execution,
        outcome=TaskOutcome.SUCCESS,
        claims=[_claim()],
        acceptance=ACCEPTANCE,
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


async def _record(completed: list[str], task_id: str) -> None:
    completed.append(task_id)


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="bridge unit checks only; no team, no runner, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
