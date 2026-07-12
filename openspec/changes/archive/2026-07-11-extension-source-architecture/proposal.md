## Why

AgentPool treats skills, MCP servers, and commands as three separate subsystems with duplicated discovery, lifecycle, scoping, and change-notification mechanisms. This has produced 7 structural problems where cross-provision scenarios (MCP-hosted skills, skill-embedded MCP, MCP-over-ACP) require ad-hoc workarounds that bypass the capability compilation pipeline. RFC-0051 (Oracle-verified, rev 12) defines the target architecture; this change implements it.

## What Changes

- **NEW**: `ExtensionRegistry` — central registry with 4-level scope (Pool > Session > Agent > Turn), replaces `SkillProvider`, `SkillURIResolver._providers`, `SkillCommandRegistry`, and manual `AggregatedResourceSource` construction
- **NEW**: Three `@runtime_checkable` Resource Protocol interfaces: `SkillResource` (list_skills, read_skill, skill_exists), `McpResource` (list_tools, call_tool, list_resources, read_resource, resource_exists), `CommandResource` (list_commands, get_command)
- **NEW**: `ChangeObservable` Protocol — `on_change() -> AsyncIterator[ChangeEvent] | None`, with `merge_change_streams()` sentinel-based stream merging
- **NEW**: `Scope` (frozen dataclass) and `ScopeLevel` (Enum: POOL/SESSION/AGENT/TURN)
- **NEW**: `McpServerCap` — replaces `MCPCapability`, implements `McpResource + SkillResource + CommandResource + ChangeObservable`, receives `MCPClient` via DI from `SessionConnectionPool`
- **NEW**: `SkillManagerCap` — merges `SkillCapability` + `SkillActivationCapability` into one per-agent capability extending `CombinedToolsetCapability`. Manages ALL skills: `get_instructions()` returns metadata, `before_model_request()` injects matched skills, optional `matcher_fn` for dynamic selection
- **BREAKING**: Delete `SkillCapability`, `SkillActivationCapability` (merged into `SkillManagerCap`)
- **BREAKING**: Delete `SkillMcpManager` (replaced by `SessionConnectionPool` + child `McpServerCap`)
- **BREAKING**: Delete `SkillProvider` Protocol (subsumed by `SkillResource`)
- **BREAKING**: Delete `SkillCommandRegistry` (replaced by `ExtensionRegistry.get_command_resources()`)
- **BREAKING**: Delete `SkillsInstructionConfig.mode` (read at pool.py:182 but stored value never used for behavior). Deprecation warning in Phase 2, removal in Phase 4.
- **BREAKING**: Modify existing `ChangeEvent` (`change_event.py:26-35`) — widen `kind: str` (was `Literal`), add `source_uri: str = ""`, retain `capability_name: str`
- **MODIFY**: `SessionConnectionPool` — add `get_client(config) -> MCPClient` wrapping pooled transport
- **MODIFY**: `AgentFactory` — compile `McpServerCap`/`SkillManagerCap` from `ExtensionRegistry` instead of ad-hoc skill infrastructure
- **KEEP**: `AbstractCapability`, `MCPClient`, `SessionConnectionPool` transport management, `SkillsRegistry` (filesystem discovery), existing YAML config sections (`mcp_servers:`, `skills:`)

## Capabilities

### New Capabilities
- `extension-registry`: Central registry for extension capabilities with 4-level scope, Resource Protocol queries, URI routing, and change-stream merging
- `resource-protocols`: Domain-specific Protocol interfaces (`SkillResource`, `McpResource`, `CommandResource`, `ChangeObservable`) that describe what capabilities provide
- `mcp-server-cap`: MCP server capability with client DI, implementing all Resource Protocols via delegated MCPClient calls
- `skill-manager-cap`: Per-agent skill manager merging instruction injection, dynamic activation, tool aggregation, and slash command provision

### Modified Capabilities
- `mcp-session-lifecycle`: `SessionConnectionPool` gains `get_client()` method for client injection into `McpServerCap`
- `agent-factory`: Compilation reads from `ExtensionRegistry` instead of `AgentPool` skill properties; creates `McpServerCap`/`SkillManagerCap` adapters
- `host-context`: `ExtensionRegistry` reference split — pool-level on `AgentPool`, session+ on `HostContext`

## Impact

- **Code**: ~2000-3000 LOC across 4 phases. Core changes in `src/agentpool/capabilities/`, `src/agentpool/skills/`, `src/agentpool/host/`, `src/agentpool/messaging/`
- **Deletions**: `SkillCapability`, `SkillActivationCapability`, `SkillMcpManager`, `SkillProvider` Protocol, `SkillCommandRegistry`, `SkillsInstructionConfig.mode`
- **APIs**: `AgentContext.resources` (set but never read — 0 consumer call sites) replaced by `AgentContext.extension_registry` in Phase 4. Simple field removal, no adapter needed.
- **Dependencies**: No new external dependencies except `watchdog` (Phase 4 only, optional, for filesystem watching). Add to optional dependencies in pyproject.toml.
- **RFC**: `docs/rfcs/draft/RFC-0051-extension-source-architecture.md` (rev 12, Oracle-verified)
- **Related changes**: `m3-5-backdoor-cleanup` (prerequisite — archived, removes `AgentPool` backdoor references)
- **Migration**: 4 phases, each independently shippable. `MCPCapability`, `SkillCapability`, `SkillActivationCapability` kept as deprecated aliases during transition. `SkillMcpManager`, `SkillProvider`, `SkillCommandRegistry` deleted directly in Phase 4 (no alias — functionality fully replaced).
