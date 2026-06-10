## ADDED Requirements

### Requirement: Session-scoped resource providers
The system SHALL support attaching resource providers to a `SessionState` such that all agents executing within that session (or its child sessions) can discover and invoke tools from those providers.

#### Scenario: Lead agent sees session-level MCP providers
- **WHEN** an ACP session is created with `mcp_servers` configured
- **THEN** `initialize_mcp_servers()` creates `MCPResourceProvider` instances
- **AND** those providers are permanently attached to `ACPSession.agent.tools`
- **AND** the lead agent executing the session can list and invoke tools from those providers

#### Scenario: Subagent inherits parent's session-level MCP providers
- **WHEN** a subagent is spawned via `SubagentTools.task()` with a `parent_session_id`
- **AND** the parent session has `resource_providers` registered in its `SessionState`
- **THEN** the child session's `SessionState` SHALL inherit the parent's `resource_providers`
- **AND** the subagent's per-session agent SHALL have those providers attached to its `ToolManager`

#### Scenario: Per-session agent receives session providers
- **WHEN** `SessionController.get_or_create_session_agent()` creates a per-session agent for a native agent config
- **AND** the session has `resource_providers` registered
- **THEN** the newly created agent SHALL have all session providers added via `agent.tools.add_provider()` before being returned
- **AND** shared agents (non-native types) SHALL NOT receive permanent session provider attachment

#### Scenario: SessionState created eagerly for ACP sessions
- **WHEN** `ACPSessionManager.create_session()` creates an ACP session
- **THEN** a `SessionState` SHALL be eagerly created before `initialize_mcp_servers()` is called
- **AND** `initialize_mcp_servers()` SHALL register providers into the eager `SessionState`

### Requirement: Provider lifecycle tied to root session
The system SHALL ensure that session-scoped resource providers are not closed or cleaned up when a per-session agent exits. The provider lifecycle SHALL be tied to the root session, not the agent. Child sessions SHALL inherit provider references, not ownership.

#### Scenario: Per-session agent exit does not close session providers
- **WHEN** a per-session agent completes its turn and `__aexit__()` is called
- **AND** the agent has session-scoped providers attached
- **THEN** those providers SHALL NOT be closed or disconnected
- **AND** subsequent agents in the same session SHALL continue to use the same provider instances

#### Scenario: Root session cleanup closes all session providers
- **WHEN** the root ACP session is closed
- **THEN** all `resource_providers` in that session's `SessionState` SHALL be properly cleaned up
- **AND** any underlying MCP connections SHALL be disconnected
- **AND** child sessions SHALL NOT attempt to close inherited providers

### Requirement: Removal of temporary provider injection for per-session agents
The system SHALL no longer use the temporary add/remove pattern in `ACPSession.process_prompt()` for per-session agents. Session-level providers SHALL be permanently attached via `SessionState`. The lead agent (`ACPSession.agent`) SHALL continue to receive providers via permanent attachment from `ACPSession`.

#### Scenario: ACPSession run does not temporarily inject providers for per-session agents
- **WHEN** `ACPSession.process_prompt()` executes a prompt turn
- **THEN** it SHALL NOT call `agent.tools.add_provider()` for session MCP providers before the run
- **AND** it SHALL NOT call `agent.tools.remove_provider()` after the run
- **AND** per-session agents SHALL already have the providers via `SessionState` attachment

#### Scenario: Lead agent retains permanent provider attachment
- **WHEN** `ACPSession.initialize_mcp_servers()` creates session providers
- **THEN** those providers SHALL be permanently added to `ACPSession.agent.tools`
- **AND** the lead agent SHALL retain access across multiple turns without re-attachment

### Requirement: Backward compatibility with pool-level MCP
The system SHALL continue to support pool-level MCP providers (via `AgentPool.__aenter__()`). Session-scoped providers SHALL be additive — agents receive both pool-level and session-level providers.

#### Scenario: Agent receives both pool-level and session-level providers
- **WHEN** an agent is created in a session that has both pool-level MCP servers and session-level MCP-over-ACP providers
- **THEN** the agent SHALL have access to tools from both provider sets
- **AND** there SHALL be no duplicate tool registrations
- **AND** session-level tools SHALL shadow pool-level tools with the same name

### Requirement: Agent switching preserves session providers
The system SHALL ensure that when `ACPSession.switch_active_agent()` is called, the new agent has access to session-level MCP providers.

#### Scenario: Agent switch attaches providers to new lead agent
- **WHEN** `ACPSession.switch_active_agent()` switches to a native agent
- **THEN** session providers SHALL be permanently attached to the new lead agent via `agent.tools.add_provider()`
- **AND** session providers SHALL be removed from the old lead agent's `ToolManager`
- **AND** future per-session agents for this session SHALL continue to receive providers via `SessionState.resource_providers`

#### Scenario: Agent switch removes providers from old agent
- **WHEN** `ACPSession.switch_active_agent()` switches from a native agent to another agent
- **THEN** session providers SHALL be removed from the old agent's `ToolManager`
- **AND** the old agent SHALL no longer expose session-level MCP tools

#### Scenario: Agent switch to non-native agent does not attach session providers
- **WHEN** `ACPSession.switch_active_agent()` switches to a non-native agent (ACP, ClaudeCode)
- **THEN** session providers SHALL NOT be permanently attached
- **AND** only pool-level MCP providers SHALL be available

### Requirement: Idempotent provider registration
The system SHALL prevent duplicate provider registration when `get_or_create_session_agent()` or `ACPSession.initialize_mcp_servers()` is called multiple times for the same session.

#### Scenario: Multiple get_or_create_session_agent() calls do not duplicate providers
- **WHEN** `get_or_create_session_agent()` is called multiple times for the same session
- **THEN** session providers SHALL only be attached once per agent
- **AND** duplicate `add_provider()` calls SHALL be silently ignored

#### Scenario: Multiple initialize_mcp_servers() calls are idempotent
- **WHEN** `ACPSession.initialize_mcp_servers()` is called twice
- **THEN** providers SHALL only be created and registered once
- **AND** duplicate providers SHALL not be added to `SessionState` or `ACPSession.agent.tools`

### Requirement: Session resumption restores MCP providers
The system SHALL ensure that resumed ACP sessions restore access to MCP-over-ACP providers.

#### Scenario: Resumed session re-initializes MCP providers
- **WHEN** an ACP session is resumed via `ACPSessionManager.resume_session()`
- **AND** the original session had `mcp_servers` configured
- **THEN** the resumed session SHALL re-initialize MCP servers
- **AND** the resumed session's `SessionState` SHALL contain the restored providers
