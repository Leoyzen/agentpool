## 1. Phase 1: MCP Source Extraction

### 1a. New Code

- [x] 1.1 Create `src/agentpool/capabilities/resource_protocols.py` with `SkillResource`, `McpResource`, `CommandResource`, `ChangeObservable` Protocol interfaces and `SkillEntry`, `ToolEntry`, `ToolResult`, `ResourceEntry`, `CommandEntry` dataclasses (NOT `ChangeEvent` — modify existing one in task 1.1b)
- [x] 1.1b Modify existing `src/agentpool/capabilities/change_event.py` — widen `kind` from `ChangeKind` (Literal) to `str`, add `source_uri: str = ""` field, retain `capability_name: str`. Keep `ChangeKind` Literal for known values. Do NOT create a duplicate `ChangeEvent` in `resource_protocols.py`.
- [x] 1.2 Create `src/agentpool/capabilities/mcp_server_cap.py` with `McpServerCap(AbstractCapability, McpResource, ChangeObservable)` — all methods delegate to `MCPClient` via `_ensure_client()`. SkillResource and CommandResource added in Phase 3 (tasks 3.1-3.2).
- [x] 1.3 Add `get_client(config: BaseMCPServerConfig) -> MCPClient` to `SessionConnectionPool` — wraps pooled transport, constructs `MCPClient` on transport, performs MCP handshake (initialize + capabilities exchange), pool retains ownership of transport. Client is constructed lazily on first `get_client()` call for a given config.
- [x] 1.4 Audit all `AgentContext.resources` usage sites — grep `AgentContext.resources` and `\.resources\.` across `src/`, document each call site. Note: Codebase analysis shows the field is SET at `run.py:394` but never READ (0 consumer call sites). This is a dead field — Phase 4 migration is simple removal, not a big-bang rewrite.
- [x] 1.5 Update `AgentFactory.compile()` to use `McpServerCap` instead of `MCPCapability` (keep `MCPCapability` as deprecated alias). Note: This is an intermediate step — `AgentFactory` will fully use `ExtensionRegistry` in Phase 4 (task 4.13).

### 1b. Old Code Migration

- [x] 1.6 Audit all `MCPCapability` import sites — grep `from.*import.*MCPCapability` and `MCPCapability(` across `src/`, migrate each to `McpServerCap` or use deprecated alias with `DeprecationWarning`
- [x] 1.7 Removed (merged with task 1.4 — duplicate audit)
- [x] 1.8 Verify `MCPCapability` deprecated alias emits `DeprecationWarning` on `__init__` with migration message pointing to `McpServerCap`

### 1c. Old Test Migration

- [x] 1.9 Audit test files referencing `MCPCapability` directly — grep `MCPCapability` in `tests/`. For each: rewrite to use `McpServerCap` with injected `MCPClient` mock, or keep using deprecated alias with `pytest.warns(DeprecationWarning)`
- [x] 1.10 Audit test files referencing `MCPCapability` internals (e.g., `_build_toolset`, `_client` attribute) — these tests break because `McpServerCap` has different internals. Rewrite to test via Protocol interface (`list_tools()`, `call_tool()`) instead of internal attributes

### 1d. New Tests

- [x] 1.11 Write unit tests for `McpServerCap` — delegation (list_tools, call_tool, list_resources, read_resource, resource_exists), lazy init (no connection at construct, connection on first list_tools), change notification mapping (tools/list_changed → ChangeEvent)
- [x] 1.12 Write unit tests for `SessionConnectionPool.get_client()` — client construction, MCP handshake, lazy init (no connection at construct, connection on first get_client call), transport reuse (same config → same transport)
- [x] 1.13 Write deprecation warning test — verify `MCPCapability.__init__()` emits `DeprecationWarning` with message containing "McpServerCap"
- [x] 1.14 Run existing MCP tests and verify all pass with `McpServerCap` replacing `MCPCapability` — `uv run pytest tests/ -k "mcp" -v`
- [x] 1.15 Write unit test for `SessionConnectionPool.get_client()` transport reuse — two `McpServerCap` instances with same config share one transport, two with different configs get separate transports
- [x] 1.16 Write integration test verifying `AgentContext.resources` field is unused — confirm no code reads `.resources.list()`, `.resources.read()`, `.resources.exists()`, or `.resources.on_change()` on `AgentContext` instances. This validates that Phase 4 removal is safe.

## 2. Phase 2: Skill Manager Extraction

### 2a. New Code

- [x] 2.1 Create `src/agentpool/capabilities/skill_manager_cap.py` with `SkillManagerCap(CombinedToolsetCapability, SkillResource, CommandResource, ChangeObservable)` — holds local skills as `dict[str, Skill]`, queries child `McpServerCap` for remote skills
- [x] 2.2 Implement `SkillManagerCap.get_instructions()` — returns `<available-skills>` XML metadata block (~100 tokens/skill)
- [x] 2.3 Implement `SkillManagerCap.before_model_request()` — calls optional `matcher_fn` to select 2-3 relevant skills, injects full instructions; `matcher_fn=None` injects all (backward compat)
- [x] 2.3b Implement `always_active` flag on `SkillManagerCap` — skills with `always_active: true` in their config skip the matcher and are always injected. Add the flag to `Skill` model or `SkillManagerCap` config, check in `before_model_request()` before calling `matcher_fn`.
- [x] 2.4 Implement `SkillManagerCap.list_skills()` / `read_skill()` / `skill_exists()` — aggregate local + remote (child `McpServerCap`) skills
- [x] 2.5 Implement `SkillManagerCap.list_commands()` / `get_command()` — aggregate local skill commands + remote MCP prompts; local takes precedence
- [x] 2.6 Move `SkillMcpManager` connection logic into `SkillManagerCap` child management (child `McpServerCap` via `SessionConnectionPool`)
- [x] 2.6b Move retry logic (3-retry exponential backoff) from `SkillMcpManager` to `McpServerCap._ensure_client()` — grep `retry` and `backoff` in `skills/skill_mcp_manager.py`, extract the logic, add to `McpServerCap._ensure_client()` with same parameters (3 retries, exponential backoff)
- [x] 2.7 Update `AgentFactory.compile()` to create `SkillManagerCap` instead of individual `SkillCapability` instances
- [x] 2.8 Deprecate `SkillsInstructionConfig.mode` — add deprecation warning in config parser when `mode:` is set in YAML. Stop reading the field at `pool.py:182` (pass `injection_mode=None` or remove the `SkillsTools(injection_mode=...)` argument). Do NOT delete the dataclass field yet — deletion happens in Phase 4 (task 4.26).
- [x] 2.9 Deprecate `SkillsToolsetConfig.injection_mode` at `toolsets.py:329` and `get_provider()` passing at `toolsets.py:361`. Add deprecation warning when the field is used. Stop storing `injection_mode` in `SkillsTools` class (`skills.py:574`) — accept the parameter but ignore it with a warning. Do NOT delete the field yet — deletion happens in Phase 4 (task 4.26).
- [x] 2.10 Keep `SkillCapability`, `SkillActivationCapability` as deprecated aliases (Phase 4 deletion)

### 2b. Old Code Migration

- [x] 2.11 Audit all `SkillCapability` import sites — grep `from.*import.*SkillCapability` and `SkillCapability(` across `src/`, migrate each to `SkillManagerCap` or use deprecated alias
- [x] 2.12 Audit all `SkillActivationCapability` import sites — grep `SkillActivationCapability` across `src/`, verify it's only wired but never called (orphaned), migrate wiring to `SkillManagerCap.before_model_request`
- [x] 2.13 Audit all `SkillMcpManager` usage sites — grep `SkillMcpManager` and `skill_mcp_manager` across `src/`, migrate each to `SkillManagerCap` child management + `SessionConnectionPool.get_client()`
- [x] 2.14 Audit `SkillsInstructionConfig.mode` usage — grep `\.mode` near `instruction` or `skill` config across `src/` and `tests/`, verify field is read at pool.py:182 but stored value never used for behavior. Document the read path: pool.py:182 → SkillsTools(injection_mode=...) → skills.py:574 stores but never reads it
- [x] 2.15 Verify `SkillCapability` and `SkillActivationCapability` deprecated aliases emit `DeprecationWarning` on `__init__` with migration message pointing to `SkillManagerCap`

### 2c. Old Test Migration

- [x] 2.16 Audit test files referencing `SkillCapability` directly — grep `SkillCapability` in `tests/`. For each: rewrite to use `SkillManagerCap` with local skills, or keep using deprecated alias with `pytest.warns(DeprecationWarning)`
- [x] 2.17 Audit test files referencing `SkillMcpManager` — grep `SkillMcpManager` in `tests/`. For each: rewrite to test `SkillManagerCap` child management via `SessionConnectionPool`, or delete if testing removed internals (e.g., 5min idle timeout, `_providers` dict structure)
- [x] 2.18 Audit test files referencing `SkillActivationCapability` — grep `SkillActivationCapability` in `tests/`. Rewrite to test `SkillManagerCap.before_model_request()` with `matcher_fn` instead
- [x] 2.19 Audit test files asserting on `SkillsInstructionConfig.mode` behavior — grep `mode` near `instruction` or `skill` in `tests/`. Delete tests for dead code, verify no test depends on `mode` field value. Also migrate `test_skills_injection.py` — grep `test_skills_injection` in `tests/`. Update tests that construct `SkillsInstructionConfig(mode="off")` and `SkillsToolsetConfig(injection_mode="full")` to reflect the field deletion. Reference test function names rather than line numbers.
- [x] 2.20 Audit test files referencing `injection_mode` or `SkillsTools` — grep `injection_mode` in `tests/`. Delete tests that assert on `injection_mode` field value. Also grep `test_skills_injection` and migrate those tests.

### 2d. New Tests

- [x] 2.21 Write unit tests for `SkillManagerCap` — instruction injection (metadata-only by default), matcher selection (2-3 skills), backward compat (matcher_fn=None injects all), always_active bypass, command aggregation (local + remote, local precedence), local+remote skill listing
- [x] 2.22 Write integration test: skill with embedded MCP server — verify tools from MCP are available alongside skill instructions, MCP child lifecycle (enter/exit), partial failure (MCP server fails, skill still works)
- [x] 2.23 Write deprecation warning tests — verify `SkillCapability.__init__()` and `SkillActivationCapability.__init__()` emit `DeprecationWarning` with message containing "SkillManagerCap"
- [x] 2.24 Run existing skill tests and verify all pass with `SkillManagerCap` replacing `SkillCapability` — `uv run pytest tests/ -k "skill" -v`

## 3. Phase 3: Cross-Provision (MCP as Skill/Command Source)

### 3a. New Code

- [x] 3.1 Add `SkillResource` to `McpServerCap` class declaration. Implement `McpServerCap.list_skills()` / `read_skill()` / `skill_exists()` — delegate to `MCPClient.list_resources()` / `read_resource()`, map MCP resources to `SkillEntry`
- [x] 3.2 Add `CommandResource` to `McpServerCap` class declaration. Implement `McpServerCap.list_commands()` / `get_command()` — delegate to `MCPClient.list_prompts()` / `get_prompt()`, map MCP prompts to `CommandEntry`
- [x] 3.3 Replace `SkillProvider` Protocol usage with `isinstance(cap, SkillResource)` checks in `SkillURIResolver` and pool.py
- [x] 3.4 Update `_rebuild_skill_capabilities()` (or equivalent) to iterate capabilities directly — `isinstance(cap, SkillResource)` check on compiled agent capabilities. Note: `ExtensionRegistry.get_skill_resources(scope)` is not available until Phase 4; in Phase 3, iterate the agent's compiled capability list directly.
- [x] 3.5 Wire MCP `notifications/tools/list_changed` → `ChangeEvent(kind="tools_changed")` → `SkillManagerCap` re-evaluates child `McpServerCap` skills
- [x] 3.6 Wire MCP `notifications/resources/list_changed` → `ChangeEvent(kind="resources_changed")` → `SkillManagerCap` re-evaluates remote skills

### 3b. Old Code Migration

- [x] 3.7 Audit all `SkillProvider` Protocol usage — grep `SkillProvider` and `isinstance.*SkillProvider` across `src/`, migrate each to `isinstance(cap, SkillResource)` check
- [x] 3.8 Audit `pool.py:570-600` skill registration logic — the `isinstance` check that always fails for `MCPCapability`, migrate to use `McpServerCap` with `SkillResource` implementation
- [x] 3.9 Audit `SkillCommandRegistry` dual-sync logic — grep `SkillCommandRegistry` and `initialize` across `src/`, verify MCP provider sync is replaced by `ExtensionRegistry.get_command_resources()`

### 3c. Old Test Migration

- [x] 3.10 Audit test files referencing `SkillProvider` — grep `SkillProvider` in `tests/`. Rewrite to use `SkillResource` Protocol instead
- [x] 3.11 Audit test files testing `SkillCommandRegistry` dual-sync — grep `SkillCommandRegistry` in `tests/`. Rewrite or delete tests for behavior that changes (MCP provider sync now via `ExtensionRegistry`)

### 3d. New Tests

- [x] 3.12 Write integration test: MCP-hosted skill visible via `skill://` URI resolution — MCP server provides resources, `SkillManagerCap.read_skill()` routes to `McpServerCap`
- [x] 3.13 Write integration test: MCP prompt accessible as slash command — MCP server provides prompts, `SkillManagerCap.get_command()` routes to `McpServerCap`
- [x] 3.14 Write integration test: MCP tool list change triggers skill re-evaluation — MCP server sends `notifications/tools/list_changed`, `ChangeEvent` propagates to `SkillManagerCap`, skill list updates without restart
- [x] 3.15 Write error scenario test: MCP server disconnects mid-session — `McpServerCap.call_tool()` raises connection error, error propagates to caller, other capabilities continue working
- [x] 3.16 Write deprecation warning test — verify `SkillProvider` isinstance check (if kept as alias) emits `DeprecationWarning`

## 4. Phase 4: ExtensionRegistry and Scoping

### 4a. New Code

- [x] 4.1 Create `src/agentpool/capabilities/extension_registry.py` with `ExtensionRegistry`, `Scope` (frozen dataclass), `ScopeLevel` (Enum: POOL/SESSION/AGENT/TURN)
- [x] 4.2 Implement `ExtensionRegistry.register()` / `unregister()` with 4-level scope storage and `asyncio.Lock` on turn-level dict
- [x] 4.3 Implement `ExtensionRegistry.get_visible_capabilities(scope)` — walks pool → session → agent → turn
- [x] 4.4 Implement typed query methods: `get_skill_resources()`, `get_mcp_resources()`, `get_command_resources()`, `get_observable_capabilities()`
- [x] 4.5 Implement `ExtensionRegistry.resolve_uri(uri, scope)` — routes by scheme (`skill://`, `mcp://`), uses `skill_exists()` / `resource_exists()` for cheap-check-first
- [x] 4.6 Implement `ExtensionRegistry.merge_change_streams(scope)` — sentinel-based merge, `logger.warning()` on exceptions, `None` when no observables
- [x] 4.7 Implement cycle detection at `add_child()` registration time — raise `CircularCompositionError`
- [x] 4.8 Implement composition depth limit (configurable `extensions.max_composition_depth`, default 3, root-inclusive, warning not block)
- [x] 4.9 Add `extension_registry: ExtensionRegistry | None = None` to `HostContext` for session-scoped capabilities
- [x] 4.9b Modify `AgentContext` (`src/agentpool/capabilities/agent_context.py:46`) — add `extension_registry: ExtensionRegistry | None = None` field, deprecate `resources: ResourceSource | None = None` field (keep during transition, remove in task 4.23)
- [x] 4.10 Add pool-level `ExtensionRegistry` to `AgentPool` for global capabilities
- [x] 4.11 Add `watchdog` filesystem watcher for skill hot-reload — 500ms debounce, triggers `on_change()` → `ChangeEvent`

### 4b. Old Code Migration

- [x] 4.12 Migrate `SkillURIResolver._providers` dict to `ExtensionRegistry.resolve_uri()` — grep `SkillURIResolver` and `_providers` across `src/`, replace provider registration with registry URI routing
- [x] 4.13 Migrate `AggregatedResourceSource` construction to `ExtensionRegistry.get_visible_capabilities()` — grep `AggregatedResourceSource` across `src/`, replace manual construction with registry query
- [x] 4.14 Remove `AgentContext.resources` dead field — grep `AgentContext.resources` across `src/`, remove the field from `AgentContext` dataclass (`agent_context.py:46`), remove the assignment at `run.py:394`, remove `_resource_source` from `RunHandle`, remove `_collect_resource_sources()` from `AgentFactory` if no longer used. Note: Codebase analysis confirmed 0 read call sites — this is a simple removal, not a migration.
- [x] 4.15 Audit `SkillCommandRegistry` call sites in protocol servers — grep `SkillCommandRegistry` in `src/agentpool_server/`, migrate to `ExtensionRegistry.get_command_resources()`
- [x] 4.16 Audit `SkillMcpManager` call sites — grep `SkillMcpManager` in `src/agentpool_server/` and `src/agentpool/`, verify all migrated to `SkillManagerCap` + `SessionConnectionPool` (Phase 2 should have done most, this is verification)

### 4c. Deletions

Note: Tasks within 4c should be executed in order — 4.17 through 4.26. Task 4.24b (ResourceSource deletion) must come after 4.14 (AgentContext.resources removal) since code still references `ResourceSource` interface until the field is removed.

- [x] 4.17 Delete `SkillCommandRegistry` — replaced by `ExtensionRegistry.get_command_resources()`
- [x] 4.18 Delete `SkillMcpManager` — replaced by `SkillManagerCap` child management + `SessionConnectionPool`
- [x] 4.19 Delete `SkillProvider` Protocol — subsumed by `SkillResource`
- [x] 4.20 Delete `SkillCapability` deprecated alias
- [x] 4.21 Delete `SkillActivationCapability` deprecated alias
- [x] 4.22 Delete `MCPCapability` deprecated alias — grep `MCPCapability` across `src/` and `tests/`, verify zero remaining references (all migrated to `McpServerCap` in Phase 1), then delete the alias file/class
- [x] 4.23 Verify `AgentContext.resources` fully removed — grep `AgentContext.resources` across `src/`, verify zero remaining references to the removed field. Verify `AgentContext.extension_registry` is set at the same location where `resources` was previously set.
- [x] 4.24 Delete `SkillCommand` dataclass — grep `SkillCommand` across `src/`, verify `SkillCommandRegistry` (task 4.17) and all callers deleted, `SkillCommand` is now dead code, delete it
- [x] 4.24b Delete `ResourceSource` Protocol (`src/agentpool/capabilities/resource_source.py:85`) — grep `ResourceSource` across `src/`, verify all implementations migrated to domain-specific Protocols, delete the Protocol. Also delete `AggregatedResourceSource` class if no longer used.
- [x] 4.24c Audit `SkillToolManager` (`src/agentpool/skills/skill_tool_manager.py`) for deletion — grep `SkillToolManager` across `src/`. If all tool import logic migrated to `SkillManagerCap`, delete it. If still needed for eager Python tool import, keep with a docstring explaining why.
- [x] 4.25 Evaluate `SkillURIResolver` for deletion or simplification — grep `SkillURIResolver` across `src/`. If `resolve_uri()` fully handled by `ExtensionRegistry.resolve_uri()` (task 4.5), delete `SkillURIResolver` entirely. If still needed for fuzzy matching (`_` ↔ `-`), simplify to a thin utility function delegated from `ExtensionRegistry`
- [x] 4.26 Delete `SkillsInstructionConfig.mode` field and `SkillsToolsetConfig.injection_mode` field — grep `injection_mode` and `SkillsInstructionConfig.mode` across `src/`, delete the dataclass fields (Phase 2 task 2.8/2.9 only deprecated them). Also delete `SkillsTools.injection_mode` property/setter if any remaining. Verify deprecation warnings from Phase 2 are removed (no longer needed since fields are gone).

### 4d. Old Test Migration

- [x] 4.27 Audit test files referencing `SkillCommandRegistry` — grep `SkillCommandRegistry` in `tests/`. Delete tests for removed component, rewrite any testing command discovery to use `ExtensionRegistry.get_command_resources()`
- [x] 4.28 Audit test files referencing `SkillMcpManager` — grep `SkillMcpManager` in `tests/`. Delete tests for removed component, rewrite any testing MCP lifecycle to use `SkillManagerCap` + `SessionConnectionPool`
- [x] 4.29 Audit test files referencing `AggregatedResourceSource` — grep `AggregatedResourceSource` in `tests/`. Rewrite to use `ExtensionRegistry.get_visible_capabilities()` instead
- [x] 4.30 Audit test files referencing `SkillURIResolver._providers` — grep `_providers` near `SkillURIResolver` in `tests/`. Rewrite to use `ExtensionRegistry.resolve_uri()`
- [x] 4.31 Audit test files referencing `MCPCapability` — grep `MCPCapability` in `tests/`. Delete any remaining tests using the deprecated alias (should have been migrated in Phase 1, this is final cleanup)
- [x] 4.32 Audit test files referencing `AgentContext.resources` — grep `AgentContext.resources` in `tests/`. Delete tests for the removed field, rewrite any testing resource access to use `AgentContext.extension_registry` instead
- [x] 4.33 Audit test files referencing `SkillCommand` — grep `SkillCommand` in `tests/`. Delete tests for removed dataclass, rewrite any testing command behavior to use `CommandEntry` instead
- [x] 4.34 Verify no remaining references to deleted components — grep `SkillCommandRegistry|SkillMcpManager|SkillProvider|SkillCapability|SkillActivationCapability|MCPCapability|SkillCommand` across `tests/`. All should return zero matches (except historical changelog/RFC references)

### 4e. New Tests

- [x] 4.35 Write unit tests for `ExtensionRegistry` — scope isolation (pool visible to all, session isolated, turn cleaned up), URI routing (skill://, mcp://, unknown scheme), change stream merging (two streams, exception in one, no observables), cycle detection (A→B→A raises), depth limit (warning at depth 4)
- [x] 4.36 Write concurrency tests — concurrent turn-level registration (asyncio.Lock prevents corruption), concurrent `get_visible_capabilities()` reads (lock-free, no deadlock), concurrent `register()` + `get_visible_capabilities()` (no stale reads)
- [x] 4.37 Write integration test: session-level skill scoping — session 1 has skill A, session 2 has skill B, neither sees the other's skills
- [x] 4.38 Write integration test: filesystem watcher detects new skill without restart — create new SKILL.md, verify `ChangeEvent` fires after 500ms debounce, verify skill appears in `list_skills()`
- [x] 4.39 Write error scenario test: skill file corruption — malformed SKILL.md, verify `SkillManagerCap.list_skills()` skips corrupted skill with warning, other skills still listed
- [x] 4.40 Write error scenario test: MCP server timeout — `McpServerCap.call_tool()` times out, verify retry with exponential backoff (3 retries), verify error propagates after retries exhausted
- [x] 4.41 Run full test suite and verify all tests pass — `uv run pytest -v`

## 5. Verification

### 5a. Structural Problem Verification (P1-P7)

- [ ] 5.1 Verify P1 (SkillCapability in AggregatedResourceSource) — `AgentContext.resources.list()` (or `extension_registry.get_skill_resources()`) includes skill content from `SkillManagerCap`
- [ ] 5.2 Verify P2 (MCPCapability as SkillProvider) — `skill://` URIs resolve to MCP-hosted skills via `McpServerCap.read_skill()`
- [ ] 5.3 Verify P3 (MCP skills get capabilities) — MCP-sourced skills have instruction injection via `SkillManagerCap`
- [ ] 5.4 Verify P4 (Change notification chain) — MCP tool changes trigger skill re-evaluation without restart, `ChangeEvent` propagates through `merge_change_streams()`
- [ ] 5.5 Verify P5 (Unified connection path) — no duplicate MCP connections, single `SessionConnectionPool.get_client()` path, grep for `SkillMcpManager` returns zero matches in `src/`
- [ ] 5.6 Verify P6 (Session-level scoping) — sessions have isolated skill sets, `get_visible_capabilities()` with different `scope.session_id` returns different capabilities
- [ ] 5.7 Verify P7 (Filesystem watcher) — new skills discovered without restart, `watchdog` observer active

### 5b. Code Quality

- [ ] 5.8 Run `ruff check src/` — zero errors on changed files
- [ ] 5.9 Run `mypy src/` — zero errors on changed files
- [ ] 5.10 Run `ruff format --check src/` — all changed files formatted correctly
- [ ] 5.11 Verify no `as any`, `@ts-ignore`, or type suppressions in changed files

### 5c. Test Coverage

- [ ] 5.12 Run `uv run pytest --cov-report=term-missing --cov=src/agentpool/capabilities/` — new capability files ≥ 80% coverage
- [ ] 5.13 Run `uv run pytest --cov-report=term-missing --cov=src/agentpool/skills/` — modified skill files ≥ 80% coverage
- [ ] 5.14 Verify all deprecation warnings tested — grep `DeprecationWarning` in `tests/`, each deprecated alias has a corresponding `pytest.warns(DeprecationWarning)` test

### 5d. Full Suite

- [ ] 5.15 Run full test suite `uv run pytest` — all tests pass (or only pre-existing failures remain, documented)
- [ ] 5.16 Run `uv run pytest -m unit` — all unit tests pass
- [ ] 5.17 Run `uv run pytest -m integration` — all integration tests pass
- [ ] 5.18 Verify no deleted component referenced — grep `SkillCommandRegistry|SkillMcpManager|SkillProvider|SkillCapability|SkillActivationCapability|MCPCapability|SkillCommand|SkillURIResolver._providers|ResourceSource|AggregatedResourceSource` across `src/` and `tests/`, all return zero matches (except historical changelog/RFC references)
