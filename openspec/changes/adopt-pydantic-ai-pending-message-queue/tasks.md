## Phase 1: Run Tracking Foundation (All Agent Types)

### 1. Prototype (Already Have pydantic-ai 1.102.0)

- [x] 1.1 Bump `pydantic-ai` to `>=1.101.0` in `pyproject.toml` — **ALREADY DONE** (currently at 1.102.0)
- [x] 1.2 Run `uv lock` to regenerate `uv.lock` — **ALREADY DONE**
- [ ] 1.3 Create a standalone prototype script that tests `agent.iter()` + `agent_run.next()` with `enqueue(priority='when_idle')` to verify drain behavior
- [ ] 1.4 Document the event mapping from PydanticAI node events to AgentPool EventBus events based on prototype findings

### 2. RunHandle

- [ ] 2.1 Create `src/agentpool/orchestrator/run.py` with `RunHandle` dataclass: `run_id`, `status`, `run_ctx`, `agent_type`, `session_id`, `agent_run_ref` (PydanticAI `AgentRun` for native, `LegacyTurnRunner`/`Task` for non-native), `created_at`, `completed_at`, `complete_event`
- [ ] 2.2 Define `RunStatus` enum (`pending`, `running`, `completed`, `failed`)
- [ ] 2.3 Add `RunHandle.cancel()` method that sets `run_ctx.cancelled = True` and cancels `run_ctx.current_task` (if set); if `start()` hasn't been called, mark as cancelled and schedule cleanup asynchronously via `asyncio.create_task(_cleanup_run())` — do NOT call `_cleanup_run()` synchronously to avoid deadlock with `SessionState._request_lock`
- [ ] 2.4 Add `RunHandle.start()` method that transitions status to `running` and stores `asyncio.current_task()`
- [ ] 2.5 Add `RunHandle.complete()` / `RunHandle.fail()` methods that transition status and trigger cleanup callback; cleanup callback (`_cleanup_run()`) sets `complete_event` after all cleanup and lock release

### 3. SessionPool Refactor

- [ ] 3.1 Add `SessionPool._runs: dict[str, RunHandle]` for pool-level active run tracking
- [ ] 3.2 Add `SessionPool.active_runs` property → list of running `RunHandle` objects
- [ ] 3.3 Add `SessionPool.cancel_run(run_id)` → finds run in `_runs` and calls `RunHandle.cancel()`
- [ ] 3.4 Add `SessionPool.get_run(run_id)` → returns `RunHandle | None`
- [ ] 3.5 Add async-safe access patterns for `_runs` (dict ops are atomic in CPython for single ops; use `SessionPool._runs_lock: asyncio.Lock` for multi-step operations like `max_concurrent_runs` check + insert)
- [ ] 3.6 Add test: `SessionPool.active_runs` returns all active runs across native and non-native agents
- [ ] 3.7 Add test: `SessionPool.cancel_run()` cancels a running native agent
- [ ] 3.8 Add test: `SessionPool.cancel_run()` cancels a running non-native agent
- [ ] 3.9 Add test: `SessionPool._runs` cleanup removes completed runs to prevent memory leak

### 4. SessionState Refactor

- [ ] 4.1 Keep `turn_lock: asyncio.Lock` in `SessionState` for **non-native agents**; native agents stop using it in **Phase 2** (still used during Phase 1 while native agents use existing `TurnRunner`)
- [ ] 4.2 Remove `active_run_ctx: AgentRunContext | None` from `SessionState`
- [ ] 4.3 Add `current_run_id: str | None` to `SessionState`
- [ ] 4.4 Add `_request_lock: asyncio.Lock` to `SessionState` (per-session lock for atomic check-and-create)
- [ ] 4.5 Ensure `SessionState._state_to_data()` does NOT serialize `_request_lock` or `turn_lock` (they are runtime-only fields)

### 5. SessionController Router + SessionPool Integration

- [ ] 5.1 Implement `SessionController.receive_request(session_id, content, priority='when_idle' | 'asap')` returning `None`
  - For **all agents**: acquire `SessionState._request_lock`, check `current_run_id`, create run or enqueue
  - For native agents (Phase 1): create `RunHandle` then delegate execution to existing `TurnRunner` (still using manual queue during Phase 1)
  - For non-native agents (Phase 1): create `RunHandle` then delegate to existing `TurnRunner.inject_prompt()` / `queue_prompt()` (will become `LegacyTurnRunner` in Phase 2)
  - When idle: create `RunHandle`, add to `SessionPool._runs`, set `current_run_id`, release lock, then start execution
  - When active: enqueue message via appropriate mechanism (manual queue in Phase 1, PydanticAI `enqueue()` in Phase 2 for native)
- [ ] 5.2 Implement `SessionController.cancel_run_for_session(session_id)` that finds active run via `session.current_run_id`, looks up in `SessionPool._runs`, and calls `RunHandle.cancel()`
- [ ] 5.3 Add `SessionController._create_run(session_id, initial_prompt)` that:
  1. Acquires `SessionState._request_lock`
  2. Creates `RunHandle`, adds to `SessionPool._runs`, sets `current_run_id`
  3. Releases `_request_lock`
  4. Starts turn execution via `asyncio.create_task()` inside a `try` block
  5. If execution start fails, catches exception and calls `_cleanup_run(run_id)` before re-raising — prevents dirty `current_run_id`
- [ ] 5.4 Add `SessionController._cleanup_run(run_id)` that acquires `SessionState._request_lock`, removes from `SessionPool._runs`, sets `SessionState.current_run_id = None`, releases `_request_lock`, then sets `RunHandle.complete_event`
- [ ] 5.5 Add `SessionState.closing: bool = False`; set by `close_session()`, checked by `receive_request()` — reject new requests when closing
- [ ] 5.6 Add `SessionPool.max_concurrent_runs: int | None`; enforce by acquiring `SessionPool._runs_lock` before checking count and creating new run — prevents race where concurrent requests for different sessions both pass the check
- [ ] 5.7 Add `SessionPool.cancel_run()` method that acquires `SessionState._request_lock` before calling `RunHandle.cancel()` — prevents race with `_cleanup_run()`
- [ ] 5.8 Add acceptance test: `PendingMessageDrainCapability` is outermost relative to AgentPool's own capabilities (verify no ordering conflicts with `NativeAgentHookManager.as_capability()`)

### 6. AgentRunContext Cleanup (Phase 1)

- [ ] 6.1 Make `injection_manager` field optional in `AgentRunContext`: `injection_manager: PromptInjectionManager | None = None`
- [ ] 6.2 **Phase 1**: Keep `injection_manager` for ALL agents (native and non-native) since Phase 1 still uses manual queues for all agents. Set `injection_manager=None` only for native agents in **Phase 2** when using PydanticAI queue
- [ ] 6.3 Update `NativeAgentHookManager.after_tool_execute` to handle `injection_manager is None` gracefully (for Phase 2)
- [ ] 6.4 Update `BaseAgent._get_session_run_ctx()` to find `RunHandle` via `SessionPool.get_run(session.current_run_id)` instead of reading `session.active_run_ctx`

### 7. Event Classes

- [ ] 7.1 Add `cancelled: bool = False` field to `StreamCompleteEvent` (backward-compatible change for cancellation signaling)

### 8. Metrics Update

- [ ] 8.1 Update `MetricsCollector._collect_active_turns()` to use `SessionPool.active_runs` instead of `turn_lock.locked()`
- [ ] 8.2 Add metric: `active_runs_by_agent_type` (native vs non-native breakdown from `SessionPool._runs`)
- [ ] 8.3 Verify metrics dashboard still displays active session count correctly

### 9. AgentPool Facade

- [ ] 9.1 Add `AgentPool.list_active_runs()` → delegates to `SessionPool.active_runs`; return `[]` when `session_pool` is `None`
- [ ] 9.2 Add `AgentPool.cancel_run(run_id)` → delegates to `SessionPool.cancel_run()`; raise `RuntimeError` when `session_pool` is `None`
- [ ] 9.3 Add `AgentPool.get_run(run_id)` → delegates to `SessionPool.get_run()`; return `None` when `session_pool` is `None`
- [ ] 9.4 Ensure all facade methods handle `session_pool is None` gracefully (AgentPool may operate without session pool in standalone mode)

### 10. Protocol Handler Updates (Phase 1)

- [ ] 10.1 Update `acp_server` handlers to call `SessionController.receive_request()` instead of `TurnRunner.inject_prompt()`
- [ ] 10.2 Update `opencode_server` handlers to call `SessionController.receive_request()` instead of `TurnRunner.inject_prompt()`
- [ ] 10.3 Verify `mcp_server/tool_bridge.py` `get_active_run_context()` usage still works with new architecture
- [ ] 10.4 Search for all `inject_prompt` / `queue_prompt` call sites and migrate them
- [ ] 10.5 Ensure all protocol handlers subscribe to EventBus before calling `receive_request()`

### 11. SessionPool Facade

- [ ] 11.1 Update `SessionPool.process_prompt()` to delegate to `SessionController.receive_request()` for **all agents** (both native and non-native create `RunHandle` in Phase 1; non-native execution still delegates to `TurnRunner`)
- [ ] 11.2 Redesign `SessionPool.run_stream()`: subscribe to EventBus, call `receive_request()`, await `RunHandle.complete_event` instead of `process_prompt()`
- [ ] 11.3 Update `SessionPool.close_session()` to get `RunHandle` BEFORE removing session from `_sessions`, then await `RunHandle.complete_event` with 30-second timeout; fall back to `SessionController.cancel_run()` on timeout
- [ ] 11.4 Ensure `close_session()` acquires `SessionState._request_lock` before setting `SessionState.closing = True`, then releases it before waiting; `receive_request()` checks `closing=True` while holding `_request_lock` and rejects
- [ ] 11.5 Ensure `close_session()` sets `complete_event` AFTER all cleanup in run task's `finally` block

### 12. Error Propagation

- [ ] 12.1 Add `RunFailedEvent` to EventBus with `run_id`, `session_id`, `exception` fields
- [ ] 12.2 Update `RunHandle.fail()` to publish `RunFailedEvent` before setting `complete_event`
- [ ] 12.3 Update existing `TurnRunner` to call `RunHandle.fail()` in its exception handler (will become `LegacyTurnRunner` in Phase 2)
- [ ] 12.4 Update existing `TurnRunner` to use `SessionPool.get_run(session.current_run_id)` instead of `session.active_run_ctx` for finding the active run context (Phase 1 only; `active_run_ctx` is being removed)
- [ ] 12.5 Audit all call sites that catch exceptions from `SessionPool.process_prompt()` and update to handle `RunFailedEvent` on EventBus
- [ ] 12.5 Add test: `RunFailedEvent` is published when native run crashes
- [ ] 12.6 Add test: `RunFailedEvent` is published when non-native run crashes

### 13. Phase 1 Tests

- [ ] 13.1 Add test: `SessionController.receive_request()` creates run for idle native session
- [ ] 13.2 Add test: `SessionController.receive_request()` creates run for idle non-native session
- [ ] 13.3 Add test: `SessionController.receive_request()` enqueues follow-up for active native session (still using manual queue during Phase 1)
- [ ] 13.4 Add test: `RunHandle.cancel()` interrupts active native turn
- [ ] 13.5 Add test: `RunHandle.cancel()` interrupts active non-native turn
- [ ] 13.6 Add test: Concurrent requests to same native session result in one run + one enqueue (no duplicate runs)
- [ ] 13.7 Add test: `close_session()` gracefully waits for active run completion via `RunHandle.complete_event`
- [ ] 13.8 Add test: `close_session()` forcefully cancels run after 30-second timeout
- [ ] 13.9 Add test: `close_session()` race - `complete_event` set after cleanup finishes
- [ ] 13.10 Add test: `close_session()` rejects new `receive_request()` after `closing=True` is set
- [ ] 13.11 Add test: `max_concurrent_runs` rejects new run when at capacity
- [ ] 13.12 Verify existing non-native agent tests still pass
- [ ] 13.13 Run full test suite: `uv run pytest`

## Phase 2: Native Agent PydanticAI Queue (Blocked by Phase 1 + Prototype)

### 14. RunExecutor for Native Agents

- [ ] 14.1 Create `RunExecutor` class for native agents in `orchestrator/run_executor.py`
  - Drives `agent.iter()` + `agent_run.next()` loop
  - Maps PydanticAI node events to AgentPool EventBus events (must match current `_stream_events()` behavior)
- [ ] 14.2 Ensure `RunExecutor` preserves isolated `agent_iteration_task` pattern for CancelScope safety
- [ ] 14.3 Map PydanticAI node events to AgentPool EventBus events in `RunExecutor`
- [ ] 14.4 Remove auto-resume logic from native path; PydanticAI handles follow-up drain via `PendingMessageDrainCapability`

### 15. LegacyTurnRunner Extraction

- [ ] 15.1 Extract non-native queue logic from `TurnRunner` into `LegacyTurnRunner` class in `orchestrator/legacy_runner.py`
  - Preserve `_post_turn_injections`, `_post_turn_prompts`, `_injection_locks`, `inject_prompt()`, `queue_prompt()`, `_process_queued_work()`, `_trigger_auto_resume()`
  - `LegacyTurnRunner` continues using `SessionState.turn_lock` for turn serialization
  - `LegacyTurnRunner` creates `RunHandle`, adds to `SessionPool._runs`, sets `current_run_id`
- [ ] 15.2 Ensure `LegacyTurnRunner` preserves existing behavior for ACP, ClaudeCode, AGUI agents
- [ ] 15.3 Add test: `LegacyTurnRunner.inject_prompt()` still works for non-native agents

### 16. Native Agent Queue Migration

- [ ] 16.1 Remove `TurnRunner._post_turn_prompts`, `_injection_locks`, `queue_prompt()`, `_process_queued_work()`, `_trigger_auto_resume()` for native agents only
- [ ] 16.2 Remove `AgentRunContext.injection_manager.queue()`/`pop_queued()` usage for native agents (follow-up prompts only)
- [ ] 16.3 Keep `AgentRunContext.injection_manager.inject()`/`consume()` for native agents (tool result augmentation)
- [ ] 16.4 Remove the internal prompt continuation loop from `BaseAgent._run_stream_once()` for native agents only (lines ~850-871)
- [ ] 16.5 Update `BaseAgent.inject_prompt()` / `queue_prompt()` for native agents to delegate to `SessionController.receive_request()` with appropriate priority, which routes to PydanticAI `enqueue()` for active native runs

### 17. BaseAgent & HookManager

- [ ] 17.1 Update `BaseAgent.interrupt()` to delegate to `SessionController.cancel_run()` when SessionPool is active; fall back to canceling `run_ctx.current_task` directly in standalone mode
- [ ] 17.2 Verify `NativeAgentHookManager.after_tool_execute` still injects tool result context correctly for native agents (do NOT replace with `ctx.enqueue()`)
- [ ] 17.3 Verify `NativeAgentHookManager` capabilities don't conflict with auto-injected `PendingMessageDrainCapability`

### 18. Phase 2 Tests

- [ ] 18.1 Write PydanticAI-native auto-resume tests equivalent to `test_acp_sessionpool_inject_redflag.py`
- [ ] 18.2 Add test: PydanticAI `PendingMessageDrainCapability` drains `'asap'` before next model request
- [ ] 18.3 Add test: PydanticAI `PendingMessageDrainCapability` drains `'when_idle'` at end-of-run
- [ ] 18.4 Add test: `enqueue(priority='asap')` during tool execution on native agent
- [ ] 18.5 Add test: multiple `when_idle` messages queued and all drained
- [ ] 18.6 Add test: `enqueue()` called after run completes (should error or be handled by new run)
- [ ] 18.7 Add test: Tool result augmentation via `inject_prompt()` still works for native agents after migration
- [ ] 18.8 Add test: Event stream from `RunExecutor` matches current `_stream_events()` output
- [ ] 18.9 Verify existing non-native agent tests still pass
- [ ] 18.10 Remove old `TurnRunner` queue tests for native agents only
- [ ] 18.11 Run full test suite: `uv run pytest`

## 19. Documentation

- [ ] 19.1 Update `docs/` to reference `SessionController.receive_request()` instead of `TurnRunner.inject_prompt()`
- [ ] 19.2 Document the `pydantic-ai>=1.101.0` requirement
- [ ] 19.3 Document known limitation: `enqueue()` from Temporal activities may be dropped
- [ ] 19.4 Document event mapping from PydanticAI nodes to AgentPool EventBus events
- [ ] 19.5 Document two queue systems: PydanticAI for native agents, manual for non-native agents
- [ ] 19.6 Document `PromptInjectionManager` dual purpose: `inject()`/`consume()` for tool result augmentation (all agents), `queue()`/`pop_queued()` replaced by PydanticAI `enqueue()` for native agents
