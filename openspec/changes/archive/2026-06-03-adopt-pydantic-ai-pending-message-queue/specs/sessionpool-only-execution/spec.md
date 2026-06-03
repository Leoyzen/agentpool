## MODIFIED Requirements

### Requirement: SessionPool is the mandatory execution entry point
The system SHALL route all streaming agent execution through `SessionPool` when `AgentPool` is active. `BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` and emit a deprecation warning. `BaseAgent` SHALL NOT store `session_id`, `_active_run_ctx`, `_current_stream_task`, or `_event_queue` as instance state.

#### Scenario: Direct run_stream triggers deprecation
- **WHEN** a caller invokes `agent.run_stream()` on an agent that is part of an `AgentPool`
- **THEN** the system emits a `DeprecationWarning` and delegates execution to `SessionPool.run_stream()`

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

## REMOVED Requirements

### Requirement: TurnLock serialization
**Reason**: The per-session `turn_lock` was used to guard the manual queue system (`_post_turn_injections`, `_post_turn_prompts`). With PydanticAI's `PendingMessageDrainCapability` handling queueing internally for native agents, turn execution needs no explicit lock during execution. However, the check-and-create sequence in `receive_request()` requires mutual exclusion; this is provided by `SessionState._request_lock` (per-session lock, not global).
**Migration**: Concurrency control for run creation is handled by per-session `_request_lock`. Run execution serialization is implicit in PydanticAI's agent loop for native agents. Non-native agents continue using `LegacyTurnRunner` which retains its own concurrency model.

### Requirement: InjectionManager mid-turn injection (native agents only)
**Reason**: For native agents, replaced by PydanticAI's native `ctx.enqueue_message(..., priority='asap')`. Non-native agents retain `injection_manager`.
**Migration**: Native-agent tools previously using `run_ctx.injection_manager.inject()` shall use PydanticAI's `ctx.enqueue_message()` instead. Non-native agents continue using `injection_manager`. Protocol handlers previously calling `TurnRunner.inject_prompt()` shall use `SessionController.receive_request()` with steering semantics for native agents.

### Requirement: BaseAgent internal prompt continuation loop (native agents only)
**Reason**: `BaseAgent._run_stream_once()` contains a `while True` loop that processes queued prompts from the run context after each stream completes. For native agents, this loop duplicates PydanticAI's `PendingMessageDrainCapability` behavior and conflicts with it. Non-native agents retain this loop as it is their only continuation mechanism.
**Migration**: Remove the internal loop from `_run_stream_once()` for native agents. PydanticAI handles continuation via `PendingMessageDrainCapability` at `before_model_request` and `after_node_run`. Non-native agents keep the loop.
