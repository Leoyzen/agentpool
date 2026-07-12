## Context

AgentPool's extension ecosystem (skills, MCP, commands) evolved as three independent subsystems with duplicated patterns for discovery, lifecycle, scoping, and change notification. RFC-0051 (rev 12, Oracle-verified) defines a unified architecture with 3 concepts: Resource Protocols (WHAT), Client Injection (HOW), and Scope (VISIBILITY). This design implements RFC-0051's 4-phase migration plan.

**Current state**: `MCPCapability` directly manages MCP connections. `SkillCapability` wraps individual skills. `SkillActivationCapability` is orphaned (wired but never called). `SkillMcpManager` has a dual connection path with `SessionConnectionPool`. 7 structural problems documented in RFC-0051.

**Constraints**: `AbstractCapability` (pydantic-ai's interface) cannot change. `SessionConnectionPool` transport management is reused, not replaced. Existing YAML config sections (`mcp_servers:`, `skills:`) are preserved. `m3-5-backdoor-cleanup` is archived (prerequisite completed).

## Goals / Non-Goals

**Goals:**
- Unify skill, MCP, and command under domain-specific Resource Protocol interfaces
- Replace fragmented infrastructure (`SkillProvider`, `SkillURIResolver._providers`, `SkillCommandRegistry`, `SkillMcpManager`) with `ExtensionRegistry`
- Merge `SkillCapability` + `SkillActivationCapability` into `SkillManagerCap`
- Add session-level scoping to enable M5 multi-tenant preparation
- Fix all 7 structural problems from RFC-0051
- Enable filesystem watching for skill hot-reload

**Non-Goals:**
- Redesign pydantic-ai's Toolset system
- Implement multi-tenant (M5) — only add session-level scoping
- Replace MCP protocol implementation (`MCPClient` stays)
- Create `AcpAgentCap` — ACP agents are delegation targets, not tool providers (dropped in RFC-0051 rev 11)
- Backward-compatible YAML config changes (existing sections preserved, no `sources:` section)

## Decisions

### D1: Domain-specific Resource Protocols over generic interfaces

**Decision**: Use `SkillResource`, `McpResource`, `CommandResource` instead of generic `ResourceSource`/`ToolProvider`/`CommandProvider`.

**Rationale**: Generic protocols require a mapping matrix (Resource × Client) to determine meaningful combinations. Domain-specific protocols encode this knowledge in the type system — `McpResource` methods return MCP-specific types, `SkillResource` methods return skill-specific types. `isinstance(cap, SkillResource)` is more expressive than `isinstance(cap, ResourceSource)`.

**Alternative considered**: Generic `ResourceSource`/`ToolProvider`/`CommandProvider` (RFC-0051 rev 1). Rejected because the meaningful-combination matrix showed that generic protocols add ceremony without value — `ToolProvider` on a local skill is meaningless, `McpResource` without an `MCPClient` is meaningless.

### D2: Client Injection replaces Transport abstraction

**Decision**: Capabilities receive `MCPClient` (or `None` for local) as constructor parameters. No `ExtensionTransport` Protocol or `TransportHandle`.

**Rationale**: Protocol server/client pairs (MCP, ACP) ARE the transport. MCP-over-ACP reuses the ACP client as transport — this is already how RFC-0033 works. A separate transport abstraction adds indirection without value.

**Alternative considered**: `ExtensionTransport` Protocol with `TransportHandle` (RFC-0051 rev 1). Rejected because it duplicates what `MCPClient`/`ACPClient` already provide.

### D3: SkillManagerCap extends CombinedToolsetCapability

**Decision**: `SkillManagerCap(CombinedToolsetCapability, SkillResource, CommandResource)` — inherits `get_toolset()`, `on_change()`, `__aenter__`/`__aexit__` from `CombinedToolsetCapability`.

**Rationale**: Reuses existing infrastructure for tool merging and change stream merging. `SkillManagerCap` adds skill-specific concerns (instruction injection via `before_model_request`, `SkillResource`/`CommandResource` aggregation) on top of the existing composition pattern. Local skills are held as `Skill` objects directly.

**Alternative considered**: `SkillManagerCap` as standalone `AbstractCapability`. Rejected because it would reimplement tool merging and change stream merging that `CombinedToolsetCapability` already provides.

### D4: Drop AcpAgentCap

**Decision**: Do not implement `AcpAgentCap`. ACP agents remain delegation targets via existing `SubagentCapability`.

**Rationale**: ACP's tool direction is reversed (agent requests client to execute, not client calling agent's tools). ACP has no `tools/list`, `resources/list`, or `prompts/list` primitives. MCP-over-ACP is a transport concern handled by `McpServerCap` with ACP-backed `MCPClient` from `SessionConnectionPool`.

**Alternative considered**: `AcpAgentCap` implementing `McpResource` via MCP-over-ACP tunnel (RFC-0051 rev 1-10). Rejected because it duplicates `McpServerCap` functionality when ACP transport is used.

### D5: ExtensionRegistry split across Pool and HostContext

**Decision**: Pool-level capabilities stored on `AgentPool`. Session/agent/turn-level capabilities stored on `HostContext`.

**Rationale**: Pool-level capabilities (global MCP servers, default skills) are shared across all sessions. Session/agent/turn-level capabilities are per-session and need `HostContext` for DI. This mirrors the existing `McpConfigSnapshot` 4-level partition.

**Alternative considered**: Single `ExtensionRegistry` on `AgentPool` with scope IDs. Rejected because it requires passing scope IDs everywhere and doesn't leverage `HostContext`'s DI pattern.

### D6: Direct replacement of AgentContext.resources (no adapter)

**Decision**: In Phase 4, directly replace `AgentContext.resources` with `AgentContext.extension_registry`. Codebase analysis shows `AgentContext.resources` is SET at 1 location (`run.py:394`) but never READ — 0 consumer call sites. The migration is a simple field removal, not a big-bang migration.

**Rationale**: AgentPool is pre-v1 with no external consumers of `AgentContext.resources`. The field is set from `_collect_resource_sources()` in `AgentFactory` but no code ever calls `.resources.list()`, `.resources.read()`, `.resources.exists()`, or `.resources.on_change()` on an `AgentContext` instance. Removing the dead field and replacing with `extension_registry` is a 5-line change. An adapter would add a class, tests, and deletion steps without any real value.

### D7: Lazy vs non-lazy MCP connection

**Decision**: Default (non-lazy) connects at `get_toolset()` → `list_tools()` → `_ensure_client()` during compilation. Lazy mode (`config.lazy: true`) connects at first `call_tool()`, with tool list from config `tools:` field.

**Rationale**: `list_tools()` needs a connection to call MCP `tools/list`. Without lazy mode, the tool list would be empty at compilation time. Lazy mode trades live tool discovery for deferred connection.

## Risks / Trade-offs

- **[Large migration surface]** → 4 phases, each independently shippable. Old classes kept as deprecated aliases during transition.
- **[SkillManagerCap complexity]** → Merges 3 components into 1. Risk of god-class. Mitigation: `CombinedToolsetCapability` base handles tool/change merging; `SkillManagerCap` only adds skill-specific concerns.
- ** [ExtensionRegistry placement]** → Pool/HostContext split may cause confusion about where to register. Mitigation: clear API — pool-level sources via `AgentPool`, session+ via `HostContext`.
- **[ChangeEvent widening]** → `kind: str` (was `Literal`) loses type safety. Mitigation: documented in RFC, `ChangeKind` Literal preserved for known values.
- **[Dead code removal]** → `SkillsInstructionConfig.mode` deletion may break configs that set `mode: full`. Mitigation: deprecation warning in Phase 2, removal in Phase 4. Field is read at pool.py:182 but stored value never used for behavior.

## Migration Plan

### Phase 1: MCP Source Extraction (~600 LOC)
- Create Resource Protocol interfaces + `ChangeObservable`
- Create `McpServerCap` wrapping `MCPClient` via DI
- Add `SessionConnectionPool.get_client()`
- Replace `MCPCapability` with `McpServerCap` in `AgentFactory.compile()`
- **Rollback**: Keep `MCPCapability` as deprecated alias

### Phase 2: Skill Manager Extraction (~800 LOC)
- Create `SkillManagerCap` extending `CombinedToolsetCapability`
- Move `SkillMcpManager` logic into `SkillManagerCap` child management
- Replace `SkillCapability` + `SkillActivationCapability` with `SkillManagerCap`
- Deprecate `SkillsInstructionConfig.mode` (add warning, stop reading field; deletion in Phase 4)
- **Rollback**: Keep old classes as deprecated aliases

### Phase 3: Cross-Provision (~400 LOC)
- `McpServerCap` implements `CommandResource` (MCP prompts → commands)
- `McpServerCap` implements `SkillResource` (MCP resources → skill:// URIs)
- Replace `SkillProvider` Protocol with `isinstance(cap, SkillResource)`
- Wire `ChangeEvent` propagation from MCP notifications to skill re-discovery
- **Rollback**: Revert `CommandResource`/`SkillResource` on `McpServerCap`

### Phase 4: ExtensionRegistry and Scoping (~800 LOC)
- Create `ExtensionRegistry` with 4-level scope storage
- Migrate `SkillURIResolver._providers` to `ExtensionRegistry.resolve_uri()`
- Migrate `AggregatedResourceSource` to `ExtensionRegistry.get_visible_capabilities()`
- Add session-level scoping
- Add filesystem watcher (`watchdog`) for skill hot-reload
- Delete `SkillCommandRegistry` (replaced by `ExtensionRegistry.get_command_resources()`)
- Replace `AgentContext.resources` with `AgentContext.extension_registry` (field is set but never read — 0 consumer call sites, simple removal)
- **Rollback**: Revert to `AggregatedResourceSource` + `SkillURIResolver`

### Dependencies
```
Phase 1 → Phase 2 → Phase 3 → Phase 4
```
Each phase delivers value independently. `m3-5-backdoor-cleanup` is archived (prerequisite completed).

## Open Questions

All open questions from RFC-0051 are resolved (Q1-Q9). See RFC-0051 revision history for details.
