"""P0-A: offline compilation and roster validation of the MVP team config.

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

import json
import sys
from pathlib import Path

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

TEAM_NAME = "jiuwenswarm_mvp"
RESULTS_PATH = Path(__file__).resolve().parent / "results.json"

# Skill visibility is NOT carried on the spec: the spec's ``skills`` field is
# only a seed, and AgentConfigurator replaces it with ``[]`` at build time,
# routing through the on-disk ``skills-visibility.json`` declarations. The seed
# assertions below therefore only prove the config declares an intent.
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


def _call_roster_check(label: str, fn) -> dict:
    """Run one build-time roster check and capture its outcome."""
    try:
        fn()
    except Exception as exc:  # framework checks raise ValueError / BaseError
        return {"check": label, "result": "fail", "detail": f"{type(exc).__name__}: {exc}"}
    return {"check": label, "result": "pass", "detail": ""}


def run_checks(spec: TeamAgentSpec) -> list[dict]:
    members = [spec.leader, *spec.predefined_members]
    teammate_spec = spec.agents.get("teammate")

    checks: list[dict] = [
        {
            "check": "team_mode is explicitly 'predefined' (the only static no-spawn guarantee)",
            "result": "pass" if spec.team_mode == "predefined" else "fail",
            "detail": f"team_mode={spec.team_mode!r}",
        },
        {
            "check": "dispatch_mode is 'scheduled'",
            "result": "pass" if spec.dispatch_mode == "scheduled" else "fail",
            "detail": f"dispatch_mode={spec.dispatch_mode!r}",
        },
        {
            "check": "spawn_mode is 'inprocess' and lifecycle is 'persistent'",
            "result": "pass" if (spec.spawn_mode, spec.lifecycle) == ("inprocess", "persistent") else "fail",
            "detail": f"spawn_mode={spec.spawn_mode!r} lifecycle={spec.lifecycle!r}",
        },
        {
            "check": "roster is exactly three collaboration members (R02)",
            "result": "pass" if len(members) == 3 else "fail",
            "detail": "members=" + ",".join(m.member_name for m in members),
        },
        {
            "check": "all dynamic-spawn capability ceilings are closed",
            "result": "pass"
            if not any([spec.enable_hitt, spec.enable_bridge, spec.enable_fork, spec.enable_swarmflow])
            and not spec.external_cli_agents
            else "fail",
            "detail": (
                f"hitt={spec.enable_hitt} bridge={spec.enable_bridge} fork={spec.enable_fork} "
                f"swarmflow={spec.enable_swarmflow} external_cli={len(spec.external_cli_agents)}"
            ),
        },
        {
            "check": "both professional members are 'teammate' role",
            "result": "pass"
            if [m.role_type for m in spec.predefined_members] == ["teammate", "teammate"]
            else "fail",
            "detail": "roles=" + ",".join(str(m.role_type) for m in spec.predefined_members),
        },
        {
            "check": "each member has a public desc and a private prompt",
            "result": "pass"
            if all(m.desc and m.prompt for m in members)
            else "fail",
            "detail": "members=" + ",".join(m.member_name for m in members),
        },
        {
            "check": "Skill seed declared on the teammate template (seed only; authoritative set lives in skills-visibility.json)",
            "result": "pass"
            if teammate_spec is not None and teammate_spec.skills
            else "fail",
            "detail": f"teammate skill seed={getattr(teammate_spec, 'skills', None)!r}",
        },
    ]

    checks.append(_call_roster_check("_validate_reserved_names", spec._validate_reserved_names))
    checks.append(_call_roster_check("_validate_hitt_consistency", spec._validate_hitt_consistency))
    checks.append(_call_roster_check("_validate_bridge_consistency", spec._validate_bridge_consistency))
    return checks


def main() -> int:
    payload = build_team_config()
    spec = TeamAgentSpec.model_validate(payload)

    checks = run_checks(spec)
    failed = [c for c in checks if c["result"] != "pass"]

    summary = {
        "scope": "offline TeamAgentSpec compilation + roster validation; no Runner, no model call, no network",
        "baseline": "openjiuwen agent-core @ 6df9699e650b3ad9b9835b46ae3c3b7572adb04b",
        "team_name": spec.team_name,
        "member_count": 1 + len(spec.predefined_members),
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "checks": checks,
    }
    RESULTS_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for c in checks:
        mark = "PASS" if c["result"] == "pass" else "FAIL"
        print(f"[{mark}] {c['check']}")
        if c["detail"]:
            print(f"       {c['detail']}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed -> {RESULTS_PATH}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
