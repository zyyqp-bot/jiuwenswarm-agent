"""P0-D (part 2): the event projection.

PROJECT.md section 5: the same framework event arriving twice must not be
registered twice, and for events without a stable id the key comes from entity
identifiers and transition data -- never from arrival time.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0d/events_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.events import EventProjection, ProjectionError  # noqa: E402
from checks.harness import Checks, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "events_results.json"

RUN = "run-1"
OTHER_RUN = "run-2"


def main() -> int:
    checks = Checks()

    # --- a framework event id is authoritative ------------------------------
    projection = EventProjection()
    first = projection.observe(run_id=RUN, event_type="task.started", framework_event_id="fw-1")
    checks.add(
        "the first event is registered with sequence 1",
        first is not None and first.seq == 1 and first.dedup_key == f"run:{RUN}:fw:fw-1",
        f"seq={first.seq if first else None}",
    )
    checks.add(
        "the same framework event delivered again is not registered twice",
        projection.observe(run_id=RUN, event_type="task.started", framework_event_id="fw-1") is None,
        "",
    )
    checks.add(
        "a redelivery does not advance the sequence",
        projection.last_seq == 1,
        f"last_seq={projection.last_seq}",
    )

    # --- no framework id: entity plus transition data -----------------------
    without_id = EventProjection()
    payload = {"task_id": "t1", "status": "in_progress"}
    without_id.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload)
    checks.add(
        "an event without a framework id de-duplicates on entity plus transition data",
        without_id.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload) is None,
        "",
    )
    checks.add(
        "a different transition of the same entity is a new event",
        without_id.observe(
            run_id=RUN, event_type="task.updated", entity_id="t1", payload={"task_id": "t1", "status": "completed"}
        )
        is not None,
        f"last_seq={without_id.last_seq}",
    )
    checks.add(
        "the same transition of a different entity is a new event",
        without_id.observe(run_id=RUN, event_type="task.updated", entity_id="t2", payload=payload) is not None,
        "",
    )
    checks.add(
        "the registered event carries its entity id as a field",
        without_id.events()[0].entity_id == "t1",
        f"entity_id={without_id.events()[0].entity_id!r}",
    )

    # --- the key is scoped to its run ---------------------------------------
    scoped = EventProjection()
    scoped.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload)
    checks.add(
        "the same entity and transition in another run is a different event",
        scoped.observe(run_id=OTHER_RUN, event_type="task.updated", entity_id="t1", payload=payload) is not None,
        "framework ids and entity ids are only unique within a run",
    )

    # --- arrival time is not part of the key --------------------------------
    timed = EventProjection()
    timed.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload, observed_at="2026-10-12T10:00:00")
    checks.add(
        "an event redelivered later is still the same event",
        timed.observe(
            run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload, observed_at="2026-10-12T11:30:00"
        )
        is None,
        "arrival time is not part of the dedup key",
    )

    # --- payloads must digest stably ----------------------------------------
    ok, detail = raises(
        lambda: EventProjection().observe(run_id=RUN, event_type="task.updated"),
        expected=ProjectionError,
    )
    checks.add("an event with neither identifier is refused rather than keyed by arrival", ok, detail)

    ok, detail = raises(
        lambda: EventProjection().observe(
            run_id=RUN, event_type="task.updated", entity_id="t1", payload={"tags": {"a", "b", "c"}}
        ),
        expected=ProjectionError,
    )
    checks.add(
        "a payload that cannot digest stably is refused instead of hashed per process",
        ok,
        detail,
    )

    reference_keyed = EventProjection()
    reference_keyed.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload_ref="ref://1")
    checks.add(
        "an event identified only by a payload reference de-duplicates on that reference",
        reference_keyed.observe(
            run_id=RUN, event_type="task.updated", entity_id="t1", payload_ref="ref://1"
        )
        is None,
        "",
    )

    # --- sequence is monotonic ----------------------------------------------
    sequential = EventProjection()
    for index in range(3):
        sequential.observe(run_id=RUN, event_type="task.updated", entity_id=f"t{index}")
    checks.add(
        "sequences are handed out in order",
        [event.seq for event in sequential.events()] == [1, 2, 3],
        f"seqs={[event.seq for event in sequential.events()]}",
    )
    checks.add(
        "event ids embed the run and the sequence",
        sequential.events()[0].event_id == f"{RUN}:1",
        sequential.events()[0].event_id,
    )

    # --- recovery reconciles against the snapshot ---------------------------
    recovered = EventProjection()
    recovered.observe(run_id=RUN, event_type="task.updated", entity_id="t1", payload=payload)
    snapshot = [
        {"type": "task.updated", "entity_id": "t1", "payload": payload},  # already seen
        {"type": "task.updated", "entity_id": "t2", "payload": payload},  # missed
        {"type": "task.started", "framework_event_id": "fw-9"},  # missed, had an id
    ]
    result = recovered.rebuild_from_snapshot(run_id=RUN, snapshot=snapshot)
    checks.add(
        "rebuilding from a snapshot registers only what was missed",
        result.added == 2 and result.skipped == () and recovered.last_seq == 3,
        f"added={result.added} skipped={result.skipped} last_seq={recovered.last_seq}",
    )
    checks.add(
        "replaying the same snapshot again adds nothing",
        recovered.rebuild_from_snapshot(run_id=RUN, snapshot=snapshot).added == 0,
        "",
    )

    checks.add(
        "a recovered event is registered now, so its sequence is the registration order",
        recovered.events()[-1].seq == 3 and recovered.events()[-1].type == "task.started",
        f"seqs={[e.seq for e in recovered.events()]}",
    )

    # --- one unusable snapshot row does not cost the reconciliation ---------
    partial = EventProjection()
    result = partial.rebuild_from_snapshot(
        run_id=RUN,
        snapshot=[
            {"type": "task.updated", "entity_id": "t1", "payload": payload},
            {"type": "task.orphan"},  # neither id: unusable
            {"type": "task.updated", "entity_id": "t2", "payload": payload},
        ],
    )
    checks.add(
        "an unusable snapshot row is skipped, and the rows after it are still recovered",
        result.added == 2 and len(result.skipped) == 1,
        f"added={result.added} skipped={len(result.skipped)}",
    )
    checks.add(
        "the skipped row is reported with a reason",
        result.skipped and "task.orphan" in result.skipped[0],
        result.skipped[0] if result.skipped else "",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure event projection; no framework, no Runner, no model",
    )


if __name__ == "__main__":
    sys.exit(main())
