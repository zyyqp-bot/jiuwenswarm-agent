# Online Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the loggable online workbench the competition requires: FastAPI service, session-scoped runs, SSE, a React workbench, a single container image, and a deployable link — with the two runtime correctness gaps carried over from the whole-branch review closed.

**Architecture:** One FastAPI process serves the React SPA and a small REST/SSE API. Each run is a **subprocess** (`app/supervisor.py`) that owns the team runtime and writes atomic snapshots; the API monitors the snapshot file and projects it to SSE. SQLite and run artifacts live under one data directory (a persistent volume in production). Auth is a preseeded account exchanged for a signed, HttpOnly cookie; every run is scoped to the login session. Planning is a TinyAgent call with a deterministic fallback; retrieval is the existing FTS5 `KnowledgeIndex` exposed as a member tool, so citations refer to evidence the task actually received.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, `itsdangerous`, aiosqlite, Pydantic v2, React + Vite (plain JS), Docker (multi-stage).

**Spec:** `docs/superpowers/specs/2026-10-24-evidence-workbench-design.md`

## Global Constraints

- Upstream is read-only: agent-core `6df9699e650b3ad9b9835b46ae3c3b7572adb04b`, jiuwenswarm `3fea8d1623a13646e935df9e96100ab7ad470aa4`. All differences live in `app/`.
- Every check runs offline unless the plan explicitly marks it as a paid live run. Offline checks use `checks/harness.py` and write `<name>_results.json`.
- Model credentials live only in the server environment (`.env.local` in dev, container env in production). Never in git, never in the browser, never in a snapshot.
- Public surface: no uploads, no filesystem paths, no model keys. The member tool surface stays deny-by-default (`app/tool_guard.py`), and `member_complete_task` / `create_task` / `update_task` stay denied.
- One container, one instance. SQLite and the in-process run registry do not support horizontal scaling; the operator docs and startup check must say so.
- Python 3.12 from `.venv`; `PYTHONPATH` includes `upstream/agent-core` and `upstream/jiuwenswarm` (PROJECT.md §13).
- Windows development, Linux container: use `pathlib`, never hard-code `/` or `\`.
- Run artifacts are written by atomic replace (temp file + `os.replace`), never partially.

## Review Focus

These are the input classes and failure modes the spec implies but no single task's tests naturally cover. Each line gets its test in the owning task:

1. **Session isolation**: two logins with different cookies, and two logins with the *same* preseeded account, must never see each other's run list, snapshot, report, or SSE stream.
2. **SSE reconnect**: a client that disconnects mid-run and reconnects must receive the current snapshot, not restart the run and not miss terminal state.
3. **Supervisor death**: a run whose subprocess crashes, is killed, or exceeds the deadline must become a terminal `failed` record; it must never remain `running` forever.
4. **Ephemeral disk / multi-instance**: startup must verify the data directory is writable and a marker survives, and must refuse a configuration that sets more than one worker.
5. **Public tool surface**: a model-emitted forbidden tool name must be refused at execution, and no API response may contain credentials, absolute paths outside the data root, or another session's data.

---

### Task 1: Settings and signed-cookie auth

**Files:**
- Create: `app/settings.py`, `app/auth.py`
- Test: `p1/auth_check.py`
- Modify: `p0a/requirements-frozen.txt` (append `fastapi`, `uvicorn`, `itsdangerous`, `python-multipart`, `pypdf`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Settings.from_env(env: Mapping[str,str] | None = None) -> Settings`
  - `Settings` fields: `data_dir: Path`, `users: dict[str,str]`, `session_secret: str`, `environment: str`, `model_base_url: str`, `model_api_key: str`, `model_name: str`, `max_concurrent_runs: int`, `max_runs_per_day: int`, `run_deadline_seconds: int`
  - `Settings.model_credentials() -> dict[str,str]` returning `MODEL_BASE_URL` / `MODEL_API_KEY` / `MODEL_NAME`
  - `Session(user: str, sid: str)` (frozen dataclass)
  - `SessionCodec(secret: str, *, max_age_seconds: int = 604800, clock: Callable[[], float] | None = None)` with `encode(session) -> str`, `decode(token: str) -> Session | None`
  - `credentials_ok(users: Mapping[str,str], user: str, password: str) -> bool`
  - `COOKIE_NAME = "wb_session"`

- [ ] **Step 1: Write the failing test**

`p1/auth_check.py` asserts:
- correct password accepted, wrong password and unknown user rejected (constant-time compare path);
- `SessionCodec.encode` then `decode` returns the same user and a different `sid` per session;
- a tampered token decodes to `None`;
- an expired token decodes to `None` using an injected clock;
- each successful login gets a new `sid` (two encodings of two sessions differ);
- `Settings.from_env({"WORKBENCH_ENV": "production"})` silently missing `SESSION_SECRET` raises `RuntimeError`, while `development` gets an ephemeral generated secret.

Write the assertions with `Checks` and concrete expected values before creating either module.

- [ ] **Step 2: Run it and confirm RED**

Run: `.venv/Scripts/python.exe p1/auth_check.py` (with the documented `PYTHONPATH`)
Expected: `ModuleNotFoundError: No module named 'app.settings'`.

- [ ] **Step 3: Implement the modules**

`app/settings.py`:
- `data_dir` from `WORKBENCH_DATA_DIR` (default `./data`), resolved with `Path.expanduser().resolve()`.
- `users` from `WORKBENCH_USERS`, a semicolon-separated list of `user:password` pairs; an empty value yields `{}`.
- In `production`, `SESSION_SECRET` is required and must be at least 32 characters; in development, generate one with `secrets.token_urlsafe(32)`.
- `max_concurrent_runs` default 2, `max_runs_per_day` default 30, `run_deadline_seconds` default 300.

`app/auth.py`:
- `itsdangerous.URLSafeTimedSerializer(secret, salt="workbench-session")`.
- `encode` writes `{"user": ..., "sid": ...}`; `decode` catches `BadSignature`/`SignatureExpired` and returns `None`.
- `credentials_ok` uses `hmac.compare_digest` on both username and password; it must not short-circuit on unknown user in a way that leaks timing (compare against a dummy constant).

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/auth_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/settings.py app/auth.py p1/auth_check.py p1/auth_results.json p0a/requirements-frozen.txt
git commit -m "feat(service): add settings and signed-cookie auth"
```

---

### Task 2: RunService — subprocess, snapshots, SSE source of truth

**Files:**
- Create: `app/run_service.py`
- Test: `p1/run_service_check.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - `RunRecord` dataclass: `run_id: str`, `session_id: str`, `goal: str`, `workspace: str`, `phase: str`, `stop_reason: str | None`, `error: str | None`, `seq: int`, `snapshot: dict`, `created_at: float`, `process_pid: int | None`
  - `RunService(*, settings: Settings, supervisor_command: Sequence[str] | None = None, poll_seconds: float = 0.25)`
  - `async start(*, session_id: str, goal: str, workspace: str, plan: list[dict] | None = None, evidence_by_task: dict[str,list[str]] | None = None) -> RunRecord`
  - `get(run_id: str, *, session_id: str) -> RunRecord` (raises `RunNotFound`)
  - `list(*, session_id: str) -> list[RunRecord]`
  - `async stop(run_id: str, *, session_id: str) -> RunRecord`
  - `async events(run_id: str, *, session_id: str, after_seq: int = 0) -> AsyncIterator[dict]` yielding `{"seq": int, "type": "snapshot", "snapshot": dict}`
  - `async close() -> None`
  - Exceptions: `RunNotFound`, `AtCapacity`

- [ ] **Step 1: Write the failing test**

`p1/run_service_check.py` uses a **fake supervisor script** (`p1/fake_supervisor.py`) that reads `--payload`, writes three snapshots (`starting`, `streaming` with a result, `returned` with `complete: true`), then exits 0. A second mode exits 1 without writing `complete`. The check:
- `start` returns a record with `phase` eventually `returned` and `snapshot["results"]` present;
- events include an increasing `seq` and the final snapshot is last;
- a different `session_id` gets `RunNotFound` from `get`/`stop`/`events`;
- `stop` on a long-running fake (sleeps) makes the record terminal and the process gone;
- a supervisor that exits 1 leaves `phase == "failed"` with a non-empty `error`;
- starting `max_concurrent + 1` runs raises `AtCapacity`.

- [ ] **Step 2: Run it and confirm RED**

Run: `.venv/Scripts/python.exe p1/run_service_check.py`
Expected: `ModuleNotFoundError: No module named 'app.run_service'`.

- [ ] **Step 3: Implement RunService**

- `start` writes `runs/<run_id>/payload.json` and `snapshot.json` placeholder under `settings.data_dir`; `run_id` is `run-<UTC timestamp>-<6 hex>`.
- Payload includes `run_id`, `goal`, `session_id`, optional `plan`, `members`, `evidence_by_task`, `credentials`, `snapshot_path`, `results_path`, and `corpus` when a workspace provides one (Task 9 adds it; Task 2 passes `None`).
- Spawn with `asyncio.create_subprocess_exec(*command, "--payload", payload_path, cwd=repo_root, env={**os.environ, "PYTHONPATH": ...})`.
- One monitor task per run: every `poll_seconds`, read `snapshot.json`; if it changed, increment `seq` and put `{"seq", "type": "snapshot", "snapshot"}` on that run's bounded queue. Also refresh `phase`/`error`/`stop_reason` from the snapshot.
- Terminal: `complete == true` **or** process exit. If the process exits without a complete snapshot, set `phase="failed"`, `error` to `supervisor exited with code N` (and the last snapshot error if present).
- Enforce a hard `run_deadline_seconds + 60` watchdog; on expiry terminate and mark failed.
- `stop` terminates the process, waits 5s, then kills; the monitor finalizes the record.
- The run registry is a plain dict guarded by an `asyncio.Lock`; this is single-instance by design.

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/run_service_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/run_service.py p1/run_service_check.py p1/fake_supervisor.py p1/run_service_results.json
git commit -m "feat(service): add the run service with subprocess isolation"
```

---

### Task 3: REST + SSE API and SPA hosting

**Files:**
- Create: `app/api.py`, `p1/api_check.py`
- Modify: `app/run_service.py` (only if the API needs `workspace` metadata; no new behavior)

**Interfaces:**
- Consumes: `Settings`, `SessionCodec`, `RunService`.
- Produces:
  - `create_app(*, settings: Settings, service: RunService) -> FastAPI`
  - `require_session(request: Request) -> Session`
  - Routes: `POST /api/login` `{user,password}` → sets cookie, `{user}`; `POST /api/logout`; `GET /api/me`; `GET /api/health`; `GET /api/workspaces`; `POST /api/runs` `{goal,workspace}` → `{run_id}`; `GET /api/runs` → `{runs:[...]}`; `GET /api/runs/{run_id}`; `POST /api/runs/{run_id}/stop`; `GET /api/runs/{run_id}/events` (SSE); `GET /api/runs/{run_id}/report.md` (Task 4 supplies the builder).
  - SSE frames: `event: snapshot\ndata: <json>\n\n`, plus `event: heartbeat` every 15s.

- [ ] **Step 1: Write the failing test**

`p1/api_check.py` builds the app with a `FakeRunService` (in-memory records, an async event iterator) and uses `httpx.AsyncClient(transport=ASGITransport(app=app))`:
- `GET /api/runs` without a cookie → 401;
- wrong password → 401 with no `Set-Cookie`;
- correct login → 200, `Set-Cookie` contains `HttpOnly` and `SameSite=lax`;
- two different logins get different cookies; each sees only its own runs;
- `POST /api/runs` returns a `run_id`, and a second session's `GET` on it → 404 (not 403, to avoid confirming existence);
- SSE response headers are `text/event-stream`, and the first frame is a `snapshot` event with a `seq`;
- `GET /api/health` → 200 without auth;
- an SSE request for another session's run → 404.

- [ ] **Step 2: Run it and confirm RED**

Run: `.venv/Scripts/python.exe p1/api_check.py`
Expected: `ModuleNotFoundError: No module named 'app.api'`.

- [ ] **Step 3: Implement the API**

- Cookies: `httponly=True`, `samesite="lax"`, `secure=settings.environment == "production"`, `max_age=604800`.
- Login compares with `credentials_ok`; a success creates `Session(user, secrets.token_urlsafe(16))`.
- All run routes call `service.get/list/...` with `session_id=session.sid`; `RunNotFound` maps to 404.
- SSE uses `StreamingResponse` with `media_type="text/event-stream"`; it must send a `retry: 3000` line, then snapshots, and break when the record is terminal and the queue is drained.
- SPA: mount `web/dist` at `/` **after** the API routes, only if the directory exists; `GET /` returns `index.html`; unknown non-`/api` paths return `index.html` for client routing.
- `GET /api/health` returns `{"status":"ok","version":...}` and checks `settings.data_dir` is writable.
- Do not return absolute paths or credentials in any response.

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/api_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/api.py p1/api_check.py p1/api_results.json
git commit -m "feat(service): expose the run API with session-scoped SSE"
```

---

### Task 4: Final report and Markdown export

**Files:**
- Create: `app/report.py`, `p1/report_check.py`
- Modify: `app/api.py` (report route), `app/run_service.py` (build the report at finalize)

**Interfaces:**
- Consumes: `Aggregator`, `FinalResult`, `TaskResult`.
- Produces:
  - `build_report(*, run_id: str, results: Sequence[TaskResult], plan: Sequence[PlanTask], unverified: Iterable[str] = (), unavailable: Iterable[str] = (), usage: Any = None) -> FinalResult`
  - `report_markdown(final: FinalResult, *, task_results: Sequence[TaskResult] = ()) -> str`
  - `write_report(path: Path, final: FinalResult, *, task_results: Sequence[TaskResult] = ()) -> None` (writes `report.json` and `report.md` atomically)

- [ ] **Step 1: Write the failing test**

`p1/report_check.py`:
- two results (one fact with evidence, one inference) produce `FinalResult.answer` containing `[fact]` and `[inference]` and the evidence ids;
- a task with no result makes the outcome `partial` and lists the task in limitations;
- `report_markdown` includes a `## 结论`, a `## 限制`, and the evidence id list;
- an empty result set yields `FinalResult` with a non-empty limitation, not a crash;
- `write_report` leaves no `.tmp` file and re-reading the JSON round-trips.

- [ ] **Step 2: Run it and confirm RED**

Run: `.venv/Scripts/python.exe p1/report_check.py`
Expected: `ModuleNotFoundError: No module named 'app.report'`.

- [ ] **Step 3: Implement**

- Call `Aggregator().merge(...)` with `required_acceptance_ids` collected from the plan tasks; pass `unverified`/`unavailable` through.
- Markdown is deterministic: title, goal, per-task claim lines with kind and evidence ids, limitations, and a machine-readable evidence appendix.
- `write_report` uses a temp file in the same directory and `os.replace`, like the supervisor snapshot.

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/report_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/report.py p1/report_check.py p1/report_results.json app/api.py app/run_service.py
git commit -m "feat(service): build the cited final report"
```

---

### Task 5: Native task-board projection

**Files:**
- Modify: `app/run_bridge.py`, `app/run_rail.py`, `app/supervisor.py`
- Test: `p1/run_rail_check.py`

**Interfaces:**
- Consumes: `TeamTaskManager.list_tasks_with_deps(status=None) -> TaskListResult` (`TaskSummary`: `task_id`, `title`, `status`, `assignee`, `blocked_by`, `updated_at`).
- Produces:
  - `RunBridge.record_task_board(tasks: Sequence[dict]) -> None`
  - snapshot gains `"tasks": [...]`
  - `RunBootstrapRail._record_board(manager) -> None`, throttled by `BOARD_REFRESH_SECONDS = 1.0`

- [ ] **Step 1: Write the failing test**

Extend `p1/run_rail_check.py`:
- a fake manager's `list_tasks_with_deps()` returns a `TaskListResult` with one task (`in_progress`, assignee `research`, `blocked_by=["engineering"]`);
- after `after_tool_call(build_team)`, `bridge.snapshot()["tasks"]` equals the model-dumped list;
- two rapid calls refresh at most once (throttle), and a call after the interval refreshes again;
- the board is recorded even when bootstrapping was already done.

- [ ] **Step 2: Run it and confirm RED**

Run: `.venv/Scripts/python.exe p1/run_rail_check.py`
Expected: `KeyError: 'tasks'` or `AttributeError: record_task_board`.

- [ ] **Step 3: Implement**

- `RunBridge.record_task_board` stores a copied list under the lock.
- `_record_board` calls `manager.list_tasks_with_deps()`, converts each `TaskSummary` with `model_dump()`, and swallows/records read errors without failing the run.
- Call it after a successful bootstrap and at the end of every `after_tool_call`; guard with a monotonic timestamp.
- The reporter thread already snapshots the bridge, so no supervisor change is needed beyond passing through.

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/run_rail_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/run_bridge.py app/run_rail.py p1/run_rail_check.py p1/run_rail_results.json
git commit -m "feat(runtime): project the native task board into the snapshot"
```

---

### Task 6: Reviewer settlement — accept only after the native verdict

**Files:**
- Modify: `app/run_bridge.py`, `app/run_rail.py`
- Test: `p1/submit_tool_check.py` and `p1/run_rail_check.py`

**Interfaces:**
- Consumes: `PlanTask.reviewers`, the task board from Task 5.
- Produces:
  - `RunBridge` keeps a plan task with reviewers in `_pending_results` after a gated submission; it is **not** in `results()` until settlement.
  - `RunBridge.settle_review(task_id: str, native_status: str) -> str` returning `"accepted"`, `"rework"`, or `"ignored"`.
  - A rejected review (`in_progress` after `in_review`) discards the pending result, opens a new execution, and records an error string `"review rejected; new execution opened"`.

- [ ] **Step 1: Write the failing tests**

In `p1/submit_tool_check.py` add a plan task with `reviewers=("team_leader",)`:
- submitting a valid result returns ok but `bridge.results()` is empty and the snapshot exposes `pending_reviews`;
- `settle_review(task, "completed")` moves it into `results()` and returns `"accepted"`;
- `settle_review(task, "in_progress")` discards it, returns `"rework"`, and `execution_for(task)` yields a new id;
- settling an unknown task returns `"ignored"`.

In `p1/run_rail_check.py`, the board refresh calls `settle_review` for each task's current status.

- [ ] **Step 2: Run both and confirm RED**

Run: `.venv/Scripts/python.exe p1/submit_tool_check.py` and `.venv/Scripts/python.exe p1/run_rail_check.py`
Expected: pending review is currently accepted immediately (`results()` non-empty), so the new assertions fail.

- [ ] **Step 3: Implement**

- In `_submit`, after the native completion succeeds, if `plan_task.reviewers` is non-empty, store into `_pending_results` (and `_submitted`), return ok with a note.
- `settle_review`: `completed` → move to `_results`; `in_progress` → drop pending and the `_submitted` key, call `execution_for` (opens a new execution), and append the error; other states → `"ignored"`.
- `snapshot()` includes `"pending_reviews": sorted(...)`.

- [ ] **Step 4: Run both and confirm GREEN**

Expected: both checks pass; the full offline suite still passes.

- [ ] **Step 5: Commit**

```bash
git add app/run_bridge.py p1/submit_tool_check.py p1/submit_tool_results.json p1/run_rail_check.py p1/run_rail_results.json
git commit -m "feat(runtime): settle reviewer tasks before accepting results"
```

---

### Task 7: Dependency invariant on the board

**Files:**
- Modify: `app/run_bridge.py`, `app/run_rail.py`
- Test: `p1/run_rail_check.py`

**Interfaces:**
- Consumes: board projection (Task 5), accepted results.
- Produces: `RunBridge.verify_dependencies() -> list[str]` returning violations; `_record_board` calls it and, on a violation, calls `fail()` and raises `AbortError`.

- [ ] **Step 1: Write the failing test**

Extend `p1/run_rail_check.py`:
- a board with an `in_progress` task whose `blocked_by=["research"]` and **no** accepted result for `research` produces a fatal error and raises `AbortError`;
- after `bridge.submit(...)` accepts a `research` result, the same board produces no violation;
- a `blocked`/`pending` task with an unresolved predecessor is not a violation (it has not started).

- [ ] **Step 2: Run and confirm RED**

Expected: no violation is reported today.

- [ ] **Step 3: Implement**

- Only check tasks whose status is `in_progress`.
- A predecessor is satisfied iff it is in `self._results()` (the single completion path makes native `completed` equivalent to app acceptance; this assertion is what keeps that true).
- Return human-readable violations; the rail fails the run with the joined text.

- [ ] **Step 4: Run and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/run_rail_check.py`

- [ ] **Step 5: Commit**

```bash
git add app/run_bridge.py app/run_rail.py p1/run_rail_check.py p1/run_rail_results.json
git commit -m "feat(runtime): assert app acceptance before dependent tasks run"
```

---

### Task 8: Limits — concurrency and daily quota

**Files:**
- Create: `app/limits.py`, `p1/limits_check.py`
- Modify: `app/api.py`, `app/run_service.py` (count running records per session)

**Interfaces:**
- Consumes: `RunService.list`.
- Produces:
  - `Limits(max_concurrent_global: int, max_concurrent_per_session: int, max_runs_per_day: int)`
  - `Limits.check_start(*, running_global: int, running_session: int, runs_today: int) -> None` raising `LimitExceeded(code: str, message: str, status_code: int)` where code is `global_capacity` (503), `session_capacity` (429), `daily_quota` (429)
  - `RunService.runs_today(session_id: str) -> int`

- [ ] **Step 1: Write the failing test**

`p1/limits_check.py` covers each limit at the boundary, the message includes the limit and the current value, and `run_service.runs_today` counts only the session's runs created in the last 24h.

- [ ] **Step 2: Run and confirm RED**

- [ ] **Step 3: Implement**

`Limits` is a pure function over counts; the API maps `LimitExceeded.status_code` and returns `{"error": code, "message": ...}`. `POST /api/runs` checks before `service.start`.

- [ ] **Step 4: Run and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/limits.py p1/limits_check.py p1/limits_results.json app/api.py app/run_service.py
git commit -m "feat(service): enforce concurrency and daily run limits"
```

---

### Task 9: Corpus import, workspaces, and the retrieval tool

**Files:**
- Create: `app/ingest.py`, `app/workspaces.py`, `app/retrieval_tool.py`, `scripts/import_corpus.py`, `p1/retrieval_check.py`
- Modify: `app/hooks.py` (register `app.retrieve`), `app/agent_template.py` (mount it on both roles when configured), `app/supervisor.py` (build the index and pass the tool params), `app/run_bridge.py` (`add_evidence`)

**Interfaces:**
- Consumes: `KnowledgeIndex` (`open(db_path, corpus=..., access_scope=...)`, `add_document(source_uri, source_revision, text)`, `search(query, limit=10, authorized_scopes=(scope,)) -> list[Evidence]`), `Evidence.evidence_id`.
- Produces:
  - `load_document(path: Path) -> str` for `.txt` / `.md` (UTF-8, BOM-tolerant) and `.pdf` via `pypdf`; any other suffix raises `UnsupportedDocument`
  - `import_corpus(*, data_dir: Path, corpus: str, scope: str, source_dir: Path) -> dict` returning `{"documents": n, "chunks": n, "failures": [...]}`
  - `WORKSPACES: dict[str, Workspace]` with `Workspace(name, corpus, access_scope, description)` and one `ai-governance` entry
  - `RetrievalTool(TeamTool)` schema `{task_id, query, limit}`; refuses a `task_id` whose plan assignee differs from the producer; returns `ToolOutput(data={"evidence_ids": [...], "evidence": [{id,text,source_uri,locator}]})`; calls `bridge.add_evidence(task_id, ids)`
  - `make_retrieval_tool(params, context)` reading `db_path`, `corpus`, `access_scope` from `params` and `producer` from `BuildContext.member_name`
  - `RunBridge.add_evidence(task_id, evidence_ids) -> None`

- [ ] **Step 1: Write the failing test**

`p1/retrieval_check.py`:
- import two small Chinese text files; the second import of the same `source_uri` with new text replaces the old chunks (chunk count unchanged, search finds the new term);
- `load_document` handles UTF-8 BOM and rejects `.docx`;
- `search` with a wrong scope returns `[]` before matching (no leakage);
- the tool with the correct producer/task returns evidence ids and `bridge.snapshot()["evidence"]` contains them;
- the tool with a task assigned to someone else returns `success=False` and adds nothing;
- the tool with an unknown task returns an error.

- [ ] **Step 2: Run and confirm RED**

- [ ] **Step 3: Implement**

- `scripts/import_corpus.py` is the admin path: `--data-dir`, `--corpus`, `--scope`, `--source`; prints the summary JSON. The public API never exposes it.
- The supervisor builds `RetrievalTool` only when the payload has a `corpus` block and the database file exists; otherwise the run proceeds without retrieval (offline checks).
- Evidence records are added per task **after** the assignee check, and only ids actually returned by this call.
- `run_bridge.snapshot()` includes `"evidence": {task_id: sorted(ids)}` for the API.

- [ ] **Step 4: Run and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/ingest.py app/workspaces.py app/retrieval_tool.py scripts/import_corpus.py p1/retrieval_check.py p1/retrieval_results.json app/hooks.py app/agent_template.py app/supervisor.py app/run_bridge.py
git commit -m "feat(rag): import a corpus and expose evidence to members"
```

---

### Task 10: Planner — TinyAgent plan with a deterministic fallback

**Files:**
- Create: `app/planner.py`, `p1/planner_check.py`
- Modify: `app/supervisor.py` (plan when `payload["plan"]` is absent), `app/run_service.py` (omit `plan` by default)

**Interfaces:**
- Consumes: `create_tiny_agent(*, system_prompt, model_name, model_resolver, default_schema=None, ...)`, `TinyAgent.run(content, schema=...)`, `spec.agents["leader"].model` (a `TeamModelConfig`; `model_pool` is empty in this app, so the resolver returns it directly).
- Produces:
  - `fallback_plan(goal: str) -> list[PlanTask]` — two tasks: `research` (`assignee="research"`) and `engineering` (`assignee="engineering"`, `depends_on=("research",)`); each instruction names the goal and the retrieval/submit protocol
  - `plan_goal(goal: str, *, model: Any, run: Callable[[str, Any], Awaitable[Any]] | None = None, timeout_seconds: float = 60) -> list[PlanTask]`
  - The planner schema: `{"tasks": [{"task_id","instruction","acceptance_ids","depends_on","assignee"}]}`

- [ ] **Step 1: Write the failing test**

`p1/planner_check.py` injects a fake `run` callable:
- a valid plan payload (two tasks, assignees research/engineering) validates and is returned;
- an unknown assignee (`team_leader`) or a cycle falls back to `fallback_plan`;
- a raised exception/timeout falls back;
- `fallback_plan` itself passes `PlanValidator(members=ASSIGNABLE_MEMBERS)`;
- the leader's task instruction contains the goal text.

- [ ] **Step 2: Run and confirm RED**

- [ ] **Step 3: Implement**

- `plan_goal` builds a TinyAgent with `system_prompt="You plan a two-member research task graph..."`, `model_name="leader"`, `model_resolver=lambda _: model`, `default_schema=PLAN_SCHEMA`, then `await agent.run(goal, schema=PLAN_SCHEMA)`; parse with `PlanTask(**raw)`; validate; fall back on any exception or invalid plan.
- The supervisor calls `plan_goal` **before** constructing `RunBridge`; it needs the compiled spec, so move spec compilation before bridge construction when planning is needed (the current order compiles after validation; restructure without changing the explicit-plan path).
- Record `"planner": "tiny" | "fallback"` in the snapshot.

- [ ] **Step 4: Run and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/planner.py p1/planner_check.py p1/planner_results.json app/supervisor.py app/run_service.py
git commit -m "feat(planner): plan the task graph from the goal"
```

---

### Task 11: Web scaffold, login, and run submission

**Files:**
- Create: `web/package.json`, `web/vite.config.js`, `web/index.html`, `web/src/main.jsx`, `web/src/api.js`, `web/src/App.jsx`, `web/src/styles.css`
- Create: `p1/web_build_check.py`
- Test: `p1/web_build_check.py`

**Interfaces:**
- Consumes: the API from Task 3 (`/api/login`, `/api/me`, `/api/workspaces`, `/api/runs`, `/api/runs/{id}/events`).
- Produces: a built SPA in `web/dist/`; `api.js` exports `login`, `logout`, `me`, `workspaces`, `startRun`, `listRuns`, `getRun`, `stopRun`, `subscribe(runId, onSnapshot)` (EventSource with reconnect).

- [ ] **Step 1: Write the failing test**

`p1/web_build_check.py` runs `npm ci` when `web/package-lock.json` exists, otherwise `npm install` (first run, which generates the lockfile), then `npm run build`, then asserts `web/dist/index.html` exists, contains a root element, and at least one hashed JS asset is referenced; it also asserts the built bundle contains the string `/api/runs` (the contract is wired). The check is offline and deterministic.

- [ ] **Step 2: Run and confirm RED**

Run: `.venv/Scripts/python.exe p1/web_build_check.py`
Expected: missing `web/package.json` (or build failure).

- [ ] **Step 3: Implement the scaffold**

- Vite + React 19, no router (two states: logged out / logged in), no UI library.
- Login form posts to `/api/login` with `credentials: "same-origin"`; on success load `/api/me`.
- Workbench: workspace select, goal textarea, start button, run list, status line.
- `subscribe` uses `EventSource`; on `snapshot` events it calls the provided callback; it reconnects automatically (EventSource does), and the first snapshot carries the full state.
- Never store credentials or tokens in `localStorage`; the session is the HttpOnly cookie.

- [ ] **Step 4: Run and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/web_build_check.py`
Expected: `N/N checks passed`.

- [ ] **Step 5: Commit**

```bash
git add web p1/web_build_check.py p1/web_build_results.json
git commit -m "feat(web): add the Vite shell with login and run submission"
```

---

### Task 12: Workbench panes — task graph, evidence, report

**Files:**
- Create: `web/src/TaskGraph.jsx`, `web/src/EvidenceDrawer.jsx`, `web/src/Report.jsx`
- Modify: `web/src/App.jsx`, `web/src/styles.css`
- Test: `p1/web_build_check.py` (build + string contract) and a recorded manual checklist in PROJECT.md

**Interfaces:**
- Consumes: snapshot `tasks` (Task 5), `evidence` (Task 9), `report.md` (Task 4).
- Produces: four panes — controls/status, task graph (nodes colored by status, edges from `blocked_by`), evidence drawer (click a task/claim → evidence text + source + locator), report (rendered Markdown or a claim list if no Markdown renderer).

- [ ] **Step 1: Write the failing test**

Extend `p1/web_build_check.py`:
- the built bundle contains `task-graph`, `evidence-drawer`, and `report-pane` markers (data attributes or class names);
- `Report.jsx` imports the report fetch path `/report.md`.
Manual checklist added to PROJECT.md: run a real run, watch nodes move, open an evidence item, export Markdown.

- [ ] **Step 2: Run and confirm RED**

- [ ] **Step 3: Implement**

- `TaskGraph` lays nodes out in dependency order (simple topological columns; no force layout).
- Status colors: `pending`, `blocked`, `in_progress`, `in_review`, `completed`, `cancelled`.
- `EvidenceDrawer` shows the evidence id, text, source URI, revision, locator; a claim click filters to its ids.
- `Report` fetches `/api/runs/{id}/report.md` when the run is terminal; otherwise shows "running".
- Degradation rule from the spec: if time is short, a table replaces the graph, but the evidence ids stay visible.

- [ ] **Step 4: Run and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add web p1/web_build_check.py p1/web_build_results.json PROJECT.md
git commit -m "feat(web): add task graph, evidence drawer and report"
```

---

### Task 13: Container image, deploy config, and smoke script

**Files:**
- Create: `deploy/Dockerfile`, `deploy/.env.example`, `deploy/cloudbase.md`, `scripts/smoke_check.py`, `p1/deploy_check.py`
- Modify: `.dockerignore`, `app/api.py` (health already exists)

**Interfaces:**
- Consumes: `web/dist`, `app/`, `upstream/`, `p0a/requirements-frozen.txt`.
- Produces: an image that runs `uvicorn app.api:app` (a module-level app built from env), `/data` volume, `HEALTHCHECK` on `/api/health`, non-root user, and a documented single-instance setting.

- [ ] **Step 1: Write the failing test**

`p1/deploy_check.py` parses the Dockerfile and `.env.example` and asserts:
- two `FROM` stages (node build, python runtime) and a `COPY --from=build /web/dist`;
- `USER` is non-root;
- `HEALTHCHECK` hits `/api/health`;
- `EXPOSE 8000`;
- `ENV PYTHONPATH` names both upstream directories;
- `.env.example` contains no real-looking key (`sk-`/long hex) and lists `SESSION_SECRET`, `WORKBENCH_USERS`, `MODEL_*`, `DATA_DIR`;
- `cloudbase.md` states single instance, persistent volume at `/data`, and no public upload.

- [ ] **Step 2: Run and confirm RED**

- [ ] **Step 3: Implement**

- Multi-stage build: `node:24-alpine` runs `npm ci` (the lockfile is committed by Task 11) then `npm run build`; `python:3.12-slim` installs requirements, copies `app/`, `upstream/agent-core`, `upstream/jiuwenswarm`, `web/dist`; creates `/data` owned by `app`.
- Entry: `uvicorn app.api:app --host 0.0.0.0 --port 8000 --workers 1`.
- `scripts/smoke_check.py --base-url URL --user U --password P` performs health → login → start a tiny run → read the first SSE frame → poll to terminal; exits non-zero on any failure. It never prints the password or cookie.
- `cloudbase.md`: console steps (create environment, image, volume `/data`, env vars, single instance, default domain), plus the verification checklist from the spec (SSE through the gateway, outbound model call, writable volume, no cold-start sleep).

- [ ] **Step 4: Run and confirm GREEN**

Run: `.venv/Scripts/python.exe p1/deploy_check.py`
Expected: `N/N checks passed`. If Docker is available, also `docker build -t workbench:local deploy/` and `docker run --rm -p 8000:8000 ...` manually; record the result in PROJECT.md. Do not block on Docker being absent.

- [ ] **Step 5: Commit**

```bash
git add deploy scripts/smoke_check.py p1/deploy_check.py p1/deploy_results.json .dockerignore
git commit -m "feat(deploy): add the single-container image and smoke check"
```

---

### Task 14: End-to-end offline acceptance and documentation

**Files:**
- Create: `p1/workbench_e2e_check.py`
- Modify: `PROJECT.md` (P2 record), `README.md` (run/deploy instructions)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: one check that exercises the whole offline path with a fake model/supervisor: login → start run → SSE snapshots → task board → retrieval evidence → submit → report → Markdown; plus a `not_run` list for the paid live run.

- [ ] **Step 1: Write the failing test**

`p1/workbench_e2e_check.py` uses the fake supervisor from Task 2 and a temporary corpus, and asserts the full chain above, including session isolation and the report containing an evidence id that the retrieval tool returned. It records `live_run: "not_run"` explicitly.

- [ ] **Step 2: Run and confirm RED, then implement the glue**

The check may reveal missing wiring (e.g., the report route, the workspace registry in the API). Fix the production code, not the assertion, unless the assertion contradicts the spec.

- [ ] **Step 3: Run the full suite**

Run every `p0*/**/*_check.py` and `p1/*_check.py` with `PYTHONPATH` set. Expected: all pass; the new check included.

- [ ] **Step 4: Update docs**

PROJECT.md: version 0.26, P2 record with the measured commands, what is deployed vs not, and the remaining limitations (token accounting, conflicts, agentic multi-round retrieval, second workspace). README: how to run locally, import a corpus, build the image, deploy.

- [ ] **Step 5: Commit**

```bash
git add p1/workbench_e2e_check.py p1/workbench_e2e_results.json PROJECT.md README.md
git commit -m "test(workbench): add the offline end-to-end acceptance chain"
```

---

## Self-Review

**1. Spec coverage:** Online link + login + session isolation (Tasks 1–3, 11); SSE and task board (Tasks 2, 3, 5, 12); cited report (Tasks 4, 9, 12); retrieval degradation is explicit (Task 9 returns no results rather than fabricating vectors); deployment hard gate (Task 13); per-run deadline + limits (Tasks 2, 8); public tool surface already pinned in P1 and re-asserted by the deploy check; two carried correctness obligations (Tasks 6, 7). Not covered here, deferred to Plan 3: structural chunking, agentic multi-round retrieval, conflict candidates, evaluation set, second workspace, token accounting, dynamic members. These are named in PROJECT.md, not silently dropped.

**2. Step scan:** Every task has a failing test first, an exact check command, an implementation description with signatures, and a commit. No "handle edge cases" placeholders.

**3. Type consistency:** `Session.sid` is used as `RunRepository.register_run(session_id=...)`; `RunRecord.session_id` is the login session, not the team session (the team session is derived inside the supervisor). `RunService.events` yields `seq` starting at 1. `RunBridge.record_task_board` stores model-dumped `TaskSummary` dicts; the API and frontend consume the same keys.

**4. Review Focus:** Each of the five lines has an owning test: session isolation (Tasks 3, 14), SSE reconnect (Task 3 + the EventSource contract in Task 11), supervisor death (Task 2), ephemeral disk/multi-instance (Tasks 1/3 health + Task 13 docs/check), public surface (Task 3 no-leak assertions + P1 guard tests + Task 13 env check).

**5. Proportion:** The plan is longer than the spec because deployment and auth have exact env/route contracts; implementation code is left to the tasks except signatures and the two algorithms (planner fallback, report Markdown). No task transcribes a body the tests already determine.
