"""P1: one real round against a real model.

Everything up to here verified modules, contracts, and the tool surface of a
*build*. This is the first check that starts a runtime and lets the leader
actually work.

It is deliberately the smallest round: one query, one run, and the question is
whether the chain works at all -- whether the leader plans, whether a member
receives a task, whether anything comes back. It is not section 12's scenario 1;
that needs a research -> engineering plan and would multiply the cost and the
number of ways to fail at once.

Credentials come from the environment or from ``.env.local`` (git-ignored).
They are never printed, and the check reports only what the run did.

Costs money and calls a real provider. Run it deliberately.

Run: .venv/Scripts/python.exe p1/live_round_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "live_round_results.json"
ENV_FILE = ROOT / ".env.local"

QUERY = "用一句话说明：为什么在开始实现前要先核对源码版本？"


def _load_credentials() -> dict[str, str] | None:
    """Read model credentials from the environment, falling back to .env.local."""
    keys = ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME")
    values = {key: os.environ.get(key, "").strip() for key in keys}
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
        if name in keys and not values.get(name):
            values[name] = value.strip().strip('"').strip("'")
    return values if all(values.values()) else None


_BASE_CHECKS: list[dict] = []


def _report(chunks: list[str], events: int, elapsed: float, *, final: bool) -> None:
    """Write and print the round's result.

    Called *from inside* the stream, and repeatedly. The framework terminates
    the process with ``os._exit()`` when the team round ends, so nothing after
    the loop is reachable and this cannot wait for it.
    """
    checks = Checks()
    checks.items.extend(_BASE_CHECKS)

    answer = "".join(chunks).strip()
    checks.add(
        "the round produced output",
        bool(answer),
        f"{events} chunks, {len(answer)} chars in {elapsed:.0f}s",
    )
    checks.add(
        "the output is not an error message",
        bool(answer) and not answer.lower().startswith(("error", "traceback")),
        answer[:200].replace("\n", " ") if answer else "(empty)",
    )
    checks.write(
        RESULTS_PATH,
        scope="one real round against a real provider; costs money and is non-deterministic",
        query=QUERY,
        complete=final,
    )
    for item in checks.items:
        mark = "PASS" if item["result"] == "pass" else "FAIL"
        print(f"[{mark}] {item['check']}", flush=True)
        if item["detail"]:
            print(f"       {item['detail']}", flush=True)


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


async def _run_round() -> None:
    from openjiuwen.core.runner.runner import Runner

    from app.config_adapter import compile_team_spec

    credentials = _load_credentials()
    if credentials is None:
        return

    spec = compile_team_spec(_team_config(credentials))
    _BASE_CHECKS.append(
        {
            "check": "the team spec compiles with team_mode='predefined'",
            "result": "pass" if spec.team_mode == "predefined" else "fail",
            "detail": f"team_mode={spec.team_mode!r}",
        }
    )

    started = time.monotonic()
    team = spec.build()
    _BASE_CHECKS.append(
        {
            "check": "the team builds",
            "result": "pass" if team is not None else "fail",
            "detail": f"built in {time.monotonic() - started:.1f}s",
        }
    )

    await Runner.start()

    # Everything below happens inside the stream, and the report is written from
    # inside it too. The framework tears the process down with os._exit() when
    # the team round ends -- atexit does not run, finally does not run, and no
    # statement after the loop is ever reached. Anything that has to survive
    # the round must be persisted before the round finishes.
    session_id = f"live-round-{int(time.time())}"
    chunks: list[str] = []
    events = 0
    async for chunk in Runner.run_agent_team_streaming(
        agent_team=spec, inputs={"query": QUERY}, session=session_id
    ):
        events += 1
        text = getattr(chunk, "content", None) or getattr(chunk, "text", None)
        if text:
            chunks.append(str(text))
        if chunks and events % 5 == 0:
            _report(checks, chunks, events, time.monotonic() - started, final=False)

    _report(checks, chunks, events, time.monotonic() - started, final=True)


def main() -> int:
    credentials = _load_credentials()
    _BASE_CHECKS.append(
        {
            "check": "model credentials are available (from env or .env.local)",
            "result": "pass" if credentials is not None else "fail",
            "detail": "MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME",
        }
    )
    if credentials is None:
        _report([], 0, 0.0, final=True)
        return 1

    try:
        asyncio.run(_run_round())
    except Exception as exc:
        _BASE_CHECKS.append(
            {"check": "the round raised", "result": "fail", "detail": f"{type(exc).__name__}: {exc}"}
        )
        _report([], 0, 0.0, final=True)
        return 1
    # Reaching here means the framework did *not* tear the process down; the
    # final report has already been written from inside the stream.
    print("-> the round returned control; see the results file", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
