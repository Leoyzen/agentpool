## MODIFIED Requirements

### Requirement: SessionController receives and routes all requests with agent-type awareness
The system SHALL route all session-bound requests through `SessionController.receive_request()`. `receive_request()` SHALL be fire-and-forget, returning `None`. Protocol handlers SHALL continue consuming events via `EventBus` subscription before calling `receive_request()`. `receive_request()` SHALL inspect the session's agent type and route accordingly:
- **Native agents**: acquire `SessionState._request_lock`, then check `SessionState.current_run_id`. If idle, create a `RunHandle` with PydanticAI `AgentRun` and start execution via `RunExecutor` — which is now the SOLE run execution path. The legacy `BaseAgent.run_stream()` standalone path SHALL NOT be used. If active, call `TurnRunner.steer()` or `TurnRunner.followup()` based on priority.
- **Non-native agents**: delegate to `TurnRunner` which preserves manual queue system for non-PydanticAI agents.

The `RunExecutor` SHALL call `agent_run.next(node)` explicitly (not bare `async for`) so that pdai Capability hooks fire on all run paths. The `BaseAgent.run_stream()` standalone Path B (producer/consumer pattern) SHALL be removed.

#### Scenario: RunExecutor is sole execution path for native agents
- **WHEN** a native agent runs via `SessionPool` or standalone
- **THEN** execution SHALL go through `RunExecutor` which calls `agent_run.next(node)` explicitly

#### Scenario: Capability hooks fire on all native agent runs
- **WHEN** a native agent with pdai Capabilities runs via any path (standalone, session pool, subagent)
- **THEN** all Capability hooks SHALL fire (wrap_node_run, before_model_request, after_node_run)

#### Scenario: BaseAgent.run_stream standalone path removed
- **WHEN** `BaseAgent.run_stream()` is called
- **THEN** it SHALL delegate to `RunExecutor` and SHALL NOT use a producer/consumer pattern with `asyncio.ensure_future`
