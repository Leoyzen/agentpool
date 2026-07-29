# Capabilities (M3 — Replaces Resource Providers)

In M3, the old `ResourceProvider` hierarchy was replaced with native pydantic-ai `AbstractCapability` / `AbstractToolset` implementations. Each `AbstractCapability` produces tools, instructions, change notifications, and optionally implements `ResourceSource` for read-only data access. The old `src/agentpool/resource_providers/` directory (14 files, ~3860 LOC) was physically deleted after migration.

## Capability Registry

| Capability | Replaces | Key File |
|---|---|---|
| `MCPCapability` | `MCPResourceProvider` | `capabilities/mcp_capability.py` |
| `SkillCapability` | `LocalResourceProvider` | `skills/capability.py` |
| `SubagentCapability` | `PoolResourceProvider` | `capabilities/subagent_capability.py` |
| `FunctionToolsetCapability` | `StaticResourceProvider` | `capabilities/function_toolset.py` |
| `CombinedToolsetCapability` | `AggregatingResourceProvider` | `capabilities/combined_toolset.py` |
| `FilteredToolsetCapability` | `FilteringResourceProvider` | `capabilities/filtered_toolset.py` |
| `CodeModeCapability` | `CodeModeResourceProvider` | `capabilities/code_mode_capability.py` |

## Supporting Types

- `ResourceSource` (`capabilities/resource_source.py`) — `@runtime_checkable Protocol` for read-only data access (`list()`, `read(uri)`, `exists(uri)`, `on_change()`). Orthogonal to `AbstractCapability` — same object can implement both.
- `AggregatedResourceSource` — Composes multiple `ResourceSource` instances, routes by URI scheme.
- `AgentContext` (`capabilities/agent_context.py`) — Frozen dataclass carrying `agent_registry`, `delegation`, `session`, `scope`, `resources`, `host`. Constructed by RunLoop per-turn.
- `DelegationService` (`capabilities/delegation.py`) — Protocol exposing `spawn_subagent(name, prompt)` and `get_available_agents()`. Limits tools to operations they need without exposing `AgentPool`.
- `ChangeEvent` (`capabilities/change_event.py`) — Frozen dataclass for capability change notifications (`on_change()` stream).
- Entry-point registry (`capabilities/registry.py`) — Discovers custom capabilities via `agentpool.capabilities` entry-point group.

## Deleted Alongside ResourceProviders

- `src/agentpool/tools/factory.py` (194 LOC, 6 `ToolsetFactory` classes) — became dead code after all providers migrated.
- `src/agentpool/tools/manager.py` (364 LOC, `ToolManager`) — all `agent.tools.X` access migrated to direct capability references.
