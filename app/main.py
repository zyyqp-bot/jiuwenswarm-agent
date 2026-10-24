"""ASGI entrypoint: ``uvicorn app.main:app``.

This module is the only place that builds the application from the process
environment. ``app.api.create_app`` stays injectable so the offline checks can
construct it around a fake run service without a production secret.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api import create_app
from app.instance_lock import InstanceLock, managed_lifespan
from app.run_service import RunService
from app.settings import Settings

__all__ = ["app", "build_app"]


def build_app() -> FastAPI:
    """Build the app from ``os.environ`` (raises on invalid production config)."""
    settings = Settings.from_env()
    # Refuse a second instance before serving: the registry and concurrency
    # cap are process-local, so two containers on one volume would diverge.
    lock = InstanceLock(settings.data_dir)
    lock.acquire()
    service = RunService(settings=settings)
    return create_app(
        settings=settings,
        service=service,
        lifespan=lambda _app: managed_lifespan(lock, service),
    )


app = build_app()
