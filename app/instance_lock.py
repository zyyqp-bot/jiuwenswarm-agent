"""One data directory belongs to one process.

CloudBase must run exactly one instance: the run registry, the concurrency cap
and SQLite all live in this process. A second container on the same volume
would not crash — it would split the run list and double-admit jobs — so the
process takes a lock in the data directory and refreshes it while serving.

The lock is advisory and best-effort: a fresh file from another host/pid
refuses startup, a stale one (no heartbeat for ``stale_seconds``) is taken
over so a crashed instance does not brick the next deploy.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["InstanceLock", "managed_lifespan"]


@dataclass
class InstanceLock:
    """A heartbeat file under ``data_dir`` that one process owns."""

    data_dir: Path
    # Four missed 5-second beats. Long enough that a live process is never
    # taken over, short enough that a redeploy after SIGKILL does not wait a
    # minute before the new container can start.
    stale_seconds: float = 20.0
    heartbeat_seconds: float = 5.0
    host: str = field(default_factory=socket.gethostname)
    pid: int = field(default_factory=os.getpid)
    clock: Callable[[], float] = time.time

    @property
    def path(self) -> Path:
        return Path(self.data_dir) / "instance.lock"

    def acquire(self) -> None:
        """Take the lock, or refuse when another live instance owns it."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        existing = self._read()
        if existing is not None:
            age = self.clock() - float(existing.get("heartbeat") or 0)
            same_owner = (
                existing.get("host") == self.host
                and int(existing.get("pid") or 0) == self.pid
            )
            if age < self.stale_seconds and not same_owner:
                raise RuntimeError(
                    "another workbench instance "
                    f"({existing.get('host')}:{existing.get('pid')}) is using {self.data_dir}; "
                    "CloudBase must run a single instance"
                )
        self._write()

    def heartbeat(self) -> None:
        self._write()

    async def heartbeat_forever(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                self.heartbeat()
            except OSError:
                # A transient write failure must not kill the server; the next
                # beat retries, and a real storage failure surfaces elsewhere.
                pass

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read(self) -> dict[str, Any] | None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return document if isinstance(document, dict) else None

    def _write(self) -> None:
        payload = {
            "host": self.host,
            "pid": self.pid,
            "heartbeat": self.clock(),
        }
        tmp = self.path.with_name(f"{self.path.name}.{self.pid}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)


@asynccontextmanager
async def managed_lifespan(lock: InstanceLock, service: Any) -> AsyncIterator[None]:
    """Run the heartbeat, and on shutdown close children and release the lock."""
    task = asyncio.create_task(lock.heartbeat_forever())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        lock.release()
        await service.close()
