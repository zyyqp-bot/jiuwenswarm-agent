import test from "node:test";
import assert from "node:assert/strict";

import { mergeSnapshot } from "../src/snapshot.js";

test("a complete snapshot marks the run terminal and carries the report trigger", () => {
  const record = { run_id: "run-1", phase: "starting", terminal: false, goal: "g" };
  const merged = mergeSnapshot(record, {
    phase: "returned",
    complete: true,
    stream_stop_reason: "team_completed",
    error: null,
  });
  assert.equal(merged.terminal, true);
  assert.equal(merged.phase, "returned");
  assert.equal(merged.stop_reason, "team_completed");
  assert.equal(merged.goal, "g");
});

test("an intermediate snapshot keeps a live run live", () => {
  const merged = mergeSnapshot({ run_id: "run-1", phase: "starting" }, {
    phase: "streaming",
    complete: false,
  });
  assert.equal(merged.terminal, false);
  assert.equal(merged.phase, "streaming");
});

test("terminal is sticky across a late non-complete frame", () => {
  const merged = mergeSnapshot({ terminal: true, phase: "failed" }, {
    phase: "starting",
    complete: false,
  });
  assert.equal(merged.terminal, true);
});
