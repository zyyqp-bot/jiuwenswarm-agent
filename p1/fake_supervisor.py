"""A stand-in for ``app/supervisor.py`` in offline service checks.

Behaves according to the payload goal so one service instance can exercise
every path without environment juggling:

- goal containing ``crash``  -> writes one snapshot, exits 1;
- goal containing ``sleep``  -> writes one snapshot and sleeps until killed;
- anything else              -> starting, streaming, returned (complete).

Snapshots are written atomically, exactly like the real supervisor.

Run: python p1/fake_supervisor.py --payload <path>
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _snapshot(run_id: str, phase: str, *, complete: bool, results=None, error=None) -> dict:
    return {
        "run_id": run_id,
        "phase": phase,
        "complete": complete,
        "stream_stop_reason": "team_completed" if complete else None,
        "error": error,
        "results": results or {},
        "team_markers": ["team.completed"] if complete else [],
    }


def main(argv: list[str]) -> int:
    payload_path = Path(argv[argv.index("--payload") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    run_id = payload["run_id"]
    goal = str(payload.get("goal", "")).lower()
    snapshot_path = Path(payload["snapshot_path"])
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    _write(snapshot_path, _snapshot(run_id, "starting", complete=False))
    time.sleep(0.05)

    if "crash" in goal:
        sys.exit(1)

    _write(
        snapshot_path,
        _snapshot(
            run_id,
            "streaming",
            complete=False,
            results={"research": {
                "producer": "research",
                "outcome": "success",
                "execution_id": "fake:research:1",
                "artifact_refs": ["claims:research"],
                "claims": [{"text": "done", "kind": "inference", "evidence_ids": ["ev-1"]}],
            }},
        ),
    )
    time.sleep(0.05)

    if "sleep" in goal:
        while True:
            time.sleep(0.2)

    _write(
        snapshot_path,
        _snapshot(
            run_id,
            "returned",
            complete=True,
            results={"research": {
                "producer": "research",
                "outcome": "success",
                "execution_id": "fake:research:1",
                "artifact_refs": ["claims:research"],
                "claims": [{"text": "done", "kind": "inference", "evidence_ids": ["ev-1"]}],
            }},
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
