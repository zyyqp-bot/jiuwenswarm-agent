"""Run the workbench locally with the production env file for UI checks.

Reads deploy/.env.production.local (git-ignored) instead of exporting it in a
shell, because WORKBENCH_USERS contains semicolons. Never prints values.

Run: .venv/Scripts/python.exe scripts/dev_server.py [port]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ENV_FILE = ROOT / "deploy" / ".env.production.local"


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()
    os.environ.setdefault("WORKBENCH_ENV", "production")
    os.environ["WORKBENCH_DATA_DIR"] = str(ROOT / ".local_data")
    os.environ.setdefault("WORKBENCH_SEED_CORPUS", "1")

    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
