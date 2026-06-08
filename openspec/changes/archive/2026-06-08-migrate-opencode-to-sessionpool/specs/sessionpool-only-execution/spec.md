## MODIFIED Requirements

### Requirement: SessionPool is the mandatory execution entry point
The system SHALL route all streaming agent execution through `SessionPool` when `AgentPool` is active. `BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` and emit a deprecation warning. `BaseAgent` SHALL NOT store `session_id`, `_active_run_ctx`, `_current_stream_task`, or `_event_queue` as instance state. **OpenCode Server route handlers and internal commands SHALL be included in this mandate, with the exception of shell execution which remains a direct passthrough.**

#### Scenario: Direct run_stream triggers deprecation
- **WHEN** a caller invokes `agent.run_stream()` on an agent that is part of an `AgentPool`
- **THEN** the system emits a `DeprecationWarning` and delegates execution to `SessionPool.run_stream()`
- *(Note: This behavior is already implemented in `base_agent.py:884-889`; this scenario documents the existing contract.)*

#### Scenario: Shared agent used across sessions
- **WHEN** a shared agent instance is used in two different sessions concurrently
- **THEN** neither session's `session_id` or `run_ctx` is stored on the agent instance
- **AND** both sessions execute independently without state corruption for the explicitly removed attributes

#### Scenario: Model switching targets per-session agent
- **WHEN** a client requests a model switch for session `s1`
- **THEN** the route obtains the per-session agent via `SessionController.get_or_create_session_agent("s1")`
- **AND** calls `agent.set_model(requested_model)` on the per-session agent
- **AND** does NOT mutate the shared agent's model

#### Scenario: Shell execution bypasses SessionPool
- **WHEN** a client requests shell command execution
- **THEN** the route does NOT call `SessionPool.receive_request()`
- **AND** the command executes directly via `Env.execute_command()`
- **AND** the response is returned immediately

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

### Requirement: BaseAgent legacy bypass for OpenCode internal callers
**Reason**: The original plan proposed removing `_should_bypass_session_pool()` entirely. Review revealed this is a critical deadlock prevention mechanism for TurnRunner internal calls and AG-UI direct streaming. It must be preserved and replaced with a type-safe `ContextVar` mechanism.
**Migration**: Replace stack inspection in `_should_bypass_session_pool()` with a `ContextVar` flag set by TurnRunner before calling `agent._run_stream_once()`. AG-UI bypass is preserved until AG-UI server is audited for SessionPool compatibility.

### Requirement: Shell execution routes through SessionPool tool framework
**Reason**: Routing shell commands through the agent tool framework changes product semantics -- shell commands become LLM-mediated operations with latency and potential refusal. Users expect immediate deterministic execution.
**Migration**: Shell commands continue as direct passthroughs using a standalone `Env`/`ProcessManager`. Remove dependency on `state.agent.env` but preserve immediate execution semantics.
