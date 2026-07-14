## MODIFIED Requirements

### Requirement: SessionController receives and routes all requests with agent-type awareness
The system SHALL route all session-bound requests through `SessionController.receive_request()`. `receive_request()` SHALL be fire-and-forget, returning `None`. Protocol handlers SHALL continue consuming events via `EventBus` subscription before calling `receive_request()`. `receive_request()` SHALL inspect the session's agent type and route accordingly:
- **Native agents (Phase 2)**: acquire `SessionState._request_lock`, then check `SessionState.current_run_id`. If idle, create a `RunHandle` with PydanticAI `AgentRun` and start execution via `RunExecutor`. If active, call `TurnRunner.steer()` or `TurnRunner.followup()` based on priority.
- **Non-native agents**: delegate to `TurnRunner` which preserves manual queue system for non-PydanticAI agents.

After the core split, `SessionController` SHALL reside in `agentpool.orchestrator.session_controller` (not `agentpool.orchestrator.core`). The public API and behavior SHALL remain unchanged. All existing imports from `agentpool.orchestrator.core` SHALL continue to work via re-exports.

#### Scenario: SessionController accessible from new module
- **WHEN** code imports `SessionController` from `agentpool.orchestrator.session_controller`
- **THEN** the import SHALL succeed and return the same class

#### Scenario: receive_request routes native agents via RunExecutor
- **WHEN** `receive_request()` is called for a session with a native agent and the session is idle
- **THEN** a `RunHandle` SHALL be created and execution SHALL start via `RunExecutor`

#### Scenario: receive_request is fire-and-forget
- **WHEN** `receive_request()` is called
- **THEN** it SHALL return `None` immediately, with execution happening asynchronously
