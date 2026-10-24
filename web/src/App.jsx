import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api.js";

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

function Login({ onLoggedIn }) {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
      <form className="login-card" onSubmit={submit}>
        <p className="eyebrow">JiuwenSwarm · 证据工作台</p>
        <h1>带引用的资料研读</h1>
        <p className="muted">
          输入评委账号后开始一次任务：两名成员分工检索与成稿，每条结论都回到
          公开政策原文。
        </p>
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
    return <p className="muted">还没有任务。填写目标后开始第一次运行。</p>;
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

function RunDetail({ detail }) {
  if (!detail) {
    return <p className="muted">选择一次运行，查看任务与证据。</p>;
  }
  const snapshot = detail.snapshot || {};
  const tasks = snapshot.tasks || [];
  const evidence = snapshot.evidence || {};
  const evidenceCount = Object.keys(evidence).length;
  return (
    <section className="run-detail">
      <header>
        <h2>{detail.goal}</h2>
        <span className={`badge phase-${detail.phase}`}>{phaseLabel(detail.phase)}</span>
      </header>
      <dl className="facts">
        <div>
          <dt>计划来源</dt>
          <dd>{snapshot.planner || "等待规划"}</dd>
        </div>
        <div>
          <dt>任务</dt>
          <dd>{tasks.length}</dd>
        </div>
        <div>
          <dt>证据</dt>
          <dd>{evidenceCount}</dd>
        </div>
        <div>
          <dt>停止原因</dt>
          <dd>{detail.stop_reason || "—"}</dd>
        </div>
      </dl>
      {detail.error ? <p className="error">{detail.error}</p> : null}
      <ul className="task-strip">
        {tasks.map((task) => (
          <li key={task.task_id} className={`task-chip status-${task.status || "pending"}`}>
            <strong>{task.task_id}</strong>
            <span>{task.assignee || "未分配"}</span>
            <span>{task.status || "pending"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Workbench({ session, onLoggedOut }) {
  const [workspaceList, setWorkspaceList] = useState([]);
  const [workspace, setWorkspace] = useState("ai-governance");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const unsubscribe = useRef(null);

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
        setDetail((current) => ({
          ...(current || {}),
          phase: event.snapshot?.phase || current?.phase,
          stop_reason: event.snapshot?.stream_stop_reason,
          error: event.snapshot?.error,
          snapshot: event.snapshot,
        }));
        if (event.snapshot?.complete) refreshRuns().catch(() => {});
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

  async function start(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await api.startRun(workspace, goal);
      setGoal("");
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

  return (
    <div className="workbench">
      <header className="topbar">
        <div>
          <p className="eyebrow">JiuwenSwarm · 证据工作台</p>
          <h1>资料研读与带引用报告</h1>
        </div>
        <div className="account">
          <span>{session.user}</span>
          <button type="button" className="ghost" onClick={logout}>
            退出
          </button>
        </div>
      </header>

      <main className="layout">
        <section className="panel controls">
          <h2>新建任务</h2>
          <form onSubmit={start}>
            <label>
              工作区
              <select value={workspace} onChange={(event) => setWorkspace(event.target.value)}>
                {workspaceList.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.description}
                  </option>
                ))}
              </select>
            </label>
            <label>
              目标
              <textarea
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                rows={5}
                placeholder="例如：面向公众的生成式 AI 客服上线前，需要完成哪些合规动作？"
                required
              />
            </label>
            <button type="submit" disabled={busy || !goal.trim()}>
              {busy ? "提交中…" : "开始研读"}
            </button>
          </form>
          {error ? <p className="error">{error}</p> : null}
          <h2>本次会话</h2>
          <RunList runs={runs} selected={selectedId} onSelect={setSelectedId} />
        </section>

        <section className="panel detail">
          <RunDetail detail={detail} />
          {detail && !detail.terminal ? (
            <button type="button" className="ghost danger" onClick={stop}>
              停止本次运行
            </button>
          ) : null}
        </section>
      </main>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(undefined);

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

  if (session === undefined) {
    return (
      <main className="login-page">
        <p className="muted">正在检查会话…</p>
      </main>
    );
  }
  if (session === null) {
    return <Login onLoggedIn={setSession} />;
  }
  return <Workbench session={session} onLoggedOut={() => setSession(null)} />;
}
