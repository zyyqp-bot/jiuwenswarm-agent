"""P2: one data directory belongs to one process.

The registry, the concurrency cap and SQLite are process-local, so a second
instance on the same volume would split run lists and double-admit jobs. This
check pins the lock and the lifespan that releases it:

- a fresh lock owned by another host/pid refuses startup;
- a stale lock is taken over after a crash;
- the heartbeat keeps the lock fresh;
- the lifespan closes the run service and removes the lock.

Run: .venv/Scripts/python.exe p1/instance_check.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.instance_lock import InstanceLock, managed_lifespan  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "instance_results.json"


class FakeService:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _run(checks: Checks, data_dir: Path) -> None:
    now = {"value": 1000.0}
    first = InstanceLock(
        data_dir,
        host="host-a",
        pid=111,
        clock=lambda: now["value"],
        heartbeat_seconds=0.01,
    )
    first.acquire()
    checks.add("acquiring the lock writes it", first.path.is_file(), str(first.path))

    second = InstanceLock(
        data_dir,
        host="host-b",
        pid=222,
        clock=lambda: now["value"],
    )
    refused = ""
    try:
        second.acquire()
    except RuntimeError as exc:
        refused = str(exc)
    checks.add(
        "a second live instance is refused",
        "another workbench instance" in refused,
        refused or "no error",
    )

    now["value"] += 1000.0
    takeover = InstanceLock(
        data_dir,
        host="host-b",
        pid=222,
        clock=lambda: now["value"],
        stale_seconds=45.0,
    )
    takeover.acquire()
    checks.add(
        "a stale lock is taken over after a crash",
        '"host-b"' in first.path.read_text(encoding="utf-8"),
        first.path.read_text(encoding="utf-8"),
    )

    # The heartbeat must keep the same host/pid from being treated as stale.
    now["value"] += 10.0
    takeover.heartbeat()
    fresh = InstanceLock(
        data_dir,
        host="host-c",
        pid=333,
        clock=lambda: now["value"],
        stale_seconds=45.0,
    )
    blocked = False
    try:
        fresh.acquire()
    except RuntimeError:
        blocked = True
    checks.add("the heartbeat keeps the lock fresh", blocked, f"blocked={blocked}")

    service = FakeService()
    async with managed_lifespan(takeover, service):
        checks.add(
            "the lifespan keeps the lock while serving",
            takeover.path.is_file() and not service.closed,
            f"lock={takeover.path.is_file()} closed={service.closed}",
        )
    checks.add(
        "the lifespan closes the service and releases the lock",
        service.closed and not takeover.path.is_file(),
        f"closed={service.closed} lock={takeover.path.is_file()}",
    )


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(RESULTS_PATH, scope="single-instance lock and lifespan")


if __name__ == "__main__":
    sys.exit(main())
