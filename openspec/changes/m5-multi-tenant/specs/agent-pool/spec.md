## MODIFIED Requirements

### Requirement: AgentPool accepts optional tenant_id parameter

AgentPool SHALL accept an optional `tenant_id` parameter in its context manager constructor. When provided, all operations (agent lookup, team compilation, session management) SHALL be scoped to that tenant. When omitted, `tenant_id` SHALL default to `"default"`, preserving pre-M5 single-tenant behavior.

#### Scenario: AgentPool with explicit tenant_id

- **WHEN** `async with AgentPool("config.yml", tenant_id="tenant-1") as pool:` is used
- **THEN** all agents and teams within the pool SHALL be scoped to tenant-1
- **AND** the pool's Host SHALL have tenant_id="tenant-1"
- **AND** storage queries SHALL filter by tenant_id="tenant-1"

#### Scenario: AgentPool without tenant_id defaults to "default"

- **WHEN** `async with AgentPool("config.yml") as pool:` is used (no tenant_id)
- **THEN** tenant_id SHALL be "default"
- **AND** behavior SHALL be identical to pre-M5 single-tenant mode
- **AND** no isolation overhead SHALL be imposed beyond the default tenant filter

### Requirement: AgentPool operations scoped to tenant when tenant_id provided

When `tenant_id` is provided to AgentPool, `get_agent()`, `get_team()`, and session creation SHALL only return resources belonging to that tenant. Agents compiled within the pool SHALL carry the tenant_id in their HostContext.

#### Scenario: get_agent returns tenant-scoped agent

- **WHEN** `pool.get_agent("coder")` is called on a pool with tenant_id="tenant-1"
- **THEN** the returned agent SHALL be compiled within tenant-1's Host
- **AND** the agent's HostContext SHALL have tenant_id="tenant-1"
- **AND** the agent's MCP tools SHALL only access tenant-1's MCP server processes

#### Scenario: get_agent on wrong tenant raises error

- **WHEN** `pool.get_agent("coder")` is called on a pool with tenant_id="tenant-1"
- **AND** the agent was previously compiled for tenant-2
- **THEN** a new agent SHALL be compiled for tenant-1
- **AND** no cross-tenant agent sharing SHALL occur

### Requirement: AgentPool multi-tenant mode via ConfigRegistry

When AgentPool is constructed from a ConfigRegistry (introduced in M4), it SHALL support runtime tenant switching. Each `pool.for_tenant(tenant_id)` call SHALL return a scoped view of the pool bound to that tenant, backed by the correct Host from HostRegistry.

#### Scenario: for_tenant returns scoped pool view

- **WHEN** `pool.for_tenant("tenant-2")` is called on a pool backed by ConfigRegistry
- **THEN** a scoped AgentPool view SHALL be returned with tenant_id="tenant-2"
- **AND** the view SHALL use the Host from HostRegistry for (config_id, "tenant-2")
- **AND** operations on the view SHALL be isolated from tenant-1

#### Scenario: for_tenant with unknown tenant creates Host lazily

- **WHEN** `pool.for_tenant("tenant-3")` is called and no Host exists for that tenant
- **THEN** HostRegistry SHALL lazily create a new Host for (config_id, "tenant-3")
- **AND** the scoped view SHALL use the newly created Host
- **AND** the Host SHALL have fresh infrastructure (MCP processes, storage, skills)
