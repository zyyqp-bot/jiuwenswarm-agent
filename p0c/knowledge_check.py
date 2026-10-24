"""P0-C (part 1): the corpus index -- import chain and query chain.

PROJECT.md section 7.2's two chains, exercised offline over a real SQLite FTS5
file. The behaviours that matter most, and why each is asserted:

- a query shorter than three characters matches nothing in a trigram index, so
  it must go through the restricted scan and be *labelled* as such;
- a phrase straddling a chunk boundary must still be retrievable, which is what
  chunk overlap buys;
- an unauthorised scope gets nothing, decided before any retrieval runs.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0c/knowledge_check.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge import (  # noqa: E402
    CHUNK_CHARS,
    METHOD_SHORT_QUERY,
    METHOD_TRIGRAM_BM25,
    KnowledgeIndex,
)
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "knowledge_results.json"

SCOPE = "project:alpha"
OTHER_SCOPE = "project:beta"

DOC_ONE = (
    "研究团队需要核对源码版本与接口差异，并记录未解决的技术问题。"
    "所有结论必须能够追溯到具体的提交与文件位置。"
)
DOC_TWO = (
    "工程团队负责架构设计与可执行验证，提出接口草案与回滚方案。"
    "不得编造测试结果，也不得声称未执行的验证已经通过。"
)


async def _expect_raises(checks, label, awaitable_factory, expected: type[Exception]) -> None:
    try:
        await awaitable_factory()
    except expected as exc:
        checks.add(label, True, str(exc))
    except Exception as exc:  # a different failure is not the guard we claim
        checks.add(label, False, f"wrong error {type(exc).__name__}: {exc}")
    else:
        checks.add(label, False, "no error raised")


async def _async_checks(checks: Checks, db_path: Path) -> None:
    index = await KnowledgeIndex.open(db_path, corpus="docs", access_scope=SCOPE)

    # Two concurrent runs may open the same corpus; WAL plus a busy timeout
    # keeps a first retrieval from failing with "database is locked".
    async with index._db.execute("PRAGMA journal_mode") as cursor:  # noqa: SLF001
        mode = str((await cursor.fetchone())[0]).lower()
    checks.add("the index runs in WAL mode", mode == "wal", f"journal_mode={mode}")

    first_ids = await index.add_document(
        source_uri="docs/research.md", source_revision="r1", text=DOC_ONE
    )
    second_ids = await index.add_document(
        source_uri="docs/engineering.md", source_revision="r1", text=DOC_TWO
    )

    checks.add("import writes at least one chunk per document", len(first_ids) >= 1, f"chunks={len(first_ids)}")
    checks.add(
        "the index holds every imported chunk",
        await index.chunk_count() == len(first_ids) + len(second_ids),
        f"count={await index.chunk_count()} expected={len(first_ids) + len(second_ids)}",
    )

    # --- the trigram path ---------------------------------------------------
    hits = await index.search("源码版本", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a three-character query is answered by the trigram BM25 path",
        len(hits) == 1 and hits[0].retrieval_method == METHOD_TRIGRAM_BM25,
        f"methods={[h.retrieval_method for h in hits]}",
    )
    checks.add(
        "the hit carries full provenance",
        hits
        and hits[0].source_uri == "docs/research.md"
        and hits[0].source_revision == "r1"
        and hits[0].locator.startswith("chars:")
        and len(hits[0].content_hash) == 64
        and hits[0].access_scope == SCOPE,
        f"uri={hits[0].source_uri if hits else None} locator={hits[0].locator if hits else None}",
    )
    checks.add(
        "fused_rank starts at 1 and raw_score is the FTS5 bm25 value",
        hits and hits[0].fused_rank == 1 and hits[0].raw_score <= 0,
        f"rank={hits[0].fused_rank if hits else None} score={hits[0].raw_score if hits else None}",
    )

    multi = await index.search("源码版本 接口差异", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a multi-term query matches on the trigram path instead of returning nothing",
        len(multi) == 1 and multi[0].source_uri == "docs/research.md",
        f"hits={len(multi)}",
    )

    second = await index.search("架构设计", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a query for the other document finds the other document",
        len(second) == 1 and second[0].source_uri == "docs/engineering.md",
        f"uri={second[0].source_uri if second else None}",
    )
    checks.add(
        "a query with no match returns nothing",
        await index.search("量子隧穿效应", limit=5, authorized_scopes=[SCOPE]) == [],
        "",
    )

    # --- the short-query path ----------------------------------------------
    short_hits = await index.search("团队", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a two-character query is served by the restricted scan, not silently empty",
        len(short_hits) == 2 and all(h.retrieval_method == METHOD_SHORT_QUERY for h in short_hits),
        f"hits={len(short_hits)} methods={[h.retrieval_method for h in short_hits]}",
    )
    checks.add(
        "the short-query path is never labelled as BM25",
        all(h.retrieval_method != METHOD_TRIGRAM_BM25 for h in short_hits),
        f"methods={sorted({h.retrieval_method for h in short_hits})}",
    )
    checks.add(
        "a mixed-length query falls back rather than dropping its short term",
        (await index.search("团队 架构", limit=5, authorized_scopes=[SCOPE]))[0].retrieval_method
        == METHOD_SHORT_QUERY,
        "",
    )

    wildcard = await index.search("%", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "LIKE metacharacters in a short query are escaped, not treated as wildcards",
        wildcard == [],
        f"hits={len(wildcard)}",
    )
    checks.add(
        "an underscore in a short query is escaped too",
        await index.search("_", limit=5, authorized_scopes=[SCOPE]) == [],
        "",
    )

    # --- authorisation ------------------------------------------------------
    checks.add(
        "an unauthorised scope returns nothing",
        await index.search("源码版本", limit=5, authorized_scopes=[OTHER_SCOPE]) == [],
        "",
    )
    checks.add(
        "an empty authorised set returns nothing",
        await index.search("源码版本", limit=5, authorized_scopes=[]) == [],
        "",
    )
    await _expect_raises(
        checks,
        "passing a single scope string is rejected instead of silently authorising nothing",
        lambda: index.search("源码版本", authorized_scopes=SCOPE),
        TypeError,
    )

    # --- chunk boundaries ---------------------------------------------------
    boundary = "甲" * (CHUNK_CHARS - 1) + "接口差异" + "乙" * 50
    await index.add_document(source_uri="docs/boundary.md", source_revision="r1", text=boundary)
    boundary_hits = await index.search("接口差异", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a phrase straddling a chunk boundary is still retrievable (overlap)",
        any(h.source_uri == "docs/boundary.md" for h in boundary_hits),
        f"uris={sorted({h.source_uri for h in boundary_hits})}",
    )

    # --- re-import replaces rather than duplicates -------------------------
    before = await index.chunk_count()
    await index.add_document(source_uri="docs/research.md", source_revision="r1", text=DOC_ONE)
    checks.add(
        "re-importing an unchanged document does not duplicate its chunks",
        await index.chunk_count() == before,
        f"before={before} after={await index.chunk_count()}",
    )

    before = await index.chunk_count()
    await index.add_document(source_uri="docs/engineering.md", source_revision="r1", text=DOC_TWO, doc_id="eng")
    checks.add(
        "importing the same source under an explicit doc id replaces the earlier import",
        await index.chunk_count() == before,
        f"before={before} after={await index.chunk_count()}",
    )

    revised = DOC_ONE + "补充：本轮的接口核对范围仅限 MVP 相关路径。"
    await index.add_document(source_uri="docs/research.md", source_revision="r2", text=revised)
    stale = await index.search("研究团队", limit=5, authorized_scopes=[SCOPE])
    checks.add(
        "a changed revision replaces the old chunks rather than leaving them behind",
        all(h.source_revision == "r2" for h in stale) and bool(stale),
        f"revisions={sorted({h.source_revision for h in stale})}",
    )

    # --- document ids cannot collide ---------------------------------------
    # A bare ':' join would make ("u/a", "b:c") and ("u/a:b", "c") collide on
    # one primary key; the components are percent-encoded before joining.
    await index.add_document(source_uri="u/a", source_revision="b:c", text="第一份文档的内容足够长。")
    collision = ""
    try:
        await index.add_document(
            source_uri="u/b", doc_id="u/a:b", source_revision="c", text="第二份文档的内容足够长。"
        )
    except Exception as exc:
        collision = f"{type(exc).__name__}: {exc}"
    checks.add(
        "ids and revisions containing the id separator coexist in the primary key",
        not collision,
        collision or "both imports succeeded",
    )

    # --- empty documents ----------------------------------------------------
    await _expect_raises(
        checks,
        "a document with no indexable text is rejected rather than silently indexed",
        lambda: index.add_document(source_uri="docs/empty.md", source_revision="r1", text="   \n\t "),
        ValueError,
    )

    await index.close()


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="p0c-knowledge-") as tmp:
        asyncio.run(_async_checks(checks, Path(tmp) / "corpus.sqlite"))

    return checks.report(
        RESULTS_PATH,
        scope="SQLite FTS5 corpus index; no framework, no Runner, no model",
        sqlite_note="trigram tokenizer verified available on SQLite 3.53.1",
    )


if __name__ == "__main__":
    sys.exit(main())
