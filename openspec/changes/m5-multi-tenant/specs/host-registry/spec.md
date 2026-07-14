## MODIFIED Requirements

### Requirement: HostRegistry lookup key is (config_id, tenant_id)

HostRegistry SHALL use `(config_id, tenant_id)` as the composite lookup key. Each unique pair SHALL resolve to a distinct AgentHost instance. Two tenants with the same config_id SHALL receive different Hosts. M4 introduced this key; M5 enforces that the tenant_id dimension produces isolated Hosts rather than shared ones.

#### Scenario: Same config, different tenants get different Hosts

- **WHEN** `HostRegistry.get_or_create(config_id="default", tenant_id="tenant-1")` is called
- **AND** `HostRegistry.get_or_create(config_id="default", tenant_id="tenant-2")` is called
- **THEN** two distinct AgentHost instances SHALL be returned
- **AND** each Host SHALL have independent MCP processes, storage connections, and skill registries

#### Scenario: Same config, same tenant returns same Host

- **WHEN** `HostRegistry.get_or_create(config_id="default", tenant_id="tenant-1")` is called twice
- **THEN** the same AgentHost instance SHALL be returned on both calls
- **AND** no new MCP processes SHALL be spawned on the second call

#### Scenario: Different configs, same tenant get different Hosts

- **WHEN** `HostRegistry.get_or_create(config_id="team-a", tenant_id="tenant-1")` is called
- **AND** `HostRegistry.get_or_create(config_id="team-b", tenant_id="tenant-1")` is called
- **THEN** two distinct AgentHost instances SHALL be returned
- **AND** each Host SHALL load its respective config's infrastructure

### Requirement: HostRegistry lazy creation

HostRegistry SHALL lazily create AgentHost instances on first access for a given `(config_id, tenant_id)` pair. Hosts SHALL NOT be eagerly created at startup unless explicitly configured. A warm pool MAY be configured for known-active tenants.

#### Scenario: First access creates Host

- **WHEN** `HostRegistry.get_or_create(config_id="default", tenant_id="tenant-1")` is called for the first time
- **THEN** a new AgentHost SHALL be created with its own infrastructure
- **AND** MCP server processes SHALL be started for that Host

#### Scenario: Warm pool pre-creates Hosts for known tenants

- **WHEN** HostRegistry is configured with `warm_pool_tenants: ["tenant-1", "tenant-2"]`
- **THEN** Hosts for those tenants SHALL be created at startup
- **AND** first request for those tenants SHALL not incur creation latency

### Requirement: HostRegistry eviction with session drain

HostRegistry SHALL evict AgentHost instances that have been idle beyond a configurable threshold. Eviction SHALL drain active sessions gracefully before destroying infrastructure. Evicted Hosts SHALL be recreated on next access.

#### Scenario: Idle Host eviction

- **WHEN** a Host has had no active sessions for longer than the configured idle threshold (default: 30 minutes)
- **THEN** HostRegistry SHALL initiate eviction
- **AND** active sessions SHALL be drained (allowed to complete or timeout)
- **AND** MCP server processes SHALL be terminated
- **AND** storage connections SHALL be closed
- **AND** the Host SHALL be removed from the registry

#### Scenario: Evicted Host recreated on next access

- **WHEN** a Host was evicted due to idle timeout
- **AND** a new request arrives for the same `(config_id, tenant_id)` pair
- **THEN** a new AgentHost SHALL be created with fresh infrastructure
- **AND** the new Host SHALL behave identically to the original

#### Scenario: Eviction does not interrupt active sessions

- **WHEN** a Host is marked for eviction but has active sessions
- **THEN** eviction SHALL wait for sessions to complete or timeout
- **AND** active sessions SHALL NOT be forcibly terminated unless the drain timeout is exceeded
- **AND** the drain timeout SHALL be configurable (default: 60 seconds)
