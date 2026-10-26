"""G1 baseline: one retrieval pass + one generation, no task split or gates.

This is deliberately a strong baseline: it receives the same evidence budget
as the workbench's two tasks combined and is allowed to answer in prose.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.knowledge import KnowledgeIndex  # noqa: E402

DEFAULT_QUERIES = {
    "education-policy": [
        "课后作业", "生成式人工智能", "教育 人工智能", "白名单", "人机协同", "评价",
        "未成年人 个人信息",
    ],
    "ai-governance": [
        "生成式人工智能", "备案", "深度合成", "标识", "个人信息", "安全评估", "服务提供者",
    ],
}


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def corpus_db(data_dir: Path, corpus: str) -> Path:
    return data_dir / "corpora" / f"{corpus}.db"


async def run(args: argparse.Namespace) -> int:
    load_env(ROOT / "deploy" / ".env.production.local")
    load_env(ROOT / ".env.local")
    base = os.environ.get("MODEL_BASE_URL", "")
    key = os.environ.get("MODEL_API_KEY", "")
    model = os.environ.get("MODEL_NAME", "")
    if not (base and key and model):
        print("missing MODEL_BASE_URL/MODEL_API_KEY/MODEL_NAME", file=sys.stderr)
        return 2

    db = corpus_db(args.data_dir, args.workspace)
    if not db.is_file():
        print(f"corpus db not found: {db}", file=sys.stderr)
        return 2

    queries = args.query or DEFAULT_QUERIES.get(args.workspace) or [args.goal[:12]]
    index = await KnowledgeIndex.open(db, corpus=args.workspace, access_scope="public")
    try:
        seen: dict[str, dict] = {}
        for q in queries:
            for item in await index.search(q, limit=8, authorized_scopes=("public",)):
                seen.setdefault(item.evidence_id, {
                    "evidence_id": item.evidence_id,
                    "text": item.text,
                    "source_uri": item.source_uri,
                    "source_revision": item.source_revision,
                    "locator": item.locator,
                    "query": q,
                })
    finally:
        await index.close()

    evidence = list(seen.values())[: args.budget]
    if not evidence:
        print("no evidence retrieved", file=sys.stderr)
        return 2

    from openai import AsyncOpenAI

    blocks = []
    for i, e in enumerate(evidence, 1):
        blocks.append(f"[{i}] 来源：{e['source_uri']}\n定位：{e['locator']}\n原文：{e['text']}")
    prompt = (
        "你是政策与合规分析助手。请依据下面提供的资料回答用户问题。\n"
        "要求：先给结论，再给依据；引用时写出文件名与条号/章节；"
        "资料没有覆盖的内容明确说“未检索到”，不要凭印象补条文。\n\n"
        f"问题：{args.goal}\n\n资料：\n" + "\n\n".join(blocks)
    )
    client = AsyncOpenAI(base_url=base, api_key=key)
    resp = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = (resp.choices[0].message.content or "").strip()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "answer.md").write_text(answer + "\n", encoding="utf-8")
    (out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "group": "A",
        "workspace": args.workspace,
        "goal": args.goal,
        "model": model,
        "temperature": 0.2,
        "queries": queries,
        "budget": args.budget,
        "evidence_count": len(evidence),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"answer={len(answer)} chars evidence={len(evidence)} -> {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default="education-policy")
    p.add_argument("--goal", required=True)
    p.add_argument("--budget", type=int, default=20)
    p.add_argument("--query", action="append", help="retrieval query; repeatable")
    p.add_argument("--data-dir", type=Path, default=ROOT / ".local_data")
    p.add_argument("--out", type=Path, required=True)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
