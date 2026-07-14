## ADDED Requirements

### Requirement: AgentHost is the tenant-scoped infrastructure bundle

AgentHost SHALL be the Layer 2 component that bundles all mutable runtime infrastructure for a single `(config_id, tenant_id)` pair. One AgentHost instance SHALL exist per unique pair. AgentHost SHALL own infrastructure (MCP server processes, storage connections, skill registries) and SHALL wrap the `HostContext`, `AgentFactory`, and `AgentRegistry` produced from a config. AgentHost SHALL NOT be owned by `AgentFactory` — Factory is a standalone service that receives `(manifest, host_context)` as method parameters.

#### Scenario: Single AgentHost per (config_id, tenant_id)

- **WHEN** `HostRegistry.get_or_create("default", "tenant-1")` is called
- **THEN** an AgentHost instance SHALL be returned with `config_id="default"` and `tenant_id="tenant-1"`
- **AND** a second call with the same pair SHALL return the same instance
- **AND** a call with a different pair SHALL return a different instance

### Requirement: AgentHost fields are typed and wrap M1 components

AgentHost SHALL be a class with the following fields, each using concrete types (not `Any`):
- `host_context: HostContext` — frozen dataclass from M1 carrying infrastructure handles
- `factory: AgentFactory` — standalone compilation service from M1
- `registry: AgentRegistry` — compiled agent instances from M1
- `config_id: str` — which config this Host was created from
- `tenant_id: str` — which tenant this Host belongs to
- `model_cache: ModelCache` — shared pydantic-ai Model instances (from M4)

AgentHost SHALL NOT duplicate fields already present in `HostContext`. It SHALL wrap `HostContext` and delegate infrastructure access to it.

#### Scenario: AgentHost construction from HostRegistry

- **WHEN** HostRegistry creates a new AgentHost for `("config-a", "tenant-1")`
- **THEN** the Host SHALL be constructed with a HostContext built from config-a's HostConfig
- **AND** the Host SHALL have an AgentFactory instance (standalone, not owned)
- **AND** the Host SHALL have an AgentRegistry populated by `factory.compile(manifest, host_context)`
- **AND** `config_id` SHALL be `"config-a"` and `tenant_id` SHALL be `"tenant-1"`
- **AND** `model_cache` SHALL be a fresh `ModelCache` instance scoped to this Host

#### Scenario: AgentHost does not duplicate HostContext fields

- **WHEN** code accesses `host.host_context.mcp` to get the MCP manager
- **THEN** the MCP manager SHALL be returned
- **AND** AgentHost SHALL NOT expose a separate `mcp_servers` field that duplicates `host_context.mcp`
- **AND** all infrastructure access SHALL go through `host_context`

### Requirement: AgentHost.get_agent delegates to registry

AgentHost SHALL provide a `get_agent(name: str) -> MessageNode` method that delegates to `registry.get(name)`. If the agent name does not exist in the registry, a `KeyError` SHALL be raised.

#### Scenario: Get agent by name

- **WHEN** `host.get_agent("coder")` is called and "coder" exists in the registry
- **THEN** the `MessageNode` for "coder" SHALL be returned
- **AND** the returned node SHALL be the same instance across multiple calls (not recreated)

#### Scenario: Get non-existent agent

- **WHEN** `host.get_agent("nonexistent")` is called
- **THEN** a `KeyError` SHALL be raised
- **AND** no agent execution SHALL occur

### Requirement: AgentHost.reload rebuilds infrastructure and triggers recompile

AgentHost SHALL provide an `async reload() -> None` method that rebuilds infrastructure when a `HostConfig` change is detected. Reload SHALL:
1. Stop MCP server processes managed by this Host
2. Reconnect storage with the new configuration
3. Reload skill registries from the new skill paths
4. Construct a new `HostContext` with updated infrastructure handles (preserving `config_id` and `tenant_id`)
5. Call `factory.recompile(new_manifest, new_host_context)` to rebuild agents

Active turns SHALL continue using the old HostContext snapshot until they complete. New turns SHALL use the new HostContext. This is the turn-level snapshot guarantee.

#### Scenario: Config change triggers full reload

- **WHEN** a HostConfig change is detected (e.g., new MCP server added, storage reconfigured)
- **AND** `host.reload()` is called
- **THEN** MCP server processes SHALL be stopped and restarted with new configuration
- **AND** storage connections SHALL be reconnected
- **AND** skill registries SHALL be reloaded
- **AND** `factory.recompile()` SHALL be called with the new HostContext
- **AND** `host_context` SHALL be atomically replaced with the new instance

#### Scenario: Active turns complete against old context during reload

- **WHEN** `host.reload()` is called while 2 turns are in progress
- **THEN** those 2 turns SHALL continue using the old HostContext snapshot
- **AND** new turns started after reload SHALL use the new HostContext
- **AND** no turn SHALL see a mix of old and new infrastructure handles

#### Scenario: Reload preserves config_id and tenant_id

- **WHEN** `host.reload()` is called
- **THEN** the new HostContext SHALL have the same `config_id` as before reload
- **AND** the new HostContext SHALL have the same `tenant_id` as before reload

### Requirement: AgentHost.cleanup drains sessions and tears down infrastructure

AgentHost SHALL provide an `async cleanup() -> None` method that gracefully shuts down all infrastructure. Cleanup SHALL:
1. Drain active sessions — wait for in-flight turns to complete (with a configurable timeout, default: 30s)
2. If the drain timeout expires, cancel remaining active sessions gracefully
3. Stop all MCP server processes managed by this Host
4. Close storage connections
5. Clear the agent registry

After `cleanup()` returns, the AgentHost SHALL be in a state where it cannot serve new requests. `HostRegistry` SHALL remove the Host from cache before or during cleanup.

#### Scenario: Cleanup with no active sessions

- **WHEN** `host.cleanup()` is called and the Host has no active sessions
- **THEN** MCP server processes SHALL be stopped
- **AND** storage connections SHALL be closed
- **AND** the registry SHALL be cleared
- **AND** cleanup SHALL complete immediately without waiting

#### Scenario: Cleanup with active sessions

- **WHEN** `host.cleanup()` is called and the Host has 3 active sessions
- **THEN** cleanup SHALL wait for those sessions to complete (up to the drain timeout)
- **AND** once all sessions complete, infrastructure SHALL be torn down
- **AND** new session requests SHALL be rejected during drain

#### Scenario: Cleanup timeout expires

- **WHEN** `host.cleanup(timeout=5)` is called and active sessions do not complete within 5 seconds
- **THEN** active sessions SHALL be cancelled gracefully
- **AND** infrastructure SHALL be torn down regardless
- **AND** the Host SHALL be removed from the HostRegistry cache

### Requirement: AgentHost.validate_tenant enforces tenant boundary

AgentHost SHALL provide a `validate_tenant(tenant_id: str) -> None` method that checks whether the given `tenant_id` matches `self.tenant_id`. If they do not match, a `TenantMismatchError` SHALL be raised. This method SHALL be called at the AgentHost layer boundary to prevent cross-tenant access.

#### Scenario: Tenant validation passes

- **WHEN** `host.validate_tenant("tenant-1")` is called and `host.tenant_id == "tenant-1"`
- **THEN** no exception SHALL be raised
- **AND** execution SHALL proceed normally

#### Scenario: Tenant validation fails

- **WHEN** `host.validate_tenant("tenant-2")` is called and `host.tenant_id == "tenant-1"`
- **THEN** a `TenantMismatchError` SHALL be raised
- **AND** the error message SHALL include both the expected and actual tenant_id
- **AND** no agent execution SHALL occur

### Requirement: AgentHost is created by HostRegistry

AgentHost SHALL NOT be directly instantiated by protocol servers or user code. It SHALL only be created by `HostRegistry.get_or_create(config_id, tenant_id)`. The HostRegistry SHALL handle: config loading, infrastructure initialization, HostContext construction, and AgentFactory compilation. AgentHost's `__init__` SHALL accept the pre-built components (host_context, factory, registry, config_id, tenant_id, model_cache) rather than building them itself.

#### Scenario: AgentHost created through HostRegistry

- **WHEN** a protocol server needs an AgentHost
- **THEN** it SHALL call `HostRegistry.get_or_create(config_id, tenant_id)`
- **AND** HostRegistry SHALL construct the Host with pre-built components
- **AND** the protocol server SHALL NOT call `AgentHost.__init__` directly

#### Scenario: AgentHost accepts pre-built components

- **WHEN** HostRegistry constructs an AgentHost
- **THEN** the `__init__` SHALL accept `host_context`, `factory`, `registry`, `config_id`, `tenant_id`, and `model_cache` as parameters
- **AND** it SHALL NOT perform config loading or infrastructure initialization itself
- **AND** it SHALL store all components as instance attributes
