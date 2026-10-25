"""Post-deploy smoke check: health, login, a real run, the SSE stream, terminal.

Run from your machine against the public URL:

    .venv/Scripts/python.exe scripts/smoke_check.py \
        --base-url https://your-service.example.com --user judge1 --password '…'

The password and the session cookie are never printed. The check starts one
real run (which costs one model call) and waits for it to terminate.

Exit codes: 0 all stages passed; 1 a stage failed; 2 a transport error.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["main", "run_checks"]


def _opener(jar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _request(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    *,
    body: dict | None = None,
    timeout: float = 20.0,
) -> tuple[int, bytes]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _stage(name: str) -> None:
    print(f"[stage] {name}")


def _diagnose(record: dict) -> None:
    """Print the team state that explains a failed run (no secrets)."""
    snapshot = record.get("snapshot") or {}
    for key in ("team_markers", "bootstrapped", "tool_names", "fatal_errors"):
        print(f"   {key}={snapshot.get(key)!r}")
    plan = snapshot.get("plan") or record.get("plan") or []
    print(f"   plan={[(task.get('task_id'), task.get('assignee')) for task in plan]!r}")
    results = snapshot.get("results") or record.get("results") or {}
    print(
        "   results="
        + repr(
            {
                key: (value.get("outcome"), len(value.get("claims") or []))
                for key, value in results.items()
            }
        )
    )


def run_checks(
    *,
    base_url: str,
    user: str,
    password: str,
    goal: str,
    wait_seconds: float,
    workspace: str = "ai-governance",
    save_report: str | None = None,
) -> bool:
    base = base_url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = _opener(jar)

    _stage("health")
    status, raw = _request(opener, "GET", f"{base}/api/health")
    if status != 200:
        print(f"health returned {status}")
        return False
    health = json.loads(raw.decode("utf-8"))
    if not health.get("writable"):
        print("health reports the data directory is not writable")
        return False
    print(f"   status={health.get('status')} writable={health.get('writable')}")

    _stage("login")
    status, raw = _request(
        opener, "POST", f"{base}/api/login", body={"user": user, "password": password}
    )
    if status != 200:
        print(f"login returned {status}")
        return False
    print(f"   user={json.loads(raw.decode('utf-8')).get('user')}")

    _stage("start run")
    status, raw = _request(
        opener,
        "POST",
        f"{base}/api/runs",
        body={"workspace": workspace, "goal": goal},
    )
    if status != 200:
        print(f"start returned {status}")
        return False
    run_id = json.loads(raw.decode("utf-8"))["run_id"]
    print(f"   run_id={run_id}")

    _stage("first SSE frame")
    stream_url = f"{base}/api/runs/{run_id}/events"
    request = urllib.request.Request(stream_url, headers={"Accept": "text/event-stream"})
    try:
        with opener.open(request, timeout=wait_seconds) as stream:
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                line = stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("data:"):
                    print("   received a snapshot frame")
                    break
            else:
                print("no snapshot frame within the wait window")
                return False
    except urllib.error.HTTPError as exc:
        print(f"stream returned {exc.code}")
        return False
    except TimeoutError:
        print("stream timed out before the first frame")
        return False

    _stage("terminal state")
    terminal = False
    deadline = time.monotonic() + wait_seconds
    record: dict = {}
    while time.monotonic() < deadline:
        status, raw = _request(opener, "GET", f"{base}/api/runs/{run_id}")
        if status != 200:
            print(f"poll returned {status}")
            return False
        record = json.loads(raw.decode("utf-8"))
        terminal = bool(record.get("terminal"))
        if terminal:
            break
        time.sleep(2.0)

    if not terminal:
        print(f"run did not terminate within {wait_seconds:.0f}s")
        return False
    print(f"   phase={record.get('phase')} stop_reason={record.get('stop_reason')}")
    if record.get("phase") != "returned" or record.get("error"):
        # A terminal failed or stopped run is not a successful smoke check.
        print(f"run did not succeed: {record.get('error') or record.get('stop_reason')}")
        _diagnose(record)
        return False

    _stage("report")
    status, raw = _request(opener, "GET", f"{base}/api/runs/{run_id}/report.md")
    if status != 200:
        print(f"report returned {status}")
        return False
    print(f"   report_bytes={len(raw)}")
    if save_report:
        Path(save_report).write_bytes(raw)
        print(f"   report_saved={save_report}")
    return True


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--goal",
        default="用三句话说明生成式 AI 客服上线前最关键的合规动作。",
        help="goal sent to the real run",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=240.0,
        help="seconds to wait for the first frame and for termination",
    )
    parser.add_argument(
        "--workspace",
        default="ai-governance",
        help="workspace to run in (ai-governance or education-policy)",
    )
    parser.add_argument(
        "--save-report",
        default=None,
        help="write the fetched report.md to this path for inspection",
    )
    args = parser.parse_args(argv)

    try:
        ok = run_checks(
            base_url=args.base_url,
            user=args.user,
            password=args.password,
            goal=args.goal,
            wait_seconds=args.wait,
            workspace=args.workspace,
            save_report=args.save_report,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"transport error: {type(exc).__name__}: {exc}")
        return 2
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
