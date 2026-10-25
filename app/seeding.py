"""Index the baked corpus into a fresh data volume on first boot.

The image carries the public regulations under ``/app/corpus``. CloudBase may
not expose a container terminal on every plan, so requiring a manual import
would leave the public link without retrieval. Startup therefore seeds only
when the database is absent; an existing database is never overwritten, and
adding documents later is the explicit CLI path.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.ingest import import_corpus
from app.knowledge import corpus_db_path
from app.workspaces import Workspace

__all__ = ["ensure_corpus"]

logger = logging.getLogger(__name__)


async def ensure_corpus(
    data_dir: Path,
    seed_root: Path,
    workspaces: Iterable[Workspace],
) -> list[dict[str, Any]]:
    """Import each workspace's seed documents when the corpus DB is missing.

    Returns one import summary per seeded workspace; an empty list means the
    database already existed or no workspace had documents.
    """
    data_dir = Path(data_dir)
    seed_root = Path(seed_root)

    summaries: list[dict[str, Any]] = []
    for workspace in workspaces:
        source = seed_root / workspace.corpus
        if not source.is_dir():
            continue
        if corpus_db_path(data_dir, workspace.corpus).exists():
            # One corpus per file: adding a new workspace seeds only its own
            # database and leaves the existing one untouched.
            continue
        documents = [
            path
            for path in source.iterdir()
            if path.is_file() and not path.name.startswith(".")
        ]
        if not documents:
            continue
        summary = await import_corpus(
            data_dir=data_dir,
            corpus=workspace.corpus,
            scope=workspace.access_scope,
            source_dir=source,
        )
        logger.info(
            "seeded corpus %s: %s documents, %s chunks, %s failures",
            workspace.corpus,
            summary["documents"],
            summary["chunks"],
            len(summary["failures"]),
        )
        summaries.append(summary)
    return summaries
