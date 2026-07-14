## ADDED Requirements

### Requirement: Per-tenant AgentHost isolation

Each `(config_id, tenant_id)` pair SHALL resolve to a distinct AgentHost instance. Two tenants sharing the same config_id SHALL NOT share MCP processes, storage connections, skill registries, or capability caches. The Host IS the tenant isolation boundary.

#### Scenario: Two tenants with same config get different Hosts

- **WHEN** tenant-1 and tenant-2 both request a Host for config_id="default"
- **THEN** HostRegistry SHALL return two distinct AgentHost instances
- **AND** tenant-1's Host SHALL have its own MCP server processes separate from tenant-2's
- **AND** tenant-1's Host SHALL have its own StorageProvider connection separate from tenant-2's
- **AND** tenant-1's Host SHALL have its own SkillsRegistry separate from tenant-2's

#### Scenario: Single-tenant default uses one Host

- **WHEN** no tenant_id is provided and config_id="default"
- **THEN** HostRegistry SHALL return a single AgentHost with tenant_id="default"
- **AND** behavior SHALL be identical to pre-M5 single-tenant mode

### Requirement: MCP process separation per tenant

Each AgentHost SHALL spawn its own MCP server processes. MCP server processes belonging to tenant-1 SHALL NOT be accessible to tenant-2's agents. MCP tool calls from tenant-1 SHALL only interact with tenant-1's MCP server processes.

#### Scenario: MCP filesystem isolation

- **WHEN** tenant-1's config specifies an MCP filesystem server scoped to `/data/tenant-1/`
- **AND** tenant-2's config specifies an MCP filesystem server scoped to `/data/tenant-2/`
- **THEN** tenant-1's agent calling a filesystem tool SHALL only access `/data/tenant-1/`
- **AND** tenant-2's agent calling a filesystem tool SHALL only access `/data/tenant-2/`
- **AND** tenant-1's agent SHALL NOT be able to list or read files in `/data/tenant-2/`

#### Scenario: MCP process count scales with tenants

- **WHEN** 3 tenants each use a config with 2 MCP servers
- **THEN** there SHALL be 6 MCP server processes total (2 per tenant)
- **AND** each process SHALL be independently managed (started, stopped, restarted) by its respective Host

### Requirement: Storage connection separation per tenant

Each AgentHost SHALL maintain its own StorageProvider connection. Storage queries SHALL automatically filter by tenant_id. No storage query SHALL return records belonging to a different tenant.

#### Scenario: Interaction history isolation

- **WHEN** tenant-1 calls `storage.get_interactions(agent_name="coder")`
- **THEN** only interaction records with `tenant_id="tenant-1"` SHALL be returned
- **AND** interaction records with `tenant_id="tenant-2"` SHALL NOT appear in results

#### Scenario: Raw query without tenant_id filter is rejected

- **WHEN** code attempts to execute a storage query without providing a tenant_id filter
- **THEN** the StorageProvider SHALL raise a `TenantFilterRequiredError`
- **AND** the query SHALL NOT execute

#### Scenario: Session records isolation

- **WHEN** tenant-1 lists active sessions
- **THEN** only sessions belonging to tenant-1 SHALL be returned
- **AND** sessions belonging to tenant-2 SHALL NOT appear in results

### Requirement: Skill registry separation per tenant

Each AgentHost SHALL maintain its own SkillsRegistry. Skill discovery, loading, and instruction injection SHALL be scoped to the tenant's Host. Skills configured for tenant-1 SHALL NOT be visible to tenant-2 unless tenant-2's Host independently loads them.

#### Scenario: Per-tenant skill discovery

- **WHEN** tenant-1's Host loads skills from `/skills/tenant-1/`
- **AND** tenant-2's Host loads skills from `/skills/tenant-2/`
- **THEN** tenant-1's agent SHALL only see skills from `/skills/tenant-1/`
- **AND** tenant-2's agent SHALL only see skills from `/skills/tenant-2/`

### Requirement: EventBus per-session tenant isolation

EventBus subscriptions SHALL be scoped by session_id. Session_id SHALL encode tenant_id in its composition. Events published in tenant-1's sessions SHALL NOT be delivered to tenant-2's subscribers.

#### Scenario: Cross-tenant event delivery prevention

- **WHEN** tenant-1's session publishes a `PartDeltaEvent` to the EventBus
- **AND** tenant-2 has an active subscription with `scope="session"` for its own session
- **THEN** tenant-2's subscriber SHALL NOT receive tenant-1's event
- **AND** only subscribers to tenant-1's session_id SHALL receive the event

#### Scenario: Child session inherits tenant scope

- **WHEN** tenant-1's session spawns a child session via `SpawnSessionStart`
- **THEN** the child session_id SHALL encode tenant-1's tenant_id
- **AND** child session events SHALL only be delivered to subscribers within tenant-1's scope

### Requirement: Cross-tenant access prevention

No code path SHALL allow a request scoped to tenant-1 to access tenant-2's Host, storage, MCP processes, or EventBus events. Cross-tenant access SHALL be structurally prevented, not merely policy-enforced.

#### Scenario: Forged tenant_id rejected at Host boundary

- **WHEN** a request arrives with RunScope.tenant_id="tenant-2" but is routed to tenant-1's Host
- **THEN** the Host SHALL raise `TenantMismatchError`
- **AND** the request SHALL NOT be processed

#### Scenario: Agent delegation stays within tenant

- **WHEN** tenant-1's coordinator agent delegates to a subagent
- **THEN** the subagent SHALL execute within tenant-1's Host
- **AND** the subagent SHALL NOT have access to any other tenant's infrastructure
