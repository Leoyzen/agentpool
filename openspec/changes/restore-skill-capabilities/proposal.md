## Why

The M3 capability refactor (commit `15179ea40`) and subsequent extension-source-architecture change replaced the per-skill `SkillCapability` class with a unified `SkillManagerCap`. During this migration, three critical regressions were introduced: (1) `skill://` URI resolution broke because `SkillManagerCap` and `McpServerCap` were never registered with `ExtensionRegistry`, (2) per-skill Python tool and MCP server tool registration was dropped because `SkillManagerCap` does not call `SkillToolManager` or create per-skill `McpServerCap` instances, and (3) `load_skill` tool propagation has a gap because `_inject_pool_providers()` no longer injects `skills_tools_provider`. Additionally, the `load_skill`/`list_skills` tools (570 LOC) have zero OpenSpec coverage, and no end-to-end tests verify skill tool registration or URI resolution through the new architecture.

## What Changes

- **Fix `skill://` URI resolution**: Register `SkillManagerCap` with `ExtensionRegistry` at POOL scope during pool initialization, so `ExtensionRegistry.resolve_uri()` can find it. `McpServerCap` instances are children of `SkillManagerCap` and do not need individual registration. URIs are flattened to `skill://{name}` (no provider segment).
- **Flatten `skill://` URIs**: Remove the hardcoded `skill://local/` prefix in `SkillManagerCap.list_skills()`/`list_commands()` and fix `ResolvedSkillURI.parse()` to handle flat `skill://{name}` format.
- **Restore per-skill Python tool registration**: `SkillManagerCap` will accept a `SkillToolManager` and, for each local skill with `tools` frontmatter, eagerly import Python tools and expose them as a `PrefixedToolset("{skill_name}__tool__")` via `get_toolset()`.
- **Restore per-skill MCP tool registration**: For each local skill with `mcp_servers` frontmatter, create a `McpServerCap` instance and add it as a child of `SkillManagerCap`, so `CombinedToolsetCapability.get_toolset()` merges its tools with the `{skill_name}__mcp__` prefix.
- **Fix `load_skill` tool propagation**: Restore `skills_tools_provider` injection in `_inject_pool_providers()` so agents created outside SessionPool/AgentFactory also receive the `load_skill` and `list_skills` tools.
- **Restore `allowed_tools` filtering**: `SkillManagerCap` will implement tool filtering based on each skill's `allowed_tools` frontmatter, equivalent to the old `SkillCapability.get_wrapper_toolset()`.
- **New spec: `skill-tool-registration`**: Formally specify how `Skill.tools` and `Skill.mcp_servers` frontmatter fields map to agent tools, including prefixing conventions, filtering, and lifecycle.
- **New spec: `skill-tools`**: Formally specify the `load_skill` and `list_skills` tools — resolution paths (bare name vs URI), argument substitution, reference loading, error handling, and protocol-based dispatch via `SkillResource`.
- **Update spec: `skill-manager-cap`**: Add requirements for per-skill tool registration, `ExtensionRegistry` registration, and `allowed_tools` filtering.
- **Update spec: `extension-registry`**: Add requirement that `SkillManagerCap` must be registered at POOL scope during pool initialization so `resolve_uri()` can discover it for flat `skill://{name}` URIs. `McpServerCap` instances are children of `SkillManagerCap` and do not need individual registration.
- **Add end-to-end tests**: Per-skill Python tool registration, per-skill MCP tool registration, `load_skill` tool availability, `skill://` URI end-to-end resolution, `allowed_tools` filtering, and non-SessionPool agent propagation.
- **Remove dead code**: Delete deprecated pool fields, unreachable agent.py branches, dead manager.py `resource_provider`, and stale comments referencing deleted classes.

## Capabilities

### New Capabilities

- `skill-tool-registration`: How `Skill.tools` and `Skill.mcp_servers` frontmatter fields map to agent tools — prefixing (`{name}__tool__`, `{name}__mcp__`), eager import via `SkillToolManager`, lazy MCP connection via `McpServerCap`, `allowed_tools` filtering, and lifecycle cleanup.
- `skill-tools`: The `load_skill` and `list_skills` user-facing tools — resolution paths (bare name vs `skill://` URI), argument substitution (`$1`, `$@`, `$ARGUMENTS`), reference file loading, error handling, and protocol-based dispatch via `SkillResource` instead of `getattr` duck-typing.

### Modified Capabilities

- `skill-manager-cap`: Add requirements for per-skill tool registration via `SkillToolManager` + `PrefixedToolset`, per-skill MCP tool registration via child `McpServerCap` instances, `allowed_tools` filtering, and registration with `ExtensionRegistry` at POOL scope.
- `extension-registry`: Add requirement that pool-level `SkillManagerCap` must be registered at POOL scope during pool initialization so `resolve_uri()` can discover it for flat `skill://{name}` URIs. `McpServerCap` instances are children of `SkillManagerCap` and do not need individual registration.
- `agent-factory`: Add requirement that `_inject_pool_providers()` must inject `skills_tools_provider` for all agent creation paths, not just SessionPool/Factory paths.

## Impact

- **`src/agentpool/capabilities/skill_manager_cap.py`**: Add `SkillToolManager` dependency, per-skill tool import logic, `PrefixedToolset` creation, `allowed_tools` filtering, and `ExtensionRegistry` registration.
- **`src/agentpool/delegation/pool.py`**: Update `_rebuild_skill_capabilities()` to pass `SkillToolManager` to `SkillManagerCap`; register `SkillManagerCap` with `ExtensionRegistry` at POOL scope (McpServerCap instances are children and do not need individual registration). Remove deprecated fields: `_skill_commands`, `_skill_mcp_manager`, `_skill_tool_manager`, and `skill_commands` property.
- **`src/agentpool/agents/native_agent/agent.py`**: Remove unreachable branch after `isinstance(cap, SkillManagerCap)` continue (lines 932-945).
- **`src/agentpool/skills/manager.py`**: Remove dead `_resource_provider` field and `resource_provider` property.
- **`src/agentpool/skills/uri_resolver.py`**: Fix `ResolvedSkillURI.parse()` to handle flat `skill://{name}` URIs (treat netloc as skill_name when path is empty). Fix `SkillURIResolver.resolve()` post-ExtensionRegistry parsing.
- **`src/agentpool/capabilities/skill_manager_cap.py`**: Change hardcoded `skill://local/{name}` → `skill://{name}` in `list_skills()` and `list_commands()`.
- **`src/agentpool/host/factory.py`**: Restore `skills_tools_provider` injection in `_inject_pool_providers()`.
- **`src/agentpool_toolsets/builtin/skills.py`**: Migrate `load_skill`/`list_skills` MCP provider dispatch from `getattr` duck-typing to `SkillResource` protocol `isinstance` checks. Local skills path (`ctx.pool.skills`) stays as-is — `ctx.pool` → `host_context` migration deferred to separate change.
- **`src/agentpool/skills/skill_tool_manager.py`**: No changes — already functional, just not called.
- **Tests**: New end-to-end test files for per-skill tool registration, URI resolution, and `load_skill` propagation.
- **Specs**: 2 new spec files, 3 modified spec files.
