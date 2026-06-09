## 1. Design and Implement ProtocolEventConsumerMixin

- [x] 1.1 Design mixin interface with abstract hooks (`_handle_event`, `_handle_spawn_session_start`, `_get_subscription_scope`)
- [x] 1.2 Write TDD tests for mixin (`tests/servers/test_subagent_event_mixin.py`)
- [x] 1.3 Implement mixin lifecycle methods (`start_event_consumer`, `stop_event_consumer`, `_event_consumer_loop`)
- [x] 1.4 Verify mixin tests pass (7 tests, GREEN phase)
- [x] 1.5 Verify ruff and mypy pass on `src/agentpool_server/mixins.py`

## 2. Refactor OpenCode Server

- [x] 2.1 Refactor `OpenCodeProtocolHandler` to inherit from `ProtocolEventConsumerMixin`
- [x] 2.2 Move event conversion to `_handle_event()`
- [x] 2.3 Move `SpawnSessionStart` handling to `_handle_spawn_session_start()`
- [x] 2.4 Remove duplicated consumer loop and cleanup code
- [x] 2.5 Write OpenCode integration tests (`tests/servers/opencode_server/test_subagent_events.py`)
- [x] 2.6 Verify all existing OpenCode tests pass (backward compatibility)

## 3. Fix and Refactor ACP Server

- [x] 3.1 Fix ACP raw child event handling (route by `event.session_id`, create per-child converters)
- [x] 3.2 Refactor `ACPProtocolHandler` to inherit from `ProtocolEventConsumerMixin`
- [x] 3.3 Move event conversion to `_handle_event()` with per-session converter cache
- [x] 3.4 Move `SpawnSessionStart` handling to `_handle_spawn_session_start()`
- [x] 3.5 Remove duplicated consumer loop and cleanup code
- [x] 3.6 Preserve canary flag logic (`_should_use_session_pool`)
- [x] 3.7 Write ACP integration tests (`tests/servers/acp_server/test_subagent_events.py`)
- [x] 3.8 Verify all existing ACP tests pass (backward compatibility)

## 4. Cross-Cutting Verification

- [x] 4.1 Run full test suite (`uv run pytest`) — all tests pass
- [x] 4.2 Run lint (`uv run ruff check src/`) — no errors
- [x] 4.3 Run type check (`uv run mypy src/`) — no errors
- [x] 4.4 Verify no leaked EventBus subscriptions in tests

## 5. Documentation

- [x] 5.1 Create `openspec/changes/auto-subscribe-subagent-events/design.md`
- [x] 5.2 Create `openspec/changes/auto-subscribe-subagent-events/specs/auto-subscribe-subagent-events/spec.md`
- [x] 5.3 Create `openspec/changes/auto-subscribe-subagent-events/tasks.md`
- [x] 5.4 Verify mixin docstrings are complete (`src/agentpool_server/mixins.py`)
- [x] 5.5 Verify no stale references to "SubAgentEvent wrapping" in openspec/

## 6. Future Work (Out of Scope for This Change)

- [ ] 6.1 Adopt `ProtocolEventConsumerMixin` in AG-UI handler
- [ ] 6.2 Adopt `ProtocolEventConsumerMixin` in OpenAI API handler
- [ ] 6.3 BackgroundTaskProvider simplification (parent repo `../xeno-agent`)
