## MODIFIED Requirements

### Requirement: SessionPool creates all sessions through a single API
The system SHALL provide `SessionPool.create_session()` as the unified entry point for creating both top-level and child sessions, which delegates to `SessionController.get_or_create_session()`. **OpenCode session creation (initial load, fork, new) SHALL use `SessionController.get_or_create_session()` exclusively.** `SessionController.get_or_create_session()` creates the session directly (it does not delegate back to `SessionPool.create_session()`; `SessionPool.create_session()` is a thin wrapper that calls `SessionController`).

#### Scenario: Top-level session creation
- **WHEN** a protocol handler calls `session_pool.create_session(session_id="s1", agent_name="coder")`
- **THEN** a `SessionState` is created with `session_id="s1"`, `parent_session_id=None`, and stored in `SessionController`
- **AND** the session is returned to the caller

#### Scenario: Child session creation
- **WHEN** a tool calls `session_pool.create_session(parent_session_id="s1", agent_name="reviewer")`
- **THEN** a `SessionState` is created with a generated `session_id`, `parent_session_id="s1"`, and stored in `SessionController`
- **AND** the parent session's child index is updated to include the new child
- **AND** the child session ID is returned to the caller

#### Scenario: OpenCode session initialization uses SessionPool
- **WHEN** an OpenCode client requests a new session or loads an existing session
- **THEN** the OpenCode route calls `SessionController.get_or_create_session()` (which creates sessions directly; `SessionPool.create_session()` delegates to it)
- **AND** `ServerState.sessions` is not used as the source of truth

### Requirement: SessionState tracks parent-child relationships
The system SHALL maintain parent-child relationship metadata in every `SessionState`.

#### Scenario: Parent session tracks children
- **WHEN** a child session is created with `parent_session_id="s1"`
- **THEN** `SessionController` maintains an index mapping `s1 -> [child_id1, child_id2, ...]`
- **AND** `session.get_children()` returns the list of child session IDs

#### Scenario: Child session references parent
- **WHEN** a child session with `session_id="s1.1"` is created
- **THEN** `session.parent_session_id` equals `"s1"`
- **AND** `session.get_parent()` returns the parent `SessionState` or `None`

### Requirement: SessionPool closes sessions with configurable cascade behavior
The system SHALL close sessions according to their `lifecycle_policy`.

#### Scenario: Cascade policy closes children with parent
- **GIVEN** session `s1` has `lifecycle_policy=cascade` and child `s1.1`
- **WHEN** `session_pool.close_session("s1")` is called
- **THEN** `s1.1` is also closed before `s1` is removed

#### Scenario: Independent policy preserves children
- **GIVEN** session `s1` has `lifecycle_policy=independent` and child `s1.1`
- **WHEN** `session_pool.close_session("s1")` is called
- **THEN** `s1.1` remains active and retains its own TTL

#### Scenario: Bound policy closes child immediately
- **GIVEN** session `s1` has `lifecycle_policy=bound` and child `s1.1`
- **WHEN** `session_pool.close_session("s1")` is called
- **THEN** `s1.1` is closed immediately (no TTL wait)

#### Scenario: OpenCode session delete delegates to SessionPool
- **WHEN** an OpenCode client deletes a session
- **THEN** the route calls `SessionPool.close_session()` with the session's configured lifecycle policy
- **AND** `ServerState` does not perform its own cleanup

### Requirement: BaseAgent accepts session_id from caller
The system SHALL allow `BaseAgent.run_stream()` to receive `session_id` from an external authority rather than generating it internally. *(Note: `session_id` is already an accepted parameter on `run_stream()`; this requirement documents the existing contract.)*

#### Scenario: SessionPool assigns session ID before run
- **GIVEN** a SessionPool has created session `s1` for agent `"coder"`
- **WHEN** `session_pool.process_prompt("s1", "hello")` is called
- **THEN** `BaseAgent.run_stream()` receives `session_id="s1"`
- **AND** does not generate a new session ID

#### Scenario: Standalone agent generates ephemeral session ID
- **GIVEN** a `BaseAgent` is used without an `AgentPool`
- **WHEN** `agent.run_stream("hello")` is called
- **THEN** an ephemeral session ID is generated internally
- **AND** no parent-child tracking or EventBus routing is attempted

## ADDED Requirements

### Requirement: OpenCode session CRUD delegates exclusively to SessionController
OpenCode Server session operations SHALL delegate to `SessionController` for all CRUD and lifecycle management. `ServerState` SHALL NOT maintain parallel `sessions` or agent resolution tracking.

### Requirement: SessionController exposes session listing API
The system SHALL provide `SessionController.list_sessions() -> list[SessionInfo]` as a public API for listing all active sessions, where `SessionInfo` is a DTO containing `session_id`, `status`, `created_at`, `last_activity`, and `message_count`. The API is added in Migration A Phase A1 (method declaration returning `list[SessionState]` as a stub) and updated to return `list[SessionInfo]` in Phase A7.

#### Scenario: Session list from SessionController
- **WHEN** an OpenCode client requests the list of sessions
- **THEN** the route queries `SessionController.list_sessions()` for all active sessions as `SessionInfo` DTOs
- **AND** does not read from `ServerState.sessions`

#### Scenario: Session status from SessionState
- **WHEN** an OpenCode client queries session status
- **THEN** the route reads status from `SessionState` via `SessionController`
- **AND** `ServerState.session_status` is not consulted

#### Scenario: No dual session tracking
- **GIVEN** an OpenCode session is created
- **THEN** only one `SessionState` exists in `SessionController`
- **AND** `ServerState` does not hold a parallel copy of session metadata

### Requirement: Permission handling uses OpenCodeInputProvider on SessionState
OpenCode permission and question handling SHALL use `OpenCodeInputProvider` (not `ACPInputProvider`) registered on `SessionState`.

#### Scenario: Permission provider on SessionState
- **WHEN** a permission request is created for an OpenCode session
- **THEN** an `OpenCodeInputProvider` is registered on the session's `SessionState` via `session.input_provider`
- **AND** the provider uses OpenCode protocol events (`PermissionRequestEvent`, `PermissionReplyEvent`)
- **AND** `ServerState.input_providers` is not used

#### Scenario: Question provider on SessionState
- **WHEN** a question is asked during an OpenCode session
- **THEN** the question is tracked via `OpenCodeInputProvider` on `SessionState` via `session.input_provider`
- **AND** the global question listing endpoint queries `SessionController` for pending questions across all sessions
- **AND** `ServerState.pending_questions` is not used

### Requirement: SessionController exposes per-session agent for lifecycle operations
The system SHALL provide two public APIs on `SessionController` for per-session agent access:
- `get_or_create_session_agent(session_id)` — creates the agent if missing (used for session loading, model switching, stream cleanup)
- `get_session_agent(session_id)` — returns existing agent or raises if not found (used for interrupt/abort)

#### Scenario: Obtain per-session agent for interrupt
- **WHEN** `SessionController.get_session_agent("s1")` is called
- **THEN** it returns the existing agent instance associated with session `s1`
- **AND** the caller can invoke `agent.interrupt()` on the returned instance
- **AND** if the agent does not exist, it raises `KeyError` (consistent with `SessionController` session lookup semantics)

#### Scenario: Abort session uses get_session_agent
- **WHEN** an OpenCode client aborts session `s1`
- **THEN** the route calls `SessionController.get_session_agent("s1")` to get the agent
- **AND** if the agent is a **native** agent, calls `agent.interrupt()` on it
- **AND** if the agent is a **non-native shared** agent, does NOT call `agent.interrupt()` (to avoid killing all sessions using that shared instance)
- **AND** calls `SessionPool.cancel_run(run_id)` to cancel the active RunHandle regardless of agent type
