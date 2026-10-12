"""P0-C (part 2): reciprocal rank fusion.

PROJECT.md section 7.2 fixes the formula ``sum(1 / (60 + rank))`` with rank
starting at 1, and fixes the rule that keeps the score meaningful: a result
already fused inside one source must not be re-entered as two rankings.

Pure functions; no framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0c/fusion_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.fusion import DEFAULT_RRF_K, fuse_scores, reciprocal_rank_fusion  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "fusion_results.json"

EPSILON = 1e-9


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) < EPSILON


def main() -> int:
    checks = Checks()

    checks.add("the default rank constant is 60, as section 7.2 fixes", DEFAULT_RRF_K == 60, str(DEFAULT_RRF_K))

    single = reciprocal_rank_fusion([["a", "b", "c"]])
    checks.add(
        "a single ranking scores 1/(k+rank) with rank starting at 1",
        _close(single["a"], 1 / 61) and _close(single["b"], 1 / 62) and _close(single["c"], 1 / 63),
        f"a={single['a']:.6f} b={single['b']:.6f}",
    )
    checks.add(
        "earlier ranks score higher",
        single["a"] > single["b"] > single["c"],
        f"{[round(v, 6) for v in single.values()]}",
    )

    consensus = reciprocal_rank_fusion([["a", "b"], ["a", "c"]])
    checks.add(
        "a result both sources agree on outranks either source's leader",
        consensus["a"] > consensus["b"] and consensus["a"] > consensus["c"],
        f"a={consensus['a']:.6f} b={consensus['b']:.6f} c={consensus['c']:.6f}",
    )
    checks.add(
        "the consensus score is the sum of its contributions",
        _close(consensus["a"], 2 / 61) and _close(consensus["c"], 1 / 62),
        f"a={consensus['a']:.6f}",
    )

    checks.add(
        "a key repeated inside one ranking is counted once, at its best rank",
        _close(reciprocal_rank_fusion([["a", "a", "a"]])["a"], 1 / 61),
        f"score={reciprocal_rank_fusion([['a', 'a', 'a']])['a']:.6f}",
    )

    tuned = reciprocal_rank_fusion([["a"]], k=0)
    checks.add("the rank constant is configurable", _close(tuned["a"], 1.0), f"score={tuned['a']}")

    checks.add("no rankings fuse to no scores", reciprocal_rank_fusion([]) == {}, "")
    checks.add(
        "an empty ranking contributes nothing",
        reciprocal_rank_fusion([[], ["a"]]) == reciprocal_rank_fusion([["a"]]),
        "",
    )

    ordered = fuse_scores([["a", "b"], ["a", "c"]])
    checks.add(
        "fuse_scores returns pairs best-first",
        [key for key, _ in ordered][0] == "a" and len(ordered) == 3,
        f"order={[k for k, _ in ordered]}",
    )
    tied = fuse_scores([["a"], ["b"]])  # both at rank 1 of their own source, so equal scores
    checks.add(
        "ties order deterministically by key",
        [key for key, _ in tied] == ["a", "b"],
        f"order={[k for k, _ in tied]}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure RRF functions; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
