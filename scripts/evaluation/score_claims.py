"""Score G1 A/B claims without importing the workbench's own citation gate.

Locatable (manual axis): a claim names a corpus file and a section/quote that
exists in that file. Machine-verifiable (audit axis): the claim carries
evidence ids that resolve to retained evidence with a char locator.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

QUOTE_RE = re.compile(r"[“\"]([^”\"]{6,})[”\"]")
FILE_RE = re.compile(r"《([^》]+)》")


def corpus_texts(corpus_dir: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in corpus_dir.glob("*.md")}


def _norm(text: str) -> str:
    # Quote style and spacing vary between the corpus and generated prose.
    for ch in ("“", "”", "‘", "’", "「", "」", "『", "』", '"'):
        text = text.replace(ch, "")
    return re.sub(r"\s+", "", text)


def _segments(quote: str) -> list[str]:
    parts = re.split(r"…+|\.\.\.", quote)
    return [p.strip().strip("。；，") for p in parts if len(p.strip().strip("。；，")) >= 6]


def find_quote(quote: str, texts: dict[str, str], files: list[str]) -> str | None:
    for name in files:
        if name not in texts:
            continue
        body = _norm(texts[name])
        for seg in _segments(quote):
            if _norm(seg) in body:
                return name
    return None


def score_a(answer_path: Path, corpus_dir: Path) -> list[dict]:
    texts = corpus_texts(corpus_dir)
    rows = []
    for line in answer_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "合规事项" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        claim = f"{cells[0]}：{cells[1]}"
        basis = cells[2]
        files = [f if f.endswith(".md") else f + ".md" for f in FILE_RE.findall(basis)]
        quotes = QUOTE_RE.findall(basis)
        matched = [find_quote(q, texts, files) for q in quotes]
        hit = next((m for m in matched if m), None)
        rows.append({
            "claim": claim[:200],
            "files": files,
            "quotes": len(quotes),
            "locatable": bool(hit),
            "matched_file": hit,
            "machine_verifiable": False,
            "reason": "manual file+section+quote" if hit else "no quote matched",
        })
    return rows


def score_b(record_path: Path) -> list[dict]:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    snap = record.get("snapshot") or {}
    details = snap.get("evidence_details") or {}
    rows = []
    for task, result in (snap.get("results") or {}).items():
        for claim in result.get("claims") or []:
            text = claim.get("text") or ""
            ids = claim.get("evidence_ids") or []
            resolved = [details.get(i) for i in ids]
            resolved = [d for d in resolved if isinstance(d, dict)]
            machine = bool(ids) and len(resolved) == len(ids) and all(d.get("locator") for d in resolved)
            quotes = QUOTE_RE.findall(text)
            hit = None
            for q in quotes:
                for d in resolved:
                    if q.strip("。；，") in (d.get("text") or ""):
                        hit = d.get("source_uri"); break
                if hit: break
            rows.append({
                "task": task,
                "claim": text[:200],
                "evidence_ids": len(ids),
                "resolved": len(resolved),
                "locatable": bool(hit) or (machine and not quotes),
                "matched_file": hit,
                "machine_verifiable": machine,
                "reason": "resolved ids+locator" if machine else "unresolved evidence ids",
            })
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--a", type=Path, required=True)
    p.add_argument("--b", type=Path, required=True)
    p.add_argument("--corpus-dir", type=Path, default=Path("corpus/education-policy"))
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    a_rows = score_a(args.a, args.corpus_dir)
    b_rows = score_b(args.b)
    def summary(rows):
        n = len(rows)
        loc = sum(1 for r in rows if r["locatable"])
        mach = sum(1 for r in rows if r["machine_verifiable"])
        return {"claims": n, "locatable": loc, "locatable_rate": round(loc / n, 3) if n else 0,
                "machine_verifiable": mach, "machine_rate": round(mach / n, 3) if n else 0,
                "unsupported": n - loc}
    out = {"A": summary(a_rows), "B": summary(b_rows), "a_rows": a_rows, "b_rows": b_rows}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("A", "B")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
