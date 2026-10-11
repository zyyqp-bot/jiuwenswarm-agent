"""P0-B (part 7): the runtime adapter's first slice.

Builds a real ``TeamAgent`` from the compiled spec and checks the object
actually carries the identity the spec declared, reading the materialized
values off ``runtime_context`` -- not off ``team.spec``, which is the very same
object the caller passed in and therefore proves nothing.

The per-field mismatch branches are exercised against lightweight stand-ins, so
one slow build covers the happy path and every guard is still tested.

No run is started, no member is spawned, no model is called.

Run: .venv/Scripts/python.exe p0b/runtime_adapter_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from app.config_adapter import compile_team_spec  # noqa: E402
from app.runtime_adapter import RuntimeBuildError, build_team, verify_identity  # noqa: E402
from checks.harness import Checks, call_ok, raises  # noqa: E402
from compile_spec_check import LEADER, build_config  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "runtime_adapter_results.json"


def _stand_in(
    *,
    team_name: str = "jiuwenswarm_mvp",
    member_name: str = LEADER["member_name"],
    desc: str = LEADER["desc"],
    prompt: str = LEADER["prompt"],
    dispatch_mode: str = "scheduled",
) -> SimpleNamespace:
    """A minimal object shaped like the parts of a TeamAgent verify_identity reads."""
    context = SimpleNamespace(
        member_name=member_name,
        desc=desc,
        prompt=prompt,
        team_spec=SimpleNamespace(dispatch_mode=dispatch_mode),
    )
    return SimpleNamespace(team_name=team_name, runtime_context=context)


async def _async_checks(checks: Checks) -> None:
    spec = compile_team_spec(build_config())
    team = await build_team(spec)

    checks.add(
        "the compiled spec builds into a live TeamAgent",
        type(team).__name__ == "TeamAgent",
        f"type={type(team).__module__}.{type(team).__name__}",
    )
    checks.add(
        "the built team carries the spec's team name",
        team.team_name == spec.team_name,
        f"team_name={team.team_name!r}",
    )
    checks.add(
        "team.spec is the input spec object, so it cannot prove anything",
        team.spec is spec,
        "this is why verify_identity reads runtime_context instead",
    )

    context = team.runtime_context
    checks.add(
        "the built leader's member name matches the spec",
        context.member_name == spec.leader.member_name,
        f"member_name={context.member_name!r}",
    )
    checks.add(
        "the leader desc survives the build (the config chain drops it, the build must not)",
        context.desc == LEADER["desc"],
        f"desc={context.desc!r}",
    )
    checks.add(
        "the leader prompt survives the build",
        context.prompt == LEADER["prompt"],
        f"prompt={context.prompt!r}",
    )
    checks.add(
        "the built object is the leader role",
        getattr(context.role, "value", context.role) == "leader",
        f"role={context.role!r}",
    )
    checks.add(
        "the runtime TeamSpec is materialized separately and keeps dispatch_mode='scheduled'",
        context.team_spec is not spec and context.team_spec.dispatch_mode == "scheduled",
        f"dispatch_mode={context.team_spec.dispatch_mode!r}",
    )
    checks.add(
        "the runtime TeamSpec carries the team name",
        context.team_spec.team_name == spec.team_name,
        f"team_name={context.team_spec.team_name!r}",
    )
    checks.add(
        "the team exposes the native task manager",
        team.task_manager is not None and team.task_manager.team_name == spec.team_name,
        f"task_manager.team_name={getattr(team.task_manager, 'team_name', None)!r}",
    )
    checks.add("verify_identity accepts a team that matches its spec", call_ok(verify_identity, team, spec), "")

    # --- every guard, on stand-ins (no second build needed) -----------------
    checks.add(
        "a stand-in identical to the spec passes",
        call_ok(verify_identity, _stand_in(), spec),
        "",
    )
    for label, override in (
        ("team_name", {"team_name": "some-other-team"}),
        ("leader member name", {"member_name": "someone-else"}),
        ("leader desc", {"desc": "a different blurb"}),
        ("leader prompt", {"prompt": "a different agreement"}),
        ("dispatch mode", {"dispatch_mode": "autonomous"}),
    ):
        ok, detail = raises(verify_identity, _stand_in(**override), spec, expected=RuntimeBuildError)
        checks.add(f"verify_identity rejects a mismatched {label}", ok, detail)

    # --- the real mismatch path disposes rather than leaking ---------------
    ok, detail = raises(verify_identity, team, spec.model_copy(update={"team_name": "some-other-team"}),
                        expected=RuntimeBuildError)
    checks.add("a renamed spec no longer matches the built team", ok, detail)


def main() -> int:
    checks = Checks()
    asyncio.run(_async_checks(checks))

    return checks.report(
        RESULTS_PATH,
        scope="real TeamAgent construction from a compiled spec; no run, no spawn, no model call",
        not_covered="spawn-tool removal and run behaviour require an initialized runtime",
    )


if __name__ == "__main__":
    sys.exit(main())
