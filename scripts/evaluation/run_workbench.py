"""Run group B through the deployed workbench and save the evidence record."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def env_value(name: str) -> str:
    for path in (ROOT / "deploy" / ".env.production.local", ROOT / ".env.local"):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def request(opener, method: str, url: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="https://workbench-335317-9-1515867239.sh.run.tcloudbase.com")
    p.add_argument("--user", default="judge1")
    p.add_argument("--workspace", default="education-policy")
    p.add_argument("--goal", required=True)
    p.add_argument("--wait", type=float, default=900)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    users = env_value("WORKBENCH_USERS")
    pairs = dict(item.split(":", 1) for item in users.split(";") if ":" in item)
    password = pairs.get(args.user)
    if not password:
        print("no password for user in deploy/.env.production.local")
        return 2

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    base = args.base_url.rstrip("/")
    request(opener, "POST", base + "/api/login", {"user": args.user, "password": password})
    _, raw = request(opener, "POST", base + "/api/runs", {"workspace": args.workspace, "goal": args.goal})
    run_id = json.loads(raw)["run_id"]
    print("run_id", run_id)

    record, deadline = {}, time.monotonic() + args.wait
    while time.monotonic() < deadline:
        _, raw = request(opener, "GET", f"{base}/api/runs/{run_id}", timeout=45)
        record = json.loads(raw)
        print("poll", record.get("phase"), record.get("stop_reason"), record.get("error"), flush=True)
        if record.get("terminal"):
            break
        time.sleep(5)
    else:
        print("run did not terminate", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "group": "B",
        "run_id": run_id,
        "workspace": args.workspace,
        "goal": args.goal,
        "phase": record.get("phase"),
        "stop_reason": record.get("stop_reason"),
        "error": record.get("error"),
        "created_at": record.get("created_at"),
    }
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        status, raw = request(opener, "GET", f"{base}/api/runs/{run_id}/report.md", timeout=60)
        if status == 200:
            (args.out / "report.md").write_bytes(raw)
    except urllib.error.HTTPError as exc:
        print("report", exc.code)
    print("phase", record.get("phase"), "stop_reason", record.get("stop_reason"), "->", args.out)
    return 0 if record.get("phase") == "returned" and not record.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
