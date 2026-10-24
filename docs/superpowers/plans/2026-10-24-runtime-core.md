# Runtime Core & Delegation Nail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove and then productise the path from an app-submitted task graph to members executing it and returning structured, citation-checked results — without shell/file tools on the public surface.

**Architecture:** The app validates a plan whose tasks carry `assignee`, converts it to native `TaskGraphSpec`s, and submits it from a leader-mounted rail at the first task iteration. Teammates receive an app-injected `submit_task_result` tool bound to their `BuildContext.member_name`; it validates citations and completion against the app ledger, then completes the native task. A supervisor subprocess owns the run and flushes state every 0.5 s.

**Tech Stack:** Python 3.12, agent-core/JiuwenSwarm (pinned), pydantic v2, asyncio, SQLite/aiosqlite (later tasks), the repo's `checks/harness.py` assertion style.

**Spec:** `docs/superpowers/specs/2026-10-24-evidence-workbench-design.md`

## Global Constraints

- Upstream commits are pinned: agent-core `6df9699e650b3ad9b9835b46ae3c3b7572adb04b`, jiuwenswarm `3fea8d1623a13646e935df9e96100ab7ad470aa4`. Never edit `upstream/`.
- `team_mode="predefined"`, `dispatch_mode="scheduled"`, `spawn_mode="inprocess"`, `lifecycle="persistent"`; no dynamic spawn.
- Model credentials come from the environment or `.env.local`; never print or commit them.
- Every check script must be runnable as `.venv/Scripts/python.exe <script>` with `PYTHONPATH` = `upstream/agent-core;upstream/jiuwenswarm`.
- Real-model checks cost money and are marked as such; offline checks must run without credentials.
- The public agent surface must not include harness shell/file tools, `spawn_*`, or any tool that writes outside the run directory.
- Results are immutable once submitted; a result from a superseded execution must be rejected.

## Review Focus

1. A member submits a result for a task assigned to someone else → **rejected**, because the producer is bound at build time and compared with the task's assignee.
2. A `fact` claim cites evidence the task did not receive → **completion blocked** by `CitationValidator`.
3. The deployed agent still offers shell/file/spawn tools → **fail the run** by asserting on the captured model request tool names.
4. The bootstrap rail fires twice (retry / second iteration) → native tasks are submitted **once**, and duplicate submissions are rejected.
5. A submit tool is asked to complete a result with no claims and no artifact → **rejected**, so a member cannot clear a task by sending an empty shell.

The pre-execution dependency guard (business validity of a predecessor, not just the native edge) is deliberately **not** in this plan: it needs the app result store from Plan 2 and is exercised by the dependency-failure scenario there. It must not be forgotten — the spec's §5.3 step 5 owns it.

---

### Task 1: Workbench agent template with a hardened tool surface

**Files:**
- Create: `app/agent_template.py`
- Test: `p1/workbench_template_check.py`

**Interfaces:**
- Consumes: `app.config_adapter.compile_team_spec(config: dict) -> TeamAgentSpec`.
- Produces:
  - `build_workbench_config(credentials: Mapping[str, str], *, team_name: str, run_id: str) -> dict`
  - The returned config sets, for both `agents.agent_leader` and `agents.agent_teammate`:
    - `"enable_sys_operation": False`
    - `"tools": [{"type": "app.submit_task_result", "params": {"run_id": run_id}}]`
  - The leader template additionally has `"rails": [{"type": "app.run_bootstrap", "params": {"run_id": run_id}}]`.
  - Team-level gates match `p0b/compile_spec_check.build_config()`: `team_mode="predefined"`, `dispatch_mode="scheduled"`, `enable_hitt=False`, `enable_bridge=False`, `enable_swarmflow=False`, `external_cli_agents=[]`.

- [ ] **Step 1: Write the failing check**

Create `p1/workbench_template_check.py` using `checks.harness.Checks`. Assertions, with these exact values:

```python
spec = compile_team_spec(build_workbench_config(_fake_credentials(), team_name="tpl_probe", run_id="run-1"))
checks.add("leader disables sys_operation", spec.agents["leader"].enable_sys_operation is False)
checks.add("teammate disables sys_operation", spec.agents["teammate"].enable_sys_operation is False)
checks.add("teammate mounts the submit tool",
           any(getattr(t, "type", None) == "app.submit_task_result" for t in spec.agents["teammate"].tools or []))
checks.add("leader mounts the bootstrap rail",
           any(getattr(r, "type", None) == "app.run_bootstrap" for r in spec.agents["leader"].rails or []))
checks.add("capability gates stay closed",
           spec.enable_hitt is False and spec.enable_bridge is False and spec.enable_swarmflow is False)
```

`_fake_credentials()` returns `{"MODEL_BASE_URL": "http://127.0.0.1:9", "MODEL_API_KEY": "x", "MODEL_NAME": "x"}`.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=... .venv/Scripts/python.exe p1/workbench_template_check.py`
Expected: `ModuleNotFoundError: app.agent_template` (or an import error).

- [ ] **Step 3: Implement `build_workbench_config`**

Start from `p0b/compile_spec_check.build_config()` (import it), then override:
- `modes.team.<TEAM>.team_name = team_name`
- `agents.agent_leader` / `agents.agent_teammate` as specified in Interfaces.
- Set the model entry from `credentials` exactly as `p1/live_round_check._team_config` does.
Keep the original function unmodified; this is an additive module.

- [ ] **Step 4: Run the check to verify it passes**

Run: `PYTHONPATH=... .venv/Scripts/python.exe p1/workbench_template_check.py`
Expected: `5/5 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/agent_template.py p1/workbench_template_check.py p1/workbench_template_results.json
git commit -m "feat(workbench): harden the agent template and mount app tools"
```

---

### Task 2: Hook registry, run bridge, and the submit tool

**Files:**
- Create: `app/hooks.py`
- Create: `app/run_bridge.py`
- Create: `app/submit_tool.py`
- Test: `p1/submit_tool_check.py`

**Interfaces:**
- `app/hooks.py` produces:
  - `register_bridge(run_id: str, bridge: RunBridge) -> None`
  - `get_bridge(run_id: str) -> RunBridge` (raises `KeyError` when absent)
  - `unregister_bridge(run_id: str) -> None`
  - `register_providers() -> None` (idempotent; registers `"app.submit_task_result"` and `"app.run_bootstrap"` with `openjiuwen.harness.schema.deep_agent_spec.register_tool_provider` / `register_rail_provider`).
- `app/run_bridge.py` produces:

```python
@dataclass(frozen=True)
class SubmitOutcome:
    ok: bool
    error: str = ""

class RunBridge:
    def __init__(self, *, run_id: str, plan: Sequence[PlanTask],
                 evidence_by_task: Mapping[str, Iterable[str]] | None = None) -> None: ...
    def bind_native_complete(self, complete: Callable[[str], Awaitable[None]]) -> None: ...
    def execution_for(self, task_id: str) -> str: ...
    def set_evidence(self, task_id: str, evidence_ids: Iterable[str]) -> None: ...
    async def submit(self, *, producer: str, task_id: str, execution_id: str,
                     outcome: TaskOutcome, claims: Sequence[Claim],
                     artifact_refs: Sequence[str] = (),
                     acceptance: Mapping[str, AcceptanceCheck] | None = None) -> SubmitOutcome: ...
    def results(self) -> dict[str, TaskResult]: ...
    def snapshot(self) -> dict[str, Any]: ...
```

Rules inside `submit`:
1. `task_id` must be in the plan; `producer` must equal `plan_task.assignee` → else `ok=False, error="not the assignee of <task>"`.
2. A result for `(task_id, execution_id)` may be stored once → second call `ok=False`.
3. `CitationValidator().validate(claims, available_evidence_ids=self._evidence.get(task_id, set()))` must be valid → else `ok=False` with the distinct issue codes.
4. `CompletionGate(self._ledger).evaluate(run_id=..., task_id=..., execution_id=..., result=result, reviewers=plan_task.reviewers, require_reviewer=False, required_acceptance_ids=plan_task.acceptance_ids, acceptance=acceptance)` must be accepted → else `ok=False` with the block reason.
5. On success store the result, then `await self._complete(task_id)` if bound; native completion failure turns `ok=False` but keeps the result.

- `app/submit_tool.py` produces `SubmitTaskResultTool(TeamTool)` constructed as `(bridge: RunBridge, *, producer: str)`; its card name is `submit_task_result`; input schema: `outcome` (`success|partial|error`), `claims` (array of `{text, kind, evidence_ids}`), `artifact_refs` (array of strings), `acceptance` (array of `{acceptance_id, result, verifier, evidence_refs}`). `invoke` calls `bridge.submit` and returns `ToolOutput(success=outcome.ok, error=outcome.error or None)`.

  **Artifact rule:** the MVP's members have no file tools, so the submitted claims are the deliverable. When `artifact_refs` is empty and the result has at least one non-blank claim, `submit` uses `(f"claims:{task_id}:{execution_id}",)`. When both claims and artifact refs are empty it rejects with `ok=False, error="empty result: no claims and no artifacts"` — the completion gate must never be satisfiable by an empty shell.

- `app/submit_tool.py` also produces `make_submit_tool(params: dict, context: BuildContext) -> SubmitTaskResultTool`, which resolves `bridge = get_bridge(params["run_id"])` and binds `producer=context.member_name` (raise `RuntimeError` if `context.member_name` is None). `app/hooks.register_providers()` registers it as `"app.submit_task_result"`.

- `app/run_rail.py` produces `make_bootstrap_rail(params: dict, context: BuildContext) -> RunBootstrapRail`, registered as `"app.run_bootstrap"`, resolving the bridge by `params["run_id"]`.

- [ ] **Step 1: Write the failing check** with four cases:
  1. Assigned producer with a valid `fact` claim citing `ev-1` and **no** artifact refs → `ok=True`; the stored result's `artifact_refs == ("claims:research:<execution_id>",)`.
  2. A different producer (`team_leader`) submitting the research task → `ok=False`, error mentions `not the assignee`.
  3. A `fact` citing `ev-9` when only `ev-1` was received → `ok=False`, error contains `unknown_evidence`.
  4. Submitting the same `(task_id, execution_id)` twice → second `ok=False`.
  5. An empty submission (no claims, no artifacts) → `ok=False`, error contains `empty result`.

  Plan used: `[PlanTask(task_id="research", role="teammate", instruction="x", acceptance_ids=("a1",), assignee="research_member")]`; evidence for `research` = `{"ev-1"}`.

- [ ] **Step 2: Run it to verify it fails**

Expected: import error for `app.run_bridge`.

- [ ] **Step 3: Implement the three modules** as specified. `register_providers` must import upstream lazily, so importing `app.hooks` without upstream on `PYTHONPATH` still works.

- [ ] **Step 4: Run the check to verify it passes**

Expected: `5/5 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add app/hooks.py app/run_bridge.py app/submit_tool.py p1/submit_tool_check.py p1/submit_tool_results.json
git commit -m "feat(workbench): add the run bridge and structured result tool"
```

---

### Task 3: Plan assignee is validated

**Files:**
- Modify: `app/plan.py` (add `assignee`, two issue codes, member validation)
- Test: `p1/plan_assignee_check.py`

**Interfaces:**
- `PlanTask` gains `assignee: str = ""`.
- `PlanValidator.__init__(*, roles: Iterable[str] = DEFAULT_ROLES, members: Iterable[str] | None = None)`.
- New codes: `PlanIssueCode.MISSING_ASSIGNEE = "missing_assignee"`, `PlanIssueCode.UNKNOWN_ASSIGNEE = "unknown_assignee"`.
- When `members is None`, assignee checks are skipped (existing scripts keep passing). When provided, every task must have a non-blank assignee contained in `members`.

- [ ] **Step 1: Write the failing check**

Cases: blank assignee → `missing_assignee`; `assignee="ghost"` with `members={"research_member","engineering_member"}` → `unknown_assignee`; valid plan → `valid=True`; `members=None` with blank assignee → `valid=True` (legacy).

- [ ] **Step 2: Run it to verify it fails** (attribute error / no such code).

- [ ] **Step 3: Implement** the field and the two checks in the existing per-task loop; do not reorder existing checks.

- [ ] **Step 4: Run both new and existing plan checks**

Run: `.venv/Scripts/python.exe p1/plan_assignee_check.py`
Run: `.venv/Scripts/python.exe p0b/plan_validator_check.py`
Expected: new check passes 4/4; the P0-B check still passes 20/20.

- [ ] **Step 5: Commit**

```bash
git add app/plan.py p1/plan_assignee_check.py p1/plan_assignee_results.json
git commit -m "feat(plan): carry and validate the task assignee"
```

---

### Task 4: Plan → native task graph

**Files:**
- Create: `app/task_bridge.py`
- Test: `p1/task_bridge_check.py`

**Interfaces:**
- `plan_to_graph(plan: Sequence[PlanTask]) -> list[TaskGraphSpec]` where `TaskGraphSpec` is `openjiuwen.agent_teams.schema.task.TaskGraphSpec`.
- Mapping: `task_id=task.task_id`, `title=task.task_id`, `content=task.instruction`, `depends_on=task.depends_on`, `assignee=task.assignee`, `reviewer=tuple({"reviewer_id": r, "type": "verifier"} for r in task.reviewers)`.
- `async def submit_graph(backend, plan: Sequence[PlanTask]) -> TaskGraphResult` calls `backend.task_manager.add_graph(plan_to_graph(plan))` and returns the result unchanged.

- [ ] **Step 1: Write the failing check**

Assert for a two-task plan: same length and order; the second task's `depends_on == ("research",)`; assignees preserved; a reviewer string becomes `{"reviewer_id": "team_leader", "type": "verifier"}`; `submit_graph` calls a fake backend exactly once with the converted list and propagates `ok=False` reasons.

- [ ] **Step 2: Run it to verify it fails.**

- [ ] **Step 3: Implement** the two functions. No native calls in `plan_to_graph`.

- [ ] **Step 4: Run the check to verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add app/task_bridge.py p1/task_bridge_check.py p1/task_bridge_results.json
git commit -m "feat(workbench): convert a validated plan into a native task graph"
```

---

### Task 5: Bootstrap rail and request-tool probe

**Files:**
- Create: `app/run_rail.py`
- Create: `app/request_probe.py`
- Test: `p1/run_rail_check.py`

**Interfaces:**
- `app/run_rail.py` produces `RunBootstrapRail(DeepAgentRail)` constructed as `(bridge: RunBridge, *, leader_name: str)`. Its `before_task_iteration(ctx)`:
  1. returns immediately when `ctx.agent` has no rail named `TeamToolRail`;
  2. finds that rail and takes `rail._team_backend`;
  3. `bridge.bind_native_complete(lambda task_id: backend.task_manager.complete(task_id=task_id))`;
  4. submits the graph once (module-level `set[tuple[str, int]]` keyed by `(run_id, id(bridge))` is **not** acceptable — store the flag on the bridge: `bridge.mark_bootstrapped() -> bool` returning False if already done);
  5. raises `RuntimeError` when `submit_graph` returns `ok=False`, so a failed graph lands in the run error rather than a silent empty board.
- `app/request_probe.py` produces `install_request_probe(record: Callable[[list[str]], None]) -> None` which wraps `openjiuwen.core.single_agent.agents.react_agent.log_llm_request` and records the tool names from its `tools` argument (name from `tool.name`, `tool["function"]["name"]`, or `tool.card.name`).
- `RunBridge` gains `mark_bootstrapped() -> bool` and `record_tool_names(names: Sequence[str]) -> None`; `snapshot()` includes `"tool_names"` and `"bootstrapped"`.

- [ ] **Step 1: Write the failing check** with fakes:
  - A fake agent whose `find_rail_by_name("TeamToolRail")` returns a fake rail with `_team_backend.task_manager` recording `add_graph` calls.
  - First `before_task_iteration` submits once; second call submits zero more.
  - `submit_graph` failure raises `RuntimeError`.
  - `install_request_probe`: call the module-level `log_llm_request(log, [], [fake_tool])` after installation and assert the recorder saw `["submit_task_result"]`; restore the original function afterwards.

- [ ] **Step 2: Run it to verify it fails.**

- [ ] **Step 3: Implement** both modules and the two bridge methods.

- [ ] **Step 4: Run the check to verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add app/run_rail.py app/request_probe.py app/run_bridge.py p1/run_rail_check.py p1/run_rail_results.json
git commit -m "feat(workbench): submit the graph from a leader rail and capture the request tool list"
```

---

### Task 6: Supervisor subprocess (fixed plan, no API yet)

**Files:**
- Create: `app/supervisor.py`
- Test: `p1/supervisor_check.py`

**Interfaces:**
- `async def run_payload(payload: dict, *, runner_factory: Callable[[], Any] | None = None) -> dict`
  - `payload` keys: `run_id`, `goal`, `plan` (list of dicts matching `PlanTask`), `members` (list[str]), `credentials` (dict with the three keys), `evidence_by_task` (object: task_id → list of evidence ids), `session_id`, `results_path`, `snapshot_path`.
  - `runner_factory` defaults to `openjiuwen.core.runner.runner.Runner`; the check injects a fake exposing `start()`, `run_agent_team_streaming(agent_team, inputs, session)`, `stop()`.
  - Returns the final snapshot dict and writes it atomically to `snapshot_path`.
- `def main(argv: list[str]) -> int` reads `--payload <path>` and runs `asyncio.run(run_payload(json.load(...)))`.
- Flow: validate plan with `PlanValidator(members=members)` → build config via `build_workbench_config` → `compile_team_spec` → `register_providers()` → `register_bridge` → `install_request_probe(bridge.record_tool_names)` → start the 0.5 s snapshot reporter → `await runner.start()` → stream until `team.completed`, idle grace (12 s), or a 300 s deadline → stop → write final snapshot.
- The snapshot contains at least: `run_id`, `phase`, `stream_stop_reason`, `team_markers`, `tool_names`, `results` (task_id → result dict), `bootstrapped`, `error`.

- [ ] **Step 1: Write the failing check** with the fake runner yielding three chunks: a `llm_output`, a `team.completed` marker, and end. Assert: the snapshot is written; `phase == "returned"`; `team_markers == ["team.completed"]`; `bootstrapped is False` (the fake runner never builds a team, so no rail fires — the real nail is what proves bootstrapping); `tool_names == []` for the same reason; the config passed to `compile_team_spec` has the submit tool on both roles and the bootstrap rail on the leader. Also assert a plan with an unknown assignee returns `phase == "invalid_plan"` without starting the runner.

- [ ] **Step 2: Run it to verify it fails.**

- [ ] **Step 3: Implement** the supervisor. Atomic writes use a temp file + `os.replace`. The reporter thread stops in `finally`.

- [ ] **Step 4: Run the check to verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add app/supervisor.py p1/supervisor_check.py p1/supervisor_results.json
git commit -m "feat(workbench): add the run supervisor with in-stream snapshots"
```

---

### Task 7: The real delegation nail

**Files:**
- Create: `p1/delegation_nail.py`
- Results: `p1/delegation_nail_results.json`

**Interfaces:**
- Consumes `run_payload` from Task 6; builds a fixed plan:
  - `research` (assignee `research_member`, role `teammate`) — instruction: “用给定证据回答目标。可用证据 id：ev-1、ev-2。提交至少一条 fact claim，evidence_ids 必须取自这两个 id。”;
  - `engineering` (assignee `engineering_member`, role `teammate`, `depends_on=("research",)`) — instruction: compose a short Chinese answer from the research result; the available evidence ids are the same `ev-1`/`ev-2`.
- Uses `.env.local` via `p1.live_round_check._load_credentials`; refuses to run without credentials.
- The corpus/evidence in this nail is stubbed: `evidence_by_task={"research": {"ev-1","ev-2"}, "engineering": {"ev-1","ev-2"}}`. Retrieval is a later plan; the nail proves delegation and structured submission, not RAG. The evidence ids are put **in the task instructions** because the member has no retrieval tool yet and cannot otherwise know them.

- [ ] **Step 1: Write the nail script** with these assertions:
  1. both tasks appear in `results`;
  2. the research result's `producer == "research_member"` and the engineering result's `producer == "engineering_member"`;
  3. at least one `fact` claim exists and its evidence id is in the task's seeded evidence;
  4. `snapshot["team_markers"]` contains `team.completed`;
  5. `snapshot["tool_names"]` contains no name starting with `spawn_`, no `shell`, `bash`, `cmd`, `powershell`, `read_file`, `write_file`, `edit_file`, or `list_dir`;
  6. `snapshot["tool_names"]` contains `submit_task_result`;
  7. no result was rejected with `not the assignee`.

- [ ] **Step 2: Run it once** (costs one or two model calls):

```bash
PYTHONPATH=... .venv/Scripts/python.exe p1/delegation_nail.py
```

Expected: all assertions pass. If assertion 4 fails because the members never submit, inspect `snapshot["tool_names"]` and the child stdout before changing anything; the plan's fallback is recorded in the spec §7 (drive two single-task executions directly).

- [ ] **Step 3: Record the result** in `p1/delegation_nail_results.json` and update `PROJECT.md` §13 with the measured facts (who executed, tool names, whether `submit_task_result` was called, completion path).

- [ ] **Step 4: Commit**

```bash
git add p1/delegation_nail.py p1/delegation_nail_results.json PROJECT.md
git commit -m "test(workbench): prove delegated execution and structured result capture"
```

---

## Plan 2 (next, not in this document)

After the nail: TinyAgent planner, RunService/API/auth, React workbench, deployment, then the RAG plan. Do not start them until the nail's evidence exists, because the nail decides whether the bootstrap-rail path is viable.
