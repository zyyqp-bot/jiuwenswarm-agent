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
import traceback
from pathlib import Path
from typing import Any, Callable

from app.agent_template import build_workbench_config
from app.config_adapter import compile_team_spec
from app.hooks import register_bridge, register_providers, unregister_bridge
from app.plan import PlanTask, PlanValidator
from app.planner import model_from_credentials, resolve_plan
from app.request_probe import install_request_probe
from app.run_bridge import RunBridge
from app.stream_chunks import chunk_text_and_marker
from app.team_config import ASSIGNABLE_MEMBERS

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

    state: dict[str, Any] = {
        "phase": "starting",
        "stop_reason": None,
        "markers": [],
        "idle_at": None,
        "error": None,
    }
    lock = threading.Lock()

    # The plan is resolved before the bridge exists: an explicit payload plan
    # wins, otherwise the goal is planned here. Planning failures fall back to
    # the template inside resolve_plan; only a malformed explicit plan fails
    # the run, and it must fail rather than silently run a different graph.
    parse_error = ""
    plan_source = "payload"
    # Production passes model credentials through the child environment rather
    # than the payload; an explicit payload mapping still wins. Resolved before
    # the try so every later step, including config build, sees them.
    credentials = payload.get("credentials") or {
        "MODEL_BASE_URL": os.environ.get("MODEL_BASE_URL", ""),
        "MODEL_API_KEY": os.environ.get("MODEL_API_KEY", ""),
        "MODEL_NAME": os.environ.get("MODEL_NAME", ""),
    }
    try:
        model = model_from_credentials(credentials) if credentials.get("MODEL_API_KEY") else None
        outcome = await resolve_plan(payload.get("plan"), goal, model=model)
        plan = outcome.tasks
        plan_source = outcome.source
    except Exception as exc:  # noqa: BLE001 - a malformed plan is a failed run, not a crash
        plan = []
        plan_source = "invalid"
        parse_error = f"malformed plan: {type(exc).__name__}: {exc}"

    bridge = RunBridge(
        run_id=run_id,
        plan=plan,
        evidence_by_task=payload.get("evidence_by_task") or {},
    )

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
                "planner": plan_source,
                "plan": [task.model_dump(mode="json") for task in plan],
                "team_markers": list(state["markers"]),
                "error": state["error"],
            }
        _atomic_write_json(snapshot_path, snapshot)
        if results_path is not None:
            _atomic_write_json(results_path, snapshot)
        return snapshot

    if parse_error:
        with lock:
            state["error"] = parse_error
        return _snapshot("invalid_plan", complete=True)

    roster = list(ASSIGNABLE_MEMBERS)
    payload_members = [str(member) for member in payload.get("members") or []]
    if payload_members and set(payload_members) != set(roster):
        with lock:
            state["error"] = (
                f"members must be exactly {sorted(roster)} (the native scheduler "
                f"rejects any other assignee, including the leader); got {sorted(payload_members)}"
            )
        return _snapshot("invalid_plan", complete=True)

    report = PlanValidator(members=roster).validate(plan)
    if not report.valid:
        with lock:
            state["error"] = "; ".join(
                f"{issue.code.value}:{issue.task_id or '-'}:{issue.detail}" for issue in report.issues
            )
        return _snapshot("invalid_plan", complete=True)

    extra_tools: list[dict[str, Any]] = []
    corpus = payload.get("corpus")
    if isinstance(corpus, dict) and corpus.get("db_path"):
        extra_tools.append(
            {
                "type": "app.retrieve",
                "params": {
                    "run_id": run_id,
                    "db_path": corpus["db_path"],
                    "corpus": corpus["corpus"],
                    "access_scope": corpus["access_scope"],
                },
            }
        )
    config = build_workbench_config(
        credentials,
        team_name=_team_name(run_id),
        run_id=run_id,
        extra_tools=extra_tools,
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

    final_phase = "returned"
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
            final_phase = "raised"
    finally:
        # The reporter builds its snapshot under the state lock but writes
        # outside it. Joining before the final write is what stops an in-flight
        # "streaming" snapshot from landing after "returned".
        stop_reporter.set()
        reporter.join(timeout=5.0)
        unregister_bridge(run_id)

    with lock:
        if not state["error"]:
            fatal = bridge.snapshot()["fatal_errors"]
            if fatal:
                state["error"] = "; ".join(str(item) for item in fatal)

    return _snapshot(final_phase, complete=True)


def record_crash(payload: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """Persist a complete snapshot when preparation itself raises.

    Without this, an import/config failure exits the child before any snapshot
    exists, and the API can only report "exited with code 1" with no cause.
    """
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    trace = traceback.format_exc(limit=8)
    snapshot = {
        "run_id": str(payload.get("run_id") or ""),
        "phase": "raised",
        "complete": True,
        "stream_stop_reason": "raised",
        "error": f"{detail}\n{trace}"[-2000:],
        "team_markers": [],
        "results": {},
    }
    for key in ("snapshot_path", "results_path"):
        path = payload.get(key)
        if path:
            try:
                _atomic_write_json(Path(path), snapshot)
            except OSError:
                # Reported by the caller's exit code; do not mask the cause.
                pass
    return snapshot


def main(argv: list[str]) -> int:
    """CLI: ``python app/supervisor.py --payload <path>``."""
    if "--payload" not in argv:
        print("usage: supervisor.py --payload <path>", file=sys.stderr)
        return 2
    payload_path = Path(argv[argv.index("--payload") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    try:
        snapshot = asyncio.run(run_payload(payload))
    except BaseException as exc:  # noqa: BLE001 - the child must report its cause
        snapshot = record_crash(payload, exc)
    print(json.dumps({"phase": snapshot["phase"], "error": snapshot["error"]}, ensure_ascii=False))
    # A run only succeeded if it ended through the native completion marker.
    # "returned" also covers idle_grace, deadline and stream_closed, which mean
    # the board did not finish.
    ok = (
        snapshot["phase"] == "returned"
        and not snapshot["error"]
        and snapshot.get("stream_stop_reason") == "team_completed"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
