## ADDED Requirements

### Requirement: ACPProtocolHandler uses SessionPool for session management
The ACPProtocolHandler SHALL delegate all session and turn management to SessionPool.

#### Scenario: Handle ACP prompt via SessionPool
- **WHEN** handle_prompt() is called with session_id and content blocks
- **THEN** the prompt is processed via session_pool.process_prompt() and events are consumed from EventBus

#### Scenario: Persistent event consumer
- **WHEN** a session is first accessed
- **THEN** a persistent background task subscribes to EventBus and forwards events to the ACP client

#### Scenario: Event consumer survives between turns
- **WHEN** a turn completes and post-turn events arrive
- **THEN** the persistent consumer forwards them to the ACP client without requiring a new handle_prompt() call

#### Scenario: Session close cleanup
- **WHEN** close_session() is called
- **THEN** the event consumer is cancelled, EventBus subscription removed, and SessionPool session closed

### Requirement: ACP handler supports feature flag toggle
The ACP server SHALL support switching between old and new handler implementations via configuration.

#### Scenario: SessionPool disabled
- **WHEN** acp.use_session_pool is false
- **THEN** the existing AgentPoolACPAgent handler is used

#### Scenario: SessionPool enabled
- **WHEN** acp.use_session_pool is true
- **THEN** ACPProtocolHandler is used instead of AgentPoolACPAgent for session management

#### Scenario: Gradual rollout
- **WHEN** acp.use_session_pool is enabled for a subset of sessions
- **THEN** only new sessions use ACPProtocolHandler; existing sessions continue with old handler

### Requirement: ACP event conversion preserved
The ACPProtocolHandler SHALL maintain the same event conversion behavior as the existing handler.

#### Scenario: Tool call events
- **WHEN** a tool call event is received from EventBus
- **THEN** it is converted to ACP format using ACPEventConverter

#### Scenario: Subagent display mode
- **WHEN** subagent events are received
- **THEN** they are displayed according to the configured subagent_display_mode
