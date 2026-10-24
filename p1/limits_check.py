"""P2: admission limits are explicit and mapped to the right HTTP status.

A public link that can be scripted must refuse work before it starts, with a
reason the operator can read. This check pins the three limits and their
status codes.

Run: .venv/Scripts/python.exe p1/limits_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.limits import LimitExceeded, Limits  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "limits_results.json"


def _raised(limits: Limits, **counts) -> LimitExceeded | None:
    try:
        limits.check_start(**counts)
    except LimitExceeded as exc:
        return exc
    return None


def main() -> int:
    checks = Checks()
    limits = Limits(max_concurrent_global=2, max_concurrent_per_session=1, max_runs_per_day=3)

    allowed = _raised(limits, running_global=1, running_session=0, runs_today=2)
    checks.add("a request under every limit is admitted", allowed is None, "admitted")

    global_full = _raised(limits, running_global=2, running_session=0, runs_today=0)
    checks.add(
        "the global cap is a 503",
        global_full is not None
        and global_full.code == "global_capacity"
        and global_full.status_code == 503,
        f"error={global_full.code if global_full else None}",
    )
    checks.add(
        "the global message names the limit and the current value",
        global_full is not None and "2" in str(global_full),
        str(global_full) if global_full else "no error",
    )

    session_full = _raised(limits, running_global=1, running_session=1, runs_today=0)
    checks.add(
        "the session cap is a 429",
        session_full is not None
        and session_full.code == "session_capacity"
        and session_full.status_code == 429,
        f"error={session_full.code if session_full else None}",
    )

    daily_full = _raised(limits, running_global=0, running_session=0, runs_today=3)
    checks.add(
        "the daily quota is a 429",
        daily_full is not None
        and daily_full.code == "daily_quota"
        and daily_full.status_code == 429,
        f"error={daily_full.code if daily_full else None}",
    )

    checks.add(
        "the daily message says quota is exhausted",
        daily_full is not None and "quota" in str(daily_full).lower(),
        str(daily_full) if daily_full else "no error",
    )

    invalid = ""
    try:
        Limits(max_concurrent_global=0, max_concurrent_per_session=1, max_runs_per_day=1)
    except ValueError as exc:
        invalid = str(exc)
    checks.add("a non-positive limit is refused at construction", bool(invalid), invalid or "no error")

    return checks.report(
        RESULTS_PATH,
        scope="admission limit arithmetic only; no service, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
