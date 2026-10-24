"""One run supervisor: build the team, stream the round, persist as it goes.

The supervisor owns everything process-local about a run: the bridge, the
provider registrations, the request probe, the snapshot writer. It is designed
to be the child process of an API request (Plan 2); here it is also callable
in-process for the delegation nail.

Two stream facts shape this file (measured 2026-10-24):

- text lives in ``chunk.payload`` (``llm_output`` / ``answer``), not on
  ``chunk.content``;
- an empty task board never satisfies ``is_team_completed``, so after
  ``team.idle`` a task-less round would stay open forever. The supervisor
  therefore stops after an idle grace period and records why.

Snapshots are written from a daemon thread so a kill at any moment leaves the
last good snapshot behind.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.agent_template import build_workbench_config
from app.config_adapter import compile_team_spec
from app.hooks import register_bridge, register_providers, unregister_bridge
from app.plan import PlanTask, PlanValidator
from app.request_probe import install_request_probe
from app.run_bridge import RunBridge
from app.stream_chunks import chunk_text_and_marker

SNAPSHOT_INTERVAL_SECONDS = 0.5
IDLE_GRACE_SECONDS = 12.0
DEADLINE_SECONDS = float(os.environ.get("WORKBENCH_ROUND_DEADLINE_SECONDS", "300"))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _team_name(run_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_id)
    return f"wb_{safe}"[:64]


def _default_runner() -> Any:
    from openjiuwen.core.runner.runner import Runner

    return Runner


async def run_payload(
    payload: dict[str, Any],
    *,
    runner_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run one payload to completion and return (and persist) the final snapshot."""
    run_id = str(payload["run_id"])
    goal = str(payload.get("goal", ""))
    session_id = str(payload.get("session_id") or f"session-{run_id}")
    snapshot_path = Path(payload["snapshot_path"])
    results_path = Path(payload["results_path"]) if payload.get("results_path") else None

    plan = [PlanTask(**raw) for raw in payload.get("plan") or []]
    members = [str(member) for member in payload.get("members") or []]
    bridge = RunBridge(
        run_id=run_id,
        plan=plan,
        evidence_by_task=payload.get("evidence_by_task") or {},
    )

    state: dict[str, Any] = {
        "phase": "starting",
        "stop_reason": None,
        "markers": [],
        "idle_at": None,
        "error": None,
    }
    lock = threading.Lock()

    def _snapshot(phase: str, *, complete: bool) -> dict[str, Any]:
        with lock:
            state["phase"] = phase
            base = bridge.snapshot()
            snapshot = {
                **base,
                "run_id": run_id,
                "goal": goal,
                "session_id": session_id,
                "phase": phase,
                "complete": complete,
                "stream_stop_reason": state["stop_reason"],
                "team_markers": list(state["markers"]),
                "error": state["error"],
                "results_path": str(results_path) if results_path else None,
            }
        _atomic_write_json(snapshot_path, snapshot)
        if results_path is not None:
            _atomic_write_json(results_path, snapshot)
        return snapshot

    report = PlanValidator(members=members).validate(plan)
    if not report.valid:
        with lock:
            state["error"] = "; ".join(
                f"{issue.code.value}:{issue.task_id or '-'}:{issue.detail}" for issue in report.issues
            )
        return _snapshot("invalid_plan", complete=True)

    config = build_workbench_config(
        payload["credentials"], team_name=_team_name(run_id), run_id=run_id
    )
    spec = compile_team_spec(config)

    register_providers()
    register_bridge(run_id, bridge)
    install_request_probe(bridge.record_tool_names)

    stop_reporter = threading.Event()

    def _reporter() -> None:
        while not stop_reporter.wait(SNAPSHOT_INTERVAL_SECONDS):
            try:
                _snapshot("streaming", complete=False)
            except Exception:  # noqa: BLE001 - a snapshot failure must not kill the run
                pass

    reporter = threading.Thread(target=_reporter, daemon=True, name=f"snapshot-{run_id}")
    reporter.start()
    start_time = time.monotonic()

    try:
        try:
            runner = (runner_factory or _default_runner)()
            await runner.start()
            queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
            producer_error: list[BaseException] = []

            async def _producer() -> None:
                try:
                    async for chunk in runner.run_agent_team_streaming(
                        agent_team=spec, inputs={"query": goal}, session=session_id
                    ):
                        await queue.put(chunk)
                except BaseException as exc:  # noqa: BLE001 - reported on the main path
                    producer_error.append(exc)
                finally:
                    await queue.put(None)

            task = asyncio.create_task(_producer())
            stop_reason: str | None = None
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        with lock:
                            idle_at = state["idle_at"]
                        if idle_at is not None and now - idle_at >= IDLE_GRACE_SECONDS:
                            stop_reason = "idle_grace"
                            break
                        if now - start_time >= DEADLINE_SECONDS:
                            stop_reason = "deadline"
                            break
                        continue

                    if chunk is None:
                        stop_reason = "stream_closed"
                        break

                    _, marker = chunk_text_and_marker(chunk)
                    if marker:
                        with lock:
                            state["markers"].append(marker)
                            if marker == "team.idle":
                                state["idle_at"] = time.monotonic()
                        if marker == "team.completed":
                            stop_reason = "team_completed"
                            break
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException:  # noqa: BLE001 - cancellation
                        pass

            with lock:
                state["stop_reason"] = stop_reason
                if producer_error:
                    state["error"] = (
                        f"stream producer: {type(producer_error[0]).__name__}: {producer_error[0]}"
                    )

            if stop_reason in ("idle_grace", "deadline"):
                stopper = getattr(runner, "stop_agent_team", None)
                if callable(stopper):
                    try:
                        await stopper(team_name=_team_name(run_id), session_id=session_id)
                    except Exception:  # noqa: BLE001 - best effort
                        pass
            else:
                try:
                    await runner.stop()
                except Exception:  # noqa: BLE001 - best effort
                    pass
        except BaseException as exc:  # noqa: BLE001 - SystemExit included
            with lock:
                state["error"] = f"{type(exc).__name__}: {exc}"
            return _snapshot("raised", complete=True)
    finally:
        stop_reporter.set()
        unregister_bridge(run_id)

    return _snapshot("returned", complete=True)


def main(argv: list[str]) -> int:
    """CLI: ``python app/supervisor.py --payload <path>``."""
    if "--payload" not in argv:
        print("usage: supervisor.py --payload <path>", file=sys.stderr)
        return 2
    payload_path = Path(argv[argv.index("--payload") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    snapshot = asyncio.run(run_payload(payload))
    print(json.dumps({"phase": snapshot["phase"], "error": snapshot["error"]}, ensure_ascii=False))
    return 0 if snapshot["phase"] in ("returned", "streaming") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
