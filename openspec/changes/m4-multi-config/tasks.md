## 1. Config Split: HostConfig and AgentManifest

- [ ] 1.1 Create `src/agentpool_config/host.py` defining `HostConfig` as a `Schema` subclass containing infrastructure fields: `mcp_servers`, `storage`, `observability`, `skills`, `protocol`, `models` (typed as `ModelLayerConfig` from task group 2), `resources`, `session_pool`, `prompts`, `compaction`, `context`, `converters`
- [ ] 1.2 Create `src/agentpool_config/agent_manifest.py` defining `AgentManifest` as a `Schema` subclass containing agent definition fields: `agents`, `teams`, `graph`, `responses`, `commands`, `jobs`, `INHERIT`, `include_packages`, `name`
- [ ] 1.3 Add `HostConfig.model_config` with `extra="forbid"` to catch unknown host-section keys
- [ ] 1.4 Add `AgentManifest.model_config` with `extra="forbid"` to catch unknown agent-section keys
- [ ] 1.5 Write unit tests for `HostConfig`: construction from dict, field types, extra-field rejection, default values
- [ ] 1.6 Write unit tests for `AgentManifest`: construction from dict, field types, extra-field rejection, default values

## 2. Config Split: Flat YAML Auto-Migration

- [ ] 2.1 Add a `model_validator(mode="before")` on `AgentsManifest` that detects whether the input dict has a `host:` key — if not, partitions the flat dict into `host` (infrastructure fields) and agent fields automatically
- [ ] 2.2 Define the partition mapping: infrastructure fields list = `["mcp_servers", "storage", "observability", "skills", "protocol", "models", "resources", "session_pool", "prompts", "compaction", "context", "converters"]`; remaining fields go to agent section
- [ ] 2.3 Add `.host_config: HostConfig` and `.agent_manifest: AgentManifest` computed properties on `AgentsManifest` that return the split views
- [ ] 2.4 Write unit tests for flat YAML migration: flat config produces correct HostConfig + AgentManifest, explicit `host:` section passes through unchanged, mixed flat + `host:` raises validation error
- [ ] 2.5 Write unit tests verifying all existing example configs in `site/examples/*/config.yml` still parse without error via the auto-migration validator

## 3. Model Config: Three-Layer Models

- [ ] 3.1 Create `src/agentpool_config/models.py` defining `ProviderConfig` (fields: `api_key`, `base_url`, `timeout`, `max_retries`, `extra_headers`) keyed by provider name (`openai`, `anthropic`, `google`, etc.)
- [ ] 3.2 Define `ModelAlias` as a `Schema` with `model: str | list[str]` (concrete string or fallback chain) and optional `description: str`
- [ ] 3.3 Define `ModelLayerConfig` as a `Schema` with `providers: dict[str, ProviderConfig]`, `aliases: dict[str, ModelAlias]`, and `default_model: str | None`
- [ ] 3.4 Add `ModelLayerConfig` to `HostConfig.models` field (created in task 1.1) with `default_factory=ModelLayerConfig`
- [ ] 3.5 Write unit tests for `ProviderConfig`: construction, optional fields, extra-header dict
- [ ] 3.6 Write unit tests for `ModelAlias`: single model string, fallback chain list, serialization round-trip

## 4. Model Config: Alias Resolver and ModelCache

- [ ] 4.1 Create `src/agentpool/models/alias_resolver.py` defining `resolve_alias(alias_name: str, aliases: dict[str, ModelAlias], _seen: set[str] | None = None) -> str | list[str]` with transitive resolution and cycle detection raising `ModelAliasCycleError`
- [ ] 4.2 Write unit tests for alias resolver: simple resolution, transitive chain (a→b→c), fallback chain passthrough, cycle detection raises `ModelAliasCycleError` with cycle path in message, unknown alias returns input unchanged
- [ ] 4.3 Create `src/agentpool/models/model_cache.py` defining `ModelCache` class with `get_or_create(model_string: str, provider_config: ProviderConfig | None = None) -> Model`, internal `dict[str, Model]` cache, and `clear()` method
- [ ] 4.4 Implement `ModelCache.get_or_create()`: check cache by resolved model string, if miss instantiate pydantic-ai `Model` via existing model factory, apply provider config (api_key, base_url, timeout), store and return
- [ ] 4.5 Write unit tests for `ModelCache`: same model string returns same instance, different strings return different instances, `clear()` empties cache, provider config applied to created Model

## 5. ConfigRegistry: Versioned Storage and Lookup

- [ ] 5.1 Create `src/agentpool/config/registry.py` defining `ConfigRegistry` class with internal `dict[str, _ConfigEntry]` where `_ConfigEntry` holds `config: AgentsManifest`, `version: int`, `source: Path | str | None`
- [ ] 5.2 Implement `ConfigRegistry.register(config_id: str, source: Path | str | AgentsManifest) -> int` — loads/parses if string/path, stores with version=1 (or increments if exists), returns new version
- [ ] 5.3 Implement `ConfigRegistry.get(config_id: str) -> AgentsManifest` — returns current parsed config, raises `KeyError` for unknown config_id
- [ ] 5.4 Implement `ConfigRegistry.get_version(config_id: str) -> int` — returns current version number
- [ ] 5.5 Implement `ConfigRegistry.list_configs() -> list[str]` — returns all registered config IDs
- [ ] 5.6 Implement `ConfigRegistry.unregister(config_id: str) -> None` — removes config, stops file watching (if active), notifies listeners with `new_config=None`; raises `KeyError` for unknown
- [ ] 5.7 Write unit tests for ConfigRegistry storage: register from path, register from YAML string, register from parsed manifest, version increments on re-register, get/list/unregister, KeyError on unknown config

## 6. ConfigRegistry: File Watching and Hot-Reload Notifications

- [ ] 6.1 Implement `ConfigRegistry._start_watching(config_id: str, path: Path) -> None` using `watchfiles` (or equivalent async file watcher) to monitor the config file for modifications
- [ ] 6.2 Implement debounced reload: accumulate file-change events within a 500ms window (configurable via `debounce_ms` constructor param), then trigger a single reload
- [ ] 6.3 Implement `ConfigRegistry._reload(config_id: str) -> None` — re-reads file, re-parses, increments version, invokes registered callbacks
- [ ] 6.4 Implement `ConfigRegistry.on_change(config_id: str, callback: Callable[[str, AgentsManifest | None, AgentsManifest | None, int], Awaitable[None] | None]) -> None` — registers a listener for config changes
- [ ] 6.5 Implement callback dispatch: invoke all registered callbacks in registration order, catch and log exceptions from individual callbacks so subsequent callbacks still run
- [ ] 6.6 Write unit tests for file watching: file modification triggers reload + version increment, debounce coalesces rapid changes, multiple subscribers all notified, callback exception doesn't block subsequent callbacks, unregister stops watching

## 7. RunScope: Frozen Dataclass and Validation

- [ ] 7.1 Create `src/agentpool/run_scope.py` defining `RunScope` as `@dataclass(frozen=True)` with fields: `config_id: str = "default"`, `tenant_id: str = "default"`, `user_id: str = "anonymous"`, `session_id: str = field(default_factory=lambda: uuid4().hex)`
- [ ] 7.2 Define `ConfigNotFoundError(Exception)` with `config_id` attribute and descriptive message
- [ ] 7.3 Implement `RunScope.validate(config_registry: ConfigRegistry) -> None` — checks `config_id` is registered (raises `ConfigNotFoundError` if not), checks `tenant_id` is non-empty (raises `ValueError` if empty)
- [ ] 7.4 Write unit tests for RunScope: immutability raises `FrozenInstanceError`, default values, `validate()` passes for registered config, `validate()` raises `ConfigNotFoundError` for unknown config_id, `validate()` raises `ValueError` for empty tenant_id

## 8. RunScope: Protocol Server Extraction

- [ ] 8.1 Create a helper `extract_run_scope(metadata: dict[str, str], session_id: str | None = None) -> RunScope` that pulls `config_id`, `tenant_id`, `user_id` from a metadata dict with defaults
- [ ] 8.2 Modify ACP server `initialize` handler to extract `config_id`/`tenant_id`/`user_id` from request metadata and construct `RunScope`, storing it in session context
- [ ] 8.3 Modify OpenCode server session creation to extract `RunScope` from connection headers/params
- [ ] 8.4 Modify AG-UI server request handling to extract `RunScope` from request headers
- [ ] 8.5 Modify OpenAI API server request handling to extract `RunScope` from request headers or URL path params
- [ ] 8.6 Write unit tests for `extract_run_scope`: all fields from metadata, defaults when missing, session_id auto-generated when not provided
- [ ] 8.7 Write integration tests for ACP server: `initialize` with metadata produces correct RunScope stored in session, `initialize` without metadata produces default RunScope

## 9. HostRegistry: Lazy Create and Cache

- [ ] 9.1 Create `src/agentpool/host/registry.py` defining `HostRegistry` class with `__init__(self, config_registry: ConfigRegistry)`, internal `_cache: dict[tuple[str, str], AgentHost]`, and `_locks: dict[tuple[str, str], asyncio.Lock]`
- [ ] 9.2 Implement `HostRegistry.get_or_create(config_id: str, tenant_id: str) -> AgentHost` — returns cached Host or creates new one: load config from ConfigRegistry, init infrastructure (MCP, storage, skills), build HostContext with `config_id`/`tenant_id`, compile agents via AgentFactory, cache and return
- [ ] 9.3 Implement per-key locking: concurrent `get_or_create` for the same key serializes (second call awaits first), concurrent calls for different keys proceed without blocking
- [ ] 9.4 Implement `HostRegistry.get(config_id: str, tenant_id: str) -> AgentHost | None` — returns cached Host or `None` without creating
- [ ] 9.5 Write unit tests for HostRegistry: first access creates Host, subsequent access returns cached, different configs get different Hosts, same config different tenants get isolated Hosts, concurrent same-key creates only one Host

## 10. HostRegistry: Eviction and Config Change Reaction

- [ ] 10.1 Implement `HostRegistry.evict(config_id: str, tenant_id: str, timeout: float = 30.0) -> None` — marks Host as draining, rejects new session requests, waits for active sessions to complete up to timeout, then cancels remaining sessions and cleans up infrastructure
- [ ] 10.2 Implement drain logic: check Host's active session count, `await` with timeout, on timeout cancel active sessions gracefully, remove from cache, call `host.cleanup()`
- [ ] 10.3 Implement `HostRegistry.subscribe_to_config_changes() -> None` — registers a callback with ConfigRegistry that receives `(config_id, new_config, old_config, version)` and triggers appropriate reload on all Hosts using that config
- [ ] 10.4 Implement config change diff: compare `old_config.host_config` vs `new_config.host_config` — if HostConfig changed, call `host.reload()` on all affected Hosts; if only AgentManifest changed, call `factory.recompile()` on all affected Hosts
- [ ] 10.5 Write unit tests for eviction: no active sessions evicts immediately, active sessions wait then evict, timeout cancels sessions, new requests rejected during drain
- [ ] 10.6 Write unit tests for config change reaction: AgentManifest-only change triggers `factory.recompile()` not `host.reload()`, HostConfig change triggers `host.reload()`, both changes trigger `host.reload()`

## 11. AgentFactory: AgentManifest Input and Model Resolution

- [ ] 11.1 Modify `AgentFactory.compile()` signature to accept `AgentManifest` (not full `AgentsManifest`) as first parameter — infrastructure handles come from `HostContext`, not the manifest
- [ ] 11.2 Update `AgentFactory.compile()` internals: replace `manifest.mcp_servers` lookups with `host_context.mcp`, `manifest.storage` with `host_context.storage`, etc.
- [ ] 11.3 Implement model resolution in compile: for each agent, check if `agent.model` is a registered alias in `host_context.model_aliases` — if yes, resolve via `resolve_alias()`; if no, treat as direct model string
- [ ] 11.4 Fetch resolved model from `host_context.model_cache.get_or_create(resolved_string, provider_config)` instead of creating a new Model instance per agent
- [ ] 11.5 Write unit tests: compile with AgentManifest produces correct agents, infrastructure sourced from HostContext not manifest, alias resolution during compile, direct model string bypasses alias, shared Model instance for same resolved string

## 12. AgentFactory: Diff-Based Recompile

- [ ] 12.1 Implement `AgentFactory.recompile(new_manifest: AgentManifest, host_context: HostContext) -> AgentRegistry` — compares `new_manifest` with `self._last_manifest`, identifies changed/added/removed agents by name
- [ ] 12.2 Implement agent config diff: serialize each agent config to a comparable form (dict/hash), compare old vs new per agent name — only recreate agents whose config changed
- [ ] 12.3 Implement added agents: create new agent instances and add to existing registry; removed agents: remove from registry and clean up; unchanged agents: preserve from previous registry
- [ ] 12.4 Update `self._last_manifest` and `self._last_registry` after recompile
- [ ] 12.5 Write unit tests for recompile: changed agent only recreates that agent, no changes returns same registry, added agent creates new entry, removed agent cleans up, multiple changes handled in one pass

## 13. AgentHost

- [ ] 13.1 Create `src/agentpool/host/agent_host.py` defining `AgentHost` class with fields: `host_context` (HostContext), `factory` (AgentFactory), `registry` (AgentRegistry), `config_id` (str), `tenant_id` (str), `model_cache` (ModelCache)
- [ ] 13.2 Implement `get_agent(name: str) -> MessageNode` — delegates to `registry.get(name)`, raises `KeyError` if not found
- [ ] 13.3 Implement `async reload() -> None` — stops MCP processes, reconnects storage, reloads skills, calls `factory.recompile()` with new HostContext
- [ ] 13.4 Implement `async cleanup() -> None` — drains active sessions (with configurable timeout), stops MCP processes, closes storage connections, clears registry
- [ ] 13.5 Implement `validate_tenant(tenant_id: str) -> None` — raises `TenantMismatchError` if `tenant_id != self.tenant_id`
- [ ] 13.6 Write unit tests for AgentHost: construction, `get_agent` success and `KeyError`, `reload` with new config, `cleanup` with drain timeout, `validate_tenant` pass/fail

## 14. HostContext and AgentPool Modifications

- [ ] 14.1 Add `model_cache: ModelCache` field to `HostContext` (replacing the M1 stub) — initialized per-Host, not shared across Hosts
- [ ] 14.2 Add `model_aliases: dict[str, ModelAlias]` field to `HostContext` — populated from `HostConfig.models.aliases` during Host construction
- [ ] 14.3 Add `tenant_id: str = "default"` field to `HostContext` (if not already present from M1) — populated from `RunScope.tenant_id` when created through HostRegistry
- [ ] 14.4 Implement `HostContext` reconstruction on `Host.reload()`: build new HostContext with updated infrastructure handles, preserve `config_id` and `tenant_id` from previous context, replace old context atomically
- [ ] 14.5 Implement `AgentPool.from_registry(registry: ConfigRegistry, config_id: str = "default") -> AgentPool` classmethod — retrieves config from registry, initializes infrastructure from HostConfig, constructs HostContext with `config_id`, compiles agents via AgentFactory
- [ ] 14.6 Verify `async with AgentPool("config.yml")` still works: internally creates ConfigRegistry, registers file with `config_id="default"`, delegates to `from_registry()`
- [ ] 14.7 Write unit tests: `from_registry` produces correct HostContext with config_id, file-path constructor preserves all behavior, HostContext model_cache is per-Host scoped, HostContext reconstruction preserves config_id/tenant_id

## 15. Hot Reload: Triggers and Turn-Level Snapshot

- [ ] 15.1 Implement turn-level config version snapshot: capture `config_version` from ConfigRegistry at turn start, store in `AgentRunContext`, ensure the entire turn runs against that version even if a reload occurs mid-turn
- [ ] 15.2 Implement `AgentHost.reload()` method: tear down infrastructure (MCP processes, storage connections, skill registries), rebuild from new HostConfig, construct new HostContext, trigger `factory.recompile()` with new AgentManifest, swap in atomically
- [ ] 15.3 Ensure active turns continue using the old HostContext snapshot during reload — new turns use the new HostContext
- [ ] 15.4 Write unit tests: turn-level snapshot prevents mid-turn inconsistency, `host.reload()` creates new HostContext, active turn uses old context, post-reload turn uses new context

## 16. Multi-Config CLI

- [ ] 16.1 Modify `agentpool serve-acp` command to accept multiple positional config file paths (`config-a.yml config-b.yml`) in addition to single file
- [ ] 16.2 Add `--name` flag to CLI serve commands: `--name prod config-a.yml --name staging config-b.yml` — maps each config to a named config_id
- [ ] 16.3 Implement config_id derivation when `--name` not provided: use filename stem (e.g., `config-a.yml` → `config-a`)
- [ ] 16.4 Update serve command internals: create ConfigRegistry, register all configs, create HostRegistry, start protocol server with RunScope-based routing to HostRegistry
- [ ] 16.5 Apply the same multi-config pattern to `serve-opencode`, `serve-agui`, `serve-api`, and `serve-mcp` commands
- [ ] 16.6 Write unit tests for CLI argument parsing: single config (backward compat), multiple configs, `--name` flag pairs with configs, missing `--name` derives from filename
- [ ] 16.7 Write integration test: `agentpool serve-acp config-a.yml config-b.yml` registers both configs, HostRegistry creates separate Hosts, requests route to correct Host by config_id

## 18. OpenCode Server Hardening (from pre-M4 cleanup)

These tasks were merged from the `pre-m4-protocol-cleanup` change (formerly Phase 3 + Phase 5) because they touch the same OpenCode route files that M4's RunScope routing modifies. Doing them together avoids two rounds of changes to the same files.

### 18.1 Public API for Private Attributes

- [ ] 18.1 Add public API methods to replace private attribute access:
  - `Agent.get_capabilities() -> list[AbstractCapability]` (replaces `agent._all_capabilities`)
  - `Agent.get_all_tools() -> list[Tool]` (make `_get_all_tools` public)
  - `SessionController.get_session(session_id) -> SessionState | None` (replaces `session_controller._sessions[id]`)
  - `LspManager.get_server(name) -> LspServer | None` (replaces `lsp_manager._servers[name]`)
  - `SessionPool.get_runs(session_id) -> dict` (replaces `session_pool.sessions._runs`)
  Verify: `grep -rn '_all_capabilities\|_get_all_tools\|_sessions\[' src/agentpool_server/opencode_server/` returns 0.

### 18.2 state.pool Migration to HostContext

- [ ] 18.2 Migrate 68 `state.pool.*` accesses to `state.host_context.*` across OpenCode server routes. Key clusters: `session_pool` (~40), `manifest` (8), `todos` (5), `skill_*` (10), `storage` (3), `file_ops` (6), `extension_registry` (2). Add missing accessors on `HostContext` as needed. Verify: `grep -rn 'state\.pool\.' src/agentpool_server/opencode_server/` returns 0.

### 18.3 Event Processor Hardening

- [ ] 18.3 Handle `RunStartedEvent` in `EventProcessor._handle_event()` — emit `SessionStatusEvent(status="busy")`. Currently only handled in `session_pool_integration.py:1132`.
- [ ] 18.4 Remove `typing.Any` propagation in OpenCode event adapter. Type all event fields explicitly.

### 18.4 Legacy Path Removal

- [ ] 18.5 Remove dual abort paths in `session_routes.py`: delete legacy `state.agent.interrupt()` fallback at lines 936-947. All aborts go through `SessionController.abort()`. Also remove `state.agent.run()` bypass at line 1954.
- [ ] 18.6 Remove legacy `agent.list_sessions()` fallback at `session_routes.py:660-665`. All session listing goes through `SessionPool`.

### 18.5 Session/Pool Identity Abstraction

- [ ] 18.7 Abstract session identity from `state.agent.name` to `run_scope.session_id` in 5 OpenCode files: `message_routes.py:94`, `session_routes.py:86,868`, `server.py:267`, `session_pool_integration.py:415`. Uses `RunScope` from task group 7.
- [ ] 18.8 Abstract pool identity from `config_file_path` to `run_scope.config_id` in 3 files: `server.py:268`, `session_routes.py:867`, `session_pool_integration.py:414`. Uses `RunScope` from task group 7.
- [ ] 18.9 Remove single-config hardcoding in `session_controller.py`: replace `self.pool.manifest.agents` at lines 436, 442, 502, 964 with `host_context.manifest.agents` or `host_registry.get_agents(config_id)`.
- [ ] 18.10 Abstract `state.agent.env` access through `HostContext`: `state.agent.env.cwd` (8 occurrences in `agent_routes.py`), `state.agent.env.get_pty_manager()` (`pty_routes.py:54`).

## 17. Integration Verification

- [ ] 17.1 Run full test suite: `uv run pytest` — all tests must pass without modification to existing tests
- [ ] 17.2 Run mypy: `uv run --no-group docs mypy src/agentpool/config/ src/agentpool/run_scope.py src/agentpool/models/alias_resolver.py src/agentpool/models/model_cache.py src/agentpool/host/` — no type errors
- [ ] 17.3 Run ruff: `uv run ruff check src/agentpool/config/ src/agentpool/run_scope.py src/agentpool/models/alias_resolver.py src/agentpool/models/model_cache.py src/agentpool/host/ src/agentpool_config/host.py src/agentpool_config/agent_manifest.py src/agentpool_config/models.py` — no lint errors
- [ ] 17.4 Verify backward compatibility: `agentpool run assistant "Hello"` works with existing flat YAML configs (auto-migration)
- [ ] 17.5 Verify single-config ACP server: `agentpool serve-acp config.yml` starts and handles requests (no RunScope → defaults)
- [ ] 17.6 Verify multi-config ACP server: `agentpool serve-acp config-a.yml config-b.yml` serves both, requests with `config_id` header route to correct Host
- [ ] 17.7 Verify hot reload: modify a config file while server is running, confirm agent definition change triggers recompile without infrastructure restart, confirm infrastructure change triggers full Host reload
- [ ] 17.8 Verify `AgentPool.from_registry()` standalone: construct AgentPool from ConfigRegistry reference without file path, confirm agents are accessible and functional
