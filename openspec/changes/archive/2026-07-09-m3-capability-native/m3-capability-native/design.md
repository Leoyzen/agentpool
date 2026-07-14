## Context

AgentPool's tool/capability system is built on a custom `ResourceProvider` abstraction that predates and overlaps with pydantic-ai's native `Capability`/`Toolset` system. The `ResourceProvider` hierarchy consists of 7 implementations across 14 files totaling ~3860 LOC, with 52 consumers across the codebase. Each `ResourceProvider` implements `as_capability()` — a bridge method that wraps its tools/prompts/resources into a pydantic-ai `Capability`, adding an adapter tax on every agent run.

`SkillCapability(AbstractCapability)` already exists as a proof of concept — it demonstrates that a pydantic-ai native Capability can replace a ResourceProvider (`LocalResourceProvider`) without loss of functionality. This milestone extends that pattern to all 7 ResourceProvider implementations.

The migration is the third milestone (M3) of the six-milestone AgentWolf v1 foundation plan (RFC-0050). M3 tasks 1-14 may run in parallel with M2 (RunLoop). Task group 15 (RunLoop Integration) requires M2 completion as a hard dependency — M3 needs to modify RunLoop to construct AgentContext per turn and implement DelegationService, which cannot be done until M2's RunLoop is implemented. M1 (HostContext, AgentFactory, AgentRegistry) must be complete before M3 begins, as `AgentFactory.compile()` is the compilation entry point where Capabilities are wired.

**Key constraint**: Migration must be incremental. Agents that still use ResourceProvider must continue to work while individual providers are migrated one-by-one. The `AdapterToolsetFactory` bridge enables this coexistence.

## Goals / Non-Goals

**Goals:**
- All agents use pydantic-ai native `AbstractCapability` / `AbstractToolset` for tools, hooks, instructions, and lifecycle management
- Build 7 `ToolsetFactory` equivalents replacing each `ResourceProvider` implementation:
  - `MCPToolset` + `MCPCapability` (replaces `MCPResourceProvider`, includes `ResourceSource`)
  - `FunctionToolset` (replaces `StaticResourceProvider`)
  - `FilteredToolset` (replaces `FilteringResourceProvider`)
  - `CombinedToolset` (replaces `AggregatingResourceProvider`)
  - `SubagentCapability` + `SubagentToolset` (replaces `PoolResourceProvider`)
  - `CodeModeCapability` (replaces `CodeModeResourceProvider`)
  - `SkillCapability` (already exists, supplemented with `ResourceSource`)
- `AdapterToolsetFactory` bridge: wraps existing `ResourceProvider` as `Capability` during migration
- `AbstractCapability.on_change()` replaces `ResourceProvider` change signal system
- `ResourceSource` protocol for read-only data access (orthogonal to Capability)
- `AgentContext` frozen dataclass constructed by RunLoop at Turn time
- `DelegationService` limited interface exposed by RunLoop
- Entry-point registration via `agentpool.capabilities` group
- Physical deletion of all `ResourceProvider` code after migration complete

**Non-Goals:**
- RunLoop implementation (M2) — M3 task group 15 modifies RunLoop after M2 completes, but does not implement the RunLoop itself
- Config split — `HostConfig` / `AgentManifest` separation (M4)
- Multi-tenant isolation (M5)
- `FileResourceSource` implementation (future, beyond v1.0)
- Knowledge base / vector store integration (future Capability, not in scope)

## Decisions

### Decision 1: AdapterToolsetFactory bridges during migration

**Choice**: An `AdapterToolsetFactory` wraps any existing `ResourceProvider` as a pydantic-ai `AbstractCapability`, allowing old and new systems to coexist during incremental migration.

**Rationale**: 7 ResourceProviders with 52 consumers cannot be migrated atomically. The adapter lets each provider be migrated independently — migrated agents use native Capabilities directly, unmigrated agents transparently use the adapter. Once all providers are migrated, the adapter is deleted along with ResourceProvider itself.

**Alternative considered**: Big-bang migration (all 7 at once) — rejected because the blast radius is too large. A single broken provider would block all agents.

### Decision 2: AbstractCapability.on_change() replaces ResourceProvider signals

**Choice**: Change notification is delegated to each `AbstractCapability` via an optional `on_change()` method returning `AsyncIterator[ChangeEvent] | None`. No central `CapabilityRegistry` is introduced.

**Rationale**: A central registry that tracks all capabilities and broadcasts change events is architecturally identical to `AggregatingResourceProvider` + signal forwarding — the exact pattern being deleted. Change notification should be the responsibility of the Capability that knows when its own tools change (e.g., `SkillCapability` yields when SKILL.md files are added/removed), not a central authority.

`AgentFactory` subscribes to `on_change()` streams from compiled capabilities. When a change event arrives, Factory performs a local hot-swap — only the affected agent's capability is replaced, not the entire Host.

**Alternative considered**: Central `CapabilityRegistry` with broadcast — rejected because it recreates the deleted pattern under a new name.

### Decision 3: ResourceSource is orthogonal to Capability

**Choice**: `ResourceSource` is a separate `Protocol` for read-only data access (`list()`, `read(uri)`, `exists(uri)`, `on_change()`). The same object can implement both `AbstractCapability` (behavior) and `ResourceSource` (data).

**Rationale**: pydantic-ai's Capability system manages behavior (tools, hooks, instructions). But agents also need data access — MCP resources, skill content. pydantic-ai has no data abstraction. Rather than forcing data into tools (losing semantic clarity) or creating a parallel hierarchy (recreating ResourceProvider's mistake), `ResourceSource` is a second interface that capabilities can optionally implement. Two interfaces, two concerns, same object.

`MCPCapability` implements both: it provides MCP tools (via `AbstractCapability`) and MCP resources (via `ResourceSource`). This is not dual-abstraction — it's two axes on one object.

**Alternative considered**: Wrap all data access as tools — rejected because it loses URI-based addressing, content negotiation, and change notification semantics.

### Decision 4: AgentContext constructed by RunLoop, not Factory

**Choice**: `AgentContext` is a frozen dataclass constructed by RunLoop at Turn execution time, not by `AgentFactory` at compile time.

**Rationale**: `AgentContext` carries per-turn state: `session` (SessionState), `scope` (RunScope), `delegation` (DelegationService). These are runtime constructs that don't exist at compile time. Factory produces agents with Capabilities and an `AggregatedResourceSource`; RunLoop injects the per-turn `AgentContext` into the pydantic-ai `RunContext` when `turn.execute()` is called.

**Alternative considered**: Factory constructs AgentContext — rejected because session state and run scope are not available at compile time.

### Decision 5: DelegationService is a limited interface

**Choice**: `DelegationService` exposes only `spawn_subagent(name, prompt)` and `get_available_agents()`. Tools know WHAT they can do (spawn a subagent by name), not HOW RunLoop implements the spawning (queue, priority, background task).

**Rationale**: The current `PoolResourceProvider` gives tools full access to `AgentPool`, creating a layer violation (tools can reach storage, MCP servers, connection topology). `DelegationService` limits the interface to the two operations subagent tools actually need, enforcing layer boundaries. RunLoop implements `DelegationService` internally — it controls spawning mechanism, queueing, and lifecycle.

**Alternative considered**: Tools receive full `AgentPool` reference — rejected because it perpetuates the `agent_pool` backdoor that M1 is removing.

### Decision 6: Entry-point group for custom capabilities

**Choice**: Custom Capabilities are registered via the `agentpool.capabilities` entry-point group. `AgentFactory` discovers entry-point capabilities at compile time and makes them available for YAML `type:` references.

**Rationale**: Entry-point registration is the standard Python extensibility mechanism. It allows third-party packages to add new Capability types without modifying AgentPool. This aligns with pydantic-ai's own extension model and replaces the ad-hoc registration in `ResourceProvider` subclasses.

**Alternative considered**: Plugin directory scanning — rejected because entry points are more reliable, type-safe, and standard.

## Risks / Trade-offs

- **[Risk] Migration must be incremental** — 7 providers with 52 consumers cannot migrate simultaneously → Mitigated by `AdapterToolsetFactory` bridge. Each provider migrates independently. MEDIUM risk.
- **[Risk] `on_change()` may not cover all signal use cases** — ResourceProvider had 4 signal types (`tools_changed`, `prompts_changed`, `resources_changed`, `skills_changed`). A single `ChangeEvent` stream may lose granularity → `ChangeEvent` includes a `kind` field to distinguish change types. Capabilities that don't need change notification return `None`. LOW risk.
- **[Risk] Compile-time composition preferred over runtime lookup** — `AggregatedResourceSource` is built at compile time, not runtime. If capabilities are hot-swapped, the aggregated source must be rebuilt → `AgentFactory` handles this during `on_change()` hot-swap by rebuilding only the affected agent's aggregated source. LOW risk.
- **[Trade-off] Two-axis design (Capability + ResourceSource) vs single-hierarchy** — Two interfaces on one object is conceptually cleaner but requires developers to understand the orthogonality. Documented with clear examples in RFC-0050. ACCEPTABLE trade-off.
- **[Risk] AdapterToolsetFactory performance overhead** — During migration, unmigrated providers incur an extra adapter layer → Temporary, removed when migration completes. LOW risk.
- **[Trade-off] `DelegationService` limited interface may need expansion** — Future use cases (inter-agent messaging, shared memory) may require more methods → Start minimal, add methods as proven needs arise (YAGNI). ACCEPTABLE trade-off.

## Implementation Notes

The following notes document divergences and completions observed during actual implementation relative to the spec above.

### Note 1: Lazy compilation (Decision 4 refinement)

`AgentFactory.compile()` returns an empty `AgentRegistry()` — agents are not eagerly instantiated at compile time. Instead, `create_session_agent()` creates agents lazily per-session. This is architecturally correct because AgentPool's session model requires per-session isolation (separate MCP connections, conversation history). The design above assumed eager compilation; the implementation chose lazy. The Decision 4 rationale ("Factory constructs AgentContext — rejected") is unchanged; the lazy approach reinforces that factory produces registries, not running agents.

### Note 2: Task group 15 (RunLoop Integration) completed in M3

The design stated task group 15 was deferred to M2 (Non-Goals section, paragraph 2). In reality, after M2 was merged, task group 15 was completed in the same M3 PR:

- `RunLoopDelegationService` created in `capabilities/runloop_delegation.py`
- `_inject_agent_context()` method added to `RunHandle` in `orchestrator/run.py`, called per-turn in `start()`
- 10 tests in `tests/lifecycle/test_runloop_injection.py`
- All 5 subtasks (15.1–15.5) marked complete

### Note 3: `tools/factory.py` deleted as dead code

After all 7 ResourceProviders were migrated and deleted (task group 13), `tools/factory.py` (194 LOC, 6 classes: `ToolsetFactory` Protocol, `StaticToolsetFactory`, `AdapterToolsetFactory`, `MCPToolsetFactory`, `LocalSkillToolsetFactory`, `PoolToolsetFactory`) was found to have zero remaining imports across the codebase. It was deleted as part of M3 cleanup. This deletion was not anticipated by the spec.

### Note 4: `ToolManager` deleted

`src/agentpool/tools/manager.py` (364 LOC) was deleted. All `agent.tools.X` references were migrated to direct capability access: `agent._get_all_tools()`, `agent._builtin_provider`, `agent._worker_provider`, `agent._external_capabilities`. This deletion was not anticipated by the spec but follows naturally from the ResourceProvider removal (task group 13).

### Note 5: `agent_pool` backdoor partially remains

The spec (Decision 5 alternative) described removing the `agent_pool` property entirely. M2 added `DeprecationWarning` and migrated ~60 call sites. M3 migrated additional sites, but 18 files still reference `agent_pool` (primarily ACP server code: `acp_agent.py`, `session.py`, `handler.py`). The `DeprecationWarning` remains in place. Full migration is tracked as a follow-up before M4 begins.

### Note 6: `recompile()` not implemented

The M1 spec mentioned `recompile()`. This is deferred to M4 (hot reload) since it depends on M4's `ConfigRegistry` infrastructure. Not an M3 regression.

### Note 7: `Resource.source` field added

The spec (Decision 3 area) required a `source` field on the `Resource` dataclass. This was initially missing from the implementation and has now been retroactively added.

### Note 8: `HostContext.config_id` / `tenant_id` defaults

Already `"default"` as specified — no change needed. Confirmed during implementation.
