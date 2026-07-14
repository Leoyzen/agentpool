## Context

AgentPool's current architecture (v0.x) has a single `AgentPool` class that serves as registry, infrastructure owner, agent factory, and dependency injector. Every `MessageNode` (agent or team) holds a direct `agent_pool` reference, creating a backdoor that spans all architectural layers. Oracle assessment (RFC-0050, Appendix A) identified this as the #1 architectural risk: it blocks tenant isolation, makes layer boundaries permeable, and prevents clean dependency injection.

The current `AgentPool` class (~825 lines) has 151 importers across 85 test files. It owns: MCP server processes, storage connections, skills registry, prompt manager, agent registry, connection topology, and the agent creation logic. This monolithic structure must be decomposed before RunLoop (M2) or multi-tenant (M5) work can proceed.

**Key constraint**: This milestone produces NO user-visible changes. The `async with AgentPool(...)` API, YAML config format, and CLI commands must remain identical. This is purely internal restructuring.

## Goals / Non-Goals

**Goals:**
- Extract `HostContext` frozen dataclass carrying only dependency handles
- Extract `AgentFactory` as standalone compilation service with `(manifest, host_context) → AgentRegistry`
- AgentPool becomes a thin facade delegating to AgentFactory
- `MessageNode.agent_pool` property returns HostContext (compatibility shim) — actual backdoor removal (replacing all call sites) is M1b, parallel with M2
- All existing tests pass without modification
- Config model unchanged — flat `AgentsManifest` stays as-is

**Non-Goals:**
- Config split (HostConfig/AgentManifest) — deferred to M4
- `MessageNode.agent_pool` full removal — call site migration is M1b (parallel with M2)
- ResourceProvider deletion — M3
- RunLoop implementation — M2
- ModelRegistry/ModelCache full implementation — can be stub/passthrough for now
- AgentContext / DelegationService — M2 (needs RunLoop)

## Decisions

### Decision 1: HostContext as frozen dataclass, not Protocol

**Choice**: `HostContext` is a `@dataclass(frozen=True)` carrying typed handles.

**Rationale**: Frozen dataclass is immutable, hashable, and type-safe. Protocol would allow structural subtyping but loses the "this IS the dependency bundle" clarity. Agents receive HostContext as a constructor parameter, not via runtime lookup.

**Alternative considered**: Protocol with `isinstance()` check — rejected because HostContext is a concrete data bundle, not an interface. Multiple implementations don't make sense (there's one set of dependencies per host).

### Decision 2: AgentFactory receives (manifest, host_context) as method parameters, not constructor

**Choice**: `AgentFactory.compile(manifest, host_context)` — parameters on the method, not `__init__`.

**Rationale**: Factory is a standalone service, not owned by AgentHost. It maintains an internal compilation cache for diff-based recompile. If manifest were a constructor param, recompile would need a new Factory instance, losing the cache. Method params allow: `factory.recompile(new_manifest, same_host_context)`.

**Alternative considered**: Factory owned by AgentHost with constructor injection — rejected per RFC-0050 Layer 3 design. Factory lifecycle is decoupled from Host lifecycle.

### Decision 3: AgentRegistry is a typed dict wrapper, not a new class hierarchy

**Choice**: `AgentRegistry` is `dict[str, MessageNode]` with a typed wrapper providing `get(name)`, `list_names()`, and `exists(name)`.

**Rationale**: Adding a class hierarchy for a name→agent lookup is over-engineering. The wrapper provides type safety and discoverability without inheritance complexity.

### Decision 4: AgentPool.agent_pool property becomes compatibility shim

**Choice**: `MessageNode.agent_pool` property is NOT removed in M1. It returns a HostContext-like object (the AgentPool facade which implements the same interface). Full call-site migration is M1b.

**Rationale**: M1 is 14 days. Migrating 25 call sites across 4 files is a separate concern (M1b, 21 days) that can overlap with M2. Removing the property in M1 would break all agents immediately. The shim preserves backward compatibility while enabling gradual migration.

### Decision 5: No new package structure

**Choice**: New classes go in `src/agentpool/host/` (HostContext, AgentFactory, AgentRegistry). AgentPool stays in `src/agentpool/delegation/pool.py`.

**Rationale**: Creating `agentwolf_*` packages is premature — the rename is an Open Question in RFC-0050. New code in `agentpool.host` module is clean separation without package-level disruption.

## Risks / Trade-offs

- **[Risk] HostContext will gain fields later** (model_registry, model_cache full implementation in M4) → Adding fields to a frozen dataclass with defaults is backward-compatible. LOW risk.
- **[Risk] AgentFactory.compile() signature may change** when config split happens in M4 (manifest narrows from AgentsManifest to AgentManifest) → Factory only reads agent/team/graph sections, type narrowing is transparent. LOW risk.
- **[Risk] Compatibility shim masks incomplete migration** — `agent_pool` property still exists, developers may keep using it → M1b explicitly tracks call-site migration. The shim returns HostContext, so new code pattern works even through the shim. MEDIUM risk, mitigated by M1b.
- **[Trade-off] Two-step migration** (M1 creates structure, M1b migrates call sites) vs one-step → Two-step reduces blast radius. M1 ships in 14d, M1b in 21d. One-step would be 35d with higher risk.
