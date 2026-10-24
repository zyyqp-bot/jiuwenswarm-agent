"""P1: the web shell builds and its bundle wires the run API.

The SPA is a build artifact: the container serves ``web/dist``, so a broken
build is a broken deployment. This check installs from the committed lockfile
(or generates one on the first run), builds, and asserts the contract the
backend exposes: the login and run endpoints are present in the bundle, the
mount point exists, and no credential is kept in ``localStorage``.

Offline after the first install. The build is the product; there is no
browser here.

Run: .venv/Scripts/python.exe p1/web_build_check.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = WEB / "dist"
RESULTS_PATH = Path(__file__).resolve().parent / "web_build_results.json"

sys.path.insert(0, str(ROOT))

from checks.harness import Checks  # noqa: E402

API_EXPORTS = (
    "login",
    "logout",
    "me",
    "workspaces",
    "startRun",
    "listRuns",
    "getRun",
    "stopRun",
    "subscribe",
)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=WEB,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _tail(text: str, limit: int = 1200) -> str:
    clean = (text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def _dist_bundle() -> str:
    parts = []
    for path in sorted((DIST / "assets").glob("*.js")):
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def main() -> int:
    # npm prints box-drawing and check-mark glyphs that the Windows GBK
    # console cannot encode; the report must not fail on a passing build.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = Checks()
    package = WEB / "package.json"
    has_package = package.is_file()
    checks.add("web/package.json exists", has_package, str(package))

    if not has_package:
        for name in (
            "npm install succeeds",
            "npm run build succeeds",
            "dist/index.html exists",
            "index.html has the SPA mount point",
            "index.html references a hashed JS asset",
            "the bundle calls /api/runs",
            "the bundle calls /api/login",
            "the bundle keeps no credential in localStorage",
            "api.js exports the run API",
        ):
            checks.add(name, False, "no web/package.json")
        return checks.report(RESULTS_PATH, scope="SPA build and API wiring")

    npm = shutil.which("npm")
    install = None
    if npm is None:
        checks.add("npm install succeeds", False, "npm not found on PATH")
    else:
        lockfile = WEB / "package-lock.json"
        install = _run([npm, "ci"] if lockfile.is_file() else [npm, "install"])
        checks.add(
            "npm install succeeds",
            install.returncode == 0,
            _tail(install.stdout + install.stderr),
        )

    build = None
    if install is not None and install.returncode == 0:
        build = _run([npm, "run", "build"])
        checks.add(
            "npm run build succeeds",
            build.returncode == 0,
            _tail(build.stdout + build.stderr),
        )
    else:
        checks.add("npm run build succeeds", False, "install did not succeed")

    built = build is not None and build.returncode == 0
    index = DIST / "index.html"
    checks.add("dist/index.html exists", built and index.is_file(), str(index))

    html = index.read_text(encoding="utf-8", errors="replace") if index.is_file() else ""
    checks.add(
        "index.html has the SPA mount point",
        'id="root"' in html,
        html[:200],
    )
    checks.add(
        "index.html references a hashed JS asset",
        bool(re.search(r'src="[^"]*assets/[^"]+\.js"', html)),
        html[:300],
    )

    bundle = _dist_bundle() if DIST.is_dir() else ""
    checks.add(
        "the bundle calls /api/runs",
        "/api/runs" in bundle,
        f"bundle_bytes={len(bundle)}",
    )
    checks.add(
        "the bundle calls /api/login",
        "/api/login" in bundle,
        f"bundle_bytes={len(bundle)}",
    )
    checks.add(
        "the bundle keeps no credential in localStorage",
        "localStorage" not in bundle,
        "localStorage found" if "localStorage" in bundle else "no localStorage use",
    )

    source = WEB / "src" / "api.js"
    text = source.read_text(encoding="utf-8", errors="replace") if source.is_file() else ""
    missing = [name for name in API_EXPORTS if not re.search(rf"\bexport\b[^\n]*\b{name}\b", text)]
    checks.add(
        "api.js exports the run API",
        source.is_file() and not missing,
        f"missing={missing}" if missing else "all exported",
    )

    return checks.report(RESULTS_PATH, scope="SPA build and API wiring")


if __name__ == "__main__":
    sys.exit(main())
