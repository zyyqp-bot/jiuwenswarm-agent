"""P1: each workspace has its own corpus and never sees another's chunks.

The knowledge module's contract says one index serves one corpus. Before this
check existed, every workspace shared ``data/corpus.db`` and the retrieval
queries carried no corpus predicate, so adding a second workspace would have
leaked its chunks into the first one's results — the exact failure the spec's
"跨 scope 泄漏数 = 0" is meant to rule out.

Run: .venv/Scripts/python.exe p1/workspace_check.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge import KnowledgeIndex, corpus_db_path  # noqa: E402
from app.seeding import ensure_corpus  # noqa: E402
from app.workspaces import WORKSPACES, Workspace, get_workspace  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "workspace_results.json"

WORKSPACE_A = Workspace(name="alpha", description="", corpus="corpus-a", access_scope="public")
WORKSPACE_B = Workspace(name="beta", description="", corpus="corpus-b", access_scope="public")
WORKSPACE_C = Workspace(name="gamma", description="", corpus="corpus-c", access_scope="public")


async def _search(db_path: Path, corpus: str, query: str) -> list[str]:
    index = await KnowledgeIndex.open(db_path, corpus=corpus, access_scope="public")
    try:
        return [hit.evidence_id for hit in await index.search(query, authorized_scopes=["public"])]
    finally:
        await index.close()


async def _run(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        seed_root = root / "seed"
        data_dir = root / "data"
        for workspace, word in (
            (WORKSPACE_A, "阿尔法安全评估要求"),
            (WORKSPACE_B, "贝塔学位授予条件"),
        ):
            directory = seed_root / workspace.corpus
            directory.mkdir(parents=True)
            (directory / "doc.md").write_text(
                f"# {word}\n\n{word}的完整说明。" * 20, encoding="utf-8"
            )

        summaries = await ensure_corpus(data_dir, seed_root, [WORKSPACE_A, WORKSPACE_B])
        checks.add(
            "each workspace is seeded into its own database",
            len(summaries) == 2
            and corpus_db_path(data_dir, "corpus-a").is_file()
            and corpus_db_path(data_dir, "corpus-b").is_file()
            and not (data_dir / "corpus.db").exists(),
            f"summaries={len(summaries)}",
        )

        alpha_hits = await _search(corpus_db_path(data_dir, "corpus-a"), "corpus-a", "贝塔学位")
        beta_hits = await _search(corpus_db_path(data_dir, "corpus-b"), "corpus-b", "阿尔法安全")
        checks.add(
            "a workspace cannot retrieve the other workspace's chunks",
            alpha_hits == [] and beta_hits == [],
            f"alpha_for_beta={alpha_hits} beta_for_alpha={beta_hits}",
        )

        own_hits = await _search(corpus_db_path(data_dir, "corpus-a"), "corpus-a", "阿尔法安全评估")
        checks.add(
            "the same workspace can retrieve its own chunks",
            bool(own_hits),
            f"hits={own_hits}",
        )

        second = await ensure_corpus(data_dir, seed_root, [WORKSPACE_A, WORKSPACE_B])
        checks.add(
            "a second boot does not re-index existing corpora",
            second == [],
            f"second={second}",
        )

    # --- a newly added workspace seeds without touching the others ----------
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        seed_root = root / "seed"
        data_dir = root / "data"
        (seed_root / "corpus-a").mkdir(parents=True)
        (seed_root / "corpus-a" / "doc.md").write_text("阿尔法内容。" * 30, encoding="utf-8")
        await ensure_corpus(data_dir, seed_root, [WORKSPACE_A])
        (seed_root / "corpus-c").mkdir(parents=True)
        (seed_root / "corpus-c" / "doc.md").write_text("伽马内容。" * 30, encoding="utf-8")
        added = await ensure_corpus(data_dir, seed_root, [WORKSPACE_A, WORKSPACE_C])
        checks.add(
            "adding a workspace seeds only the new corpus",
            len(added) == 1 and corpus_db_path(data_dir, "corpus-c").is_file(),
            f"added={len(added)}",
        )

    # --- the shipped registry and corpora ----------------------------------
    checks.add(
        "the registry exposes three distinct workspaces",
        set(WORKSPACES) >= {"ai-governance", "education-policy", "medical-ai-compliance"}
        and get_workspace("education-policy").corpus == "education-policy"
        and get_workspace("ai-governance").corpus != "education-policy"
        and get_workspace("medical-ai-compliance").corpus == "medical-ai-compliance",
        f"workspaces={sorted(WORKSPACES)}",
    )
    ai_docs = sorted((ROOT / "corpus" / "ai-governance").glob("*.md"))
    edu_docs = sorted((ROOT / "corpus" / "education-policy").glob("*.md"))
    med_docs = sorted((ROOT / "corpus" / "medical-ai-compliance").glob("*.md"))
    checks.add(
        "all corpora ship real documents",
        len(ai_docs) >= 6 and len(edu_docs) >= 8 and len(med_docs) >= 8,
        f"ai={len(ai_docs)} education={len(edu_docs)} medical={len(med_docs)}",
    )


def main() -> int:
    checks = Checks()
    import asyncio

    asyncio.run(_run(checks))
    return checks.report(RESULTS_PATH, scope="per-corpus isolation and workspace registry")


if __name__ == "__main__":
    sys.exit(main())
