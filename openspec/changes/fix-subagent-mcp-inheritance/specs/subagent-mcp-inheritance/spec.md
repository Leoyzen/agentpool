## ADDED Requirements

### Requirement: Child sessions inherit parent session-level MCP providers via agent sharing
The system SHALL make parent session's dynamically-added MCP resource providers available to child sessions (subagents) by returning the parent's per-session agent from `get_or_create_session_agent()`, without creating new agents or mutating the shared pool-level `base_agent`.

#### Scenario: Subagent inherits parent's workspace-fs MCP tools via agent sharing
- **GIVEN** a parent session has an MCP-over-ACP provider (e.g., workspace-fs) added to its per-session agent via `session_agent.tools.add_provider()`
- **AND** the parent spawns a child session (subagent) via `create_child_session(parent_session_id=...)`
- **WHEN** `get_or_create_session_agent(child_session_id)` is called
- **THEN** the child session's agent is the same object as the parent's per-session agent (`is` identity)
- **AND** the child session's agent has the parent's session-level MCP providers in `tools.external_providers`
- **AND** the shared pool-level `base_agent.tools.external_providers` is NOT mutated

#### Scenario: Pool-level MCP providers remain available to child sessions
- **GIVEN** a pool has MCP providers from YAML `mcp_servers` on `base_agent.tools`
- **WHEN** `get_or_create_session_agent(child_session_id)` is called for a child session
- **THEN** the child session's agent has all pool-level MCP providers (via the parent's per-session agent which also has them)

#### Scenario: Child session falls back to base_agent when parent has no per-session agent
- **GIVEN** a parent session uses the shared `base_agent` (e.g., MCP limit reached or non-native config)
- **AND** the parent spawns a child session
- **WHEN** `get_or_create_session_agent(child_session_id)` is called
- **THEN** the child session's agent is the shared `base_agent`
- **AND** behavior matches current (no regression)

#### Scenario: Child session cleanup does not close parent's agent
- **GIVEN** a child session shares the parent's per-session agent
- **WHEN** `close_session(child_session_id)` is called
- **THEN** the child's `is_per_session_agent` is `False`
- **AND** `agent.__aexit__()` is NOT called for the shared agent
- **AND** the parent session can continue using its agent
