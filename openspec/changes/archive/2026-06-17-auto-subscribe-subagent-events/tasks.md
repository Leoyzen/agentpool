## 1. Design and Implement ProtocolEventConsumerMixin

- [x] 1.1 Design mixin interface with abstract hooks (`_handle_event`, `_on_spawn_session_start`, `_before_consumer_loop`, `_after_consumer_loop`, `_get_subscription_scope`)
- [x] 1.2 Add `ConsumerShutdown` exception to `src/agentpool_server/mixins.py`
- [x] 1.3 Implement mixin class with `__init__`, `_consumer_tasks`, `_consumer_queues`, `_consumer_locks`, `_consumer_lock_creation_lock`
- [x] 1.4 Write TDD tests for mixin (`tests/servers/test_subagent_event_mixin.py`):
  - test_start_consumer_subscribes_and_runs_loop
  - test_start_consumer_is_idempotent
  - test_start_consumer_is_threadsafe
  - test_stop_consumer_cancels_task_and_unsubscribes
  - test_stop_consumer_is_safe_when_not_running
  - test_handle_event_dispatches_to_subclass
  - test_consumer_shutdown_gracefully_stops_loop
  - test_unhandled_exception_unsubscribes_in_finally
  - test_cancelled_error_reraised_after_cleanup
  - test_none_sentinel_stops_loop
  - test_spawn_session_start_calls_hook
  - test_before_after_hooks_called_in_order
- [x] 1.5 Verify mixin tests pass (target: 12 tests)
- [x] 1.6 Verify ruff and mypy pass on `src/agentpool_server/mixins.py`

## 2. Refactor ACP Server to Use Mixin

- [x] 2.1 Fix `SpawnSessionStart` handling in `ACPEventConverter` (replace `...` placeholder)
- [x] 2.2 Refactor `ACPProtocolHandler` to inherit from `ProtocolEventConsumerMixin`
- [x] 2.3 Implement `_before_consumer_loop()` — create per-session `ACPEventConverter`, store in `self._converters[session_id]`
- [x] 2.4 Implement `_handle_event()` — retrieve converter from `self._converters`, convert event, emit `session/update`; catch connection errors and raise `ConsumerShutdown`
- [x] 2.5 Implement `_on_spawn_session_start()` — no-op (ACP does not create child consumers)
- [x] 2.6 Implement `_after_consumer_loop()` — remove converter from `self._converters`
- [x] 2.7 Remove duplicated consumer loop and cleanup code from `handler.py`
- [x] 2.8 Preserve canary flag logic (`_should_use_session_pool`) — gate mixin usage
- [x] 2.9 Write ACP subagent event integration tests (`tests/servers/acp_server/test_subagent_events.py`):
  - test_acp_handler_converts_spawn_session_start
  - test_acp_handler_converts_part_delta
  - test_acp_handler_converts_tool_call
  - test_acp_handler_converts_stream_complete
  - test_acp_handler_converts_run_error
  - test_acp_handler_connection_error_stops_consumer
  - test_acp_handler_converter_isolated_per_session
  - test_acp_handler_no_child_consumers_created
- [x] 2.10 Verify all existing ACP tests pass (backward compatibility, 179+ passed)

## 3. Cross-Cutting Verification

- [x] 3.1 Run ACP test suite (`uv run pytest tests/servers/acp_server/`)
- [x] 3.2 Run mixin tests (`uv run pytest tests/servers/test_subagent_event_mixin.py`)
- [x] 3.3 Run lint (`uv run ruff check src/`)
- [x] 3.4 Run type check (`uv run mypy src/`)
- [x] 3.5 Verify no leaked EventBus subscriptions in tests (custom assertion: subscriber count before == after)
- [x] 3.6 Verify no leaked asyncio tasks in tests (custom assertion: `_consumer_tasks` empty after `stop_event_consumer`)

## 4. Documentation

- [x] 4.1 Update `openspec/changes/auto-subscribe-subagent-events/proposal.md`
- [x] 4.2 Update `openspec/changes/auto-subscribe-subagent-events/design.md`
- [x] 4.3 Update `openspec/changes/auto-subscribe-subagent-events/tasks.md`
- [x] 4.4 Update `openspec/changes/auto-subscribe-subagent-events/specs/auto-subscribe-subagent-events/spec.md`
- [x] 4.5 Add mixin docstrings to `src/agentpool_server/mixins.py`
- [x] 4.6 Add architecture note to `AGENTS.md` about ProtocolEventConsumerMixin

## 5. Future Work (Out of Scope for This Change)

- [x] 5.1 Adopt `ProtocolEventConsumerMixin` in OpenCode handler (`session_pool_integration.py`) — implemented at line 604
- [x] 5.2 Adopt `ProtocolEventConsumerMixin` in AG-UI handler — implemented at line 35
- [x] 5.3 Adopt `ProtocolEventConsumerMixin` in OpenAI API handler — implemented at line 36
- [~] 5.4 BackgroundTaskProvider simplification (parent repo `../xeno-agent`) — out of scope
