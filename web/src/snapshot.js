// One place that folds a streamed snapshot into the record the views read.
//
// The SSE frame is the authority on phase and completion: the run-list record
// fetched at selection time still says "starting" when the run has already
// finished. `terminal` is sticky because a late reconnect must not revive a
// finished run.

export function mergeSnapshot(record, snapshot) {
  const complete = Boolean(snapshot && snapshot.complete);
  return {
    ...(record || {}),
    phase: (snapshot && snapshot.phase) || (record && record.phase),
    stop_reason:
      snapshot && snapshot.stream_stop_reason !== undefined
        ? snapshot.stream_stop_reason
        : record && record.stop_reason,
    error: snapshot && snapshot.error !== undefined ? snapshot.error : record && record.error,
    terminal: complete || Boolean(record && record.terminal),
    snapshot: snapshot || (record && record.snapshot) || {},
  };
}
