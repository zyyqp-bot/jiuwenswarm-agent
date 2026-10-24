// The terminal report: the artifact a judge actually takes away.
//
// The server writes report.md when a run ends; this pane fetches it with the
// session cookie and renders it as preformatted text. No Markdown renderer:
// one less dependency, and the raw structure (sections, citations) stays
// visible exactly as generated.

import { useEffect, useState } from "react";
import { reportMarkdown } from "./api.js";

export default function Report({ runId, terminal }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setText("");
    setError("");
    if (!runId || !terminal) return undefined;
    let alive = true;
    setLoading(true);
    reportMarkdown(runId)
      .then((value) => {
        if (alive) setText(value);
      })
      .catch((failure) => {
        if (alive) setError(failure.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [runId, terminal]);

  return (
    <section className="report-pane">
      <header className="drawer-header">
        <h2>报告</h2>
        {terminal && runId ? (
          <a className="ghost small" href={`/api/runs/${encodeURIComponent(runId)}/report.md`}>
            下载 Markdown
          </a>
        ) : null}
      </header>
      {!terminal ? (
        <p className="muted">运行结束后生成报告。</p>
      ) : loading ? (
        <p className="muted">正在读取报告…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
        <pre className="report-body">{text}</pre>
      )}
    </section>
  );
}
