"""Orchestrate runs as child processes and expose them to the API.

The API process never hosts the agent runtime. Each run is a subprocess that
writes atomic snapshots; this service reads them, keeps one record per run, and
serves the records to the session that created them.

Why a subprocess: the framework's round termination is not reliable enough to
host inside the web server, and a crash must not take the service down. The
snapshot is the only contract between parent and child.

Single instance by design: the registry, the concurrency cap and SQLite all
live in this process.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import Claim, ClaimKind, TaskOutcome, TaskResult
from app.plan import PlanTask
from app.report import build_report, write_report
from app.settings import Settings
from app.workspaces import get_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUPERVISOR = [sys.executable, str(REPO_ROOT / "app" / "supervisor.py")]
TERMINATION_GRACE_SECONDS = 5.0


class RunNotFound(KeyError):
    """The run does not exist, or does not belong to this session."""


class AtCapacity(RuntimeError):
    """The global concurrent-run cap is reached."""


@dataclass
class RunRecord:
    """What the API knows about one run."""

    run_id: str
    session_id: str
    goal: str
    workspace: str
    phase: str = "starting"
    stop_reason: str | None = None
    error: str | None = None
    seq: int = 0
    snapshot: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    process_pid: int | None = None
    terminal: bool = False
    stop_requested: bool = False
    plan: list[dict] = field(default_factory=list)
    report_error: str | None = None


class RunService:
    """Spawn, observe and stop runs."""

    def __init__(
        self,
        *,
        settings: Settings,
        supervisor_command: Sequence[str] | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        self._settings = settings
        self._command = list(supervisor_command or DEFAULT_SUPERVISOR)
        self._poll = poll_seconds
        self._records: dict[str, RunRecord] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._monitors: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # -- lifecycle ----------------------------------------------------------
    async def start(
        self,
        *,
        session_id: str,
        goal: str,
        workspace: str,
        plan: list[dict] | None = None,
        evidence_by_task: dict[str, list[str]] | None = None,
    ) -> RunRecord:
        """Spawn one supervisor and return its record."""
        if not goal.strip():
            raise ValueError("goal cannot be empty")

        async with self._lock:
            running = sum(1 for record in self._records.values() if not record.terminal)
            if running >= self._settings.max_concurrent_runs:
                raise AtCapacity(
                    f"concurrent run capacity reached "
                    f"({running}/{self._settings.max_concurrent_runs})"
                )

            run_id = _new_run_id()
            run_dir = self._settings.data_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = run_dir / "snapshot.json"
            results_path = run_dir / "results.json"
            payload_path = run_dir / "payload.json"

            # Credentials travel in the child's environment, never in a file:
            # /data is a persistent volume that may be backed up or inspected.
            payload: dict[str, Any] = {
                "run_id": run_id,
                "goal": goal.strip(),
                "workspace": workspace,
                "snapshot_path": str(snapshot_path),
                "results_path": str(results_path),
            }
            if plan is not None:
                payload["plan"] = plan
            if evidence_by_task is not None:
                payload["evidence_by_task"] = evidence_by_task
            workspace_config = get_workspace(workspace)
            corpus_db = self._settings.data_dir / "corpus.db"
            if workspace_config is not None and corpus_db.is_file():
                payload["corpus"] = {
                    "db_path": str(corpus_db),
                    "corpus": workspace_config.corpus,
                    "access_scope": workspace_config.access_scope,
                }
            _write_json(payload_path, payload)

            record = RunRecord(
                run_id=run_id,
                session_id=session_id,
                goal=goal.strip(),
                workspace=workspace,
                plan=list(plan or []),
            )
            self._records[run_id] = record

            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command,
                    "--payload",
                    str(payload_path),
                    cwd=str(REPO_ROOT),
                    env=_child_env(self._settings.model_credentials()),
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller
                record.phase = "failed"
                record.error = f"supervisor could not start: {type(exc).__name__}: {exc}"
                record.terminal = True
                self._finalize_snapshot(record)
                raise

            record.process_pid = process.pid
            self._processes[run_id] = process
            self._monitors[run_id] = asyncio.create_task(self._monitor(record, process))
            return record

    def get(self, run_id: str, *, session_id: str) -> RunRecord:
        record = self._records.get(run_id)
        if record is None or record.session_id != session_id:
            raise RunNotFound(run_id)
        return record

    def list(self, *, session_id: str) -> list[RunRecord]:
        return sorted(
            (record for record in self._records.values() if record.session_id == session_id),
            key=lambda record: record.created_at,
        )

    async def stop(self, run_id: str, *, session_id: str) -> RunRecord:
        """Terminate the run's process and make the record terminal."""
        record = self.get(run_id, session_id=session_id)
        if record.terminal:
            return record
        record.stop_requested = True
        process = self._processes.get(run_id)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=TERMINATION_GRACE_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        # The child may have written its final snapshot between the monitor's
        # last poll and this click; read the file so Stop cannot lose results.
        final = self._read_snapshot(record)
        if final is not None:
            self._adopt_snapshot(record, final)
        self._settle_stopped(record)
        self._finalize_snapshot(record)
        self._write_report(record)
        record.terminal = True
        return record

    async def events(
        self,
        run_id: str,
        *,
        session_id: str,
        after_seq: int = 0,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield snapshots newer than ``after_seq`` until the run is terminal.

        A heartbeat is emitted when nothing changed for ``heartbeat_seconds``,
        so a planning phase that produces no snapshot does not leave the SSE
        connection looking dead to a platform proxy.
        """
        last = max(0, int(after_seq))
        last_emit = time.monotonic()
        while True:
            record = self.get(run_id, session_id=session_id)
            if record.seq > last:
                last = record.seq
                last_emit = time.monotonic()
                yield {"seq": last, "type": "snapshot", "snapshot": dict(record.snapshot)}
            elif time.monotonic() - last_emit >= heartbeat_seconds:
                last_emit = time.monotonic()
                yield {"seq": last, "type": "heartbeat"}
            if record.terminal:
                return
            await asyncio.sleep(min(self._poll, 0.1))

    async def close(self) -> None:
        """Terminate every child and cancel every monitor."""
        for run_id, process in list(self._processes.items()):
            if process.returncode is None:
                process.terminate()
        await asyncio.gather(
            *(process.wait() for process in self._processes.values()),
            return_exceptions=True,
        )
        for task in self._monitors.values():
            task.cancel()
        await asyncio.gather(*self._monitors.values(), return_exceptions=True)
        self._processes.clear()
        self._monitors.clear()

    def admission_counts(self, session_id: str) -> dict[str, int]:
        """Return the three counts the admission limits need."""
        return {
            "running_global": sum(1 for record in self._records.values() if not record.terminal),
            "running_session": sum(
                1
                for record in self._records.values()
                if not record.terminal and record.session_id == session_id
            ),
            "runs_today": self.runs_today(session_id),
        }

    def runs_today(self, session_id: str) -> int:
        """Count this session's runs started in the last 24 hours."""
        cutoff = time.time() - 86400
        return sum(
            1
            for record in self._records.values()
            if record.session_id == session_id and record.created_at >= cutoff
        )

    # -- monitoring ---------------------------------------------------------
    async def _monitor(self, record: RunRecord, process: asyncio.subprocess.Process) -> None:
        snapshot_path = self._settings.data_dir / "runs" / record.run_id / "snapshot.json"
        last_text = ""
        deadline = time.monotonic() + self._settings.run_deadline_seconds + 60.0
        try:
            while True:
                if record.terminal:
                    return

                text = _read_text(snapshot_path)
                if text and text != last_text:
                    last_text = text
                    document = _parse_json(text)
                    if document is not None:
                        record.seq += 1
                        self._adopt_snapshot(record, document)
                        if document.get("complete"):
                            await self._finish_from_snapshot(record, process, document)
                            return

                if process.returncode is not None:
                    if record.stop_requested:
                        self._settle_stopped(record)
                    else:
                        record.phase = "failed"
                        record.error = record.error or (
                            f"supervisor exited with code {process.returncode}"
                        )
                    self._finalize_snapshot(record)
                    self._write_report(record)
                    record.terminal = True
                    return

                if time.monotonic() > deadline:
                    process.kill()
                    await process.wait()
                    record.phase = "failed"
                    record.error = "run deadline exceeded"
                    self._finalize_snapshot(record)
                    self._write_report(record)
                    record.terminal = True
                    return

                await asyncio.sleep(self._poll)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the record must still terminate
            record.phase = "failed"
            record.error = f"monitor failed: {type(exc).__name__}: {exc}"
            self._finalize_snapshot(record)
            self._write_report(record)
            record.terminal = True
        finally:
            self._processes.pop(record.run_id, None)
            if record.terminal:
                self._finalize_snapshot(record)

    async def _finish_from_snapshot(
        self,
        record: RunRecord,
        process: asyncio.subprocess.Process,
        document: dict[str, Any],
    ) -> None:
        """Classify a complete snapshot using the exit code, not the phase alone.

        A run that hit its deadline or idle grace writes ``complete`` and exits
        1; reporting that as "returned" would show a green run that produced
        nothing. Wait briefly for the exit so the classification is not a race.
        """
        try:
            returncode: int | None = await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            process.terminate()
            try:
                returncode = await asyncio.wait_for(
                    process.wait(), timeout=TERMINATION_GRACE_SECONDS
                )
            except asyncio.TimeoutError:
                process.kill()
                returncode = await process.wait()

        if record.stop_requested:
            self._settle_stopped(record)
        elif document.get("stream_stop_reason") == "team_completed" and returncode == 0:
            record.phase = "returned"
        else:
            record.phase = "failed"
            record.error = record.error or (
                "run ended without native completion "
                f"(stop_reason={document.get('stream_stop_reason')}, exit={returncode})"
            )
        self._finalize_snapshot(record)
        self._write_report(record)
        record.terminal = True

    def _adopt_snapshot(self, record: RunRecord, document: dict[str, Any]) -> None:
        """Copy the child's snapshot into the record."""
        record.snapshot = document
        record.phase = str(document.get("phase") or record.phase)
        record.stop_reason = document.get("stream_stop_reason")
        record.error = document.get("error")
        # The child plans when the request carried no plan; adopt it so the
        # report describes the graph that actually ran.
        if isinstance(document.get("plan"), list) and document["plan"]:
            record.plan = document["plan"]

    def _settle_stopped(self, record: RunRecord) -> None:
        """Stop is not allowed to erase a completion that already happened."""
        snapshot = record.snapshot or {}
        if (
            snapshot.get("complete")
            and snapshot.get("stream_stop_reason") == "team_completed"
            and not snapshot.get("error")
        ):
            record.phase = "returned"
            record.stop_reason = "team_completed"
            record.error = None
            return
        record.phase = "stopped"
        record.stop_reason = "stopped_by_user"
        record.error = None

    def _finalize_snapshot(self, record: RunRecord) -> None:
        """Publish one terminal snapshot so SSE subscribers see the end state.

        Without this, a failed or stopped run streams its last live snapshot
        and closes: the client never learns the run ended and reconnects.
        """
        record.seq += 1
        record.snapshot = {
            **(record.snapshot or {}),
            "run_id": record.run_id,
            "phase": record.phase,
            "complete": True,
            "stream_stop_reason": record.stop_reason,
            "error": record.error,
        }

    def _read_snapshot(self, record: RunRecord) -> dict[str, Any] | None:
        path = self._settings.data_dir / "runs" / record.run_id / "snapshot.json"
        return _parse_json(_read_text(path))

    def _write_report(self, record: RunRecord) -> None:
        """Write the report once, never letting a report failure fail the run."""
        run_dir = self._settings.data_dir / "runs" / record.run_id
        if (run_dir / "report.json").exists():
            return
        try:
            plan = [PlanTask(**raw) for raw in record.plan]
            results = _results_from_snapshot(record)
            final = build_report(run_id=record.run_id, results=results, plan=plan)
            write_report(run_dir, final, task_results=results)
        except Exception as exc:  # noqa: BLE001 - the run's state is authoritative
            record.report_error = f"{type(exc).__name__}: {exc}"


def _results_from_snapshot(record: RunRecord) -> list[TaskResult]:
    """Rebuild accepted results from the snapshot the child wrote."""
    results: list[TaskResult] = []
    for task_id, raw in (record.snapshot.get("results") or {}).items():
        if not isinstance(raw, dict):
            continue
        claims = tuple(
            Claim(
                text=str(item.get("text", "")),
                kind=ClaimKind(str(item.get("kind") or "inference")),
                evidence_ids=tuple(str(item_id) for item_id in item.get("evidence_ids") or ()),
            )
            for item in raw.get("claims") or []
            if isinstance(item, dict)
        )
        results.append(
            TaskResult(
                run_id=record.run_id,
                task_id=str(task_id),
                execution_id=str(raw.get("execution_id") or f"{record.run_id}:{task_id}:1"),
                producer=str(raw.get("producer") or ""),
                outcome=TaskOutcome(str(raw.get("outcome") or "success")),
                artifact_refs=tuple(str(item) for item in raw.get("artifact_refs") or ()),
                claims=claims,
            )
        )
    return results


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run-{stamp}-{secrets.token_hex(3)}"


def _child_env(credentials: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    # The supervisor runs as a file, so Python puts app/ (not the repository
    # root) on sys.path; without the root the child cannot import `app.*`.
    paths = [
        str(REPO_ROOT),
        str(REPO_ROOT / "upstream" / "agent-core"),
        str(REPO_ROOT / "upstream" / "jiuwenswarm"),
    ]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(paths + ([existing] if existing else []))
    # Credentials are passed in the environment, not the payload file.
    for key, value in (credentials or {}).items():
        env[key] = value
    return env


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


