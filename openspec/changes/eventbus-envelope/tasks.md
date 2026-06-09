## 1. Core Infrastructure

- [x] 1.1 Define `EventEnvelope` dataclass with `source_session_id: str`, `event: Any`, and `__getattr__` forwarding
- [x] 1.2 Update `EventBus.publish()` to wrap events in `EventEnvelope` before storing and distributing
- [x] 1.3 Update `EventBus.subscribe()` type annotation to return `Queue[EventEnvelope]`
- [x] 1.4 Update `EventBus` replay buffer to store `EventEnvelope` instead of raw events
- [x] 1.5 Add unit tests for `EventEnvelope` attribute forwarding and precedence

## 2. Producer Cleanup

- [x] 2.1 Remove session_id injection from `TurnRunner._publish_event` (core.py)
- [x] 2.2 Remove session_id injection from `StreamEventEmitter._emit` (event_emitter.py)
- [x] 2.3 Remove `session_id` parameter from `RunExecutor` event constructors (run_executor.py)
- [x] 2.4 Remove `session_id` parameter from `process_tool_event` and `ToolCallCompleteEvent` construction (helpers.py)
- [x] 2.5 Update `AgentRunContext.event_queue` type to `Queue[EventEnvelope]` if applicable

## 3. Consumer Adaptation

- [x] 3.1 Update `ACPProtocolHandler._handle_event` to consume `EventEnvelope`, use `envelope.source_session_id` for routing
- [x] 3.2 Update `ProtocolEventConsumerMixin` in mixins.py to handle `EventEnvelope`
- [x] 3.3 Update OpenCode server consumers (message_routes.py, session_pool_integration.py, status_bridge.py, event_bridge.py)
- [x] 3.4 Update Claude Code Agent event consumer (claude_code_agent.py)
- [x] 3.5 Update ACP Agent event consumer (acp_agent.py)
- [x] 3.6 Update BaseAgent event subscription (base_agent.py)

## 4. Test Updates

- [x] 4.1 Update `test_turn_runner.py` to assert `EventEnvelope` received, not raw events
- [x] 4.2 Update `test_run_executor.py` to verify events don't carry session_id (producer doesn't set it)
- [x] 4.3 Update `test_integration_redflags.py` to work with `EventEnvelope`
- [x] 4.4 Update ACP handler tests to verify `source_session_id` routing
- [x] 4.5 Update subagent tests to verify child events carry correct `source_session_id`
- [x] 4.6 Add new integration test: child session events reach parent with correct `source_session_id`

## 5. Validation

- [x] 5.1 Run full test suite and fix regressions
- [x] 5.2 Verify type checker (mypy) passes with new `EventEnvelope` types
- [x] 5.3 Verify no remaining `setattr(event, "session_id"` or `hasattr(event, "session_id")` in codebase
- [x] 5.4 End-to-end test: ACP subagent events display under correct child session ID
