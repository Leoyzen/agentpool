## ADDED Requirements

### Requirement: AgentFactory is a standalone compilation service

AgentFactory SHALL be a class that compiles an `AgentsManifest` and `HostContext` into an `AgentRegistry`. It SHALL NOT own infrastructure (MCP processes, storage connections). It SHALL maintain an internal compilation cache for diff-based recompile efficiency.

#### Scenario: Compile agents from manifest

- **WHEN** `factory.compile(manifest, host_context)` is called with a valid manifest and host context
- **THEN** an AgentRegistry SHALL be returned containing all agents, teams, and graphs defined in the manifest
- **AND** each agent SHALL have access to the infrastructure handles from host_context

#### Scenario: AgentFactory does not own infrastructure

- **WHEN** AgentFactory is constructed
- **THEN** it SHALL NOT start MCP server processes, open database connections, or create storage managers
- **AND** it SHALL only transform config + dependency handles into agent instances

### Requirement: AgentFactory.compile accepts method parameters

AgentFactory.compile() SHALL accept `manifest: AgentsManifest` and `host_context: HostContext` as method parameters, not constructor arguments. This allows the same factory instance to recompile with a new manifest while preserving the compilation cache.

#### Scenario: Recompile with new manifest

- **WHEN** `factory.recompile(new_manifest, same_host_context)` is called
- **THEN** only agents whose config section changed SHALL be recreated
- **AND** unchanged agents SHALL be preserved from the previous compilation

### Requirement: AgentRegistry provides name-based lookup

AgentRegistry SHALL provide typed lookup by agent name. It SHALL be a wrapper around `dict[str, MessageNode]` with `get(name)`, `list_names()`, and `exists(name)` methods.

#### Scenario: Get agent by name

- **WHEN** `registry.get("coder")` is called
- **THEN** the MessageNode for the "coder" agent SHALL be returned
- **AND** the returned node SHALL be the same instance across multiple calls (not recreated)

#### Scenario: Get non-existent agent

- **WHEN** `registry.get("nonexistent")` is called
- **THEN** a KeyError or return of None SHALL indicate the agent does not exist
- **AND** `registry.exists("nonexistent")` SHALL return False

### Requirement: AgentFactory preserves existing agent creation behavior

AgentFactory SHALL produce agents with identical behavior to the current AgentPool agent creation. This includes: model resolution, tool/capability injection, team compilation, connection setup, and skill loading.

#### Scenario: Agent created by Factory behaves identically

- **WHEN** an agent is created via `factory.compile(manifest, host_context)` and then `agent.run("prompt")` is called
- **THEN** the result SHALL be identical to creating the same agent via `pool.get_agent("name")` and calling `agent.run("prompt")`
- **AND** all tools, capabilities, system prompts, and model configuration SHALL match

### Requirement: AgentFactory is the sole place where config maps to runtime

AgentFactory SHALL be the only component that transforms config models into runtime agent instances. No other class SHALL directly instantiate agents from config.

#### Scenario: Agent creation goes through Factory

- **WHEN** AgentPool.get_agent() is called
- **THEN** it SHALL delegate to AgentFactory for agent creation
- **AND** AgentPool SHALL NOT contain agent instantiation logic itself
