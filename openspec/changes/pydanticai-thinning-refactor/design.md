## Context

AgentPool is built on PydanticAI, using a composition pattern: `get_agentlet()` creates a fresh `pydantic_ai.Agent` at runtime with capabilities assembled from tool providers, hooks, MCP servers, skills, etc. The integration surface is ~500 lines of bridge code (`as_capability()`, `get_agentlet()`, `to_pydantic_ai()`, `wrap_tool()`).

Over time, AgentPool accumulated custom abstractions that overlap with PydanticAI's evolving API:

1. **Hooks**: AgentPool has a 3-type hierarchy (`CallableHook`/`CommandHook`/`PromptHook`) with regex matchers, timeouts, and parallel result combining. PydanticAI now provides `pydantic_ai.capabilities.Hooks` with typed callbacks (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`).
2. **ProcessHistory**: AgentPool has `ProcessHistoryAdapter` (~200 lines with caching + signature validation). PydanticAI provides `pydantic_ai.capabilities.ProcessHistory`.
3. **PromptInjectionManager**: For native agents, `queue()`/`pop_queued()` is redundant with `PendingMessageDrainCapability` (already the spec'd mechanism per `pending-message-queue`).
4. **Tool metadata**: `Tool` dataclass carries `ToolKind` taxonomy, `ToolResult.structured_content`, and other fields PydanticAI's `Tool` already provides.
5. **Event subclasses**: `PartStartEvent`/`PartDeltaEvent` subclass PydanticAI's versions just to add `session_id`.

Current state: ~1,500 lines of custom code that duplicates PydanticAI functionality.

## Goals / Non-Goals

**Goals:**
- Remove ~1,000-1,500 lines of code that duplicates PydanticAI functionality
- Delegate agent-engine concerns to PydanticAI (hooks, history processing, tool definition, event types)
- Keep AgentPool "thick" only at the orchestration layer (sessions, multi-agent, protocols, skills)
- Maintain behavioral parity — all existing test scenarios must pass
- Reduce coupling to `pydantic_ai._internal` APIs

**Non-Goals:**
- Removing ACP agent support (the manual queue system for ACP stays)
- Changing the `ResourceProvider` hierarchy (it's AgentPool's value-add)
- Changing the Skills system (implements Agent Skills Spec, no PydanticAI equivalent)
- Changing `SessionPool`/`SessionController`/`EventBus`/`TurnRunner` (orchestration layer stays)
- Changing YAML graph compiler or pydantic-graph integration
- Changing protocol servers (ACP/AG-UI/OpenCode/OpenAI API)

## Decisions

### D1: Hooks — Delegate to `pydantic_ai.capabilities.Hooks`, keep Command/Prompt as thin adapters

**Choice**: Migrate `NativeAgentHookManager.as_capability()` to directly produce a `pydantic_ai.capabilities.Hooks` instance with typed callbacks. Remove `Hook` base class, `CallableHook`, regex matchers, timeout handling, and parallel result combining. `CommandHook` and `PromptHook` survive as thin adapters that implement `Hooks` callbacks internally.

**Rationale**: PydanticAI's `Hooks` provides the same 4 hook points (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`) with a cleaner API. AgentPool's parallel result combining (deny > ask > allow) can be replicated inside a single `Hooks` callback that aggregates multiple registered hooks. `CommandHook` (subprocess evaluation) and `PromptHook` (LLM evaluation) are unique to AgentPool and have no PydanticAI equivalent — they become thin wrappers that register their logic as `Hooks` callbacks.

**Alternatives considered**:
- *Keep all custom hooks, just wrap them*: Rejected — defeats the purpose of thinning and adds an extra layer.
- *Remove CommandHook and PromptHook entirely*: Rejected — they provide unique capabilities (subprocess + LLM evaluation) not available in PydanticAI.

### D2: ProcessHistory — Use `pydantic_ai.capabilities.ProcessHistory` directly

**Choice**: Remove `ProcessHistoryAdapter`. Register custom history processors (compaction, etc.) as callbacks on PydanticAI's `ProcessHistory` capability. The caching and signature validation in `ProcessHistoryAdapter` is dropped — PydanticAI handles lifecycle internally.

**Rationale**: `ProcessHistoryAdapter` was written before PydanticAI had a stable `ProcessHistory` capability. The caching layer was needed because AgentPool rebuilt the agent per-run, but PydanticAI's `ProcessHistory` already handles this efficiently. Signature validation was for detecting config changes — this is now handled by the capability rebuild mechanism in `get_agentlet()`.

**Alternatives considered**:
- *Keep ProcessHistoryAdapter as a thin wrapper*: Rejected — it would just forward calls to PydanticAI's `ProcessHistory` with no added value.

### D3: PromptInjectionManager — Remove native path, keep ACP-only

**Choice**: `PromptInjectionManager.inject()`/`consume()` (tool result augmentation) is preserved for all agents. `queue()`/`pop_queued()`/`flush_pending_to_queue()` are removed for native agents — native agents fully use `PendingMessageDrainCapability` for follow-up queue. `PromptInjectionManager` survives only as the ACP-agent manual queue.

**Rationale**: The `pending-message-queue` spec already established that native agents use `PendingMessageDrainCapability` for follow-up delivery. The `queue()`/`pop_queued()` methods on `PromptInjectionManager` are dead code for native agents — they're never called because `RunExecutor` drives `agent_run.next(node)` which triggers `PendingMessageDrainCapability` hooks. The `inject()`/`consume()` path (tool result augmentation via `<injected-context>` tags) is NOT replaced by PydanticAI — it modifies tool results, not conversation messages.

**Alternatives considered**:
- *Remove PromptInjectionManager entirely*: Rejected — ACP agents still need the manual queue, and tool result augmentation is unique to AgentPool.
- *Keep everything as-is*: Rejected — the native path is dead code that confuses maintainers.

### D4: Tool dataclass — Thin wrapper, remove redundant metadata

**Choice**: Remove `ToolKind` taxonomy (PydanticAI has no equivalent and it's unused outside config validation). Remove `ToolResult.structured_content` (PydanticAI's `ToolReturn` already supports structured returns). Simplify `Tool.to_pydantic_ai()` to a direct 1:1 mapping. Tool confirmation uses PydanticAI's `requires_approval` where possible; `ApprovalRequiredToolset` stays for deferred execution scenarios.

**Rationale**: `ToolKind` was a categorization system for tool permissions, but it's only used in config validation, not at runtime. `ToolResult.structured_content` duplicates PydanticAI's native structured return. The 60-line `to_pydantic_ai()` conversion handles edge cases that no longer exist after simplification.

**Alternatives considered**:
- *Keep ToolKind for config validation*: Rejected — validation can use string matching on tool names instead.
- *Remove Tool dataclass entirely, use PydanticAI Tool*: Rejected — AgentPool needs `AgentContext` injection, which PydanticAI's `RunContext` doesn't provide natively. The `wrap_tool()` adapter is still needed.

### D5: Events — Stop subclassing PydanticAI events, pass session_id via context

**Choice**: Remove `PartStartEvent(PyAIPartStartEvent)` and `PartDeltaEvent(PyAIPartDeltaEvent)` subclasses. Use PydanticAI's `AgentStreamEvent` types directly. `session_id` is passed via `RunContext.deps` (which already carries `AgentContext` containing `session_id`). `ToolCallStartEvent`/`ToolCallCompleteEvent` become thin wrappers over PydanticAI's `FunctionToolCallEvent`/`FunctionToolResultEvent`, constructed in `RunExecutor` without subclassing.

**Rationale**: Subclassing PydanticAI events just to add `session_id` creates coupling — every PydanticAI event change requires AgentPool to update subclasses. `session_id` is already available via `AgentContext` in `RunContext.deps`. Protocol consumers that need `session_id` can get it from the context, not the event payload.

**Alternatives considered**:
- *Keep subclasses, just add session_id to all*: Rejected — requires maintaining subclasses for every PydanticAI event type, now and in the future.
- *Wrap events in an envelope*: Rejected — `EventEnvelope` on `EventBus` already provides session_id routing; duplicating it on the event itself is redundant.

## Risks / Trade-offs

- **[Risk] Hook behavior divergence**: AgentPool's parallel hook combining (deny > ask > allow) may behave differently from sequential `Hooks` callbacks. → **Mitigation**: Implement combining logic inside the `Hooks` callback wrapper, preserving exact priority semantics. Add regression tests for all hook combination scenarios before migration.

- **[Risk] ProcessHistory caching loss**: Removing `ProcessHistoryAdapter`'s caching may impact performance for agents with many history processors. → **Mitigation**: Benchmark before/after. If impact is significant, add caching at the PydanticAI `ProcessHistory` callback level.

- **[Risk] Breaking YAML configs**: Hook config schema changes (`matcher`/`event`/`timeout` → callback references) break existing configs. → **Mitigation**: Provide a config migration script. Document the migration in CHANGELOG. Keep a deprecation period where old config format is auto-translated.

- **[Risk] Event consumer breakage**: Protocol servers that read `session_id` from event payload will break. → **Mitigation**: Audit all `event.session_id` access points. Replace with `run_ctx.session_id` lookups. Add type errors to catch missed access points.

- **[Risk] ToolKind removal breaks config validation**: Configs using `kind: read` / `kind: edit` will fail validation. → **Mitigation**: Replace `kind` validation with string-based tool name patterns. Provide migration guide.

## Migration Plan

### Phase 1: Hooks Migration (highest impact, highest risk)
1. Write regression tests for all existing hook combination scenarios
2. Implement `HooksCapabilityAdapter` that wraps multiple AgentPool hooks into a single `pydantic_ai.capabilities.Hooks`
3. Migrate `CallableHook` to use `Hooks` callbacks
4. Migrate `CommandHook` and `PromptHook` as thin adapters
5. Remove `Hook` base class, regex matchers, timeout handling
6. Update YAML config schema with deprecation shim for old format

### Phase 2: ProcessHistory + PromptInjectionManager
1. Replace `ProcessHistoryAdapter` with PydanticAI `ProcessHistory`
2. Remove `PromptInjectionManager.queue()`/`pop_queued()` for native agent path
3. Keep `inject()`/`consume()` for tool result augmentation
4. Keep `PromptInjectionManager` ACP manual queue intact

### Phase 3: Tool + Event Thinning
1. Remove `ToolKind` taxonomy
2. Remove `ToolResult.structured_content`
3. Simplify `Tool.to_pydantic_ai()` to direct mapping
4. Remove `PartStartEvent`/`PartDeltaEvent` subclasses
5. Replace `event.session_id` access with context lookups
6. Simplify `RunExecutor` event mapping

### Rollback Strategy
- Each phase is independently revertable via git
- Phase 1 can be rolled back without affecting Phase 2/3
- If hook migration reveals behavioral divergence, restore `NativeAgentHookManager` from git

## Open Questions

1. **Hook config migration**: Should we provide an automatic YAML config migration script, or just document the new format? (Recommend: document + deprecation shim)
2. **ToolKind replacement**: Is string-based tool name pattern matching sufficient for config validation, or do we need a replacement taxonomy? (Recommend: string patterns, remove taxonomy)
3. **ProcessHistory benchmarking**: Should we benchmark before starting Phase 2, or trust that PydanticAI's implementation is efficient enough? (Recommend: quick benchmark first)
