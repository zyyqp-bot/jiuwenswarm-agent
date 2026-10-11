"""Shared check harness for the staged verification scripts.

Every check script reports the same way: a list of named assertions, a JSON
result file, and an exit code. That reporting lives here rather than being
copy-pasted into each script.

Usage::

    from checks.harness import Checks, call_ok, raises

    checks = Checks()
    checks.add("something holds", value == expected, f"value={value!r}")
    sys.exit(checks.report(RESULTS_PATH))
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["Checks", "call_ok", "raises"]


class Checks:
    """Collect named pass/fail assertions and render the standard report."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, check: str, ok: bool, detail: str = "") -> None:
        self.items.append({"check": check, "result": "pass" if ok else "fail", "detail": detail})

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if item["result"] != "pass"]

    def write(self, path: Path, **extra: Any) -> dict[str, Any]:
        """Write the standard JSON result document."""
        payload: dict[str, Any] = {
            "checks_passed": len(self.items) - len(self.failed),
            "checks_total": len(self.items),
            "checks": self.items,
            **extra,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def report(self, path: Path, **extra: Any) -> int:
        """Write the result file, print the report, and return an exit code."""
        self.write(path, **extra)
        for item in self.items:
            mark = "PASS" if item["result"] == "pass" else "FAIL"
            print(f"[{mark}] {item['check']}")
            if item["detail"]:
                print(f"       {item['detail']}")
        passed = len(self.items) - len(self.failed)
        print(f"\n{passed}/{len(self.items)} checks passed -> {path}")
        return 1 if self.failed else 0


def call_ok(fn: Callable[..., Any], *args: Any) -> bool:
    """Return True when ``fn(*args)`` does not raise."""
    try:
        fn(*args)
    except Exception:
        return False
    return True


def raises(fn: Callable[..., Any], *args: Any, expected: type[Exception]) -> tuple[bool, str]:
    """Return ``(raised-as-expected, message)``.

    A different exception type is reported as a failure, so a check cannot pass
    because the wrong error happened to be raised.
    """
    try:
        fn(*args)
    except expected as exc:
        return True, str(exc)
    except Exception as exc:  # a different failure is not the guard we claim
        return False, f"wrong error {type(exc).__name__}: {exc}"
    return False, "no error raised"
