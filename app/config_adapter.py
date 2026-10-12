"""Compile a JiuwenSwarm team config into a validated ``TeamAgentSpec``.

This is the adapter boundary from PROJECT.md D01. It delegates the actual
compilation to JiuwenSwarm's own ``load_team_spec_dict`` and then repairs what
that chain is known not to carry.

``_build_leader_spec`` emits only ``member_name`` / ``display_name`` / ``name`` /
``persona``, so every other ``LeaderSpec`` field written under ``leader:`` in
YAML is dropped -- notably ``desc``, ``prompt`` and ``model_name``. Teammates are
unaffected: ``predefined_members`` are copied verbatim. We re-apply those fields
here, keeping ``persona`` only as a legacy fallback for ``desc`` (PROJECT.md
section 9 requires the public ``desc`` / private ``prompt`` split).

The compiler never starts a Runner, instantiates a model, or touches the
network.

Baseline: workswarm 0.2.5.beta1; openjiuwen agent-core @ 6df9699e650b3
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from jiuwenswarm.agents.harness.team.config_loader import (
    _select_modes_team,
    load_team_spec_dict,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

__all__ = [
    "SpecCompilationError",
    "compile_team_spec",
    "leader_identity_overrides",
    "validate_roster_closed",
]

# ``LeaderSpec`` fields the config chain discards. ``member_name`` and
# ``display_name`` are handled by ``_build_leader_spec`` itself; everything else
# inherited from ``MemberSpecBase`` has to be restored here.
_LEADER_PASSTHROUGH_FIELDS = ("desc", "prompt", "model_name")


class SpecCompilationError(RuntimeError):
    """Raised when a compiled spec violates an MVP invariant."""


def _select_team_raw(config_base: dict[str, Any], *, template_id: str | None = None) -> dict[str, Any]:
    """Return a copy of the team template block the compiler actually used.

    Delegates to the loader's own selector instead of reimplementing it. A
    hand-written selector previously drifted from the real one -- it missed the
    legacy top-level ``team:`` fallback, so for those configs the leader repair
    below was silently skipped. ``_select_modes_team`` is private upstream API,
    consumed deliberately at the pinned baseline in source-lock.json.
    """
    _, team_raw = _select_modes_team(config_base, template_id=template_id)
    return deepcopy(team_raw) if isinstance(team_raw, dict) else {}


def leader_identity_overrides(leader_raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the leader fields the config chain drops.

    Only non-empty values are returned, so the framework defaults stay in place
    when the config says nothing.
    """
    overrides: dict[str, Any] = {}
    for field in _LEADER_PASSTHROUGH_FIELDS:
        value = leader_raw.get(field)
        if isinstance(value, str):
            value = value.strip()
        if value:
            overrides[field] = value

    if "desc" not in overrides:
        persona = leader_raw.get("persona")
        persona = persona.strip() if isinstance(persona, str) else ""
        if persona:
            overrides["desc"] = persona
    return overrides


def validate_roster_closed(spec: TeamAgentSpec) -> None:
    """Fail fast when the compiled spec does not hold the MVP roster invariant (R02).

    Roster closure needs **every** capability gate shut, not just
    ``team_mode="predefined"``. The leader's spawn tools are stripped by more
    than one mechanism, and each keeps a different one alive:

    - ``team_mode="predefined"`` removes four of them -- ``spawn_teammate``,
      ``spawn_human_agent``, ``spawn_bridge_agent``, ``spawn_external_cli``
      (``agent_configurator.py``);
    - ``spawn_passive_human`` is **not** in that list. It is gated on
      ``enable_hitt`` (``tool_factory.py``), so leaving HITT open keeps it, and
      a leader that can spawn a human can still grow the roster;
    - ``enable_bridge`` / ``external_cli_agents`` / ``enable_swarmflow`` gate
      the remaining capability ceilings the same way.

    Checking only ``team_mode`` was the earlier mistake: it reads as though the
    roster is closed while ``spawn_passive_human`` remains callable. Note that
    the loader defaults ``enable_hitt`` to True, so this must be closed
    explicitly rather than assumed.
    """
    if spec.team_mode != "predefined":
        raise SpecCompilationError(
            f"team_mode must be explicitly 'predefined' for the MVP (got {spec.team_mode!r}); "
            "any other value keeps the leader's dynamic spawn tools and breaks R02"
        )
    if not spec.predefined_members:
        raise SpecCompilationError("the MVP roster requires at least one predefined teammate")

    kept_alive = {
        "enable_hitt": spec.enable_hitt,
        "enable_bridge": spec.enable_bridge,
        "enable_swarmflow": spec.enable_swarmflow,
        "external_cli_agents": bool(spec.external_cli_agents),
    }
    open_gates = [name for name, is_open in kept_alive.items() if is_open]
    if open_gates:
        raise SpecCompilationError(
            "these capability gates keep a spawn tool alive even under "
            f"team_mode='predefined': {', '.join(sorted(open_gates))}. "
            "In particular enable_hitt keeps spawn_passive_human, which is not in the "
            "predefined exclude list"
        )


def compile_team_spec(
    config_base: dict[str, Any],
    *,
    template_id: str | None = None,
    validate: bool = True,
) -> TeamAgentSpec:
    """Compile a team config into a validated ``TeamAgentSpec``.

    The caller's ``config_base`` is never mutated: both ``load_team_spec_dict``
    and ``_select_team_raw`` work on copies.

    Args:
        config_base: JiuwenSwarm-shaped config (``modes.team`` / ``agents`` / ...).
        template_id: Team template to compile; the first declared one otherwise.
        validate: Run :func:`validate_roster_closed` on the result.
    """
    spec_dict = load_team_spec_dict(config_base=config_base, template_id=template_id)
    spec = TeamAgentSpec.model_validate(spec_dict)

    # The MVP uses none of these capabilities (PROJECT.md D02: inprocess,
    # persistent, predefined, scheduled), and the loader's defaults are not
    # neutral: ``enable_hitt`` defaults to True, which keeps
    # ``spawn_passive_human`` callable even under ``team_mode="predefined"``
    # because that tool is not in the predefined exclude list. R02 needs the
    # roster closed, so the gates are shut here rather than inherited.
    spec = spec.model_copy(
        update={
            "enable_hitt": False,
            "enable_bridge": False,
            "enable_swarmflow": False,
            "external_cli_agents": [],
        }
    )

    leader_raw = _select_team_raw(config_base, template_id=template_id).get("leader")
    if isinstance(leader_raw, dict):
        overrides = leader_identity_overrides(leader_raw)
        if overrides:
            spec = spec.model_copy(update={"leader": spec.leader.model_copy(update=overrides)})

    if validate:
        validate_roster_closed(spec)
    return spec
