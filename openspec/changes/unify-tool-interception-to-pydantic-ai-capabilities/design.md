## Context

AgentPool's tool execution pipeline has two parallel paths:

```
Path A (legacy): wrap_tool() → confirmation + hooks + injection → direct tools only
Path B (capability): pydantic-ai capability chain → MCP/ACP tools only
```

`NativeAgentHookManager.as_capability()` already creates a pydantic-ai `Hooks` capability that wraps `after_tool_execute` for injection consumption. However, it's gated behind `if not self.hooks` in `get_agentlet()`, meaning it only activates when the old `AgentHooks` mechanism is disabled. When active, it covers ALL tools (direct, MCP, ACP).

`wrap_tool()` provides three things: (1) confirmation via `handle_confirmation()`, (2) pre/post hooks, (3) AgentContext injection. Items 2 and 3 are already covered by the capability chain. Item 1 (confirmation) is the only gap — it currently only applies to direct tools.

pydantic-ai already has a native confirmation mechanism: `ApprovalRequiredToolset` → raises `ApprovalRequired` → deferred tool call → `HandleDeferredToolCalls` capability. AgentPool already bridges this via `approval_bridge.py`. The `tool_confirmation_mode` (always/never/per_tool) can be implemented by wrapping the toolset with `ApprovalRequiredToolset` via `get_wrapper_toolset()`, rather than relying on agentpool's custom `handle_confirmation()` in `wrap_tool()`.

## Goals / Non-Goals

**Goals:**
- Unify all tool interception (confirmation, hooks, injection, schema modification, error handling) through pydantic-ai's `AbstractCapability` chain
- Make `NativeAgentHookManager.as_capability()` always register, removing the `if not self.hooks` guard
- Implement `tool_confirmation_mode` via `get_wrapper_toolset()` → `ApprovalRequiredToolset` → native pydantic-ai deferred mechanism
- Remove confirmation and hooks logic from `wrap_tool()`, retaining only AgentContext injection
- Enable users to extend tool behavior uniformly via pydantic-ai capabilities

**Non-Goals:**
- Changing `wrap_tool()`'s AgentContext injection for legacy direct tools (deferred to future migration)
- Removing `wrap_tool()` entirely (legacy providers still need AgentContext injection)
- Modifying the `AgentHooks` callback interface or `InputProvider` API
- Changing how MCP/ACP tools are discovered or registered

## Decisions

### Decision 1: Use `get_wrapper_toolset()` with `ApprovalRequiredToolset` for confirmation mode

**Chosen**: Use `get_wrapper_toolset()` to inject pydantic-ai's `ApprovalRequiredToolset` when `tool_confirmation_mode` is `"always"` or `"per_tool"` (for tools that require confirmation).

**Rationale**: `ToolDefinition` does **not** have a `requires_approval` field (verified against pydantic-ai v1.102.0). The correct pydantic-ai mechanism for requiring tool approval is `ApprovalRequiredToolset`, which raises `ApprovalRequired` during `call_tool()`, triggering pydantic-ai's native deferred tool mechanism. `HandleDeferredToolCalls` (via `approval_bridge`) then routes each approval to `InputProvider.get_tool_confirmation()`.

- mode=`"always"`: `ApprovalRequiredToolset` with `approval_required_func=lambda *_: True` — all tools require approval
- mode=`"never"`: No wrapper — tools execute directly
- mode=`"per_tool"`: `ApprovalRequiredToolset` with a function that checks each tool's `requires_confirmation` flag (looked up from `ToolManager`)

`get_wrapper_toolset()` is the right hook because it wraps the **assembled toolset** (all tools combined) with a single `ApprovalRequiredToolset`, which is more efficient than wrapping individual tools.

**`prepare_tools()` is reserved for schema modification** (the user's "harness" use case), not confirmation.

**Alternatives considered**:
- Set `kind='unapproved'` in `prepare_tools()`: Works technically, but changes the tool's `kind` field which is model-visible — the model sees the tool as "requires human approval" rather than just "needs confirmation." `ApprovalRequiredToolset` only affects execution, not the model's view of the tool.
- `before_tool_execute()` with `ModelRetry`: Wastes a model call and doesn't integrate with pydantic-ai's deferred tool chain.

### Decision 2: `_ToolInterceptCapability` owns all hook execution; `hooks_cap` is stripped to non-hook concerns only

**Chosen**: The new `_ToolInterceptCapability` runs ALL hooks (`before_tool_execute` for pre-tool hooks, `after_tool_execute` for post-tool hooks + injection consumption). The existing `hooks_cap` (from `AgentHooks.as_capability()`) is only kept for non-hook lifecycle events (`before_run`, `after_run`) that the `_ToolInterceptCapability` does not handle.

**`CombinedCapability` order**: `[_ToolInterceptCapability(), hooks_cap]`. `CombinedCapability` chains `after_tool_execute` in reverse, so `hooks_cap` (the `AgentHooks` capability, which has `after_tool_execute` that discards results) runs OUTERMOST first. Since `hooks_cap`'s `after_tool_execute` does NOT run hooks (only returns result unchanged), it does not interfere with `_ToolInterceptCapability`'s hook handling. The `_ToolInterceptCapability` runs INNERMOST (closest to the actual tool), where it runs hooks, applies `modified_output`/`additional_context`, and consumes injections.

**Rationale**: Having both capabilities run hooks in their `after_tool_execute` would cause double-firing. The cleanest approach is to make `_ToolInterceptCapability` the sole owner of hook execution. The existing `hooks_cap` from `AgentHooks.as_capability()` is kept for compatibility with `before_run`/`after_run` lifecycle callbacks, but its `after_tool_execute` (which runs hooks and discards `modified_output`/`additional_context`) is overridden or stripped.

**Alternatives considered**:
- Have `hooks_cap` run hooks and `_ToolInterceptCapability` only fix results: Still double-fires hooks. Worse because the old `AgentHooks._wrap_after_tool_execute` discards `modified_output`/`additional_context` before `_ToolInterceptCapability` can apply them.
- Remove `hooks_cap` entirely and have `_ToolInterceptCapability` handle everything: Would lose `before_run`/`after_run` lifecycle hooks. Overly broad for this change.

### Decision 3: Always register the hooks capability, remove `if not self.hooks` guard

**Chosen**: Remove the condition and always append `hooks_capability` to `tool_capabilities`.

**Rationale**: The guard exists to prevent double-firing when the old `AgentHooks` mechanism is active. But the old mechanism only fires for direct tools (via `wrap_tool()`). Once we remove hooks from `wrap_tool()`, the old mechanism no longer fires at all, so there's no double-firing risk. The capability-based hooks become the only source.

### Decision 4: `wrap_tool()` keeps AgentContext injection, removes hooks + confirmation

**Chosen**: Simplify `wrap_tool()` to only handle AgentContext injection and deferred execution. Remove `_execute_with_hooks()` and `handle_confirmation()` calls.

**Rationale**: AgentContext injection is complex (RunContext/AgentContext parameter detection, signature manipulation) and tightly coupled to how pydantic-ai calls tool functions. Moving it to a capability would require significant refactoring of how direct tools are registered. This is better deferred to a future change where all tool providers migrate to `as_capability()`.

## Risks / Trade-offs

- **[Risk] `ApprovalRequiredToolset` wrapping may interfere with existing `ApprovalRequiredToolset` usage in the capability chain** → Mitigation: `CombinedCapability.get_wrapper_toolset()` chains wrappers in reverse order (outermost first). The confirmation wrapper from our capability is outermost, so it intercepts before any inner `ApprovalRequiredToolset` from tool providers. Test with tools that already have `requires_confirmation=True`.

- **[Risk] Removing hooks from `wrap_tool()` while keeping the `if not self.hooks` guard would leave no hooks at all for direct tools when old mechanism is active** → Mitigation: Remove the guard first, then remove hooks from `wrap_tool()`. Order matters.

- **[Trade-off] `wrap_tool()` still exists for AgentContext injection** → Acceptable for now. It's a clean separation: capability chain handles behavior (confirmation, hooks, errors), `wrap_tool()` handles plumbing (context injection). Full removal is a future migration.

- **[Risk] Pre-tool hook "deny" currently raises `ToolSkippedError` (skip this tool, continue run), but `AgentHooks.as_capability()` raises `RuntimeError` (abort entire run)** → Mitigation: The new capability's `before_tool_execute` must map "deny" to `ToolSkippedError`, not `RuntimeError`. If `ToolSkippedError` is not handled by pydantic-ai's tool execution pipeline, consider raising `ModelRetry(message="Tool denied by hook")` instead, which asks the model to try a different approach.

- **[Risk] `AgentHooks.as_capability()` discards `modified_output` and `additional_context` from hook results** → Mitigation: The new capability's `after_tool_execute` must explicitly apply `modified_output` (replace result) and `additional_context` (append to result) from hook results. The existing `_wrap_after_tool_execute` in `agent_hooks.py:421-422` returns the original `result` unchanged, ignoring hook results. The new capability must fix this.

- **[Risk] `approval_bridge.py:96` has a redundant `mode == "never"` auto-approval check that becomes dead code** → Mitigation: After `ApprovalRequiredToolset` is not applied in "never" mode, pydantic-ai never defers tools, so `HandleDeferredToolCalls` never fires. Remove the redundant check in the cleanup phase.

- **[Risk] `CombinedCapability` chains `after_tool_execute` in reverse order (last element runs first/outermost)** → Mitigation: The order `[_ToolInterceptCapability(), hooks_cap]` means `hooks_cap` runs outermost first. Since `hooks_cap`'s `after_tool_execute` does NOT run hooks (Decision 2), it passes through cleanly. `_ToolInterceptCapability` runs innermost (closest to tool), where it runs hooks, applies `modified_output`/`additional_context`, and consumes injections. Verified order: outermost `hooks_cap` (pass-through) → innermost `_ToolInterceptCapability` (runs hooks).

## Migration Plan

1. **Phase 1**: Enhance `NativeAgentHookManager.as_capability()` → return `CombinedCapability` with `get_wrapper_toolset` (confirmation via `ApprovalRequiredToolset`), `prepare_tools` (schema modification), `wrap_tool_execute` (error handling). Remove `if not self.hooks` guard.
2. **Phase 2**: Remove confirmation logic from `wrap_tool()` → `handle_confirmation()` no longer called. Remove pre/post hooks from `wrap_tool()` → `_execute_with_hooks()` simplified.
3. **Phase 3** (future): Migrate all tool providers to `as_capability()`, then remove `wrap_tool()` entirely.

Rollback: Each phase is independently reversible. Revert `as_capability()` changes → restore guard → restore `wrap_tool()` hooks.

## Open Questions

- Should `tool_confirmation_mode` remain on `AgentContext.node` or move to a capability-level config? (Current plan: read from `ctx.deps.node.tool_confirmation_mode` as before)
- Should the `prepare_tools` schema modification logic be configurable/extensible by users? (Current plan: not in this change; future capability-based extensions will handle this)
- How should pre-tool hook "deny" map to pydantic-ai? `ToolSkippedError` (skip tool, continue run) vs `ModelRetry` (ask model to try different approach) vs `RuntimeError` (abort run)?
