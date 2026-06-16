## 1. RunExecutor Feature Parity

- [ ] 1.1 Add `emitted_tool_starts` deduplication set to `RunExecutor.execute()` to prevent duplicate `ToolCallStartEvent` when both `FunctionToolCallEvent` and `PartStartEvent(part=BaseToolCallPart)` fire for the same `tool_call_id`.
- [ ] 1.2 Add `PartStartEvent(part=BaseToolCallPart)` → `ToolCallStartEvent` mapping in `RunExecutor.execute()` (currently only `FunctionToolCallEvent` is mapped).
- [ ] 1.3 Add cancelled-response fallback in `RunExecutor.execute()`: when `run_ctx.cancelled` and no `response_msg` was produced, create an empty `ChatMessage` with `finish_reason="stop"` instead of raising `RuntimeError`.
- [ ] 1.4 Add concurrent-run warning in `RunExecutor.execute()`: when `self._iteration_task` is still active from a previous run, log a warning.
- [ ] 1.5 Add `session_id` and `parent_session_id` to `RunStartedEvent` emitted by `RunExecutor.execute()` (port from agent.py:1131-1136).

## 2. Refactor _stream_events to Use RunExecutor

- [ ] 2.1 Replace `_run_agentlet_core()` call in `_stream_events()`'s `agent_iteration_task()` closure with `RunExecutor.execute()`. Pass `RunHandle` to `RunExecutor` constructor so `active_agent_run` is properly managed.
- [ ] 2.2 Remove `RunStartedEvent` yield from `_stream_events()` (now emitted by `RunExecutor`).
- [ ] 2.3 Remove `StreamCompleteEvent` yield and cancelled-response fallback from `_stream_events()` (now emitted by `RunExecutor`).
- [ ] 2.4 Remove background-task wrapping — `RunExecutor.execute()` already has built-in background-task + consumer-loop isolation.
- [ ] 2.5 Verify event flow: `_stream_events()` → `RunExecutor.execute()` → TurnRunner → EventBus.

## 3. Refactor _execute_node to Use RunExecutor

- [ ] 3.1 Replace `_run_agentlet_core()` call in `_execute_node()` with iteration over `RunExecutor.execute()`.
- [ ] 3.2 Forward all events from `execute()` to `state.event_queue` (including `RunStartedEvent` — verify graph consumers handle it).
- [ ] 3.3 Extract final `StreamCompleteEvent.message` as the return value.

## 4. Remove _run_agentlet_core

- [ ] 4.1 Delete `_run_agentlet_core()` method from `native_agent/agent.py`.
- [ ] 4.2 Remove `merge_queue_into_iterator` import from `native_agent/agent.py`.
- [ ] 4.3 Migrate all test callers of `_run_agentlet_core()` to use `RunExecutor.execute()` (7 test files: `test_native_agent_event_bus.py`, `test_run_agentlet_core_next.py`, `test_steer_followup_edge_cases.py`, and others found via `lsp_find_references`).
- [ ] 4.4 Verify no remaining references to `_run_agentlet_core` in the codebase.

## 5. Migrate Standalone Callers to SessionPool

- [ ] 5.1 Migrate `agentpool_cli/serve_vercel.py`: replace `agent.run_stream()` with `session_pool.run_stream()`. Create session if needed.
- [ ] 5.2 Migrate `agentpool_server/openai_api_server/completions/helpers.py`: replace `agent.run_stream()` with `session_pool.run_stream()`.
- [ ] 5.3 **SKIP** `agentpool_server/agui_server/base_agent_adapter.py` — permanent protocol bypass pattern (documented in `docs/audit/agui-bypass-audit.md`).
- [ ] 5.4 **SKIP** `agentpool_toolsets/streaming_tools.py` — uses `message_history=fork_history` incompatible with `SessionPool.run_stream()`. Runs inside turn context (deadlock risk).
- [ ] 5.5 **SKIP** `agentpool_toolsets/fsspec_toolset/toolset.py` — same fork-history + turn-context deadlock pattern.
- [ ] 5.6 Formalize `agentpool_server/acp_server/session.py` SessionPool routing — already routes through SessionPool via `BaseAgent.run_stream()` delegation (lines 901-939). Verify the MCP provider lifecycle workaround (lines 634-647) still works correctly.

## 6. Update BaseAgent.run_stream Delegation

- [ ] 6.1 Remove `_execute_direct()` fallback from `BaseAgent.run_stream()` (lines 939+). The existing SessionPool delegation (lines 901-939) stays.
- [ ] 6.2 When `SessionPool` is unavailable (no `self.agent_pool.session_pool`), raise `RuntimeError` with a clear message indicating SessionPool is required.
- [ ] 6.3 Remove manual follow-up loop for native agents from `BaseAgent.run_stream()` (the `while injection_manager.has_queued()` loop, already gated by `AGENT_TYPE != "native"`, can be fully removed for native agents).

## 7. Testing

- [ ] 7.1 Run existing test suite: `uv run pytest -m "not slow"` and fix any regressions.
- [ ] 7.2 Verify `steer()`/`followup()` work correctly for native agents after migration (the original bug is fixed). Use `uv run pytest -k steer` or the steer/followup edge case tests.
- [ ] 7.3 Verify graph execution path (`_execute_node()` → `RunExecutor`) with `uv run pytest -k graph`. Specifically check that `RunStartedEvent` in the graph state's event queue doesn't break graph consumers.
- [ ] 7.4 Verify standalone caller migrations: test each migrated caller manually or with integration tests.
- [ ] 7.5 Run `uv run ruff check src/` and `uv run mypy src/` to verify no new lint/type errors.
- [ ] 7.6 Verify `_interrupt()` cancellation still works after `_stream_events()` stops managing `self._iteration_task`. Check that cancellation latency is acceptable (up to 100ms extra from 0.1s polling interval).
- [ ] 7.7 Verify `RunStartedEvent` with `session_id` and `parent_session_id` reaches protocol consumers correctly.
- [ ] 7.8 Verify known regressions: confirm streaming_tools and FSSpec toolset break after `_execute_direct()` removal (expected), and document the breakage clearly.

## 8. Cleanup

- [ ] 8.1 Gate `_consume_event_queue` background task in TurnRunner behind `agent_type != "native"` (core.py:1456). Non-native agents still need it for `merge_queue_into_iterator`.
- [ ] 8.2 Remove `_run_agentlet_core` references from comments and docstrings in `eventbus_hooks_adapter.py`.
- [ ] 8.3 Update AGENTS.md if it references `_run_agentlet_core` or the dual-path architecture.
- [ ] 8.4 **DO NOT** remove `merge_queue_into_iterator` from `agentpool/utils/streams.py` — ACP agent at `acp_agent.py:490` still uses it. Only the native agent import is removed.
