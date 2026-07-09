## Why

AgentPool's tool/capability system is built on a custom `ResourceProvider` abstraction that predates and overlaps with pydantic-ai's native `Capability`/`Toolset` system. This creates a maintenance burden (two parallel abstraction layers), a bridge tax (`as_capability()` adapter), and missing features (ResourceProvider has no equivalent of pydantic-ai's lifecycle hooks, middleware chain, or deferred loading). Migrating to pydantic-ai native Capability/Toolset eliminates the adapter layer, aligns AgentPool with upstream, and enables future extensibility through entry-point registration.

## What Changes

- **Adopt pydantic-ai Capability/Toolset natively**: All agents use `AbstractCapability` + `AbstractToolset` for tools, hooks, instructions, and lifecycle management.
- **Build 7 ToolsetFactory equivalents** replacing ResourceProvider implementations:
  - `MCPToolset` + `MCPCapability` (replaces MCPResourceProvider, includes ResourceSource)
  - `FunctionToolset` (replaces StaticResourceProvider)
  - `FilteredToolset` (replaces FilteringResourceProvider)
  - `CombinedToolset` (replaces AggregatingResourceProvider)
  - `SubagentCapability` + `SubagentToolset` (replaces PoolResourceProvider)
  - `CodeModeCapability` (replaces CodeModeResourceProvider)
  - `SkillCapability` (already exists, supplement with ResourceSource)
- **`AdapterToolsetFactory`**: Bridge during migration — wraps existing ResourceProvider as Capability, allowing incremental migration.
- **`AbstractCapability.on_change()`**: Replaces ResourceProvider change signal system. Capabilities notify their own observers.
- **`ResourceSource` protocol**: Read-only data access (`list()`, `read(uri)`, `exists()`, `on_change()`). Implemented by MCPCapability and SkillCapability. `AggregatedResourceSource` composes at compile time.
- **`AgentContext`**: Constructed by RunLoop at Turn execution time. Contains: `agent_registry` (read-only), `delegation` (DelegationService), `session` (SessionState), `scope` (RunScope), `resources` (ResourceSource, optional).
- **`DelegationService`**: Limited interface exposed by RunLoop — tools know WHAT they can do (spawn subagent), not HOW RunLoop implements it.
- **Delete ResourceProvider**: All 7 implementations, `as_capability()` bridge, and related infrastructure physically removed after migration complete.
- **Entry-point registration**: Custom Capabilities registered via `agentpool.capabilities` entry point group.

**Dependency note**: M3 tasks 1-14 (interface definitions, capability implementations) may run in parallel with M2 (RunLoop). Task group 15 (RunLoop Integration) requires M2 completion as a hard dependency — M3 modifies RunLoop to construct AgentContext per turn and implement DelegationService, which requires M2's RunLoop to be implemented first.

## Capabilities

### New Capabilities

- `capability-toolset`: pydantic-ai native Capability/Toolset system for agent tool/hook/instruction management. Replaces ResourceProvider.
- `resource-source`: Read-only data access protocol for MCP resources, skills, and other content sources. Orthogonal to Capability (behavior) — same object can implement both.
- `agent-context`: Turn-scoped context constructed by RunLoop. Carries agent registry, delegation service, session state, run scope, and optional resources.
- `delegation-service`: Limited interface for subagent spawning exposed by RunLoop to agent tools.

### Modified Capabilities

- `agent-pool`: AgentFactory.compile() now produces agents with pydantic-ai Capabilities instead of ResourceProviders. AgentPool no longer manages ResourceProvider lifecycle.
