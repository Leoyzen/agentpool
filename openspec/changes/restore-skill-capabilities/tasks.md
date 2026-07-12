## 1. Fix skill:// URI Resolution

- [x] 1.1 Register `SkillManagerCap` with `ExtensionRegistry` at `ScopeLevel.POOL` in `_rebuild_skill_capabilities()` (pool.py)
- [x] 1.2 Unregister old `SkillManagerCap` from `ExtensionRegistry` before registering new one on rebuild
- [x] 1.3 Verify `SkillURIResolver.resolve()` → `ExtensionRegistry.resolve_uri()` → `SkillManagerCap.read_skill()` chain works end-to-end
- [ ] 1.4 Add end-to-end test: `skill://` URI resolution through pool initialization → skill registration → URI resolution

## 2. Restore Per-Skill Python Tool Registration

- [x] 2.1 Add `tool_manager: SkillToolManager | None = None` parameter to `SkillManagerCap.__init__()`
- [x] 2.2 Implement `_import_skill_tools()` private method: iterate `local_skills`, call `tool_manager.import_tools(skill.tools)` for skills with `tools` frontmatter, store as `_skill_tools: dict[str, list[Tool]]`
- [x] 2.3 Add `_skill_mcp_children: dict[str, list[McpServerCap]]` data structure to `SkillManagerCap` for tracking per-skill MCP children (needed by get_toolset override in 2.4)
- [x] 2.4 Fully override `get_toolset()` in `SkillManagerCap` (do NOT call `super().get_toolset()`). The override SHALL: (a) create `PrefixedToolset("{skill_name}__tool__")` for each skill with Python tools, (b) create `PrefixedToolset("{skill_name}__mcp__")` for each per-skill McpServerCap child (from `_skill_mcp_children`), (c) include non-skill children from `_capabilities` (excluding skill children) unprefixed, (d) combine all into `CombinedToolset`
- [x] 2.5 Update `_rebuild_skill_capabilities()` in pool.py to create/pass `SkillToolManager` to `SkillManagerCap`
- [x] 2.6 Update `for_run()` in `SkillManagerCap` to pass `tool_manager=self._tool_manager` to the new instance (D7)
- [ ] 2.7 Add end-to-end test: SKILL.md with `tools: [{import_path: "json:loads"}]` → agent toolset contains `{skill_name}__tool__loads`

## 3. Restore Per-Skill MCP Tool Registration

- [x] 3.1 In `SkillManagerCap.__init__()`, for each skill with `mcp_servers` frontmatter, create `McpServerCap` instances per declared server, store in `_skill_mcp_children[skill_name]`, and add to `_capabilities` for lifecycle management
- [x] 3.2 Verify `get_toolset()` override (task 2.4) correctly wraps per-skill McpServerCap children in `PrefixedToolset("{skill_name}__mcp__")` — this was already implemented in 2.4 but needs verification with actual McpServerCap instances
- [x] 3.3 Verify `McpServerCap.__aenter__()`/`__aexit__()` lifecycle is managed by `CombinedToolsetCapability` parent (children in `_capabilities`)
- [x] 3.4 Verify `McpServerCap.for_run()` returns `self` (inherited default) — shared MCP connection across runs is acceptable
- [ ] 3.5 Add end-to-end test: SKILL.md with `mcp_servers` → agent toolset contains prefixed MCP tools

## 4. Restore allowed_tools Filtering

- [x] 4.1 Implement `get_wrapper_toolset()` override in `SkillManagerCap`: build composite filter from all skills' `allowed_tools`, create `FilteredToolset` with a single filter function that handles multiple skills (non-skill tools always pass, skill tools checked against their skill's allowed set)
- [ ] 4.2 Add test: skill with `allowed_tools: ["read", "list"]` → `restricted__tool__write` filtered out, `restricted__tool__read` accessible
- [ ] 4.3 Add test: `allowed_tools` not declared → all tools accessible (no filter applied)
- [ ] 4.4 Add test: `allowed_tools: []` (explicitly empty) → all skill tools filtered out (none accessible)

## 5. Fix load_skill Tool Propagation

- [x] 5.1 Restore `skills_tools_provider` injection in `_inject_pool_providers()` (factory.py), guarded by `if host_context.skills_tools_provider is not None`
- [x] 5.2 Add test: standalone `Agent.from_config()` path has `load_skill` and `list_skills` in tool list
- [x] 5.3 Add test: child session agent created via `_inject_pool_providers()` has `load_skill` and `list_skills`

## 6. Migrate load_skill/list_skills MCP Provider Dispatch to SkillResource Protocol

- [x] 6.1 Replace `getattr(provider, 'get_skills', None)` with `isinstance(provider, SkillResource)` in the MCP provider iteration path of `agentpool_toolsets/builtin/skills.py` — call `list_skills()` instead of `get_skills()`
- [x] 6.2 Replace `getattr(provider, 'get_skill_instructions', None)` with `isinstance(provider, SkillResource)` + `read_skill()` calls in the MCP provider path
- [x] 6.3 Replace `getattr(provider, 'skill_exists', None)` with `isinstance(provider, SkillResource)` + `skill_exists()` calls in the MCP provider path
- [x] 6.4 Verify local skills path (`ctx.pool.skills.list_skills()`) is NOT modified — stays as direct `SkillsManager` call returning `Skill` objects
- [x] 6.5 Verify type difference handling: `Skill` (local path) vs `SkillEntry` (SkillResource path) — both converted to common display format in tool results
- [x] 6.6 Run existing skill tests to verify no regressions from migration

## 7. Spec Updates

- [ ] 7.1 Verify `specs/skill-tool-registration/spec.md` is complete and matches implementation
- [ ] 7.2 Verify `specs/skill-tools/spec.md` is complete and matches implementation
- [ ] 7.3 Verify `specs/skill-manager-cap/spec.md` delta correctly modifies existing spec
- [ ] 7.4 Verify `specs/extension-registry/spec.md` delta correctly adds to existing spec
- [ ] 7.5 Verify `specs/agent-factory/spec.md` delta correctly adds to existing spec

## 8. Unit Tests — SkillManagerCap Methods

- [x] 8.1 `_import_skill_tools()`: skill with `tools` frontmatter → tools imported and stored in `_skill_tools`
- [x] 8.2 `_import_skill_tools()`: skill without `tools` → no tools imported, `_skill_tools` empty for that skill
- [x] 8.3 `_import_skill_tools()`: skill with empty `tools: []` → no tools imported, no error
- [x] 8.4 `get_toolset()`: returns `PrefixedToolset("{skill_name}__tool__")` wrapping `FunctionToolset` of imported Python tools
- [x] 8.5 `get_toolset()`: merges Python tool PrefixedToolsets with per-skill McpServerCap toolsets (prefixed with `{skill_name}__mcp__`) and non-skill children (unprefixed) via `CombinedToolset`
- [x] 8.6 `get_toolset()`: wraps per-skill McpServerCap children's toolsets in `PrefixedToolset("{skill_name}__mcp__")`
- [x] 8.7 `get_toolset()`: non-skill children from `_capabilities` included unprefixed
- [x] 8.8 `get_wrapper_toolset()`: skill with `allowed_tools: ["read", "list"]` → `FilteredToolset` filters non-allowed tools
- [x] 8.9 `get_wrapper_toolset()`: skill without `allowed_tools` → all tools accessible (no filter applied)
- [x] 8.10 `get_wrapper_toolset()`: skill with empty `allowed_tools: []` → all skill tools filtered out
- [ ] 8.11 `get_wrapper_toolset()`: multiple skills each with different `allowed_tools` → composite filter handles all
- [x] 8.12 `SkillManagerCap.__init__()`: skill with `mcp_servers` → `McpServerCap` instances created, stored in `_skill_mcp_children`, and added to `_capabilities`
- [x] 8.13 `SkillManagerCap.__init__()`: skill without `mcp_servers` → no `McpServerCap` children created
- [x] 8.14 `for_run()`: new instance receives `tool_manager` and has `_skill_tools` populated

## 9. Unit Tests — ExtensionRegistry Integration

- [x] 9.1 `ExtensionRegistry.register(skill_manager_cap, POOL)` → `get_skill_resources(scope)` returns it for any scope
- [x] 9.2 `ExtensionRegistry.unregister(skill_manager_cap, POOL)` → `get_skill_resources(scope)` no longer returns it
- [x] 9.3 Pool rebuild: old `SkillManagerCap` unregistered before new one registered → no stale entries
- [x] 9.4 `ExtensionRegistry.resolve_uri("skill://my-skill", scope)` → flat URI, no provider matching → `read_skill("my-skill")` called on `SkillManagerCap`

## 10. Unit Tests — _inject_pool_providers

- [x] 10.1 `_inject_pool_providers()` with `skills_tools_provider` not None → `agent._external_capabilities` includes it
- [x] 10.2 `_inject_pool_providers()` with `skills_tools_provider` None → no error, no injection

## 11. Unit Tests — load_skill/list_skills Protocol Migration

- [x] 11.1 `load_skill`: MCP provider implementing `SkillResource` → skill found via `isinstance` check, `list_skills()` called
- [x] 11.2 `load_skill`: MCP provider NOT implementing `SkillResource` → skipped, no `AttributeError`
- [x] 11.3 `load_skill`: mixed `SkillResource` + non-`SkillResource` providers → only `SkillResource` queried
- [x] 11.4 `list_skills`: protocol-based dispatch returns skills from all `SkillResource` MCP providers
- [x] 11.5 `list_skills`: each entry includes `name`, `description`, and `source`
- [x] 11.6 `load_skill` local path unchanged: `ctx.pool.skills.list_skills()` still returns `Skill` objects
- [x] 11.7 `load_skill`: skill not found → `SkillNotFoundError` with descriptive message
- [x] 11.8 `load_skill` argument substitution: `$1` → first positional arg (regression after migration)
- [x] 11.9 `load_skill` argument substitution: `$@` and `$ARGUMENTS` → all args joined (regression after migration)
- [x] 11.10 `load_skill` argument substitution: no `arguments` provided → placeholders remain as-is
- [x] 11.11 `load_skill` reference loading: skill with `references/` directory → reference content included
- [x] 11.12 `load_skill` bare name resolution: searches local skills first, then `SkillResource` providers
- [x] 11.13 `load_skill` reference path traversal: reference path containing `..` → `SecurityError` raised, no file outside skill directory read
- [x] 11.14 `load_skill` with URI argument: `load_skill(ctx, skill_name="skill://my-skill")` → skill instructions returned via URI resolution path

## 12. Edge Case Tests

- [ ] 12.1 Skill with both `tools` AND `mcp_servers` → both `{name}__tool__*` and `{name}__mcp__*` tools registered
- [ ] 12.2 Skill with invalid `import_path` (nonexistent module) → graceful error, other skills unaffected
- [ ] 12.3 `McpServerCap` creation failure (bad config) → other skills' tools still registered, error logged
- [ ] 12.4 Pool rebuild with skill removal → removed skill's tools no longer in agent toolset
- [ ] 12.5 `allowed_tools` empty list `[]` → all skill tools filtered out (distinct from `None` = no filter)

## 13. Integration Tests

- [ ] 13.1 End-to-end: SKILL.md with `tools: [{import_path: "json:loads"}]` → agent toolset contains `{skill_name}__tool__loads`
- [ ] 13.2 End-to-end: SKILL.md with `mcp_servers` → agent toolset contains `{skill_name}__mcp__*` tools
- [ ] 13.3 End-to-end: `load_skill` available in standalone `Agent.from_config()` (non-SessionPool path)
- [ ] 13.4 End-to-end: `load_skill` available in child session agent via `_inject_pool_providers()`
- [ ] 13.5 End-to-end: `skill://` URI resolution (pool init → skill registration → URI resolve → content returned)
- [ ] 13.6 End-to-end: `allowed_tools` filtering (skill with `allowed_tools` → non-allowed tools filtered from agent toolset)
- [ ] 13.7 End-to-end: multiple skills with same `import_path` → isolated by `{skill_name}__tool__` prefix
- [ ] 13.8 End-to-end: pool rebuild re-imports tools and re-creates McpServerCap children
- [ ] 13.9 `SkillManagerCap` + `McpServerCap` child `__aenter__`/`__aexit__` lifecycle (enter all, exit in reverse)
- [ ] 13.10 Agent via TestModel calls prefixed skill tool → tool executes and returns result
- [ ] 13.11 Agent via TestModel calls `load_skill` → skill instructions returned in tool result
- [ ] 13.12 `load_skill` return value includes tool/MCP server status info after protocol migration
- [ ] 13.13 Multiple skills each with MCP servers → isolation by prefix, independent lifecycle
- [ ] 13.14 `for_run()` on SkillManagerCap → new instance has `tool_manager` and `_skill_tools` populated

## 14. Migration Regression Tests

- [x] 14.1 Existing `tests/skills/test_mcp_skills_integration.py` passes after protocol migration
- [x] 14.2 Existing `tests/integration/test_skill_resolution.py` passes after protocol migration
- [x] 14.3 Existing `tests/capabilities/test_skill_manager_cap_integration.py` passes (update mocks for new `tool_manager` and `_skill_mcp_children` params)
- [x] 14.4 Existing `tests/skills/test_uri_resolver.py` passes (no changes to URI parsing)
- [x] 14.5 Existing `tests/delegation/test_pool_skills.py` passes (update for ExtensionRegistry registration and flat URI)
- [x] 14.6 Existing `tests/capabilities/test_extension_registry.py` passes
- [x] 14.7 Existing `tests/capabilities/test_review_fixes.py` passes
- [ ] 14.8 Existing `tests/toolsets/test_package_scoped_skills.py` passes

## 15. Full Suite Validation

- [x] 15.1 Run skill tests: `uv run pytest tests/ -k "skill" -x -v`
- [ ] 15.2 Run full test suite: `uv run pytest` (no regressions)
- [ ] 15.3 Run mypy: `uv run mypy src/agentpool/capabilities/skill_manager_cap.py src/agentpool_toolsets/builtin/skills.py`
- [x] 15.4 Run ruff: `uv run ruff check src/agentpool/capabilities/skill_manager_cap.py src/agentpool_toolsets/builtin/skills.py`
- [x] 15.5 Run ruff format check: `uv run ruff format --check src/agentpool/capabilities/skill_manager_cap.py src/agentpool_toolsets/builtin/skills.py`

## 16. Dead Code Cleanup

- [x] 16.1 Remove `_skill_commands`, `_skill_mcp_manager`, `_skill_tool_manager` fields from `pool.py` (always `None`, never assigned)
- [x] 16.2 Remove `skill_commands` property from `pool.py` (wraps always-`None` field)
- [x] 16.3 Remove unreachable branch after `isinstance(cap, SkillManagerCap): continue` in `agent/native_agent/agent.py` (lines 932-945)
- [x] 16.4 Remove `_resource_provider` field and `resource_provider` property from `skills/manager.py` (always raises RuntimeError)
- [x] 16.5 Delete empty `resource_providers/` directory (confirm only `__pycache__` remains)
- [x] 16.6 Update stale comments referencing `SkillCapability`, `SkillProvider`, `SkillCommandRegistry` in `uri_resolver.py`, `pool.py`, `agent.py`, `command.py`, `extension_registry.py`, `manager.py`
- [x] 16.7 Verify all existing tests pass after dead code removal

## 17. Flat URI Implementation

- [x] 17.1 Change `SkillManagerCap.list_skills()` line 270: `f"skill://local/{name}"` → `f"skill://{name}"`
- [x] 17.2 Change `SkillManagerCap.list_commands()` line 371: `f"skill://local/{name}"` → `f"skill://{name}"`
- [x] 17.3 Fix `ResolvedSkillURI.parse()` in `uri_resolver.py`: when `urlparse` puts name in netloc with empty path, treat netloc as `skill_name` with `provider=None`
- [x] 17.4 Fix `SkillURIResolver.resolve()` (uri_resolver.py line ~486): handle flat URI parsing after ExtensionRegistry success
- [x] 17.5 Add test: `skill://my-skill` flat URI resolves correctly through ExtensionRegistry
- [x] 17.6 Add test: `load_skill(ctx, "skill://my-skill")` returns skill instructions via flat URI
- [x] 17.7 Add test: `list_skills()` returns URIs in `skill://{name}` format (no `/local/` segment)
