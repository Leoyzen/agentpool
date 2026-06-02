## Context

After the `sessionpool-only-architecture` change, BaseAgent becomes a pure execution engine with no session-scoped mutable state. This creates the ideal conditions to thin AgentPool's wrapper layers around pydantic-ai.

Currently, AgentPool distributes agent functionality across many disjoint manager classes:
- `ToolManager` + `ResourceProvider` → tools and instructions
- `AgentHooks` + `NativeAgentHookManager` → lifecycle hooks
- `MCPManager` → MCP server lifecycle and tools
- Manual history processor resolution → history processing
- `SystemPrompts` + `wrap_instruction()` → dynamic instructions

pydantic-ai solves the same problem with a **unified capability system**: `AbstractCapability` is simultaneously a plugin (lifecycle hooks), a tool provider (`get_toolset()`), an instruction provider (`get_instructions()`), and a configuration modifier (`get_model_settings()`). A single capability can play all roles.

The thinning strategy is to **collapse AgentPool's multiple manager classes into unified pydantic-ai capabilities**. A pydantic-ai `AbstractCapability` is simultaneously a plugin (lifecycle hooks), a tool provider (`get_toolset()`), an instruction provider (`get_instructions()`), and a configuration modifier (`get_model_settings()`). A single capability can play all roles. `AgentInstructions` in pydantic-ai is a type alias (`str | SystemPromptFunc | Sequence[str | SystemPromptFunc] | None`), not a class — we pass compatible values directly to the `instructions` parameter.

This is not a rewrite — it is a migration of internal implementation details. Public APIs remain largely unchanged during a deprecation period.

## Goals / Non-Goals

**Goals:**
- Collapse `ToolManager`/`ResourceProvider`, `AgentHooks`/`NativeAgentHookManager`, `MCPManager`, history processors, and `SystemPrompts` into unified pydantic-ai `AbstractCapability` instances
- `get_agentlet()` collects all capabilities from tool providers, hooks, MCP servers, and history processors, passing them as a single `capabilities=[...]` list to `PydanticAgent`
- Expose `capabilities` configuration field for direct pydantic-ai capability passthrough (native, third-party, or custom)
- Replace `SystemPrompts`/`wrap_instruction()` with pydantic-ai `AgentInstructions`
- Thin `RichAgentStreamEvent` wrapper to propagate pydantic-ai native events directly
- Keep AgentPool's public API stable during a deprecation period
- Maintain full backward compatibility via shim layers

**Non-Goals:**
- Replacing `MessageNode`, `AgentPool`, `ConnectionManager`, or protocol servers (these are AgentPool's unique differentiators)
- Using `pydantic_graph` for team orchestration (that is Phase 3)
- Changing YAML configuration schemas at the top level (legacy config is shim-mapped)
- Removing AgentPool's cross-protocol message abstractions (`ChatMessage`, `EventBus`)
- Modifying non-native agent types (Claude Code, ACP, AG-UI)

## Decisions

### Decision: Unified capability architecture
**Rationale**: pydantic-ai's `AbstractCapability` is designed as a single abstraction for plugin + tool provider + instruction provider. AgentPool currently splits these concerns into `ToolManager`/`ResourceProvider` (tools), `AgentHooks`/`NativeAgentHookManager` (hooks), `MCPManager` (MCP lifecycle), manual history resolution (history), and `SystemPrompts` (instructions). This creates unnecessary indirection and divergence from upstream patterns.

**Approach**: Refactor all AgentPool internal providers to return pydantic-ai `AbstractCapability` instances via `as_capability()`. `get_agentlet()` becomes a collector:
```python
capabilities = []
for provider in self.tool_providers:
    capabilities.append(provider.as_capability())
if self.hooks:
    capabilities.append(self.hooks.as_capability())
for mcp_server in self.mcp_servers:
    capabilities.append(MCP(mcp_server))
if self.history_processors:
    capabilities.append(ProcessHistory(self.history_processors))

# Note: instructions is NOT a class — it's a type alias
instructions: list[str | SystemPromptFunc] = self._collect_instructions()

return PydanticAgent(
    model=self.model,
    instructions=instructions,
    capabilities=capabilities,
)
```

**Migration path**: Existing manager classes become thin wrappers that internally construct and return pydantic-ai capabilities. Deprecation warnings emitted. YAML config auto-mapped. Shim layers kept for minimum 2 release cycles.

### Decision: Direct capability passthrough
**Rationale**: pydantic-ai's capability ecosystem is extensible — users may want to use native capabilities (Instrumentation, WebSearch) or third-party capabilities (custom tracing, rate limiting) that AgentPool doesn't wrap. Closing this door would limit AgentPool's utility and create friction for users already using pydantic-ai capabilities.

**Approach**: `NativeAgentConfig` and YAML config gain a `capabilities` field that accepts a list of `AbstractCapability` instances or `CapabilityConfig` objects. These are merged with internally-generated capabilities:
```python
capabilities = []
# 1. Internal providers via as_capability()
for provider in self.tool_providers:
    capabilities.append(provider.as_capability())
# ... hooks, MCP, history ...

# 2. User-provided capabilities (highest priority)
for cap in self.config.capabilities:
    if isinstance(cap, AbstractCapability):
        capabilities.append(cap)
    elif isinstance(cap, CapabilityConfig):
        capabilities.append(cap.build())

return PydanticAgent(
    model=self.model,
    instructions=self.instructions,
    capabilities=capabilities,
)
```

**YAML support**:
```yaml
agents:
  coder:
    type: native
    model: openai:gpt-4o
    capabilities:
      - type: pydantic_ai.capabilities.Instrumentation
        settings:
          service_name: agentpool
      - type: custom_plugin.MyCapability
        args:
          param: value
```

**Migration path**: Direct capability passthrough is a new feature with no backward-compat concerns.

### Decision: EventBus adapter for capability hooks
**Rationale**: pydantic-ai `Hooks` capability provides lifecycle callbacks (`before_run`, `after_run`, `before_tool_execute`, `after_tool_execute`), but these execute within pydantic-ai's internal loop. AgentPool's `EventBus`, protocol servers (ACP/AG-UI/OpenCode), and cross-session consumers depend on receiving these events. Without an adapter, protocol servers would stop receiving lifecycle events.

**Approach**: Implement `EventBusHooksAdapter` — wraps a pydantic-ai `Hooks` capability and publishes lifecycle events to AgentPool's `EventBus`. Uses composition over inheritance to avoid `Hooks.__init__` signature mismatch (Hooks has 20+ hook function parameters). Session ID is resolved dynamically from the hook's run context:

```python
class EventBusHooksAdapter:
    """Wraps a Hooks capability, publishing lifecycle events to EventBus.
    
    Uses composition instead of inheriting Hooks directly to avoid
    __init__ signature conflicts (Hooks has 20+ hook parameters).
    """
    
    def __init__(self, hooks: Hooks, event_bus: EventBus):
        self._hooks = hooks
        self._event_bus = event_bus
    
    def as_capability(self) -> Hooks:
        """Return a Hooks capability that delegates to wrapped hooks + EventBus."""
        # Build Hooks by mapping all hook methods to wrapped versions
        return Hooks(
            before_run=self._wrap_before_run(),
            after_run=self._wrap_after_run(),
            before_tool_execute=self._wrap_before_tool_execute(),
            after_tool_execute=self._wrap_after_tool_execute(),
            # ... all other hooks pass through transparently
        )
    
    def _get_session_id(self, ctx: RunContext[AgentContext[Any]]) -> str | None:
        agent_ctx = ctx.deps
        if agent_ctx.run_ctx is not None:
            return agent_ctx.run_ctx.session_id
        return None
    
    def _wrap_before_tool_execute(self):
        original = self._hooks.before_tool_execute
        async def wrapped(ctx, tool_call):
            session_id = self._get_session_id(ctx)
            if session_id:
                await self._event_bus.publish(session_id, ToolCallStartEvent(...))
            if original:
                await original(ctx, tool_call)
        return wrapped
    
    # ... similar wrappers for after_tool_execute, before_run, after_run, and all other hooks
```

**Migration path**: This adapter is added in Phase 2a alongside the first `as_capability()` implementation. It ensures protocol servers continue to work throughout the migration.

### Decision: ToolManager/ResourceProvider → AbstractToolset capability
**Rationale**: `ResourceProvider` already provides both tools and instructions — exactly what `AbstractCapability.get_toolset()` + `get_instructions()` does.

**Approach**: Each `ResourceProvider` implements `as_capability()` returning an `AbstractToolset` (or custom capability) that contributes tools and instructions. `ToolManager` collects them into a single `CombinedToolset` capability or passes them individually.

**Migration path**: `ResourceProvider` subclasses add `as_capability()` method. `ToolManager` delegates to capability construction.

### Decision: AgentHooks → Hooks capability
**Rationale**: `Hooks` capability covers `before_run`, `after_run`, `before_tool_execute`, `after_tool_execute`, etc.

**Approach**: `AgentHooks.as_capability()` returns a pydantic-ai `Hooks()` instance with decorators registered for all configured hooks.

**Migration path**: `AgentHooks` dataclass gains `as_capability()` method. `NativeAgentHookManager` becomes thin adapter.

### Decision: MCPManager → MCP capability
**Rationale**: `MCP` capability handles server lifecycle AND contributes tools via `MCPToolset`.

**Approach**: `MCPManager.as_capability()` returns `pydantic_ai.capabilities.MCP(...)` configured with the server's `MCPServerStdio`/`MCPServerSSE` instance.

**Migration path**: `MCPManager` implements `as_capability()`.

### Decision: History processors → ProcessHistory capability
**Rationale**: `ProcessHistory` is a native capability for history processing.

**Approach**: Manual resolution is replaced with `ProcessHistory(hooks=self.history_processors)` capability.

### Decision: SystemPrompts → instructions parameter
**Rationale**: pydantic-ai accepts instructions directly as `str | SystemPromptFunc | Sequence[str | SystemPromptFunc] | None` via the `instructions` parameter. `AgentInstructions` is a type alias, not a class. `SystemPrompts` and `wrap_instruction()` are workarounds for older pydantic-ai limitations.

**Approach**: Convert instruction functions to pydantic-ai compatible signatures (accepting `RunContext[AgentContext[TDeps]]`) and pass as `instructions=[...]` to `PydanticAgent`. Static strings and template strings pass through directly.

**Migration path**: `wrap_instruction()` utility remains during deprecation but delegates to pydantic-ai compatible wrapping. `SystemPrompts` class delegates internally.

### Decision: Event stream unchanged in Phase 2
**Rationale**: `RichAgentStreamEvent` is deeply embedded in the streaming pipeline, `EventBus`, and protocol servers. Changing the event taxonomy during capability migration creates cascading breakage that's hard to debug. Separating the concerns reduces risk.

**Approach**: Phase 2 preserves `RichAgentStreamEvent` as the external interface. pydantic-ai native events are translated to `RichAgentStreamEvent` at the agent boundary (this is already how it works today). Event thinning to propagate native events directly is deferred to Phase 2g after capability migration is stable.

**Migration path**: No change in Phase 2. Phase 2g will introduce a new event taxonomy that supports both native and custom events.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| pydantic-ai API churn (sibling project, not PyPI) | Pin version to `>=1.102.0,<2.0.0`; keep shim layers for 2+ releases; add CI tests against pydantic-ai main branch |
| One capability failing affects all capabilities | pydantic-ai capabilities are isolated; failures don't cascade |
| YAML config backward compatibility | Shim layer maps legacy config to capability construction |
| EventBus integration | `EventBusHooksAdapter` publishes capability lifecycle events to EventBus |
| Loss of per-manager granular control | Unified design is simpler; add logging at boundaries |
| Tool confirmation breaks | `ApprovalRequiredToolset` + `InputProvider` bridge preserves UI flow |

## Migration Plan

1. **Phase 2a - Foundation** (capability collection + EventBus adapter)
   - Add `as_capability()` to `ResourceProvider` base class
   - Implement `EventBusHooksAdapter`
   - Update `get_agentlet()` to collect capabilities + adapter
   - Run full test suite

2. **Phase 2b - Provider migration** (parallel)
   - Implement `as_capability()` for builtin toolsets, MCP, custom tools
   - Implement `AgentHooks.as_capability()`
   - Replace history resolution with `ProcessHistory`
   - Convert instructions to pydantic-ai format
   - Run full test suite

3. **Phase 2c - Direct passthrough** (parallel)
   - Add `capabilities` config field
   - Implement `CapabilityConfig` for YAML
   - Run full test suite

4. **Phase 2d - Tool Confirmation Bridge**
   - Map `Tool.requires_confirmation` to pydantic-ai approval mechanism
   - Bridge denial signals to AgentPool `InputProvider` flow
   - Run full test suite

5. **Phase 2e - Backward compat shims**
   - Implement shims for `ToolManager`, `AgentHooks`, `MCPManager`
   - Add deprecation warnings
   - Run backward-compat tests

6. **Phase 2f - Test Migration Inventory**
   - Migrate all tests from old manager APIs to capability-based APIs
   - Run complete test suite

7. **Phase 2g - Stabilization** (2+ weeks)
   - Monitor for issues
   - Fix edge cases
   - Benchmark capability overhead

8. **Phase 2h - Cleanup**
   - Remove shim layers (after 2 release cycles)
   - Update documentation

9. **Phase 2i - Event thinning** (separate, after stabilization)
   - Audit `RichAgentStreamEvent` hierarchy
   - Propagate native events where safe
   - Update protocol servers to handle native events

Rollback: Revert to pre-change commit; shim layers keep old code paths.

## Open Questions

1. ~~Should we expose pydantic-ai capability config directly in YAML or keep AgentPool's abstraction?~~ **Resolved**: Yes, via `capabilities` field with `CapabilityConfig`.
2. ~~How do `Tool.confirmation_mode` and `InputProvider` integrate with pydantic-ai's `ApprovalRequiredToolset`?~~ **Resolved**: `ApprovalRequiredToolset` marks tools needing approval; `InputProvider` handles actual UI confirmation.
3. ~~What is the deprecation timeline for shim layers?~~ **Resolved**: Target removal in v0.5.0 (2 release cycles after merge).
