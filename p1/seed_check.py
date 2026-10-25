"""P2: first boot indexes the baked corpus into the data volume.

CloudBase may not offer a container terminal on every plan, so the corpus
import cannot be the only path to a usable deployment. When the corpus
database is absent and the image carries documents, startup imports them;
a second boot must not re-index or overwrite an existing database.

Run: .venv/Scripts/python.exe p1/seed_check.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge import corpus_db_path  # noqa: E402
from app.seeding import ensure_corpus  # noqa: E402
from app.workspaces import Workspace  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "seed_results.json"
WORKSPACE = Workspace(
    name="demo",
    description="demo",
    corpus="demo-corpus",
    access_scope="public",
)


async def _run(checks: Checks, root: Path) -> None:
    data_dir = root / "data"
    seed_root = root / "corpus"
    (seed_root / "demo-corpus").mkdir(parents=True)
    (seed_root / "demo-corpus" / "rule.txt").write_text(
        "生成式人工智能服务提供者应当进行安全评估。", encoding="utf-8"
    )

    first = await ensure_corpus(data_dir, seed_root, [WORKSPACE])
    checks.add(
        "the first boot imports the baked corpus",
        len(first) == 1 and first[0]["documents"] == 1 and not first[0]["failures"],
        f"summary={first}",
    )
    checks.add(
        "the corpus database now exists",
        corpus_db_path(data_dir, WORKSPACE.corpus).is_file(),
        str(corpus_db_path(data_dir, WORKSPACE.corpus)),
    )

    second = await ensure_corpus(data_dir, seed_root, [WORKSPACE])
    checks.add(
        "a second boot does not re-index",
        second == [],
        f"summary={second}",
    )

    missing = await ensure_corpus(root / "other-data", root / "no-corpus", [WORKSPACE])
    checks.add(
        "a workspace without seed files is skipped",
        missing == [],
        f"summary={missing}",
    )


def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(_run(checks, Path(raw)))
    return checks.report(RESULTS_PATH, scope="first-boot corpus seeding")


if __name__ == "__main__":
    sys.exit(main())
