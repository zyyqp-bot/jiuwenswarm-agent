"""P2: import a corpus and let a member retrieve evidence for its own task.

The retrieval tool is where "evidence this task actually received" becomes
decidable: it returns evidence ids, records exactly those ids against the
task, and refuses a task assigned to someone else.

Offline: FTS5 only, no model, no embedding provider.

Run: .venv/Scripts/python.exe p1/retrieval_check.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.hooks import register_bridge, unregister_bridge  # noqa: E402
from app.ingest import UnsupportedDocument, import_corpus, load_document  # noqa: E402
from app.knowledge import KnowledgeIndex, corpus_db_path  # noqa: E402
from app.plan import PlanTask  # noqa: E402
from app.retrieval_tool import RetrievalTool, make_retrieval_tool  # noqa: E402
from app.run_bridge import RunBridge  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "retrieval_results.json"

PLAN = [
    PlanTask(
        task_id="research",
        role="teammate",
        instruction="collect",
        acceptance_ids=("a1",),
        assignee="research",
    ),
    PlanTask(
        task_id="engineering",
        role="teammate",
        instruction="propose",
        acceptance_ids=("a2",),
        assignee="engineering",
    ),
]


async def _search(db_path: Path, query: str, scopes: list[str]):
    index = await KnowledgeIndex.open(db_path, corpus="demo", access_scope="public")
    try:
        return await index.search(query, authorized_scopes=scopes)
    finally:
        await index.close()


async def _run(checks: Checks, data_dir: Path) -> None:
    source = data_dir / "source"
    source.mkdir(parents=True)
    (source / "a.txt").write_text(
        "﻿生成式人工智能服务提供者应当进行安全评估。", encoding="utf-8"
    )
    (source / "b.txt").write_text(
        "个人信息处理者应当制定内部管理制度。", encoding="utf-8"
    )
    # The image ships an empty corpus directory kept by .gitkeep. A hidden
    # placeholder is not a document and must not appear as an import failure.
    (source / ".gitkeep").write_text("", encoding="utf-8")

    imported = await import_corpus(
        data_dir=data_dir, corpus="demo", scope="public", source_dir=source
    )
    checks.add(
        "every supported document is imported",
        imported["documents"] == 2 and imported["chunks"] >= 2 and not imported["failures"],
        f"imported={imported}",
    )
    checks.add(
        "hidden files are ignored by the import",
        not any(".gitkeep" in failure for failure in imported["failures"]),
        f"failures={imported['failures']}",
    )

    db_path = corpus_db_path(data_dir, "demo")
    hits = await _search(db_path, "安全评估", ["public"])
    checks.add(
        "a Chinese trigram query finds the passage",
        bool(hits) and "安全评估" in hits[0].text,
        f"hits={[hit.evidence_id for hit in hits]}",
    )
    checks.add(
        "the evidence carries a locator and source",
        bool(hits) and hits[0].source_uri == "a.txt" and bool(hits[0].locator),
        f"source={hits[0].source_uri if hits else None} locator={hits[0].locator if hits else None}",
    )
    checks.add(
        "an unauthorised scope sees nothing",
        await _search(db_path, "安全评估", ["other"]) == [],
        "wrong scope",
    )

    # -- re-import replaces the old revision ---------------------------------
    (source / "a.txt").write_text("安全评估应当每年进行一次。", encoding="utf-8")
    again = await import_corpus(
        data_dir=data_dir, corpus="demo", scope="public", source_dir=source
    )
    checks.add(
        "re-importing a document replaces its chunks",
        again["chunks"] == imported["chunks"]
        and bool(await _search(db_path, "每年", ["public"]))
        and not await _search(db_path, "提供者应当", ["public"]),
        f"chunks={imported['chunks']}->{again['chunks']}",
    )

    # -- parsing -------------------------------------------------------------
    checks.add(
        "a UTF-8 BOM is stripped by the loader",
        load_document(source / "a.txt").startswith("安全评估"),
        load_document(source / "a.txt")[:12],
    )
    unsupported = ""
    try:
        load_document(source / "c.docx")
    except UnsupportedDocument as exc:
        unsupported = str(exc)
    checks.add("an unsupported document type is refused", ".docx" in unsupported, unsupported)

    # -- the tool records only what it returned ------------------------------
    bridge = RunBridge(run_id="run-r", plan=PLAN, evidence_by_task={})
    index = await KnowledgeIndex.open(db_path, corpus="demo", access_scope="public")
    try:
        tool = RetrievalTool(
            bridge,
            index,
            producer="research",
            corpus="demo",
            access_scope="public",
        )
        result = await tool.invoke({"task_id": "research", "query": "安全评估", "limit": 5})
        returned = result.data.get("evidence_ids") if result.data else []
        checks.add(
            "the retrieval tool returns evidence ids",
            result.success and bool(returned),
            f"success={result.success} ids={returned} error={result.error}",
        )
        snapshot = bridge.snapshot()
        checks.add(
            "the bridge records exactly the returned evidence for the task",
            snapshot.get("evidence", {}).get("research") == sorted(returned),
            f"evidence={snapshot.get('evidence')}",
        )
        # The workbench's evidence drawer needs the passage itself, not just
        # its id: the snapshot is the only thing the API process can read.
        details = snapshot.get("evidence_details", {})
        first = returned[0] if returned else ""
        checks.add(
            "the bridge records the passage behind each evidence id",
            bool(first)
            and details.get(first, {}).get("text")
            and details[first].get("source_uri")
            and details[first].get("locator"),
            f"detail={details.get(first)}",
        )

        refused = await tool.invoke({"task_id": "engineering", "query": "安全评估"})
        checks.add(
            "a member cannot retrieve for another member's task",
            not refused.success and "not the assignee" in (refused.error or ""),
            refused.error or "no error",
        )
        unknown = await tool.invoke({"task_id": "ghost", "query": "安全评估"})
        checks.add(
            "an unknown task is refused",
            not unknown.success and "not in the plan" in (unknown.error or ""),
            unknown.error or "no error",
        )
        checks.add(
            "a refused call records no evidence",
            bridge.snapshot().get("evidence", {}).get("engineering") is None,
            f"evidence={bridge.snapshot().get('evidence')}",
        )
    finally:
        await index.close()

    # -- the snapshot must not grow without bound ---------------------------
    cap_bridge = RunBridge(run_id="run-cap", plan=PLAN)
    for index in range(205):
        evidence_id = f"e{index}"
        cap_bridge.add_evidence(
            "research",
            [evidence_id],
            [
                {
                    "evidence_id": evidence_id,
                    "text": "x" * 400,
                    "source_uri": "doc.txt",
                    "locator": "chars:0-1",
                }
            ],
        )
    capped = cap_bridge.snapshot()
    checks.add(
        "the snapshot caps the number of passages it carries",
        len(capped.get("evidence_details", {})) == 200
        and capped.get("evidence_details_omitted") == 5,
        f"details={len(capped.get('evidence_details', {}))} omitted={capped.get('evidence_details_omitted')}",
    )

    # -- provider factory ----------------------------------------------------
    register_bridge("run-f", RunBridge(run_id="run-f", plan=PLAN))
    try:
        built = make_retrieval_tool(
            {
                "run_id": "run-f",
                "db_path": str(db_path),
                "corpus": "demo",
                "access_scope": "public",
            },
            type("Ctx", (), {"member_name": "research"})(),
        )
        checks.add("the provider binds the member identity", built.producer == "research", built.producer)
        missing = ""
        try:
            make_retrieval_tool(
                {"run_id": "run-f", "db_path": str(db_path), "corpus": "demo", "access_scope": "public"},
                type("Ctx", (), {})(),
            )
        except RuntimeError as exc:
            missing = str(exc)
        checks.add(
            "a context without a member name refuses to build the tool",
            "member_name" in missing,
            missing or "no error",
        )
    finally:
        unregister_bridge("run-f")


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(
        RESULTS_PATH,
        scope="FTS5 import and retrieval tool only; no model, no embedding call",
    )


if __name__ == "__main__":
    sys.exit(main())
