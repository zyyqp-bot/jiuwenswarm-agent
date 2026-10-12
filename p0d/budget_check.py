"""P0-D (part 3): the budget manager.

PROJECT.md section 10: reserve before the call, settle after it, release on
failure, keep a finalization reserve, stop at the deadline, and never record a
call with no provider usage as a zero-cost success.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0d/budget_check.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.budget import BudgetExceeded, BudgetManager  # noqa: E402
from checks.harness import Checks, call_ok, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "budget_results.json"


def main() -> int:
    checks = Checks()

    ledger = BudgetManager(max_model_calls=5, max_tokens=1000)
    checks.add(
        "a fresh budget reports everything available",
        ledger.calls_remaining == 5 and ledger.tokens_remaining == 1000,
        f"calls={ledger.calls_remaining} tokens={ledger.tokens_remaining}",
    )

    # --- reservation --------------------------------------------------------
    reservation = ledger.reserve(calls=1, tokens=100)
    checks.add(
        "a reservation reduces what is available",
        ledger.calls_remaining == 4 and ledger.tokens_remaining == 900,
        f"calls={ledger.calls_remaining} tokens={ledger.tokens_remaining}",
    )
    checks.add("a reservation is not yet usage", ledger.usage() == (), "")

    tight = BudgetManager(max_model_calls=1, max_tokens=100)
    tight.reserve(calls=1, tokens=100)
    ok, detail = raises(lambda: tight.reserve(calls=1, tokens=0), expected=BudgetExceeded)
    checks.add("a second reservation cannot take budget already held", ok, detail)
    ok, detail = raises(lambda: tight.reserve(calls=0), expected=ValueError)
    checks.add("a reservation covering no calls is refused", ok, detail)

    # --- settling -----------------------------------------------------------
    record = ledger.settle(reservation.reservation_id, tokens=80, quality="provider")
    checks.add(
        "settling charges what the call actually used, not what was reserved",
        record.tokens == 80 and ledger.tokens_remaining == 920 and ledger.calls_remaining == 4,
        f"tokens_remaining={ledger.tokens_remaining}",
    )
    ok, detail = raises(lambda: ledger.settle(reservation.reservation_id), expected=KeyError)
    checks.add("settling twice on one reservation is refused", ok, detail)

    # --- a malformed settlement must not corrupt the ledger -----------------
    intact = BudgetManager(max_model_calls=5, max_tokens=1000)
    held = intact.reserve(calls=1, tokens=300)
    tokens_before, calls_before = intact.tokens_remaining, intact.calls_remaining
    ok, detail = raises(lambda: intact.settle(held.reservation_id, quality="provider"), expected=ValueError)
    checks.add("a provider-quality settlement without a figure is refused", ok, detail)
    checks.add(
        "the refused settlement leaves the ledger exactly as it was",
        intact.tokens_remaining == tokens_before and intact.calls_remaining == calls_before,
        f"tokens={intact.tokens_remaining} calls={intact.calls_remaining}",
    )
    checks.add(
        "the reservation is still open and settles normally afterwards",
        call_ok(lambda: intact.settle(held.reservation_id, tokens=100, quality="provider"))
        and intact.tokens_remaining == 900,
        f"tokens_remaining={intact.tokens_remaining}",
    )

    ok, detail = raises(lambda: ledger.settle("res-nope"), expected=KeyError)
    checks.add("settling an unknown reservation is refused", ok, detail)

    # --- releasing ----------------------------------------------------------
    released = ledger.reserve(calls=1, tokens=50)
    before = ledger.tokens_remaining
    ledger.release(released.reservation_id)
    checks.add(
        "releasing returns the reservation to the pool",
        ledger.tokens_remaining == before + 50,
        f"before={before} after={ledger.tokens_remaining}",
    )
    checks.add(
        "a released reservation is not usage",
        all(item.reservation_id != released.reservation_id for item in ledger.usage()),
        "",
    )
    ok, detail = raises(lambda: ledger.release("res-nope"), expected=KeyError)
    checks.add("releasing an unknown reservation is refused", ok, detail)

    # --- quantities cannot be negative --------------------------------------
    ok, detail = raises(lambda: ledger.reserve(calls=1, tokens=-1000), expected=ValueError)
    checks.add("a reservation cannot hold a negative token count", ok, detail)
    ok, detail = raises(lambda: BudgetManager(max_model_calls=5, max_tokens=1000, finalization_reserve_tokens=-500),
                        expected=ValueError)
    checks.add("a negative finalization reserve is refused", ok, detail)
    ok, detail = raises(lambda: BudgetManager(max_model_calls=-1, max_tokens=1000), expected=ValueError)
    checks.add("a negative call ceiling is refused", ok, detail)

    negative = BudgetManager(max_model_calls=5, max_tokens=1000)
    held_neg = negative.reserve(calls=1, tokens=10)
    ok, detail = raises(
        lambda: negative.settle(held_neg.reservation_id, tokens=-500), expected=ValueError
    )
    checks.add("a negative settled count is refused rather than credited", ok, detail)
    checks.add(
        "the refused negative settlement left the reservation open",
        negative.tokens_remaining == 990,
        f"tokens_remaining={negative.tokens_remaining}",
    )

    # --- quality is validated ------------------------------------------------
    ok, detail = raises(
        lambda: negative.settle(held_neg.reservation_id, tokens=1, quality="PROVIDER"), expected=ValueError
    )
    checks.add("an unrecognised usage quality is refused, not silently downgraded", ok, detail)

    # --- unmeasured calls are not free -------------------------------------
    estimated = ledger.reserve(calls=1, tokens=200)
    unmeasured = ledger.reserve(calls=1, tokens=70)
    ledger.settle(estimated.reservation_id, quality="estimated")
    ledger.settle(unmeasured.reservation_id)
    checks.add(
        "a call with no provider figure settles at its estimate, never at zero",
        all(item.tokens > 0 for item in ledger.usage()),
        f"tokens={[item.tokens for item in ledger.usage()]}",
    )
    checks.add(
        "an unmeasured call is labelled unknown rather than provider",
        ledger.usage()[-1].quality == "unknown",
        f"quality={ledger.usage()[-1].quality}",
    )
    checks.add(
        "the run's quality is as weak as its weakest call",
        ledger.usage_quality() == "unknown",
        f"usage_quality={ledger.usage_quality()}",
    )

    measured = BudgetManager(max_model_calls=5, max_tokens=1000)
    first = measured.reserve(calls=1, tokens=10)
    measured.settle(first.reservation_id, tokens=10, quality="provider")
    second = measured.reserve(calls=1, tokens=10)
    measured.settle(second.reservation_id, quality="estimated")
    checks.add(
        "a run mixing provider and estimated measurement reports the weaker",
        measured.usage_quality() == "estimated",
        measured.usage_quality(),
    )

    # --- the finalization reserve is a separate pool ------------------------
    for label, finalization_first in (("finalization first", True), ("ordinary first", False)):
        pool = BudgetManager(max_model_calls=10, max_tokens=1000, finalization_reserve_tokens=200)
        if finalization_first:
            pool.reserve(calls=1, tokens=200, finalization=True)
            ordinary_ok = call_ok(lambda: pool.reserve(calls=1, tokens=800))
        else:
            pool.reserve(calls=1, tokens=800)
            ordinary_ok = call_ok(lambda: pool.reserve(calls=1, tokens=200, finalization=True))
        checks.add(
            f"a full ordinary share and the reserve can both be held ({label})",
            ordinary_ok,
            f"ordinary_remaining={pool.tokens_remaining} finalization_remaining={pool.finalization_tokens_remaining}",
        )

    reserved = BudgetManager(max_model_calls=10, max_tokens=1000, finalization_reserve_tokens=200)
    checks.add(
        "ordinary work does not see the finalization reserve",
        reserved.tokens_remaining == 800,
        f"tokens_remaining={reserved.tokens_remaining}",
    )
    ok, detail = raises(lambda: reserved.reserve(calls=1, tokens=900), expected=BudgetExceeded)
    checks.add("ordinary work cannot spend past its share", ok, detail)
    ok, detail = raises(lambda: reserved.reserve(calls=1, tokens=201, finalization=True), expected=BudgetExceeded)
    checks.add("finalization work cannot spend past the reserve", ok, detail)
    ok, detail = raises(
        lambda: BudgetManager(max_model_calls=1, max_tokens=10, finalization_reserve_tokens=20), expected=ValueError
    )
    checks.add("a reserve larger than the whole budget is refused at construction", ok, detail)

    # --- overspending is shown, not hidden ----------------------------------
    over = BudgetManager(max_model_calls=5, max_tokens=100)
    small = over.reserve(calls=1, tokens=10)
    over.settle(small.reservation_id, tokens=500, quality="provider")
    checks.add(
        "a settlement larger than its reservation surfaces as an overrun",
        over.tokens_remaining == -400 and over.is_overrun,
        f"tokens_remaining={over.tokens_remaining} is_overrun={over.is_overrun}",
    )

    # --- the deadline -------------------------------------------------------
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    clock = lambda: now  # noqa: E731
    before_deadline = BudgetManager(
        max_model_calls=5, max_tokens=100, deadline_at=now + timedelta(hours=1), clock=clock
    )
    checks.add(
        "work may reserve before the deadline",
        call_ok(lambda: before_deadline.reserve(calls=1, tokens=10)),
        "",
    )
    after_deadline = BudgetManager(
        max_model_calls=5, max_tokens=100, deadline_at=now - timedelta(seconds=1), clock=clock
    )
    ok, detail = raises(lambda: after_deadline.reserve(calls=1, tokens=10), expected=BudgetExceeded)
    checks.add("work is refused once the deadline has passed (section 10)", ok, detail)
    checks.add(
        "a run with no deadline is never refused for time",
        call_ok(lambda: BudgetManager(max_model_calls=5, max_tokens=100).reserve(calls=1, tokens=10)),
        "",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure budget ledger; no framework, no Runner, no model",
        note="every method is await-free, so check-then-reserve is atomic under asyncio",
    )


if __name__ == "__main__":
    sys.exit(main())
