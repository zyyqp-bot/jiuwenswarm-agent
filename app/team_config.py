"""The MVP team definition, owned by the application.

``p0b/compile_spec_check.py`` carries the same roster as a verification
fixture. Production code must not import a test script, so this module holds
the values the app ships with; the fixture remains the check that the chain
compiles them.

Keep the two in sync when the roster or prompts change. The template check in
``p1/workbench_template_check.py`` pins the parts the workbench depends on
(predefined roster, closed capability gates, hardened agent templates).
"""
from __future__ import annotations

import copy
from typing import Any

TEAM = "jiuwenswarm_mvp"

MODEL_DEFAULTS: list[dict[str, Any]] = [
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

ASSIGNABLE_MEMBERS: tuple[str, ...] = (RESEARCH["member_name"], ENGINEERING["member_name"])
"""Who the native scheduler may assign a task to.

The leader is excluded: ``TeamTaskManager._validate_task_owner`` and
``add_graph`` reject leader-assigned tasks, and the leader's job is
coordination. A payload may not widen this roster — the plan validator must
reject an assignee the scheduler would refuse, rather than failing later at
bootstrap.
"""


def agents_templates() -> dict[str, dict[str, Any]]:
    return {
        "agent_leader": {"workspace": {"stable_base": True}},
        "agent_teammate": {"workspace": {"stable_base": True}},
    }


def build_mvp_config() -> dict[str, Any]:
    """Return the MVP team in the framework's ``modes.team`` shape."""
    return {
        "preferred_language": "zh",
        "models": {"defaults": copy.deepcopy(MODEL_DEFAULTS)},
        "agents": agents_templates(),
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
