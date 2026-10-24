"""A stand-in for ``app/supervisor.py`` in offline service checks.

Behaves according to the payload goal so one service instance can exercise
every path without environment juggling:

- goal containing ``crash``  -> writes one snapshot, exits 1;
- goal containing ``sleep``  -> writes one snapshot and sleeps until killed;
- anything else              -> starting, streaming, returned (complete).

When the payload carries a workspace corpus, the fake uses the *real*
RetrievalTool against it and cites the id it returned. That is what makes the
end-to-end check's "the report contains an evidence id the tool returned"
claim real rather than a fixture the report was handed.

Snapshots are written atomically, exactly like the real supervisor.

Run: python p1/fake_supervisor.py --payload <path>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _snapshot(
    run_id: str,
    phase: str,
    *,
    complete: bool,
    plan: list[dict] | None = None,
    planner: str = "",
    tasks: list[dict] | None = None,
    evidence: dict | None = None,
    evidence_details: dict | None = None,
    results: dict | None = None,
    error: str | None = None,
    stop_reason: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "phase": phase,
        "complete": complete,
        "stream_stop_reason": stop_reason or ("team_completed" if complete else None),
        "error": error,
        "planner": planner,
        "plan": plan or [],
        "tasks": tasks or [],
        "evidence": evidence or {},
        "evidence_details": evidence_details or {},
        "results": results or {},
        "team_markers": ["team.completed"] if complete else [],
    }


def _resolve_plan(payload: dict, goal: str) -> tuple[list, str]:
    from app.plan import PlanTask
    from app.planner import fallback_plan

    raw = payload.get("plan") or []
    if raw:
        return [PlanTask(**{"role": "teammate", **item}) for item in raw], "payload"
    return fallback_plan(goal), "fallback"


def _board(plan: list, *, status: str) -> list[dict]:
    return [
        {
            "task_id": task.task_id,
            "assignee": task.assignee,
            "status": status,
            "blocked_by": list(task.depends_on),
        }
        for task in plan
    ]


def _synthetic_results() -> dict:
    """The no-corpus shape: one claim, so report writing is still exercised."""
    return {
        "research": {
            "producer": "research",
            "outcome": "success",
            "execution_id": "fake:research:1",
            "artifact_refs": ["claims:research"],
            "claims": [{"text": "done", "kind": "inference", "evidence_ids": ["ev-1"]}],
        }
    }


def _retrieve(payload: dict, plan: list, goal: str) -> tuple[dict, dict, dict]:
    """Use the real retrieval tool; return (evidence, details, results)."""
    from app.knowledge import KnowledgeIndex
    from app.retrieval_tool import RetrievalTool
    from app.run_bridge import RunBridge

    corpus = payload.get("corpus") or {}
    db_path = Path(str(corpus.get("db_path") or ""))
    if not db_path.is_file():
        return {}, {}, _synthetic_results()

    async def collect():
        bridge = RunBridge(run_id=str(payload["run_id"]), plan=plan)
        index = await KnowledgeIndex.open(
            db_path,
            corpus=str(corpus["corpus"]),
            access_scope=str(corpus["access_scope"]),
        )
        try:
            tool = RetrievalTool(
                bridge,
                index,
                producer="research",
                corpus=str(corpus["corpus"]),
                access_scope=str(corpus["access_scope"]),
            )
            outcome = await tool.invoke(
                {"task_id": "research", "query": goal, "limit": 3}
            )
            # A long natural-language goal may match no trigram; the fake has
            # no model to reformulate it, so retry with a term the fixture
            # document definitely contains. The real member would reformulate.
            if not (outcome.data or {}).get("evidence_ids"):
                outcome = await tool.invoke(
                    {"task_id": "research", "query": "安全评估", "limit": 3}
                )
        finally:
            await index.close()
        return outcome, bridge.snapshot()

    outcome, snapshot = asyncio.run(collect())
    data = outcome.data or {}
    ids = list(data.get("evidence_ids") or [])
    evidence = snapshot.get("evidence") or {}
    details = snapshot.get("evidence_details") or {}
    if not ids:
        return evidence, details, {}
    results = {
        "research": {
            "producer": "research",
            "outcome": "success",
            "execution_id": "fake:research:1",
            "artifact_refs": ["claims:research"],
            "claims": [
                {
                    "text": "检索到的合规要求见证据",
                    "kind": "fact",
                    "evidence_ids": ids[:1],
                }
            ],
        }
    }
    return evidence, details, results


def main(argv: list[str]) -> int:
    payload_path = Path(argv[argv.index("--payload") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    run_id = payload["run_id"]
    goal = str(payload.get("goal", ""))
    snapshot_path = Path(payload["snapshot_path"])
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    plan, planner = _resolve_plan(payload, goal)
    plan_raw = [task.model_dump(mode="json") for task in plan]

    _write(
        snapshot_path,
        _snapshot(
            run_id,
            "starting",
            complete=False,
            plan=plan_raw,
            planner=planner,
            tasks=_board(plan, status="pending"),
        ),
    )
    time.sleep(0.05)

    if "crash" in goal.lower():
        sys.exit(1)

    evidence, details, results = _retrieve(payload, plan, goal)
    _write(
        snapshot_path,
        _snapshot(
            run_id,
            "streaming",
            complete=False,
            plan=plan_raw,
            planner=planner,
            tasks=_board(plan, status="in_progress"),
            evidence=evidence,
            evidence_details=details,
            results=results,
        ),
    )
    time.sleep(0.05)

    if "sleep" in goal.lower() and "sleepend" not in goal.lower():
        while True:
            time.sleep(0.2)

    # A complete snapshot that does not carry the native completion reason is
    # not a success: the run deadline and idle grace both end this way.
    if "deadline" in goal.lower():
        _write(
            snapshot_path,
            _snapshot(
                run_id,
                "returned",
                complete=True,
                stop_reason="deadline",
                plan=plan_raw,
                planner=planner,
                tasks=_board(plan, status="in_progress"),
                evidence=evidence,
                evidence_details=details,
                results=results,
            ),
        )
        return 1

    _write(
        snapshot_path,
        _snapshot(
            run_id,
            "returned",
            complete=True,
            plan=plan_raw,
            planner=planner,
            tasks=_board(plan, status="completed"),
            evidence=evidence,
            evidence_details=details,
            results=results,
        ),
    )
    if "sleepend" in goal.lower():
        while True:
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
