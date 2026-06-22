## 1. Remove EventBusHooksAdapter (LOWEST RISK FIRST)

- [ ] 1.1 Remove `src/agentpool/agents/native_agent/eventbus_hooks_adapter.py` entirely
- [ ] 1.2 Remove all imports and usages of `EventBusHooksAdapter` from native agent creation (`src/agentpool/agents/native_agent/agent.py`)
- [ ] 1.3 Verify `RunStartedEvent` is still published correctly via `RunExecutor.execute()` after removal
- [ ] 1.4 Verify `ToolCallStartEvent`/`ToolCallCompleteEvent` flow correctly through `RunExecutor` without the adapter
- [ ] 1.5 Run `uv run pytest tests/agents/native_agent/ -x -m unit` — expect zero failures

## 2. Consolidate RunFailedEvent

- [ ] 2.1 Verify zero external consumers of `BaseAgent.RunFailedEvent` signal (`grep -r "run_failed" src/ --include="*.py" | grep -v base_agent`)
- [ ] 2.2 Remove `BaseAgent.RunFailedEvent` inner class definition (`src/agentpool/agents/base_agent.py:140`)
- [ ] 2.3 Remove `BaseAgent.run_failed` Signal emission at `base_agent.py:1142`
- [ ] 2.4 Run `uv run pytest tests/orchestrator/test_runhandle_checkpoint.py tests/orchestrator/test_turn_runner.py -x -k "run_failed"` — expect zero failures

## 3. Add `_skip_event_processing` to ProtocolEventConsumerMixin

- [ ] 3.1 Add `_skip_event_processing: bool = False` class variable to `ProtocolEventConsumerMixin` (`src/agentpool_server/mixins.py`)
- [ ] 3.2 In `_event_consumer_loop`, wrap `_handle_event()` call with `if not self._skip_event_processing:` guard
- [ ] 3.3 Set `_skip_event_processing = True` in AG-UI server (`src/agentpool_server/agui_server/server.py`)
- [ ] 3.4 Set `_skip_event_processing = True` in OpenAI API server (`src/agentpool_server/openai_api_server/server.py`)
- [ ] 3.5 Run `uv run pytest tests/servers/agui_server/ tests/servers/openai_api_server/ -x` — expect zero failures

## 4. Merge SessionStatusBridge into OpenCodeSessionPoolIntegration

- [ ] 4.1 Add `RunStartedEvent`, `StreamCompleteEvent`, `RunFailedEvent` handling to `OpenCodeSessionPoolIntegration._handle_event()` in `session_pool_integration.py`
- [ ] 4.2 Remove duplicate `RunStartedEvent → SessionStatusEvent(type="busy")` broadcast from the OpenCode event adapter (`event_processor.py` line ~185) to fix double-broadcast bug
- [ ] 4.3 Simplify `set_session_status()` in `session_pool_integration.py` to broadcast directly via `server_state.broadcast_event()` instead of going through `_status_bridges`
- [ ] 4.4 Remove `SessionStatusBridge` class from `src/agentpool_server/opencode_server/status_bridge.py`
- [ ] 4.5 Remove `_start_status_bridge`/`_stop_status_bridge` from `OpenCodeSessionPoolIntegration`
- [ ] 4.6 Remove all imports and usages of `SessionStatusBridge`
- [ ] 4.7 Run `uv run pytest tests/servers/opencode_server/test_status_bridge.py tests/servers/opencode_server/test_session_integration.py -x` — status bridge tests migrated, session integration passes

## 5. Remove ACP Non-SessionPool Fallback + Dead Legacy Path

- [ ] 5.1 In `src/agentpool_server/acp_server/session.py`, remove the `else` branch in `ACPSession.process_prompt()` that calls `agent.run_stream()` directly when `session_pool` is None
- [ ] 5.2 Make `session_pool` mandatory: raise a clear error if `session_pool` is not available
- [ ] 5.3 Remove dead legacy prompt path in `acp_agent.py` (lines 656-698) that calls `session.process_prompt()` when `_protocol_handler.handle_prompt()` returns `None`
- [ ] 5.4 Update ACP session tests that create `ACPSession` without `SessionPool` to use `SessionPool` fixtures
- [ ] 5.5 Run `uv run pytest tests/servers/acp_server/test_acp_session_process_prompt_turn_complete.py tests/servers/acp_server/test_skill_content_delivery.py tests/servers/acp_server/test_skill_command_staged_content.py -x` — expect zero failures after test fixture updates

## 6. Rename `_active` to `_acp_sessions` + Delegate Lifecycle

- [ ] 6.1 Rename `ACPSessionManager._active` to `_acp_sessions: dict[str, ACPSession]`
- [ ] 6.2 Remove `ACPSessionManager._lock` (SessionController._lock covers lifecycle operations)
- [ ] 6.3 Update `ACPSessionManager.get_session()` to first check `SessionController.get_session()` for lifecycle, then resolve from `_acp_sessions`
- [ ] 6.4 Update `ACPSessionManager.list_sessions()` to delegate to `SessionController.list_sessions()` for session IDs, resolving ACPSession objects from `_acp_sessions`
- [ ] 6.5 Update `acp_agent.py:558` (`first_session` for `list_sessions()`) to use new pattern
- [ ] 6.6 Update `acp_agent.py:1110-1111` (pool swap) to iterate `_acp_sessions` for ACPSession cleanup, delegate lifecycle to `SessionController`
- [ ] 6.7 Update all test files that access `manager._active` directly (~5 test files)
- [ ] 6.8 Run `uv run pytest tests/ -k acp` — expect zero failures

## 7. Final Verification

- [ ] 7.1 Run full test suite: `uv run pytest`
- [ ] 7.2 Run type checking: `uv run --no-group docs mypy src/`
- [ ] 7.3 Run linting: `uv run ruff check src/`
- [ ] 7.4 Verify ACP end-to-end: start ACP server with `agentpool serve-acp` and confirm prompt processing works
- [ ] 7.5 Verify OpenCode end-to-end: confirm session status updates work without `SessionStatusBridge`
