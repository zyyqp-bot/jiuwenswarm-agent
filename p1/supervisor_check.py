"""P1: the supervisor wires a run together and writes its state as it goes.

This check uses a fake runner: it proves orchestration, snapshot writing and the
plan-invalid short circuit. It cannot prove delegation — the fake never builds
a team — so it asserts that bootstrapping did **not** happen, which is the
honest reading of that run. The real delegation nail is the next task.

Offline: no team, no model, no network.

Run: .venv/Scripts/python.exe p1/supervisor_check.py
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

import app.supervisor as supervisor_module  # noqa: E402
from app.supervisor import run_payload  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "supervisor_results.json"


class _Chunk:
    def __init__(self, chunk_type: str, payload: dict) -> None:
        self.type = chunk_type
        self.payload = payload


def _chunks() -> list[_Chunk]:
    return [
        _Chunk("llm_output", {"content": "working"}),
        _Chunk("message", {"event_type": "team.completed", "member_count": 3, "task_count": 1}),
    ]


class _FakeRunner:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def stop_agent_team(self, **kwargs) -> bool:
        return True

    def run_agent_team_streaming(self, *, agent_team, inputs, session):
        async def _iterate():
            for chunk in _chunks():
                yield chunk

        return _iterate()


class _SlowRunner(_FakeRunner):
    """Yield late enough for the reporter thread to have a write in flight."""

    def run_agent_team_streaming(self, *, agent_team, inputs, session):
        async def _iterate():
            await asyncio.sleep(0.05)
            for chunk in _chunks():
                yield chunk

        return _iterate()


def _payload(
    directory: Path,
    *,
    assignee: str = "research",
    members: list[str] | None = None,
) -> dict:
    return {
        "run_id": "run-sup-1",
        "goal": "answer the question",
        "plan": [
            {
                "task_id": "research",
                "role": "teammate",
                "instruction": "collect evidence",
                "acceptance_ids": ["a1"],
                "assignee": assignee,
            }
        ],
        "members": members or ["research", "engineering"],
        "credentials": {
            "MODEL_BASE_URL": "http://127.0.0.1:9",
            "MODEL_API_KEY": "x",
            "MODEL_NAME": "x",
        },
        "evidence_by_task": {"research": ["ev-1"]},
        "session_id": "sess-1",
        "snapshot_path": str(directory / "snapshot.json"),
        "results_path": str(directory / "results.json"),
    }


async def _run(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        fake = _FakeRunner()
        captured: dict = {}
        original_compile = supervisor_module.compile_team_spec

        def _spy(config):
            captured["config"] = config
            return original_compile(config)

        supervisor_module.compile_team_spec = _spy
        try:
            snapshot = await run_payload(_payload(directory), runner_factory=lambda: fake)
        finally:
            supervisor_module.compile_team_spec = original_compile

        written = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        checks.add("the snapshot is written to disk", written == snapshot, f"phase={written.get('phase')!r}")
        checks.add("the run returns after the completion marker", snapshot["phase"] == "returned", snapshot["phase"])
        checks.add(
            "the completion marker is recorded",
            snapshot["team_markers"] == ["team.completed"],
            f"markers={snapshot['team_markers']}",
        )
        checks.add(
            "a fake runner leaves bootstrapping unproven",
            snapshot["bootstrapped"] is False and snapshot["tool_names"] == [],
            f"bootstrapped={snapshot['bootstrapped']} tools={snapshot['tool_names']}",
        )

        config = captured.get("config") or {}
        leader = (config.get("agents") or {}).get("agent_leader") or {}
        teammate = (config.get("agents") or {}).get("agent_teammate") or {}
        checks.add(
            "the supervisor compiles the hardened template",
            any(t.get("type") == "app.submit_task_result" for t in teammate.get("tools", []))
            and any(t.get("type") == "app.submit_task_result" for t in leader.get("tools", []))
            and any(r.get("type") == "app.run_bootstrap" for r in leader.get("rails", [])),
            f"leader_tools={[t.get('type') for t in leader.get('tools', [])]} "
            f"leader_rails={[r.get('type') for r in leader.get('rails', [])]}",
        )

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        fake = _FakeRunner()
        snapshot = await run_payload(
            _payload(directory, assignee="ghost"), runner_factory=lambda: fake
        )
        checks.add(
            "an invalid plan stops before the runner starts",
            snapshot["phase"] == "invalid_plan" and fake.started is False,
            f"phase={snapshot['phase']!r} started={fake.started}",
        )

    # --- a roster the payload invents is rejected ---------------------------
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        fake = _FakeRunner()
        snapshot = await run_payload(
            _payload(directory, members=["research", "engineering", "team_leader"]),
            runner_factory=lambda: fake,
        )
        checks.add(
            "a payload roster that differs from the fixed team is rejected",
            snapshot["phase"] == "invalid_plan"
            and "members" in (snapshot["error"] or "")
            and fake.started is False,
            f"phase={snapshot['phase']!r} error={snapshot['error']!r}",
        )

    # --- a fatal bridge error reaches the run state -------------------------
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        fake = _FakeRunner()
        original_bridge = supervisor_module.RunBridge

        class _FatalBridge(original_bridge):  # type: ignore[misc, valid-type]
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.fatal_errors.append("graph submission failed: cycle")

        supervisor_module.RunBridge = _FatalBridge
        try:
            snapshot = await run_payload(_payload(directory), runner_factory=lambda: fake)
        finally:
            supervisor_module.RunBridge = original_bridge
        checks.add(
            "a fatal bridge error fails the run",
            snapshot["error"] is not None and "cycle" in snapshot["error"],
            f"error={snapshot['error']!r} phase={snapshot['phase']!r}",
        )

    # --- the reporter cannot overwrite the final snapshot -------------------
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        original_write = supervisor_module._atomic_write_json
        original_interval = supervisor_module.SNAPSHOT_INTERVAL_SECONDS
        phases: list[str] = []

        def _slow_write(path, payload):
            phases.append(str(payload.get("phase")))
            if payload.get("phase") == "streaming":
                time.sleep(0.15)
            original_write(path, payload)

        supervisor_module._atomic_write_json = _slow_write
        supervisor_module.SNAPSHOT_INTERVAL_SECONDS = 0.01
        try:
            snapshot = await run_payload(_payload(directory), runner_factory=_SlowRunner)
        finally:
            supervisor_module._atomic_write_json = original_write
            supervisor_module.SNAPSHOT_INTERVAL_SECONDS = original_interval
        checks.add(
            "the final snapshot is the last one written",
            phases[-1] == "returned" and snapshot["phase"] == "returned",
            f"last_written={phases[-1] if phases else None!r} phases={phases}",
        )


def _crash_check(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as raw:
        payload = {
            "run_id": "run-crash",
            "snapshot_path": str(Path(raw) / "snapshot.json"),
            "results_path": str(Path(raw) / "results.json"),
        }
        snapshot = supervisor_module.record_crash(
            payload, RuntimeError("preparation failed")
        )
        written = json.loads(Path(payload["snapshot_path"]).read_text(encoding="utf-8"))
        checks.add(
            "a preparation crash still writes a complete snapshot",
            written.get("complete") is True
            and written.get("phase") == "raised"
            and "RuntimeError" in written.get("error", "")
            and "preparation failed" in written.get("error", ""),
            f"snapshot={written.get('phase')} error={str(written.get('error'))[:80]}",
        )


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    _crash_check(checks)
    return checks.report(
        RESULTS_PATH,
        scope="supervisor orchestration with a fake runner; no team, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
