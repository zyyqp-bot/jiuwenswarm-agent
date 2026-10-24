"""ASGI entrypoint: ``uvicorn app.main:app``.

This module is the only place that builds the application from the process
environment. ``app.api.create_app`` stays injectable so the offline checks can
construct it around a fake run service without a production secret.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api import create_app
from app.run_service import RunService
from app.settings import Settings

__all__ = ["app", "build_app"]


def build_app() -> FastAPI:
    """Build the app from ``os.environ`` (raises on invalid production config)."""
    settings = Settings.from_env()
    return create_app(settings=settings, service=RunService(settings=settings))


app = build_app()
