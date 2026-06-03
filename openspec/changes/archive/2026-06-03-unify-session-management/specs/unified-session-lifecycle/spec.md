## ADDED Requirements

### Requirement: SessionPool creates all sessions through a single API
The system SHALL provide `SessionPool.create_session()` as the unified entry point for creating both top-level and child sessions.

#### Scenario: Top-level session creation
- **WHEN** a protocol handler calls `session_pool.create_session(session_id="s1", agent_name="coder")`
- **THEN** a `SessionState` is created with `session_id="s1"`, `parent_session_id=None`, and stored in `SessionController`
- **AND** the session is returned to the caller

#### Scenario: Child session creation
- **WHEN** a tool calls `session_pool.create_session(parent_session_id="s1", agent_name="reviewer")`
- **THEN** a `SessionState` is created with a generated `session_id`, `parent_session_id="s1"`, and stored in `SessionController`
- **AND** the parent session's child index is updated to include the new child
- **AND** the child session ID is returned to the caller

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

### Requirement: BaseAgent accepts session_id from caller
The system SHALL allow `BaseAgent.run_stream()` to receive `session_id` from an external authority rather than generating it internally.

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
