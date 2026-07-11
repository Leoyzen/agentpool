## ADDED Requirements

### Requirement: AgentFactory SHALL compile agents from ExtensionRegistry capabilities

`AgentFactory.compile()` SHALL query `ExtensionRegistry.get_visible_capabilities(scope)` to discover `McpServerCap` and `SkillManagerCap` instances, instead of reading from `AgentPool` skill properties (`_skill_capabilities`, `_skill_provider`, etc.). The factory SHALL compile `McpServerCap` and `SkillManagerCap` instances as `AbstractCapability` instances for pydantic-ai.

#### Scenario: Compile agent with MCP and skill capabilities
- **WHEN** `AgentFactory.compile(agent_config)` is called
- **THEN** the factory SHALL query `ExtensionRegistry.get_visible_capabilities(scope)` for the agent's scope
- **AND** `McpServerCap` instances SHALL be included in the capability list
- **AND** `SkillManagerCap` instances SHALL be included in the capability list
- **AND** the factory SHALL NOT read `AgentPool._skill_capabilities` or `AgentPool._skill_provider`

#### Scenario: Compile agent without ExtensionRegistry
- **WHEN** `AgentFactory.compile(agent_config)` is called and no `ExtensionRegistry` is available
- **THEN** the factory SHALL construct `McpServerCap` and `SkillManagerCap` directly from agent config (intermediate Phase 1-3 behavior) with a `DeprecationWarning` recommending `ExtensionRegistry`
