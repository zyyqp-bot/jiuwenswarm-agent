// Candidate conflicts: show both sides, never pick one.
//
// The server only detects "same sentence skeleton, different sources,
// different values". The panel therefore states the rule alongside the
// candidates, and an empty list says "nothing found by this rule" rather than
// "these documents agree" — those are different claims.

export default function Conflicts({ conflicts = [] }) {
  return (
    <section className="conflicts-pane">
      <header className="drawer-header">
        <h2>冲突候选</h2>
        <span className="muted small">同一句式 · 不同来源 · 不同数值；仅并列，不裁决</span>
      </header>
      {conflicts.length === 0 ? (
        <p className="muted">
          未发现候选冲突（规则：同一句式、不同来源、不同数值）。
        </p>
      ) : (
        <ul className="conflict-list">
          {conflicts.map((candidate) => (
            <li key={`${candidate.pattern}-${(candidate.values || []).join("-")}`} className="conflict-item">
              <p className="conflict-values">
                数值：{(candidate.values || []).join(" / ")}
              </p>
              <p className="muted small">{candidate.pattern}</p>
              <ul>
                {(candidate.sides || []).map((side) => (
                  <li key={`${side.task_id}-${side.evidence_id}`}>
                    <p>{side.claim_text}</p>
                    <p className="muted small">
                      {side.source_uri}
                      {side.locator ? ` · ${side.locator}` : ""} ·{" "}
                      <code>{side.evidence_id}</code>
                    </p>
                    {side.passage ? <blockquote>{side.passage}</blockquote> : null}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
