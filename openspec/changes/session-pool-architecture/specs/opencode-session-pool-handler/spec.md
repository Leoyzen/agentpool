## ADDED Requirements

### Requirement: OpenCodeProtocolHandler uses SessionPool for session management
The OpenCodeProtocolHandler SHALL delegate all session and turn management to SessionPool.

#### Scenario: Handle OpenCode message via SessionPool
- **WHEN** handle_message() is called with session_id and user prompt
- **THEN** the message is processed via session_pool.process_prompt() and events are consumed from EventBus

#### Scenario: Persistent SSE event consumer
- **WHEN** a session is first accessed
- **THEN** a persistent background task subscribes to EventBus and forwards events via SSE

#### Scenario: Event consumer survives between turns
- **WHEN** a turn completes and post-turn events arrive
- **THEN** the persistent consumer forwards them via SSE without requiring a new handle_message() call

#### Scenario: Session close cleanup
- **WHEN** close_session() is called
- **THEN** the event consumer is cancelled, EventBus subscription removed, and SessionPool session closed

### Requirement: OpenCode handler supports feature flag toggle
The OpenCode server SHALL support switching between old and new handler implementations via configuration.

#### Scenario: SessionPool disabled
- **WHEN** opencode.use_session_pool is false
- **THEN** the existing ServerState._session_agents handler is used

#### Scenario: SessionPool enabled
- **WHEN** opencode.use_session_pool is true
- **THEN** OpenCodeProtocolHandler is used instead of ServerState for session management

#### Scenario: Gradual rollout
- **WHEN** opencode.use_session_pool is enabled for a subset of sessions
- **THEN** only new sessions use OpenCodeProtocolHandler; existing sessions continue with old handler

### Requirement: OpenCode state.py coupling handled
The migration SHALL preserve non-session-related ServerState functionality.

#### Scenario: Session-independent state preserved
- **WHEN** OpenCodeProtocolHandler is used for session management
- **THEN** ServerState continues to manage skill bridge, todo callbacks, title generation, and other non-session state

#### Scenario: Ensure_session store-first behavior
- **WHEN** ensure_session() is called
- **THEN** session data is loaded from store before creating new session (preserving RFC-0028 behavior)

### Requirement: OpenCode event conversion preserved
The OpenCodeProtocolHandler SHALL maintain the same event conversion behavior as the existing handler.

#### Scenario: Event conversion
- **WHEN** an event is received from EventBus
- **THEN** it is converted to OpenCode format and sent via SSE

#### Scenario: File system operations
- **WHEN** file system events are received
- **THEN** they are handled via the existing fsspec integration
