"""P2: the HTTP surface is session-scoped and the cookie is the only authority.

Uses a fake RunService, so this check proves routing, auth, isolation and the
SSE frame shape without spawning anything:

- no cookie is 401 everywhere except health and login;
- a wrong password sets no cookie;
- two sessions cannot see, stop or stream each other's runs;
- a run response never leaks a pid, credentials or the data directory;
- the SSE stream starts with a snapshot event.

Run: .venv/Scripts/python.exe p1/api_check.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.api import create_app  # noqa: E402
from app.auth import SessionCodec  # noqa: E402
from app.run_service import AtCapacity, RunNotFound, RunRecord  # noqa: E402
from app.settings import Settings  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "api_results.json"


class FakeRunService:
    """In-memory stand-in for RunService."""

    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}
        self._counter = 0

    async def start(self, *, session_id, goal, workspace, plan=None, evidence_by_task=None):
        self._counter += 1
        record = RunRecord(
            run_id=f"run-{self._counter}",
            session_id=session_id,
            goal=goal,
            workspace=workspace,
            phase="returned",
            stop_reason="team_completed",
            seq=1,
            snapshot={"phase": "returned", "complete": True, "results": {"research": {}}},
            terminal=True,
        )
        self.records[record.run_id] = record
        return record

    def get(self, run_id, *, session_id):
        record = self.records.get(run_id)
        if record is None or record.session_id != session_id:
            raise RunNotFound(run_id)
        return record

    def list(self, *, session_id):
        return [record for record in self.records.values() if record.session_id == session_id]

    async def stop(self, run_id, *, session_id):
        return self.get(run_id, session_id=session_id)

    async def events(self, run_id, *, session_id, after_seq=0):
        record = self.get(run_id, session_id=session_id)
        yield {"seq": record.seq, "type": "snapshot", "snapshot": record.snapshot}

    def admission_counts(self, session_id):
        return {"running_global": 0, "running_session": 0, "runs_today": 0}


def _settings(data_dir: Path) -> Settings:
    return Settings.from_env(
        {
            "WORKBENCH_ENV": "development",
            "WORKBENCH_USERS": "judge:secret",
            "WORKBENCH_DATA_DIR": str(data_dir),
        }
    )


async def _login(client: httpx.AsyncClient, password: str = "secret"):
    return await client.post("/api/login", json={"user": "judge", "password": password})


async def _first_sse_frame(client: httpx.AsyncClient, run_id: str) -> tuple[str, str]:
    async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        content_type = response.headers.get("content-type", "")
        collected = ""
        async for text in response.aiter_text():
            collected += text
            if "event: snapshot" in collected:
                return content_type, collected
            if len(collected) > 4096:
                break
        return content_type, collected


async def _run(checks: Checks, data_dir: Path) -> None:
    settings = _settings(data_dir)
    app = create_app(settings=settings, service=FakeRunService())
    transport = httpx.ASGITransport(app=app)

    # The container serves the SPA from the same origin. A fake dist keeps
    # this check independent of a local `npm run build`.
    static = data_dir / "static"
    (static / "assets").mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text(
        '<!doctype html><div id="root"></div>'
        '<script type="module" src="/assets/app-abc123.js"></script>',
        encoding="utf-8",
    )
    (static / "assets" / "app-abc123.js").write_text("console.log('ok')\n", encoding="utf-8")
    static_app = create_app(
        settings=settings, service=FakeRunService(), static_dir=static
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=static_app), base_url="http://test"
    ) as browser:
        index = await browser.get("/")
        checks.add(
            "the SPA is served at the root",
            index.status_code == 200 and 'id="root"' in index.text,
            f"status={index.status_code}",
        )
        match = re.search(r'src="(/assets/[^"]+\.js)"', index.text)
        asset = await browser.get(match.group(1)) if match else None
        checks.add(
            "the hashed bundle is served",
            asset is not None and asset.status_code == 200,
            f"asset={match.group(1) if match else 'missing'}",
        )
        checks.add(
            "an unknown client route falls back to the SPA",
            (await browser.get("/runs/whatever")).text == index.text,
            "history fallback",
        )
        checks.add(
            "an unknown api path is not the SPA",
            (await browser.get("/api/nope")).status_code == 404,
            "404 expected",
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
        checks.add(
            "health is public",
            (await anonymous.get("/api/health")).status_code == 200,
            "health without a cookie",
        )
        checks.add(
            "a run list without a cookie is rejected",
            (await anonymous.get("/api/runs")).status_code == 401,
            "401 expected",
        )
        bad = await _login(anonymous, "wrong")
        checks.add(
            "a wrong password is rejected without a cookie",
            bad.status_code == 401 and "set-cookie" not in bad.headers,
            f"status={bad.status_code} set-cookie={'set-cookie' in bad.headers}",
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as alice:
        login = await _login(alice)
        cookie = login.headers.get("set-cookie", "")
        checks.add(
            "a correct login sets a hardened cookie",
            login.status_code == 200 and "httponly" in cookie.lower() and "samesite=lax" in cookie.lower(),
            cookie,
        )
        workspaces = await alice.get("/api/workspaces")
        checks.add(
            "the preseeded workspace is listed",
            workspaces.status_code == 200
            and any(item["name"] == "ai-governance" for item in workspaces.json()["workspaces"]),
            workspaces.text[:120],
        )
        started = await alice.post("/api/runs", json={"goal": "check compliance", "workspace": "ai-governance"})
        run_id = started.json().get("run_id", "") if started.status_code == 200 else ""
        checks.add("starting a run returns its id", started.status_code == 200 and run_id.startswith("run-"), started.text[:120])

        detail = await alice.get(f"/api/runs/{run_id}")
        body = detail.text
        checks.add(
            "the run detail never leaks a pid, credentials or the data directory",
            detail.status_code == 200
            and "process_pid" not in body
            and "api_key" not in body.lower()
            and str(data_dir) not in body,
            body[:200],
        )

        content_type, frame = await _first_sse_frame(alice, run_id)
        checks.add(
            "the event stream starts with a snapshot frame",
            "text/event-stream" in content_type
            and "event: snapshot" in frame
            and '"seq"' in frame,
            f"content_type={content_type} frame={frame[:120]}",
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as bob:
            await _login(bob)
            checks.add(
                "another session cannot read the run",
                (await bob.get(f"/api/runs/{run_id}")).status_code == 404,
                "404 expected",
            )
            checks.add(
                "another session cannot stop the run",
                (await bob.post(f"/api/runs/{run_id}/stop")).status_code == 404,
                "404 expected",
            )
            checks.add(
                "another session cannot stream the run",
                (await bob.get(f"/api/runs/{run_id}/events")).status_code == 404,
                "404 expected",
            )
            checks.add(
                "another session's run list is empty",
                (await bob.get("/api/runs")).json().get("runs") == [],
                (await bob.get("/api/runs")).text[:120],
            )

    capacity_app = create_app(settings=settings, service=_CapacityService())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=capacity_app), base_url="http://test"
    ) as carol:
        await _login(carol)
        full = await carol.post("/api/runs", json={"goal": "x", "workspace": "ai-governance"})
        checks.add(
            "capacity is reported as 503",
            full.status_code == 503,
            full.text[:120],
        )
        unknown = await carol.post("/api/runs", json={"goal": "x", "workspace": "nope"})
        checks.add(
            "an unknown workspace is a 400",
            unknown.status_code == 400,
            unknown.text[:120],
        )

    session_cap_settings = Settings.from_env(
        {
            "WORKBENCH_ENV": "development",
            "WORKBENCH_USERS": "judge:secret",
            "WORKBENCH_DATA_DIR": str(data_dir),
            "WORKBENCH_MAX_CONCURRENT_PER_SESSION": "1",
        }
    )
    session_app = create_app(settings=session_cap_settings, service=_SessionCapService())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=session_app), base_url="http://test"
    ) as dave:
        await _login(dave)
        refused = await dave.post("/api/runs", json={"goal": "x", "workspace": "ai-governance"})
        checks.add(
            "a full session is a 429",
            refused.status_code == 429 and refused.json().get("detail") == "session_capacity",
            refused.text[:120],
        )


class _CapacityService(FakeRunService):
    async def start(self, **kwargs):
        raise AtCapacity("concurrent run capacity reached")


class _SessionCapService(FakeRunService):
    def admission_counts(self, session_id):
        return {"running_global": 0, "running_session": 1, "runs_today": 0}


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(
        RESULTS_PATH,
        scope="API routing and auth with a fake run service; no subprocess, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
