// Evidence and the claims that cite it.
//
// A claim is only credible if you can open the passage behind it, so the
// drawer pairs the task's claims with the passages the task actually
// retrieved. Clicking a claim narrows the list to its evidence ids; clicking
// it again clears the filter. Everything here comes from the run snapshot:
// no corpus re-read, no second request.

function uniqueIds(evidence, taskId) {
  if (taskId) return evidence?.[taskId] || [];
  const seen = new Set();
  for (const ids of Object.values(evidence || {})) {
    for (const id of ids || []) seen.add(id);
  }
  return [...seen].sort();
}

export default function EvidenceDrawer({
  evidence,
  details,
  results,
  selectedTask,
  claimEvidenceIds,
  onSelectClaim,
}) {
  const ids = uniqueIds(evidence, selectedTask).filter(
    (id) => !claimEvidenceIds || claimEvidenceIds.includes(id),
  );
  const claims = selectedTask ? results?.[selectedTask]?.claims || [] : [];

  return (
    <section className="evidence-drawer">
      <header className="drawer-header">
        <h2>证据</h2>
        {claimEvidenceIds ? (
          <button type="button" className="ghost small" onClick={() => onSelectClaim?.(null)}>
            清除筛选
          </button>
        ) : null}
      </header>

      {claims.length ? (
        <ul className="claim-list">
          {claims.map((claim, index) => {
            const active =
              claimEvidenceIds &&
              claim.evidence_ids?.length === claimEvidenceIds.length &&
              claim.evidence_ids.every((id) => claimEvidenceIds.includes(id));
            return (
              <li key={`${index}-${claim.text.slice(0, 12)}`}>
                <button
                  type="button"
                  className={active ? "claim active" : "claim"}
                  onClick={() => onSelectClaim?.(active ? null : claim.evidence_ids || [])}
                >
                  <span className="claim-kind">{claim.kind || "claim"}</span>
                  {claim.text}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {ids.length === 0 ? (
        <p className="muted">
          {selectedTask ? "该任务尚未返回带出处的证据。" : "运行产生证据后显示在这里。"}
        </p>
      ) : (
        <ul className="evidence-list">
          {ids.map((id) => {
            const item = details?.[id];
            return (
              <li key={id} className="evidence-item">
                {item ? (
                  <>
                    {/* The source leads: a judge should read "which document,
                        which section", not a percent-encoded chunk id. */}
                    <p className="evidence-source">
                      {item.source_uri}
                      {item.locator ? ` · ${item.locator}` : ""}
                    </p>
                    <p className="evidence-text">{item.text}</p>
                    <p className="evidence-meta">
                      {item.source_revision ? `修订 ${item.source_revision}` : ""}
                      <code className="evidence-id" title={id}>
                        {id}
                      </code>
                    </p>
                  </>
                ) : (
                  <>
                    <code className="evidence-id">{id}</code>
                    <p className="evidence-meta">该证据的正文不在本次快照中。</p>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
