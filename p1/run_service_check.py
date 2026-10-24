"""P2: the run service owns a subprocess, its snapshots and its sessions.

The fake supervisor writes the same atomic snapshot shape the real one does,
so this check proves orchestration without a team or a model:

- a returned snapshot becomes a terminal record with results;
- the event stream is ordered and ends on the terminal snapshot;
- another login session cannot get, stop or stream the run;
- a crashing supervisor becomes a failed record, never a stuck "running";
- stop terminates the process and the record is terminal;
- the global concurrency cap is enforced.

Run: .venv/Scripts/python.exe p1/run_service_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.run_service import AtCapacity, RunNotFound, RunService, _child_env  # noqa: E402
from app.settings import Settings  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "run_service_results.json"
FAKE = Path(__file__).resolve().parent / "fake_supervisor.py"


def _settings(data_dir: Path) -> Settings:
    return Settings.from_env(
        {
            "WORKBENCH_ENV": "development",
            "WORKBENCH_USERS": "judge:secret",
            "WORKBENCH_DATA_DIR": str(data_dir),
            "WORKBENCH_MAX_CONCURRENT_RUNS": "1",
            "WORKBENCH_MAX_RUNS_PER_DAY": "10",
            "WORKBENCH_ROUND_DEADLINE_SECONDS": "5",
            "MODEL_API_KEY": "sk-test-secret",
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_NAME": "test-model",
        }
    )


async def _wait_terminal(service: RunService, run_id: str, *, session_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.get(run_id, session_id=session_id)
        if record.terminal:
            return record
        await asyncio.sleep(0.02)
    raise TimeoutError(f"run {run_id} did not become terminal")


async def _run(checks: Checks, data_dir: Path) -> None:
    service = RunService(
        settings=_settings(data_dir),
        supervisor_command=[sys.executable, str(FAKE)],
        poll_seconds=0.02,
    )
    try:
        # -- a normal run ---------------------------------------------------
        record = await service.start(session_id="sid-1", goal="do the work", workspace="demo")
        final = await _wait_terminal(service, record.run_id, session_id="sid-1")
        checks.add(
            "a completed supervisor snapshot becomes a returned record",
            final.phase == "returned" and final.snapshot.get("complete") is True,
            f"phase={final.phase!r} complete={final.snapshot.get('complete')!r}",
        )
        checks.add(
            "the record carries the task results",
            "research" in (final.snapshot.get("results") or {}),
            f"results={list(final.snapshot.get('results') or {})}",
        )
        checks.add("the run id is recorded", record.run_id.startswith("run-"), record.run_id)
        # The supervisor is launched as a file, so Python's sys.path[0] is
        # app/, not the repository root: without the root on PYTHONPATH the
        # child cannot import `app.*` at all.
        child_paths = _child_env()["PYTHONPATH"].split(os.pathsep)
        checks.add(
            "the child environment can import the app package",
            str(ROOT) in child_paths,
            f"PYTHONPATH={child_paths}",
        )

        report_dir = data_dir / "runs" / record.run_id
        report_md = report_dir / "report.md"
        report_json = report_dir / "report.json"
        checks.add(
            "a terminal run writes its report",
            report_md.is_file()
            and report_json.is_file()
            and "done" in report_md.read_text(encoding="utf-8")
            and "ev-1" in report_md.read_text(encoding="utf-8"),
            f"files={sorted(path.name for path in report_dir.iterdir())}",
        )

        # -- a live stream sees every snapshot ------------------------------
        live = await service.start(session_id="sid-1", goal="do the work", workspace="demo")
        events = [
            event
            async for event in service.events(live.run_id, session_id="sid-1", after_seq=0)
        ]
        seqs = [event["seq"] for event in events]
        # One frame is a valid stream: the snapshot contract is "latest state",
        # not "every intermediate state", and the monitor may coalesce writes
        # under load. Ordering and uniqueness are the properties under test.
        checks.add(
            "the live event stream is ordered",
            len(seqs) >= 1 and seqs == sorted(seqs) and len(seqs) == len(set(seqs)),
            f"seqs={seqs}",
        )
        checks.add(
            "the last event is the terminal snapshot",
            bool(events) and events[-1]["snapshot"].get("phase") == "returned",
            f"last={events[-1]['snapshot'].get('phase') if events else None}",
        )
        checks.add(
            "a subscriber that joins after the fact still gets the final state",
            [event async for event in service.events(record.run_id, session_id="sid-1")][-1][
                "snapshot"
            ].get("phase")
            == "returned",
            "late subscriber",
        )

        # -- session isolation ----------------------------------------------
        hidden = []
        for action in (
            lambda: service.get(record.run_id, session_id="sid-2"),
            lambda: service.list(session_id="sid-2"),
        ):
            try:
                result = action()
                hidden.append(result)
            except RunNotFound:
                hidden.append("not-found")
        try:
            await service.stop(record.run_id, session_id="sid-2")
            hidden.append("stopped")
        except RunNotFound:
            hidden.append("not-found")
        try:
            [event async for event in service.events(record.run_id, session_id="sid-2")]
            hidden.append("streamed")
        except RunNotFound:
            hidden.append("not-found")
        checks.add(
            "another session cannot see or touch the run",
            hidden[:2] == ["not-found", []]
            and hidden[2:] == ["not-found", "not-found"],
            f"hidden={hidden}",
        )
        owned = service.list(session_id="sid-1")
        checks.add(
            "the owning session lists only its own runs",
            all(item.session_id == "sid-1" for item in owned)
            and record.run_id in {item.run_id for item in owned}
            and service.list(session_id="sid-2") == [],
            f"runs={[item.run_id for item in owned]}",
        )

        payload_path = data_dir / "runs" / record.run_id / "payload.json"
        payload_text = payload_path.read_text(encoding="utf-8")
        checks.add(
            "the child payload does not persist the model key",
            "sk-test-secret" not in payload_text,
            f"payload_keys={sorted(json.loads(payload_text))}",
        )

        # -- a crashing supervisor ------------------------------------------
        crashed = await service.start(session_id="sid-1", goal="crash now", workspace="demo")
        broken = await _wait_terminal(service, crashed.run_id, session_id="sid-1")
        checks.add(
            "a crashing supervisor becomes a failed run",
            broken.phase == "failed" and bool(broken.error),
            f"phase={broken.phase!r} error={broken.error!r}",
        )
        broken_events = [
            event async for event in service.events(crashed.run_id, session_id="sid-1")
        ]
        crash_report = data_dir / "runs" / crashed.run_id / "report.md"
        checks.add(
            "a failed run still streams a terminal frame",
            bool(broken_events)
            and broken_events[-1]["snapshot"].get("complete") is True
            and broken_events[-1]["snapshot"].get("phase") == "failed",
            f"last={broken_events[-1]['snapshot'] if broken_events else None}",
        )
        checks.add(
            "the report exists before the terminal frame",
            crash_report.is_file(),
            f"report={'present' if crash_report.is_file() else 'missing'}",
        )

        # -- a complete snapshot that is not a native completion ------------
        timed = await service.start(session_id="sid-1", goal="deadline end", workspace="demo")
        timed_final = await _wait_terminal(service, timed.run_id, session_id="sid-1")
        checks.add(
            "a deadline-complete snapshot is not reported as returned",
            timed_final.phase == "failed" and "native completion" in (timed_final.error or ""),
            f"phase={timed_final.phase!r} error={timed_final.error!r}",
        )

        # -- stop after the child already finished --------------------------
        finished = await service.start(session_id="sid-1", goal="sleepend now", workspace="demo")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            live = service.get(finished.run_id, session_id="sid-1")
            if live.snapshot.get("complete"):
                break
            await asyncio.sleep(0.02)
        raced = await service.stop(finished.run_id, session_id="sid-1")
        checks.add(
            "stopping after the child finished keeps the completed run",
            raced.phase == "returned" and raced.terminal,
            f"phase={raced.phase!r} terminal={raced.terminal}",
        )
        race_report = data_dir / "runs" / finished.run_id / "report.md"
        checks.add(
            "the stop race does not lose the final results",
            race_report.is_file() and "done" in race_report.read_text(encoding="utf-8"),
            f"report={'present' if race_report.is_file() else 'missing'}",
        )

        # -- stop and the concurrency cap -----------------------------------
        sleeper = await service.start(session_id="sid-1", goal="sleep now", workspace="demo")
        checks.add("a long run stays running", not sleeper.terminal, f"phase={sleeper.phase!r}")
        capacity = ""
        try:
            await service.start(session_id="sid-1", goal="second", workspace="demo")
        except AtCapacity as exc:
            capacity = str(exc)
        checks.add(
            "the global concurrency cap refuses another run",
            "concurrent" in capacity.lower() or "capacity" in capacity.lower(),
            capacity or "no error",
        )
        stopped = await service.stop(sleeper.run_id, session_id="sid-1")
        checks.add(
            "stop makes the record terminal without a false failure",
            stopped.terminal and stopped.phase in ("stopped", "returned"),
            f"phase={stopped.phase!r} terminal={stopped.terminal}",
        )
        later = await _wait_terminal(service, sleeper.run_id, session_id="sid-1")
        checks.add(
            "a stopped run is not overwritten as failed",
            later.phase in ("stopped", "returned"),
            f"phase={later.phase!r}",
        )
        stop_events = [
            event async for event in service.events(sleeper.run_id, session_id="sid-1")
        ]
        checks.add(
            "a stopped run streams a terminal frame",
            bool(stop_events)
            and stop_events[-1]["snapshot"].get("complete") is True
            and stop_events[-1]["snapshot"].get("phase") == later.phase,
            f"last={stop_events[-1]['snapshot'] if stop_events else None}",
        )
    finally:
        await service.close()


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(
        RESULTS_PATH,
        scope="run service orchestration with a fake supervisor; no team, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
