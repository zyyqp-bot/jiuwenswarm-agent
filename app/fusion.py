"""Reciprocal rank fusion for combining ranked retrievals.

PROJECT.md section 7.2 fixes the formula: ``sum(1 / (60 + rank))`` with rank
starting at 1. It also fixes the rule that makes the score interpretable: a
result already fused *inside* one source must not be re-entered as two
rankings, or a single retrieval would count twice and outrank a genuine
agreement between independent sources.

These are pure functions over ranked key sequences. They hold no framework
objects and call no model.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = ["DEFAULT_RRF_K", "fuse_scores", "reciprocal_rank_fusion"]

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> dict[str, float]:
    """Fuse several rankings into a ``{key: score}`` mapping.

    Args:
        rankings: One ranked sequence of keys per source, best first. A key
            repeated within a single ranking contributes only at its best
            position, so a source cannot inflate itself by listing a result
            twice.
        k: The rank constant. PROJECT.md fixes the default at 60.

    Returns:
        Fused scores. Higher is better; the scale is not a confidence and not
        comparable with a raw retrieval score (PROJECT.md section 7.2).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen_in_ranking: set[str] = set()
        for rank, key in enumerate(ranking, start=1):
            if key in seen_in_ranking:
                continue
            seen_in_ranking.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


def fuse_scores(
    rankings: Iterable[Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """Fuse rankings and return ``(key, score)`` pairs, best first.

    Ties break on the key so the order is deterministic.
    """
    scores = reciprocal_rank_fusion(rankings, k=k)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
