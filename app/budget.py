"""Budget manager: one ledger for every model call in a run.

PROJECT.md section 10 requires the budget to be a single account covering the
leader, the members, the refiner, the aggregator, compression and memory
extraction -- not just whatever the framework happens to meter -- and it fixes
the rules this module implements:

- **reserve before the call, settle after it**: a call is charged before it
  happens, so two members cannot both spend the last of the budget;
- **release on failure**, so a call that never ran does not consume anything;
- **keep a finalization reserve**, because a run that spends everything on work
  and nothing on aggregation cannot deliver;
- **stop new work at the deadline**, alongside the call and token ceilings.

It also refuses to record a call with no provider usage as a zero-cost success:
such a call settles as ``unknown`` and its tokens are carried as an unknown
quantity rather than as zero.

Ordinary work and the finalization reserve are accounted **separately**. If the
reserve were merely subtracted from a shared pot, a finalization reservation
would also count as ordinary spending and ordinary work would be denied budget
it owns -- and the outcome would depend on which kind of reservation happened
first.

Every method is synchronous and free of ``await``, so in a single-threaded
asyncio run the check-then-reserve step cannot interleave with another
coroutine's. That is what makes the reservation atomic here.

It starts no runtime and calls no model.
"""
from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

__all__ = ["BudgetExceeded", "BudgetManager", "Reservation", "UsageRecord"]

_QUALITIES = ("provider", "estimated", "unknown")


class BudgetExceeded(RuntimeError):
    """A reservation would take the run past its budget or its deadline."""


class Reservation(BaseModel):
    """Budget held for a call that is about to happen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    calls: int
    tokens: int
    finalization: bool = False


class UsageRecord(BaseModel):
    """One settled call, and how trustworthy its numbers are."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    calls: int
    tokens: int
    quality: str


class BudgetManager:
    """Track what a run has reserved, spent and kept in reserve."""

    def __init__(
        self,
        *,
        max_model_calls: int,
        max_tokens: int,
        finalization_reserve_tokens: int = 0,
        deadline_at: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_model_calls < 0:
            raise ValueError("max_model_calls cannot be negative")
        if max_tokens < 0:
            raise ValueError("max_tokens cannot be negative")
        if not 0 <= finalization_reserve_tokens <= max_tokens:
            raise ValueError("the finalization reserve must be between 0 and the whole token budget")

        self._max_calls = max_model_calls
        self._max_tokens = max_tokens
        self._finalization_reserve = finalization_reserve_tokens
        self._deadline_at = deadline_at
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))

        self._calls_used = 0
        self._calls_reserved = 0

        self._ordinary_used_tokens = 0
        self._ordinary_reserved_tokens = 0
        self._finalization_used_tokens = 0
        self._finalization_reserved_tokens = 0

        self._counter = itertools.count(1)
        self._open: dict[str, Reservation] = {}
        self._records: list[UsageRecord] = []

    # -- state --------------------------------------------------------------
    @property
    def calls_remaining(self) -> int:
        return self._max_calls - self._calls_used - self._calls_reserved

    @property
    def tokens_remaining(self) -> int:
        """Tokens available to ordinary work.

        May go negative if a provider reported more than was reserved for it:
        the call already happened, so the overspend is shown rather than
        hidden.
        """
        ordinary_ceiling = self._max_tokens - self._finalization_reserve
        return ordinary_ceiling - self._ordinary_used_tokens - self._ordinary_reserved_tokens

    @property
    def finalization_tokens_remaining(self) -> int:
        """Tokens available to the aggregator and other finalization work."""
        return self._finalization_reserve - self._finalization_used_tokens - self._finalization_reserved_tokens

    @property
    def is_overrun(self) -> bool:
        """True when settlement took the run past a ceiling."""
        return self.tokens_remaining < 0 or self.finalization_tokens_remaining < 0

    def usage(self) -> tuple[UsageRecord, ...]:
        """Return every settled call, oldest first."""
        return tuple(self._records)

    def usage_quality(self) -> str:
        """Return the weakest measurement quality seen in this run.

        A run is only as well-measured as its least-measured call, so one
        ``unknown`` makes the whole run ``unknown`` -- reporting ``provider``
        because most calls were metered would overstate the account.
        """
        if not self._records:
            return "unknown"
        qualities = {record.quality for record in self._records}
        for quality in reversed(_QUALITIES):  # weakest first
            if quality in qualities:
                return quality
        return "unknown"

    # -- the ledger ---------------------------------------------------------
    def reserve(self, *, calls: int = 1, tokens: int = 0, finalization: bool = False) -> Reservation:
        """Hold budget for a call about to be made.

        Raises:
            BudgetExceeded: the reservation would exceed what is available, or
                the run's deadline has passed.
        """
        if calls < 1:
            raise ValueError("a reservation must cover at least one call")
        if tokens < 0:
            raise ValueError("a reservation cannot hold a negative token count")

        self._check_deadline()

        if calls > self.calls_remaining:
            raise BudgetExceeded(
                f"reserving {calls} call(s) would exceed the budget: {self.calls_remaining} remaining"
            )

        available = self.finalization_tokens_remaining if finalization else self.tokens_remaining
        if tokens > available:
            pool = "finalization" if finalization else "ordinary"
            raise BudgetExceeded(
                f"reserving {tokens} token(s) would exceed the {pool} budget: {max(0, available)} available"
            )

        reservation = Reservation(
            reservation_id=f"res-{next(self._counter)}",
            calls=calls,
            tokens=tokens,
            finalization=finalization,
        )
        self._open[reservation.reservation_id] = reservation
        self._calls_reserved += calls
        if finalization:
            self._finalization_reserved_tokens += tokens
        else:
            self._ordinary_reserved_tokens += tokens
        return reservation

    def settle(
        self,
        reservation_id: str,
        *,
        tokens: int | None = None,
        quality: str = "unknown",
    ) -> UsageRecord:
        """Charge a completed call, replacing the reservation with what it used.

        ``tokens`` is the provider's figure when there is one. When there is
        not, the reservation's own estimate is kept and ``quality`` says so --
        the call is never recorded as free.

        Arguments are validated **before** the reservation is consumed, so a
        malformed settlement leaves the ledger untouched rather than quietly
        freeing the budget for someone else to spend.

        Raises:
            ValueError: the quality is unknown, the count is negative, or
                provider quality was claimed without a provider figure.
            KeyError: the reservation is unknown or already settled.
        """
        if quality not in _QUALITIES:
            raise ValueError(f"unknown usage quality {quality!r}; expected one of {_QUALITIES}")
        if tokens is not None and tokens < 0:
            raise ValueError("a settled token count cannot be negative")
        if tokens is None and quality == "provider":
            raise ValueError("a provider-quality settlement must supply the provider's token count")

        reservation = self._take(reservation_id)
        used = reservation.tokens if tokens is None else tokens

        self._calls_used += reservation.calls
        if reservation.finalization:
            self._finalization_used_tokens += used
        else:
            self._ordinary_used_tokens += used

        record = UsageRecord(
            reservation_id=reservation_id,
            calls=reservation.calls,
            tokens=used,
            quality=quality,
        )
        self._records.append(record)
        return record

    def release(self, reservation_id: str) -> None:
        """Return a reservation that never became a call.

        Raises:
            KeyError: the reservation is unknown or already settled.
        """
        self._take(reservation_id)

    # -- helpers ------------------------------------------------------------
    def _check_deadline(self) -> None:
        if self._deadline_at is not None and self._clock() >= self._deadline_at:
            raise BudgetExceeded(f"the run's deadline ({self._deadline_at.isoformat()}) has passed")

    def _take(self, reservation_id: str) -> Reservation:
        try:
            reservation = self._open.pop(reservation_id)
        except KeyError as exc:
            raise KeyError(f"no open reservation {reservation_id!r}") from exc
        self._calls_reserved -= reservation.calls
        if reservation.finalization:
            self._finalization_reserved_tokens -= reservation.tokens
        else:
            self._ordinary_reserved_tokens -= reservation.tokens
        return reservation
