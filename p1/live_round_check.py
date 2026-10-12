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


async def _run_round(checks: Checks) -> None:
    from openjiuwen.core.runner.runner import Runner

    from app.config_adapter import compile_team_spec

    credentials = _load_credentials()
    checks.add(
        "model credentials are available (from env or .env.local)",
        credentials is not None,
        "MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME",
    )
    if credentials is None:
        return

    spec = compile_team_spec(_team_config(credentials))
    checks.add("the team spec compiles", spec.team_mode == "predefined", f"team_mode={spec.team_mode!r}")

    started = time.monotonic()
    team = spec.build()
    checks.add("the team builds", team is not None, f"built in {time.monotonic() - started:.1f}s")

    await Runner.start()
    try:
        # Runner accepts a bare session id string, so no session object is needed.
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
        elapsed = time.monotonic() - started

        answer = "\n".join(chunks).strip()
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
    finally:
        await Runner.stop()


def main() -> int:
    checks = Checks()
    try:
        asyncio.run(_run_round(checks))
    except Exception as exc:
        checks.add("the round ran to completion", False, f"{type(exc).__name__}: {exc}")

    checks.write(
        RESULTS_PATH,
        scope="one real round against a real provider; costs money and is non-deterministic",
        query=QUERY,
    )
    for item in checks.items:
        mark = "PASS" if item["result"] == "pass" else "FAIL"
        print(f"[{mark}] {item['check']}")
        if item["detail"]:
            print(f"       {item['detail']}")
    print(f"\n-> {RESULTS_PATH}")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
