"""HTTP surface: auth, runs, SSE and the single-page app.

Every run route resolves through the login session's id, so one browser cannot
read, stop or stream another browser's run even when both use the same
preseeded account.

The app is deliberately small: no upload endpoint, no filesystem path in a
response, no credentials in a response, and no CORS (the SPA is same-origin).
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import COOKIE_NAME, Session, SessionCodec, credentials_ok
from app.limits import LimitExceeded, Limits
from app.run_service import AtCapacity, RunNotFound, RunRecord, RunService
from app.settings import Settings
from app.workspaces import get_workspace, workspace_summaries

REPO_ROOT = Path(__file__).resolve().parents[1]
COOKIE_MAX_AGE = 604800
_PLACEHOLDER = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>Workbench</title></head>
<body style="font-family:system-ui;margin:3rem">
<h1>Workbench API is running</h1>
<p>The web client is not built in this image yet.</p>
<p><a href="/api/health">health</a></p>
</body></html>"""


class LoginBody(BaseModel):
    user: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RunBody(BaseModel):
    goal: str = Field(min_length=1)
    workspace: str = "ai-governance"


def create_app(
    *,
    settings: Settings,
    service: RunService,
    codec: SessionCodec | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app around an injected run service."""
    _ensure_data_dir(settings.data_dir)
    session_codec = codec or SessionCodec(settings.session_secret)
    static_root = static_dir if static_dir is not None else REPO_ROOT / "web" / "dist"
    limits = Limits(
        max_concurrent_global=settings.max_concurrent_runs,
        max_concurrent_per_session=settings.max_concurrent_per_session,
        max_runs_per_day=settings.max_runs_per_day,
    )

    app = FastAPI(title="Evidence Workbench", docs_url=None, redoc_url=None)

    def require_session(request: Request) -> Session:
        session = session_codec.decode(request.cookies.get(COOKIE_NAME, ""))
        if session is None:
            raise HTTPException(status_code=401, detail="login required")
        return session

    def record_or_404(run_id: str, session: Session) -> RunRecord:
        try:
            return service.get(run_id, session_id=session.sid)
        except RunNotFound:
            # 404 rather than 403: another session must not learn that the id exists.
            raise HTTPException(status_code=404, detail="run not found") from None

    # -- auth ---------------------------------------------------------------
    @app.post("/api/login")
    async def login(body: LoginBody, response: Response) -> dict[str, str]:
        if not credentials_ok(settings.users, body.user, body.password):
            raise HTTPException(status_code=401, detail="invalid credentials")
        session = Session(user=body.user, sid=secrets.token_urlsafe(16))
        response.set_cookie(
            COOKIE_NAME,
            session_codec.encode(session),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=settings.environment == "production",
            path="/",
        )
        return {"user": session.user}

    @app.post("/api/logout")
    async def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me")
    async def me(session: Session = Depends(require_session)) -> dict[str, str]:
        return {"user": session.user}

    # -- public health ------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "writable": os.access(settings.data_dir, os.W_OK),
        }

    @app.get("/api/workspaces")
    async def workspaces(session: Session = Depends(require_session)) -> dict[str, Any]:
        return {"workspaces": workspace_summaries()}

    # -- runs ---------------------------------------------------------------
    @app.post("/api/runs")
    async def start_run(body: RunBody, session: Session = Depends(require_session)) -> dict[str, str]:
        if get_workspace(body.workspace) is None:
            raise HTTPException(status_code=400, detail="unknown workspace")
        try:
            limits.check_start(**service.admission_counts(session.sid))
        except LimitExceeded as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
        try:
            record = await service.start(
                session_id=session.sid,
                goal=body.goal,
                workspace=body.workspace,
            )
        except AtCapacity as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": record.run_id}

    @app.get("/api/runs")
    async def list_runs(session: Session = Depends(require_session)) -> dict[str, Any]:
        return {"runs": [_record_json(record) for record in service.list(session_id=session.sid)]}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, session: Session = Depends(require_session)) -> dict[str, Any]:
        return _record_json(record_or_404(run_id, session))

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str, session: Session = Depends(require_session)) -> dict[str, Any]:
        record_or_404(run_id, session)
        return _record_json(await service.stop(run_id, session_id=session.sid))

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        after_seq: int = Query(default=0, ge=0),
        session: Session = Depends(require_session),
    ) -> StreamingResponse:
        record_or_404(run_id, session)

        async def frames():
            yield "retry: 3000\n\n"
            async for event in service.events(
                run_id, session_id=session.sid, after_seq=after_seq
            ):
                if event.get("type") == "heartbeat":
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: snapshot\ndata: {payload}\n\n"

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/runs/{run_id}/report.md")
    async def report_markdown(
        run_id: str, session: Session = Depends(require_session)
    ) -> FileResponse:
        record_or_404(run_id, session)
        path = settings.data_dir / "runs" / run_id / "report.md"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="report not ready")
        return FileResponse(path, media_type="text/markdown; charset=utf-8")

    # -- the single-page app ------------------------------------------------
    index = static_root / "index.html"
    assets = static_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa(full_path: str) -> Any:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="not found")
        if index.is_file():
            candidate = (static_root / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and static_root.resolve() in candidate.parents
            ):
                return FileResponse(candidate)
            return FileResponse(index)
        return HTMLResponse(_PLACEHOLDER)

    return app


def _record_json(record: RunRecord) -> dict[str, Any]:
    """The public view of a run. Never a pid, a credential or a path."""
    return {
        "run_id": record.run_id,
        "workspace": record.workspace,
        "goal": record.goal,
        "phase": record.phase,
        "stop_reason": record.stop_reason,
        "error": record.error,
        "seq": record.seq,
        "created_at": record.created_at,
        "terminal": record.terminal,
        "snapshot": record.snapshot,
    }


def _ensure_data_dir(path: Path) -> None:
    """Create the data directory and prove it is writable at startup."""
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(f"data directory {path} is not writable: {exc}") from exc
