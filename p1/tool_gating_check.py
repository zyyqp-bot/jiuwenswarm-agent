"""P1 (part 1): what actually gates the leader's spawn tools.

R02 requires the roster to stay at three members. Until now that rested on a
reading of the source: "``team_mode='predefined'`` strips the spawn tools". This
script checks the tool sets themselves, and the reading turned out to be
incomplete.

The leader's spawn capability is gated by **two** mechanisms, not one:

- ``team_mode="predefined"`` removes four tools --
  ``spawn_teammate``, ``spawn_human_agent``, ``spawn_bridge_agent``,
  ``spawn_external_cli`` (``agent_configurator.py``);
- ``spawn_passive_human`` is **not in that list**. It is gated on
  ``enable_hitt`` (``tool_factory.py``), so with HITT left open the leader keeps
  it -- and a leader that can spawn a human can still grow the roster.

The loader defaults ``enable_hitt`` to True, so "predefined" alone was never
enough. The sets below are the upstream constants plus the upstream subtraction
rules, evaluated rather than eyeballed.

No framework runtime, no model, no network.

Run: .venv/Scripts/python.exe p1/tool_gating_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from app.config_adapter import SpecCompilationError, compile_team_spec, validate_roster_closed  # noqa: E402
from checks.harness import Checks, raises  # noqa: E402
from compile_spec_check import build_config  # noqa: E402
from openjiuwen.agent_teams.tools.tool_permissions import LEADER_TOOLS  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "tool_gating_results.json"

# agent_configurator.py: the exclude list applied when _resolve_team_mode(spec)
# returns "predefined".
PREDEFINED_EXCLUDE = frozenset(
    {"spawn_teammate", "spawn_human_agent", "spawn_bridge_agent", "spawn_external_cli"}
)
# tool_factory.py: the capability gates applied after the exclude list.
HITT_GATED = frozenset({"spawn_human_agent", "spawn_passive_human"})


def _leader_tools_after_gates(*, enable_hitt: bool, predefined: bool) -> set[str]:
    """Apply the upstream subtraction rules to the upstream leader tool set."""
    allowed = set(LEADER_TOOLS)
    if predefined:
        allowed -= PREDEFINED_EXCLUDE
    if not enable_hitt:
        allowed -= HITT_GATED
    return allowed


def main() -> int:
    checks = Checks()

    spawn_tools = {name for name in LEADER_TOOLS if name.startswith("spawn_")}
    checks.add(
        "the leader's tool set contains five spawn tools",
        len(spawn_tools) == 5,
        f"spawn={sorted(spawn_tools)}",
    )
    checks.add(
        "the predefined exclude list omits spawn_passive_human",
        "spawn_passive_human" not in PREDEFINED_EXCLUDE and "spawn_passive_human" in spawn_tools,
        "this is the gap: one spawn tool is not covered by team_mode",
    )

    # --- the gap, demonstrated ---------------------------------------------
    leaking = _leader_tools_after_gates(enable_hitt=True, predefined=True)
    checks.add(
        "predefined alone leaves spawn_passive_human callable when HITT is open",
        {"spawn_passive_human"} == {name for name in leaking if name.startswith("spawn_")},
        f"remaining spawn tools={sorted(name for name in leaking if name.startswith('spawn_'))}",
    )

    # --- closing both gates -------------------------------------------------
    for label, enable_hitt in (("HITT closed", False),):
        closed = _leader_tools_after_gates(enable_hitt=enable_hitt, predefined=True)
        checks.add(
            f"with {label}, the leader keeps no spawn tool at all",
            not {name for name in closed if name.startswith("spawn_")},
            f"remaining={sorted(name for name in closed if name.startswith('spawn_'))}",
        )

    hybrid = _leader_tools_after_gates(enable_hitt=False, predefined=False)
    checks.add(
        "without predefined, the four team_mode-gated spawn tools survive",
        {name for name in hybrid if name.startswith("spawn_")}
        == spawn_tools - HITT_GATED,
        f"remaining={sorted(name for name in hybrid if name.startswith('spawn_'))}",
    )

    # --- the adapter closes them --------------------------------------------
    spec = compile_team_spec(build_config())
    checks.add(
        "the compiled MVP spec has every capability gate shut",
        spec.team_mode == "predefined"
        and not spec.enable_hitt
        and not spec.enable_bridge
        and not spec.enable_swarmflow
        and not spec.external_cli_agents,
        f"hitt={spec.enable_hitt} bridge={spec.enable_bridge} swarmflow={spec.enable_swarmflow} "
        f"cli={len(spec.external_cli_agents)}",
    )
    checks.add(
        "so the leader the spec describes keeps no spawn tool",
        not {
            name
            for name in _leader_tools_after_gates(enable_hitt=spec.enable_hitt, predefined=True)
            if name.startswith("spawn_")
        },
        "",
    )

    # --- a config that asks for HITT does not get it ------------------------
    permissive = build_config()
    permissive["modes"]["team"]["jiuwenswarm_mvp"]["enable_hitt"] = True
    checks.add(
        "a config that explicitly asks for HITT is still compiled with it closed",
        not compile_team_spec(permissive).enable_hitt,
        "the MVP does not use HITT, so the gate is shut rather than inherited",
    )

    # --- and the validator refuses a spec that leaves one open --------------
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402

    hot = TeamAgentSpec.model_validate(
        {
            "team_name": spec.team_name,
            "team_mode": "predefined",
            "dispatch_mode": "scheduled",
            "agents": {"leader": {}, "teammate": {}},
            "predefined_members": [
                {"member_name": "research", "display_name": "Research", "role_type": "teammate"}
            ],
            "enable_hitt": True,
        }
    )
    ok, detail = raises(validate_roster_closed, hot, expected=SpecCompilationError)
    checks.add(
        "the roster validator refuses a predefined spec with HITT left open",
        ok and "enable_hitt" in detail,
        detail,
    )

    return checks.report(
        RESULTS_PATH,
        scope="leader tool gating from upstream constants and subtraction rules; no runtime, no model",
        not_run=[
            "the mounted tool list of a team that never ran: rails register lazily, "
            "so this checks the rules and the compiled spec, not a live agent",
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
