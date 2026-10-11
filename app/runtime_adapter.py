"""Runtime adapter: the boundary between a compiled spec and a live team.

PROJECT.md section 8 defines RuntimeAdapter as the component that turns a
validated ``TeamAgentSpec`` into a running team, and D01 keeps that behind a
thin adapter so the rest of the application never touches framework
construction directly.

This is the first slice: it builds the real ``TeamAgent`` from the spec and
checks the object actually carries the identity that was declared. It
deliberately does **not** start a run, spawn members, or call a model --
``build()`` constructs the leader's agent and its rails without any model call,
which is what makes this checkable offline.

Where the checks read from matters. ``TeamAgent.spec`` is the *same object* the
caller passed in (``build()`` hands ``self`` to the configurator), so comparing
``team.spec`` against the input spec proves nothing. The materialized values
live elsewhere:

- ``team.runtime_context`` -- ``member_name`` / ``desc`` / ``prompt`` / ``role``
  as the leader's agent actually received them,
- ``team.runtime_context.team_spec`` -- a *separate* ``TeamSpec`` with the
  runtime ``dispatch_mode``.

What is not verified here, and why: the leader's tool list is populated by rails
the harness registers lazily, so "``team_mode="predefined"`` removed the
``spawn_*`` tools" is not observable on a team that has never run. Note also
that ``TeamSpec`` has no ``team_mode`` field at all -- that switch only exists
on ``TeamAgentSpec`` and is consumed by the configurator at tool-construction
time. The removal check belongs with the first real run, not here.
"""
from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

if TYPE_CHECKING:  # the concrete type is heavy; keep it out of the runtime import
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent

__all__ = ["RuntimeBuildError", "build_team", "verify_identity"]


class RuntimeBuildError(RuntimeError):
    """The built team does not correspond to the spec it was built from."""


async def build_team(spec: TeamAgentSpec) -> "TeamAgent":
    """Build the live ``TeamAgent`` for a validated spec.

    PROJECT.md section 9 requires the final mounted configuration to be checked
    rather than assumed: the config chain already proved it can silently drop
    leader fields, so the same suspicion applies to the build step. On a
    mismatch the just-built team is disposed before the error propagates --
    otherwise the caller, who never received the object, could not release it.

    Raises:
        RuntimeBuildError: the built team's identity does not match the spec.
    """
    team = spec.build()
    try:
        verify_identity(team, spec)
    except RuntimeBuildError:
        await _dispose(team)
        raise
    return team


def verify_identity(team: "TeamAgent", spec: TeamAgentSpec) -> None:
    """Check that a built team carries the identity the spec declared."""
    if team.team_name != spec.team_name:
        raise RuntimeBuildError(f"built team is named {team.team_name!r}, spec says {spec.team_name!r}")

    context = team.runtime_context
    if context.member_name != spec.leader.member_name:
        raise RuntimeBuildError(
            f"built leader is {context.member_name!r}, spec says {spec.leader.member_name!r}"
        )
    if context.desc != spec.leader.desc:
        raise RuntimeBuildError(
            f"built leader desc is {context.desc!r}, spec says {spec.leader.desc!r}"
        )
    if context.prompt != spec.leader.prompt:
        raise RuntimeBuildError(
            f"built leader prompt is {context.prompt!r}, spec says {spec.leader.prompt!r}"
        )

    runtime_spec = context.team_spec
    if runtime_spec.dispatch_mode != spec.dispatch_mode:
        raise RuntimeBuildError(
            f"built team carries dispatch_mode {runtime_spec.dispatch_mode!r}, "
            f"spec says {spec.dispatch_mode!r}"
        )


async def _dispose(team: "TeamAgent") -> None:
    """Release a team the caller never received. Never raises."""
    for name in ("destroy_team", "shutdown_self"):
        method = getattr(team, name, None)
        if not callable(method):
            continue
        try:
            outcome = method()
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:  # disposal is best-effort on an already-failing path
            pass
        return
