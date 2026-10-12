"""P1: the leader's tool surface, read off a **built** team.

The earlier conclusion about R02 came from reading ``agent_configurator.py``,
and it was wrong: ``team_mode="predefined"`` strips four spawn tools, but the
leader's set has five, and the fifth -- ``spawn_passive_human`` -- is gated on
``enable_hitt`` instead.

So this check does not read source and does not re-encode any rule. It builds a
real ``TeamAgent``, takes the ``TeamToolRail`` the framework configured, and
materialises the tool set by handing that rail's **own** parameters back to
``create_team_tools``. Nothing is transcribed: not the exclude list, not the
role, not the modes.

The contrast at the end is what makes the assertion meaningful -- same path,
same configuration, only the HITT gate restored to its pre-fix value, and the
leaked tool comes back. The two builds then differ by exactly that one tool, so
"which gate removed it" is measured rather than inferred.

Requires real team construction (no model call, tens of seconds per build).

Run: .venv/Scripts/python.exe p1/live_tool_surface_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from app.config_adapter import compile_team_spec  # noqa: E402
from checks.harness import Checks  # noqa: E402
from compile_spec_check import build_config  # noqa: E402
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools  # noqa: E402
from openjiuwen.agent_teams.tools.tool_permissions import LEADER_TOOLS  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "live_tool_surface_results.json"

COORDINATION_TOOLS = ("create_task", "send_message")


def _tool_rail(team):
    """Return the built team's tool rail, or None when it is not there.

    Missing rather than raising: an upstream rename should fail a named check,
    not abort the run and leave no results file.
    """
    for rail in team.harness.inner_agent.configured_rails():
        if type(rail).__name__ == "TeamToolRail":
            return rail
    return None


def _leader_tools(rail) -> list[str]:
    """Materialise the leader's tools from the rail's own configuration."""
    tools = create_team_tools(
        role=rail._role,
        agent_team=rail._team_backend,
        team_mode=rail._team_mode,
        dispatch_mode=rail._dispatch_mode,
        lifecycle=rail._lifecycle,
        teammate_mode=rail._teammate_mode,
        exclude_tools=set(rail._exclude_tools or []),
    )
    return sorted(str(tool.card.name) for tool in tools)


def main() -> int:
    checks = Checks()

    spec = compile_team_spec(build_config())
    team = spec.build()
    rail = _tool_rail(team)

    checks.add(
        "the built team exposes a tool rail",
        rail is not None,
        "TeamToolRail found on the leader's deep agent" if rail else "not found",
    )
    if rail is None:
        return checks.report(RESULTS_PATH, scope="blocked: no tool rail on the built team")

    checks.add(
        "the rail was configured for the mode the spec asked for",
        rail._team_mode == "predefined" and rail._dispatch_mode == "scheduled",
        f"team_mode={rail._team_mode!r} dispatch_mode={rail._dispatch_mode!r}",
    )

    exclude = sorted(rail._exclude_tools or [])
    checks.add(
        "the exclude list the framework actually applied has four entries",
        len(exclude) == 4,
        f"exclude={exclude}",
    )
    checks.add(
        "and it omits spawn_passive_human",
        "spawn_passive_human" not in exclude,
        "this is why team_mode alone could not close the roster",
    )
    checks.add(
        "the leader's own tool set contains five spawn tools",
        len({name for name in LEADER_TOOLS if name.startswith("spawn_")}) == 5,
        f"spawn={sorted(name for name in LEADER_TOOLS if name.startswith('spawn_'))}",
    )

    fixed_tools = _leader_tools(rail)
    checks.add(
        "the leader of the compiled MVP spec holds no spawn tool at all",
        not [name for name in fixed_tools if name.startswith("spawn_")],
        f"spawn={[name for name in fixed_tools if name.startswith('spawn_')]}",
    )
    checks.add(
        "the leader still holds its coordination tools",
        set(COORDINATION_TOOLS) <= set(fixed_tools),
        f"tools={fixed_tools}",
    )

    # --- the contrast: same path, one gate apart ---------------------------
    # A distinct team name keeps the second build from re-registering the
    # first one's process-global resource ids.
    legacy = spec.model_copy(update={"enable_hitt": True, "team_name": f"{spec.team_name}_legacy"})
    legacy_rail = _tool_rail(legacy.build())
    if legacy_rail is None:
        checks.add("the contrast build exposes a tool rail", False, "not found")
        return checks.report(RESULTS_PATH, scope="blocked: contrast build had no tool rail")

    legacy_tools = _leader_tools(legacy_rail)
    legacy_spawn = [name for name in legacy_tools if name.startswith("spawn_")]

    checks.add(
        "with the HITT gate left open the same path keeps spawn_passive_human",
        legacy_spawn == ["spawn_passive_human"],
        f"spawn={legacy_spawn}",
    )
    checks.add(
        "so closing that gate is what removed it, not team_mode",
        set(fixed_tools) == set(legacy_tools) - set(legacy_spawn),
        "the two builds differ by exactly the leaked tool",
    )

    return checks.report(
        RESULTS_PATH,
        scope="real TeamAgent construction and tool materialisation; no model call, no run",
        not_run=[
            "an actual round: the tools are materialised from a built team, "
            "but nothing was ever invoked",
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
