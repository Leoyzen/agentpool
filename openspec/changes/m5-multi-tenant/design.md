## Context

M4 introduced ConfigRegistry, HostRegistry, and RunScope. HostRegistry already keys by `(config_id, tenant_id)`, but in practice the same config shared by different tenants currently resolves to the same AgentHost instance — the tenant_id dimension is carried but not enforced. MCP processes, storage connections, skill registries, and EventBus subscriptions are shared across tenants using the same config, creating cross-tenant data leakage and tool access risks.

For production multi-user deployments (e.g., a hosted AgentPool serving multiple teams), this is a security blocker. Tenant-A's filesystem MCP server can access Tenant-B's working directory. Storage queries for interaction history return records from all tenants. EventBus events from one tenant's session leak into another tenant's subscription scope.

RFC-0050 defines AgentHost as the **tenant isolation boundary** — one Host per `(config_id, tenant_id)` pair, each owning completely separate infrastructure. M5 makes this boundary real by enforcing isolation at every layer: HostRegistry creates distinct Host instances per tenant, StorageProvider auto-filters by tenant_id, EventBus subscriptions are scoped by session_id (which encodes tenant_id), and RunScope.tenant_id is validated at every layer boundary.

**Key constraint**: Single-tenant mode (`tenant_id="default"`) MUST behave identically to pre-M5 behavior. No configuration change is required for existing users. Multi-tenant isolation is opt-in — it activates when a non-default tenant_id is provided at the protocol boundary.

## Goals / Non-Goals

**Goals:**
- AgentHost isolation by `(config_id, tenant_id)` — different tenants get different Host instances even with the same config, each with its own MCP processes, storage connections, and skill registries
- EventBus per-session isolation — tenant-1 events never reach tenant-2 subscribers; session_id composition includes tenant_id
- StorageProvider tenant_id filtering — all storage queries (interaction history, sessions, projects) automatically filter by tenant_id; no raw query can return cross-tenant data
- RunScope.tenant_id validation at layer boundaries — each layer validates that the incoming RunScope.tenant_id matches the Host's tenant_id; forged tenant_id requests are rejected
- Auth-based tenant extraction — ProtocolServer extracts tenant_id from authentication token (API key, JWT, OAuth) at the protocol boundary
- Single-tenant default preserved — `tenant_id="default"` is the implicit zero-config default; no behavior change for existing users

**Non-Goals:**
- Polyglot runtime (M6) — multi-language agent execution
- New authentication system — M5 uses existing token mechanisms (API keys, JWT claims); it does not build a new auth provider
- Tenant management UI — no admin dashboard for creating/managing tenants
- Per-tenant rate limiting or quota enforcement — future work
- Distributed HostRegistry (remote Hosts across machines) — M5 is in-process only; distributed routing is a future milestone

## Decisions

### Decision 1: Different tenants get different Host instances even with same config

**Choice**: HostRegistry lookup by `(config_id, tenant_id)` always returns a distinct AgentHost per tenant. Two tenants sharing the same config_id each get their own Host with separate MCP processes, storage connections, and skill registries.

**Rationale**: The Host IS the isolation boundary (RFC-0050, Layer 2). Sharing a Host across tenants defeats the purpose — MCP processes would share filesystem access, storage connections would share database sessions, and skill registries would share instruction sets. Full infrastructure isolation per tenant is the only structurally sound approach.

**Alternative considered**: Shared Host with per-tenant namespacing inside MCP/storage — rejected because it requires every MCP server and storage provider to implement tenant awareness internally, which is infeasible and fragile. Process-level isolation is enforced by the OS, not by convention.

### Decision 2: EventBus subscriptions scoped by session_id which includes tenant_id

**Choice**: EventBus subscriptions use `scope="session"` with session_id as the key. Session_id is composed to include tenant_id (e.g., `{tenant_id}:{session_uuid}`). Subscribers never receive events from sessions belonging to other tenants.

**Rationale**: The EventBus already supports session-scoped subscriptions. By encoding tenant_id into session_id composition, we get tenant isolation for free without adding a separate tenant filter to every EventBus call. The session_id is the natural isolation key because it already gates event delivery.

**Alternative considered**: Adding an explicit `tenant_id` parameter to every `subscribe()` and `publish()` call — rejected because it adds API surface and is easy to forget. Session_id composition is automatic and cannot be bypassed.

### Decision 3: StorageProvider queries auto-filter by tenant_id

**Choice**: All StorageProvider query methods (get_interactions, get_sessions, get_projects, etc.) automatically append a `WHERE tenant_id = ?` filter. Raw queries without a tenant_id filter SHALL be rejected at the storage layer.

**Rationale**: Storage is the persistence boundary — if tenant data leaks here, it persists across restarts. Auto-filtering at the provider level (not the caller level) ensures no code path can bypass the filter. This is defense-in-depth: even if an agent somehow obtains a reference to another tenant's storage, queries return zero rows.

**Alternative considered**: Separate databases per tenant — rejected for v1.0 because it complicates connection management and schema migrations. Column-level filtering with a `tenant_id` column on every table is sufficient and is the RFC-0050 recommendation. Separate DB support is a future option (Open Question 7 in RFC-0050).

### Decision 4: RunScope.tenant_id validated at every layer boundary

**Choice**: Each layer boundary (ProtocolServer → HostRegistry, HostRegistry → AgentHost, AgentHost → AgentFactory, AgentFactory → Agent) validates that the incoming RunScope.tenant_id matches the expected tenant_id for that context. Mismatches raise `TenantMismatchError` and the request is rejected.

**Rationale**: RunScope is the cross-cutting routing context (RFC-0050). Without validation at each boundary, a forged RunScope could traverse layers and access another tenant's Host. Validation at every hop ensures that even if one layer is compromised, the next layer catches the mismatch.

**Alternative considered**: Validation only at the protocol boundary (ProtocolServer) — rejected because internal layers could still be called directly (e.g., by background jobs or hooks). Defense-in-depth requires validation at every hop, not just the entry point.

### Decision 5: tenant_id extracted from auth token at protocol boundary

**Choice**: ProtocolServer extracts tenant_id from the authentication token during `initialize`. API keys map to tenant_id via a lookup table. JWT claims include a `tenant_id` field. OAuth tokens resolve tenant_id via the provider's userinfo endpoint. If no auth token is provided, tenant_id defaults to `"default"`.

**Rationale**: The protocol boundary is the trust boundary — it is where untrusted network input is converted to trusted internal context. Extracting tenant_id here (rather than from request headers or query params) prevents forgery. Auth tokens are cryptographically signed; request headers are not.

**Alternative considered**: tenant_id passed as an explicit parameter in the protocol `initialize` message — rejected because it is trivially forgeable. The protocol client could claim any tenant_id. Auth token extraction binds tenant identity to cryptographic proof.

### Decision 6: Single-tenant mode (tenant_id="default") is zero-config default

**Choice**: When no tenant_id is provided (standalone usage, single-user CLI, no auth configured), `tenant_id="default"` is used. All HostRegistry lookups, StorageProvider queries, and EventBus subscriptions use this default. Behavior is identical to pre-M5.

**Rationale**: AgentPool's primary use case is single-user development. Multi-tenant is an opt-in deployment scenario. Requiring tenant configuration for single-user usage would break backward compatibility and add friction. The `"default"` tenant_id is a sentinel that enables all isolation machinery to run uniformly without special-casing.

**Alternative considered**: Disabling isolation machinery when tenant_id is "default" — rejected because it creates two code paths (isolated vs non-isolated) that must be maintained and tested separately. Running the same machinery with a default value is simpler and ensures the isolation code is always exercised.

## Risks / Trade-offs

- **[Risk] MCP process explosion** — N tenants × M MCP servers per config = N×M processes. For 10 tenants with 3 MCP servers each, that is 30 processes. Mitigation: HostRegistry eviction policy drains and kills idle Hosts (idle > configurable threshold). Process pools can be shared across tenants for read-only MCP servers in a future optimization. MEDIUM risk for large deployments.
- **[Risk] Storage migration for existing data** — Existing interaction history and session records have no `tenant_id` column. Migration must add the column with `default` value. Mitigation: Alembic migration script runs on startup. Data is not lost — all existing records become tenant_id="default". LOW risk for data integrity, MEDIUM risk for migration downtime on large databases.
- **[Risk] Auth token parsing varies by protocol** — ACP, OpenCode, AG-UI, and OpenAI API each have different auth mechanisms. Extracting tenant_id consistently across protocols requires per-protocol adapters. Mitigation: Define a `TenantExtractor` protocol with per-server implementations. Each protocol server implements its own extraction logic. MEDIUM risk for implementation complexity.
- **[Trade-off] Process-level isolation vs namespacing** — Full Host isolation (separate processes per tenant) is more secure but more resource-intensive. The alternative (shared processes with namespacing) is lighter but requires every MCP server and storage provider to be tenant-aware. We choose security over efficiency for v1.0.
- **[Trade-off] Column-level filtering vs separate databases** — A `tenant_id` column on shared tables is simpler to implement but theoretically allows cross-tenant queries if the filter is bypassed. Separate databases provide physical isolation but complicate connection management. We choose column-level for v1.0, with separate DB as a future option.
- **[Risk] Session_id composition changes** — Encoding tenant_id into session_id changes the session_id format. Existing session references (stored in client state, databases) may break. Mitigation: Backward-compatible parsing — session_ids without tenant_id prefix are treated as tenant_id="default". LOW risk for backward compatibility.
