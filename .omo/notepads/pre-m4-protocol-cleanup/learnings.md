# Task 0: ACP Streaming Snapshot Baseline (V10) - Learnings

## Key Findings

### ACP Streaming Architecture (`_stream_events` method)
- Location: `src/agentpool/agents/acp_agent/acp_agent.py:412-611`
- Yields `RunStartedEvent` first, then polls ACP state updates, finally yields `StreamCompleteEvent`
- **Critical**: `_stream_events` calls `self._state.clear()` at the start, wiping any pre-populated updates
- Polling loop: `while not prompt_task.done()` → waits on `_update_event` with 0.05s timeout → pops updates via `self._state.pop_update()` → converts via `acp_to_native_event()`
- After polling loop: drain remaining updates with `while (update := self._state.pop_update()) is not None`

### ToolResultMetadataEvent Enrichment
- `ToolResultMetadataEvent` is a native event (not an ACP `SessionUpdate`)
- `acp_to_native_event()` does NOT produce `ToolResultMetadataEvent` — it only handles ACP schema types
- The enrichment code in `_stream_events` (lines 518-536) captures `ToolResultMetadataEvent` from the event stream and uses it to enrich `ToolCallCompleteEvent.metadata`
- To test this path, we patched `acp_to_native_event` to pass through `ToolResultMetadataEvent` instances

### Test Setup Patterns
- **State persistence**: Need `_PrePopulatedSessionState` subclass that re-populates updates after `clear()`
- **Event yielding**: Mock `_update_event.wait_with_timeout()` must `await asyncio.sleep(0.001)` to yield control to the event loop, allowing `prompt_task` to execute concurrently
- **Tool bridge mock**: `_MockToolBridge.set_run_context()` is a simple `@asynccontextmanager` that yields self
- **API mock**: `_MockAPI.prompt()` sleeps briefly then returns `PromptResponse(stop_reason="end_turn")`

### Snapshot Testing
- Project uses **syrupy** (>=4.0.0) for snapshot testing
- `acp_snapshot` marker is excluded by default (`addopts = ["-m", "not slow and not acp_snapshot"]`)
- Must run with `-m "acp_snapshot"` to include these tests
- Use `--snapshot-update` to generate/update baselines
- Snapshot files go in `tests/integration/__snapshots__/test_acp_streaming.ambr`

### Event Sequence Captured
1. `RunStartedEvent` (agent_name, parent_session_id)
2. `PartDeltaEvent` (text chunks via `TextPartDelta`)
3. `ToolCallStartEvent` (tool_call_id, tool_name, title, kind, raw_input)
4. `ToolCallCompleteEvent` (enriched with metadata, agent_name set by agent)
5. `StreamCompleteEvent` (content, finish_reason, role, name)

### Files Created
- `tests/integration/test_acp_streaming.py` - Test file with 2 snapshot tests
- `tests/integration/__snapshots__/test_acp_streaming.ambr` - Syrupy snapshot baseline
- `.omo/evidence/task-0-pre-m4-protocol-cleanup.log` - Test run evidence

## Task 1: ACPAgentAPI adapter with stream_events() and get_messages()

### What was done
- Added `stream_events()` and `get_messages()` to `ACPAgentAPI` (`src/acp/agent/acp_agent_api.py`)
- Added `@runtime_checkable` to `ACPClientProtocol` in `turn.py` so `isinstance()` works
- Added `_SessionStateProtocol` and `_UpdateEventProtocol` (runtime_checkable Protocols) to acp_agent_api.py for type-safe state/event injection without importing from agentpool
- Modified `ACPAgentAPI.__init__` to accept optional `state` and `update_event` kwargs
- Added `_attach_state()` method for deferred wiring (state/event created after ACPAgentAPI)
- Removed `cast("ACPClientProtocol", self._api)` in `acp_agent.py:create_turn()`
- Removed unused `ACPClientProtocol` TYPE_CHECKING import and `cast` import from `acp_agent.py`
- Added 3 tests in `test_create_turn.py`: isinstance positive (with state), isinstance positive (without state), and existing create_turn test

### Key design decisions
1. **Protocols for state/event** — Used `@runtime_checkable Protocol` in acp package to avoid circular dependency (acp cannot import from agentpool). `_SessionStateProtocol` requires `pop_update()` and `clear()`. `_UpdateEventProtocol` requires `wait_with_timeout()` and `clear()`.
2. **Deferred wiring** — `_attach_state()` allows ACPAgentAPI to be constructed first (with just connection), then state/event attached after they're created in `_initialize()`. This matches the existing construction order.
3. **`stream_events()` drains queue post-prompt** — In ACPTurn.execute(), `prompt()` is awaited first (blocking until response), then `stream_events()` drains queued updates. The 50ms wait_with_timeout handles straggling notifications. Loop ends when a full drain cycle produces no updates.
4. **`_consumed_updates` list** — `stream_events()` collects all yielded updates in `_consumed_updates`. `get_messages()` returns a copy of this list. Cleared at the start of each `stream_events()` call.
5. **`@runtime_checkable` limitation** — Only checks method existence, not attribute state. An ACPAgentAPI without state/event still passes isinstance because the methods exist on the class (they just return early/empty).

### Files modified
- `src/acp/agent/acp_agent_api.py` — Added Protocols, state/event params, stream_events(), get_messages()
- `src/agentpool/agents/acp_agent/turn.py` — Added @runtime_checkable to ACPClientProtocol
- `src/agentpool/agents/acp_agent/acp_agent.py` — Removed cast, TODO, unused imports; added _attach_state() call
- `tests/agents/acp_agent/test_create_turn.py` — Added isinstance tests

### Test results
- `tests/agents/acp_agent/ -k stream`: 3 passed
- `tests/integration/test_acp_streaming.py -m acp_snapshot`: 2 passed (V10 baseline preserved)
- `test_create_turn.py`: 3 passed (including 2 new isinstance tests)
- Ruff: All checks passed

## Task 6: Update tests mocking _run_stream_once for new execution path

### Summary
Updated 10 test files to replace `_run_stream_once` references with `_stream_events`.
`_stream_events` is the public entry point that will survive T2's refactor.

### Files Modified (test files only)

**Active mock updates (5 files):**
1. `tests/agents/test_run_stream_direct_gating.py` — Moved spy from `_run_stream_once` to `_stream_events`. Removed `_run_stream_once` override entirely.
2. `tests/servers/opencode_server/test_auto_resume_message_redflag.py` — Renamed mock fn + attribute.
3. `tests/servers/opencode_server/test_session_scoped_consumer.py` — Same pattern.
4. `tests/orchestrator/test_session_pool_input_provider.py` — Updated mock, docstring, test name.
5. `tests/orchestrator/test_session_lifecycle.py` — Renamed MockAgent method.

**Comment/name-only updates (5 files):**
6. `tests/orchestrator/test_run_lifecycle.py` 7. `tests/agents/test_base_agent_run_v2.py`
8. `tests/agents/native_agent/test_inject_prompt_cross_task.py`
9. `tests/agents/test_capability_hooks_standalone.py`
10. `tests/servers/opencode_server/test_cancelled_message.py`

### Key Findings
- 10 files had references (vs 4 in the plan).
- For Mock objects, attribute name is cosmetic but ensures consistency.
- `test_run_stream_direct_gating.py` works because `run_stream()` → `_run_stream_once()` → `_stream_events()` preserves call count.
- Pre-existing failure: `test_acp_adapter_has_todo_comment` fails due to T1's source changes (removed TODO comment from acp_agent_api.py). Not caused by T6.
- V10 snapshot tests pass (2/2).
- `grep -rn '_run_stream_once' tests/` returns 0 matches.
- 65 tests pass across all modified files.

## Task 2: Refactor _stream_events() to delegate to ACPTurn.execute()

### Summary
Replaced the inline 200-LOC `_stream_events()` implementation with a thin delegate that creates an `ACPTurn` and iterates `turn.execute()`, intercepting events for enrichment.

### What was done
1. **`turn.py`**: Added `self._prompt_response: PromptResponse | None = None` to `ACPTurn.__init__` and stored the response after `await self._acp_client.prompt()` so the delegate can access `stop_reason` for `finish_reason`.
2. **`acp_agent.py`**: Replaced lines 415-614 (inline `_stream_events`) with a thin delegate that:
   - Yields `RunStartedEvent` (ACPTurn doesn't yield this)
   - Handles session forking for `store_history=False`
   - Creates an `ACPTurn` with the correct (possibly forked) `session_id`
   - Iterates `turn.execute()`, intercepting:
     - `ToolResultMetadataEvent` → captured for enrichment, not yielded
     - `ToolCallCompleteEvent` → enriched with `agent_name` and `metadata`
     - `StreamCompleteEvent` → intercepted (not yielded); replaced with enriched version after iteration
   - Catches `CancelledError` for cancellation handling
   - After iteration: builds enriched `StreamCompleteEvent` with `finish_reason`, `usage`, `cost_info`, `name`, `model_name`, `message_id`, `session_id`, `parent_id`
3. **`acp_agent.py`**: Cleaned up unused imports (`ModelRequest`, `ModelResponse`, `UserPromptPart`, `EventEnvelope`, duplicate `ThinkingPart`/`ToolCallPart`/`UserContent` in TYPE_CHECKING).
4. **`test_acp_streaming.py`**: Updated `_MockAPI` to accept `state` and `update_event` parameters and implement `stream_events()` and `get_messages()` with the same polling logic as `ACPAgentAPI`. Updated `_make_acp_agent_with_mocks()` to pass state/event to `_MockAPI`.

### Key design decisions
1. **RunStartedEvent stays in delegate** — ACPTurn.execute() doesn't yield it (it's published by RunHandle.start() in the protocol-server path). The standalone `_stream_events` path needs it.
2. **StreamCompleteEvent intercepted and replaced** — ACPTurn's `StreamCompleteEvent` lacks `finish_reason`, `name`, `usage`, `cost_info`. The delegate intercepts it (breaks the loop) and builds a replacement using `turn._prompt_response.stop_reason` and `turn.message_history`.
3. **ToolResultMetadataEvent enrichment in delegate** — ACPTurn doesn't do this enrichment. The delegate captures `ToolResultMetadataEvent` events (not yielded) and uses them to enrich subsequent `ToolCallCompleteEvent` metadata.
4. **Session forking before ACPTurn creation** — The delegate handles `store_history=False` by forking the session before creating ACPTurn, passing the forked `acp_session_id` to the constructor. `create_turn()` doesn't support forked sessions (uses `self._sdk_session_id`), so the delegate creates ACPTurn directly.
5. **`self._prompt_task` no longer set** — The inline implementation created a background `prompt_task` for concurrent polling. ACPTurn awaits `prompt()` directly. `self._prompt_task` is only set to `None` on cancellation. Interrupt support (`_interrupt()`) will need updating in a future task.
6. **Tool bridge context preserved** — The delegate wraps `turn.execute()` with `self._tool_bridge.set_run_context()` just like the inline implementation.
7. **Event ordering equivalent** — V10 snapshot tests pass unchanged: `RunStartedEvent → PartDeltaEvent(s) → ToolCallStartEvent → ToolCallCompleteEvent (enriched) → PartDeltaEvent(s) → StreamCompleteEvent`.

### Files modified
- `src/agentpool/agents/acp_agent/turn.py` — Added `_prompt_response` field and storage
- `src/agentpool/agents/acp_agent/acp_agent.py` — Replaced inline `_stream_events` with delegate; cleaned imports
- `tests/integration/test_acp_streaming.py` — Updated `_MockAPI` with `stream_events()`/`get_messages()`

### Test results
- V10 snapshot tests: 2/2 passed (snapshot baseline unchanged)
- ACP agent tests: 58/58 passed
- Stream tests: 35/35 passed
- Ruff: All checks passed (source files)

## Task 3: Remove _run_stream_once() hook firing for ACP agents

### Summary
Deleted the two `AGENT_TYPE != "native"` branches in `_run_stream_once()` that fired pre-turn and post-turn hooks for ACP agents. Hooks now fire exclusively through `HookAwareTurn` in `Turn.execute()`.

### What was done
1. **Pre-turn hook firing** (was at line ~1593): Removed the entire `if self.AGENT_TYPE != "native" and self.hooks and "pre_turn" not in run_ctx.hooks_fired:` block that called `run_pre_turn_hooks()` and handled deny by yielding a cancelled `StreamCompleteEvent`. ~24 lines deleted.
2. **Post-turn hook firing** (was at line ~1655): Removed the `if self.AGENT_TYPE != "native" and self.hooks and "post_turn" not in run_ctx.hooks_fired:` block that called `run_post_turn_hooks()`. ~18 lines deleted.
3. Kept the `hooks_fired` guard on `run_ctx` (T4 will remove it).
4. Kept `AGENT_TYPE` field itself — only non-hook `== "native"` branches remain (lines 1051, 1104 for session_pool access).

### Key design decisions
- No changes needed to `HookAwareTurn` or `ACPTurn` — they already fire hooks via `Turn.execute()`, and the `hooks_fired` double-fire guard was preventing duplicates. With the old path removed, the guard becomes a no-op (T4 will clean it up).
- The pre-turn deny path (yielding cancelled StreamCompleteEvent) is now handled by `HookAwareTurn.fire_pre_turn_hooks()` which raises on deny, caught by `ACPTurn.execute()`.

### Files modified
- `src/agentpool/agents/base_agent.py` — Removed ~42 lines of ACP hook firing code

### Test results
- `tests/agents/`: 404 passed, 1 skipped, 6 deselected
- `tests/integration/test_acp_streaming.py -m acp_snapshot`: 2 passed (V10 snapshots unchanged)
- Ruff: All checks passed
- `grep -n 'AGENT_TYPE.*native' base_agent.py`: Only 2 remaining (both `== "native"`, non-hook-related)

## Task 5: Remove deprecated queue_prompt/inject_prompt ACP branching

### What changed
- `src/agentpool/agents/base_agent.py`: Removed `AGENT_TYPE == "native"` check from both `queue_prompt` and `inject_prompt` methods
- ACP agents now use the same `session_pool.followup()`/`steer()` path as native agents
- Removed ACP-specific legacy fallback paths in `inject_prompt` (SessionPool.inject_prompt fallback, shared agent receive_request fallback, warning log)
- Standalone agents (no session_pool) still use `injection_manager` as fallback
- ~30 lines removed

### Key observations
- The `AGENT_TYPE == "native"` check was the only thing preventing ACP agents from using the session_pool delegation path
- The removed ACP legacy paths (lines 1141-1174) were redundant with the session_pool.steer() delegation path — they handled the same "no active run context" case but through a different code path
- The `test_non_native_inject_prompt_no_deprecation` test still passes because it tests a standalone ACP agent (no session_pool), which doesn't trigger the deprecation warning
- The test name is now slightly misleading (implies ACP agents never get warnings) but the test itself is correct (standalone agents don't get warnings)
- Deprecation warnings updated from "pooled native agents" to "pooled agents" to reflect the unified behavior

### Files touched
- `src/agentpool/agents/base_agent.py` (modified)
- `.omo/evidence/task-5-pre-m4-protocol-cleanup.log` (created)

## Task 2.5: Remove unreachable code in session_controller.py

### What was done
- Removed 8 lines of dead code (lines 484-491) in `get_or_create_session_agent()` that were unreachable after `return agent` at line 363.
- The deleted code was a duplicate of the error handling block at lines 368-375 that raises `RuntimeError` when agent config is not found.
- Since the function already returns after successfully creating the agent, the duplicate error handler could never execute.

### Files modified
- `src/agentpool/orchestrator/session_controller.py` (8 lines deleted)

### Verification
- Ruff: All checks passed
- Tests: 292/293 orchestrator tests passed (1 pre-existing failure from other uncommitted changes)
- The removed code was purely syntactic dead code — no behavioral impact

## Task 2.1a: Rename _SessionContext to McpSessionContext, add MCPManager.add_transport()

### What was done
1. **Renamed `_SessionContext` to `McpSessionContext`** (public) in `src/agentpool/mcp_server/manager.py` — class definition at line 120, all type annotations and docstrings updated.
2. **Added `MCPManager.add_transport(session_id, client_id, transport, skill_name=None)`** — async method that delegates to the session's `SessionConnectionPool.add_transport()`. Creates the session context if it doesn't exist via `get_or_create_session()`.
3. **Updated all references** in 10 test files: `test_session_lifecycle.py` (import + isinstance + docstrings), `test_review_fixes.py`, `test_review_fixes_r3.py`, `test_e2e_session_controller.py`, `test_session_close_integration.py`, `test_stale_mcp_connection.py`, `test_resume_session_lifecycle.py`, `test_e2e_session_lifecycle.py`, `test_resume_reconnect.py`.
4. **Verified** `get_session_context()` (line 212) and `update_session_snapshot()` (line 219) already exist and work correctly.
5. **Did NOT modify** `SessionConnectionPool.add_transport()` at `session_pool.py:225` — the new MCPManager method is a delegation wrapper.

### Files modified
- `src/agentpool/mcp_server/manager.py` — Class rename + new `add_transport` method
- 10 test files — Updated references from `_SessionContext` to `McpSessionContext`

### Key decisions
- **New `add_transport` vs existing `add_acp_transport`**: The existing `add_acp_transport` is ACP-specific (tracks connection IDs, takes `connection_id` and `session_key` params). The new `add_transport` is a general delegation to `SessionConnectionPool.add_transport(client_id, transport, skill_name)` — simpler signature for non-ACP use cases.
- **Docs/openspec not updated**: Historical RFCs and archived OpenSpec changes still reference `_SessionContext`. These are immutable historical documents and should not be modified.

### Verification
- `grep -rn '_SessionContext' src/ tests/` returns 0 matches
- `uv run ruff check src/agentpool/mcp_server/manager.py` — All checks passed
- `uv run ruff check` on all 10 modified test files — All checks passed
- AST verification confirmed: `McpSessionContext` class at line 120, `add_transport` method at line 237, `get_session_context` at line 212, `update_session_snapshot` at line 219
- Full test suite could not run due to pre-existing `ImportError: cannot import name 'RunStatus'` from other worktree changes in `run.py`

---

## Task 1.4: Remove hooks_fired double-fire guard

### Summary
Removed the `hooks_fired` set from `AgentRunContext` and replaced the tool-log idempotency guard in `HookAwareTurn._log_tool_execution` with a per-Turn-instance `_logged_tools: set[str]` set.

### Files Modified
- `src/agentpool/agents/context.py` — Removed `hooks_fired` field from `AgentRunContext`
- `src/agentpool/orchestrator/turn.py` — Added `__init__` to `HookAwareTurn` initializing `_logged_tools`; removed all `hooks_fired` guards from `_fire_pre_turn_hooks`, `_fire_post_turn_hooks`, `_fire_pre_tool_hooks`, `_fire_post_tool_hooks`; replaced `_log_tool_execution` idempotency with `_logged_tools`
- `src/agentpool/agents/base_agent.py` — Removed `run_ctx.hooks_fired.clear()` from `_run_stream_once()`
- `src/agentpool/orchestrator/run.py` — Removed `self.run_ctx.hooks_fired.clear()` from `start()`
- `tests/agents/native_agent/test_native_turn_hooks.py` — Updated `test_tool_hooks_not_fired_by_hook_aware_turn_for_native` to check `_logged_tools` instead of `hooks_fired`; updated `test_hooks_fired_prevents_double_firing_via_old_path` to reflect guard removal
- `tests/orchestrator/test_session_pool_hooks.py` — Updated `test_hooks_fired_cleared_between_turns` to remove `hooks_fired.clear()` call

### Key Decisions
- `_logged_tools` is a per-Turn-instance set, not on `AgentRunContext`. A new Turn is created for each turn, so the set is naturally fresh. No cross-turn reset needed.
- `HookAwareTurn.__init__` calls `super().__init__()` for cooperative MRO chain. Both `NativeTurn.__init__` and `ACPTurn.__init__` already call `super().__init__()`, so `_logged_tools` is initialized correctly.
- The double-fire guard for hooks (`pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) was removed entirely — not replaced. T3 eliminated the old ACP standalone path that caused double-firing, so the guard is no longer needed.
- The tool-log idempotency guard was REPLACED (not removed) with `_logged_tools` to prevent double-logging within a single Turn instance.

### Pre-existing Issues (NOT caused by this task)
- `test_run_handle_steer_for_acp_path` fails due to `DirectChannel.deliver_feedback()` (added by a previous task) causing `steer()` to route through the no-op feedback path instead of queuing to `queued_steer_messages`.
- `RunStatus` was removed from `run.py` by a previous task but `orchestrator/__init__.py` still imports it, causing collection errors in `test_run_lifecycle.py` and `test_runhandle_checkpoint.py`.

### Verification
- `grep -rn 'hooks_fired' src/` returns 0 matches
- `uv run ruff check` on all modified files — All checks passed
- 862 tests passed (agents + orchestrator, excluding pre-existing failures)
- ACP snapshot tests: 2 passed
