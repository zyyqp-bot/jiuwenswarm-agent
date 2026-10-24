"""ASGI entrypoint: ``uvicorn app.main:app``.

This module is the only place that builds the application from the process
environment. ``app.api.create_app`` stays injectable so the offline checks can
construct it around a fake run service without a production secret.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import create_app
from app.instance_lock import InstanceLock, managed_lifespan
from app.run_service import RunService
from app.seeding import ensure_corpus
from app.settings import Settings
from app.workspaces import WORKSPACES

__all__ = ["app", "build_app"]

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_app() -> FastAPI:
    """Build the app from ``os.environ`` (raises on invalid production config)."""
    settings = Settings.from_env()
    # Refuse a second instance before serving: the registry and concurrency
    # cap are process-local, so two containers on one volume would diverge.
    lock = InstanceLock(settings.data_dir)
    lock.acquire()
    service = RunService(settings=settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # The image carries the public corpus; a fresh volume gets an index
        # without needing a container terminal. WORKBENCH_SEED_CORPUS=0 skips.
        if os.environ.get("WORKBENCH_SEED_CORPUS", "1") != "0":
            await ensure_corpus(settings.data_dir, REPO_ROOT / "corpus", WORKSPACES.values())
        async with managed_lifespan(lock, service):
            yield

    return create_app(settings=settings, service=service, lifespan=lifespan)


app = build_app()
