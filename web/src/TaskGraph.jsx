// The task graph in dependency order: one column per topological level.
//
// No force layout and no graph library. The board is small (2-4 tasks), and a
// deterministic column layout is what lets a judge read "who waits for whom"
// at a glance. A cycle cannot reach here -- PlanValidator rejects it -- but
// the depth walk still fails soft rather than looping if one ever did.

const STATUS_LABELS = {
  pending: "待开始",
  blocked: "被阻塞",
  in_progress: "进行中",
  in_review: "待复核",
  completed: "已完成",
  cancelled: "已取消",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "待开始";
}

function columnsFor(tasks) {
  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const depth = new Map();
  const visiting = new Set();

  function depthOf(taskId) {
    if (depth.has(taskId)) return depth.get(taskId);
    if (visiting.has(taskId)) return 0;
    visiting.add(taskId);
    const task = byId.get(taskId);
    const dependencies = (task?.blocked_by || []).filter((id) => byId.has(id));
    const level = dependencies.length
      ? 1 + Math.max(...dependencies.map(depthOf))
      : 0;
    visiting.delete(taskId);
    depth.set(taskId, level);
    return level;
  }

  const columns = [];
  for (const task of tasks) {
    const level = depthOf(task.task_id);
    if (!columns[level]) columns[level] = [];
    columns[level].push(task);
  }
  return columns;
}

export default function TaskGraph({ tasks, selectedTask, onSelect }) {
  if (!tasks.length) {
    return <p className="muted">计划生成后，任务会出现在这里。</p>;
  }
  const columns = columnsFor(tasks);
  return (
    <div className="task-graph">
      {columns.map((column, index) => (
        <div className="graph-column" key={index}>
          <p className="column-label">阶段 {index + 1}</p>
          {column.map((task) => (
            <button
              type="button"
              key={task.task_id}
              className={[
                "graph-node",
                `status-${task.status || "pending"}`,
                selectedTask === task.task_id ? "selected" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onSelect?.(task.task_id)}
            >
              <strong>{task.task_id}</strong>
              <span>{task.assignee || "未分配"}</span>
              <span className="node-status">{statusLabel(task.status)}</span>
              {task.blocked_by?.length ? (
                <small>依赖 {task.blocked_by.join("、")}</small>
              ) : null}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
