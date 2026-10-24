"""P1: one real round against a real model, with assertions that survive it.

Everything up to here verified modules, contracts, and the tool surface of a
*build*. This is the first check that starts a runtime and lets the leader
actually work.

It is deliberately the smallest round: one query, one run, and the question is
whether the chain works at all -- whether the leader plans, whether a member
receives a task, whether anything comes back. It is not section 12's scenario 1;
that needs a research -> engineering plan and would multiply the cost and the
number of ways to fail at once.

Why a parent process
--------------------
The first real round ended without running ``atexit`` and without reaching the
statement after the async loop. Section 13 recorded that as ``os._exit()``, but
that was inferred from the symptom -- neither upstream tree contains an
``os._exit`` on this path. A hard crash, an external kill, or a stream that
never returns all produce the same symptom, and they mean different things.

So this check does not assume the mechanism, it measures it:

* the child runs the round and a daemon thread flushes a JSON snapshot to disk
  every ``SNAPSHOT_INTERVAL_SECONDS`` (state must be persisted *during* the
  stream, since there may be no "after");
* the child registers an ``atexit`` marker and, if it ever returns, writes a
  ``returned`` snapshot;
* the parent waits for the child, then classifies the termination from the exit
  code, the marker, and the last snapshot -- and writes the final report, which
  is now reachable regardless of how the child ended.

What the assertions use
-----------------------
The framework logs through the standard ``logging`` module (``DefaultLogger``
wraps ``logging.getLogger(log_type)``), so a record factory captures every
record regardless of handler/propagation details. That turns the facts section
13 confirmed by eye into assertions: the leader issued a model request with a
tool count, the security rail saw the input, a checkpoint was saved, an outer
loop iteration completed, the team went idle, and the provider reported usage.

The tool count is compared against the team tool surface materialised from the
same built team, not against the literal 29 -- the relation is what section 13
found, the number is an observation.

Credentials come from the environment or from ``.env.local`` (git-ignored).
They are never printed, and the check reports only what the run did.

Costs money and calls a real provider. Run it deliberately. ``--dry-run`` stops
before any model call and needs no credentials; it exercises the same spec,
build, tool-surface and reporting machinery.

Run:
    .venv/Scripts/python.exe p1/live_round_check.py            # real round
    .venv/Scripts/python.exe p1/live_round_check.py --dry-run  # no model call
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))
sys.path.insert(0, str(HERE))

from checks.harness import Checks  # noqa: E402

RESULTS_PATH = HERE / "live_round_results.json"
DRY_RUN_RESULTS_PATH = HERE / "live_round_dry_run_results.json"
RUNTIME_DIR = HERE / ".live_round"
ENV_FILE = ROOT / ".env.local"

QUERY = "用一句话说明：为什么在开始实现前要先核对源码版本？"

CREDENTIAL_KEYS = ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME")

CHILD_FLAG = "--child"
DRY_RUN_FLAG = "--dry-run"
CHILD_RESULTS_ENV = "LIVE_ROUND_CHILD_RESULTS"
ATEXIT_MARKER_ENV = "LIVE_ROUND_ATEXIT_MARKER"
DRY_RUN_ENV = "LIVE_ROUND_DRY_RUN"

MAX_ROUND_SECONDS = 600.0
SNAPSHOT_INTERVAL_SECONDS = 0.5
STREAM_TICK_SECONDS = 1.0
IDLE_GRACE_SECONDS = 12.0
ROUND_DEADLINE_SECONDS = float(os.environ.get("LIVE_ROUND_DEADLINE_SECONDS", "300"))
MAX_CAPTURED_RECORDS = 20_000

NOT_RUN_ALWAYS = [
    "scenario 3 (embedding outage -> keyword_only): no embedding provider configured",
    "scenario 7 (long tool chain and compression recall): no framework context rail wired",
    "scenario 10 (team memory single writer): team and member memory not wired",
    "scenario 2 runtime part (roster held during execution): needs members to execute tasks",
]


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------
def _load_credentials() -> dict[str, str] | None:
    """Read model credentials from the environment, falling back to .env.local."""
    values = {key: os.environ.get(key, "").strip() for key in CREDENTIAL_KEYS}
    if all(values.values()):
        return values

    if not ENV_FILE.is_file():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in CREDENTIAL_KEYS and not values.get(name):
            values[name] = value.strip().strip('"').strip("'")
    return values if all(values.values()) else None


def _dry_run_credentials() -> dict[str, str]:
    """Placeholders: compiling and building a spec never contacts the endpoint."""
    return {
        "MODEL_BASE_URL": "http://127.0.0.1:9/dry-run",
        "MODEL_API_KEY": "dry-run",
        "MODEL_NAME": "dry-run-model",
    }


# --------------------------------------------------------------------------
# log capture
# --------------------------------------------------------------------------
class _LogCapture:
    """Capture framework log records in memory via the global record factory.

    A handler would depend on propagation and handler ordering, and would be
    blind to loggers created after it was attached. The record factory sees
    every record the moment it is built, for every logger, exactly once.

    Records below the emitting logger's own level are dropped: the framework
    did not emit them, and a debug line that never reached a handler must not
    become evidence.
    """

    def __init__(self) -> None:
        self._records: deque[dict[str, str]] = deque(maxlen=MAX_CAPTURED_RECORDS)
        self._lock = threading.Lock()
        self._previous = logging.getLogRecordFactory()
        self.installed = False

    def install(self) -> None:
        previous = self._previous

        def factory(*args, **kwargs):
            record = previous(*args, **kwargs)
            try:
                if logging.getLogger(record.name).isEnabledFor(record.levelno):
                    message = record.getMessage()
                    with self._lock:
                        self._records.append(
                            {
                                "logger": record.name,
                                "level": record.levelname,
                                "message": message,
                            }
                        )
            except Exception:  # capture must never break logging
                pass
            return record

        logging.setLogRecordFactory(factory)
        self.installed = True

    def uninstall(self) -> None:
        if self.installed:
            logging.setLogRecordFactory(self._previous)
            self.installed = False

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._records)


# --------------------------------------------------------------------------
# log-derived assertions
# --------------------------------------------------------------------------
_REQUEST_RE = re.compile(r"\[LLM\] >>> request: msg_count=(\d+), tool_count=(\d+)")
_USAGE_RE = re.compile(r"tokens=\{input=(\d+), output=(\d+)\}")
_OUTER_LOOP_RE = re.compile(r"\[OuterLoop\] iteration=(\d+)\b.*completed")
_TOOL_CALL_RE = re.compile(r"\[LLM\]\s+tool_call:\s*([A-Za-z0-9_.\-]+)")


def _analyse_logs(records: list[dict[str, str]], team_tool_count: int | None) -> dict:
    """Turn captured log records into measurements, checks and usage totals."""
    messages = [record["message"] for record in records]

    requests: list[tuple[int, int]] = []
    for message in messages:
        match = _REQUEST_RE.search(message)
        if match:
            requests.append((int(match.group(1)), int(match.group(2))))

    usage_input = 0
    usage_output = 0
    usage_calls = 0
    for message in messages:
        if "[LLM] <<< response" not in message:
            continue
        match = _USAGE_RE.search(message)
        if match:
            usage_calls += 1
            usage_input += int(match.group(1))
            usage_output += int(match.group(2))

    tool_calls = []
    for message in messages:
        match = _TOOL_CALL_RE.search(message)
        if match:
            tool_calls.append(match.group(1))

    checks: list[tuple[str, bool, str]] = []
    checks.append(
        (
            "the leader issued a model request",
            bool(requests),
            f"{len(requests)} request(s); tool_count={[count for _, count in requests]}",
        )
    )

    if team_tool_count:
        largest = max((count for _, count in requests), default=0)
        checks.append(
            (
                "the request carried more tools than the team tool surface",
                largest > team_tool_count,
                f"request tool_count={largest}, materialised team tools={team_tool_count}; "
                "the difference is harness-supplied capability",
            )
        )

    checks.append(
        (
            "the provider reported token usage",
            usage_calls > 0,
            f"{usage_calls} response(s) with usage; input={usage_input}, output={usage_output}",
        )
    )
    security_hits = [
        message
        for message in messages
        if "BaseSecurityRail" in message and "get_user_input" in message
    ]
    checks.append(
        (
            "the security rail handled the input",
            bool(security_hits),
            security_hits[-1][:160] if security_hits else "no BaseSecurityRail.get_user_input record",
        )
    )
    checkpoints = [
        message
        for message in messages
        if "checkpoint" in message.lower()
        and ("succeed" in message.lower() or "success" in message.lower())
    ]
    checks.append(
        (
            "a checkpoint was saved",
            bool(checkpoints),
            checkpoints[-1][:160] if checkpoints else "no successful checkpoint record",
        )
    )
    iterations = []
    for message in messages:
        match = _OUTER_LOOP_RE.search(message)
        if match:
            iterations.append(int(match.group(1)))
    checks.append(
        (
            "the task loop completed an iteration",
            bool(iterations),
            f"iterations={iterations}",
        )
    )
    idle_hits = [message for message in messages if "team went idle" in message]
    checks.append(
        (
            "the team went idle",
            bool(idle_hits),
            idle_hits[-1][:160] if idle_hits else "no team-idle record",
        )
    )

    observed_tool_calls = {
        "tool_calls": sorted(set(tool_calls)),
        "spawn_calls": sorted({name for name in tool_calls if name.startswith("spawn_")}),
    }

    # Bound each retained message: a provider traceback can be tens of KB and
    # the report is meant to be read, not to be a log archive.
    errors = [
        {
            "logger": record["logger"],
            "level": record["level"],
            "message": record["message"][:500],
        }
        for record in records
        if record["level"] in ("ERROR", "CRITICAL")
    ]

    return {
        "checks": checks,
        "request_count": len(requests),
        "requests": [{"msg_count": msgs, "tool_count": count} for msgs, count in requests[-20:]],
        "usage": {
            "calls_with_usage": usage_calls,
            "input_tokens": usage_input,
            "output_tokens": usage_output,
        },
        "tool_calls": observed_tool_calls,
        "framework_errors": errors[-5:],
        "error_count": len(errors),
    }


# --------------------------------------------------------------------------
# round state and snapshots
# --------------------------------------------------------------------------
def _chunk_field(chunk: object, name: str):
    if isinstance(chunk, dict):
        return chunk.get(name)
    return getattr(chunk, name, None)


def _chunk_text_and_marker(chunk: object) -> tuple[str, str | None]:
    """Return the text a chunk carries and, for team markers, its event type.

    The framework puts content in ``payload`` (``OutputSchema`` is
    ``type/index/payload``), not on ``content``/``text``. ``llm_output``
    carries incremental content; ``answer`` carries the finalized result and
    may repeat what the llm_output chunks already said, so the caller keeps
    them apart.
    """
    payload = _chunk_field(chunk, "payload")
    if not isinstance(payload, dict):
        return "", None
    event_type = payload.get("event_type")
    if isinstance(event_type, str) and event_type.startswith("team."):
        return "", event_type
    chunk_type = _chunk_field(chunk, "type")
    if chunk_type == "llm_output":
        content = payload.get("content")
        return (content, None) if isinstance(content, str) else ("", None)
    if chunk_type == "answer":
        content = payload.get("content") or payload.get("output")
        return (content, None) if isinstance(content, str) else ("", None)
    return "", None


class _RoundState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.events = 0
        self.chunks_with_text = 0
        self.llm_parts: list[str] = []
        self.answer_text: str = ""
        self.chunk_types: dict[str, int] = {}
        self.markers: list[str] = []
        self.saw_idle = False
        self.saw_completed = False
        self.idle_at: float | None = None
        self.stream_entered = False
        self.stream_ended = False
        self.stop_reason: str | None = None
        self.midstream_writes = 0
        self.base_checks: list[dict] = []
        self.team_tool_count: int | None = None
        self.team_tool_names: list[str] = []
        self.error: str | None = None


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write via a unique temp file and replace, so a kill mid-write keeps the last good snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _build_payload(
    state: _RoundState,
    capture: _LogCapture,
    *,
    phase: str,
    complete: bool,
    dry_run: bool,
    not_run: list[str],
) -> dict:
    with state.lock:
        events = state.events
        chunks_with_text = state.chunks_with_text
        answer = (state.answer_text or "".join(state.llm_parts)).strip()
        chunk_types = dict(state.chunk_types)
        markers = list(state.markers)
        saw_completed = state.saw_completed
        stop_reason = state.stop_reason
        stream_entered = state.stream_entered
        stream_ended = state.stream_ended
        snapshot_index = state.midstream_writes
        error = state.error
        elapsed = time.monotonic() - state.started
        base_checks = list(state.base_checks)
        team_tool_count = state.team_tool_count
        team_tool_names = list(state.team_tool_names)

    checks = Checks()
    checks.items.extend(base_checks)

    blocked = not dry_run and phase == "no_credentials"
    if dry_run:
        checks.add(
            "the dry run stopped before any model call",
            True,
            "spec, build and tool surface only; the endpoint is never contacted",
        )
    elif not blocked:
        checks.add(
            "the round produced output",
            bool(answer),
            f"{events} chunks, {chunks_with_text} with text, {len(answer)} chars in {elapsed:.0f}s",
        )
        checks.add(
            "the output is not an error message",
            bool(answer) and not answer.lower().startswith(("error", "traceback")),
            answer[:200].replace("\n", " ") if answer else "(no text yet)",
        )

    analysis = _analyse_logs(capture.snapshot(), team_tool_count)
    if not dry_run and not blocked:
        # In a dry run no model call happens, so asserting on request/rail/loop
        # records would report "fail" for something that was never attempted.
        checks.items.extend(
            {"check": name, "result": "pass" if ok else "fail", "detail": detail}
            for name, ok, detail in analysis["checks"]
        )

    observed_not_run = list(not_run)
    if blocked:
        observed_not_run.append(
            "the round never started: no model credentials, so no request, rail, "
            "checkpoint or idle record could exist"
        )
    if not dry_run and not blocked:
        if saw_completed:
            checks.add(
                "the team emitted a completion marker",
                True,
                f"markers={markers[-5:]}, stop_reason={stop_reason!r}",
            )
        elif stop_reason == "idle_grace":
            # Native completion requires at least one task; a plain query
            # creates none, so the stream stays open by design.
            observed_not_run.append(
                "team completion marker: is_team_completed requires at least one task "
                "and every task terminal; this round created none, so the stream was "
                "stopped after the idle grace period"
            )
        else:
            checks.add(
                "the team emitted a completion marker",
                False,
                f"markers={markers[-5:]}, stop_reason={stop_reason!r}",
            )
    spawn_calls = analysis["tool_calls"]["spawn_calls"]
    all_calls = analysis["tool_calls"]["tool_calls"]
    if all_calls:
        checks.add(
            "no spawn tool was invoked during the round",
            not spawn_calls,
            f"tool calls={all_calls}; spawn={spawn_calls}",
        )
    elif not dry_run:
        observed_not_run.append(
            "no tool call was observed in this round, so 'no spawn tool was invoked' has no evidence"
        )

    passed = sum(1 for item in checks.items if item["result"] == "pass")
    return {
        "checks_passed": passed,
        "checks_total": len(checks.items),
        "checks": checks.items,
        "scope": (
            "dry run: spec, build and tool surface; no model call, no network"
            if dry_run
            else "one real round against a real provider; costs money and is non-deterministic"
        ),
        "query": QUERY,
        "phase": phase,
        "complete": complete,
        "dry_run": dry_run,
        "events": events,
        "chunks_with_text": chunks_with_text,
        "chunk_types": chunk_types,
        "team_markers": markers,
        "stream_stop_reason": stop_reason,
        "answer_chars": len(answer),
        "answer_preview": answer[:200],
        "elapsed_s": round(elapsed, 1),
        "stream_entered": stream_entered,
        "stream_ended": stream_ended,
        "midstream_writes": snapshot_index,
        "model_request_count": analysis["request_count"],
        "model_requests": analysis["requests"],
        "usage": analysis["usage"],
        "tool_calls": analysis["tool_calls"],
        "team_tools": {
            "count": team_tool_count,
            "names": team_tool_names,
            "spawn": [name for name in team_tool_names if name.startswith("spawn_")],
        },
        "framework_error_count": analysis["error_count"],
        "framework_errors": analysis["framework_errors"],
        "error": error,
        "not_run": observed_not_run,
    }


def _write_snapshot(
    state: _RoundState,
    capture: _LogCapture,
    path: Path,
    *,
    phase: str,
    complete: bool,
    dry_run: bool,
    not_run: list[str],
) -> dict:
    payload = _build_payload(
        state, capture, phase=phase, complete=complete, dry_run=dry_run, not_run=not_run
    )
    if not complete:
        with state.lock:
            state.midstream_writes += 1
            payload["midstream_writes"] = state.midstream_writes
    _atomic_write_json(path, payload)
    return payload


def _start_reporter(
    state: _RoundState,
    capture: _LogCapture,
    path: Path,
    *,
    dry_run: bool,
    not_run: list[str],
) -> threading.Event:
    """Flush the snapshot on a timer: an os._exit leaves no callback to hang one on."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(SNAPSHOT_INTERVAL_SECONDS):
            try:
                _write_snapshot(
                    state,
                    capture,
                    path,
                    phase="streaming",
                    complete=False,
                    dry_run=dry_run,
                    not_run=not_run,
                )
            except Exception:
                # A snapshot failure must not kill the round; the next tick retries.
                pass

    thread = threading.Thread(target=loop, daemon=True, name="live-round-reporter")
    thread.start()
    return stop


def _materialise_team_tools(team) -> tuple[int | None, list[str]]:
    """Read the leader's tool surface off the team this round actually built."""
    try:
        from live_tool_surface_check import _leader_tools, _tool_rail

        rail = _tool_rail(team)
        if rail is None:
            return None, []
        names = _leader_tools(rail)
        return len(names), names
    except Exception as exc:
        return None, [f"tool surface unavailable: {type(exc).__name__}: {exc}"]


# --------------------------------------------------------------------------
# child: the actual round
# --------------------------------------------------------------------------
def _team_config(credentials: dict[str, str]) -> dict:
    """The MVP team, pointed at the caller's endpoint."""
    from compile_spec_check import build_config

    config = build_config()
    template = config["modes"]["team"]["jiuwenswarm_mvp"]
    template["agents"] = {"leader": "$agent_leader", "teammate": "$agent_teammate"}
    # A single entry the leader and both teammates share. The framework's own
    # model_client_config shape; nothing here is invented.
    config["models"] = {
        "defaults": [
            {
                "model_client_config": {
                    "model_name": credentials["MODEL_NAME"],
                    "client_provider": "OpenAI",
                    "api_base": credentials["MODEL_BASE_URL"],
                    "api_key": credentials["MODEL_API_KEY"],
                },
                "model_config_obj": {"temperature": 0.2},
                "is_default": True,
            }
        ]
    }
    return config


async def _run_round(
    state: _RoundState,
    credentials: dict[str, str],
) -> None:
    from openjiuwen.core.runner.runner import Runner

    from app.config_adapter import compile_team_spec

    spec = compile_team_spec(_team_config(credentials))
    state.base_checks.append(
        {
            "check": "the team spec compiles with team_mode='predefined'",
            "result": "pass" if spec.team_mode == "predefined" else "fail",
            "detail": f"team_mode={spec.team_mode!r}",
        }
    )

    started = time.monotonic()
    team = spec.build()
    state.base_checks.append(
        {
            "check": "the team builds",
            "result": "pass" if team is not None else "fail",
            "detail": f"built in {time.monotonic() - started:.1f}s",
        }
    )

    tool_count, tool_names = _materialise_team_tools(team)
    state.team_tool_count = tool_count
    state.team_tool_names = tool_names
    spawn = [name for name in tool_names if name.startswith("spawn_")]
    state.base_checks.append(
        {
            "check": "the built leader holds no spawn tool",
            "result": "pass" if tool_count is not None and not spawn else "fail",
            "detail": f"{tool_count} team tools; spawn={spawn}",
        }
    )

    await Runner.start()

    # Everything below happens inside the stream, and the snapshot is written
    # from inside it too: there may be no statement after the loop to hang a
    # final save on. The reporter thread covers the tail of the round as well.
    session_id = f"live-round-{int(time.time())}"
    queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
    producer_error: list[BaseException] = []

    async def _producer() -> None:
        try:
            async for item in Runner.run_agent_team_streaming(
                agent_team=spec, inputs={"query": QUERY}, session=session_id
            ):
                await queue.put(item)
        except BaseException as exc:  # noqa: BLE001 - reported on the main path
            producer_error.append(exc)
        finally:
            await queue.put(None)

    task = asyncio.create_task(_producer())
    stop_reason: str | None = None
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=STREAM_TICK_SECONDS)
            except asyncio.TimeoutError:
                now = time.monotonic()
                with state.lock:
                    idle_for = now - state.idle_at if state.idle_at else 0.0
                    completed = state.saw_completed
                    idle = state.saw_idle
                if idle and not completed and idle_for >= IDLE_GRACE_SECONDS:
                    stop_reason = "idle_grace"
                    break
                if now - state.started >= ROUND_DEADLINE_SECONDS:
                    stop_reason = "deadline"
                    break
                continue

            if chunk is None:
                stop_reason = "stream_closed"
                break

            text, marker = _chunk_text_and_marker(chunk)
            chunk_type = _chunk_field(chunk, "type")
            with state.lock:
                state.events += 1
                state.stream_entered = True
                if isinstance(chunk_type, str):
                    state.chunk_types[chunk_type] = state.chunk_types.get(chunk_type, 0) + 1
                if marker:
                    state.markers.append(marker)
                    if marker == "team.idle":
                        state.saw_idle = True
                        state.idle_at = time.monotonic()
                    elif marker == "team.completed":
                        state.saw_completed = True
                if text:
                    state.chunks_with_text += 1
                    if chunk_type == "answer":
                        state.answer_text = text
                    else:
                        state.llm_parts.append(text)

            if marker == "team.completed":
                stop_reason = "team_completed"
                break
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    if stop_reason in ("idle_grace", "deadline"):
        with contextlib.suppress(Exception):
            await Runner.stop_agent_team(team_name=spec.team_name, session_id=session_id)
    else:
        with contextlib.suppress(Exception):
            await Runner.stop()

    with state.lock:
        state.stream_ended = True
        state.stop_reason = stop_reason
        if producer_error:
            state.error = f"stream producer: {type(producer_error[0]).__name__}: {producer_error[0]}"


def _verify_without_model_call(state: _RoundState) -> str | None:
    """Compile, build and materialise the tool surface without contacting a model.

    Used by the dry run and by the no-credentials path: a missing key must not
    blank out everything that is still checkable offline.
    """
    try:
        from app.config_adapter import compile_team_spec

        spec = compile_team_spec(_team_config(_dry_run_credentials()))
        state.base_checks.append(
            {
                "check": "the team spec compiles with team_mode='predefined'",
                "result": "pass" if spec.team_mode == "predefined" else "fail",
                "detail": f"team_mode={spec.team_mode!r}",
            }
        )
        started = time.monotonic()
        team = spec.build()
        state.base_checks.append(
            {
                "check": "the team builds",
                "result": "pass" if team is not None else "fail",
                "detail": f"built in {time.monotonic() - started:.1f}s",
            }
        )
        tool_count, tool_names = _materialise_team_tools(team)
        state.team_tool_count = tool_count
        state.team_tool_names = tool_names
        spawn = [name for name in tool_names if name.startswith("spawn_")]
        state.base_checks.append(
            {
                "check": "the built leader holds no spawn tool",
                "result": "pass" if tool_count is not None and not spawn else "fail",
                "detail": f"{tool_count} team tools; spawn={spawn}",
            }
        )

        # Prove the log-derived assertions fire on the framework's own line
        # formats. A regex typo would otherwise only surface in the expensive
        # real round, after the money was spent.
        synthetic = [
            {"logger": "common", "level": "INFO",
             "message": "[LLM] >>> request: msg_count=2, tool_count=29"},
            {"logger": "common", "level": "INFO",
             "message": "[LLM] <<< response: content=ok, tokens={input=100, output=20}"},
            {"logger": "common", "level": "INFO",
             "message": "[LLM]   tool_call: create_task({'goal': 'x'})"},
            {"logger": "common", "level": "INFO",
             "message": "[BaseSecurityRail] get_user_input subject_id='u' raw_input_type=user"},
            {"logger": "session", "level": "INFO",
             "message": "Succeed to save workflow checkpoint on turn end"},
            {"logger": "common", "level": "INFO",
             "message": "[OuterLoop] iteration=1 task_id=t completed, output=ok"},
            {"logger": "team", "level": "INFO",
             "message": "[team_leader] team went idle: {}"},
        ]
        probe = _analyse_logs(synthetic, tool_count)
        failed = [name for name, ok, _ in probe["checks"] if not ok]
        state.base_checks.append(
            {
                "check": "the log assertions fire on the framework's log line formats",
                "result": "pass" if not failed else "fail",
                "detail": f"{len(probe['checks'])} derived checks; "
                + (f"failed={failed}" if failed else "all fired"),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
        return f"{type(exc).__name__}: {exc}"
    return None


def _child_main() -> int:
    dry_run = os.environ.get(DRY_RUN_ENV) == "1"
    results_path = Path(os.environ[CHILD_RESULTS_ENV])
    marker_path = Path(os.environ[ATEXIT_MARKER_ENV])
    results_path.unlink(missing_ok=True)
    marker_path.unlink(missing_ok=True)

    def _mark_atexit() -> None:
        try:
            _atomic_write_json(
                marker_path,
                {"atexit_ran": True, "pid": os.getpid(), "at": time.time()},
            )
        except Exception:
            pass

    atexit.register(_mark_atexit)

    capture = _LogCapture()
    capture.install()

    state = _RoundState()
    not_run = list(NOT_RUN_ALWAYS)

    credentials = _dry_run_credentials() if dry_run else _load_credentials()
    if dry_run:
        not_run.append("model credentials: not required; a dry run never contacts the endpoint")
    else:
        state.base_checks.append(
            {
                "check": "model credentials are available (from env or .env.local)",
                "result": "pass" if credentials else "fail",
                "detail": "MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME",
            }
        )

    if credentials is None:
        # A missing key blocks the round, not the offline checks: compile, build
        # and tool surface are still measurable and belong in the report.
        state.error = _verify_without_model_call(state)
        _write_snapshot(
            state, capture, results_path, phase="no_credentials", complete=True,
            dry_run=dry_run, not_run=not_run,
        )
        return 1

    if dry_run:
        state.error = _verify_without_model_call(state)
        if state.error:
            _write_snapshot(
                state, capture, results_path, phase="raised", complete=True,
                dry_run=True, not_run=not_run,
            )
            return 1
        _write_snapshot(
            state, capture, results_path, phase="dry_run", complete=True,
            dry_run=True, not_run=not_run,
        )
        return 0

    stop = _start_reporter(state, capture, results_path, dry_run=False, not_run=not_run)
    try:
        asyncio.run(_run_round(state, credentials))
    except BaseException as exc:  # noqa: BLE001 - SystemExit included; report it
        with state.lock:
            state.error = f"{type(exc).__name__}: {exc}"
        stop.set()
        _write_snapshot(
            state, capture, results_path, phase="raised", complete=True,
            dry_run=False, not_run=not_run,
        )
        return 1
    finally:
        stop.set()

    _write_snapshot(
        state, capture, results_path, phase="returned", complete=True,
        dry_run=False, not_run=not_run,
    )
    return 0


# --------------------------------------------------------------------------
# parent: run the child and classify how it ended
# --------------------------------------------------------------------------
def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _classify_termination(
    returncode: int | None, timed_out: bool, child: dict, atexit_ran: bool, dry_run: bool
) -> str:
    if timed_out:
        return "timeout"
    if returncode is None:
        return "unknown"
    if returncode != 0:
        return f"nonzero_exit:{returncode:#x}" if returncode > 2**31 else f"nonzero_exit:{returncode}"
    if dry_run:
        return "dry_run_returned_normally"
    stream_ended = bool(child.get("stream_ended"))
    if stream_ended and atexit_ran:
        return "returned_normally"
    if not stream_ended and not atexit_ran:
        return "ended_without_unwinding (exit code 0, no atexit, no final snapshot)"
    if not stream_ended and atexit_ran:
        return "atexit_ran_without_stream_end"
    return "returned_without_final_snapshot"


def _parent_main(dry_run: bool) -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    child_results = RUNTIME_DIR / "child_results.json"
    atexit_marker = RUNTIME_DIR / "atexit.marker"
    child_log = RUNTIME_DIR / "child_stdout.log"
    for path in (child_results, atexit_marker, child_log):
        path.unlink(missing_ok=True)

    env = os.environ.copy()
    env[CHILD_RESULTS_ENV] = str(child_results)
    env[ATEXIT_MARKER_ENV] = str(atexit_marker)
    env[DRY_RUN_ENV] = "1" if dry_run else "0"
    # Credentials are never copied into the child's environment here: the child
    # reads the same environment or .env.local itself.

    command = [sys.executable, str(Path(__file__).resolve()), CHILD_FLAG]
    started = time.monotonic()
    returncode: int | None = None
    timed_out = False
    with open(child_log, "wb") as sink:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            returncode = process.wait(timeout=MAX_ROUND_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            returncode = process.wait()

    elapsed = time.monotonic() - started
    child = _read_json(child_results) or {}
    atexit_ran = atexit_marker.is_file()
    termination = {
        "returncode": returncode,
        "timed_out": timed_out,
        "atexit_ran": atexit_ran,
        "stream_ended": bool(child.get("stream_ended")),
        "stream_entered": bool(child.get("stream_entered")),
        "midstream_writes": child.get("midstream_writes", 0),
        "last_phase": child.get("phase"),
        "classified": _classify_termination(returncode, timed_out, child, atexit_ran, dry_run),
        "child_stdout": str(child_log.relative_to(ROOT)),
    }

    checks = Checks()

    checks.add(
        "the round process ran and exited",
        returncode is not None,
        f"returncode={returncode} after {elapsed:.0f}s"
        + (" (timed out and killed)" if timed_out else ""),
    )
    blocked = not dry_run and child.get("phase") == "no_credentials"
    extra_not_run = list(child.get("not_run", []))
    if blocked:
        checks.add(
            "the round could start (model credentials available)",
            False,
            "no MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME in the environment or .env.local",
        )
        extra_not_run.extend(
            [
                "the round process exit status: the child stopped before the round",
                "a snapshot while the stream was running: the stream never started",
                "the model request count: no request was attempted",
            ]
        )
    else:
        checks.add(
            "the round process exited with status 0",
            returncode == 0,
            f"returncode={returncode}",
        )
    if dry_run:
        checks.add(
            "the dry run persisted its result",
            bool(child),
            f"phase={child.get('phase')!r}",
        )
    elif not blocked:
        checks.add(
            "a result snapshot was persisted while the stream was running",
            bool(child) and termination["midstream_writes"] > 0 and termination["stream_entered"],
            f"midstream_writes={termination['midstream_writes']}, "
            f"stream_entered={termination['stream_entered']}",
        )
        checks.add(
            "the persisted snapshot is readable after the process ended",
            bool(child),
            f"last_phase={child.get('phase')!r}, complete={child.get('complete')}",
        )
        checks.add(
            "the round made at least one model request",
            bool(child.get("model_request_count")),
            f"requests={child.get('model_request_count', 0)}",
        )

    merged: list[dict] = []
    for item in child.get("checks", []):
        if item.get("result") == "not_run":
            continue  # reported in not_run instead of counted as a pass/fail
        if blocked and item.get("check", "").startswith("model credentials"):
            continue  # the parent states this once, with the consequence
        merged.append(item)
    merged.extend(checks.items)

    passed = sum(1 for item in merged if item["result"] == "pass")
    payload = {
        "checks_passed": passed,
        "checks_total": len(merged),
        "checks": merged,
        "scope": child.get(
            "scope",
            "dry run: spec, build and tool surface; no model call, no network"
            if dry_run
            else "one real round against a real provider; costs money and is non-deterministic",
        ),
        "query": QUERY,
        "dry_run": dry_run,
        "report": "parent_final",
        "termination": termination,
        "events": child.get("events", 0),
        "chunks_with_text": child.get("chunks_with_text", 0),
        "chunk_types": child.get("chunk_types", {}),
        "team_markers": child.get("team_markers", []),
        "stream_stop_reason": child.get("stream_stop_reason"),
        "answer_chars": child.get("answer_chars", 0),
        "answer_preview": child.get("answer_preview", ""),
        "usage": child.get("usage", {}),
        "model_request_count": child.get("model_request_count", 0),
        "model_requests": child.get("model_requests", []),
        "tool_calls": child.get("tool_calls", {}),
        "team_tools": child.get("team_tools", {}),
        "framework_error_count": child.get("framework_error_count", 0),
        "framework_errors": child.get("framework_errors", []),
        "child_error": child.get("error"),
        "not_run": extra_not_run,
    }
    output_path = DRY_RUN_RESULTS_PATH if dry_run else RESULTS_PATH
    _atomic_write_json(output_path, payload)

    for item in merged:
        mark = "PASS" if item["result"] == "pass" else "FAIL"
        print(f"[{mark}] {item['check']}", flush=True)
        if item["detail"]:
            print(f"       {item['detail']}", flush=True)
    print(f"\ntermination: {termination['classified']}", flush=True)
    for entry in payload["not_run"]:
        print(f"[NOT RUN] {entry}", flush=True)
    print(f"\n{passed}/{len(merged)} checks passed -> {output_path}", flush=True)
    return 1 if any(item["result"] != "pass" for item in merged) else 0


# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    if CHILD_FLAG in argv:
        return _child_main()
    return _parent_main(dry_run=DRY_RUN_FLAG in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
