"""The workbench's agent template: app tools in, harness capabilities out.

The framework's default member template is built for a trusted local operator:
it grants a local sys_operation (shell, files, code) and registers the whole
harness tool set. The first real round measured 29 tools in front of the
leader. That is fine on a laptop and unacceptable behind a public login, where
the model's input is whatever a judge (or a retrieved document) contains.

This module is additive: it starts from the application's own MVP config in
``app/team_config.py`` and overrides the two agent templates. The P0-B fixture
still checks that the config chain compiles the same roster.

What the template changes:

- ``enable_sys_operation=False`` on both roles removes the shell/file/code
  tools at build time (``DeepAgentSpec.enable_sys_operation``).
- both roles get ``app.submit_task_result``, the only way a member can turn
  work into a checked result;
- the leader additionally gets ``app.run_bootstrap``, which submits the
  validated task graph once before the first task iteration.

The provider types are resolved at build time; calling this function without
``app.hooks.register_providers()`` produces a config that compiles but cannot
build a team.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping


def _model_defaults(credentials: Mapping[str, str]) -> list[dict[str, Any]]:
    """One shared model entry in the framework's own model_client_config shape."""
    return [
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


def build_workbench_config(
    credentials: Mapping[str, str],
    *,
    team_name: str,
    run_id: str,
) -> dict[str, Any]:
    """Return the MVP team pointed at ``credentials`` with the hardened templates.

    The caller's mapping is not retained: credentials are read into the model
    entry and everything else is a fresh deepcopy.
    """
    from app.team_config import TEAM, build_mvp_config

    config = copy.deepcopy(build_mvp_config())
    team_config = config["modes"]["team"][TEAM]
    team_config["team_name"] = team_name

    # The MVP never uses these capabilities; stating them here keeps the
    # template honest even if the fixture's defaults change.
    team_config["enable_hitt"] = False
    team_config["enable_bridge"] = False
    team_config["enable_swarmflow"] = False
    team_config["external_cli_agents"] = []

    config["models"] = {"defaults": _model_defaults(credentials)}

    submit_tool = {"type": "app.submit_task_result", "params": {"run_id": run_id}}
    bootstrap_rail = {"type": "app.run_bootstrap", "params": {"run_id": run_id}}

    config["agents"]["agent_leader"] = {
        "workspace": {"stable_base": True},
        "enable_sys_operation": False,
        "tools": [copy.deepcopy(submit_tool)],
        "rails": [copy.deepcopy(bootstrap_rail)],
    }
    config["agents"]["agent_teammate"] = {
        "workspace": {"stable_base": True},
        "enable_sys_operation": False,
        "tools": [copy.deepcopy(submit_tool)],
    }
    return config
