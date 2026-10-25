"""The product path against the real model and the real corpus.

The delegation nail passes an explicit plan and seeded evidence. This one
does neither: the supervisor plans the goal with the TinyAgent, the members
must call ``retrieve`` on the committed regulation corpus, and the report is
built from what they actually cited.

Costs money: planner + leader + members, several model calls.

Run: .venv/Scripts/python.exe p1/live_product_check.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p1"))

from app.ingest import import_corpus  # noqa: E402
from app.knowledge import corpus_db_path  # noqa: E402
from app.models import Claim, ClaimKind, TaskOutcome, TaskResult  # noqa: E402
from app.plan import PlanTask  # noqa: E402
from app.report import build_report, report_markdown  # noqa: E402
from app.supervisor import run_payload  # noqa: E402
from app.tool_guard import is_allowed  # noqa: E402
from checks.harness import Checks  # noqa: E402
from live_round_check import _load_credentials  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "live_product_results.json"
RUNTIME_DIR = Path(__file__).resolve().parent / ".live_round" / "product"
GOAL = (
    "面向公众的生成式 AI 客服上线前，必须完成哪些合规动作？"
    "请给出可执行清单，每条都引用公开法规原文。"
)


async def _run(checks: Checks, credentials: dict[str, str]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data_dir = RUNTIME_DIR / "data"
    imported = await import_corpus(
        data_dir=data_dir,
        corpus="ai-governance",
        scope="public",
        source_dir=ROOT / "corpus" / "ai-governance",
    )
    checks.add(
        "the committed corpus imports",
        imported["documents"] == 6 and not imported["failures"],
        f"imported={imported}",
    )

    run_id = f"product-{int(time.time())}"
    payload = {
        "run_id": run_id,
        "goal": GOAL,
        "session_id": f"session-{run_id}",
        "credentials": credentials,
        "corpus": {
            "db_path": str(corpus_db_path(data_dir, "ai-governance")),
            "corpus": "ai-governance",
            "access_scope": "public",
        },
        "snapshot_path": str(RUNTIME_DIR / "snapshot.json"),
        "results_path": str(RUNTIME_DIR / "results.json"),
    }
    snapshot = await run_payload(payload)

    checks.add(
        "the run ends through the native completion marker",
        snapshot.get("phase") == "returned"
        and snapshot.get("stream_stop_reason") == "team_completed"
        and not snapshot.get("error"),
        f"phase={snapshot.get('phase')} stop={snapshot.get('stream_stop_reason')} error={snapshot.get('error')}",
    )
    checks.add(
        "the planner produced or fell back to a valid graph",
        snapshot.get("planner") in ("tiny", "fallback")
        and 2 <= len(snapshot.get("plan") or []) <= 4,
        f"planner={snapshot.get('planner')} tasks={[t.get('task_id') for t in snapshot.get('plan') or []]}",
    )

    results = snapshot.get("results") or {}
    evidence = snapshot.get("evidence") or {}
    details = snapshot.get("evidence_details") or {}
    planned = snapshot.get("plan") or []
    by_id = {task.get("task_id"): task.get("assignee") for task in planned}
    checks.add(
        "every planned task produced a result by its assignee",
        bool(planned)
        and set(by_id) <= set(results)
        and all(results[task_id].get("producer") == assignee for task_id, assignee in by_id.items()),
        f"planned={by_id} results={sorted(results)}",
    )
    facts = [
        (task_id, claim)
        for task_id, result in results.items()
        for claim in result.get("claims", [])
        if claim.get("kind") == "fact"
    ]
    checks.add(
        "the members produced cited facts",
        bool(facts),
        f"facts={len(facts)}",
    )
    wrong = [
        (task_id, evidence_id)
        for task_id, claim in facts
        for evidence_id in claim.get("evidence_ids") or []
        if evidence_id not in set(evidence.get(task_id) or [])
    ]
    checks.add(
        "every cited fact uses evidence its task retrieved",
        not wrong,
        f"wrong={wrong[:3]}",
    )
    checks.add(
        "the cited passages are in the snapshot",
        all(evidence_id in details for task_id, claim in facts for evidence_id in claim.get("evidence_ids") or []),
        f"details={len(details)}",
    )

    requests = snapshot.get("tool_requests") or []
    denied = [
        name
        for request in requests
        for name in request.get("names") or []
        if not is_allowed(name)
    ]
    checks.add(
        "no request carried a tool outside the allowlist",
        not denied,
        f"requests={len(requests)} denied={denied[:5]}",
    )

    plan = [PlanTask(**raw) for raw in snapshot.get("plan") or []]
    result_models = [
        TaskResult(
            run_id=run_id,
            task_id=task_id,
            execution_id=str(raw.get("execution_id") or f"{run_id}:{task_id}:1"),
            producer=str(raw.get("producer") or ""),
            outcome=TaskOutcome(raw.get("outcome") or "success"),
            claims=tuple(
                Claim(
                    text=str(claim.get("text", "")),
                    kind=ClaimKind(claim.get("kind") or "inference"),
                    evidence_ids=tuple(claim.get("evidence_ids") or ()),
                )
                for claim in raw.get("claims") or []
            ),
        )
        for task_id, raw in results.items()
    ]
    final = build_report(
        run_id=run_id, results=result_models, plan=plan, evidence_by_task=evidence
    )
    checks.add(
        "the report is complete under structural acceptance",
        final.outcome.value == "complete",
        f"outcome={final.outcome.value} limitations={final.limitations[:2]}",
    )
    markdown = report_markdown(final, task_results=result_models)
    (RUNTIME_DIR / "report.md").write_text(markdown, encoding="utf-8")
    checks.add(
        "the report cites at least one retrieved passage",
        "## 证据" in markdown
        and any(evidence_id in markdown for ids in evidence.values() for evidence_id in ids),
        f"bytes={len(markdown)}",
    )


def main() -> int:
    checks = Checks()
    credentials = _load_credentials()
    if credentials is None:
        checks.add("model credentials are available", False, "no .env.local / env credentials")
    else:
        asyncio.run(_run(checks, credentials))
    return checks.report(
        RESULTS_PATH,
        scope="live product path: planner, retrieval, submission, structural acceptance",
    )


if __name__ == "__main__":
    sys.exit(main())
