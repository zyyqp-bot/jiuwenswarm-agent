"""P0-A (part 1): offline compilation and roster validation of the MVP team config.

Compiles the fixed three-member MVP roster (Leader + Research + Engineering)
into a real openjiuwen ``TeamAgentSpec`` -- no Runner startup, no model call,
no network -- and asserts the project invariants from PROJECT.md:

- R02: exactly three collaboration members; the roster is closed
- R05: Skill seed declared per professional member
- modes: team_mode=predefined, dispatch_mode=scheduled, spawn_mode=inprocess
- the framework's own build-time roster checks pass

Those three roster checks (reserved names / HITT consistency / bridge
consistency) normally run inside ``TeamAgentSpec.build()``. This script calls
them directly, so it never constructs a live TeamAgent and never starts a
runtime.

Baseline: openjiuwen agent-core @ 6df9699e650b3ad9b9835b46ae3c3b7572adb04b
Run:      .venv/Scripts/python.exe p0a/roster_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402

TEAM_NAME = "jiuwenswarm_mvp"
RESULTS_PATH = Path(__file__).resolve().parent / "results.json"

# Skill visibility is NOT carried on the spec: the spec's ``skills`` field is
# only a seed, and AgentConfigurator replaces it with ``[]`` at build time,
# routing through the on-disk ``skills-visibility.json`` declarations. The seed
# assertion below therefore only proves the config declares an intent.
LEADER_SKILL_SEED = ["planning", "verification"]
TEAMMATE_SKILL_SEED = ["research", "software_engineering"]


def build_team_config() -> dict:
    """Return the fixed MVP roster as a TeamAgentSpec payload."""
    return {
        "team_name": TEAM_NAME,
        "lifecycle": "persistent",
        "spawn_mode": "inprocess",
        "team_mode": "predefined",
        "dispatch_mode": "scheduled",
        # Capability ceilings. With team_mode="predefined" these are the
        # static guarantee that the declared roster is the whole team.
        "enable_hitt": False,
        "enable_bridge": False,
        "enable_fork": False,
        "enable_swarmflow": False,
        "external_cli_agents": [],
        # ``agents`` maps TeamRole -> DeepAgentSpec template. Both professional
        # members share the "teammate" template; they are told apart by the
        # per-member desc/prompt and by their Skill visibility metadata.
        "agents": {
            "leader": {
                "system_prompt": "You own planning, dispatch and final judgement.",
                "enable_skill_discovery": False,
                "skills": LEADER_SKILL_SEED,
            },
            "teammate": {
                "system_prompt": "You work the task assigned to you and report evidence.",
                "enable_skill_discovery": False,
                "skills": TEAMMATE_SKILL_SEED,
            },
        },
        "leader": {
            "member_name": "team_leader",
            "display_name": "Team Leader",
            "desc": "Plans the work, assigns tasks, resolves conflicts and owns the final judgement.",
            "prompt": (
                "You are the team leader. Refine the request into an explicit task plan, "
                "assign it to the professional members, and only accept results that carry "
                "verifiable evidence. Never mark an unverified claim as fact."
            ),
        },
        "predefined_members": [
            {
                "member_name": "research",
                "display_name": "Research",
                "role_type": "teammate",
                "desc": "Collects evidence, checks facts and reports unknowns.",
                "prompt": (
                    "Use only retrieved, citable material. Keep constraints, acceptance, "
                    "assumptions and unknowns separate. Report what you could not verify."
                ),
            },
            {
                "member_name": "engineering",
                "display_name": "Engineering",
                "role_type": "teammate",
                "desc": "Turns validated findings into an actionable engineering plan.",
                "prompt": (
                    "Propose architecture, interfaces and verifiable steps. Do not invent "
                    "test results or claim verification you did not perform."
                ),
            },
        ],
    }


def _check_roster_rule(checks: Checks, label: str, fn) -> None:
    """Run one framework build-time roster check and record its outcome."""
    try:
        fn()
    except Exception as exc:  # framework checks raise ValueError / BaseError
        checks.add(label, False, f"{type(exc).__name__}: {exc}")
    else:
        checks.add(label, True)


def run_checks(spec: TeamAgentSpec, checks: Checks) -> None:
    members = [spec.leader, *spec.predefined_members]
    teammate_spec = spec.agents.get("teammate")

    checks.add(
        "team_mode is explicitly 'predefined' (the only static no-spawn guarantee)",
        spec.team_mode == "predefined",
        f"team_mode={spec.team_mode!r}",
    )
    checks.add(
        "dispatch_mode is 'scheduled'",
        spec.dispatch_mode == "scheduled",
        f"dispatch_mode={spec.dispatch_mode!r}",
    )
    checks.add(
        "spawn_mode is 'inprocess' and lifecycle is 'persistent'",
        (spec.spawn_mode, spec.lifecycle) == ("inprocess", "persistent"),
        f"spawn_mode={spec.spawn_mode!r} lifecycle={spec.lifecycle!r}",
    )
    checks.add(
        "roster is exactly three collaboration members (R02)",
        len(members) == 3,
        "members=" + ",".join(m.member_name for m in members),
    )
    checks.add(
        "all dynamic-spawn capability ceilings are closed",
        not any([spec.enable_hitt, spec.enable_bridge, spec.enable_fork, spec.enable_swarmflow])
        and not spec.external_cli_agents,
        f"hitt={spec.enable_hitt} bridge={spec.enable_bridge} fork={spec.enable_fork} "
        f"swarmflow={spec.enable_swarmflow} external_cli={len(spec.external_cli_agents)}",
    )
    checks.add(
        "both professional members are 'teammate' role",
        [m.role_type for m in spec.predefined_members] == ["teammate", "teammate"],
        "roles=" + ",".join(str(m.role_type) for m in spec.predefined_members),
    )
    checks.add(
        "each member has a public desc and a private prompt",
        all(m.desc and m.prompt for m in members),
        "members=" + ",".join(m.member_name for m in members),
    )
    checks.add(
        "Skill seed declared on the teammate template "
        "(seed only; the authoritative set lives in skills-visibility.json)",
        teammate_spec is not None and bool(teammate_spec.skills),
        f"teammate skill seed={getattr(teammate_spec, 'skills', None)!r}",
    )

    _check_roster_rule(checks, "_validate_reserved_names", spec._validate_reserved_names)
    _check_roster_rule(checks, "_validate_hitt_consistency", spec._validate_hitt_consistency)
    _check_roster_rule(checks, "_validate_bridge_consistency", spec._validate_bridge_consistency)


def main() -> int:
    spec = TeamAgentSpec.model_validate(build_team_config())

    checks = Checks()
    run_checks(spec, checks)

    return checks.report(
        RESULTS_PATH,
        scope="offline TeamAgentSpec compilation + roster validation; no Runner, no model call, no network",
        baseline="openjiuwen agent-core @ 6df9699e650b3ad9b9835b46ae3c3b7572adb04b",
        team_name=spec.team_name,
        member_count=1 + len(spec.predefined_members),
    )


if __name__ == "__main__":
    sys.exit(main())
