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


def _payload(directory: Path, *, assignee: str = "research") -> dict:
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
        "members": ["research", "engineering", "team_leader"],
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


def main() -> int:
    checks = Checks()
    asyncio.run(_run(checks))
    return checks.report(
        RESULTS_PATH,
        scope="supervisor orchestration with a fake runner; no team, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
