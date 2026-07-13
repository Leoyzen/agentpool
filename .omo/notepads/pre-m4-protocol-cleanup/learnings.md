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
