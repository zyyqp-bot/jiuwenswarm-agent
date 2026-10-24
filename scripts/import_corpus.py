"""Admin CLI: index a directory of documents into one workspace corpus.

The public link never accepts uploads; this command is run by the operator
locally or inside the container.

Run: .venv/Scripts/python.exe scripts/import_corpus.py --data-dir data \
       --corpus ai-governance --scope public --source corpus/ai-governance
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ingest import import_corpus  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--scope", default="public")
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)

    summary = asyncio.run(
        import_corpus(
            data_dir=Path(args.data_dir),
            corpus=args.corpus,
            scope=args.scope,
            source_dir=Path(args.source),
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
