"""Run isolated upstream function probes without importing/installing the SDK.

These are source-behavior probes with stubbed dependencies, not integration tests.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "upstream/agent-core"
SWARM = ROOT / "upstream/jiuwenswarm"


def extract(path, name, owner=None):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    scope = tree.body
    if owner:
        scope = next(n for n in scope if isinstance(n, ast.ClassDef) and n.name == owner).body
    return next(n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name)


def evaluate(node, namespace):
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<extracted-upstream>", "exec"), namespace)
    return namespace[node.name]


async def main():
    observations = []

    mock_node = extract(SWARM / "jiuwenswarm/agents/harness/common/memory/embeddings.py", "MockEmbeddingProvider")
    mock = evaluate(mock_node, {"EmbeddingProvider": object})()
    vector = await mock.embed_query("OpenJiuwen MVP")
    assert len(vector) == 128 and any(vector)
    observations.append({"id": "P01", "result": "pass", "observation": "Swarm app mock embeddings are nonzero 128-dimensional pseudorandom vectors."})

    async def keyword(query, limit):
        return [{"id": "hit", "score": 0.2, "text": "known keyword hit"}]

    async def no_vector(query):
        return []

    async def sync(**kwargs):
        pass

    state = SimpleNamespace(settings=SimpleNamespace(sync={}, query={"min_score": 0.3, "minScore": 0.3,
        "max_results": 10, "maxResults": 10, "hybrid": {"enabled": True}}),
        dirty=False, fts_available=True, _search_keyword=keyword, _embed_query_with_timeout=no_vector,
        sync=sync, _filter_recent_memory_results=lambda values: values)
    search_core = evaluate(extract(CORE / "openjiuwen/core/memory/lite/manager.py", "search", "MemoryIndexManager"), {})
    core_results = await search_core(state, "known keyword")
    assert len(core_results) == 1
    observations.append({"id": "P02", "result": "pass", "observation": "Team core lite memory retains keyword hit at 0.2 when vector unavailable (keyword floor 0.1)."})

    merge_app = evaluate(extract(SWARM / "jiuwenswarm/agents/harness/common/memory/manager.py", "_merge_hybrid_results", "MemoryIndexManager"), {})
    state._merge_hybrid_results = lambda *args: merge_app(state, *args)
    search_app = evaluate(extract(SWARM / "jiuwenswarm/agents/harness/common/memory/manager.py", "search", "MemoryIndexManager"), {})
    app_results = await search_app(state, "known keyword")
    assert app_results == []
    observations.append({"id": "P03", "result": "pass", "observation": "Swarm app path with vector failure weights keyword 0.2 by 0.3 => 0.06, then filters it at minScore 0.3."})

    captured = []

    class Store:
        async def hybrid_search(self, **kwargs):
            captured.append(kwargs)
            return []

    retrieve = evaluate(extract(CORE / "openjiuwen/core/retrieval/retriever/hybrid_retriever.py", "retrieve", "HybridRetriever"), {})
    await retrieve(SimpleNamespace(alpha=0.5, embed_model=None, vector_store=Store()), "query", filters={"tenant": "allowed"})
    assert captured[0]["filters"] is None
    observations.append({"id": "P04", "result": "pass", "observation": "HybridRetriever.retrieve ignores supplied filters and calls store with filters=None."})

    normalize = evaluate(extract(CORE / "openjiuwen/agent_teams/skill/visibility.py", "normalize_skill_names"), {})
    compose = evaluate(extract(CORE / "openjiuwen/agent_teams/skill/visibility.py", "compose_skill_visibility"), {"normalize_skill_names": normalize})
    allow, deny = compose(SimpleNamespace(allow=["research"], deny=[]), SimpleNamespace(allow=["common"], deny=["research"]), [])
    assert allow == {"research", "common"} and deny == {"research"}
    empty_allow, _ = compose(SimpleNamespace(allow=[], deny=[]), None, [])
    assert empty_allow == set()
    observations.append({"id": "P05", "result": "pass", "observation": "Skill declarations compose by allow union and deny union; empty effective allow remains unfiltered."})

    node = extract(CORE / "openjiuwen/agent_teams/tools/database/task_dao.py", "_terminate_task_in_session")
    text = ast.unparse(node)
    assert "TaskStatus.COMPLETED, TaskStatus.CANCELLED" in text and ".values(resolved=True)" in text
    observations.append({"id": "P06", "result": "pass", "kind": "static", "observation": "COMPLETED and CANCELLED share downstream dependency resolution in termination helper."})

    rail_text = (CORE / "openjiuwen/harness/rails/context_engineer/context_processor_rail.py").read_text(encoding="utf-8")
    assert "forked.activate()" in rail_text and "ForkedRoundLevelCompressorConfig(" in rail_text
    base = (CORE / "openjiuwen/core/context_engine/processor/forked/compressor/base.py").read_text(encoding="utf-8")
    assert "if target_tokens < min_target_tokens:" in base
    observations.append({"id": "P07", "result": "pass", "kind": "static", "observation": "Harness preset uses forked compressor; min_target_context_ratio guards minimum compressible span, not output ratio."})

    result = {"scope": "isolated extracted-function and AST probes; no SDK imports, model calls or full runtime tests",
        "passed": len(observations), "observations": observations}
    (ROOT / "probes/results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
