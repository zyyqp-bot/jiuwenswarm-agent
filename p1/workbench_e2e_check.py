"""P2: the whole offline chain, from login to a cited Markdown report.

One real RunService spawns a fake supervisor (no team, no model, no network).
The supervisor uses the real RetrievalTool against a temporary corpus, so the
evidence id in the report is one the tool actually returned -- not a fixture
the report was handed.

Live model execution is *not* part of this check and is recorded as
``live_run: "not_run"`` in the result file.

Run: .venv/Scripts/python.exe p1/workbench_e2e_check.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.api import create_app  # noqa: E402
from app.ingest import import_corpus  # noqa: E402
from app.run_service import RunService  # noqa: E402
from app.settings import Settings  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "workbench_e2e_results.json"
FAKE = Path(__file__).resolve().parent / "fake_supervisor.py"
GOAL = "生成式 AI 服务上线前的安全评估要求"


async def _wait_terminal(service: RunService, run_id: str, *, session_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.get(run_id, session_id=session_id)
        if record.terminal:
            return record
        await asyncio.sleep(0.02)
    raise TimeoutError(f"run {run_id} did not become terminal")


def _first_event(lines) -> dict | None:
    for line in lines:
        text = line.strip()
        if text.startswith("data:"):
            return json.loads(text[5:].strip())
    return None


async def _run(checks: Checks, data_dir: Path) -> None:
    corpus_dir = data_dir / "seed"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "安全评估办法.txt").write_text(
        "生成式人工智能服务提供者应当按照本办法进行安全评估，并留存评估记录。",
        encoding="utf-8",
    )
    imported = await import_corpus(
        data_dir=data_dir, corpus="ai-governance", scope="public", source_dir=corpus_dir
    )
    checks.add(
        "the temporary corpus imports",
        imported["documents"] == 1 and not imported["failures"],
        f"imported={imported}",
    )

    settings = Settings.from_env(
        {
            "WORKBENCH_ENV": "development",
            "WORKBENCH_USERS": "judge:secret",
            "WORKBENCH_DATA_DIR": str(data_dir),
            "WORKBENCH_MAX_CONCURRENT_RUNS": "2",
            "WORKBENCH_MAX_RUNS_PER_DAY": "10",
            "WORKBENCH_ROUND_DEADLINE_SECONDS": "20",
        }
    )
    service = RunService(
        settings=settings,
        supervisor_command=[sys.executable, str(FAKE)],
        poll_seconds=0.02,
    )
    static = data_dir / "static"
    static.mkdir(exist_ok=True)
    app = create_app(settings=settings, service=service, static_dir=static)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as judge:
            anonymous = await judge.get("/api/runs")
            checks.add(
                "the run list requires a session",
                anonymous.status_code == 401,
                f"status={anonymous.status_code}",
            )

            failed = await judge.post(
                "/api/login", json={"user": "judge", "password": "wrong"}
            )
            checks.add(
                "a wrong password sets no session",
                failed.status_code == 401 and "wb_session" not in judge.cookies,
                f"status={failed.status_code}",
            )

            login = await judge.post(
                "/api/login", json={"user": "judge", "password": "secret"}
            )
            checks.add(
                "login establishes the session",
                login.status_code == 200 and login.json().get("user") == "judge",
                f"status={login.status_code}",
            )

            listed = await judge.get("/api/workspaces")
            workspaces = [
                item.get("name") for item in listed.json().get("workspaces", [])
            ]
            checks.add(
                "the workspace registry reaches the client",
                listed.status_code == 200 and "ai-governance" in workspaces,
                f"workspaces={workspaces}",
            )

            started = await judge.post(
                "/api/runs", json={"workspace": "ai-governance", "goal": GOAL}
            )
            run_id = started.json().get("run_id")
            checks.add(
                "a run starts without a plan in the request",
                started.status_code == 200 and bool(run_id),
                f"status={started.status_code} run_id={run_id}",
            )

            async with judge.stream("GET", f"/api/runs/{run_id}/events") as stream:
                lines = [line async for line in stream.aiter_lines()]
            event = _first_event(lines)
            checks.add(
                "the stream delivers a snapshot frame",
                event is not None and event.get("type") == "snapshot",
                f"event={event and event.get('type')}",
            )

            record = await _wait_terminal(service, run_id, session_id=_sid(service, run_id))
            checks.add(
                "the run reaches a terminal phase",
                record.phase == "returned" and record.terminal,
                f"phase={record.phase} terminal={record.terminal}",
            )

            detail = await judge.get(f"/api/runs/{run_id}")
            snapshot = detail.json().get("snapshot") or {}
            tasks = snapshot.get("tasks") or []
            evidence = snapshot.get("evidence") or {}
            details = snapshot.get("evidence_details") or {}
            research_ids = evidence.get("research") or []
            checks.add(
                "the task board is projected",
                len(tasks) >= 2 and all(task.get("task_id") for task in tasks),
                f"tasks={[task.get('task_id') for task in tasks]}",
            )
            checks.add(
                "the planner source is recorded",
                bool(snapshot.get("planner")),
                f"planner={snapshot.get('planner')}",
            )
            checks.add(
                "retrieval recorded evidence for the research task",
                bool(research_ids),
                f"evidence={evidence}",
            )
            checks.add(
                "the snapshot carries the passage behind the id",
                bool(research_ids)
                and bool(details.get(research_ids[0], {}).get("text"))
                and bool(details.get(research_ids[0], {}).get("source_uri")),
                f"detail={details.get(research_ids[0]) if research_ids else None}",
            )

            report = await judge.get(f"/api/runs/{run_id}/report.md")
            body = report.text
            checks.add(
                "the report renders",
                report.status_code == 200 and "## 结论" in body and "## 证据" in body,
                f"status={report.status_code} bytes={len(body)}",
            )
            checks.add(
                "the report cites the evidence the tool returned",
                bool(research_ids) and research_ids[0] in body,
                f"ids={research_ids}",
            )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
            await other.post("/api/login", json={"user": "judge", "password": "secret"})
            denied = await other.get(f"/api/runs/{run_id}")
            checks.add(
                "another session cannot read the run",
                denied.status_code == 404,
                f"status={denied.status_code}",
            )
    finally:
        await service.close()


def _sid(service: RunService, run_id: str) -> str:
    return service._records[run_id].session_id  # noqa: SLF001 - the check owns the service


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(
        RESULTS_PATH,
        scope="offline chain: login, run, stream, retrieval, report, isolation",
        live_run="not_run",
    )


if __name__ == "__main__":
    sys.exit(main())
