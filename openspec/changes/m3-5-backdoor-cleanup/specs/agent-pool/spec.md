## MODIFIED Requirements

### Requirement: AgentPool as Registry and SkillService implementor

AgentPool SHALL be a `BaseRegistry[NodeName, MessageNode]` that manages lifecycle of all agents and teams. AgentPool SHALL conform to the `SkillService` Protocol via duck-typing — its existing methods (`skill_capabilities`, `skill_provider`, `skill_commands`, `is_skill_visible_to_node`, `get_skill_instructions_for_node`) match the Protocol exactly. `AgentPool.get_context()` SHALL pass `skill_service=self` and `main_agent_name=self.main_agent_name` when constructing HostContext.

#### Scenario: AgentPool constructs HostContext with skill_service

- **WHEN** `pool.get_context()` is called
- **THEN** the returned HostContext SHALL have `skill_service` set to the pool itself
- **AND** `main_agent_name` SHALL be set to `pool.main_agent_name`

#### Scenario: AgentPool conforms to SkillService Protocol

- **WHEN** `isinstance(pool, SkillService)` is checked
- **THEN** the result SHALL be `True`
- **AND** `pool.skill_capabilities`, `pool.skill_provider`, `pool.skill_commands` SHALL return valid values
- **AND** `pool.is_skill_visible_to_node(skill, node_name)` SHALL return a bool
- **AND** `await pool.get_skill_instructions_for_node(skill_name, node_name)` SHALL return a str

#### Scenario: AgentPool no longer passed to protocol server constructors

- **WHEN** `ACPProtocolHandler` is constructed
- **THEN** it SHALL receive a `HostContext` instead of an `AgentPool`
- **AND** `ACPProtocolHandler` SHALL access `session_pool` and `event_bus` via `host_context`
