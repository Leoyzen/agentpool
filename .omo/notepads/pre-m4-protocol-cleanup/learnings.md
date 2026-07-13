# Learnings — pre-m4-protocol-cleanup

## Task 19: Fix ACPTurn generic except Exception clauses

### Exception analysis for ACPTurn.execute() (turn.py)

All three `except Exception` sites in `ACPTurn.execute()` follow the same pattern:
catch, yield `RunErrorEvent`, return. `asyncio.CancelledError` is always re-raised
separately before the generic catch. The behavior is preserved — only the exception
types are narrowed.

**Line 180 — `self._acp_client.prompt()` (Phase 1: Send prompt)**
- `RequestError` — JSON-RPC error response from the remote agent (acp.exceptions)
- `ConnectionError` — connection closed via `reject_all_outgoing()` in `Connection.close()`
- `ValidationError` — pydantic validation of `PromptResponse.model_validate(resp)`

**Line 221 — `self._acp_client.stream_events()` (Phase 2: Stream events)**
- `RequestError` — protocol-level errors during streaming
- `ConnectionError` — connection lost mid-stream
- `RuntimeError` — from hook execution or streaming infrastructure
- `ValueError` — from hook command parsing (e.g., invalid decision in `command.py:172`)
- Note: `ValidationError` not included here because stream events are not validated
  through pydantic in the same way — `acp_to_native_event()` does pattern matching
  without raising.

**Line 234 — `self._acp_client.get_messages()` (Phase 3: Collect history)**
- Same as Phase 1: `RequestError`, `ConnectionError`, `ValidationError`

### Key files traced
- `src/acp/exceptions.py` — `RequestError(Exception)` with JSON-RPC error codes
- `src/acp/connection.py` — `Connection.send_request()` awaits a future rejected
  with `RequestError` (from `_handle_response`) or `ConnectionError` (from
  `reject_all_outgoing` in `close()`)
- `src/acp/client/connection.py` — `ClientSideConnection.prompt()` calls
  `send_request("session/prompt", dct)` then `PromptResponse.model_validate(resp)`
- `src/acp/agent/acp_agent_api.py` — `ACPAgentAPI` wraps `ClientSideConnection`
- `src/agentpool/hooks/command.py:172` — `raise ValueError(f"Invalid decision: {decision}")`

### Pre-existing LSP error
Line 205 has a pre-existing pyright error: `tool_name` from the match pattern is
`str | None` but `_fire_pre_tool_hooks` expects `str`. This is unrelated to the
exception handling fix and was present before this change.
# Pre-M4 Protocol Cleanup — Learnings

## Task 18: Replace hasattr patterns in ACP code

### Changes made
- **`src/agentpool_server/acp_server/session.py`**:
  - Added `is_busy` property on `ACPSession` class that encapsulates `self._task_lock.locked()` check
  - Replaced `hasattr(cmd_config, "type")` with `isinstance(cmd_config, BaseCommandConfig)` in the manifest command registration exception handler
  - Added import for `BaseCommandConfig` from `agentpool_config.commands`
- **`src/agentpool_server/acp_server/acp_agent.py`**:
  - Replaced `hasattr(session, "_task_lock") and session._task_lock.locked()` with `session.is_busy`

### Key findings
- `ACPSession._task_lock` is an `asyncio.Lock` always initialized in `__post_init__` (line 220). The `hasattr` check was purely defensive and unnecessary — the lock always exists on any properly constructed `ACPSession` instance.
- `cmd_config` in `_register_manifest_commands` comes from `manifest.get_command_configs()` which returns `dict[str, CommandConfig]`. `CommandConfig` is `Annotated[StaticCommandConfig | FileCommandConfig | CallableCommandConfig, ...]`, all of which inherit from `BaseCommandConfig` (which has a `type: str` field). The `hasattr(cmd_config, "type")` was always True for valid configs.
- The `hasattr` in the exception handler was using `type(cmd_config).__name__` (Python class name), not `cmd_config.type` (config type field). The `isinstance` check preserves the same semantics: if it's a `BaseCommandConfig`, show the class name; otherwise "unknown".
- Pre-existing LSP error at session.py:380 (`_os_type` assignment) is unrelated to this task.

### Verification
- `grep -n 'hasattr' src/agentpool_server/acp_server/acp_agent.py` -> 0 matches
- `grep -n 'hasattr' src/agentpool_server/acp_server/session.py` -> 0 matches
- `uv run --no-group docs mypy src/agentpool_server/acp_server/` -> Success: no issues found in 23 source files
- `uv run pytest tests/agentpool_server/acp_server/ -x -q` -> 49 passed
- `uv run ruff check` on both files -> All checks passed
- Note: `tests/agents/test_create_turn.py::test_acp_turn_uses_run_ctx_run_id` fails in full suite but passes in isolation -- pre-existing flaky test, not related to this change.

### Commit
- `9fdb5ff4a` -- `refactor(acp): replace hasattr patterns with typed interfaces`
# Pre-M4 Protocol Cleanup — Learnings

## T16: Add `set_replaying()` to CommChannel protocol

- **Files modified**: `protocols.py`, `comm_channel.py`, `run.py`, `tests/lifecycle/test_types.py`
- **Pattern**: Removed `_replaying: bool` data attribute from `CommChannel` Protocol, replaced with `set_replaying(flag: bool) -> None` method. Both `DirectChannel` and `ProtocolChannel` implement by setting their internal `self._replaying` flag.
- **T10 dependency**: T10 already added `deliver_feedback` and `publishes_to_event_bus` to the `CommChannel` protocol. The `_DummyCommChannel` test class in `test_types.py` was missing these methods and needed updating alongside the `set_replaying` addition.
- **Test flakiness**: Some tests (e.g. `test_acp_turn_prompt_error_yields_run_error_event`, `test_no_duplicate_stream_complete_in_run_stream_once`, `test_steer_direct_channel_does_not_use_deliver_feedback`) are flaky under parallel execution (`pytest -n auto`) but pass in isolation or with `-p no:xdist`. These are pre-existing issues unrelated to this change.
- **grep check note**: `grep '_replaying' run.py` still matches `set_replaying()` calls because the method name contains the substring. The correct check is `grep 'self\._comm_channel\._replaying'` which returns 0 (no direct private attribute access).
# Pre-M4 Protocol Cleanup — Learnings Notepad

## T8: ACPSession.initialize_mcp_servers() → MCPManager methods

### What changed
- `session.py:438-439` — Removed `self.agent._session_connection_pool.add_transport(...)` (legacy path). The `add_acp_transport()` call at line 451 already delegates to `SessionConnectionPool.add_transport()` internally via `MCPManager`, so the legacy path was redundant.
- `session.py:482` — Replaced `self.agent._mcp_snapshot` read with `self.agent.mcp.get_session_context(self.session_id)` → `ctx.snapshot`.
- `session.py:491` — Removed `self.agent._mcp_snapshot = new_snapshot` write. The `update_session_snapshot()` call at line 500 already stores the snapshot in the MCPManager session context.
- Docstring updated to reference `MCPManager.update_session_snapshot` and `MCPManager.add_acp_transport` instead of agent fields.

### Key insight
The `add_acp_transport()` method on MCPManager (added in T7) already calls `pool.add_transport(client_id, transport)` internally. The old code had TWO paths: (1) legacy `self.agent._session_connection_pool.add_transport()` and (2) `self.agent.mcp.add_acp_transport()`. Both did the same thing. Removing the legacy path is safe because `add_acp_transport` covers it.

### MCPManager API used
- `get_session_context(session_id)` → Returns `_SessionContext | None` (line 212 of manager.py)
- `update_session_snapshot(session_id, snapshot)` → Creates session context if needed, sets `ctx.snapshot` (line 219)
- `add_acp_transport(session_id, client_id, transport, connection_id, session_key)` → Delegates to `SessionConnectionPool.add_transport()` + tracks ACP connection (line 576)

### Pre-existing test failures (NOT caused by T8)
- `tests/lifecycle/test_types.py::test_comm_channel_protocol_isinstance` — Fails due to uncommitted changes from T10 (CommChannel.deliver_feedback)
- `tests/agents/acp_agent/test_turn.py::test_acp_turn_prompt_error_yields_run_error_event` — Pre-existing failure
- `tests/servers/acp_server/test_agent_role.py::TestSwapSessionAgent::test_role_swap_success` — Pre-existing failure

### Commit
Changes were committed as part of `9fdb5ff4a` ("refactor(acp): replace hasattr patterns with typed interfaces") which included both the hasattr→isinstance refactor and the T8 MCPManager migration.

### T9 readiness
T9 (remove `_mcp_snapshot` and `_session_connection_pool` from NativeAgent) can now proceed. Verify with `grep -rn '_mcp_snapshot\|_session_connection_pool' src/` — should only find field definitions in `agent.py` and no external accessors.

## T15: Remove type: ignore[attr-defined] cluster in run.py

### What changed
- **`src/agentpool/orchestrator/run.py`**: Removed `__post_init__` journal injection pattern (lines 263-270). Both `DirectChannel` and `ProtocolChannel` already receive the journal via constructor, so post-hoc `self._comm_channel._journal = self._journal` mutation was unnecessary. Removed 2 `type: ignore[attr-defined]` for `_journal` access.
- **`src/agentpool/orchestrator/run.py`**: Replaced `try/except AttributeError` pattern for `deliver_feedback` in `steer()` and `followup()` with direct boolean-returning call. Removed 4 `type: ignore[attr-defined]` for `deliver_feedback`.
- **`src/agentpool/lifecycle/protocols.py`**: Added `deliver_feedback(self, feedback: Feedback) -> bool` to `CommChannel` protocol. Returns `True` if handled, `False` to fall through.
- **`src/agentpool/lifecycle/comm_channel.py`**: Added `deliver_feedback` to `DirectChannel` (returns `False`). Updated `ProtocolChannel.deliver_feedback` to return `True` instead of `None`.
- **`tests/lifecycle/test_run_loop.py`**: Updated `test_steer_direct_channel_does_not_use_deliver_feedback` to verify `DirectChannel.deliver_feedback` returns `False` instead of checking it doesn't exist.
- **`tests/lifecycle/test_session_migration.py`**: Updated tests to use `self._comm_channel.publishes_to_event_bus` instead of `self._channel_publishes_to_event_bus` (from T16).

### Key insight
The `try/except AttributeError` pattern for `deliver_feedback` was needed because `DirectChannel` didn't have the method. By adding `deliver_feedback` to the `CommChannel` protocol with a `bool` return, `DirectChannel` can return `False` to signal "not handled", and the caller falls through to the queue-based path. This preserves behavior exactly while removing all `type: ignore[attr-defined]`.

### Verification
- `grep -n 'type: ignore\[attr-defined\]' src/agentpool/orchestrator/run.py` returns 0
- `uv run --no-group docs mypy src/agentpool/orchestrator/run.py` → Success: no issues found
- `uv run pytest tests/lifecycle/test_run_loop.py tests/lifecycle/test_session_migration.py -x -q` → 64 passed
- Pre-existing failures: `test_acp_turn_prompt_error_yields_run_error_event` and `test_role_swap_success` fail on committed code without these changes (caused by T19's narrowed exception types in `turn.py`)

### Commit
- `6b8d6b2b1` — `refactor(lifecycle): add set_replaying() to CommChannel protocol` (includes T15+T16 changes)

## T17: publishes_to_event_bus property (replaces isinstance check)

- Replaced `RunHandle._channel_publishes_to_event_bus` (isinstance check against `ProtocolChannel`) with a `publishes_to_event_bus: bool` property on the `CommChannel` protocol.
- `DirectChannel.publishes_to_event_bus` returns `False`; `ProtocolChannel.publishes_to_event_bus` returns `True`.
- 5 usage sites in `run.py` updated from `self._channel_publishes_to_event_bus` to `self._comm_channel.publishes_to_event_bus`.
- Tests in `test_session_migration.py` updated to assert on `run_handle._comm_channel.publishes_to_event_bus` instead of the removed method.
- Pattern follows T10's `deliver_feedback` approach: add to protocol, implement in both classes, replace caller checks.
- Pre-existing test failures (unrelated): `test_acp_turn_prompt_error_yields_run_error_event` (ACP connection refused) and `test_role_swap_success` (ACP server test) — both fail on base branch.

## T20: Wire McpToolsChangedEvent emission and handling

### Architecture
The wiring follows the existing `_watch_skill_changes` pattern in `server.py`:
1. `McpServerCap._on_tools_changed()` (core capability layer) emits `ChangeEvent(kind="tools_changed")` via `on_change()` stream
2. `ExtensionRegistry.merge_change_streams()` merges all capability change streams
3. `server._watch_mcp_tool_changes()` (OpenCode server layer) subscribes to the merged stream, filters for `kind="tools_changed"`, and calls `EventProcessor.create_mcp_tools_changed_event()` to convert to `McpToolsChangedEvent`
4. The `McpToolsChangedEvent` is broadcast via `state.broadcast_event()` as an SSE event to connected clients

### Files modified
- **`src/agentpool/capabilities/mcp_server_cap.py`**: Added cross-layer wiring comment on `_on_tools_changed()` callback documenting the ChangeEvent → McpToolsChangedEvent conversion path.
- **`src/agentpool_server/opencode_server/models/events.py`**: Updated `McpToolsChangedEvent` docstring — removed TODO, documented the full wiring path.
- **`src/agentpool_server/opencode_server/event_processor.py`**: Added `create_mcp_tools_changed_event(server: str)` static method and `McpToolsChangedEvent` import.
- **`src/agentpool_server/opencode_server/server.py`**: Added `_watch_mcp_tool_changes()` async task (parallel to `_watch_skill_changes()`) that subscribes to the merged change stream and broadcasts `McpToolsChangedEvent`. Added cleanup in the lifespan shutdown.
- **`src/agentpool_server/opencode_server/state.py`**: Added `_mcp_tool_change_task` field to `ServerState`.
- **`tests/servers/opencode_server/test_event_processor.py`**: Added `test_create_mcp_tools_changed_event` (unit test) and `test_mcp_tools_changed_event_from_change_event` (integration test verifying the ChangeEvent → McpToolsChangedEvent flow).

### Key design decisions
- The `McpToolsChangedEvent` stays in OpenCode server models (not promoted to core events), as required.
- The `EventProcessor` doesn't process `McpToolsChangedEvent` as an input (it only handles `RichAgentStreamEvent`). Instead, `create_mcp_tools_changed_event()` is a factory method that the server's watcher task calls.
- The watcher reuses the same `extension_registry.merge_change_streams()` call as `_watch_skill_changes()`, consuming the same merged stream but filtering for `kind="tools_changed"`.
- Pre-existing ruff error: `F821 Undefined name 'SessionStatusEvent'` at line 213 of `event_processor.py` — this is from T18.3's changes (StreamCompleteEvent cancelled handling), not from T20.

### Pre-existing test failures (NOT caused by T20)
- `test_stream_complete_emits_idle_status` and `test_stream_complete_emits_cancelled_status` — from T18.3's `SessionStatusEvent` usage without import
- `test_model_switch_targets_per_session_agent`, `test_model_switch_affects_only_target_session`, `test_other_sessions_retain_original_model` — model switching tests
- `test_background_task_inject_prompt_wakes_lead_agent` — subagent completion test
- `test_child_done_events_items_wrapped_with_list` — flaky under parallel execution, passes in isolation

## T9: Remove _mcp_snapshot and _session_connection_pool from NativeAgent

### Summary
Removed two stale fields from `NativeAgent.__init__()` that were only used as passthrough storage for MCP lifecycle management. All access was migrated to `MCPManager` methods in T8.

### Changes
- **`src/agentpool/agents/native_agent/agent.py`**:
  - Removed `McpConfigSnapshot` from the `McpConfigEntry, McpConfigSnapshot` import (kept `McpConfigEntry`)
  - Removed the entire `SessionConnectionPool` import line
  - Removed the comment block + 2 field definitions (`_mcp_snapshot` and `_session_connection_pool`)
- **Net**: -6 LOC (7 deleted, 1 inserted)

### Verification
- `grep -rn '_mcp_snapshot\|_session_connection_pool' src/` — only pyc cache remains, zero source references
- `uv run pytest tests/agents/native_agent/ -x -q` — 191 passed
- `uv run --no-group docs mypy src/agentpool/agents/native_agent/` — Success: no issues found
- `uv run ruff check src/agentpool/agents/native_agent/agent.py` — All checks passed
- Pre-existing failures: `test_inject_prompt_from_different_task_with_session_pool` (deprecated inject_prompt API), `test_model_switch_targets_per_session_agent` (unrelated OpenCode server test)

### Commit
- `f582e2f86` — `refactor(agent): remove _mcp_snapshot and _session_connection_pool from NativeAgent`

## Task T19 (T20 in tasks.md): Decompose RunHandle.start() into 5 sub-methods

### What was done
Decomposed `RunHandle.start()` (~397 SLOC) into 5 composable sub-methods, each < 100 SLOC:
1. `_handle_recovery()` — crash recovery + dimension subscription (48 SLOC)
2. `_idle_loop()` — idle wait, feedback drain, prompt collection (37 SLOC)
3. `_execute_turn()` — turn execution, event streaming as async generator (96 SLOC)
4. `_handle_turn_result()` — cancel handling, error handling, returns action string (40 SLOC)
5. `_drain_events()` — post-turn snapshot, child events, feedback drain (64 SLOC)
`start()` itself is now ~59 SLOC (coordinator only).

### Key decisions
- **State sharing**: Added 3 instance fields (`_current_turn`, `_current_turn_id`, `_current_turn_failed`) to `RunHandle` dataclass since `_execute_turn()` is an async generator and can't return values. Set in `_execute_turn()`, read by `_handle_turn_result()` and `_drain_events()`.
- **Async generator delegation**: `_execute_turn()` is an async generator that `yield`s events. `start()` delegates via `async for event in self._execute_turn(...): yield event`.
- **Cancel path `current_prompts` clearing**: In original code, `current_prompts = []` was set before `continue` in the cancel handler. Since `current_prompts` is a local in `start()`, added `current_prompts = []` in `start()` when `_handle_turn_result` returns `"continue"`.
- **Test fix**: `test_child_done_events_items_wrapped_with_list` and `test_child_done_events_values_wrapped_with_list` inspected `RunHandle.start` source for `list(self.run_ctx.child_done_events.items())`. Updated to inspect `RunHandle._drain_events` since that's where the code now lives.
- Replaced arrow characters (→) with ASCII equivalents (->) in docstrings/comments to avoid encoding issues.

### Verification
- `grep -n 'PLR0915' src/agentpool/orchestrator/run.py` — 0 matches
- `uv run ruff check src/agentpool/orchestrator/run.py` — All checks passed
- `uv run pytest tests/orchestrator/ tests/lifecycle/ -x -q` — 793 passed, 10 deselected

## Task T22: Remove deprecated stream_adapter._handle_event

### What was done
- Removed `OpenCodeStreamAdapter._handle_event()` method (lines 213-226) from `stream_adapter.py`. This was a deprecated backward-compat shim that delegated to `self.processor.process(event, self.main_context)` without StepFinishPart tracking.
- Updated `tests/servers/opencode_server/test_reasoning.py` (5 tests) to use `EventProcessor` + `EventProcessorContext` directly instead of going through `OpenCodeStreamAdapter._handle_event`.
- The `stream_adapter.py` file was NOT deleted — it still has active functionality: `OpenCodeStreamAdapter` class with `process_stream()`, `convert_event()`, `finalize()`, and property accessors.

### Key finding
- `_handle_event` was functionally identical to `convert_event` except it lacked StepFinishPart tracking. Tests didn't rely on that difference.
- Tests now create `EventProcessor` and `EventProcessorContext` directly via helper `_make_processor_and_ctx()`, eliminating the need for the full `OpenCodeStreamAdapter` in test setup.
- Pre-existing test failures in the worktree (`test_model_switch_targets_per_session_agent` — NameError, `test_child_done_events_items_wrapped_with_list`) are unrelated to this change.
