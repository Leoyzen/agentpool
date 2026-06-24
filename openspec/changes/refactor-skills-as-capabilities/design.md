## Context

### Current State

Skills are discovered via `SkillsRegistry` → `LocalResourceProvider` (filesystem) or `MCPResourceProvider` (MCP-over-ACP). They inject instructions into agent prompts via `SkillsInstructionProvider.get_instructions()` → `<available-skills>` XML. The `SkillsTools` toolset provides `load_skill`/`list_skills` tools.

Skills are **purely informational** — they cannot declare tools, MCP servers, or lifecycle hooks. The `allowed_tools` field exists as a free-form `str` that is displayed to the LLM but never parsed or enforced.

pydantic-ai 1.102.0 introduced `AbstractCapability` — a composable middleware system with 15+ lifecycle hooks, tool provision, instruction injection, and ordering control. AgentPool's `ResourceProvider.as_capability()` already bridges to this system via the `Toolset` capability wrapper.

### Constraints

- Backward compatibility: existing YAML configs (`skills.paths`, `skills.instruction.mode`) must work unchanged
- The `Skill` model uses `extra="forbid"` — new frontmatter fields must be explicitly added; existing fields' types must not change in breaking ways
- Skills are discovered from multiple sources (local filesystem, MCP-over-ACP, virtual/prompt-based)
- The `SkillsInstructionProvider` injection mode (`off`/`metadata`/`full`) must be preserved
- `load_skill` tool behavior must not change for existing callers
- pydantic-ai's `get_toolset()` is called once at agent construction time — tools cannot be dynamically added mid-run

## Goals / Non-Goals

**Goals:**
- Skills become `AbstractCapability` subclasses that participate in pydantic-ai's middleware chain
- Skills can declare MCP servers (`mcp_servers` frontmatter field) with lazy connection and cleanup
- Skills can declare native Python tools (`tools` frontmatter field) via import path
- `allowed_tools` is enforced as a structured allowlist via `get_wrapper_toolset()`
- Existing config and tool APIs continue to work identically
- Redundant code paths (dual instruction injection) are unified

**Non-Goals:**
- Changing how skills are discovered (filesystem, MCP, virtual) — discovery is unchanged
- Changing the SKILL.md format beyond adding new optional fields
- Implementing per-skill tool/MCP YAML config at the agent level (skills declare their own)
- Supporting skill-level `get_model_settings()` or `wrap_run()` hooks in the initial implementation
- Auto-activation of skills (skills still activated via `load_skill` tool call or explicit config)
- YAML-level `SkillCapability` configuration via `config.capabilities` (skills are always auto-created from discovered SKILL.md files)

## Decisions

### Decision 1: `SkillCapability` as a standalone `AbstractCapability` subclass

**Choice**: Create `SkillCapability(AbstractCapability[AgentDepsT])` that wraps a single `Skill` instance.

**Rationale**: Each skill maps naturally to one capability. `CombinedCapability` handles composition when multiple skills are active. This matches pydantic-ai's design — `MCP` capability wraps one MCP server, `NativeTool` wraps one native tool.

**Alternatives considered**:
- *One capability for all skills* — rejected: `CombinedCapability` already handles multi-skill composition; a monolithic capability would need to reimplement merging logic
- *Extend `Toolset` capability* — rejected: `Toolset` only provides tools; skills need instructions + tools + MCP + lifecycle hooks

### Decision 2: Tool pre-registration at construction time, activation at `load_skill`

**Choice**: All discovered skills' tools and MCP servers are **declared** at agent construction time via `SkillCapability.get_toolset()`. The LLM sees all skill tools from the start. `load_skill` **activates** the skill by loading instructions and lazily connecting MCP backends — it does NOT add new tools mid-run.

**Rationale**: pydantic-ai's toolset is assembled once at `PydanticAgent` construction time and cannot be mutated mid-run. `load_skill` is a tool called during a run. By pre-registering all skill tools at construction time, the LLM can always discover them. `load_skill` ensures the backend infrastructure (MCP connections, Python imports) is ready when the LLM calls those tools.

**Tool lifecycle**:
```
Construction:  get_toolset() → declare all skill tools (LLM can see them)
Run starts:    get_wrapper_toolset() → apply allowed_tools filter (per-run)
load_skill:    connect MCP servers (lazy), import Python tools
               → inject skill instructions into prompt
Run ends:      on_run_ended() → disconnect MCP servers, cleanup (session_id from ctx)
```

**Alternatives considered**:
- *Register tools only on `load_skill`* — rejected: impossible with pydantic-ai's static toolset
- *Use `prepare_tools()` for dynamic tool injection* — rejected: `prepare_tools()` can only filter existing tools, not add new ones

### Decision 3: Instructions flow through `get_instructions()`, not `get_toolset()`

**Choice**: `SkillCapability.get_instructions()` returns raw skill instruction content (no XML wrapper). `SkillsInstructionProvider` remains responsible for the `<available-skills>` XML catalog format, collecting instructions from all `SkillCapability` instances and wrapping them.

**Rationale**: `SkillsInstructionProvider` serves as the aggregator/catalog — it lists ALL skills in `<available-skills>` XML. `SkillCapability.get_instructions()` provides per-skill content. Keeping them separate avoids double-wrapping and maintains the existing XML format that protocol servers depend on.

**Alternatives considered**:
- *Put everything in `get_toolset()`* — rejected: tools and instructions have different lifecycle (instructions are static, tools are per-run-step)
- *Have each SkillCapability return XML* — rejected: would cause double-wrapping; no single owner of the `<available-skills>` wrapper

### Decision 4: `allowed_tools` via `@field_validator`, enforced via `get_wrapper_toolset()`

**Choice**: Keep `allowed_tools` as `str | None` on the `Skill` model. Add a `@field_validator` that normalizes both `str` (space-separated) and `list[str]` inputs to a parsed `list[str]` at the capability level. Enforce via `get_wrapper_toolset()` → `FilteredToolset` with a dynamic filter that checks skill activation state.

**Rationale**: Changing the type from `str` to `list[str]` would break every existing SKILL.md file with `allowed-tools: "bash, read"` (a string, not a list). A validator preserves backward compatibility while enabling structured enforcement. The `FilteredToolset.filter_func` receives `(RunContext, ToolDefinition)` and can check whether the current skill is active — supporting mid-run `load_skill` changes.

**Alternatives considered**:
- *Type change to `list[str]`* — rejected: **BREAKING** — existing YAML `allowed-tools: "bash, read"` would fail Pydantic validation
- *`prepare_tools()` hook* — rejected: only sees function tools, not output tools or MCP tools
- *Informational only (current behavior)* — rejected: doesn't actually enforce restrictions

### Decision 5: `SkillMcpManager` for per-skill MCP lifecycle

**Choice**: A dedicated manager that creates `MCPResourceProvider` per skill MCP server, with lazy connection, idle timeout, and thread-safe connection serialization.

**Rationale**: Skill-level MCP servers have different lifecycle from pool-level (permanent) and session-level (ACP session scope). They are declared at construction time, connected lazily on first tool call, and disconnected when the run ends. Thread safety is critical — concurrent sessions may call `load_skill` for the same skill simultaneously.

**Thread safety**: Per-server-name `asyncio.Lock` serializes connection attempts. The second caller awaits the first connection's result rather than starting a duplicate server.

**Alternatives considered**:
- *Reuse `MCPManager`* — rejected: `MCPManager` manages pool-level servers with persistent connections. Skill MCP servers need activation-scoped lifecycle
- *Inline in `SkillCapability`* — rejected: would couple capability logic with MCP connection management

### Decision 6: Capability injection at `get_agentlet()` time with explicit ordering

**Choice**: `SkillCapability` instances are created during `get_agentlet()` and appended to the `tool_capabilities` list after MCP capabilities. `get_ordering()` returns `wrapped_by=[ProcessHistory, NativeTool]` to ensure precise positioning, NOT `position='innermost'`.

**Rationale**: `position='innermost'` would place `SkillCapability` at the END of the chain (after ProcessHistory, NativeTool, and user-configured capabilities), contradicting the desired position between MCP and ProcessHistory. Using `wrapped_by` gives precise control.

Position in the chain:
```
1. Tool providers (Toolset)
2. Hooks
3. Deferred bridge
4. Approval bridge
5. MCP servers (MCP)
6. ✨ SkillCapability instances (NEW) — wrapped_by=[ProcessHistory, NativeTool]
7. ProcessHistory
8. NativeTool
9. User-configured capabilities
```

### Decision 7: Companion `mcp.json` file

**Choice**: Skills may optionally include an `mcp.json` file in their directory as an alternative to the `mcp_servers` YAML frontmatter field. When both exist, `mcp.json` takes precedence.

**Format**: Standard MCP server config JSON:
```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp", "--headless"]
    },
    "remote-api": {
      "url": "https://api.example.com/mcp",
      "headers": {"Authorization": "Bearer ${API_KEY}"}
    }
  }
}
```

**Rationale**: This pattern is used by oh-my-opencode and the broader MCP ecosystem. It keeps complex MCP config separate from SKILL.md instructions, and mirrors the global MCP config format. Supports both stdio (`command`/`args`) and HTTP (`url`/`headers`) transports with `${VAR}` env var expansion.

**Alternatives considered**:
- *Frontmatter-only* — rejected: complex MCP configs clutter SKILL.md
- *No companion file* — rejected: would force all MCP config into YAML frontmatter

## Risks / Trade-offs

- **Risk**: Skill MCP server connections leak if `load_skill` is called but cleanup doesn't fire
  - **Mitigation**: `SkillMcpManager` tracks all connections by `(session_id, skill_name, server_name)`. `SkillCapability.on_run_ended(ctx)` hook triggers cleanup — `session_id` is obtained from `ctx.deps.session_id`. Fallback: idle timeout (5 min default). Per-server-name `asyncio.Lock` prevents duplicate connections.

- **Risk**: Tool name collisions between skill tools and agent tools
  - **Mitigation**: Skill tools are prefixed with `{skill_name}_` via `PrefixedToolset`. Collisions are detected at registration time and logged as warnings. Native skill tools and MCP tools from the same skill use distinct sub-prefixes (`{skill_name}__tool__` and `{skill_name}__mcp__`) to avoid intra-skill collisions.

- **Risk**: `Skill` model `extra="forbid"` rejects new fields in old code that parses SKILL.md with new fields
  - **Mitigation**: New fields are added to the `Skill` model in this change. No old code exists that would parse new-format SKILL.md files before this change is deployed.

- **Risk**: All skill tools pre-registered → large tool list if many skills with many MCP servers
  - **Mitigation**: MCP connections are lazy (not started until first tool call). Skill tools are prefixed, keeping them identifiable. `allowed_tools` filtering reduces effective tool count per-skill. If tool count becomes a problem, a future change could add `skills.max_tools` config.

- **Trade-off**: `SkillsInstructionProvider` becomes thinner but still exists as a backward-compat layer
  - **Acceptable**: It provides the `<available-skills>` XML wrapper format that protocol servers depend on. The actual instruction content comes from `SkillCapability`.

## Migration Plan

1. Add `mcp_servers` and `tools` fields to `Skill` model (optional, default `None`)
2. Add `@field_validator` on `allowed_tools` to normalize `str` → parsed `list[str]` (no type change)
3. Create `SkillCapability`, `SkillMcpManager`, `SkillToolManager` in `src/agentpool/skills/`
4. Modify `SkillsInstructionProvider` to collect instructions from `SkillCapability` instances while keeping the `<available-skills>` XML wrapper
5. Modify `Agent.get_agentlet()` to create and inject `SkillCapability` instances at position 6
6. Modify `SkillsTools.load_skill` to trigger MCP backend activation and instruction injection
7. Implement `SkillCapability.on_run_ended()` for cleanup (session_id from `ctx.deps`)
8. Remove redundant code: old `allowed_tools` string display, dual instruction paths
9. No config migration needed — all new fields are optional; `allowed_tools` accepts both `str` and `list[str]`

**Rollback**: Revert to previous commit. No database or persistent state changes. Existing configs are unaffected.

## Open Questions

- Should `SkillCapability` instances be cached across `get_agentlet()` calls within the same pool session? (Currently: created fresh each time)
- Should MCP servers declared by skills support OAuth 2.1 authentication? (Currently: `command`/`args`/`url`/`headers` only)
- Should the `mcp.json` format also support the top-level `mcpServers` key or only direct server definitions?
