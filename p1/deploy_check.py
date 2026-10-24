"""P1: the container image is buildable from the Git repository alone.

Two facts make this check stricter than "a Dockerfile exists":

- ``upstream/`` is git-ignored, so a Git-based build cannot COPY it. The
  image must fetch both repositories at the commits pinned in
  ``source-lock.json``; a floating branch would silently change the product.
- the public link has one login and one writable volume, so the image runs
  one worker as a non-root user and keeps state under ``/data``.

Static parsing only: no Docker daemon is required (and absent on this host).

Run: .venv/Scripts/python.exe p1/deploy_check.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = Path(__file__).resolve().parent / "deploy_results.json"

sys.path.insert(0, str(ROOT))

from checks.harness import Checks  # noqa: E402

REQUIRED_ENV = (
    "SESSION_SECRET",
    "WORKBENCH_USERS",
    "MODEL_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "WORKBENCH_DATA_DIR",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = Checks()

    dockerfile = ROOT / "Dockerfile"
    text = _read(dockerfile)
    checks.add("Dockerfile exists at the repository root", dockerfile.is_file(), str(dockerfile))

    stages = re.findall(r"(?im)^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", text)
    build_stages = [name for _, name in stages if name]
    checks.add(
        "the image is a multi-stage build",
        len(stages) >= 2 and bool(build_stages),
        f"stages={stages}",
    )
    checks.add(
        "the SPA is copied from the node build stage",
        bool(re.search(r"(?m)^COPY\s+--from=\S+\s+/web/dist", text)),
        "expected COPY --from=<node stage> /web/dist ...",
    )

    lock = json.loads(_read(ROOT / "source-lock.json") or "{}")
    for name in ("agent_core", "jiuwenswarm"):
        commit = str((lock.get(name) or {}).get("commit") or "")
        checks.add(
            f"the image fetches {name} at its pinned commit",
            bool(commit) and commit in text,
            f"commit={commit or 'missing from source-lock.json'}",
        )

    user_lines = re.findall(r"(?im)^USER\s+(\S+)", text)
    checks.add(
        "the container runs as a non-root user",
        bool(user_lines) and all(user not in ("root", "0") for user in user_lines),
        f"USER={user_lines}",
    )
    checks.add(
        "the image health-checks the API",
        bool(re.search(r"(?im)^HEALTHCHECK\b.*?/api/health", text, re.S)),
        "expected HEALTHCHECK ... /api/health",
    )
    checks.add(
        "the image exposes port 8000",
        bool(re.search(r"(?im)^EXPOSE\s+8000\b", text)),
        f"EXPOSE={re.findall(r'(?im)^EXPOSE.*', text)}",
    )

    # The ENV block uses line continuations, so read every line that mentions
    # PYTHONPATH rather than requiring it to start an ENV instruction.
    pythonpath = " ".join(line for line in text.splitlines() if "PYTHONPATH" in line)
    checks.add(
        "PYTHONPATH names both upstream directories",
        "upstream/agent-core" in pythonpath and "upstream/jiuwenswarm" in pythonpath,
        pythonpath or "no ENV PYTHONPATH",
    )

    ignore = _read(ROOT / ".dockerignore")
    checks.add(
        "the build context excludes the git-ignored upstream trees",
        "upstream" in ignore and "node_modules" in ignore,
        ignore[:200] or "missing .dockerignore",
    )

    env_example = _read(ROOT / "deploy" / ".env.example")
    checks.add("deploy/.env.example exists", bool(env_example), f"bytes={len(env_example)}")
    missing = [name for name in REQUIRED_ENV if name not in env_example]
    checks.add(
        "the env example lists every required setting",
        bool(env_example) and not missing,
        f"missing={missing}" if missing else "all present",
    )
    secret_like = re.search(r"sk-[A-Za-z0-9]{16,}|[0-9a-f]{48,}", env_example)
    checks.add(
        "the env example carries no real-looking secret",
        secret_like is None,
        secret_like.group(0)[:12] + "…" if secret_like else "clean",
    )

    cloudbase = _read(ROOT / "deploy" / "cloudbase.md")
    lowered = cloudbase.lower()
    checks.add(
        "the deploy guide states the single-instance and volume rules",
        cloudbase
        and "单实例" in cloudbase
        and "/data" in cloudbase,
        f"bytes={len(cloudbase)}",
    )
    checks.add(
        "the deploy guide rules out public uploads",
        cloudbase and ("不上传" in cloudbase or "no upload" in lowered),
        f"bytes={len(cloudbase)}",
    )

    checks.add(
        "the image carries the corpus seed directory",
        bool(re.search(r"(?m)^COPY\s+corpus\s+\./corpus", text))
        and (ROOT / "corpus" / "ai-governance").is_dir(),
        f"copy={bool(re.search(r'(?m)^COPY corpus', text))}",
    )

    entry = ROOT / "app" / "main.py"
    entry_text = _read(entry)
    checks.add(
        "app/main.py exposes a module-level ASGI app",
        bool(re.search(r"(?m)^app\s*=", entry_text)),
        entry_text[:120] or "missing app/main.py",
    )
    smoke = ROOT / "scripts" / "smoke_check.py"
    smoke_text = _read(smoke)
    checks.add(
        "the smoke check exercises health, login, a run and the stream",
        smoke.is_file()
        and "/api/health" in smoke_text
        and "/api/login" in smoke_text
        and "/events" in smoke_text,
        f"bytes={len(smoke_text)}",
    )

    return checks.report(RESULTS_PATH, scope="image, env template and deploy guide")


if __name__ == "__main__":
    sys.exit(main())
