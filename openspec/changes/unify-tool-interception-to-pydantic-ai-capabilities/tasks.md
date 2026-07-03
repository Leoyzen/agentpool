## 0. Spikes (must complete before implementation)

- [x] 0.1 **Spike: `ModelRetry` from `before_tool_execute`**: Verify that raising `ModelRetry` from a custom `AbstractCapability.before_tool_execute()` is caught by pydantic-ai's agent loop and correctly retries the model call (not just in the built-in `Hooks` capability). If `ModelRetry` is not caught, test `ToolSkippedError` and `RuntimeError` as alternatives. Document the working mechanism before implementing task 1.4. This spike **blocks** task 1.4.
- [x] 0.2 **Verify: Nested `ApprovalRequiredToolset` short-circuits via `ctx.tool_call_approved`**: Confirmed from pydantic-ai source (`approval_required.py`) that `call_tool()` checks `ctx.tool_call_approved` — after the outer wrapper defers and approval is granted, the inner wrapper sees `tool_call_approved=True` and skips its own deferral. Write a quick integration test with `mode="always"` on a tool that already has `requires_confirmation=True` to empirically confirm in the current pydantic-ai version. No deduplication logic is expected to be needed.
- [x] 0.3 **Spike: `Hooks._registry` structure for stripping `after_tool_execute`**: Inspect pydantic-ai's `Hooks` class internal `_registry` dict to confirm that `after_tool_execute` can be cleanly stripped (set to pass-through) without affecting other callbacks (`before_run`, `after_run`, `before_tool_execute`). Verify the `Hooks` dataclass supports mutation of individual registry entries. This spike **blocks** task 1.7.

## 1. Enhance NativeAgentHookManager.as_capability()

- [x] 1.1 Create `_ToolInterceptCapability` inner dataclass extending `AbstractCapability` in `hook_manager.py`, with `get_wrapper_toolset()` that reads `tool_confirmation_mode` from `ctx.deps.node` and wraps the toolset with `ApprovalRequiredToolset` when mode is "always" or "per_tool" (for tools with `requires_confirmation=True`)
- [x] 1.2 Add `prepare_tools()` to `_ToolInterceptCapability` for schema modification (e.g., injecting bridge metadata into dynamic MCP tool descriptions). Use `dataclasses.replace()` to avoid mutating shared `ToolDefinition` state
- [x] 1.3 Add `wrap_tool_execute()` to `_ToolInterceptCapability` that wraps handler execution in try/except, returning `ToolReturn` with annotated error message on failure. Convert agentpool `ToolResult` to pydantic-ai `ToolReturn` for direct tools (extract `structured_content` or `content` from `ToolResult`, wrap in `ToolReturn(return_value=..., content=...)`). This replaces the conversion previously done in `_execute_with_hooks()` at `tool_wrapping.py:130-132`
- [x] 1.4 Add `before_tool_execute()` to `_ToolInterceptCapability` that runs pre-tool hooks and handles "deny" by raising `ModelRetry` (not `RuntimeError`) and applies `modified_input` to validated args [DEPENDS ON 0.1 — spike must confirm `ModelRetry` is caught by pydantic-ai; if not, use the mechanism documented in the spike]
- [x] 1.5 Add `after_tool_execute()` to `_ToolInterceptCapability` that runs post-tool hooks and explicitly applies `modified_output` (replace result) and `additional_context` (append via `_inject_additional_context`) from hook results — fixing the existing gap where `AgentHooks._wrap_after_tool_execute` discards these fields
- [x] 1.6 Consume pending prompt injections from `PromptInjectionManager` in `after_tool_execute`
- [x] 1.7 Modify `as_capability()` to strip `hooks_cap`'s `after_tool_execute` (make it pass-through) to prevent double-firing — `_ToolInterceptCapability` owns all hook execution per Decision 2 [DEPENDS ON 0.3 — spike must confirm `Hooks._registry` supports stripping individual callbacks]
- [x] 1.8 Return `CombinedCapability(capabilities=[_ToolInterceptCapability(), hooks_cap])` from `as_capability()`. The order `[_ToolInterceptCapability(), hooks_cap]` is correct: `CombinedCapability` chains `after_tool_execute` in reverse, so pass-through `hooks_cap` runs outermost first, then `_ToolInterceptCapability` runs innermost (runs hooks, applies results, consumes injections)

## 2. Remove `if not self.hooks` guard in get_agentlet()

- [x] 2.1 In `agent.py` `get_agentlet()`, remove the `if not self.hooks:` condition around `hooks_capability = self._hook_manager.as_capability()` so it always registers
- [x] 2.2 Verify that `before_tool_execute` and `after_tool_execute` callbacks from `AgentHooks` still fire correctly when old hook mechanism is active (no double-firing)

## 3. Simplify wrap_tool() — remove hooks and confirmation

- [x] 3.1 Remove `handle_confirmation()` call from `wrap_tool()` — confirmation is now handled by `get_wrapper_toolset()` + `ApprovalRequiredToolset` + `HandleDeferredToolCalls` [DEPENDS ON 2.1 — guard must be removed first to avoid leaving direct tools with no hooks or confirmation]
- [x] 3.2 Remove `_execute_with_hooks()` function and its pre/post hook calls from `wrap_tool()` — hooks are now handled by capability chain's `before_tool_execute` and `after_tool_execute` [DEPENDS ON 2.1 — guard must be removed first]
- [x] 3.3 Remove `_inject_additional_context()` usage from `wrap_tool()` (injection is handled by `after_tool_execute` in capability)
- [x] 3.4 Remove `_handle_confirmation_result()` from `tool_wrapping.py` — its mappings (skip/abort_run/abort_chain) are now handled by `_map_confirmation_result` in `approval_bridge.py`
- [x] 3.5 Keep AgentContext injection logic (RunContext/AgentContext parameter detection, signature manipulation) intact
- [x] 3.6 Keep deferred execution support (`_handle_deferred_exception`, `CallDeferred`/`ApprovalRequired` handling) intact — this is separate from pydantic-ai's approval mechanism
- [x] 3.7 Verify `wrap_tool()` still correctly injects `AgentContext` for tools that need it

## 4. Clean up approval_bridge

- [x] 4.1 Remove redundant `mode == "never"` auto-approval check from `approval_bridge.py` (line ~96) — after `ApprovalRequiredToolset` is not applied in "never" mode, this code path is unreachable
- [x] 4.2 Verify `approval_bridge.py` correctly processes deferred approvals when mode="always" (all tools deferred)
- [x] 4.3 Verify `approval_bridge.py` correctly processes deferred approvals when mode="per_tool" (only `requires_confirmation=True` tools deferred)

## 5. Testing

- [ ] 5.1 Write unit test: `get_wrapper_toolset()` wraps toolset with `ApprovalRequiredToolset` when mode="always"
- [ ] 5.2 Write unit test: `get_wrapper_toolset()` returns `None` when mode="never"
- [ ] 5.3 Write unit test: `get_wrapper_toolset()` wraps with per-tool check when mode="per_tool"
- [ ] 5.4 Write unit test: `wrap_tool_execute()` catches exception and returns annotated `ToolReturn`
- [ ] 5.5 Write unit test: `wrap_tool_execute()` passes through successful results unchanged
- [ ] 5.6 Write unit test: `before_tool_execute()` applies `modified_input` from pre-tool hooks
- [ ] 5.7 Write unit test: `before_tool_execute()` raises `ModelRetry` when pre-tool hook denies
- [ ] 5.8 Write unit test: `after_tool_execute()` applies `modified_output` from post-tool hooks
- [ ] 5.9 Write unit test: `after_tool_execute()` applies `additional_context` from post-tool hooks
- [ ] 5.10 Write unit test: `after_tool_execute()` consumes pending injection
- [ ] 5.11 Write integration test: hooks fire for MCP tools (not just direct tools)
- [ ] 5.12 Write integration test: confirmation works for MCP tools when mode="always"
- [ ] 5.13 Write integration test: no double-firing when old `AgentHooks` is active AND capability chain is active
- [x] 5.14 Run existing test suite (`uv run pytest`) and fix any regressions
- [x] 5.15 Run type checking (`uv run mypy src/`) and fix any new type errors

## 6. Cleanup

- [x] 6.1 Remove unused imports from `tool_wrapping.py` after hooks/confirmation removal
- [x] 6.2 Remove `_handle_confirmation_result` from `tool_wrapping.py`
- [x] 6.3 Remove redundant `mode == "never"` check from `approval_bridge.py`
- [x] 6.4 Update docstrings in `hook_manager.py`, `tool_wrapping.py`, and `agent.py` to reflect new architecture
- [x] 6.5 Add deprecation notice to `AgentContext.handle_confirmation()` — it is no longer called from `wrap_tool()`
