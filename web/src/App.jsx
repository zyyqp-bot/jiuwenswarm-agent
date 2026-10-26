import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api.js";
import Conflicts from "./Conflicts.jsx";
import EvidenceDrawer from "./EvidenceDrawer.jsx";
import { useLiquidGlass } from "./glass.js";
import Report from "./Report.jsx";
import TaskGraph from "./TaskGraph.jsx";
import { mergeSnapshot } from "./snapshot.js";

const PHASE_LABELS = {
  starting: "启动中",
  streaming: "执行中",
  returned: "已返回",
  invalid_plan: "计划无效",
  failed: "失败",
  stopped: "已停止",
};

function phaseLabel(value) {
  return PHASE_LABELS[value] || value || "未知";
}

function formatTime(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString("zh-CN", {
    hour12: false,
  });
}

function Landing({ onEnter }) {
  return (
    <main className="landing" onClick={onEnter}>
      <div className="landing-beam" aria-hidden="true" />
      <div className="landing-inner">
        <p className="landing-eyebrow">JIUWENSWARM · MULTI-AGENT RESEARCH</p>
        <h1 className="landing-wordmark">
          Evidence<span>{" "}Workbench</span>
        </h1>
        <p className="landing-tagline">可审计的多 Agent 资料研读与交付</p>
        <button
          type="button"
          className="enter-button"
          onClick={(event) => {
            event.stopPropagation();
            onEnter();
          }}
        >
          点击进入
        </button>
      </div>
      <p className="landing-foot">结论级引用 · 冲突保留 · 失败可见</p>
    </main>
  );
}

function Login({ onLoggedIn, onBack }) {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cardRef = useLiquidGlass(22);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await api.login(user, password);
      onLoggedIn(session);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <button type="button" className="back-link" onClick={onBack}>
        ← 返回
      </button>
      <form ref={cardRef} className="login-card glass-surface" onSubmit={submit}>
        <p className="eyebrow">JiuwenSwarm · Evidence Workbench</p>
        <h1>登录后开始研读</h1>
        <p className="muted">两名成员分工检索与成稿，每条结论都回到公开政策原文。</p>
        <label>
          账号
          <input
            value={user}
            onChange={(event) => setUser(event.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </main>
  );
}

function RunList({ runs, selected, onSelect }) {
  if (runs.length === 0) {
    return <p className="muted">还没有任务。写下一个目标开始第一次运行。</p>;
  }
  return (
    <ul className="run-list">
      {runs.map((run) => (
        <li key={run.run_id}>
          <button
            type="button"
            className={run.run_id === selected ? "run-item selected" : "run-item"}
            onClick={() => onSelect(run.run_id)}
          >
            <span className="run-goal">{run.goal}</span>
            <span className={`badge phase-${run.phase}`}>{phaseLabel(run.phase)}</span>
            <span className="run-time">{formatTime(run.created_at)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function Composer({
  workspace,
  workspaceList,
  onWorkspace,
  goal,
  onGoal,
  onStart,
  busy,
  docked,
}) {
  function keyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onStart(event);
    }
  }

  return (
    <form className={docked ? "composer docked" : "composer"} onSubmit={onStart}>
      <div className="composer-box glass-surface">
        <textarea
          value={goal}
          onChange={(event) => onGoal(event.target.value)}
          onKeyDown={keyDown}
          rows={docked ? 2 : 3}
          placeholder="描述你想研读的问题，例如：学校引入 AI 作业批改工具前，需要完成哪些合规动作？"
          required
        />
        <div className="composer-bar">
          <select value={workspace} onChange={(event) => onWorkspace(event.target.value)}>
            {workspaceList.map((item) => (
              <option key={item.name} value={item.name}>
                {item.description}
              </option>
            ))}
          </select>
          <button type="submit" disabled={busy || !goal.trim()}>
            {busy ? "提交中…" : "开始研读 ↑"}
          </button>
        </div>
      </div>
      {!docked ? (
        <p className="composer-hint">Enter 发送 · Shift + Enter 换行 · 公网只读预置语料</p>
      ) : null}
    </form>
  );
}

function AgentRun({ detail, onStop }) {
  const [selectedTask, setSelectedTask] = useState(null);
  const [claimEvidenceIds, setClaimEvidenceIds] = useState(null);

  const snapshot = detail.snapshot || {};
  const tasks = snapshot.tasks || [];
  const evidence = snapshot.evidence || {};
  const evidenceCount = Object.values(evidence).reduce(
    (total, ids) => total + (ids?.length || 0),
    0,
  );
  const terminal = Boolean(detail.terminal);
  const steps = [
    { label: "规划", done: Boolean(snapshot.planner) || tasks.length > 0 },
    { label: "分派", done: tasks.length > 0 },
    { label: "检索", done: evidenceCount > 0 },
    { label: "汇总", done: terminal && detail.phase === "returned" },
  ];

  function selectTask(taskId) {
    setSelectedTask(taskId);
    setClaimEvidenceIds(null);
  }

  return (
    <section className="agent-card">
      <header className="agent-head">
        <span className={`status-dot phase-${detail.phase}`} />
        <div className="agent-title">
          <strong>Agent 团队</strong>
          <span className={`badge phase-${detail.phase}`}>{phaseLabel(detail.phase)}</span>
        </div>
        {!terminal ? (
          <button type="button" className="ghost small" onClick={onStop}>
            停止
          </button>
        ) : null}
      </header>

      <ol className="agent-steps">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "done" : ""}>
            <span className="step-dot" />
            {step.label}
          </li>
        ))}
      </ol>

      {detail.error ? <p className="error">{detail.error}</p> : null}

      <details className="agent-section" open>
        <summary>
          任务图 <span className="count">{tasks.length}</span>
        </summary>
        <TaskGraph tasks={tasks} selectedTask={selectedTask} onSelect={selectTask} />
      </details>

      <details
        className="agent-section"
        open={evidenceCount > 0 && evidenceCount <= 12}
        onToggle={(event) => {
          if (!event.target.open) setClaimEvidenceIds(null);
        }}
      >
        <summary>
          证据 <span className="count">{evidenceCount}</span>
        </summary>
        <EvidenceDrawer
          evidence={evidence}
          details={snapshot.evidence_details}
          results={snapshot.results}
          selectedTask={selectedTask}
          claimEvidenceIds={claimEvidenceIds}
          onSelectClaim={setClaimEvidenceIds}
        />
      </details>

      {terminal && detail.phase === "returned" ? (
        <>
          <Conflicts conflicts={detail.conflicts || []} />
          <Report runId={detail.run_id} terminal={terminal} />
        </>
      ) : terminal ? (
        <p className="agent-waiting">
          {detail.phase === "stopped"
            ? "运行已停止，未生成报告。"
            : "运行未正常完成，报告未生成；原因见上方状态。"}
        </p>
      ) : (
        <p className="agent-waiting">成员正在检索与成稿，报告会在完成后出现。</p>
      )}
    </section>
  );
}

function Workbench({ session, onLoggedOut }) {
  const topbarRef = useLiquidGlass(18);
  const [workspaceList, setWorkspaceList] = useState([]);
  const [workspace, setWorkspace] = useState("education-policy");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [composerOpen, setComposerOpen] = useState(true);
  const unsubscribe = useRef(null);
  const threadEnd = useRef(null);

  const refreshRuns = useCallback(async () => {
    const payload = await api.listRuns();
    setRuns(payload.runs || []);
    return payload.runs || [];
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const payload = await api.workspaces();
        if (!alive) return;
        setWorkspaceList(payload.workspaces || []);
        if (payload.workspaces?.length) setWorkspace(payload.workspaces[0].name);
        await refreshRuns();
      } catch (failure) {
        if (alive) setError(failure.message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshRuns]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return undefined;
    }
    let alive = true;
    (async () => {
      try {
        const record = await api.getRun(selectedId);
        if (alive) setDetail(record);
      } catch (failure) {
        if (alive) setError(failure.message);
      }
    })();

    unsubscribe.current?.();
    unsubscribe.current = api.subscribe(
      selectedId,
      (event) => {
        if (!alive) return;
        setDetail((current) => mergeSnapshot(current, event.snapshot));
        if (event.snapshot?.complete) {
          // The server closes the stream, but EventSource reconnects on its
          // own; close it or the page retries a finished run forever.
          unsubscribe.current?.();
          unsubscribe.current = null;
          refreshRuns().catch(() => {});
          // Conflicts are computed when the report is written, after the
          // terminal frame, so the record has to be re-read once.
          api
            .getRun(selectedId)
            .then((record) => {
              if (alive) setDetail(record);
            })
            .catch(() => {});
        }
      },
      (failure) => {
        if (alive) setError(failure.message);
      },
    );

    return () => {
      alive = false;
      unsubscribe.current?.();
      unsubscribe.current = null;
    };
  }, [selectedId, refreshRuns]);

  useEffect(() => {
    if (detail) threadEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail?.seq, detail]);

  async function start(event) {
    event?.preventDefault?.();
    if (!goal.trim()) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.startRun(workspace, goal);
      setGoal("");
      setHistoryOpen(false);
      setComposerOpen(false);
      await refreshRuns();
      setSelectedId(created.run_id);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!selectedId) return;
    setError("");
    try {
      setDetail(await api.stopRun(selectedId));
      await refreshRuns();
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      onLoggedOut();
    }
  }

  function newTask() {
    setSelectedId(null);
    setDetail(null);
    setGoal("");
    setHistoryOpen(false);
    setComposerOpen(true);
  }

  const active = Boolean(detail);

  return (
    <div className="chat-workbench">
      <header ref={topbarRef} className="topbar glass-surface">
        <button type="button" className="brand" onClick={newTask}>
          Evidence Workbench
        </button>
        <div className="account">
          <button type="button" className="ghost small" onClick={() => setHistoryOpen(true)}>
            历史 {runs.length ? `(${runs.length})` : ""}
          </button>
          <span>{session.user}</span>
          <button type="button" className="ghost small" onClick={logout}>
            退出
          </button>
        </div>
      </header>

      <main className={active ? "chat chat-active" : "chat chat-start"}>
        <div className="chat-scroll">
          {!active ? (
            <>
              <section className="chat-hero">
                <p className="eyebrow">MULTI-AGENT RESEARCH</p>
                <h1>今天想研读什么？</h1>
                <p className="muted">
                  给出一个目标，Leader 规划、两名成员取证与成稿，结论逐条回到原文。
                </p>
              </section>
              <Composer
                workspace={workspace}
                workspaceList={workspaceList}
                onWorkspace={setWorkspace}
                goal={goal}
                onGoal={setGoal}
                onStart={start}
                busy={busy}
                docked={false}
              />
            </>
          ) : (
            <div className="thread">
              <article className="user-message">
                <p>{detail.goal}</p>
                <span className="message-meta">{formatTime(detail.created_at)}</span>
              </article>
              <AgentRun detail={detail} onStop={stop} />
              <div ref={threadEnd} />
            </div>
          )}
          {error ? <p className="error chat-error">{error}</p> : null}
        </div>

        {active ? (
          <>
            <div className="composer-zone" onMouseEnter={() => setComposerOpen(true)} />
            {!composerOpen ? (
              <button
                type="button"
                className="composer-handle"
                onMouseEnter={() => setComposerOpen(true)}
                onClick={() => setComposerOpen(true)}
              >
                继续提问
              </button>
            ) : null}
            <div
              className={composerOpen ? "composer-shell open" : "composer-shell"}
              onMouseLeave={() => {
                if (!goal.trim()) setComposerOpen(false);
              }}
            >
              <Composer
                workspace={workspace}
                workspaceList={workspaceList}
                onWorkspace={setWorkspace}
                goal={goal}
                onGoal={setGoal}
                onStart={start}
                busy={busy}
                docked
              />
            </div>
          </>
        ) : null}
      </main>

      {historyOpen ? (
        <div className="history-layer" onClick={() => setHistoryOpen(false)}>
          <aside className="history-drawer glass-surface" onClick={(event) => event.stopPropagation()}>
            <header>
              <h2>本次会话</h2>
              <button type="button" className="ghost small" onClick={() => setHistoryOpen(false)}>
                关闭
              </button>
            </header>
            <button type="button" className="new-run" onClick={newTask}>
              ＋ 新建任务
            </button>
            <RunList
              runs={runs}
              selected={selectedId}
              onSelect={(runId) => {
                setSelectedId(runId);
                setHistoryOpen(false);
              }}
            />
          </aside>
        </div>
      ) : null}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(undefined);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .me()
      .then((payload) => {
        if (alive) setSession(payload);
      })
      .catch(() => {
        if (alive) setSession(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!entered) {
    return <Landing onEnter={() => setEntered(true)} />;
  }
  if (session === undefined) {
    return (
      <main className="login-page">
        <p className="muted">正在检查会话…</p>
      </main>
    );
  }
  if (session === null) {
    return <Login onLoggedIn={setSession} onBack={() => setEntered(false)} />;
  }
  return (
    <Workbench
      session={session}
      onLoggedOut={() => {
        setSession(null);
        setEntered(false);
      }}
    />
  );
}
