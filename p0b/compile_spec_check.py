"""P0-B (part 1): the app-side spec compiler.

Verifies ``app.config_adapter.compile_team_spec``:

- the leader desc/prompt reach the spec (repairs gap O1 from P0-A part 3)
- the leader ``model_name`` reaches it too -- the chain drops that as well
- ``persona`` still works as a legacy ``desc`` fallback
- a config with no identity fields keeps the framework defaults
- the roster / mode invariants from P0-A still hold through the adapter
- the compiler is pure: the caller's config is never mutated
- template selection follows the loader, including the legacy top-level shape
- a config that omits ``team_mode`` is rejected instead of silently allowing spawn

No Runner, no model call, no network.

Run: .venv/Scripts/python.exe p0b/compile_spec_check.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config_adapter import (  # noqa: E402
    SpecCompilationError,
    compile_team_spec,
    leader_identity_overrides,
    validate_roster_closed,
)
from checks.harness import Checks, call_ok, raises  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402

TEAM = "jiuwenswarm_mvp"
RESULTS_PATH = Path(__file__).resolve().parent / "compile_spec_results.json"

MODEL_DEFAULTS = [
    {
        "model_client_config": {
            "model_name": "mock-model",
            "client_provider": "OpenAI",
            "api_base": "https://example.invalid/v1",
            "api_key": "mock-api-key",
        },
        "model_config_obj": {"temperature": 0.7},
        "is_default": True,
    }
]

LEADER = {
    "member_name": "team_leader",
    "display_name": "Team Leader",
    "desc": "Plans the work and owns the final judgement.",
    "prompt": "Never mark an unverified claim as fact.",
    "model_name": "mock-model",
}

RESEARCH = {
    "member_name": "research",
    "display_name": "Research",
    "role_type": "teammate",
    "desc": "Collects evidence and reports unknowns.",
    "prompt": "Use only retrieved, citable material.",
}
ENGINEERING = {
    "member_name": "engineering",
    "display_name": "Engineering",
    "role_type": "teammate",
    "desc": "Turns validated findings into an engineering plan.",
    "prompt": "Propose interfaces and verifiable steps.",
}


def _agents_templates() -> dict:
    return {
        "agent_leader": {"workspace": {"stable_base": True}},
        "agent_teammate": {"workspace": {"stable_base": True}},
    }


def build_config() -> dict:
    """Return the MVP team in JiuwenSwarm's ``modes.team`` shape."""
    return {
        "preferred_language": "zh",
        "models": {"defaults": copy.deepcopy(MODEL_DEFAULTS)},
        "agents": _agents_templates(),
        "modes": {
            "team": {
                TEAM: {
                    "team_name": TEAM,
                    "lifecycle": "persistent",
                    "spawn_mode": "inprocess",
                    "dispatch_mode": "scheduled",
                    "team_mode": "predefined",
                    "enable_swarmflow": False,
                    "external_cli_agents": [],
                    "agents": {"leader": "$agent_leader", "teammate": "$agent_teammate"},
                    "leader": copy.deepcopy(LEADER),
                    "predefined_members": [copy.deepcopy(RESEARCH), copy.deepcopy(ENGINEERING)],
                }
            }
        },
    }


def build_legacy_config() -> dict:
    """Return the same team in the legacy top-level shape (no ``modes.team``)."""
    return {
        "preferred_language": "zh",
        "models": {"defaults": copy.deepcopy(MODEL_DEFAULTS)},
        "team_name": "legacy_mvp",
        "lifecycle": "persistent",
        "spawn_mode": "inprocess",
        "dispatch_mode": "scheduled",
        "team_mode": "predefined",
        "agents": {
            "leader": {"workspace": {"stable_base": True}},
            "teammate": {"workspace": {"stable_base": True}},
        },
        "leader": copy.deepcopy(LEADER),
        "predefined_members": [copy.deepcopy(RESEARCH), copy.deepcopy(ENGINEERING)],
    }


def main() -> int:
    checks = Checks()
    config = build_config()
    original = copy.deepcopy(config)

    spec = compile_team_spec(config)

    checks.add(
        "config compiles into a real TeamAgentSpec",
        isinstance(spec, TeamAgentSpec),
        f"team_name={spec.team_name!r}",
    )
    checks.add(
        "leader desc reaches the spec (repairs gap O1)",
        spec.leader.desc == LEADER["desc"],
        f"leader.desc={spec.leader.desc!r}",
    )
    checks.add(
        "leader prompt reaches the spec",
        spec.leader.prompt == LEADER["prompt"],
        f"leader.prompt={spec.leader.prompt!r}",
    )
    checks.add(
        "leader model_name reaches the spec (the chain drops this too)",
        spec.leader.model_name == "mock-model",
        f"leader.model_name={spec.leader.model_name!r}",
    )
    checks.add(
        "teammate desc/prompt survive the chain",
        len(spec.predefined_members) == 2 and all(m.desc and m.prompt for m in spec.predefined_members),
        "teammates=" + ",".join(f"{m.member_name}:{bool(m.desc)}/{bool(m.prompt)}" for m in spec.predefined_members),
    )
    checks.add(
        "roster is three members (R02)",
        len([spec.leader, *spec.predefined_members]) == 3,
        "members=" + ",".join(m.member_name for m in [spec.leader, *spec.predefined_members]),
    )
    checks.add(
        "mode invariants hold through the adapter",
        spec.team_mode == "predefined" and spec.dispatch_mode == "scheduled",
        f"team_mode={spec.team_mode!r} dispatch_mode={spec.dispatch_mode!r}",
    )
    checks.add(
        "the compiler never mutates the caller's config",
        config == original,
        "deepcopy comparison",
    )

    # --- legacy top-level shape (the old selector missed this) --------------
    legacy_spec = compile_team_spec(build_legacy_config())
    checks.add(
        "leader repair also works for a legacy top-level team config",
        legacy_spec.leader.desc == LEADER["desc"] and legacy_spec.leader.model_name == "mock-model",
        f"team_name={legacy_spec.team_name!r} leader.desc={legacy_spec.leader.desc!r}",
    )

    # --- persona fallback ---------------------------------------------------
    persona_config = build_config()
    persona_config["modes"]["team"][TEAM]["leader"] = {
        "member_name": "team_leader",
        "display_name": "Team Leader",
        "persona": "天才项目管理专家",
    }
    persona_spec = compile_team_spec(persona_config)
    checks.add(
        "persona is used as the desc fallback when desc is absent",
        persona_spec.leader.desc == "天才项目管理专家",
        f"leader.desc={persona_spec.leader.desc!r}",
    )
    checks.add(
        "persona fallback leaves no prompt behind",
        persona_spec.leader.prompt == "",
        f"leader.prompt={persona_spec.leader.prompt!r}",
    )

    # --- blank identity keeps framework defaults ----------------------------
    blank_config = build_config()
    blank_config["modes"]["team"][TEAM]["leader"] = {
        "member_name": "team_leader",
        "display_name": "Team Leader",
    }
    blank_spec = compile_team_spec(blank_config)
    checks.add(
        "a blank leader identity keeps the framework default desc",
        bool(blank_spec.leader.desc) and blank_spec.leader.desc != LEADER["desc"],
        f"leader.desc={blank_spec.leader.desc!r}",
    )

    # --- roster closure guard ----------------------------------------------
    no_mode = build_config()
    removed = no_mode["modes"]["team"][TEAM].pop("team_mode")
    rejected, detail = raises(compile_team_spec, no_mode, expected=SpecCompilationError)
    checks.add(
        "a config without team_mode is rejected instead of silently allowing spawn",
        rejected,
        f"(was {removed!r}) {detail}",
    )

    empty_roster = build_config()
    empty_roster["modes"]["team"][TEAM]["predefined_members"] = []
    rejected, detail = raises(compile_team_spec, empty_roster, expected=SpecCompilationError)
    checks.add("an empty predefined_members roster is rejected", rejected, detail)

    checks.add(
        "validate=False lets a caller inspect a spec that would be rejected",
        compile_team_spec(no_mode, validate=False).team_mode is None,
        "",
    )
    checks.add("validate_roster_closed passes on a well-formed spec", call_ok(validate_roster_closed, spec), "")

    # --- template selection -------------------------------------------------
    multi = build_config()
    other = copy.deepcopy(multi["modes"]["team"][TEAM])
    other["team_name"] = "other_team"
    other["leader"] = {"member_name": "team_leader", "display_name": "Team Leader", "desc": "Other leader."}
    multi["modes"]["team"]["other_team"] = other
    picked = compile_team_spec(multi, template_id="other_team")
    checks.add(
        "template_id selects the requested template",
        picked.team_name == "other_team" and picked.leader.desc == "Other leader.",
        f"team_name={picked.team_name!r} leader.desc={picked.leader.desc!r}",
    )
    checks.add(
        "with no template_id the first declared template wins",
        compile_team_spec(multi).team_name == TEAM,
        f"team_name={compile_team_spec(multi).team_name!r}",
    )

    # --- helper behaves on its own -----------------------------------------
    checks.add(
        "leader_identity_overrides drops blank values",
        leader_identity_overrides({"desc": "  ", "prompt": "keep"}) == {"prompt": "keep"},
        "",
    )

    return checks.report(
        RESULTS_PATH,
        scope="app-side spec compiler; no Runner, no model call, no network",
        baseline="workswarm 0.2.5.beta1; openjiuwen agent-core @ 6df9699e650b3",
    )


if __name__ == "__main__":
    sys.exit(main())
