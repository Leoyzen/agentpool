## ADDED Requirements

### Requirement: SessionPool is the mandatory execution entry point
The system SHALL route all streaming agent execution through `SessionPool` when `AgentPool` is active. `BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` and emit a deprecation warning. `BaseAgent` SHALL NOT store `session_id`, `_active_run_ctx`, `_current_stream_task`, or `_event_queue` as instance state.

#### Scenario: Direct run_stream triggers deprecation
- **WHEN** a caller invokes `agent.run_stream()` on an agent that is part of an `AgentPool`
- **THEN** the system emits a `DeprecationWarning` and delegates execution to `SessionPool.run_stream()`

#### Scenario: Shared agent used across sessions
- **WHEN** a shared agent instance is used in two different sessions concurrently
- **THEN** neither session's `session_id` or `run_ctx` is stored on the agent instance
- **AND** both sessions execute independently without state corruption for the explicitly removed attributes

#### Scenario: Standalone agent keeps legacy path
- **WHEN** an agent is created without an `AgentPool`
- **AND** `agent.run_stream()` is called
- **THEN** the agent uses its legacy execution path without SessionPool integration
- **AND** no deprecation warning is emitted

### Requirement: AgentRunContext carries session identity and event routing
`AgentRunContext` SHALL expose `session_id: str | None` and `event_bus: Any | None` fields. `TurnRunner` SHALL populate these fields when creating `AgentRunContext`. `StreamEventEmitter._emit()` SHALL use `run_ctx.session_id` and `run_ctx.event_bus` for event routing instead of agent instance state.

#### Scenario: Tool event routing
- **WHEN** a tool calls `ctx.events.tool_call_progress()` during a SessionPool-managed turn
- **THEN** the emitted event carries the correct `session_id` from `run_ctx.session_id`
- **AND** the event is published to the `EventBus` instance referenced by `run_ctx.event_bus`

#### Scenario: Event emission without agent instance state
- **WHEN** `StreamEventEmitter._emit()` is invoked
- **THEN** it reads `session_id` from `run_ctx.session_id` and does NOT read `agent.session_id`
- **AND** it reads `event_bus` from `run_ctx.event_bus` before falling back to `StreamEventEmitter._event_bus`

#### Scenario: _stream_event_bus_set race removed
- **WHEN** a turn is in progress
- **AND** `StreamEventEmitter._event_bus` changes mid-turn
- **THEN** `StreamEventEmitter._emit()` continues to publish correctly using `run_ctx.event_bus`
- **AND** no `_stream_event_bus_set` flag is consulted
