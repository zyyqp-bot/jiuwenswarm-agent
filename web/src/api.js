// The only module that talks to the backend.
//
// The session lives in an HttpOnly cookie: this file never reads, writes or
// stores a token, and nothing here touches localStorage. Requests go to the
// same origin, so the cookie travels without a CORS bargain.

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // A non-JSON error body is not worth masking the status for.
    }
    throw new Error(`${response.status} ${detail}`);
  }

  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

export async function login(user, password) {
  return request("/api/login", {
    method: "POST",
    body: JSON.stringify({ user, password }),
  });
}

export async function logout() {
  return request("/api/logout", { method: "POST" });
}

export async function me() {
  return request("/api/me");
}

export async function workspaces() {
  return request("/api/workspaces");
}

export async function startRun(workspace, goal) {
  return request("/api/runs", {
    method: "POST",
    body: JSON.stringify({ workspace, goal }),
  });
}

export async function listRuns() {
  return request("/api/runs");
}

export async function getRun(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function stopRun(runId) {
  return request(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
}

export async function reportMarkdown(runId) {
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/report.md`,
    { credentials: "same-origin" },
  );
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.text();
}

// EventSource reconnects on its own; the callback receives the full snapshot
// each time, so a reconnect cannot leave the view with a partial state.
// Returns a function that closes the stream.
export function subscribe(runId, onSnapshot, onError) {
  const source = new EventSource(
    `/api/runs/${encodeURIComponent(runId)}/events`,
  );
  source.addEventListener("snapshot", (event) => {
    try {
      onSnapshot(JSON.parse(event.data));
    } catch (error) {
      onError?.(error);
    }
  });
  source.addEventListener("heartbeat", () => {});
  source.onerror = () => {
    onError?.(new Error("事件流已断开，正在重连…"));
  };
  return () => source.close();
}
