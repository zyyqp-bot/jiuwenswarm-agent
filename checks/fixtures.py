"""Shared fixtures for the staged check scripts.

``evidence`` was copied byte-for-byte into two check scripts before this
module existed; keeping one copy means a change to ``Evidence`` cannot leave
one script building a different kind of evidence than another.
"""
from __future__ import annotations

from app.models import Evidence

__all__ = ["evidence"]


def evidence(
    index: int,
    text: str,
    *,
    access_scope: str = "project:alpha",
    corpus: str = "docs",
) -> Evidence:
    """Build an Evidence with stable, easy-to-assert provenance."""
    return Evidence(
        evidence_id=f"ev-{index}",
        corpus=corpus,
        source_uri=f"docs/{index}.md",
        source_revision="r1",
        doc_id=f"docs/{index}.md",
        chunk_id=f"docs/{index}.md:r1:0:aaaa",
        content_hash="a" * 64,
        locator="chars:0-10",
        text=text,
        retrieval_method="fts5_trigram_bm25",
        raw_score=-1.0,
        fused_rank=index,
        access_scope=access_scope,
    )
