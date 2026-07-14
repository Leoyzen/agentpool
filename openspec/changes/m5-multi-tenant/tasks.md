## 1. Tenant Isolation Error Types

- [ ] 1.1 Create `src/agentpool/tenant/errors.py` defining `TenantMismatchError(Exception)` with fields `expected_tenant_id: str` and `actual_tenant_id: str`, and a descriptive message: `"Tenant mismatch: expected {expected}, got {actual}"`
- [ ] 1.2 Create `TenantFilterRequiredError(Exception)` with field `query_name: str` and message: `"Storage query '{query_name}' requires a tenant_id filter — raw queries without tenant_id are rejected"`
- [ ] 1.3 Create `src/agentpool/tenant/__init__.py` with public exports: `TenantMismatchError`, `TenantFilterRequiredError`
- [ ] 1.4 Write unit tests for `TenantMismatchError`: construction with expected/actual, message format, is an Exception subclass, raises and catches correctly
- [ ] 1.5 Write unit tests for `TenantFilterRequiredError`: construction with query_name, message format, is an Exception subclass, raises and catches correctly

## 2. HostRegistry: Enforce Distinct Host per (config_id, tenant_id)

- [ ] 2.1 Modify `HostRegistry.get_or_create()` in `src/agentpool/host/registry.py` to always return a distinct `AgentHost` per `(config_id, tenant_id)` pair — verify that the cache key `tuple[str, str]` produces separate entries for different tenant_ids even with the same config_id (M4 introduced the key; M5 enforces isolation)
- [ ] 2.2 Verify `HostRegistry.get_or_create()` starts fresh MCP processes, storage connections, and skill registries for each new `(config_id, tenant_id)` pair — no infrastructure sharing across tenants
- [ ] 2.3 Add `HostRegistry.validate_tenant(run_scope: RunScope, config_id: str, tenant_id: str) -> None` — checks `run_scope.tenant_id` matches the `tenant_id` argument; raises `TenantMismatchError` on mismatch (ProtocolServer → HostRegistry boundary)
- [ ] 2.4 Implement `HostRegistry.get_or_create_validated(config_id: str, run_scope: RunScope) -> AgentHost` — calls `validate_tenant(run_scope, config_id, run_scope.tenant_id)` then delegates to `get_or_create(config_id, run_scope.tenant_id)`
- [ ] 2.5 Write unit tests: same config + different tenants → distinct Host instances with independent infrastructure, same config + same tenant → same Host instance (no new processes), `validate_tenant` raises `TenantMismatchError` on mismatch, `get_or_create_validated` rejects forged tenant_id
- [ ] 2.6 Write unit test for MCP process count: 3 tenants × 2 MCP servers each = 6 processes, each independently managed by its respective Host

## 3. HostRegistry: Eviction with Session Drain

- [ ] 3.1 Implement `HostRegistry.evict(config_id: str, tenant_id: str, idle_threshold: float = 1800.0, drain_timeout: float = 60.0) -> None` — marks Host as draining, rejects new session requests, waits for active sessions up to `drain_timeout`, then terminates MCP processes and closes storage connections
- [ ] 3.2 Implement drain logic: track Host's active session count via `SessionController`, `await` with `drain_timeout`, on timeout cancel remaining active sessions gracefully, remove Host from `_cache`, call `host.cleanup()`
- [ ] 3.3 Implement idle detection: `HostRegistry._check_idle_hosts()` background task that periodically (default: every 5 minutes) scans cached Hosts for idle duration exceeding `idle_threshold` and triggers eviction
- [ ] 3.4 Implement `HostRegistry.configure_warm_pool(tenants: list[str], config_id: str = "default") -> None` — pre-creates Hosts for known-active tenants at startup to avoid first-request latency
- [ ] 3.5 Write unit tests: idle Host evicted immediately (no active sessions), active sessions block eviction until complete, drain timeout cancels sessions, new requests rejected during drain, evicted Host recreated on next access, warm pool pre-creates Hosts

## 4. AgentHost: Per-Tenant Infrastructure Isolation

- [ ] 4.1 Verify `AgentHost` (from M4) creates its own `MCPManager` instance per Host — no MCP server process sharing across tenants; each Host's `mcp_servers` pool is independent
- [ ] 4.2 Verify `AgentHost` creates its own `StorageManager` connection per Host — each Host's storage provider instance is independent, allowing per-tenant filtering (task group 6)
- [ ] 4.3 Verify `AgentHost` creates its own `SkillsManager` / `SkillsRegistry` per Host — skill discovery and instruction injection scoped to the tenant's Host only
- [ ] 4.4 Add `AgentHost.validate_tenant(run_scope: RunScope) -> None` — checks `run_scope.tenant_id == self.tenant_id`; raises `TenantMismatchError` on mismatch (HostRegistry → AgentHost boundary)
- [ ] 4.5 Modify `AgentHost.get_agent(name: str, run_scope: RunScope) -> MessageNode` — call `validate_tenant(run_scope)` before retrieving agent from registry; reject requests with mismatched tenant_id
- [ ] 4.6 Write unit tests: two Hosts for same config different tenants have independent MCPManager instances, independent StorageManager instances, independent SkillsRegistry instances; `validate_tenant` raises on mismatch; `get_agent` with wrong tenant_id raises `TenantMismatchError`

## 5. Session ID Composition with tenant_id

- [ ] 5.1 Create `src/agentpool/tenant/session_id.py` defining `compose_session_id(tenant_id: str, session_uuid: str | None = None) -> str` — returns `"{tenant_id}:{session_uuid}"` where `session_uuid` defaults to `uuid4().hex`
- [ ] 5.2 Define `parse_session_id(session_id: str) -> tuple[str, str]` — splits `"{tenant_id}:{session_uuid}"` into components; if no `:` separator, returns `("default", session_id)` for backward compatibility
- [ ] 5.3 Define `extract_tenant_from_session_id(session_id: str) -> str` — convenience function returning just the tenant_id portion; returns `"default"` for un-prefixed session_ids
- [ ] 5.4 Write unit tests: `compose_session_id("tenant-1", "abc123")` returns `"tenant-1:abc123"`, `compose_session_id("default")` returns `"default:{uuid}"`, `parse_session_id("tenant-1:abc")` returns `("tenant-1", "abc")`, `parse_session_id("legacy-session")` returns `("default", "legacy-session")` (backward compat), `extract_tenant_from_session_id` returns correct tenant

## 6. EventBus Per-Session Tenant Isolation

- [ ] 6.1 Modify `EventBus.subscribe()` in `src/agentpool/orchestrator/event_bus.py` to accept composed session_ids (from task 5.1) — no API change needed, but verify that session-scoped subscriptions using `{tenant_id}:{session_uuid}` keys naturally isolate tenants
- [ ] 6.2 Modify `EventBus.publish()` to verify `source_session_id` in `EventEnvelope` uses composed format — events from tenant-1's session only reach subscribers of that exact session_id
- [ ] 6.3 Modify child session spawning (`SpawnSessionStart` handling) to compose child session_id with parent's tenant_id — `compose_session_id(parent_tenant_id, child_uuid)` ensures child inherits tenant scope
- [ ] 6.4 Add `EventBus.get_tenant_for_session(session_id: str) -> str` — uses `extract_tenant_from_session_id()` to return the tenant owning a session; used for diagnostics and validation
- [ ] 6.5 Write unit tests: tenant-1 subscriber never receives tenant-2 events (same config, different session_ids), child session inherits parent's tenant_id in session_id, backward-compat un-prefixed session_ids work with `scope="session"`, `get_tenant_for_session` returns correct tenant
- [ ] 6.6 Write unit test for `ProtocolEventConsumerMixin`: consumer subscribed to tenant-1's session does not receive events when tenant-2's session publishes to the EventBus

## 7. StorageProvider: tenant_id Auto-Filtering

- [ ] 7.1 Add `tenant_id: str = "default"` field to `StorageProvider.__init__()` in `src/agentpool_storage/base.py` — each StorageProvider instance is bound to a single tenant (one per AgentHost)
- [ ] 7.2 Add `_require_tenant_filter()` internal guard method on `StorageProvider` — raises `TenantFilterRequiredError` if `self.tenant_id` is not set (defense-in-depth for misconfigured providers)
- [ ] 7.3 Modify `StorageProvider.get_sessions()` to auto-append `WHERE tenant_id = self.tenant_id` to the query — no caller needs to pass tenant_id explicitly
- [ ] 7.4 Modify `StorageProvider.get_filtered_conversations()` to auto-filter by `self.tenant_id` — results only include conversations belonging to the provider's tenant
- [ ] 7.5 Modify `StorageProvider.get_session_stats()` to auto-filter by `self.tenant_id` — statistics only computed for the provider's tenant
- [ ] 7.6 Modify `StorageProvider.list_session_ids()` to auto-filter by `self.tenant_id` — only returns session IDs belonging to the provider's tenant
- [ ] 7.7 Modify `StorageProvider.log_message()`, `log_session()`, `log_command()` to auto-set `tenant_id = self.tenant_id` on all write operations
- [ ] 7.8 Modify `StorageProvider.save_session()` to auto-set `tenant_id = self.tenant_id` on session data before persistence
- [ ] 7.9 Modify `StorageProvider.list_projects()`, `get_project()`, `get_project_by_name()`, `get_project_by_worktree()` to auto-filter by `self.tenant_id`
- [ ] 7.10 Write unit tests: `get_sessions` returns only current tenant's sessions, `get_filtered_conversations` excludes other tenants, `log_message`/`log_session` write correct tenant_id, `list_projects` returns only current tenant's projects, missing tenant_id raises `TenantFilterRequiredError`

## 8. SQL Provider: tenant_id Column and Query Filtering

- [ ] 8.1 Add `tenant_id: str = Field(default="default", index=True)` column to `Conversation` model in `src/agentpool_storage/sql_provider/models.py`
- [ ] 8.2 Add `tenant_id: str = Field(default="default", index=True)` column to `Message` model
- [ ] 8.3 Add `tenant_id: str = Field(default="default", index=True)` column to `CommandHistory` model
- [ ] 8.4 Add `tenant_id: str = Field(default="default", index=True)` column to `Project` model
- [ ] 8.5 Modify `SQLModelProvider.__init__()` to accept `tenant_id: str = "default"` and pass to `super().__init__()` — each SQLModelProvider instance is bound to one tenant
- [ ] 8.6 Modify `SQLModelProvider.get_sessions()` query builder in `src/agentpool_storage/sql_provider/utils.py` (`build_message_query`) to append `WHERE Conversation.tenant_id == self.tenant_id` to all select statements
- [ ] 8.7 Modify `SQLModelProvider.get_filtered_conversations()` to add `.where(Conversation.tenant_id == self.tenant_id)` to the SQLAlchemy select
- [ ] 8.8 Modify `SQLModelProvider.log_session()` to set `tenant_id=self.tenant_id` on the `Conversation` row on insert/upsert
- [ ] 8.9 Modify `SQLModelProvider.log_message()` to set `tenant_id=self.tenant_id` on the `Message` row
- [ ] 8.10 Modify `SQLModelProvider.log_command()` to set `tenant_id=self.tenant_id` on the `CommandHistory` row
- [ ] 8.11 Modify `SQLModelProvider.save_project()` to set `tenant_id=self.tenant_id` on the `Project` row
- [ ] 8.12 Modify `SQLModelProvider.list_projects()` and `get_project_*` methods to filter by `tenant_id == self.tenant_id`
- [ ] 8.13 Modify `SQLModelProvider.list_session_ids()` to filter by `Conversation.tenant_id == self.tenant_id`
- [ ] 8.14 Write unit tests: SQL queries include tenant_id filter in WHERE clause, write operations set correct tenant_id, cross-tenant data not returned, two providers with different tenant_ids querying same DB return disjoint result sets

## 9. Storage Migration: Add tenant_id Column

- [ ] 9.1 Create Alembic migration script `add_tenant_id_column` that adds `tenant_id` column (VARCHAR, default `"default"`, indexed) to `conversation`, `message`, `commandhistory`, and `project` tables
- [ ] 9.2 Migration backfills existing rows: `UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL` for all four tables
- [ ] 9.3 Migration creates indexes: `CREATE INDEX ix_{table}_tenant_id ON {table} (tenant_id)` for all four tables (if not already created by column definition)
- [ ] 9.4 Verify migration is idempotent: running twice does not error (Alembic stamp handles this, but verify column existence check)
- [ ] 9.5 Write migration test: apply migration to a pre-populated test database, verify all existing rows have `tenant_id="default"`, verify indexes exist, verify rollback (downgrade) removes column cleanly
- [ ] 9.6 Verify `SQLModelProvider._init_database()` runs the migration on startup — existing databases are automatically migrated when AgentPool starts

## 10. RunScope tenant_id Validation at Layer Boundaries

- [ ] 10.1 Add `RunScope.validate_tenant(expected_tenant_id: str) -> None` method to `src/agentpool/run_scope.py` — raises `TenantMismatchError` if `self.tenant_id != expected_tenant_id`
- [ ] 10.2 Modify `HostRegistry.get_or_create_validated()` (from task 2.4) to call `run_scope.validate_tenant(tenant_id)` before Host lookup — ProtocolServer → HostRegistry boundary validation
- [ ] 10.3 Modify `AgentHost.get_agent()` (from task 4.5) to call `run_scope.validate_tenant(self.tenant_id)` before agent retrieval — HostRegistry → AgentHost boundary validation
- [ ] 10.4 Modify `AgentFactory.compile()` / `AgentFactory.recompile()` to accept `run_scope: RunScope` parameter and validate `run_scope.tenant_id == host_context.tenant_id` before compilation — AgentHost → AgentFactory boundary validation
- [ ] 10.5 Modify `AgentFactory` to reject compilation requests where `run_scope.tenant_id` does not match the `HostContext.tenant_id` — AgentFactory → Agent boundary validation
- [ ] 10.6 Write unit tests: each boundary raises `TenantMismatchError` on tenant_id mismatch, valid tenant_id passes through silently, validation happens before any work is done (no side effects on mismatch)

## 11. RunScope: Background Jobs and Hooks Preserve Tenant Scope

- [ ] 11.1 Modify background job scheduling (`BackgroundTaskProvider` or equivalent) to construct `RunScope` with the correct `tenant_id` from the originating session — extract tenant_id via `extract_tenant_from_session_id(session_id)`
- [ ] 11.2 Modify hook execution (`HookAwareTurn` in `src/agentpool/orchestrator/turn.py`) to carry `tenant_id` from the session's `RunScope` — `pre_turn`/`post_turn`/`pre_tool_use`/`post_tool_use` hooks execute within the correct tenant scope
- [ ] 11.3 Verify `CallableHook`, `CommandHook`, and `PromptHook` receive the correct `tenant_id` in their execution context — hooks cannot access another tenant's Host infrastructure
- [ ] 11.4 Modify subagent delegation (`SubagentToolset` / `SubagentCapability`) to propagate `tenant_id` — delegated subagent executes within the parent's tenant Host, not a different tenant's
- [ ] 11.5 Write unit tests: background job RunScope has correct tenant_id, hook execution context matches session tenant, subagent delegation stays within parent tenant, forged tenant_id in delegation is rejected

## 12. TenantExtractor Protocol and Auth-Based Extraction

- [ ] 12.1 Create `src/agentpool/tenant/extractor.py` defining `TenantExtractor` as a `Protocol` with method `extract_tenant_id(auth_token: str | None) -> str` — returns tenant_id from token, or `"default"` if no token
- [ ] 12.2 Define `APIKeyTenantExtractor(TenantExtractor)` — maps API keys to tenant_ids via a configurable `dict[str, str]` lookup table; unknown keys return `"default"`
- [ ] 12.3 Define `JWTTenantExtractor(TenantExtractor)` — decodes JWT, extracts `tenant_id` claim; invalid signature or missing claim returns `"default"`
- [ ] 12.4 Define `NoOpTenantExtractor(TenantExtractor)` — always returns `"default"`; used for single-tenant mode and testing
- [ ] 12.5 Write unit tests: `APIKeyTenantExtractor` maps known key to tenant, unknown key returns `"default"`, `None` token returns `"default"`; `JWTTenantExtractor` extracts valid claim, invalid JWT returns `"default"`, missing claim returns `"default"`; `NoOpTenantExtractor` always returns `"default"`

## 13. Per-Server TenantExtractor Integration

- [ ] 13.1 Modify `BaseServer.__init__()` in `src/agentpool_server/base.py` to accept optional `tenant_extractor: TenantExtractor = None` — defaults to `NoOpTenantExtractor()` for backward compatibility
- [ ] 13.2 Modify ACP server `initialize` handler in `src/agentpool_server/acp_server/acp_agent.py` to call `self.tenant_extractor.extract_tenant_id(auth_token)` from the ACP `initialize` params, construct `RunScope` with extracted tenant_id
- [ ] 13.3 Modify OpenCode server session creation in `src/agentpool_server/opencode_server/session_pool_integration.py` to extract tenant_id from the `Authorization` header via `tenant_extractor`
- [ ] 13.4 Modify AG-UI server request handling in `src/agentpool_server/agui_server/server.py` to extract tenant_id from request headers via `tenant_extractor`
- [ ] 13.5 Modify OpenAI API server request handling in `src/agentpool_server/openai_api_server/server.py` to extract tenant_id from `Authorization: Bearer` header via `tenant_extractor`
- [ ] 13.6 Write unit tests: each server extracts correct tenant_id from auth token, no-token defaults to `"default"`, extracted tenant_id flows into `RunScope` and reaches `HostRegistry.get_or_create_validated()`
- [ ] 13.7 Write integration test: two ACP clients with different API keys get different Hosts, their sessions are isolated, MCP tools don't cross-access

## 14. AgentPool Multi-Tenant Support

- [ ] 14.1 Add `tenant_id: str = "default"` parameter to `AgentPool.__init__()` in `src/agentpool/delegation/pool.py` — stored as `self._tenant_id`, used to scope all pool operations
- [ ] 14.2 Modify `AgentPool.__aenter__()` to pass `tenant_id` to `StorageProvider` initialization and `HostContext` construction — storage queries auto-filter by this tenant_id
- [ ] 14.3 Implement `AgentPool.for_tenant(tenant_id: str) -> AgentPool` — returns a scoped view of the pool bound to the given tenant, backed by the correct Host from `HostRegistry` for `(config_id, tenant_id)`; lazily creates Host if not exists
- [ ] 14.4 Modify `AgentPool.get_agent()` to return agents compiled within the pool's tenant Host — agent's `HostContext.tenant_id` matches the pool's `tenant_id`
- [ ] 14.5 Verify `async with AgentPool("config.yml")` (no tenant_id) uses `tenant_id="default"` and behaves identically to pre-M5 — zero-config backward compatibility
- [ ] 14.6 Write unit tests: `AgentPool("config.yml", tenant_id="tenant-1")` scopes all operations to tenant-1, `for_tenant("tenant-2")` returns isolated view, `get_agent()` returns tenant-scoped agent, no tenant_id defaults to `"default"`, two pools with different tenants have isolated storage and MCP

## 15. AgentPool CLI: Multi-Tenant Configuration

- [ ] 15.1 Add `--tenant` flag to `agentpool serve-acp` CLI command — when provided, sets the default tenant_id for the server; when omitted, defaults to `"default"`
- [ ] 15.2 Add `--api-key-map` flag to `agentpool serve-acp` — accepts a JSON file mapping API keys to tenant_ids; configures `APIKeyTenantExtractor` on the server
- [ ] 15.3 Add `--jwt-secret` flag to `agentpool serve-acp` — when provided, configures `JWTTenantExtractor` with the secret for JWT verification
- [ ] 15.4 Apply the same `--tenant`, `--api-key-map`, and `--jwt-secret` flags to `serve-opencode`, `serve-agui`, and `serve-api` commands
- [ ] 15.5 Write unit tests for CLI argument parsing: `--tenant` sets default tenant, `--api-key-map` loads JSON and configures extractor, `--jwt-secret` configures JWT extractor, no flags defaults to `"default"` with `NoOpTenantExtractor`

## 16. Single-Tenant Default Compatibility Verification

- [ ] 16.1 Verify `async with AgentPool("config.yml")` creates a Host with `tenant_id="default"` and all storage queries filter by `tenant_id="default"` — behavior identical to pre-M5
- [ ] 16.2 Verify `agentpool run assistant "Hello"` works without any tenant configuration — uses `tenant_id="default"` throughout
- [ ] 16.3 Verify `agentpool serve-acp config.yml` starts without tenant flags — `NoOpTenantExtractor` returns `"default"`, all sessions use `"default"` tenant
- [ ] 16.4 Verify existing session_ids without tenant prefix are parsed as `tenant_id="default"` by `parse_session_id()` — backward compatibility for stored session references
- [ ] 16.5 Verify migrated database rows (task 9.2) with `tenant_id="default"` are returned correctly by storage queries — no data loss after migration
- [ ] 16.6 Write integration test: run full agent interaction with default tenant, verify storage records have `tenant_id="default"`, verify EventBus events use `"default:{session_uuid}"` session_ids, verify no isolation overhead beyond default filter

## 17. Integration Verification

- [ ] 17.1 Run full test suite: `uv run pytest` — all existing tests pass without modification (single-tenant default preserves behavior)
- [ ] 17.2 Run mypy: `uv run --no-group docs mypy src/agentpool/tenant/ src/agentpool/host/ src/agentpool_storage/` — no type errors
- [ ] 17.3 Run ruff: `uv run ruff check src/agentpool/tenant/ src/agentpool/host/ src/agentpool_storage/ src/agentpool_server/base.py` — no lint errors
- [ ] 17.4 Verify multi-tenant isolation end-to-end: two tenants with same config get different Hosts, different MCP processes, different storage queries, different EventBus subscriptions — no cross-tenant data leakage
- [ ] 17.5 Verify forged tenant_id rejection: request with `RunScope.tenant_id="tenant-2"` routed to tenant-1's Host raises `TenantMismatchError` at every layer boundary
- [ ] 17.6 Verify storage migration: existing database with pre-M5 data migrates cleanly, all records get `tenant_id="default"`, queries return correct results post-migration
- [ ] 17.7 Verify ACP multi-tenant: `agentpool serve-acp --api-key-map keys.json config.yml` serves multiple tenants, API key → tenant mapping works, sessions are isolated
- [ ] 17.8 Verify backward compatibility: `agentpool run assistant "Hello"` with no tenant flags works identically to pre-M5
