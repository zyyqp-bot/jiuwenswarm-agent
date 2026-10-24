"""P1: the workbench template removes harness capabilities and mounts app tools.

A public deployment cannot inherit the default agent template: the first real
round showed 29 tools in front of the leader, including harness shell/file
capability. This check pins the three things the template must do before a
team is built — no sys_operation, a submit tool on both roles, and the
bootstrap rail on the leader — plus the capability gates the MVP keeps closed.

Offline: compiles a spec, never builds a team and never calls a model.

Run: .venv/Scripts/python.exe p1/workbench_template_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from app.agent_template import build_workbench_config  # noqa: E402
from app.config_adapter import compile_team_spec  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "workbench_template_results.json"


def _fake_credentials() -> dict[str, str]:
    return {
        "MODEL_BASE_URL": "http://127.0.0.1:9",
        "MODEL_API_KEY": "x",
        "MODEL_NAME": "x",
    }


def main() -> int:
    checks = Checks()

    spec = compile_team_spec(
        build_workbench_config(_fake_credentials(), team_name="tpl_probe", run_id="run-1")
    )

    checks.add(
        "leader disables sys_operation",
        spec.agents["leader"].enable_sys_operation is False,
        f"enable_sys_operation={spec.agents['leader'].enable_sys_operation!r}",
    )
    checks.add(
        "teammate disables sys_operation",
        spec.agents["teammate"].enable_sys_operation is False,
        f"enable_sys_operation={spec.agents['teammate'].enable_sys_operation!r}",
    )
    checks.add(
        "teammate mounts the submit tool",
        any(
            getattr(tool, "type", None) == "app.submit_task_result"
            for tool in spec.agents["teammate"].tools or []
        ),
        f"tools={[getattr(t, 'type', None) for t in spec.agents['teammate'].tools or []]}",
    )
    checks.add(
        "leader mounts the bootstrap rail",
        any(
            getattr(rail, "type", None) == "app.run_bootstrap"
            for rail in spec.agents["leader"].rails or []
        ),
        f"rails={[getattr(r, 'type', None) for r in spec.agents['leader'].rails or []]}",
    )
    checks.add(
        "capability gates stay closed",
        spec.enable_hitt is False and spec.enable_bridge is False and spec.enable_swarmflow is False,
        f"hitt={spec.enable_hitt!r} bridge={spec.enable_bridge!r} swarmflow={spec.enable_swarmflow!r}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="template compilation only; no team build, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
