"""P1 (part 2): the leader's tools on a **built** team, not a reading of them.

``p1/tool_gating_check.py`` verified the upstream rules and the compiled spec.
This one goes further: it builds a real ``TeamAgent``, reads the
``TeamToolRail`` the framework actually configured, and materialises the
leader's tool set from that rail's own parameters. Nothing here is transcribed
from the source -- the exclude list comes off the rail, and the tools come out
of ``create_team_tools`` driven by the team's real backend.

That matters because the earlier conclusion about R02 was reached by reading
``agent_configurator.py`` and was wrong. The contrast at the end is the point:
identical in every way except the ``enable_hitt`` gate, one build keeps
``spawn_passive_human`` and the other does not.

Requires a real ``TeamAgent`` construction, which needs no model but does take
tens of seconds per build.

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

RESULTS_PATH = Path(__file__).resolve().parent / "live_tool_surface_results.json"


def _leader_tools(team) -> tuple[list[str], list[str]]:
    """Return ``(tool names, exclude list)`` for a built team's leader.

    Reads the rail the framework configured rather than recomputing what it
    should have configured.
    """
    deep_agent = team.harness.inner_agent
    rail = next(
        rail for rail in deep_agent.configured_rails() if type(rail).__name__ == "TeamToolRail"
    )
    exclude = sorted(rail._exclude_tools or [])
    tools = create_team_tools(
        role="leader",
        agent_team=team.team_backend,
        team_mode="predefined",
        dispatch_mode="scheduled",
        lifecycle="persistent",
        exclude_tools=set(rail._exclude_tools or []),
    )
    return sorted(str(tool.card.name) for tool in tools), exclude


def main() -> int:
    checks = Checks()

    spec = compile_team_spec(build_config())
    fixed_tools, exclude = _leader_tools(spec.build())

    checks.add(
        "the built team's tool rail is reachable and declares an exclude list",
        len(exclude) == 4,
        f"exclude={exclude}",
    )
    checks.add(
        "the exclude list the framework actually applied omits spawn_passive_human",
        "spawn_passive_human" not in exclude,
        "this is why team_mode alone could not close the roster",
    )
    checks.add(
        "the leader of the compiled MVP spec holds no spawn tool at all",
        not [name for name in fixed_tools if name.startswith("spawn_")],
        f"spawn={[name for name in fixed_tools if name.startswith('spawn_')]}",
    )
    checks.add(
        "the leader still holds its coordination tools",
        {"create_task", "send_message"} <= set(fixed_tools),
        f"tools={fixed_tools}",
    )

    # --- the contrast: same path, one gate apart ---------------------------
    legacy = spec.model_copy(update={"enable_hitt": True})
    legacy_tools, _ = _leader_tools(legacy.build())
    legacy_spawn = [name for name in legacy_tools if name.startswith("spawn_")]

    checks.add(
        "with the HITT gate left open the same build keeps spawn_passive_human",
        legacy_spawn == ["spawn_passive_human"],
        f"spawn={legacy_spawn}",
    )
    checks.add(
        "so closing that gate is what removes it, not team_mode",
        set(fixed_tools) == set(legacy_tools) - set(legacy_spawn),
        "the two builds differ by exactly the leaked tool",
    )

    return checks.report(
        RESULTS_PATH,
        scope="real TeamAgent construction and tool materialisation; no model call, no run",
        not_run=[
            "a run: the tools are materialised from the built team's rail, "
            "but no round was ever invoked",
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
