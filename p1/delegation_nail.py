"""P1 nail: does the native scheduler actually hand tasks to the members?

Everything before this proved pieces. This is the first run where the answer
depends on the framework doing what the design assumes: the app submits two
assigned tasks, the scheduled scheduler starts both members, each member calls
``submit_task_result``, and the run ends with ``team.completed``.

The plan's two tasks are independent on purpose. A dependency would make the
first task "key" and require a reviewer, which would add the native review
round to the critical path before delegation itself is proven. Dependency
validity and review settlement are separate checks.

The evidence ids are written into the instructions because there is no
retrieval tool yet: the member must be able to cite something the bridge
knows it received.

Costs money: two members plus the leader each make at least one model call.

Run: .venv/Scripts/python.exe p1/delegation_nail.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p1"))

from app.tool_guard import is_forbidden  # noqa: E402
from checks.harness import Checks  # noqa: E402
from live_round_check import _load_credentials  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "delegation_nail_results.json"
RUNTIME_DIR = Path(__file__).resolve().parent / ".live_round" / "nail"

EVIDENCE = {
    "research": ["ev-1", "ev-2"],
    "engineering": ["ev-1", "ev-2"],
}

PLAN = [
    {
        "task_id": "research",
        "role": "teammate",
        "instruction": (
            "用给定证据回答：为什么开发前要先核对源码版本？"
            "可用证据 id 仅为 ev-1、ev-2。"
            "用 submit_task_result 提交，task_id=research，outcome=success；"
            "至少提交一条 kind=fact 的 claim，evidence_ids 从 ev-1/ev-2 中选。"
        ),
        "acceptance_ids": ["research_accept"],
        "assignee": "research",
    },
    {
        "task_id": "engineering",
        "role": "teammate",
        "instruction": (
            "基于给定证据给出一条工程建议。可用证据 id 仅为 ev-1、ev-2。"
            "用 submit_task_result 提交，task_id=engineering，outcome=success；"
            "至少提交一条 kind=fact 的 claim，evidence_ids 从 ev-1/ev-2 中选。"
        ),
        "acceptance_ids": ["engineering_accept"],
        "assignee": "engineering",
    },
]

def _forbidden(names: list[str]) -> list[str]:
    return [name for name in names if is_forbidden(name)]


async def _run(checks: Checks, credentials: dict[str, str]) -> None:
    from app.supervisor import run_payload

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"nail-{int(time.time())}"
    payload = {
        "run_id": run_id,
        "goal": (
            "团队任务板已经为本轮创建了两个已分配任务。不要自己重做，"
            "等待成员提交；如果任务已完成，直接给出简短总结。"
        ),
        "plan": PLAN,
        "members": ["research", "engineering"],
        "credentials": credentials,
        "evidence_by_task": EVIDENCE,
        "session_id": f"session-{run_id}",
        "snapshot_path": str(RUNTIME_DIR / "snapshot.json"),
        "results_path": str(RUNTIME_DIR / "results.json"),
    }
    snapshot = await run_payload(payload)

    results = snapshot.get("results") or {}
    checks.add(
        "both assigned members produced a result",
        set(results) == {"research", "engineering"},
        f"results={sorted(results)}",
    )
    checks.add(
        "each result was produced by its assignee",
        results.get("research", {}).get("producer") == "research"
        and results.get("engineering", {}).get("producer") == "engineering",
        f"producers={ {key: value.get('producer') for key, value in results.items()} }",
    )
    seeded = set(EVIDENCE["research"]) | set(EVIDENCE["engineering"])
    facts = [
        claim
        for result in results.values()
        for claim in result.get("claims", [])
        if claim.get("kind") == "fact"
    ]
    checks.add(
        "a fact claim cites evidence this run delivered",
        bool(facts)
        and all(
            (claim.get("evidence_ids") or []) and set(claim["evidence_ids"]) <= seeded
            for claim in facts
        ),
        f"facts={facts}",
    )
    checks.add(
        "the run ended through the native completion marker",
        "team.completed" in (snapshot.get("team_markers") or []),
        f"markers={snapshot.get('team_markers')} stop={snapshot.get('stream_stop_reason')!r}",
    )
    requests = snapshot.get("tool_requests") or []
    tools = snapshot.get("tool_names") or []
    checks.add(
        "the request carries the submit tool",
        any("submit_task_result" in request.get("names", []) for request in requests),
        f"last_tools={tools}",
    )
    leaked = [name for request in requests for name in _forbidden(request.get("names") or [])]
    checks.add(
        "no retained request carries a shell, file, spawn or board tool",
        bool(requests) and not leaked,
        f"requests={len(requests)} forbidden={leaked}",
    )
    errors = snapshot.get("errors") or []
    identity_errors = [error for error in errors if "not the assignee" in error]
    checks.add(
        "no submission was rejected for identity",
        not identity_errors,
        f"identity_errors={identity_errors}",
    )


def main() -> int:
    checks = Checks()
    credentials = _load_credentials()
    if credentials is None:
        checks.add("model credentials are available", False, "no .env.local and no MODEL_* env")
        return checks.report(RESULTS_PATH, scope="blocked: no credentials", plan=PLAN)

    try:
        asyncio.run(_run(checks, credentials))
    except Exception as exc:  # noqa: BLE001 - reported, then persisted
        checks.add("the nail ran to completion", False, f"{type(exc).__name__}: {exc}")

    return checks.report(
        RESULTS_PATH,
        scope="one real run with two assigned tasks; costs money and is non-deterministic",
        plan=PLAN,
        evidence_by_task=EVIDENCE,
    )


if __name__ == "__main__":
    sys.exit(main())
