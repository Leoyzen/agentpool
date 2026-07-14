## MODIFIED Requirements

### Requirement: AgentFactory.compile accepts AgentManifest not full AgentsManifest

AgentFactory.compile() SHALL accept an `AgentManifest` (containing only agent definitions: agents, teams, graph, responses) instead of the full `AgentsManifest`. Infrastructure configuration (mcp_servers, storage, observability, models) SHALL be accessed through `HostContext`, not through the manifest parameter. This separation enables targeted recompile when only agent definitions change.

#### Scenario: Compile from AgentManifest

- **WHEN** `factory.compile(agent_manifest, host_context)` is called where `agent_manifest` contains only agent/team/graph sections
- **THEN** an AgentRegistry SHALL be returned with all agents compiled from the manifest
- **AND** infrastructure handles (MCP, storage, skills) SHALL be sourced from `host_context`, not from the manifest

#### Scenario: Flat AgentsManifest auto-migrates to split

- **WHEN** a flat YAML config (no `host:` section) is loaded
- **THEN** a model validator SHALL split it into `HostConfig` (infrastructure fields) and `AgentManifest` (agent fields)
- **AND** `AgentFactory.compile()` SHALL receive the `AgentManifest` portion
- **AND** the `HostContext` SHALL be constructed from the `HostConfig` portion

### Requirement: AgentFactory resolves models through ModelCache and aliases

AgentFactory SHALL resolve agent model strings through the ModelCache and alias system before creating agent instances. When an agent's `model` field is an alias name, the factory SHALL resolve it to a concrete model string using the Host's alias definitions. The resolved model SHALL be fetched from (or created in) ModelCache.

#### Scenario: Alias resolution during compile

- **WHEN** `factory.compile(manifest, host_context)` encounters an agent with `model="smart"` and the Host defines alias `"smart"` → `"openai:gpt-4o"`
- **THEN** the factory SHALL resolve the alias to `"openai:gpt-4o"`
- **AND** SHALL fetch the `Model` instance from `host_context.model_cache`
- **AND** the agent SHALL be configured with the resolved `Model` instance

#### Scenario: Direct model string bypasses alias lookup

- **WHEN** an agent's `model` is `"openai:gpt-4o"` (not a registered alias)
- **THEN** the factory SHALL skip alias resolution
- **AND** SHALL fetch the `Model` instance directly from ModelCache

#### Scenario: ModelCache is shared across all agents in a compile

- **WHEN** two agents in the same manifest both resolve to `"openai:gpt-4o"`
- **THEN** both agents SHALL receive the same `Model` instance from ModelCache
- **AND** only one `Model` object SHALL be created for that model string

### Requirement: AgentFactory.recompile performs diff-based agent recreation

AgentFactory.recompile() SHALL accept a new `AgentManifest` and compare it with the previously compiled manifest. Only agents whose configuration has changed SHALL be recreated. Unchanged agents SHALL be preserved from the previous compilation. This enables fast hot-reload of agent definitions without touching infrastructure.

#### Scenario: Recompile with changed agent

- **WHEN** `factory.recompile(new_manifest, host_context)` is called and the "coder" agent's system prompt changed
- **THEN** only the "coder" agent SHALL be recreated
- **AND** all other agents SHALL be preserved from the previous compilation
- **AND** the AgentRegistry SHALL reflect the updated "coder" agent

#### Scenario: Recompile with no changes

- **WHEN** `factory.recompile(same_manifest, host_context)` is called with a manifest identical to the current one
- **THEN** no agents SHALL be recreated
- **AND** the existing AgentRegistry SHALL be returned unchanged

#### Scenario: Recompile with added agent

- **WHEN** `factory.recompile(new_manifest, host_context)` is called and the new manifest adds a "reviewer" agent
- **THEN** the "reviewer" agent SHALL be created and added to the registry
- **AND** all existing agents SHALL be preserved unchanged
