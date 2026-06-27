# acp-subagent-zed-protocol-upgrade - Work Plan

## TL;DR (For humans)

**What you'll get:** Subagent sessions in Zed will show a live "working" card while the subagent runs and automatically flip to "completed" when it finishes — no more frozen cards that never resolve. Background tasks will properly signal completion to the parent agent, preventing RunExecutor hangs. Subagent depth is capped at 5 levels to prevent infinite recursion.

**Why this approach:** The framework auto-emits `SpawnSessionStart` from `create_child_session()` (eliminating 4 duplicate boilerplate blocks and fixing 2 missing-emission bugs), and completion is detected via the existing `_consumer_done_events` closure pattern (no new infrastructure — ~80 lines reusing what's already there).

**What it will NOT do:** No multi-turn reprompting, no foreground-to-background promotion, no ACP proxy chains, no changes to team.py/teamrun.py, and `MAX_SUBAGENT_DEPTH` stays hardcoded (not YAML-configurable).

**Effort:** Large
**Risk:** Medium — touches 3 core files (context.py, run_executor.py, core.py) that all native agents depend on; background-task-completion refactor changes the wait mechanism for ALL native agents.
**Decisions to sanity-check:** MAX_SUBAGENT_DEPTH=5 (not 1), child_done_events replaces pending_background_tasks counter, anyio.Event everywhere.

Your next move: approve, or run a high-accuracy review. Full execution detail follows below.

---

> TL;DR (machine): Large, Medium risk — Implement 5 OpenSpec capabilities (67 tasks) for ACP subagent Zed protocol upgrade: auto-emit SpawnSessionStart, completion notification via _consumer_done_events closure, event converter fixes, recursive cancellation, background-task-completion refactor replacing pending_background_tasks with child_done_events dict.

## Scope

### Must have

- OpenSpec tasks 1.1-1.6: Framework auto-emit of `SpawnSessionStart` in `create_child_session()` with `tool_call_id`, `depth`, `MAX_SUBAGENT_DEPTH=5` enforcement, no `getattr` usage
- OpenSpec tasks 2.1-2.4 + 3 supplementary call sites: Remove 4 manual SpawnSessionStart blocks, fix 2 missing-emission bugs (resource_providers/pool.py ×2), fix 1 undocumented site (agentpool_commands/pool.py), verify team.py/teamrun.py unaffected
- OpenSpec tasks 3.1-3.5: Event converter fixes — `kind="subagent"`, real `tool_call_id`, `_meta` on `ToolCallProgress`, `build_subagent_completed()` method, `SubagentRunInfo` on `ToolCallStart`
- OpenSpec tasks 4.1-4.9: Handler completion notification via `_parent_of` dict, `_await_child_and_notify()` closure, race condition handling, error handling, cleanup
- OpenSpec tasks 5.1-5.3: Recursive cancellation via `_cancel_subagents()` walking `_parent_of` tree
- OpenSpec tasks 7.1-7.8: Background task completion refactor — replace `pending_background_tasks: int` + `background_tasks_complete: asyncio.Event` with `child_done_events: dict[str, anyio.Event]`, `complete_background_task()` helper, RunExecutor re-iteration update, `close_session()` update, `_run_turn_unlocked` finally safety net, existing test updates
- OpenSpec tasks 8.1-8.18: 18 background task completion tests
- OpenSpec tasks 9.1-9.14: 14 subagent tests
- Stale pyc cleanup (3 files with no source)
- getattr violations fixed in all touched files (context.py, workers.py, agentpool_commands/pool.py, handler.py)

### Must NOT have (guardrails, anti-slop, scope boundaries)

- No multi-turn reprompting (OpenSpec NG1)
- No foreground-to-background promotion (OpenSpec NG2)
- No ACP Proxy Chains (OpenSpec NG3)
- No Zed Parallel Agents (OpenSpec NG4)
- No ACP v2 migration (OpenSpec NG5)
- No modifying team.py or teamrun.py (OpenSpec NG6 — they use `yield SpawnSessionStart(...)` pattern, not `create_child_session()`)
- No making `MAX_SUBAGENT_DEPTH` configurable via YAML (OpenSpec D5 — hardcoded constant)
- No feature flag for `_meta` (standard ACP extension, PR #855 still draft)
- No renaming `MAX_DELEGATION_DEPTH` (different concept at `exceptions.py:59`, value=10, keep separate)
- No changing `subagent_display_mode` options (already simplified to `legacy`/`zed`)

## Verification strategy

> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (OpenSpec task structure lists implementation tasks in sections 1-5,7, then tests in sections 8-9) + framework: pytest
- Evidence: `.omo/evidence/task-<N>-acp-subagent-zed-protocol-upgrade.<ext>`
- Agent-executed QA per todo: happy path + failure path, exact tool + invocation, evidence file per todo
- Snapshot tests: Update `tests/acp/__snapshots__/test_event_converter_snapshots.ambr` after event_converter changes (T2)

## Execution strategy

### Parallel execution waves

| Wave | Todos | Duration estimate | Gate |
|------|-------|-------------------|------|
| 1: Foundation | T1, T2, T3 | Parallel, no deps | T1 passes → T4,T5 unblocked; T2 passes → T6,T7 unblocked |
| 2: Core implementation | T4, T5 | Depends on T1 | T4 passes → T6,T7,T8 unblocked |
| 3: Integration | T6, T7, T8, T9 | Depends on T2+T4 | T7 passes → T10 unblocked; T8+T9 pass → T11 unblocked |
| 4: Completion | T10, T11 | Depends on T7+T8+T9 | T10 passes → F-wave unblocked |
| 5: Guardrails | T12 | Depends on all | T12 passes → F-wave unblocked |
| 6: Final verification | F1-F4 | Parallel after all | All APPROVE → done |

### Dependency matrix

| Todo | Depends on | Blocks | Can parallelize with |
|------|-----------|--------|---------------------|
| T1: AgentRunContext field refactor | — | T4, T8, T9 | T2, T3 |
| T2: Event converter fixes | — | T6, T7 | T1, T3 |
| T3: Stale pyc cleanup | — | — | T1, T2 |
| T4: create_child_session auto-emit + complete_background_task | T1 | T6, T7, T8 | T5 |
| T5: SubagentRunInfo on ToolCallStart | T2 | — | T4 |
| T6: Call site cleanup (6 files) | T4 | T12 | T7, T8, T9 |
| T7: Handler completion notification | T2, T4 | T10 | T6, T8, T9 |
| T8: RunExecutor re-iteration | T1 | T11 | T6, T7, T9 |
| T9: close_session + _run_turn_unlocked finally | T1 | T11 | T6, T7, T8 |
| T10: Recursive cancellation | T7 | F-wave | T11 |
| T11: Update existing tests | T8, T9 | F-wave | T10 |
| T12: Legacy guardrail + team tests | T6, T7 | F-wave | T10, T11 |

## Todos

> Implementation + Test = ONE todo. Never separate.

- [x] 1. AgentRunContext field refactor — replace pending_background_tasks with child_done_events dict
  What to do:
  - In `src/agentpool/agents/context.py`:
    - Remove `_create_set_event()` function (lines 56-60) — no longer needed
    - Remove `pending_background_tasks: int = 0` field (lines 124-143, including docstring with bg_task pattern example)
    - Remove `background_tasks_complete: asyncio.Event` field (lines 145-146)
    - Add `child_done_events: dict[str, anyio.Event] = field(default_factory=dict)` to `AgentRunContext` — use `anyio.Event` (NOT `asyncio.Event`) to align with `_consumer_done_events` on `ProtocolEventConsumerMixin` (OpenSpec D6)
    - Add `MAX_SUBAGENT_DEPTH: int = 5` module-level constant (OpenSpec task 1.2, design D5)
    - Add `class SubagentDepthError(Exception): """Raised when subagent nesting exceeds MAX_SUBAGENT_DEPTH."""` exception class
    - Add `import anyio` to imports (if not already present)
  Must NOT do:
    - Do NOT modify `create_child_session()` yet (that's T4)
    - Do NOT touch `MAX_DELEGATION_DEPTH` in `exceptions.py` (different concept, value=10, keep separate)
    - Do NOT make `MAX_SUBAGENT_DEPTH` configurable via YAML
    - Do NOT remove `queued_steer_messages` or `steer_callback` fields (still needed)
  Parallelization: Wave 1 | Blocked by: — | Blocks: T4, T8, T9 | Parallelize with: T2, T3
  References (executor has NO interview context):
    - `src/agentpool/agents/context.py:56-60` — `_create_set_event()` to remove
    - `src/agentpool/agents/context.py:63-153` — `AgentRunContext` dataclass (fields to modify)
    - `src/agentpool/agents/context.py:124-146` — `pending_background_tasks` + `background_tasks_complete` fields + docstring
    - `src/agentpool/agents/context.py:91` — existing `depth: int = 0` field (keep, used by T4)
    - `src/agentpool/agents/context.py:94` — existing `event_bus: EventBus | None = None` field (keep)
    - OpenSpec: `openspec/changes/acp-subagent-zed-protocol-upgrade/tasks.md` tasks 1.2, 7.1, 7.2
    - OpenSpec: `openspec/changes/acp-subagent-zed-protocol-upgrade/design.md` D5 (MAX_SUBAGENT_DEPTH=5), D6 (child_done_events dict, anyio.Event)
    - OpenSpec: `openspec/changes/acp-subagent-zed-protocol-upgrade/specs/background-task-completion/spec.md` requirement 1 (child_done_events replaces pending_background_tasks)
  Acceptance criteria (agent-executable):
    - `uv run python -c "from agentpool.agents.context import AgentRunContext, MAX_SUBAGENT_DEPTH, SubagentDepthError; assert MAX_SUBAGENT_DEPTH == 5; assert isinstance(AgentRunContext().child_done_events, dict)"` exits 0
    - `uv run python -c "from agentpool.agents.context import AgentRunContext; r = AgentRunContext(); assert not hasattr(r, 'pending_background_tasks'); assert not hasattr(r, 'background_tasks_complete')"` exits 0
    - `uv run python -c "import anyio; from agentpool.agents.context import AgentRunContext; r = AgentRunContext(); e = r.child_done_events; e['test'] = anyio.Event(); assert isinstance(e['test'], anyio.Event)"` exits 0
  QA scenarios (name the exact tool + invocation):
    - Happy: `uv run pytest tests/orchestrator/test_background_task_wakeup.py -x --no-cov -k "not pending"` — existing tests that don't reference pending_background_tasks still pass
    - Failure: `uv run python -c "from agentpool.agents.context import _create_set_event"` exits 1 (function removed)
    - Evidence: `.omo/evidence/task-1-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `refactor(context): replace pending_background_tasks with child_done_events dict`

- [x] 2. Event converter fixes — kind, tool_call_id, _meta, build_subagent_completed
  What to do:
  - In `src/agentpool_server/acp_server/event_converter.py`:
    - Fix line 649: `tool_call_id = str(uuid.uuid4())` → `tool_call_id = event.tool_call_id or str(uuid.uuid4())` (OpenSpec 3.2)
    - Fix line 656: `kind="other"` → `kind="subagent"` (OpenSpec 3.1) — NOTE: `infer_tool_kind("task")` returns `"other"` at `acp/utils.py:219-256`, so kind MUST be set explicitly, NOT through `infer_tool_kind`
    - Fix lines 650-651: Pass `field_meta` (with `subagent_session_info` + `tool_name`) to `ToolCallProgress` for subagent tool calls (OpenSpec 3.3) — `_build_subagent_field_meta()` already exists at line 186-212
    - Add `build_subagent_completed()` method to `ACPEventConverter` class (OpenSpec 3.4) — should yield `ToolCallProgress(tool_call_id=..., status="completed", field_meta=self._build_subagent_field_meta(...))` for zed mode
    - Update `reset()` method (line 214-219) if needed to clear any new state
  - Update snapshot tests: Run `uv run pytest tests/acp/test_event_converter_snapshots.py --update-snapshots` after changes, then review the diff
  Must NOT do:
    - Do NOT change legacy mode behavior (only zed mode `ToolCallStart` at lines 648-659)
    - Do NOT remove `_build_subagent_field_meta()` — extend it
    - Do NOT populate `SubagentRunInfo` on `ToolCallStart` yet (that's T5/P2)
    - Do NOT modify the `SubagentSessionInfo` model (lines 113-128)
  Parallelization: Wave 1 | Blocked by: — | Blocks: T6, T7 | Parallelize with: T1, T3
  References (executor has NO interview context):
    - `src/agentpool_server/acp_server/event_converter.py:649` — `tool_call_id = str(uuid.uuid4())` (GAP 1, fix to use `event.tool_call_id`)
    - `src/agentpool_server/acp_server/event_converter.py:656` — `kind="other"` (GAP 2, fix to `kind="subagent"`)
    - `src/agentpool_server/acp_server/event_converter.py:650-651` — `field_meta` with `message_start_index=0` (hardcoded, P1 will fix)
    - `src/agentpool_server/acp_server/event_converter.py:186-212` — `_build_subagent_field_meta()` existing method
    - `src/agentpool_server/acp_server/event_converter.py:113-128` — `SubagentSessionInfo` model (do NOT modify)
    - `src/agentpool_server/acp_server/event_converter.py:214-219` — `reset()` method
    - `src/acp/schema/tool_call.py:30-40` — `ToolCallKind` Literal includes `"subagent"` (verified exists)
    - `src/acp/schema/session_updates.py` — Both `ToolCallStart` AND `ToolCallProgress` have `subagent: SubagentRunInfo | None` field
    - `src/acp/utils.py:219-256` — `infer_tool_kind("task")` returns `"other"` (must set kind explicitly)
    - `tests/acp/test_event_converter_snapshots.py` (466 lines) — snapshot tests to update
    - `tests/acp/__snapshots__/test_event_converter_snapshots.ambr` — snapshot file
    - OpenSpec: tasks 3.1-3.4; `specs/session-aware-event-routing/spec.md` requirement 1
  Acceptance criteria (agent-executable):
    - `uv run pytest tests/acp/test_event_converter_snapshots.py -x --no-cov` passes with updated snapshots
    - `uv run pytest tests/acp/test_zed_subagent_spawn.py -x --no-cov` passes
    - `uv run pytest tests/acp/test_meta_guardrails.py -x --no-cov` passes (no _meta leakage in legacy mode)
  QA scenarios:
    - Happy: `uv run pytest tests/acp/test_event_converter_snapshots.py -x --no-cov -k "zed"` — zed mode snapshots show `kind="subagent"` and real `tool_call_id`
    - Failure: `uv run pytest tests/acp/test_meta_guardrails.py -x --no-cov` — legacy mode has NO `_meta` field (guardrail)
    - Evidence: `.omo/evidence/task-2-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `fix(event-converter): use subagent kind, real tool_call_id, and _meta on ToolCallProgress`

- [x] 3. Stale pyc cleanup — delete 3 orphaned .pyc files
  What to do:
    - Delete these 3 files (no corresponding .py source exists — removed in commit cb0e9dde3):
      - `tests/acp/__pycache__/test_event_converter_zed_index.cpython-313-pytest-9.0.3.pyc`
      - `tests/acp/__pycache__/test_event_converter_zed_thinking.cpython-313-pytest-9.0.3.pyc`
      - `tests/acp/__pycache__/test_zed_subagent_error.cpython-313-pytest-9.0.3.pyc`
  Must NOT do:
    - Do NOT delete any other .pyc files in __pycache__
    - Do NOT delete the __pycache__ directory itself
  Parallelization: Wave 1 | Blocked by: — | Blocks: — | Parallelize with: T1, T2
  References:
    - `tests/acp/__pycache__/` — directory containing stale files
    - Commit cb0e9dde3 removed source `test_zed_subagent_error.py` (381 lines, 13 tests)
  Acceptance criteria:
    - `ls tests/acp/__pycache__/test_event_converter_zed_index.cpython-313-pytest-9.0.3.pyc 2>/dev/null` exits 1 (file not found)
    - `ls tests/acp/__pycache__/test_event_converter_zed_thinking.cpython-313-pytest-9.0.3.pyc 2>/dev/null` exits 1
    - `ls tests/acp/__pycache__/test_zed_subagent_error.cpython-313-pytest-9.0.3.pyc 2>/dev/null` exits 1
  QA scenarios:
    - Happy: `uv run pytest tests/acp/ -x --no-cov --collect-only 2>&1 | grep -c "error"` returns 0 (no collection errors from stale pyc)
    - Evidence: `.omo/evidence/task-3-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `chore: remove 3 stale pyc files with no source`

- [x] 4. create_child_session auto-emit + complete_background_task helper
  What to do:
  - In `src/agentpool/agents/context.py`, modify `create_child_session()` (lines 266-311):
    - Add keyword params: `spawn_mechanism: str = "foreground"`, `description: str = ""`, `tool_call_id: str | None = None` (OpenSpec 1.1)
    - After child session is created (after line 307 or 311), but BEFORE return:
      - Compute `child_depth = (self.run_ctx.depth + 1) if self.run_ctx else 1` (OpenSpec 1.3)
      - Check depth: `if child_depth > MAX_SUBAGENT_DEPTH: raise SubagentDepthError(f"Subagent depth {child_depth} exceeds limit {MAX_SUBAGENT_DEPTH}")` (OpenSpec 1.3)
      - Construct `SpawnSessionStart(child_session_id=child_sid, tool_call_id=tool_call_id or self.tool_call_id, depth=child_depth, spawn_mechanism=spawn_mechanism, description=description)` (OpenSpec 1.4)
      - Emit via `await self.events.emit_event(spawn_event)` (OpenSpec 1.5) — NOT `self.node._events`
      - Register done_event: `if self.run_ctx is not None: event = anyio.Event(); self.run_ctx.child_done_events[child_sid] = event` (OpenSpec 7.3) — handle `run_ctx is None` case (skip registration, session still created)
    - Fix getattr at line 201: `getattr(self.node, "agent_pool", None)` → `self.node.agent_pool` (requires NodeContext to have `agent_pool` attribute — verify it does, or add typed property)
    - Fix getattr at line 229: `getattr(self.run_ctx, "event_bus", None)` → `self.run_ctx.event_bus if self.run_ctx else None` (OpenSpec 1.6)
  - Add `complete_background_task()` async method to `AgentRunContext`:
    ```python
    async def complete_background_task(self, child_session_id: str, message: str) -> None:
        """Signal that a background child task has completed.
        
        Calls steer_callback first (if set), then pops and sets the done_event.
        Ordering is critical: steer BEFORE signal to prevent RunExecutor
        from waking before the steer message is queued.
        """
        if self.steer_callback is not None:
            try:
                await self.steer_callback(self.session_id, message)
            except Exception:
                logger.exception("steer_callback raised in complete_background_task", child_session_id=child_session_id)
        else:
            logger.warning("complete_background_task called without steer_callback", child_session_id=child_session_id)
        event = self.child_done_events.pop(child_session_id, None)
        if event is not None:
            event.set()
    ```
    (OpenSpec 7.4 — steer_callback first, then pop+set, catch exceptions to prevent RunExecutor hang)
  Must NOT do:
    - Do NOT modify `team.py` or `teamrun.py` — they use `yield SpawnSessionStart(...)` in async generators, NOT `create_child_session()`
    - Do NOT emit SpawnSessionStart when `run_ctx is None` (standalone/test mode) — auto-emit only when run_ctx exists
    - Do NOT use `self.node._events` for emission (OpenSpec 1.5 explicitly says use `self.events.emit_event()`)
    - Do NOT use `getattr` or `hasattr` anywhere (OpenSpec 1.6, AGENTS.md prohibition)
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: T6, T7, T8 | Parallelize with: T5
  References:
    - `src/agentpool/agents/context.py:266-311` — `create_child_session()` current implementation
    - `src/agentpool/agents/context.py:292` — `self.node.agent_pool` (fix getattr at line 201)
    - `src/agentpool/agents/context.py:225-230` — `events` property (fix getattr at line 229)
    - `src/agentpool/agents/context.py:91` — `depth: int = 0` (exists, will use `self.run_ctx.depth`)
    - `src/agentpool/agents/context.py:165` — `tool_call_id: str | None = None` on AgentContext
    - `src/agentpool/agents/context.py:174` — `run_ctx: AgentRunContext | None = None`
    - `src/agentpool/agents/events/events.py:694-713` — `SpawnSessionStart` class with `tool_call_id`, `depth` fields
    - OpenSpec: tasks 1.1, 1.3-1.6, 7.3, 7.4
    - OpenSpec: `specs/subagent-auto-emit/spec.md` requirements 1-4
    - OpenSpec: `specs/background-task-completion/spec.md` requirements 2-3
  Acceptance criteria:
    - `uv run pytest tests/acp/test_zed_subagent_spawn.py -x --no-cov` passes (auto-emit produces correct SpawnSessionStart)
    - `uv run python -c "from agentpool.agents.context import AgentRunContext; import inspect; sig = inspect.signature(AgentRunContext.complete_background_task); assert 'child_session_id' in sig.parameters; assert 'message' in sig.parameters"` exits 0
    - `uv run python -c "import inspect; from agentpool.agents.context import AgentContext; sig = inspect.signature(AgentContext.create_child_session); assert 'spawn_mechanism' in sig.parameters; assert 'tool_call_id' in sig.parameters"` exits 0
  QA scenarios:
    - Happy: Create child session with `tool_call_id="test-123"`, verify SpawnSessionStart emitted with `tool_call_id="test-123"` and `depth=1`
    - Failure: Create child session at depth 5 (set `run_ctx.depth=5`), verify `SubagentDepthError` raised
    - Evidence: `.omo/evidence/task-4-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `feat(context): auto-emit SpawnSessionStart in create_child_session + add complete_background_task helper`

- [x] 5. SubagentRunInfo on ToolCallStart (P2)
  What to do:
  - In `src/agentpool_server/acp_server/event_converter.py`:
    - Populate `SubagentRunInfo(child_session_id=..., run_mode="foreground", display_name=...)` on `ToolCallStart` (OpenSpec 3.5)
    - Add `subagent=SubagentRunInfo(...)` parameter to the `ToolCallStart` yield at line 653-659
    - Fields: `child_session_id` (from event), `subagent_id` (from event if available), `run_mode: Literal["foreground", "background"]` (from spawn_mechanism), `display_name` (from source_name)
  Must NOT do:
    - Do NOT add SubagentRunInfo in legacy mode (only zed mode)
    - Do NOT modify SubagentRunInfo schema definition (`tool_call.py:47-60`)
  Parallelization: Wave 2 | Blocked by: T2 | Blocks: — | Parallelize with: T4
  References:
    - `src/agentpool_server/acp_server/event_converter.py:648-659` — zed mode `ToolCallStart` yield
    - `src/acp/schema/tool_call.py:47-60` — `SubagentRunInfo(AnnotatedObject)` schema
    - `src/acp/schema/session_updates.py` — `ToolCallStart` has `subagent: SubagentRunInfo | None` field
    - OpenSpec: task 3.5
  Acceptance criteria:
    - `uv run pytest tests/acp/test_event_converter_snapshots.py -x --no-cov -k "zed"` passes with updated snapshots showing `subagent` field populated
    - `uv run pytest tests/acp/test_meta_guardrails.py -x --no-cov` passes (legacy mode unaffected)
  QA scenarios:
    - Happy: zed mode ToolCallStart has `subagent.child_session_id` matching SpawnSessionStart
    - Failure: legacy mode ToolCallStart has `subagent=None`
    - Evidence: `.omo/evidence/task-5-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `feat(event-converter): populate SubagentRunInfo on ToolCallStart in zed mode`

- [x] 6. Call site cleanup — remove 4 manual SpawnSessionStart blocks + fix 2 missing emissions + fix getattr
  What to do:
  - **Site 1** (`src/agentpool_toolsets/builtin/subagent_tools.py:244-256`): Remove 15-line manual `SpawnSessionStart` + `emit_event` block, replace with single `await ctx.context.create_child_session(agent_name=..., agent_type=..., description=..., tool_call_id=ctx.context.tool_call_id)` call (OpenSpec 2.1)
  - **Site 2** (`src/agentpool_toolsets/builtin/workers.py:165-176`): Remove 12-line manual block, replace with `create_child_session()` call (OpenSpec 2.2)
  - **Site 3** (`src/agentpool_toolsets/builtin/workers.py:283-294`): Remove 12-line manual block, replace with `create_child_session()` call (OpenSpec 2.3)
  - **Site 4** (`src/agentpool_commands/pool.py:259-269`): Remove 11-line manual block, replace with `create_child_session()` call (NOT in OpenSpec — discovered in codebase verification)
  - **Site 5** (`src/agentpool/resource_providers/pool.py:172-175`): Add `tool_call_id` and `description` params to existing `create_child_session()` call — this site was MISSING SpawnSessionStart emission entirely (bug) — now fixed by auto-emit (NOT in OpenSpec)
  - **Site 6** (`src/agentpool/resource_providers/pool.py:219-222`): Same as Site 5 — add `tool_call_id` and `description` params (NOT in OpenSpec)
  - Fix getattr violations in touched files:
    - `workers.py:149` — `getattr(worker, "agent_type", type(worker).__name__)` → typed access
    - `workers.py:267` — same pattern
    - `agentpool_commands/pool.py:241` — `getattr(ctx.context, "run_ctx", None)` → typed access
    - `agentpool_commands/pool.py:243` — `getattr(agent_ctx, "session_id", "")` → typed access
  - Verify team.py and teamrun.py are NOT modified (OpenSpec 2.4) — they use `yield SpawnSessionStart(...)` pattern in async generators
  Must NOT do:
    - Do NOT modify `src/agentpool/delegation/team.py` or `src/agentpool/delegation/teamrun.py`
    - Do NOT remove the `create_child_session()` call itself — only remove the manual SpawnSessionStart + emit_event boilerplate that surrounds it
    - Do NOT use getattr or hasattr in any replacement code
  Parallelization: Wave 3 | Blocked by: T4 | Blocks: T12 | Parallelize with: T7, T8, T9
  References:
    - `src/agentpool_toolsets/builtin/subagent_tools.py:244-256` — Site 1 (HAS emission, OpenSpec 2.1)
    - `src/agentpool_toolsets/builtin/workers.py:149,165-176` — Site 2 (HAS emission + getattr, OpenSpec 2.2)
    - `src/agentpool_toolsets/builtin/workers.py:267,283-294` — Site 3 (HAS emission + getattr, OpenSpec 2.3)
    - `src/agentpool_commands/pool.py:241,243,259-269` — Site 4 (HAS emission + getattr, NOT in OpenSpec)
    - `src/agentpool/resource_providers/pool.py:172-175` — Site 5 (MISSING emission = bug, NOT in OpenSpec)
    - `src/agentpool/resource_providers/pool.py:219-222` — Site 6 (MISSING emission = bug, NOT in OpenSpec)
    - `src/agentpool/delegation/team.py` — DO NOT MODIFY (uses yield pattern)
    - `src/agentpool/delegation/teamrun.py` — DO NOT MODIFY (uses yield pattern)
    - OpenSpec: tasks 2.1-2.4
  Acceptance criteria:
    - `uv run pytest tests/acp/test_zed_subagent_spawn.py -x --no-cov` passes (auto-emit works at call sites)
    - `uv run ruff check src/agentpool_toolsets/builtin/workers.py src/agentpool_commands/pool.py src/agentpool/resource_providers/pool.py` passes (no getattr violations)
    - `uv run pytest tests/acp/ -x --no-cov -k "spawn"` passes
  QA scenarios:
    - Happy: All 6 call sites create child sessions that auto-emit SpawnSessionStart with correct tool_call_id
    - Failure: Sites 5+6 previously had NO SpawnSessionStart — now fixed by auto-emit; verify `test_zed_none_child.py` still passes
    - Evidence: `.omo/evidence/task-6-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `refactor(call-sites): remove manual SpawnSessionStart boilerplate, fix missing emissions, fix getattr violations`

- [x] 7. Handler completion notification — _parent_of, closure, _notify_completed, race handling, cleanup
  What to do:
  - In `src/agentpool_server/acp_server/handler.py`:
    - Add `_parent_of: dict[str, str] = {}` to `ACPProtocolHandler.__init__` (or as class attribute) (OpenSpec 4.1) — maps `child_session_id → parent_session_id`
    - Modify `_on_spawn_session_start()` (lines 99-124):
      - Fix getattr at line 119: `getattr(event, "spawn_mechanism", None)` → `event.spawn_mechanism` (SpawnSessionStart has this field at `events.py:713`)
      - After `await self.start_event_consumer(child_sid)` (line 124), grab `done_event = self._consumer_done_events.get(child_sid)` (OpenSpec 4.2)
      - Register `self._parent_of[child_sid] = session_id` (parent) BEFORE starting closure (OpenSpec 4.8)
      - If `done_event is not None`: spawn closure via `asyncio.ensure_future(self._await_child_and_notify(parent_sid=session_id, child_sid=child_sid, done_event=done_event))` and append to `self._consumer_task_refs` (OpenSpec 4.9)
      - If `done_event is None` (race — consumer already finished): call `_notify_completed(session_id, child_sid)` immediately + `self._parent_of.pop(child_sid, None)` (OpenSpec 4.4)
    - Add `_notify_completed(self, parent_sid: str, child_sid: str)` async method (OpenSpec 4.3):
      - Get parent converter: `converter = self._converters.get(parent_sid)`
      - If converter is None: log debug, return (parent consumer already stopped)
      - Call `parent_converter.build_subagent_completed(child_session_id=child_sid)` to get updates
      - Send each update via `await self.client.session_update(...)` (or equivalent ACP notification method)
    - Add `_await_child_and_notify(self, parent_sid: str, child_sid: str, done_event: anyio.Event)` async closure (OpenSpec 4.5-4.7):
      ```python
      async def _await_child_and_notify(self, parent_sid, child_sid, done_event):
          try:
              await done_event.wait()
              self._parent_of.pop(child_sid, None)
              await self._notify_completed(parent_sid, child_sid)
          except (ConnectionResetError, BrokenPipeError):
              logger.debug("Client disconnected during child completion notification", child_sid=child_sid)
          except Exception:
              logger.exception("Error in child completion notification", child_sid=child_sid)
          finally:
              # Clean up task ref
              import contextlib
              task = asyncio.current_task()
              if task is not None:
                  with contextlib.suppress(ValueError):
                      self._consumer_task_refs.remove(task)
      ```
    - Modify `_after_consumer_loop()` (lines 197-203): After popping converter, also pop `_parent_of` entry for this session (OpenSpec 4.10 — cleanup on normal child exit)
  Must NOT do:
    - Do NOT emit EventBus events for completion — use closure + `_consumer_done_events` only (OpenSpec D1)
    - Do NOT use `getattr` for `spawn_mechanism` — `SpawnSessionStart` has it as a typed field
    - Do NOT block `_on_spawn_session_start` — closure runs as background task via `asyncio.ensure_future()`
    - Do NOT modify the mixin (`mixins.py`) — use existing `_consumer_done_events` and `_consumer_task_refs` infrastructure
  Parallelization: Wave 3 | Blocked by: T2, T4 | Blocks: T10 | Parallelize with: T6, T8, T9
  References:
    - `src/agentpool_server/acp_server/handler.py:99-124` — `_on_spawn_session_start()` (modify)
    - `src/agentpool_server/acp_server/handler.py:119` — `getattr(event, "spawn_mechanism", None)` (fix)
    - `src/agentpool_server/acp_server/handler.py:124` — `await self.start_event_consumer(child_sid)` (add closure after this)
    - `src/agentpool_server/acp_server/handler.py:197-203` — `_after_consumer_loop()` (extend cleanup)
    - `src/agentpool_server/mixins.py:60` — `_consumer_done_events: dict[str, anyio.Event] = {}` (existing infrastructure)
    - `src/agentpool_server/mixins.py:61` — `_consumer_task_refs: list[asyncio.Task] = []` (existing)
    - `src/agentpool_server/mixins.py:248-250` — `done_event.set()` in finally (triggers our closure)
    - `src/agentpool_server/mixins.py:258` — `_after_consumer_loop(session_id)` in finally (our cleanup hook)
    - `src/agentpool/agents/events/events.py:713` — `SpawnSessionStart.depth` field
    - `src/agentpool/agents/events/events.py:705` — `SpawnSessionStart.tool_call_id` field
    - OpenSpec: tasks 4.1-4.9
    - OpenSpec: `specs/subagent-completion-notification/spec.md` requirements 1-3
  Acceptance criteria:
    - `uv run pytest tests/servers/acp_server/test_subagent_events.py -x --no-cov` passes
    - `uv run python -c "from agentpool_server.acp_server.handler import ACPProtocolHandler; h = ACPProtocolHandler.__new__(ACPProtocolHandler); assert hasattr(type(h), '_parent_of') or '_parent_of' in dir(h)"` exits 0
  QA scenarios:
    - Happy: Mock done_event set → closure fires → `_notify_completed` called → `build_subagent_completed()` yields `ToolCallProgress(completed=True)`
    - Failure: `done_event is None` race → immediate notification fired, no hang
    - Evidence: `.omo/evidence/task-7-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `feat(handler): add event+closure completion notification for subagent sessions`

- [x] 8. RunExecutor re-iteration update — dict-based wait, snapshot, reset
  What to do:
  - In `src/agentpool/orchestrator/run_executor.py`, modify the RE-ITERATION LOOP (lines 381-404):
    - Replace `if run_ctx.pending_background_tasks > 0:` (line 385) with `if bool(run_ctx.child_done_events):` (OpenSpec 7.5)
    - Replace `await run_ctx.background_tasks_complete.wait()` (line 386) with:
      ```python
      events = list(run_ctx.child_done_events.values())  # snapshot before await
      for ev in events:
          await ev.wait()
      if run_ctx.cancelled:
          break
      ```
    - Replace reset logic (lines 394-395):
      ```python
      run_ctx.pending_background_tasks = 0
      run_ctx.background_tasks_complete.set()
      ```
      with:
      ```python
      run_ctx.child_done_events.clear()
      ```
  Must NOT do:
    - Do NOT await on the dict directly (dict mutation during iteration) — always snapshot `list(...values())` first
    - Do NOT remove the `queued_steer_messages` check (lines 389-390) — still needed for steer re-iteration
    - Do NOT change the `cancelled` check ordering
  Parallelization: Wave 3 | Blocked by: T1 | Blocks: T11 | Parallelize with: T6, T7, T9
  References:
    - `src/agentpool/orchestrator/run_executor.py:381-404` — RE-ITERATION LOOP
    - `src/agentpool/orchestrator/run_executor.py:385` — `if run_ctx.pending_background_tasks > 0:` (replace)
    - `src/agentpool/orchestrator/run_executor.py:386` — `await run_ctx.background_tasks_complete.wait()` (replace)
    - `src/agentpool/orchestrator/run_executor.py:394-395` — reset logic (replace)
    - OpenSpec: task 7.5
    - OpenSpec: `specs/background-task-completion/spec.md` requirement 4 (RunExecutor re-iteration)
  Acceptance criteria:
    - `uv run pytest tests/orchestrator/test_background_task_wakeup.py -x --no-cov` passes (after T11 updates assertions)
    - `uv run python -c "from agentpool.orchestrator.run_executor import RunExecutor; import inspect; src = inspect.getsource(RunExecutor.execute); assert 'child_done_events' in src; assert 'pending_background_tasks' not in src; assert 'background_tasks_complete' not in src"` exits 0
  QA scenarios:
    - Happy: Background task spawns → `child_done_events` non-empty → RunExecutor waits → task completes → `complete_background_task` sets event → RunExecutor wakes → clears dict → re-iterates with steer message
    - Failure: Multiple concurrent children → all must complete (all events set) before RunExecutor wakes
    - Evidence: `.omo/evidence/task-8-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `refactor(run-executor): use child_done_events dict for background task wait`

- [x] 9. close_session + _run_turn_unlocked finally safety net
  What to do:
  - In `src/agentpool/orchestrator/core.py`, modify `close_session()` (lines 3145-3174):
    - Replace `run_handle.run_ctx.background_tasks_complete.set()` (line 3159) with:
      ```python
      events = list(run_handle.run_ctx.child_done_events.values())
      for ev in events:
          ev.set()
      run_handle.run_ctx.child_done_events.clear()
      ```
      (OpenSpec 7.6 — snapshot values, set all, clear dict, no dict mutation race)
    - Verify `SessionController.close_session()` needs no changes (it doesn't access `background_tasks_complete` — confirmed in OpenSpec 7.6)
  - In `src/agentpool/orchestrator/core.py`, modify `_run_turn_unlocked()` finally block (lines 2070-2114):
    - After line 2075 (`run_ctx.completed = True`), add safety net for child sessions:
      ```python
      # Safety net: if this is a child session and the tool didn't call
      # complete_background_task(), set the parent's done_event anyway.
      if _session is not None and _session.parent_session_id is not None:
          parent_session = self.sessions.get_session(_session.parent_session_id)
          if parent_session is not None and parent_session.current_run_id is not None:
              parent_run_handle = self.sessions._runs.get(parent_session.current_run_id)
              if parent_run_handle is not None and parent_run_handle.run_ctx is not None:
                  event = parent_run_handle.run_ctx.child_done_events.pop(_session.session_id, None)
                  if event is not None:
                      event.set()
      ```
      (OpenSpec 7.7 — any None in lookup chain → no-op, no exception)
  Must NOT do:
    - Do NOT wrap the safety net in try/except — None checks are sufficient (each lookup returns None if not found)
    - Do NOT set `done_event` if `complete_background_task` already popped it (`.pop(key, None)` returns None → skip set)
    - Do NOT add the safety net for top-level sessions (`parent_session_id is None` → skip)
  Parallelization: Wave 3 | Blocked by: T1 | Blocks: T11 | Parallelize with: T6, T7, T8
  References:
    - `src/agentpool/orchestrator/core.py:3145-3174` — `close_session()` method
    - `src/agentpool/orchestrator/core.py:3157-3159` — `run_handle.run_ctx.cancelled = True; background_tasks_complete.set()` (replace line 3159)
    - `src/agentpool/orchestrator/core.py:2070-2114` — `_run_turn_unlocked()` finally block
    - `src/agentpool/orchestrator/core.py:2075` — `run_ctx.completed = True` (add safety net after this)
    - `src/agentpool/sessions/models.py` — Session model with `parent_session_id` and `current_run_id` fields (verify field names)
    - `src/agentpool/orchestrator/run.py` — `RunHandle` with `run_ctx` field
    - OpenSpec: tasks 7.6, 7.7
    - OpenSpec: `specs/background-task-completion/spec.md` requirements 5-6
  Acceptance criteria:
    - `uv run pytest tests/orchestrator/test_session_lifecycle.py -x --no-cov` passes (after T11 updates assertions)
    - `uv run python -c "from agentpool.orchestrator.core import SessionController; import inspect; src = inspect.getsource(SessionController.close_session); assert 'child_done_events' in src; assert 'background_tasks_complete' not in src"` exits 0
  QA scenarios:
    - Happy: `close_session()` called while background tasks pending → all `child_done_events` set → RunExecutor unblocks
    - Failure: `_run_turn_unlocked` finally fires for child session where `complete_background_task` already ran → `.pop` returns None → no-op (no crash)
    - Evidence: `.omo/evidence/task-9-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `refactor(core): close_session + _run_turn_unlocked finally use child_done_events`

- [x] 10. Recursive cancellation — _cancel_subagents walking _parent_of tree
  What to do:
  - In `src/agentpool_server/acp_server/handler.py`:
    - Add `_cancel_subagents(self, parent_sid: str)` method (OpenSpec 5.1):
      ```python
      async def _cancel_subagents(self, parent_sid: str) -> None:
          """Recursively cancel all child sessions of parent_sid."""
          children = [child for child, parent in self._parent_of.items() if parent == parent_sid]
          for child_sid in children:
              self._parent_of.pop(child_sid, None)  # Pop before recursing (OpenSpec 5.2)
              await self._cancel_subagents(child_sid)  # Recurse into grandchildren
              await self.stop_event_consumer(child_sid)  # Stop consumer (cascades cancellation)
      ```
    - Wire `_cancel_subagents` into `stop_event_consumer()` or session close flow (OpenSpec 5.3):
      - In `stop_event_consumer(session_id)` or wherever a session is being stopped: call `await self._cancel_subagents(session_id)` BEFORE stopping the consumer itself
  Must NOT do:
    - Do NOT modify the mixin's `stop_event_consumer()` — call it from handler's override
    - Do NOT infinite-loop on circular `_parent_of` entries — pop before recursing prevents this
  Parallelization: Wave 4 | Blocked by: T7 | Blocks: F-wave | Parallelize with: T11
  References:
    - `src/agentpool_server/acp_server/handler.py` — where to add `_cancel_subagents`
    - `src/agentpool_server/mixins.py` — `stop_event_consumer()` method (call from handler)
    - OpenSpec: tasks 5.1-5.3
    - OpenSpec: `specs/child-session-policy/spec.md` requirements 1-2
  Acceptance criteria:
    - `uv run pytest tests/servers/acp_server/test_subagent_events.py -x --no-cov -k "cancel"` passes
  QA scenarios:
    - Happy: Parent stopped → children and grandchildren consumers all stopped → `_parent_of` entries cleaned up
    - Failure: Child already stopped → `_cancel_subagents` handles gracefully (no crash on missing entries)
    - Evidence: `.omo/evidence/task-10-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `feat(handler): recursive cancellation via _cancel_subagents walking _parent_of tree`

- [x] 11. Update existing tests — replace pending_background_tasks assertions with child_done_events
  What to do:
  - In `tests/orchestrator/test_background_task_wakeup.py`:
    - Replace all `run_ctx.pending_background_tasks += 1` (lines 152, 250, 343) with `run_ctx.child_done_events[child_sid] = anyio.Event()`
    - Replace all `run_ctx.background_tasks_complete.clear()` (lines 153, 251, 344) with (no-op — event starts unset)
    - Replace all `run_ctx.pending_background_tasks -= 1` + `if ... == 0: background_tasks_complete.set()` (lines 161-163, 259-261, 352-354) with `event = run_ctx.child_done_events.pop(child_sid, None); if event: event.set()`
    - Replace `run_ctx.background_tasks_complete.set()` at line 269 with dict clear + set all
  - In `tests/orchestrator/test_session_lifecycle.py`:
    - Replace `background_tasks_complete.clear()` at line 808 with `run_ctx.child_done_events["test"] = anyio.Event()`
    - Replace `background_tasks_complete.wait()` at line 811 with `await run_ctx.child_done_events["test"].wait()`
    - Update test docstrings at lines 793, 867
  - In `tests/orchestrator/test_steer_followup.py`:
    - Search for `pending_background_tasks` and `background_tasks_complete` references and replace with `child_done_events` equivalents
  - Add new tests for OpenSpec tasks 8.1-8.18 (background task completion tests):
    - 8.1: Test `create_child_session` registers `done_event` on parent `run_ctx.child_done_events`
    - 8.2: Test `complete_background_task()` calls `steer_callback` before setting `done_event` (ordering)
    - 8.3: Test `complete_background_task()` with unknown child_session_id still calls `steer_callback`
    - 8.4: Test `complete_background_task()` when `steer_callback` is None — skips steer, sets event, logs warning
    - 8.5: Test `complete_background_task()` when `steer_callback` raises — catches, logs, sets event
    - 8.6: Test `complete_background_task()` called twice — second finds key missing, still calls steer
    - 8.7: Test RunExecutor waits on `child_done_events` when non-empty
    - 8.8: Test RunExecutor skips wait when `child_done_events` is empty
    - 8.9: Test RunExecutor reset uses `child_done_events.clear()`
    - 8.10: Test `close_session()` snapshots, sets all, clears dict
    - 8.11: Test `_run_turn_unlocked` finally sets parent `done_event` via `.pop(key, None)`
    - 8.12: Test finally is no-op when `complete_background_task` already popped key
    - 8.13: Test finally is no-op when parent run already completed (`current_run_id` is None)
    - 8.14: Test finally is no-op when parent session/RunHandle/run_ctx not found
    - 8.15: Test finally is no-op when `parent_session_id` is None (top-level)
    - 8.16: Test synchronous child — `done_event` set before RunExecutor reaches re-iteration
    - 8.17: Test safety net fires without steer when tool didn't call `complete_background_task()`
    - 8.18: Test multiple concurrent children — all must complete before RunExecutor wakes
  Must NOT do:
    - Do NOT remove existing test scenarios — only update the mechanism they test
    - Do NOT use `asyncio.Event` — use `anyio.Event` everywhere to match production code
  Parallelization: Wave 4 | Blocked by: T8, T9 | Blocks: F-wave | Parallelize with: T10
  References:
    - `tests/orchestrator/test_background_task_wakeup.py` — 22 references to `pending_background_tasks`/`background_tasks_complete`
    - `tests/orchestrator/test_session_lifecycle.py:793-867` — `background_tasks_complete` references
    - `tests/orchestrator/test_steer_followup.py` — search for references
    - OpenSpec: tasks 7.8, 8.1-8.18
  Acceptance criteria:
    - `uv run pytest tests/orchestrator/test_background_task_wakeup.py -x --no-cov` passes
    - `uv run pytest tests/orchestrator/test_session_lifecycle.py -x --no-cov` passes
    - `uv run pytest tests/orchestrator/test_steer_followup.py -x --no-cov` passes
    - `uv run pytest tests/orchestrator/ -x --no-cov -k "child_done or background_task"` passes (new tests)
  QA scenarios:
    - Happy: All updated tests pass with new `child_done_events` mechanism
    - Failure: Test 8.5 — `steer_callback` raises exception → `complete_background_task` catches it, logs error, still sets event (RunExecutor doesn't hang)
    - Evidence: `.omo/evidence/task-11-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `test: update existing tests + add 18 background task completion tests for child_done_events`

- [x] 12. Subagent tests — 14 tests for auto-emit, completion, cancellation, legacy guardrails
  What to do:
  - Add new tests for OpenSpec tasks 9.1-9.14:
    - 9.1: Test `create_child_session` auto-emits `SpawnSessionStart` with correct `tool_call_id` (in `tests/acp/test_zed_subagent_spawn.py` or new file)
    - 9.2: Test `tool_call_id` flows ctx → event → converter consistently (end-to-end)
    - 9.3: Test `kind="subagent"` in zed mode `ToolCallStart` (extend `tests/acp/test_event_converter_snapshots.py`)
    - 9.4: Test `ToolCallProgress` carries `_meta.subagent_session_info` + `tool_name` (extend snapshot tests)
    - 9.5: Test Event + closure completion notification (mock `done_event`) — in `tests/servers/acp_server/test_subagent_events.py`
    - 9.6: Test `done_event is None` race — immediate notification fired
    - 9.7: Test concurrent child sessions — each gets correct `tool_call_id` completion
    - 9.8: Test closure error handling — `session_update` raises, exception logged not swallowed
    - 9.9: Test `_consumer_task_refs` cleanup after task completion
    - 9.10: Test `_parent_of` cleanup on normal child exit
    - 9.11: Test `MAX_SUBAGENT_DEPTH` enforcement — `SubagentDepthError` raised at depth 6
    - 9.12: Test recursive cancellation — parent stop cascades to children and grandchildren
    - 9.13: Test legacy mode unchanged — `subagent_display_mode != "zed"` behavior identical to before (extend `tests/acp/test_meta_guardrails.py`)
    - 9.14: Test team.py yield pattern unaffected by auto-emit changes
  - Update existing tests as needed to accommodate new auto-emit behavior (e.g., `test_zed_subagent_spawn.py` may need adjustment if it was testing manual emission)
  Must NOT do:
    - Do NOT test `message_start_index` / `message_end_index` correctness yet (P1, not in this plan's scope — hardcoded to 0)
    - Do NOT remove existing tests — extend or add alongside
  Parallelization: Wave 5 | Blocked by: T6, T7 | Blocks: F-wave | Parallelize with: T10, T11
  References:
    - `tests/acp/test_zed_subagent_spawn.py` (174 lines) — extend for auto-emit tests
    - `tests/acp/test_zed_none_child.py` (267 lines) — verify child_session_id=None still works
    - `tests/acp/test_subagent_display_mode_coerce.py` (80 lines) — verify coercion unchanged
    - `tests/acp/test_meta_guardrails.py` (141 lines) — extend for legacy guardrail
    - `tests/acp/test_event_converter_snapshots.py` (466 lines) — extend for kind + _meta
    - `tests/acp/test_event_converter_errors.py` (195 lines) — verify error handling unchanged
    - `tests/servers/acp_server/test_subagent_events.py` (379 lines) — extend for completion + cancellation
    - `tests/fixtures/subagent_events.py` (548 lines) — extend fixtures if needed
    - `tests/acp/__snapshots__/test_event_converter_snapshots.ambr` — update snapshots
    - OpenSpec: tasks 9.1-9.14
  Acceptance criteria:
    - `uv run pytest tests/acp/ -x --no-cov` passes (all ACP tests)
    - `uv run pytest tests/servers/acp_server/test_subagent_events.py -x --no-cov` passes
    - `uv run pytest tests/acp/ -x --no-cov -k "depth"` passes (MAX_SUBAGENT_DEPTH test)
    - `uv run pytest tests/acp/ -x --no-cov -k "cancel"` passes (recursive cancellation test)
    - `uv run pytest tests/acp/ -x --no-cov -k "legacy"` passes (legacy guardrail test)
  QA scenarios:
    - Happy: All 14 new tests pass; existing tests still pass
    - Failure: 9.11 — depth 6 → `SubagentDepthError` raised (not silently allowed)
    - Evidence: `.omo/evidence/task-12-acp-subagent-zed-protocol-upgrade.txt`
  Commit: Y | `test: add 14 subagent tests for auto-emit, completion, cancellation, legacy guardrails`

## Final verification wave

> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  Verify every OpenSpec task 1.1-1.6, 2.1-2.4, 3.1-3.5, 4.1-4.9, 5.1-5.3, 7.1-7.8, 8.1-8.18, 9.1-9.14 is implemented. Cross-reference `openspec/changes/acp-subagent-zed-protocol-upgrade/tasks.md` checkbox states.
  Evidence: `.omo/evidence/f1-acp-subagent-zed-protocol-upgrade.md`

- [x] F2. Code quality review
  Run `duty lint` (ruff check + format + mypy). Verify no `getattr`/`hasattr` in touched files. Verify `anyio.Event` used everywhere (not `asyncio.Event` for child_done_events).
  Evidence: `.omo/evidence/f2-acp-subagent-zed-protocol-upgrade.txt`

- [x] F3. Real manual QA
  Run full test suite: `uv run pytest --no-cov`. Run specific: `uv run pytest tests/acp/ tests/orchestrator/ tests/servers/acp_server/ -x --no-cov`. Verify no regressions.
  Evidence: `.omo/evidence/f3-acp-subagent-zed-protocol-upgrade.txt`

- [x] F4. Scope fidelity
  Verify team.py/teamrun.py NOT modified. Verify MAX_SUBAGENT_DEPTH=5 (not configurable). Verify no multi-turn reprompting, no foreground-to-background promotion. Verify _meta not feature-flagged.
  Evidence: `.omo/evidence/f4-acp-subagent-zed-protocol-upgrade.md`

## Commit strategy

- One commit per todo (12 implementation commits + test commits)
- Commit message format: `<type>(<scope>): <summary>` (conventional commits)
- Types: `feat`, `fix`, `refactor`, `test`, `chore`
- Scopes: `context`, `event-converter`, `handler`, `run-executor`, `core`, `call-sites`, `test`
- After all todos: verify with `git diff --stat` that only expected files changed
- Final verification wave does NOT commit — it produces evidence files only

## Success criteria

1. All 67 OpenSpec tasks checked `[x]` in `openspec/changes/acp-subagent-zed-protocol-upgrade/tasks.md`
2. `uv run pytest` passes with no regressions
3. `duty lint` passes (ruff + mypy)
4. No `getattr`/`hasattr` in touched files (context.py, event_converter.py, handler.py, workers.py, pool.py, agentpool_commands/pool.py)
5. `anyio.Event` used for `child_done_events` (not `asyncio.Event`)
6. team.py and teamrun.py unchanged (`git diff --stat src/agentpool/delegation/team.py src/agentpool/delegation/teamrun.py` shows no changes)
7. 3 stale pyc files deleted
8. `_parent_of` dict exists in handler.py
9. `_cancel_subagents` method exists in handler.py
10. `complete_background_task` method exists on AgentRunContext
