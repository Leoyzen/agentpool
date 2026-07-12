## Context

The M3 capability refactor replaced the per-skill `SkillCapability` class with a unified `SkillManagerCap`. While instruction injection and skill listing were migrated correctly, three critical functions were lost:

1. **`skill://` URI resolution**: `SkillURIResolver` delegates to `ExtensionRegistry.resolve_uri()`, but `SkillManagerCap` and `McpServerCap` are never registered with `ExtensionRegistry`. The old `_providers` dict path is dead code when `extension_registry` is set.

2. **Per-skill tool registration**: The old `SkillCapability.get_toolset()` eagerly imported Python tools via `SkillToolManager.import_tools()` and lazily connected MCP servers via `SkillMcpManager`, both wrapped in `PrefixedToolset("{name}__tool__")` / `PrefixedToolset("{name}__mcp__")`. `SkillManagerCap` does none of this — it only holds `dict[str, Skill]` for instructions.

3. **`load_skill` propagation**: `_inject_pool_providers()` deliberately stopped injecting `skills_tools_provider`, leaving non-SessionPool/Factory agent creation paths without the `load_skill`/`list_skills` tools.

Additionally, the `load_skill`/`list_skills` tools (570 LOC in `agentpool_toolsets/builtin/skills.py`) have zero OpenSpec coverage and use `getattr` duck-typing instead of `SkillResource` protocol checks.

## Goals / Non-Goals

**Goals:**
- Restore `skill://` URI resolution end-to-end through `ExtensionRegistry`
- Flatten `skill://` URIs from `skill://local/{name}` to `skill://{name}` (no provider segment)
- Restore per-skill Python tool registration with `{name}__tool__` prefixing
- Restore per-skill MCP server tool registration with `{name}__mcp__` prefixing
- Restore `allowed_tools` filtering per skill
- Fix `load_skill` tool propagation for all agent creation paths
- Migrate `load_skill`/`list_skills` MCP provider dispatch from `getattr` to `SkillResource` protocol
- Remove dead code: deprecated pool fields (`_skill_commands`, `_skill_mcp_manager`, `_skill_tool_manager`, `skill_commands` property), unreachable agent.py branches, dead `resource_provider` in manager.py
- Add specs for `load_skill`/`list_skills` skills and per-skill tool registration
- Add end-to-end tests covering all three regressions

**Non-Goals:**
- Migrating `ctx.pool` references to `host_context` in `load_skill`/`list_skills` (deferred to separate change — requires extending `HostContext`). The local skills path (`ctx.pool.skills.list_skills()`) stays as-is. Only the MCP provider iteration path migrates from `getattr` to `isinstance(SkillResource)`.
- Unifying `skill://` URI format (flat vs provider-qualified) — deferred to separate change
- Restoring `SkillCapability` class — it stays dead code; all logic goes into `SkillManagerCap`
- Restoring `SkillMcpManager` — replaced by `McpServerCap` per-skill instances
- Restoring `on_run_ended()` MCP cleanup — handled by `McpServerCap.__aexit__()` lifecycle
- Restoring `CapabilityOrdering` (`ProcessHistory`, `NativeTool`) — not used by current agent assembly

## Decisions

### D1: Register `SkillManagerCap` with `ExtensionRegistry` at POOL scope

**Decision**: In `_rebuild_skill_capabilities()`, after creating the `SkillManagerCap`, call `self._extension_registry.register(skill_manager_cap, ScopeLevel.POOL)`. The capability name can be any value (e.g., `"pool-skills"`) since flat `skill://` URIs (`skill://{name}`) have no provider segment — `ExtensionRegistry.resolve_uri()` uses `provider_name=None` and tries all visible `SkillResource` capabilities without filtering by name.

**Rationale**: With flat URIs (D9), `ExtensionRegistry.resolve_uri()` extracts `provider_name=None` from `skill://{name}` and iterates ALL visible `SkillResource` capabilities without name filtering (line 451-456 of extension_registry.py: the `provider_name is not None` branch is skipped). The old `provider_name="local"` → `_get_cap_name(cap)` matching path is never taken for flat `skill://` URIs. `McpServerCap` instances are children of `SkillManagerCap` and accessed via its `read_skill()` method — they don't need individual registration (consistent with existing spec: "ExtensionRegistry sees only SkillManagerCap").

**Alternative considered**: Fall through to `_providers` dict in `SkillURIResolver.resolve()` when `ExtensionRegistry` returns `None`. Rejected because it creates two parallel resolution paths and the `_providers` dict uses old method names.

### D2: `SkillManagerCap` accepts `SkillToolManager`, imports Python tools eagerly, and fully overrides `get_toolset()`

**Decision**: Add `tool_manager: SkillToolManager | None = None` parameter to `SkillManagerCap.__init__()`. For each skill in `local_skills` that has `tools` frontmatter, call `tool_manager.import_tools(skill.tools)` and store the result as `_skill_tools: dict[str, list[Tool]]` (keyed by skill name).

`SkillManagerCap` SHALL fully override `get_toolset()` — it SHALL NOT call `super().get_toolset()`. The override algorithm:

```python
def get_toolset(self):
    toolsets: list[AbstractToolset] = []

    # 1. Python tools: PrefixedToolset per skill
    for skill_name, tools in self._skill_tools.items():
        pa_tools = [t.to_pydantic_ai() for t in tools]
        toolsets.append(PrefixedToolset(
            prefix=f"{skill_name}__tool__",
            wrapped=FunctionToolset(pa_tools),
        ))

    # 2. Per-skill McpServerCap children: PrefixedToolset per skill
    for skill_name, child_caps in self._skill_mcp_children.items():
        for child in child_caps:
            child_ts = child.get_toolset()
            if child_ts is not None:
                toolsets.append(PrefixedToolset(
                    prefix=f"{skill_name}__mcp__",
                    wrapped=child_ts,
                ))

    # 3. Non-skill children (from CombinedToolsetCapability._capabilities):
    #    iterate children NOT in _skill_mcp_children, unprefixed
    skill_child_ids = {id(c) for caps in self._skill_mcp_children.values() for c in caps}
    for cap in self._capabilities:
        if id(cap) not in skill_child_ids:
            ts = cap.get_toolset()
            if ts is not None:
                toolsets.append(ts)

    return CombinedToolset(toolsets=toolsets) if toolsets else None
```

**Why not `super().get_toolset()`**: The parent's `CombinedToolsetCapability.get_toolset()` iterates ALL children and calls `cap.get_toolset()` on each, wrapping in a `CombinedToolset` — with no prefixing. We need per-skill `PrefixedToolset` wrapping, which requires iterating children individually and wrapping each. Calling `super()` would include skill McpServerCap children unprefixed, defeating D3's purpose.

**Data structures added to `SkillManagerCap`**:
- `_skill_tools: dict[str, list[Tool]]` — imported Python tools per skill name
- `_skill_mcp_children: dict[str, list[McpServerCap]]` — per-skill MCP server capabilities
- `_tool_manager: SkillToolManager | None` — reference for `for_run()` propagation

**Rationale**: Eager import at construction time matches the old `SkillCapability` behavior. `PrefixedToolset` ensures tool name isolation per skill. The skill-to-child mapping (`_skill_mcp_children`) is necessary to apply the correct `{skill_name}__mcp__` prefix per skill.

**Alternative considered**: Create per-skill `SkillCapability` instances as children. Rejected because `SkillCapability` is dead code and we want to consolidate logic in `SkillManagerCap`.

### D3: Per-skill MCP servers create `McpServerCap` instances tracked in `_skill_mcp_children`

**Decision**: For each skill in `local_skills` that has `mcp_servers` frontmatter, create a `McpServerCap` instance per declared MCP server. Store each in `self._skill_mcp_children[skill_name]` (a `dict[str, list[McpServerCap]]`). Also add them to `self._capabilities` (the parent's children list) so lifecycle (`__aenter__`/`__aexit__`) is managed by `CombinedToolsetCapability`.

The `get_toolset()` override (D2) iterates `_skill_mcp_children` to apply `{skill_name}__mcp__` prefixing, and iterates `_capabilities` minus skill children for unprefixed non-skill children.

**Rationale**: `McpServerCap` already implements `SkillResource` and handles MCP lifecycle. By tracking them in `_skill_mcp_children`, we can apply the correct prefix per skill. By also adding them to `_capabilities`, the parent's lifecycle management enters/exits them automatically.

**`McpServerCap.for_run()` behavior**: `McpServerCap` inherits `AbstractCapability.for_run()` which returns `self` by default (no per-run isolation). This is acceptable — MCP connections are stateless from the agent's perspective. `SkillManagerCap.for_run()` (D7) will call `child.for_run(ctx)` on all children, which returns the same `McpServerCap` instance.

**Alternative considered**: Use the old `SkillMcpManager` lazy connection path. Rejected because `SkillMcpManager` is deleted and `McpServerCap` is the new standard MCP capability.

### D4: `allowed_tools` filtering via `get_wrapper_toolset()` with composite multi-skill filter

**Decision**: `SkillManagerCap` overrides `get_wrapper_toolset(toolset)` to apply a `FilteredToolset` with a composite filter function that handles multiple skills' `allowed_tools` simultaneously.

`get_wrapper_toolset()` receives the **entire assembled agent toolset** (all tools from all capabilities). The filter SHALL:
1. Allow all non-skill tools (tools not prefixed with any `{skill_name}__`)
2. For skill tools (`{skill_name}__tool__*` or `{skill_name}__mcp__*`), strip the prefix and check against that skill's `allowed_tools` set

```python
def get_wrapper_toolset(self, toolset):
    # Build per-skill filter maps: {skill_name: set(allowed_bare_names)}
    skill_filters: dict[str, set[str]] = {}
    for name, skill in self._local_skills.items():
        allowed = skill.parsed_allowed_tools()
        if allowed:  # Non-empty list means filtering is active
            skill_filters[name] = set(allowed)

    if not skill_filters:
        return None  # No filtering needed

    def _filter(ctx, tool_def):
        name = tool_def.name
        for skill_name, allowed_set in skill_filters.items():
            prefix = f"{skill_name}__"
            if name.startswith(prefix):
                # Strip prefix: "my-skill__tool__read" → "read"
                bare = name[len(prefix):].rsplit("__", 1)[-1]
                return bare in allowed_set
        return True  # Non-skill tools always pass

    return FilteredToolset(wrapped=toolset, filter_func=_filter)
```

**`allowed_tools` semantics**:
- `allowed_tools` not declared in frontmatter (`None`) → no filtering, all tools accessible
- `allowed_tools: ["read", "list"]` → only "read" and "list" accessible
- `allowed_tools: []` (explicitly empty) → all tools filtered out (none accessible). This is distinct from `None`.

**Rationale**: Matches the old `SkillCapability.get_wrapper_toolset()` behavior. The composite filter handles all skills in one pass. `FilteredToolset` is a pydantic-ai native concept.

### D5: Restore `skills_tools_provider` injection in `_inject_pool_providers()`

**Decision**: Add `agent._external_capabilities.append(host_context.skills_tools_provider)` back to `_inject_pool_providers()` in `factory.py`, guarded by `if host_context.skills_tools_provider is not None`.

**Rationale**: One-line fix. The comment said "compiled as a native capability in AgentFactory.compile()" but that path only covers `NativeAgent` instances. Non-native agents and standalone execution need this injection.

**Double injection note**: `_compile_agent_capabilities()` (factory.py line 200-201) already appends `skills_tools_provider` to the compiled capability list for NativeAgent. `_inject_pool_providers()` also appends it. This is safe because: (a) `host_context.skills_tools_provider` is a singleton created once in `AgentPool.__post_init__()`, (b) `_get_all_tools()` deduplicates by tool name, and (c) the SessionPool direct path (session_pool.py lines 363, 407) also appends the same instance. All three paths reference the same object.

### D6: Migrate `load_skill`/`list_skills` MCP provider dispatch to `SkillResource` protocol (local path stays as-is)

**Decision**: Replace `getattr(provider, 'get_skills', None)` and `getattr(provider, 'get_skill_instructions', None)` calls in the **MCP provider iteration path** of `agentpool_toolsets/builtin/skills.py` with `isinstance(provider, SkillResource)` checks and calls to `list_skills()` / `read_skill()` / `skill_exists()`.

**Scope clarification — two distinct paths in `skills.py`**:
1. **Local skills path** (`ctx.pool.skills.list_skills()`): Returns `Skill` objects directly from `SkillsManager`. This path **stays as-is** — it does NOT use `getattr` or `SkillResource`. `ctx.pool.skills` is a `SkillsManager`, not a `SkillResource`. This path is not migrated.
2. **MCP provider iteration path** (`getattr(provider, 'get_skills', None)` on MCP capabilities): This is the path being migrated. It iterates `ctx.pool.skill_provider.capabilities` (or similar) and uses `getattr` to probe for old method names. This migrates to `isinstance(provider, SkillResource)` + `list_skills()` (returns `SkillEntry` objects, not `Skill`).

**Type difference**: `Skill` (from `SkillsManager`) and `SkillEntry` (from `SkillResource.list_skills()`) are different types. The `load_skill`/`list_skills` tools already handle both types in their display logic. No type unification is needed — the tools convert both to a common display format.

**`ctx.pool` references**: The 21 `ctx.pool` references in `skills.py` are NOT migrated (per Non-Goals). The migration only changes how MCP providers are probed (from `getattr` to `isinstance`), not how they're discovered.

### D7: Update `for_run()` to propagate `tool_manager` and `_skill_tools`

**Decision**: `SkillManagerCap.for_run()` SHALL be updated to pass `tool_manager=self._tool_manager` to the new `SkillManagerCap` instance. Since `import_tools()` is eager and idempotent, the new instance will re-import the same tools. Alternatively, `_skill_tools` can be copied directly to avoid re-importing.

Updated `for_run()`:
```python
async def for_run(self, ctx: RunContext[AgentDepsT]) -> SkillManagerCap[AgentDepsT]:
    children_for_run = [await child.for_run(ctx) for child in self._capabilities]
    cap = SkillManagerCap(
        local_skills=self._local_skills,
        children=children_for_run,
        matcher_fn=self._matcher_fn,
        always_active=self._always_active,
        registry=self._registry,
        name=self._name,
        tool_manager=self._tool_manager,  # NEW: propagate for tool import
    )
    return cap
```

**Note**: `McpServerCap.for_run()` returns `self` by default (inherited from `AbstractCapability`), so the same MCP connection is shared across runs. This is acceptable — MCP connections are stateless from the agent's perspective.

**Rationale**: Without this update, per-run copies of `SkillManagerCap` would have `tool_manager=None` and no `_skill_tools`, causing Python tools to disappear in per-run contexts.

### D8: Remove dead code and deprecated skill-related fields

**Decision**: Remove the following dead code identified during the M3 refactor cleanup:

**`pool.py` — deprecated fields and property (safe removal, always `None`):**
- `self._skill_commands: Any | None = None` (line 186) — never assigned, always `None`
- `self._skill_mcp_manager: Any | None = None` (line 189) — references deleted `SkillMcpManager` class
- `self._skill_tool_manager: Any | None = None` (line 190) — references deleted manager, never assigned
- `skill_commands` property (lines 464-470) — wraps `_skill_commands`, always returns `None`

**`agent/native_agent/agent.py` — unreachable branch (lines 932-945):**
After the `isinstance(cap, SkillManagerCap): continue` at line 937, the remaining loop body (visibility_checker, `build_config_entries()`) is unreachable — all capabilities are `SkillManagerCap` in the current architecture.

**`skills/manager.py` — dead `resource_provider` (lines 57, 85-97):**
- `self._resource_provider: AbstractCapability | None = None` (line 57) — initialized but never used
- `resource_provider` property (lines 85-97) — always raises `RuntimeError`, migration remnant

**Rationale**: These are all M3 migration remnants — fields referencing deleted classes, branches that can never be reached, or properties that unconditionally raise errors. Removing them reduces code size and confusion during maintenance. None affect runtime behavior since they're all dead paths.

### D9: Flatten `skill://` URIs from `skill://local/{name}` to `skill://{name}`

**Decision**: Adopt flat `skill://{name}` URIs without a provider segment. This requires changes in four locations:

1. **`SkillManagerCap.list_skills()` (line 270)** and **`list_commands()` (line 371)**: Change `f"skill://local/{name}"` → `f"skill://{name}"`.

2. **`ResolvedSkillURI.parse()` (uri_resolver.py lines 72-161)**: Currently uses `urlparse()` which puts `name` in `netloc` for `skill://name` format (leaving `path=""` → `ValueError`). Add a special case: when `urlparse` yields empty path but non-empty netloc, treat netloc as `skill_name` with `provider=None`.

3. **`SkillURIResolver.resolve()` (uri_resolver.py line 486)**: After `ExtensionRegistry.resolve_uri()` succeeds, `ResolvedSkillURI.parse(uri)` is called to extract `skill_name` for the returned `Skill` object. After fix #2, this will work correctly on flat URIs.

4. **`ExtensionRegistry.resolve_uri()`**: No changes needed. When the URI is `skill://name`, the naïve `split("/", 1)` parsing yields `provider_name=None, skill_name="name"`, which already iterates ALL visible `SkillResource` capabilities without name filtering (the `provider_name is not None` branch at line 453 is skipped). This is exactly the desired flat-URI behavior.

**Rationale**: The provider segment was introduced during M3 but never matched any actual capability name (there is no capability named `"local"`). `ExtensionRegistry.resolve_uri()` uses `_get_cap_name(cap)` which returns serialization names like `"skill-manager"`, not `"local"`. The `provider_name` → `_get_cap_name(cap)` matching path was therefore dead code — all resolution fell through to the `provider_name=None` try-all path anyway. Flattening removes this confusion.

**Impact on D1**: `SkillManagerCap` no longer needs `name="local"` — any name works since provider matching is irrelevant for flat URIs.

## Risks / Trade-offs

- **[Risk] `SkillManagerCap` complexity increases** → Mitigation: Keep tool import logic in a private `_import_skill_tools()` method, separate from instruction injection logic. The class grows but each concern is isolated.

- **[Risk] Per-skill `McpServerCap` instances increase MCP connection count** → Mitigation: `McpServerCap` uses lazy initialization. Connections are only opened when tools are actually called. `__aexit__` cleans up properly.

- **[Risk] `PrefixedToolset` wrapper may break tool name expectations in some protocols** → Mitigation: The old `SkillCapability` used the exact same prefixing convention. This is a restoration, not a new pattern.

- **[Risk] Double injection of `skills_tools_provider`** (both `_inject_pool_providers` and SessionPool/Factory paths) → Mitigation: `host_context.skills_tools_provider` is a singleton created once in `AgentPool.__post_init__()`. `_get_all_tools()` deduplicates by tool name. No functional impact.

- **[Risk] `get_toolset()` full override bypasses parent's merge logic** → Mitigation: The override explicitly handles all three cases (Python tools, skill McpServerCap children, non-skill children). The parent's `CombinedToolsetCapability.get_toolset()` logic is replicated with prefixing added. Unit tests verify all three cases.

- **[Trade-off] `SkillManagerCap` now holds `SkillToolManager` reference** → This couples it to the tool import system. Acceptable because `SkillToolManager` is a simple utility class (101 LOC) with no external dependencies.

- **[Risk] `allowed_tools: []` (explicitly empty) filters all tools** → Mitigation: This behavior is documented in D4 and spec'd. The `parsed_allowed_tools()` method returns `[]` for both `None` and empty string, so the filter checks `if allowed:` (non-empty) before activating. `None` → no filter, `[]` → filter all.

- **[Risk] `load_skill` still creates throwaway `SkillToolManager` for display** → The `load_skill` tool creates a new `SkillToolManager()` on each call (line 353) to display tool import status. This is informational only — the actual tool registration happens in `SkillManagerCap`. The throwaway instance is harmless but slightly wasteful. Deferred to a future cleanup change.
