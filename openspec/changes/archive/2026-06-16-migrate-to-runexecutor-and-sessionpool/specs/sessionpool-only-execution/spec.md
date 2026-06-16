## MODIFIED Requirements

### Requirement: SessionPool is the mandatory execution entry point
The system SHALL route all streaming agent execution through `SessionPool` when `AgentPool` is active. `BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` and emit a deprecation warning. `BaseAgent` SHALL NOT store `session_id`, `_active_run_ctx`, `_current_stream_task`, or `_event_queue` as instance state. All standalone `agent.run_stream()` callers SHALL be migrated to `SessionPool.run_stream()`.

#### Scenario: Direct run_stream triggers deprecation
- **WHEN** a caller invokes `agent.run_stream()` on an agent that is part of an `AgentPool`
- **THEN** the system emits a `DeprecationWarning` and delegates execution to `SessionPool.run_stream()`

#### Scenario: Unpooled agent run_stream raises error
- **WHEN** a caller invokes `agent.run_stream()` on an agent without a `SessionPool`
- **THEN** the system raises a `RuntimeError` indicating SessionPool is required

#### Scenario: Shared agent used across sessions
- **WHEN** a shared agent instance is used in two different sessions concurrently
- **THEN** neither session's `session_id` or `run_ctx` is stored on the agent instance
- **AND** both sessions execute independently without state corruption for the explicitly removed attributes

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

## ADDED Requirements

### Requirement: merge_queue_into_iterator removed from native agents
The system SHALL NOT use `merge_queue_into_iterator` in native agent execution. With all callers using SessionPool (where `run_ctx.event_bus` is always set), the `else` branch that merges `run_ctx.event_queue` into the pydantic-ai stream is unreachable and SHALL be removed.

#### Scenario: Native agent does not import merge_queue_into_iterator
- **WHEN** the native agent module is loaded
- **THEN** it does NOT import `merge_queue_into_iterator` from `agentpool.utils.streams`

#### Scenario: Native agent does not merge event_queue into stream
- **WHEN** a native agent executes through SessionPool
- **THEN** tool events are published directly to EventBus via `run_ctx.event_bus`
- **AND** `run_ctx.event_queue` is NOT merged into the pydantic-ai stream
