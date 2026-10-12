"""Knowledge index: the application's own corpus index over SQLite FTS5.

PROJECT.md D06 chose an independent logical index over the framework's
retrievers, and section 7.2 fixes the shape of both chains:

- import: authorise -> parse -> source version/hash -> chunk and record a
  locator -> FTS5 index -> (optionally) a real embedding -> index status
- query: identity and scope check -> select the authorised database -> keyword
  (and/or vector) retrieval -> de-duplicate by chunk id + revision -> RRF ->
  confirm source/version -> token trim -> Evidence

Three facts decided the implementation, all verified locally against the pinned
SQLite (3.53.1):

1. ``tokenize='trigram'`` works and matches Chinese text -- but **a query
   shorter than three characters matches nothing**. Section 7.2 anticipated
   this and requires the short-query path to be a *restricted supplementary*
   search whose method is recorded, never passed off as BM25.
2. BM25 is exposed by FTS5 as a negative score where **smaller is better**, so
   ranking ascending is correct and a raw score must never be read as a
   confidence.
3. A trigram index only matches text that survives tokenisation *inside one
   row*, so chunks overlap. Without overlap any phrase straddling a chunk
   boundary is silently unretrievable.

One index serves one ``(corpus, access_scope)``. Authorisation is decided by
*which* index is opened, not by a filter argument -- PROJECT.md section 7.2 and
S13 record that the framework's RetrievalConfig filters are ignored downstream,
so a filter-shaped permission would be a false guarantee.

It starts no runtime and calls no model.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiosqlite

from app.models import Evidence

__all__ = [
    "CHUNK_CHARS",
    "CHUNK_OVERLAP",
    "METHOD_SHORT_QUERY",
    "METHOD_TRIGRAM_BM25",
    "MIN_TRIGRAM_CHARS",
    "KnowledgeIndex",
]

CHUNK_CHARS = 400
"""Chunk size in characters. Characters, not tokens: the corpus tokenizer is
not necessarily the model's tokenizer."""

CHUNK_OVERLAP = 80
"""Characters shared between consecutive chunks.

Must be at least as long as the longest phrase a query can contain, or a phrase
straddling a boundary exists in no chunk and can never be retrieved.
"""

MIN_TRIGRAM_CHARS = 3
"""A trigram index cannot match a term shorter than this."""

METHOD_TRIGRAM_BM25 = "fts5_trigram_bm25"
METHOD_SHORT_QUERY = "short_query_scan"
"""Recorded on Evidence found by the restricted short-query path. Scores from
this path are not BM25 and are not comparable with the trigram path."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT    NOT NULL,
    source_uri      TEXT    NOT NULL,
    source_revision TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    locator         TEXT    NOT NULL,
    ordinal         INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    access_scope    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_by_doc ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS chunks_by_uri ON chunks (source_uri);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    text,
    tokenize='trigram'
);
"""


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, str]]:
    """Split text into overlapping ``(offset, piece)`` windows.

    Blank windows are dropped so a run of whitespace does not become a chunk.
    """
    if size <= overlap:
        raise ValueError(f"chunk size {size} must exceed overlap {overlap}")

    step = size - overlap
    pieces: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        piece = text[start : start + size]
        if piece.strip():
            pieces.append((start, piece))
        if start + size >= len(text):
            break
        start += step
    return pieces


def _like_literal(needle: str) -> str:
    """Escape a substring for use in a LIKE pattern with ``ESCAPE '\\'``."""
    return needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _phrases(terms: Iterable[str]) -> str:
    """Build an FTS5 AND-query of quoted phrases.

    Each term is quoted so user text cannot inject FTS5 operators; an embedded
    double quote is escaped by doubling it. The whole query must not be quoted
    as one phrase: a space inside would then have to appear literally, and any
    multi-word query would match nothing.
    """
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


class KnowledgeIndex:
    """One corpus at one access scope, backed by its own SQLite database."""

    def __init__(self, connection: aiosqlite.Connection, *, corpus: str, access_scope: str) -> None:
        self._db = connection
        self._corpus = corpus
        self._access_scope = access_scope

    @classmethod
    async def open(cls, db_path: str | Path, *, corpus: str, access_scope: str) -> "KnowledgeIndex":
        """Open (creating if needed) the index for one corpus and scope."""
        connection = await aiosqlite.connect(str(db_path))
        try:
            connection.row_factory = aiosqlite.Row
            await connection.executescript(_SCHEMA)
            await connection.commit()
        except Exception:
            # The worker thread would otherwise outlive the failed open.
            await connection.close()
            raise
        return cls(connection, corpus=corpus, access_scope=access_scope)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> "KnowledgeIndex":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    # -- import chain -------------------------------------------------------
    async def add_document(
        self,
        *,
        source_uri: str,
        source_revision: str,
        text: str,
        doc_id: str | None = None,
    ) -> list[str]:
        """Index a document, replacing any previous revision of it.

        Returns the chunk ids written. Re-importing the same document removes
        its previous chunks first, so a changed revision does not leave orphans
        behind under a stale content hash.

        Raises:
            ValueError: the document has no indexable text. Indexing nothing and
                reporting success would leave a document that no later
                source/version confirmation could ever resolve.
        """
        if not text.strip():
            raise ValueError(f"document {source_uri!r} has no indexable text")

        # The document id identifies the *document*, not a revision of it.
        # Keying it on the revision would make a new revision a different
        # document, so the old chunks would survive as orphans under a stale
        # revision -- exactly the "confirm source/version" step section 7.2
        # exists to prevent.
        document = doc_id or source_uri

        chunk_ids: list[str] = []
        try:
            # Both keys are cleared: a caller that first imported under
            # ``source_uri`` and later passes an explicit ``doc_id`` (or the
            # reverse) must not end up with two copies of the same source.
            await self._db.execute(
                "DELETE FROM chunks_fts WHERE chunk_id IN "
                "(SELECT chunk_id FROM chunks WHERE doc_id = ? OR source_uri = ?)",
                (document, source_uri),
            )
            await self._db.execute(
                "DELETE FROM chunks WHERE doc_id = ? OR source_uri = ?", (document, source_uri)
            )

            for ordinal, (offset, piece) in enumerate(_chunk_text(text)):
                content_hash = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                # Components are percent-encoded before joining: doc ids and
                # revisions may themselves contain ':', and a bare join would
                # let ("a", "b:c") and ("a:b", "c") collide on one primary key.
                chunk_id = (
                    f"{quote(document, safe='')}:{quote(source_revision, safe='')}"
                    f":{ordinal}:{content_hash[:12]}"
                )
                locator = f"chars:{offset}-{offset + len(piece)}"

                await self._db.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, source_uri, source_revision, content_hash, "
                    "locator, ordinal, text, access_scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk_id,
                        document,
                        source_uri,
                        source_revision,
                        content_hash,
                        locator,
                        ordinal,
                        piece,
                        self._access_scope,
                    ),
                )
                await self._db.execute(
                    "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)", (chunk_id, piece)
                )
                chunk_ids.append(chunk_id)

            await self._db.commit()
        except Exception:
            # Without this the deletes would be committed by a later call while
            # the inserts that were meant to replace them never landed.
            await self._db.rollback()
            raise

        return chunk_ids

    # -- query chain --------------------------------------------------------
    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        authorized_scopes: Iterable[str] = (),
    ) -> list[Evidence]:
        """Return evidence for a query, or nothing when the scope is not authorised.

        The scope check happens before any retrieval, so an unauthorised caller
        cannot even measure whether a term exists in the corpus.

        Raises:
            TypeError: ``authorized_scopes`` is a single string. A string is
                iterable, so it would otherwise be read as a set of characters
                and silently authorise nothing.
        """
        if isinstance(authorized_scopes, str):
            raise TypeError("authorized_scopes must be a collection of scopes, not a single string")
        if self._access_scope not in set(authorized_scopes):
            return []

        needle = query.strip()
        if not needle:
            return []

        terms = needle.split()
        # Every term must be long enough for the trigram index; one short term
        # in the query means the whole query has to take the restricted path.
        if all(len(term) >= MIN_TRIGRAM_CHARS for term in terms):
            rows = await self._search_trigram(terms, limit)
            method = METHOD_TRIGRAM_BM25
        else:
            rows = await self._search_short_query(terms, limit)
            method = METHOD_SHORT_QUERY

        return [
            self._to_evidence(row, method=method, rank=rank)
            for rank, row in enumerate(rows, start=1)
        ]

    async def _search_trigram(self, terms: list[str], limit: int) -> list[aiosqlite.Row]:
        async with self._db.execute(
            "SELECT c.*, bm25(chunks_fts) AS score FROM chunks_fts "
            "JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id "
            "WHERE chunks_fts MATCH ? ORDER BY score, c.chunk_id LIMIT ?",
            (_phrases(terms), limit),
        ) as cursor:
            return list(await cursor.fetchall())

    async def _search_short_query(self, terms: list[str], limit: int) -> list[aiosqlite.Row]:
        """Restricted substring scan for queries a trigram index cannot match.

        Deliberately *not* called BM25 anywhere: the score is a constant and
        carries no ranking information that could be compared with the trigram
        path.
        """
        clause = " AND ".join(["c.text LIKE ? ESCAPE '\\'"] * len(terms))
        params: list[Any] = [f"%{_like_literal(term)}%" for term in terms]
        params.append(limit)
        async with self._db.execute(
            f"SELECT c.*, 0.0 AS score FROM chunks c WHERE {clause} ORDER BY c.chunk_id LIMIT ?",
            params,
        ) as cursor:
            return list(await cursor.fetchall())

    # -- helpers ------------------------------------------------------------
    async def chunk_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) AS n FROM chunks") as cursor:
            return int((await cursor.fetchone())["n"])

    def _to_evidence(self, row: aiosqlite.Row, *, method: str, rank: int) -> Evidence:
        # ``fused_rank`` is this retrieval's position while only one ranking
        # exists; once vector retrieval lands and RRF runs, it carries the fused
        # position instead. Either way it is a rank, never a confidence.
        return Evidence(
            evidence_id=row["chunk_id"],
            corpus=self._corpus,
            source_uri=row["source_uri"],
            source_revision=row["source_revision"],
            doc_id=row["doc_id"],
            chunk_id=row["chunk_id"],
            content_hash=row["content_hash"],
            locator=row["locator"],
            text=row["text"],
            retrieval_method=method,
            raw_score=float(row["score"]),
            fused_rank=rank,
            access_scope=row["access_scope"],
        )
