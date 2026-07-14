## Why

M4 enables multiple configurations per process, but all tenants sharing the same config share the same infrastructure (MCP processes, storage connections, sessions). For production multi-user deployments, tenant isolation is required — different teams or users using the same agent config must have completely isolated MCP server processes, storage queries, and sessions. Without tenant isolation, one tenant's MCP tool could access another tenant's files, or storage queries could leak cross-tenant interaction history.

## What Changes

- **AgentHost isolation by (config_id, tenant_id)**: Different tenants get different Host instances even when sharing the same config. Each Host has its own MCP processes, storage connections, and skill registries.
- **EventBus per-session isolation**: Tenant-1 events never leak to tenant-2. EventBus subscriptions are scoped by session_id, which includes tenant_id in its composition.
- **StorageProvider tenant_id filtering**: All storage queries (interaction history, sessions, projects) automatically filter by tenant_id. No query can return cross-tenant data.
- **RunScope validation at layer boundaries**: Each layer validates RunScope.tenant_id matches the Host's tenant_id. Forged tenant_id requests are rejected.
- **Session routing from auth**: ProtocolServer extracts tenant_id from authentication token (API key, JWT, OAuth). Maps to RunScope.
- **Single-tenant default**: When no tenant_id is provided (standalone, single-user), behavior is identical to pre-M5. `tenant_id="default"` is the implicit default.

## Capabilities

### New Capabilities

- `tenant-isolation`: Per-tenant infrastructure isolation — MCP processes, storage connections, skill registries, and EventBus subscriptions are scoped by tenant_id. Cross-tenant access is structurally prevented.

### Modified Capabilities

- `host-registry`: HostRegistry lookup key becomes `(config_id, tenant_id)` — two tenants with same config get different Hosts.
- `run-scope`: RunScope.tenant_id is now validated at every layer boundary, not just used for routing.
- `agent-pool`: AgentPool gains optional `tenant_id` parameter in context manager. When provided, all operations are scoped to that tenant.
