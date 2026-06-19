## MODIFIED Requirements

### Requirement: All protocol sessions SHALL be managed by SessionPool
The system SHALL ensure that session creation, teardown, and lifecycle management for all protocols are handled exclusively through SessionPool. Protocol handlers SHALL NOT create or close sessions through legacy direct agent methods when SessionPool is available. OpenCode server SHALL NOT use `getattr(state, "session_status", None)` as a fallback for session status when `SessionStatusBridge` is available.

#### Scenario: OpenCode session close through SessionPool
- **WHEN** an OpenCode client closes a session
- **THEN** the OpenCode protocol handler invokes `SessionPool.close_session()`
- **AND** the handler does NOT fall back to direct `state.sessions.pop(session_id)` or other in-memory cleanup

#### Scenario: OpenCode session operations through SessionPool
- **WHEN** an OpenCode session is created, initialized, or closed
- **THEN** the OpenCode protocol handler uses SessionPool APIs for lifecycle management
- **AND** the handler does NOT use direct agent session methods bypassing SessionPool

#### Scenario: OpenCode message storage uses SessionPool as authoritative source
- **WHEN** the OpenCode server retrieves messages for a session
- **THEN** it uses SessionPool's message API (`get_messages`) as the authoritative source
- **AND** `state.messages` is retained as a streaming buffer for subagent ToolPart updates and checkpoint restoration, but is NOT used as an authoritative fallback for message retrieval
- **AND** the subagent streaming fast-path (in-memory `state.messages` for live ToolPart updates during streaming) continues to function

#### Scenario: OpenCode session status uses SessionPool exclusively
- **WHEN** the OpenCode server reads or updates session status (busy/idle)
- **THEN** it uses `OpenCodeSessionPoolIntegration.get_session_status()` and `SessionStatusBridge` exclusively
- **AND** the `getattr(state, "session_status", None)` fallback pattern is removed
- **AND** no dynamic attribute injection of `session_status` on `ServerState` is used
