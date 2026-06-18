## ADDED Requirements

### Requirement: RunExecutor is the sole native agent execution engine
The system SHALL use `RunExecutor.execute()` as the single implementation of the pydantic-ai `agentlet.iter()` → `agent_run.next(node)` iteration loop for native agents. `_run_agentlet_core()` SHALL be removed. Both `_stream_events()` (standalone/TurnRunner path) and `_execute_node()` (graph path) SHALL use `RunExecutor.execute()`.

#### Scenario: _stream_events uses RunExecutor
- **WHEN** `_stream_events()` is called to execute a native agent run
- **THEN** it creates a `RunExecutor` instance and iterates over `executor.execute()`
- **AND** it does NOT call `_run_agentlet_core()`

#### Scenario: _execute_node uses RunExecutor
- **WHEN** `_execute_node()` is called to execute a native agent as a graph step
- **THEN** it creates a `RunExecutor` instance and iterates over `executor.execute()`
- **AND** all events from `execute()` are forwarded to `state.event_queue`
- **AND** the final `StreamCompleteEvent.message` is returned as the step result

#### Scenario: RunExecutor manages active_agent_run
- **WHEN** `RunExecutor.execute()` enters the `agentlet.iter()` context manager
- **THEN** it sets `run_handle.active_agent_run = agent_run`
- **AND** when the context manager exits, it clears `run_handle.active_agent_run = None`

### Requirement: RunExecutor includes all features from _run_agentlet_core
`RunExecutor` SHALL include: (a) `emitted_tool_starts` deduplication to prevent duplicate `ToolCallStartEvent` emissions, (b) a fallback empty `ChatMessage` when the run is cancelled before producing a response, and (c) a warning when `self._iteration_task` is still active from a previous run.

#### Scenario: ToolCallStartEvent deduplication
- **WHEN** pydantic-ai emits both `FunctionToolCallEvent` and `PartStartEvent(part=BaseToolCallPart)` for the same tool call
- **THEN** only one `ToolCallStartEvent` is emitted per unique `tool_call_id`

#### Scenario: Cancelled run produces fallback response
- **WHEN** a native agent run is cancelled before the model produces any response
- **THEN** `RunExecutor` yields a `StreamCompleteEvent` with an empty `ChatMessage` (finish_reason="stop")
- **AND** it does NOT raise `RuntimeError`

#### Scenario: Concurrent run warning
- **WHEN** `RunExecutor.execute()` is called while a previous `_iteration_task` is still active
- **THEN** a warning is logged about concurrent runs on a shared agent instance

### Requirement: RunExecutor yields complete lifecycle events
`RunExecutor.execute()` SHALL yield `RunStartedEvent` at the start of execution and `StreamCompleteEvent` at the end. The caller SHALL NOT need to emit these events separately.

#### Scenario: RunStartedEvent emitted
- **WHEN** `RunExecutor.execute()` begins
- **THEN** it yields a `RunStartedEvent` with `run_id`, `agent_name`, and `session_id`

#### Scenario: StreamCompleteEvent emitted
- **WHEN** the pydantic-ai agent run completes successfully
- **THEN** `RunExecutor` yields a `StreamCompleteEvent` with the final `ChatMessage`

## REMOVED Requirements

### Requirement: _run_agentlet_core as shared execution core
**Reason**: `_run_agentlet_core()` is replaced by `RunExecutor.execute()` as the single native agent execution engine. The bifurcation between `_run_agentlet_core()` and `RunExecutor` caused the `active_agent_run` bug (steer/followup messages undelivered for native agents).

**Migration**: All callers of `_run_agentlet_core()` (`_stream_events()` and `_execute_node()`) use `RunExecutor.execute()` instead. `_run_agentlet_core()` is deleted from `native_agent/agent.py`.
