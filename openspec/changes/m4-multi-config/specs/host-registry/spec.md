## ADDED Requirements

### Requirement: HostRegistry lazily creates AgentHost instances by (config_id, tenant_id)

HostRegistry SHALL maintain a cache of `AgentHost` instances keyed by `(config_id, tenant_id)` tuple. `get_or_create(config_id, tenant_id)` SHALL return an existing Host from cache or create a new one if none exists. Creation SHALL involve: loading config from ConfigRegistry, initializing infrastructure (MCP, storage, skills), constructing HostContext, and compiling agents via AgentFactory.

#### Scenario: First access creates a Host

- **WHEN** `registry.get_or_create("config-a", "tenant-1")` is called and no Host exists for that key
- **THEN** a new AgentHost SHALL be created with infrastructure initialized from "config-a"
- **AND** the Host SHALL be cached under key `("config-a", "tenant-1")`
- **AND** subsequent calls with the same key SHALL return the cached instance

#### Scenario: Different configs get different Hosts

- **WHEN** `registry.get_or_create("config-a", "tenant-1")` and `registry.get_or_create("config-b", "tenant-1")` are called
- **THEN** two distinct AgentHost instances SHALL be returned
- **AND** each Host SHALL have its own infrastructure (separate MCP processes, storage, skills)

#### Scenario: Same config different tenants get isolated Hosts

- **WHEN** `registry.get_or_create("config-a", "tenant-1")` and `registry.get_or_create("config-a", "tenant-2")` are called
- **THEN** two distinct AgentHost instances SHALL be returned
- **AND** both Hosts SHALL use the same config but have isolated runtime state (separate agent instances, separate sessions)

### Requirement: HostRegistry caches Host instances

HostRegistry SHALL cache created Host instances to avoid redundant initialization. The cache SHALL be thread-safe and support concurrent `get_or_create` calls for different keys without blocking.

#### Scenario: Concurrent access for different keys

- **WHEN** two threads call `get_or_create("config-a", "tenant-1")` and `get_or_create("config-b", "tenant-2")` simultaneously
- **THEN** both calls SHALL proceed without blocking each other
- **AND** both Hosts SHALL be created and cached correctly

#### Scenario: Concurrent access for same key

- **WHEN** two coroutines call `get_or_create("config-a", "tenant-1")` simultaneously and no Host exists yet
- **THEN** only one Host SHALL be created (the second call SHALL wait and return the same instance)
- **AND** no duplicate infrastructure initialization SHALL occur

### Requirement: HostRegistry evicts Hosts with active session drain

HostRegistry SHALL support `evict(config_id, tenant_id)` to remove a Host from cache. Eviction SHALL drain active sessions before destroying infrastructure — it SHALL wait for in-flight turns to complete (with a configurable timeout, default: 30s). If the timeout expires, active sessions SHALL be cancelled gracefully.

#### Scenario: Evict a Host with no active sessions

- **WHEN** `registry.evict("config-a", "tenant-1")` is called and the Host has no active sessions
- **THEN** the Host SHALL be removed from cache immediately
- **AND** infrastructure (MCP processes, storage connections) SHALL be cleaned up

#### Scenario: Evict a Host with active sessions

- **WHEN** `registry.evict("config-a", "tenant-1")` is called and the Host has 2 active sessions
- **THEN** eviction SHALL wait for both sessions to complete (up to the drain timeout)
- **AND** once all sessions complete, the Host SHALL be removed and infrastructure cleaned up
- **AND** new session requests for that Host SHALL be rejected during drain

#### Scenario: Eviction timeout expires

- **WHEN** `registry.evict("config-a", "tenant-1", timeout=5)` is called and active sessions do not complete within 5 seconds
- **THEN** active sessions SHALL be cancelled gracefully
- **AND** the Host SHALL be removed from cache
- **AND** infrastructure SHALL be cleaned up

### Requirement: HostRegistry reacts to config changes from ConfigRegistry

HostRegistry SHALL subscribe to ConfigRegistry change notifications. When a config changes, HostRegistry SHALL determine whether the change is a HostConfig change or AgentManifest change and trigger the appropriate reload mechanism on all affected Hosts.

#### Scenario: AgentManifest change triggers recompile

- **WHEN** a config change notification indicates only agent definition sections changed
- **THEN** HostRegistry SHALL call `factory.recompile()` on all Hosts using that config
- **AND** infrastructure SHALL NOT be restarted

#### Scenario: HostConfig change triggers full reload

- **WHEN** a config change notification indicates infrastructure sections changed
- **THEN** HostRegistry SHALL call `host.reload()` on all Hosts using that config
- **AND** infrastructure SHALL be restarted with the new configuration
