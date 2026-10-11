"""P0-A (part 3): compile the MVP team through the JiuwenSwarm config chain.

Parts 1-2 built the TeamAgentSpec from a hand-written agent-core payload. This
part closes the gap with PROJECT.md's design decision D01 ("integrate through a
thin adapter over JiuwenSwarm's team config"): the spec must come from
JiuwenSwarm's own YAML-shaped config via ``load_team_spec_dict``.

``load_team_spec_dict`` is a pure dict compiler -- it reads config, resolves
``$agent`` references, and returns a TeamAgentSpec-shaped dict. No Runner, no
model instantiation, no network.

It also records what the chain does NOT carry. ``_build_leader_spec`` emits only
``member_name`` / ``display_name`` / ``name`` / ``persona``, and ``LeaderSpec``
has no ``persona`` field -- so a leader desc/prompt written in YAML never
reaches the spec. Teammates keep theirs because predefined_members are copied
verbatim. This is exactly the "check the final compiled Spec" rule from
PROJECT.md section 9.

Baseline: workswarm 0.2.5.beta1; openjiuwen agent-core @ 6df9699e650b3
Run:      .venv/Scripts/python.exe p0a/swarm_config_chain_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from checks.harness import Checks  # noqa: E402
from jiuwenswarm.agents.harness.team.config_loader import load_team_spec_dict  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402

TEAM = "jiuwenswarm_mvp"
RESULTS_PATH = Path(__file__).resolve().parent / "swarm_config_chain_results.json"

# Placeholder credentials: this script never resolves a model client.
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


def build_swarm_config() -> dict:
    """Return the MVP team in JiuwenSwarm's config.yaml shape."""
    return {
        "preferred_language": "zh",
        "models": {"defaults": MODEL_DEFAULTS},
        "agents": {
            "agent_leader": {
                "workspace": {"stable_base": True},
                "max_iterations": 200,
                "completion_timeout": 6000.0,
            },
            "agent_research": {
                "workspace": {"stable_base": True},
                "max_iterations": 200,
                "completion_timeout": 6000.0,
                "skills": ["research", "fact_checking"],
            },
            "agent_engineering": {
                "workspace": {"stable_base": True},
                "max_iterations": 200,
                "completion_timeout": 6000.0,
                "skills": ["software_engineering", "code_review"],
            },
        },
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
                    "agents": {"leader": "$agent_leader", "teammate": "$agent_research"},
                    "leader": {
                        "member_name": "team_leader",
                        "display_name": "Team Leader",
                        # These two are silently dropped by _build_leader_spec.
                        "desc": "Plans the work, assigns tasks and owns the final judgement.",
                        "prompt": "Never mark an unverified claim as fact.",
                    },
                    "predefined_members": [
                        {
                            "member_name": "research",
                            "display_name": "Research",
                            "role_type": "teammate",
                            "desc": "Collects evidence, checks facts and reports unknowns.",
                            "prompt": "Use only retrieved, citable material.",
                        },
                        {
                            "member_name": "engineering",
                            "display_name": "Engineering",
                            "role_type": "teammate",
                            "desc": "Turns validated findings into an actionable engineering plan.",
                            "prompt": "Propose interfaces and verifiable steps.",
                        },
                    ],
                }
            }
        },
    }


def main() -> int:
    checks = Checks()
    config = build_swarm_config()
    yaml_leader = config["modes"]["team"][TEAM]["leader"]

    spec_dict = load_team_spec_dict(config_base=config)

    checks.add(
        "load_team_spec_dict returns a TeamAgentSpec-shaped dict",
        isinstance(spec_dict, dict) and "agents" in spec_dict,
        f"keys={sorted(spec_dict)[:8]}...",
    )
    checks.add(
        "$agent references resolve into concrete agent templates with a model",
        all("model" in spec_dict["agents"][key] for key in ("leader", "teammate")),
        f"agent keys={sorted(spec_dict['agents'])}",
    )
    checks.add(
        "team_mode survives the chain as 'predefined'",
        spec_dict.get("team_mode") == "predefined",
        f"team_mode={spec_dict.get('team_mode')!r}",
    )
    checks.add(
        "dispatch_mode survives the chain as 'scheduled'",
        spec_dict.get("dispatch_mode") == "scheduled",
        f"dispatch_mode={spec_dict.get('dispatch_mode')!r}",
    )

    spec = TeamAgentSpec.model_validate(spec_dict)
    checks.add(
        "the compiled dict validates into a real TeamAgentSpec",
        isinstance(spec, TeamAgentSpec),
        f"team_name={spec.team_name!r}",
    )
    checks.add(
        "roster is three members again after the config chain (R02)",
        len([spec.leader, *spec.predefined_members]) == 3,
        "members=" + ",".join(m.member_name for m in [spec.leader, *spec.predefined_members]),
    )
    checks.add(
        "teammate desc/prompt survive the chain (predefined_members are copied verbatim)",
        all(m.desc and m.prompt for m in spec.predefined_members),
        "teammates=" + ",".join(
            f"{m.member_name}:desc={bool(m.desc)},prompt={bool(m.prompt)}" for m in spec.predefined_members
        ),
    )

    # --- what the chain drops ----------------------------------------------
    leader_desc_kept = spec.leader.desc == yaml_leader["desc"]
    leader_prompt_kept = spec.leader.prompt == yaml_leader["prompt"]
    checks.add(
        "gap confirmed: leader desc/prompt are NOT carried by the config chain",
        not leader_desc_kept and not leader_prompt_kept,
        f"spec leader desc={spec.leader.desc!r} prompt={spec.leader.prompt!r}",
    )

    observations = [
        {
            "id": "O1",
            "observation": (
                "Leader desc/prompt written in YAML do not reach the spec: _build_leader_spec emits only "
                "member_name/display_name/name/persona, and LeaderSpec has no persona field. The adapter "
                "must set the leader's desc/prompt on the compiled spec explicitly."
            ),
            "leader_desc_kept": leader_desc_kept,
            "leader_prompt_kept": leader_prompt_kept,
            "spec_leader_desc": spec.leader.desc,
            "spec_leader_prompt": spec.leader.prompt,
        },
        {
            "id": "O2",
            "observation": (
                "enable_hitt defaults to True in load_team_spec_dict even though the config does not set it. "
                "With team_mode='predefined' the dynamic spawn_* tools are removed at build time, so the MVP "
                "roster stays closed; the flag alone does not reopen it."
            ),
            "spec_enable_hitt": spec.enable_hitt,
            "spec_team_mode": spec.team_mode,
        },
    ]

    for note in observations:
        print(f"[NOTE] {note['id']}: {note['observation']}")
    print()

    return checks.report(
        RESULTS_PATH,
        scope="JiuwenSwarm config chain -> TeamAgentSpec; no Runner, no model call, no network",
        baseline="workswarm 0.2.5.beta1; openjiuwen agent-core @ 6df9699e650b3",
        observations=observations,
    )


if __name__ == "__main__":
    sys.exit(main())
