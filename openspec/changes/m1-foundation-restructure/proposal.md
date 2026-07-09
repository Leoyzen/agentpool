## Why

AgentPool's current runtime structure couples config, infrastructure, and agent creation in a single `AgentPool` god-class. Every agent accesses the entire pool via `MessageNode.agent_pool` backdoor, spanning Layers 2-5 and blocking tenant isolation. This is the single highest-priority architectural debt identified by Oracle assessment (RFC-0050, Appendix A) and must be resolved before RunLoop (M2) or multi-tenant (M5) work can proceed.

## What Changes

- **Extract `HostContext`**: Frozen dataclass carrying only dependency handles (mcp, storage, skills_registry, prompt_manager, model_registry, model_cache, config_id, tenant_id). Replaces direct `agent_pool` references.
- **Extract `AgentFactory`**: Standalone compilation service that takes `(manifest, host_context)` and produces `AgentRegistry`. AgentPool's agent creation logic moves here. Factory has internal compilation cache for diff-based recompile but owns no infrastructure.
- **AgentPool becomes facade**: `async with AgentPool(...)` API unchanged. Internally delegates to AgentFactory for agent creation and exposes HostContext for injection.
- **No config model changes**: Flat `AgentsManifest` stays as-is. HostConfig/AgentManifest split is deferred to M4 (per Oracle analysis — Phase 1b/1c/Phase 2 depend on runtime extraction only, not config schema split).
- **No YAML schema changes**: Existing configs work without modification.
- **No user-visible API changes**: All existing Python APIs and CLI commands continue to work identically.

## Capabilities

### New Capabilities

- `host-context`: Frozen dataclass that carries infrastructure dependency handles to agents, replacing direct `agent_pool` references. Defines the contract for what agents can access at runtime.
- `agent-factory`: Standalone compilation service that transforms `AgentsManifest` + `HostContext` into runnable `AgentRegistry`. Decouples agent creation from infrastructure ownership.

### Modified Capabilities

- `agent-pool`: AgentPool transitions from god-class to facade. Loses direct agent creation logic (moved to AgentFactory) and direct infrastructure exposure (replaced by HostContext). Public API preserved.

## Impact

- **Affected code**: `src/agentpool/delegation/pool.py` (AgentPool, ~825 lines, 151 importers), `src/agentpool/messaging/messagenode.py` (MessageNode base, `agent_pool` property), `src/agentpool/agents/native_agent/agent.py`, `src/agentpool/agents/base_agent.py`, `src/agentpool/agents/base_team.py` (all access `agent_pool`)
- **New files**: `src/agentpool/host/context.py` (HostContext), `src/agentpool/host/factory.py` (AgentFactory), `src/agentpool/host/registry.py` (AgentRegistry)
- **Backward compatibility**: Full — `async with AgentPool(...)` and `pool.get_agent()` APIs unchanged. `MessageNode.agent_pool` property remains as compatibility shim returning HostContext during migration.
- **Dependencies**: Unblocks M2 (RunLoop), M3 (Capability migration). No external dependency changes.
- **Test impact**: 85 test files import AgentPool — all must continue to pass without modification.
