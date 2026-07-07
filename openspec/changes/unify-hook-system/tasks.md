## 1. Phase 1: Rename pre_run/post_run → pre_turn/post_turn (non-breaking)

- [ ] 1.1 Rename `HookInput.event` values: `"pre_run"` → `"pre_turn"`, `"post_run"` → `"post_turn"` in `hooks/base.py`
- [ ] 1.2 Rename `AgentHooks.run_pre_run_hooks()` → `run_pre_turn_hooks()` and `run_post_run_hooks()` → `run_post_turn_hooks()` in `hooks/agent_hooks.py`
- [ ] 1.3 Add deprecated aliases: `run_pre_run_hooks()` calls `run_pre_turn_hooks()` + emits `DeprecationWarning`; same for `run_post_run_hooks()`
- [ ] 1.4 Rename config fields in `agentpool_config/hooks.py`: `pre_run` → `pre_turn`, `post_run` → `post_turn`; add deprecated aliases that map old names to new + emit warning
- [ ] 1.5 Update all internal references from `pre_run`/`post_run` to `pre_turn`/`post_turn` across source code
- [ ] 1.6 Add `hooks_fired: set[str]` field to `AgentRunContext` dataclass in `agents/context.py` (line 76) for double-firing guard

## 2. Phase 1: HookAwareTurn Mixin with ALL 4 Hook Types (non-breaking)

- [ ] 2.1 Create `HookAwareTurn` mixin class in `orchestrator/turn.py` (alongside existing `Turn` ABC at line 19) with abstract `_hooks` property returning `AgentHooks | None`
- [ ] 2.2 Implement `fire_pre_turn_hooks(prompt, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_pre_turn_hooks()`, returns None if no hooks; checks `hooks_fired` guard
- [ ] 2.3 Implement `fire_post_turn_hooks(result, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_post_turn_hooks()`, returns None if no hooks; checks `hooks_fired` guard
- [ ] 2.4 Implement `fire_pre_tool_hooks(tool_name, tool_input, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_pre_tool_hooks()`, returns None if no hooks
- [ ] 2.5 Implement `fire_post_tool_hooks(tool_name, tool_output, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_post_tool_hooks()`, returns None if no hooks
- [ ] 2.6 Write core unit tests for `HookAwareTurn` mixin with mock AgentHooks (all 4 methods)

## 3. Phase 1: Integrate HookAwareTurn into NativeTurn and ACPTurn (non-breaking)

- [ ] 3.1 Make `NativeTurn` (in `agents/native_agent/turn.py`) inherit from `HookAwareTurn`; implement `_hooks` property
- [ ] 3.2 In `NativeTurn.execute()`: call `fire_pre_turn_hooks()` before LLM call, `fire_post_turn_hooks()` in `finally` block after response
- [ ] 3.3 Verify `NativeTurn` tool hooks still work via `_ToolInterceptCapability` (no change needed — already handles pre/post_tool_use)
- [ ] 3.4 Make `ACPTurn` (in `agents/acp_agent/turn.py`) inherit from `HookAwareTurn`; implement `_hooks` property
- [ ] 3.5 In `ACPTurn.execute()` (line 152): call `fire_pre_turn_hooks()` before ACP prompt, `fire_post_turn_hooks()` in `finally` block
- [ ] 3.6 In `ACPTurn.execute()`: add advisory `pre_tool_use` firing on `ToolCallStart` event — call `fire_pre_tool_hooks()`, log warning if `decision="deny"` (cannot block)
- [ ] 3.7 In `ACPTurn.execute()`: add `post_tool_use` firing on `ToolCallComplete` event — intercept event after `acp_to_native_event()` conversion and before yielding; call `fire_post_tool_hooks()`, replace `modified_output` in the event if returned
- [ ] 3.8 Pass `AgentHooks` from `ACPAgent` to `ACPTurn` during turn creation (ACPAgent already accepts `hooks` param at line 159)
- [ ] 3.9 Add blocking `pre_tool_use` in `ACPClientHandler.request_permission()` (line 208) — fire hooks **before** `auto_approve` check (line 217); return `allowed=False` if deny, `allowed=True` if allow, default behavior if ask
- [ ] 3.10 Add guard in `BaseAgent._run_stream_once()`: skip `pre_run`/`post_run` firing if already in `run_ctx.hooks_fired` (protects standalone mode during transition)

## 4. Core (Unit) Tests

- [ ] 4.1 Test `HookAwareTurn.fire_pre_turn_hooks()` constructs correct HookInput (prompt, agent_name, session_id)
- [ ] 4.2 Test `HookAwareTurn.fire_post_turn_hooks()` constructs correct HookInput and applies modified_output
- [ ] 4.3 Test `HookAwareTurn.fire_pre_tool_hooks()` constructs correct HookInput (tool_name, tool_input)
- [ ] 4.4 Test `HookAwareTurn.fire_post_tool_hooks()` constructs correct HookInput and applies modified_output
- [ ] 4.5 Test `HookAwareTurn` returns None when no hooks configured (no crash) for all 4 methods
- [ ] 4.6 Test double-firing guard: `run_ctx.hooks_fired` prevents duplicate pre_turn invocation
- [ ] 4.7 Test `AgentHooks._run_hooks()` deny>ask>allow priority combination with 3 hooks returning different decisions
- [ ] 4.8 Test `AgentHooks._run_hooks()` parallel execution with `asyncio.gather(return_exceptions=True)`
- [ ] 4.9 Test advisory deny is logged but not enforced (HookResult.decision="deny" in advisory mode → warning log, execution continues)
- [ ] 4.10 Test blocking deny raises ModelRetry (native pre_tool_use) or returns denied response (ACP permission)
- [ ] 4.11 Test deprecated `pre_run`/`post_run` aliases emit `DeprecationWarning` and delegate to `pre_turn`/`post_turn`

## 5. Smoke Tests (Hook Coverage Verification)

- [ ] 5.1 Create `tests/hooks/test_hook_smoke.py` — the "never again" test file
- [ ] 5.2 Smoke: `test_pre_turn_fires_native_standalone` — assert pre_turn hook callback invoked when native agent runs standalone
- [ ] 5.3 Smoke: `test_pre_turn_fires_native_session_pool` — assert pre_turn hook callback invoked when native agent runs via SessionPool
- [ ] 5.4 Smoke: `test_pre_turn_fires_acp_standalone` — assert pre_turn hook callback invoked when ACP agent runs standalone
- [ ] 5.5 Smoke: `test_pre_turn_fires_acp_session_pool` — assert pre_turn hook callback invoked when ACP agent runs via SessionPool
- [ ] 5.6 Smoke: `test_post_turn_fires_native_standalone` — assert post_turn hook callback invoked
- [ ] 5.7 Smoke: `test_post_turn_fires_native_session_pool` — assert post_turn hook callback invoked
- [ ] 5.8 Smoke: `test_post_turn_fires_acp_standalone` — assert post_turn hook callback invoked
- [ ] 5.9 Smoke: `test_post_turn_fires_acp_session_pool` — assert post_turn hook callback invoked
- [ ] 5.10 Smoke: `test_pre_tool_use_fires_native_standalone` — assert pre_tool_use hook callback invoked
- [ ] 5.11 Smoke: `test_pre_tool_use_fires_native_session_pool` — assert pre_tool_use hook callback invoked
- [ ] 5.12 Smoke: `test_pre_tool_use_fires_acp_standalone` — assert pre_tool_use hook callback invoked (advisory)
- [ ] 5.13 Smoke: `test_pre_tool_use_fires_acp_session_pool` — assert pre_tool_use hook callback invoked (advisory)
- [ ] 5.14 Smoke: `test_post_tool_use_fires_native_standalone` — assert post_tool_use hook callback invoked
- [ ] 5.15 Smoke: `test_post_tool_use_fires_native_session_pool` — assert post_tool_use hook callback invoked
- [ ] 5.16 Smoke: `test_post_tool_use_fires_acp_standalone` — assert post_tool_use hook callback invoked
- [ ] 5.17 Smoke: `test_post_tool_use_fires_acp_session_pool` — assert post_tool_use hook callback invoked

## 6. Integration Tests (End-to-End Behavioral)

- [ ] 6.1 Create `tests/hooks/test_hook_integration.py`
- [ ] 6.2 Integration: `test_pre_turn_deny_blocks_turn_native` — pre_turn hook returns deny → turn does not execute, RunFailedEvent published
- [ ] 6.3 Integration: `test_pre_turn_deny_blocks_turn_acp` — pre_turn hook returns deny → ACP turn does not execute
- [ ] 6.4 Integration: `test_pre_tool_use_deny_blocks_native` — pre_tool_use hook returns deny → tool not executed, ModelRetry raised
- [ ] 6.5 Integration: `test_pre_tool_use_deny_advisory_acp` — pre_tool_use hook returns deny on ACP → warning logged, tool proceeds
- [ ] 6.6 Integration: `test_pre_tool_use_deny_blocks_acp_permission` — pre_tool_use hook returns deny on ACP permission request → tool blocked
- [ ] 6.7 Integration: `test_post_tool_use_modifies_output_native` — post_tool_use hook returns modified_output → tool output replaced
- [ ] 6.8 Integration: `test_post_tool_use_modifies_output_acp` — post_tool_use hook returns modified_output → ACP tool output replaced in event
- [ ] 6.9 Integration: `test_post_tool_use_additional_context_injected` — post_tool_use hook returns additional_context → context injected into conversation
- [ ] 6.10 Integration: `test_command_hook_subprocess_receives_correct_json` — CommandHook spawns subprocess, sends correct JSON via stdin, reads exit code
- [ ] 6.11 Integration: `test_command_hook_deny_exit_code_2` — CommandHook subprocess exits with code 2 → deny
- [ ] 6.12 Integration: `test_command_hook_allow_exit_code_0` — CommandHook subprocess exits with code 0 → allow
- [ ] 6.13 Integration: `test_hook_with_condition_matching` — hook with tool_name regex + input_match condition fires only when condition matches
- [ ] 6.14 Integration: `test_hook_with_condition_no_match` — hook with condition that doesn't match is skipped
- [ ] 6.15 Integration: `test_post_turn_fires_on_error` — post_turn hook fires even when turn raises exception
- [ ] 6.16 Integration: `test_pre_turn_fires_per_turn_in_multi_turn` — in a 3-turn run, pre_turn fires 3 times and post_turn fires 3 times

## 7. Phase 2: Deprecate Old Names + AgentHooks.as_capability()

- [ ] 7.1 Verify `DeprecationWarning` emitted for `pre_run`/`post_run` config fields (added in task 1.4)
- [ ] 7.2 Verify `DeprecationWarning` emitted for `run_pre_run_hooks()`/`run_post_run_hooks()` aliases (added in task 1.3)
- [ ] 7.3 Add `DeprecationWarning` to `AgentHooks.as_capability()` with message recommending HookAwareTurn firing path
- [ ] 7.4 Add `DeprecationWarning` to `_wrap_before_run()`, `_wrap_after_run()`, `_wrap_before_tool_execute()`, `_wrap_after_tool_execute()` methods
- [ ] 7.5 Write test: deprecation warning emitted when `as_capability()` is called
- [ ] 7.6 Write test: `as_capability()` still returns functional Hooks capability (backward compat)
- [ ] 7.7 Update documentation: mark `as_capability()` and old hook names as deprecated, recommend migration path

## 8. Phase 3: Slim NativeAgentHookManager + Remove Dead Code/Tests

- [ ] 8.1 Check for subclasses of `NativeAgentHookManager` — if any exist, add delegation shims with deprecation warnings
- [ ] 8.2 Remove `run_pre_run_hooks()` and `run_post_run_hooks()` (now `run_pre_turn_hooks`/`run_post_turn_hooks`) delegation methods from `NativeAgentHookManager`
- [ ] 8.3 Remove hook-stripping logic from `NativeAgentHookManager.as_capability()` method (lines 483-486 of `hook_manager.py` — no longer needed)
- [ ] 8.4 Remove `pre_turn`/`post_turn` firing from `BaseAgent._run_stream_once()` **for native agents only** (ACP standalone retains firing — see Future Work in design.md)
- [ ] 8.5 Remove double-firing guard (`hooks_fired` set) from RunContext for native agents (retain for ACP until standalone refactored — see Future Work)
- [ ] 8.6 Identify and remove tests that assert hooks DON'T fire in SessionPool mode
- [ ] 8.7 Identify and remove tests that validate the stripping hack behavior
- [ ] 8.8 Identify and remove tests that mock around the broken hook path instead of testing real firing
- [ ] 8.9 Verify `_ToolInterceptCapability` still works correctly for native tool hooks
- [ ] 8.10 Write test: verify hooks fire correctly after slimming (no double-firing, no missing hooks)
- [ ] 8.11 Write test: verify `_ToolInterceptCapability` tool hooks still block/modify as expected
- [ ] 8.12 Verify `NativeAgentHookManager` is ~200 LOC (down from 661)

## 9. Phase 4: Remove Deprecated APIs (breaking, v0.5.0)

- [ ] 9.1 Remove `pre_run`/`post_run` aliases from `HooksConfig` (config fields)
- [ ] 9.2 Remove `run_pre_run_hooks()`/`run_post_run_hooks()` alias methods from `AgentHooks`
- [ ] 9.3 Remove `AgentHooks.as_capability()` method entirely
- [ ] 9.4 Remove `_wrap_before_run()`, `_wrap_after_run()`, `_wrap_before_tool_execute()`, `_wrap_after_tool_execute()` methods
- [ ] 9.5 Remove `as_capability` from `__init__.py` exports if present
- [ ] 9.6 Search and remove any remaining references to removed methods/names in source and tests
- [ ] 9.7 Write test: verify clean import (no ImportError) after removal
- [ ] 9.8 Update migration documentation for v0.5.0 release notes

## 10. Documentation

- [ ] 10.1 Update AGENTS.md: document unified hook system architecture, Turn.execute() firing, per-turn semantics, and test strategy
- [ ] 10.2 Document ACP limitations: advisory vs blocking hooks, subprocess execution visibility
- [ ] 10.3 Document the three-tier test strategy (core/smoke/integration) and the smoke coverage matrix
- [ ] 10.4 Document the `pre_run`→`pre_turn` / `post_run`→`post_turn` rename and migration path
- [ ] 10.5 Update `thin-wrapper-refactor` OpenSpec: cross-reference hook system changes with Phase 5/6 overlap
- [ ] 10.6 Run full test suite: `uv run pytest` — verify no regressions
- [ ] 10.7 Run type checker: `uv run mypy src/` — verify no new type errors
- [ ] 10.8 Run linter: `uv run ruff check src/` — verify no new lint errors

## 11. Future Work (out of scope for this change)

- [ ] 11.1 Build `ACPAgentAPI` adapter implementing full `ACPClientProtocol` (missing `stream_events()` and `get_messages()` — see TODO at `acp_agent.py:648-652`)
- [ ] 11.2 Refactor `ACPAgent._stream_events()` (`acp_agent.py:412-511`) to delegate to `ACPTurn.execute()` instead of inline implementation
- [ ] 11.3 Once ACP standalone routes through `ACPTurn.execute()`: remove `_run_stream_once()` hook firing for ACP agents
- [ ] 11.4 Once ACP standalone routes through `ACPTurn.execute()`: remove `hooks_fired` guard for ACP agents
- [ ] 11.5 Consider routing all standalone execution through `create_run_stream()` → `RunHandle.start()` as unified entry point
