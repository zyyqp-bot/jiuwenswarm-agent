"""Read the framework's stream chunks without guessing their shape.

``OutputSchema`` is ``type/index/payload``; text is inside ``payload``
(``llm_output`` for incremental content, ``answer`` for the finalized result),
and team markers are payload dictionaries whose ``event_type`` starts with
``team.``. Reading ``chunk.content`` — the obvious-looking attribute — finds
nothing, which is exactly what the first version of the live check did.
"""
from __future__ import annotations

from typing import Any


def chunk_field(chunk: object, name: str) -> Any:
    if isinstance(chunk, dict):
        return chunk.get(name)
    return getattr(chunk, name, None)


def chunk_text_and_marker(chunk: object) -> tuple[str, str | None]:
    """Return ``(text, marker)``; exactly one is non-empty for content/markers."""
    payload = chunk_field(chunk, "payload")
    if not isinstance(payload, dict):
        return "", None
    event_type = payload.get("event_type")
    if isinstance(event_type, str) and event_type.startswith("team."):
        return "", event_type
    chunk_type = chunk_field(chunk, "type")
    if chunk_type == "llm_output":
        content = payload.get("content")
        return (content, None) if isinstance(content, str) else ("", None)
    if chunk_type == "answer":
        content = payload.get("content") or payload.get("output")
        return (content, None) if isinstance(content, str) else ("", None)
    return "", None
