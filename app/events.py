"""Event projection: register framework events exactly once.

PROJECT.md section 5 fixes the rule this module implements:

    Events are consumed as a de-duplicating projection: the same framework
    event arriving twice must not be registered twice. For events the
    framework gives no stable event id, the dedup key is built from known
    entity identifiers and the transition data -- **never** from arrival time,
    which would make every redelivery look like a brand new event.

That last clause is the whole point. A time-based key does not merely fail to
de-duplicate: it actively fabricates events on every retry, and those phantom
events then look exactly like real ones to every downstream consumer.

Two consequences shape the implementation:

- a key must be **stable across processes**. Serialising a payload with
  ``default=str`` is not: ``str(set)`` depends on Python's randomised string
  hashing, so the same redelivery handled by a different worker would hash
  differently and be registered as new. Payloads must therefore be
  JSON-serialisable, and a payload that is not is refused rather than hashed
  into something unstable;
- a key must be **scoped to its run**. Framework event ids are only unique
  within a framework's own sequence, and entity ids are only unique within a
  run, so the run is part of the key.

PROJECT.md also requires recovery to rebuild the projection from a native task
snapshot rather than trusting the event stream, so this module exposes that
path too -- and it degrades rather than aborting: one unusable snapshot row
must not cost the whole reconciliation.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["EventProjection", "ProjectionError", "RebuildResult", "RunEvent"]


class ProjectionError(RuntimeError):
    """An event cannot be given a stable de-duplication key."""


class RunEvent(BaseModel):
    """One registered framework event.

    ``seq`` is the **registration** order, not the occurrence order: an event
    recovered from a snapshot after the fact is appended and numbered now.
    ``observed_at`` carries when the event was seen, and ``framework_event_ref``
    what the framework called it.

    ``dedup_key`` is recorded so a projection rebuilt from a snapshot can be
    compared against the live one, and so a duplicate can be explained rather
    than silently dropped. ``entity_id`` is stored separately rather than left
    inside the key, so a consumer can group events by entity without having to
    parse it back out.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    seq: int
    run_id: str
    session_id: str | None = None
    framework_event_ref: str | None = None
    entity_id: str | None = None
    dedup_key: str
    type: str
    payload_ref: str | None = None
    observed_at: str | None = None


class RebuildResult(BaseModel):
    """What a snapshot reconciliation did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    added: int
    skipped: tuple[str, ...] = ()
    """Snapshot rows that could not be keyed, with why. Reported rather than
    raised: an unusable row must not cost the remaining reconciliation."""


def _canonical(payload: Mapping[str, Any] | None) -> str:
    """Return a stable digest of an event's transition data.

    Raises:
        ProjectionError: the payload is not JSON-serialisable. Any fallback
            (``default=str`` and friends) renders some values in an order that
            varies per process, which would make redeliveries hash differently
            and be registered as new events.
    """
    if payload is None:
        return "none"
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ProjectionError(
            f"event payload must be JSON-serialisable to be de-duplicated stably: {exc}"
        ) from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class EventProjection:
    """De-duplicating projection of a run's framework events."""

    def __init__(self) -> None:
        self._dedup_keys: set[str] = set()
        self._events: list[RunEvent] = []
        self._last_seq = 0

    @property
    def last_seq(self) -> int:
        """The highest sequence handed out, for resuming a client's read."""
        return self._last_seq

    def events(self) -> tuple[RunEvent, ...]:
        """Return the registered events in registration order, oldest first."""
        return tuple(self._events)

    def dedup_key(
        self,
        *,
        run_id: str,
        event_type: str,
        framework_event_id: str | None = None,
        entity_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        payload_ref: str | None = None,
    ) -> str:
        """Return the stable key identifying one framework event.

        A framework-supplied id wins when there is one; otherwise the key is
        the run, the event type, the entity and a digest of the transition
        data. An event identified only by a payload reference keys on the
        reference, so a producer that consistently uses references (or
        consistently inlines payloads) still de-duplicates.

        Raises:
            ProjectionError: neither a framework id nor an entity id is given,
                so redeliveries could not be told apart; or the payload cannot
                be digested stably.
        """
        if framework_event_id:
            return f"run:{run_id}:fw:{framework_event_id}"
        if not entity_id:
            raise ProjectionError(
                f"event of type {event_type!r} carries neither a framework event id nor an entity id; "
                "its redeliveries cannot be distinguished"
            )

        if payload is not None:
            detail = _canonical(payload)
        elif payload_ref:
            detail = f"ref:{payload_ref}"
        else:
            detail = "none"
        return f"run:{run_id}:ent:{event_type}:{entity_id}:{detail}"

    def observe(
        self,
        *,
        run_id: str,
        event_type: str,
        framework_event_id: str | None = None,
        entity_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        payload_ref: str | None = None,
        session_id: str | None = None,
        observed_at: str | None = None,
    ) -> RunEvent | None:
        """Register an event, or return None when it is a duplicate.

        Returning None rather than raising is what makes redelivery harmless:
        the caller does not have to distinguish "already seen" from "failed".

        A producer must identify the same event the same way each time --
        inlined payload or payload reference, not one then the other, since the
        two digest differently.
        """
        key = self.dedup_key(
            run_id=run_id,
            event_type=event_type,
            framework_event_id=framework_event_id,
            entity_id=entity_id,
            payload=payload,
            payload_ref=payload_ref,
        )
        if key in self._dedup_keys:
            return None

        self._dedup_keys.add(key)
        self._last_seq += 1
        event = RunEvent(
            event_id=f"{run_id}:{self._last_seq}",
            seq=self._last_seq,
            run_id=run_id,
            session_id=session_id,
            framework_event_ref=framework_event_id,
            entity_id=entity_id,
            dedup_key=key,
            type=event_type,
            payload_ref=payload_ref,
            observed_at=observed_at,
        )
        self._events.append(event)
        return event

    def rebuild_from_snapshot(
        self,
        *,
        run_id: str,
        snapshot: Iterable[Mapping[str, Any]],
    ) -> RebuildResult:
        """Re-register a native task snapshot, reporting what it did.

        PROJECT.md section 5 requires this path: events can be lost, so
        recovery reconciles against the framework's own snapshot rather than
        assuming the stream was complete. Feeding the snapshot back through the
        same dedup keys means an event that *did* arrive is not registered
        twice, and one that was lost is registered now.

        A row that cannot be keyed is skipped and named in the result. Aborting
        the whole reconciliation over one unnamed row would leave the
        projection half-rebuilt and the remaining lost events unrecoverable.
        """
        added = 0
        skipped: list[str] = []
        for entry in snapshot:
            try:
                event = self.observe(
                    run_id=run_id,
                    event_type=str(entry.get("type", "snapshot")),
                    framework_event_id=entry.get("framework_event_id"),
                    entity_id=entry.get("entity_id"),
                    payload=entry.get("payload"),
                    payload_ref=entry.get("payload_ref"),
                    session_id=entry.get("session_id"),
                    observed_at=entry.get("observed_at"),
                )
            except ProjectionError as exc:
                skipped.append(str(exc))
                continue
            if event is not None:
                added += 1
        return RebuildResult(added=added, skipped=tuple(skipped))
