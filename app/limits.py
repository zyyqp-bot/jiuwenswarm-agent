"""Admission control for a public link.

Cost is bounded by refusing work before it starts: a global cap protects the
container, a per-session cap protects one judge from consuming every slot, and
a daily quota bounds a scripted account. Each refusal carries the limit, the
current count and the HTTP status the API should return.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LimitExceeded", "Limits"]


class LimitExceeded(RuntimeError):
    """A run was refused by an admission limit."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class Limits:
    """The three admission limits, checked before a run is created."""

    max_concurrent_global: int
    max_concurrent_per_session: int
    max_runs_per_day: int

    def __post_init__(self) -> None:
        for name in (
            "max_concurrent_global",
            "max_concurrent_per_session",
            "max_runs_per_day",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")

    def check_start(
        self,
        *,
        running_global: int,
        running_session: int,
        runs_today: int,
    ) -> None:
        """Raise unless all three counts are below their limits."""
        if running_global >= self.max_concurrent_global:
            raise LimitExceeded(
                "global_capacity",
                f"global concurrency limit {self.max_concurrent_global} reached "
                f"(running {running_global}); try again when a run finishes",
                503,
            )
        if running_session >= self.max_concurrent_per_session:
            raise LimitExceeded(
                "session_capacity",
                f"this session already has {running_session} running run(s); "
                f"the limit is {self.max_concurrent_per_session}",
                429,
            )
        if runs_today >= self.max_runs_per_day:
            raise LimitExceeded(
                "daily_quota",
                f"daily run quota {self.max_runs_per_day} is exhausted "
                f"({runs_today} started in the last 24 hours)",
                429,
            )
