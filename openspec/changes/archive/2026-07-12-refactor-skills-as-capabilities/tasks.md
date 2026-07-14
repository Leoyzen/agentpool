## 1. Data Model Extension

- [ ] 1.1 Add `SkillMcpServerConfig` and `SkillToolConfig` models to `src/agentpool_config/skills.py`
- [ ] 1.2 Add `mcp_servers: dict[str, SkillMcpServerConfig] | None` and `tools: list[SkillToolConfig] | None` fields to `Skill` model in `src/agentpool/skills/skill.py`
- [ ] 1.3 Add `@field_validator` on `allowed_tools` to normalize both `str` and `list[str]` inputs (type stays `str | None`); add `parsed_allowed_tools() -> list[str]` helper method
- [ ] 1.4 Add `mcp.json` companion file loading — parse `{"mcpServers": {...}}` format, support `${VAR}` env var expansion, precedence over frontmatter `mcp_servers`
- [ ] 1.5 Update SKILL.md test fixtures with new optional fields (`mcp_servers`, `tools`, `allowed-tools` as list)

## 2. SkillCapability Implementation

- [ ] 2.1 Create `src/agentpool/skills/capability.py` with `SkillCapability(AbstractCapability)` class
- [ ] 2.2 Implement `SkillCapability.get_instructions()` — return skill instruction content as `AgentInstructions` (no XML wrapper)
- [ ] 2.3 Implement `SkillCapability.get_toolset()` — return `CombinedToolset` with `PrefixedToolset(prefix="{skill_name}__tool__")` for native tools and `PrefixedToolset(prefix="{skill_name}__mcp__")` for MCP tools
- [ ] 2.4 Implement `SkillCapability.get_wrapper_toolset()` — return `FilteredToolset` with dynamic filter that checks skill activation state when `allowed_tools` is set
- [ ] 2.5 Implement `SkillCapability.get_ordering()` — return `CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])` for position after MCP, before history processors
- [ ] 2.6 Implement `SkillCapability.on_run_ended()` — trigger `SkillMcpManager.cleanup(session_id)` on run completion (session_id from `ctx.deps`)

## 3. SkillMcpManager

- [ ] 3.1 Create `src/agentpool/skills/skill_mcp_manager.py` with connection lifecycle management
- [ ] 3.2 Implement lazy MCP server connection (connect on first tool call, not on activation)
- [ ] 3.3 Implement idle timeout (default 5 minutes, configurable)
- [ ] 3.4 Implement retry with exponential backoff (3 retries)
- [ ] 3.5 Implement cleanup on run end — terminate all connections for the session (triggered by `SkillCapability.on_run_ended()`)
- [ ] 3.6 Support both stdio (`command` + `args`) and HTTP (`url`) transports
- [ ] 3.7 Implement thread safety — per-server-name `asyncio.Lock` to serialize concurrent connection attempts

## 4. SkillToolManager

- [ ] 4.1 Create `src/agentpool/skills/skill_tool_manager.py` with dynamic Python tool import
- [ ] 4.2 Implement `import_tool(config: SkillToolConfig) -> Tool` — resolve import path, wrap as AgentPool `Tool`
- [ ] 4.3 Handle import errors gracefully — log warning, skip tool, don't block skill activation

## 5. SkillsInstructionProvider Refactor

- [ ] 5.1 Modify `SkillsInstructionProvider` to collect instruction content from `SkillCapability` instances while keeping the `<available-skills>` XML wrapper
- [ ] 5.2 Preserve existing `<available-skills>` XML wrapper format (provider owns the wrapper, not SkillCapability)
- [ ] 5.3 Add `<tools>` and `<mcp_servers>` elements in metadata mode when skill has them
- [ ] 5.4 Remove redundant instruction generation code that's now in `SkillCapability`
- [ ] 5.5 Ensure `off`/`metadata`/`full` injection modes still work correctly

## 6. Agent Integration

- [ ] 6.1 Modify `Agent.get_agentlet()` in `src/agentpool/agents/native_agent/agent.py` to create `SkillCapability` instances
- [ ] 6.2 Inject `SkillCapability` instances into `tool_capabilities` list after MCP capabilities (position 6 in chain)
- [ ] 6.3 Handle case where no skills are configured — no `SkillCapability` instances created
- [ ] 6.4 Handle session-level MCP skills (ACP-over-MCP) — their skills also become capabilities

## 7. SkillsTools Update

- [ ] 7.1 Modify `load_skill` in `src/agentpool_toolsets/builtin/skills.py` to trigger `SkillMcpManager` activation
- [ ] 7.2 Modify `load_skill` to trigger `SkillToolManager` activation
- [ ] 7.3 Include activated tools and MCP server descriptions in `load_skill` response
- [ ] 7.4 Ensure backward compatibility — `load_skill` for skills without tools/MCP behaves identically

## 8. Code Cleanup

- [ ] 8.1 Remove old `allowed_tools` free-form string display from `SkillsTools.load_skill` response (replaced by structured enforcement)
- [ ] 8.2 Remove dual instruction injection paths — unify through `SkillCapability.get_instructions()`
- [ ] 8.3 Remove any dead code in `SkillsInstructionProvider` that's fully delegated to `SkillCapability`
- [ ] 8.4 Clean up unused imports and type annotations

## 9. Tests

- [ ] 9.1 Create `tests/test_skill_capability.py` — unit tests for `SkillCapability`
- [ ] 9.2 Add test: `SkillCapability` with no tools/MCP returns `None` from `get_toolset()`
- [ ] 9.3 Add test: `SkillCapability` with `mcp_servers` returns `PrefixedToolset(prefix="{name}__mcp__")` wrapping `CombinedToolset` of MCP toolsets
- [ ] 9.4 Add test: `SkillCapability` with `tools` returns `PrefixedToolset(prefix="{name}__tool__")` wrapping `FunctionToolset`
- [ ] 9.5 Add test: `SkillCapability` with BOTH `tools` AND `mcp_servers` returns `CombinedToolset` with both `PrefixedToolset` instances
- [ ] 9.6 Add test: `SkillCapability` with `allowed_tools` returns `FilteredToolset` from `get_wrapper_toolset()`
- [ ] 9.7 Add test: `SkillCapability.get_ordering()` returns `CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])`
- [ ] 9.8 Add test: `SkillCapability.on_run_ended()` triggers `SkillMcpManager.cleanup()` with correct session_id from ctx.deps
- [ ] 9.9 Add test: `SkillMcpManager` lazy connection, idle timeout, cleanup
- [ ] 9.10 Add test: `SkillMcpManager` thread safety — concurrent `load_skill` serialized by `asyncio.Lock`
- [ ] 9.11 Add test: `SkillMcpManager` retry with exponential backoff on connection failure
- [ ] 9.12 Add test: `SkillToolManager` dynamic import success and error handling
- [ ] 9.13 Add test: `Skill.allowed_tools` parses both `"bash, read"` (string) and `["bash", "read"]` (list) formats
- [ ] 9.14 Add test: `mcp.json` loading — precedence over frontmatter, env var expansion
- [ ] 9.15 Add test: `Skill.from_skill_dir()` parses `mcp_servers` and `tools` fields from SKILL.md frontmatter
- [ ] 9.16 Update existing `tests/test_skills.py` — verify backward compatibility
- [ ] 9.17 Add integration test: `load_skill` with MCP servers triggers lazy backend preparation
- [ ] 9.18 Add integration test: `load_skill` without tools/MCP preserves identical response format
- [ ] 9.19 Add integration test: `get_agentlet()` includes `SkillCapability` in capabilities list at correct position (after MCP, before ProcessHistory)
- [ ] 9.20 Add integration test: `SkillCapability` with `allowed_tools` filters tools at runtime when skill is active
- [ ] 9.21 Add error path test: MCP server connection failure during lazy activation

## 10. Documentation

- [ ] 10.1 Update `AGENTS.md` with new skill architecture patterns
- [ ] 10.2 Document `mcp_servers` and `tools` SKILL.md frontmatter format
- [ ] 10.3 Add example SKILL.md with `mcp_servers` and `tools` to test fixtures
