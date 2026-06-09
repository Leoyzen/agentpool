## ADDED Requirements

### Requirement: InputProvider bound to SessionState
`SessionState` SHALL be the authoritative source for the `InputProvider` associated with a session. `BaseAgent` SHALL NOT store a per-instance `_input_provider` as the primary source. `AgentContext.get_input_provider()` SHALL read from `SessionState` first, falling back to the deprecated agent-level field only when no session is available.

#### Scenario: Per-session InputProvider isolation
- **WHEN** two sessions share the same agent instance
- **AND** each session has a distinct `OpenCodeInputProvider`
- **THEN** `AgentContext.get_input_provider()` for session A returns session A's provider
- **AND** `AgentContext.get_input_provider()` for session B returns session B's provider
- **AND** neither session's provider leaks to the other

#### Scenario: InputProvider read from SessionState
- **WHEN** `AgentContext.get_input_provider()` is called during a SessionPool-managed turn
- **THEN** it queries `SessionState.input_provider` for the current session
- **AND** returns that provider

#### Scenario: Standalone fallback
- **WHEN** `AgentContext.get_input_provider()` is called outside of SessionPool (standalone agent)
- **THEN** it falls back to `AgentContext.input_provider` (the deprecated agent-level field)
- **AND** no exception is raised

### Requirement: SessionController does not mutate shared agent
`SessionController.get_or_create_session_agent()` SHALL NOT set `base_agent._input_provider` on a shared agent instance. When creating a per-session native agent, the `input_provider` SHALL be passed to the agent constructor. When returning a shared non-native agent, the `input_provider` SHALL be stored on `SessionState` only.

#### Scenario: Shared non-native agent creation
- **WHEN** `get_or_create_session_agent()` returns a shared ACP agent
- **AND** an `input_provider` is provided
- **THEN** the shared agent's `_input_provider` field is NOT modified
- **AND** the provider is stored on the session's `SessionState`

#### Scenario: Per-session native agent creation
- **WHEN** `get_or_create_session_agent()` creates a new per-session native agent
- **AND** an `input_provider` is provided
- **THEN** the provider is passed to the agent constructor
- **AND** the agent stores it as its own `_input_provider`

### Requirement: TurnRunner passes InputProvider via context
`TurnRunner._run_turn_unlocked()` SHALL construct `AgentContext` using the `input_provider` from `SessionState`. The provider SHALL be passed through to the agent's turn execution so that tools can resolve confirmations and elicitations correctly.

#### Scenario: Tool confirmation during SessionPool turn
- **WHEN** a tool calls `ctx.handle_confirmation()` during a SessionPool-managed turn
- **THEN** the `InputProvider` used is the one bound to the session's `SessionState`
- **AND** the confirmation request is routed to the correct session's UI
