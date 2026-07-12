## ADDED Requirements

### Requirement: _inject_pool_providers SHALL inject skills_tools_provider for all agent paths

`_inject_pool_providers()` in `factory.py` SHALL inject `host_context.skills_tools_provider` into `agent._external_capabilities` for all agent creation paths, including child sessions and standalone execution. This ensures `load_skill` and `list_skills` tools are available regardless of how the agent was created.

#### Scenario: Child session agent receives skills_tools_provider
- **WHEN** `_inject_pool_providers(agent, host_context, pool, include_aggregating=True)` is called
- **AND** `host_context.skills_tools_provider` is not `None`
- **THEN** `agent._external_capabilities` SHALL include the `skills_tools_provider`

#### Scenario: Standalone agent receives skills_tools_provider
- **WHEN** an agent is created via `Agent.from_config()` without SessionPool
- **AND** `_inject_pool_providers()` is called
- **THEN** `load_skill` and `list_skills` SHALL be present in the agent's tool list
