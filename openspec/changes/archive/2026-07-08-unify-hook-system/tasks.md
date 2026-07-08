## 1. Phase 1: Rename pre_run/post_run → pre_turn/post_turn (non-breaking)

- [x] 1.1 Rename `HookInput.event` values: `"pre_run"` → `"pre_turn"`, `"post_run"` → `"post_turn"` in `hooks/base.py`
- [x] 1.2 Rename `AgentHooks.run_pre_run_hooks()` → `run_pre_turn_hooks()` and `run_post_run_hooks()` → `run_post_turn_hooks()` in `hooks/agent_hooks.py`
- [x] 1.3 Add deprecated aliases: `run_pre_run_hooks()` calls `run_pre_turn_hooks()` + emits `DeprecationWarning`; same for `run_post_run_hooks()`
- [x] 1.4 Rename config fields in `agentpool_config/hooks.py`: `pre_run` → `pre_turn`, `post_run` → `post_turn`; add deprecated aliases that map old names to new + emit warning
- [x] 1.5 Update all internal references from `pre_run`/`post_run` to `pre_turn`/`post_turn` across source code
- [x] 1.6 Add `hooks_fired: set[str]` field to `AgentRunContext` dataclass in `agents/context.py` (line 76) for double-firing guard
- [x] 1.7 In `RunHandle.start()` turn loop: clear `run_ctx.hooks_fired` at the start of each turn (supports multi-turn runs)
- [x] 1.8 In `BaseAgent._run_stream_once()`: clear `run_ctx.hooks_fired` at the start of each turn (Path B standalone)

## 2. Phase 1: HookAwareTurn Mixin with ALL 4 Hook Types (non-breaking)

- [x] 2.1 Create `HookAwareTurn` mixin class in `orchestrator/turn.py` (alongside existing `Turn` ABC at line 19) with class variable annotations `_hooks: AgentHooks | None = None` and `_run_ctx: AgentRunContext | None = None` (NOT properties — properties prevent subclass `__init__` from setting via simple assignment)
- [x] 2.2 Implement `fire_pre_turn_hooks(prompt, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_pre_turn_hooks()`, returns None if no hooks; checks `hooks_fired` guard (skips if key present and `_run_ctx` is not None)
- [x] 2.3 Implement `fire_post_turn_hooks(result, duration_ms=0.0, **extra) -> HookResult | None` — constructs HookInput with `duration_ms`, calls `_hooks.run_post_turn_hooks(duration_ms=duration_ms)`, returns None if no hooks; checks `hooks_fired` guard
- [x] 2.4 Implement `fire_pre_tool_hooks(tool_name, tool_input, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_pre_tool_hooks()`, returns None if no hooks
- [x] 2.5 Implement `fire_post_tool_hooks(tool_name, tool_output, **extra) -> HookResult | None` — constructs HookInput, calls `_hooks.run_post_tool_hooks()`, returns None if no hooks
- [x] 2.6 Write core unit tests for `HookAwareTurn` mixin with mock AgentHooks (all 4 methods)
- [x] 2.7 Add `duration_ms: float = 0.0` parameter to renamed `run_post_turn_hooks()` in `hooks/agent_hooks.py`; include it in HookInput construction (currently `duration_ms` is tool-only)

## 3. Phase 1: Integrate HookAwareTurn into NativeTurn and ACPTurn (non-breaking)

- [x] 3.1 Make `NativeTurn` (in `agents/native_agent/turn.py`) inherit from `HookAwareTurn`; set `self._hooks` and `self._run_ctx` in `__init__` (already stored as instance attributes)
- [x] 3.2 In `NativeTurn.execute()`: call `fire_pre_turn_hooks()` before LLM call, `fire_post_turn_hooks(result, duration_ms=turn_duration)` in `finally` block after response
- [x] 3.3 Verify `NativeTurn` tool hooks still work via `_ToolInterceptCapability` (no change needed — already handles pre/post_tool_use)
- [x] 3.4 Make `ACPTurn` (in `agents/acp_agent/turn.py`) inherit from `HookAwareTurn`; set `self._hooks` and `self._run_ctx` in `__init__` (already stored as instance attributes)
- [x] 3.5 In `ACPTurn.execute()` (line 152): call `fire_pre_turn_hooks()` before ACP prompt, `fire_post_turn_hooks(result, duration_ms=turn_duration)` in `finally` block
- [x] 3.6 In `ACPTurn.execute()`: add advisory `pre_tool_use` firing on `ToolCallStart` event — first check if `f"pre_tool_use:{tool_call_id}"` is in `run_ctx.hooks_fired` (skip if present, blocking path already fired); if not present, call `fire_pre_tool_hooks()`, log warning if `decision="deny"` (cannot block)
- [x] 3.7 In `ACPTurn.execute()`: add `post_tool_use` firing on `ToolCallComplete` event — intercept event after `acp_to_native_event()` conversion and before yielding; call `fire_post_tool_hooks()`, replace `modified_output` in the event if returned
- [x] 3.8 Pass `AgentHooks` from `ACPAgent` to `ACPTurn` during turn creation (ACPAgent already accepts `hooks` param at line 159)
- [x] 3.9 Add blocking `pre_tool_use` in `ACPClientHandler.request_permission()` (line 208) — fire hooks **before** `auto_approve` check (line 217); return `allowed=False` if deny, `allowed=True` if allow, default behavior if ask. After firing, add `f"pre_tool_use:{tool_call_id}"` to `run_ctx.hooks_fired` to prevent advisory double-firing
- [x] 3.10 Fix guard direction in `BaseAgent._run_stream_once()`: old path fires FIRST and adds keys to `hooks_fired`; `Turn.execute()` (called via `_stream_events()`) checks `hooks_fired` and skips if key present. This is the reverse of what the original design described — the old path cannot check a guard set by the new path because the old path runs first.

## 4. Core (Unit) Tests

- [x] 4.1 Test `HookAwareTurn.fire_pre_turn_hooks()` constructs correct HookInput (prompt, agent_name, session_id)
- [x] 4.2 Test `HookAwareTurn.fire_post_turn_hooks()` constructs correct HookInput and applies modified_output
- [x] 4.3 Test `HookAwareTurn.fire_pre_tool_hooks()` constructs correct HookInput (tool_name, tool_input)
- [x] 4.4 Test `HookAwareTurn.fire_post_tool_hooks()` constructs correct HookInput and applies modified_output
- [x] 4.5 Test `HookAwareTurn` returns None when no hooks configured (no crash) for all 4 methods
- [x] 4.6 Test double-firing guard: old path fires first, adds to `hooks_fired`; `Turn.execute()` checks and skips if key present (correct guard direction for Path B)
- [x] 4.7 Test `hooks_fired` is cleared per turn: in a 3-turn run, hooks fire in all 3 turns (guard from turn 1 doesn't block turn 2)
- [x] 4.8 Test ACP tool-call-ID guard: `request_permission` fires + adds `f"pre_tool_use:{tool_call_id}"`; `ToolCallStart` advisory skips for same tool_call_id but fires for different tool_call_id
- [x] 4.9 Test `AgentHooks._run_hooks()` deny>ask>allow priority combination with 3 hooks returning different decisions
- [x] 4.10 Test `AgentHooks._run_hooks()` parallel execution with `asyncio.gather(return_exceptions=True)`
- [x] 4.11 Test advisory deny is logged but not enforced (HookResult.decision="deny" in advisory mode → warning log, execution continues)
- [x] 4.12 Test blocking deny raises ModelRetry (native pre_tool_use) or returns denied response (ACP permission)
- [x] 4.13 Test deprecated `pre_run`/`post_run` aliases emit `DeprecationWarning` and delegate to `pre_turn`/`post_turn`
- [x] 4.14 Test `run_post_turn_hooks()` accepts `duration_ms` parameter and includes it in HookInput

## 5. Smoke Tests (Hook Coverage Verification)

- [x] 5.1 Create `tests/hooks/test_hook_smoke.py` — the "never again" test file
- [x] 5.2 Smoke: `test_pre_turn_fires_native_standalone` — assert pre_turn hook callback invoked when native agent runs standalone
- [x] 5.3 Smoke: `test_pre_turn_fires_native_session_pool` — assert pre_turn hook callback invoked when native agent runs via SessionPool
- [x] 5.4 Smoke: `test_pre_turn_fires_acp_standalone` — assert pre_turn hook callback invoked when ACP agent runs standalone
- [x] 5.5 Smoke: `test_pre_turn_fires_acp_session_pool` — assert pre_turn hook callback invoked when ACP agent runs via SessionPool
- [x] 5.6 Smoke: `test_post_turn_fires_native_standalone` — assert post_turn hook callback invoked
- [x] 5.7 Smoke: `test_post_turn_fires_native_session_pool` — assert post_turn hook callback invoked
- [x] 5.8 Smoke: `test_post_turn_fires_acp_standalone` — assert post_turn hook callback invoked
- [x] 5.9 Smoke: `test_post_turn_fires_acp_session_pool` — assert post_turn hook callback invoked
- [x] 5.10 Smoke: `test_pre_tool_use_fires_native_standalone` — assert pre_tool_use hook callback invoked
- [x] 5.11 Smoke: `test_pre_tool_use_fires_native_session_pool` — assert pre_tool_use hook callback invoked
- [x] 5.12 Smoke: `test_pre_tool_use_fires_acp_standalone` — assert pre_tool_use hook callback invoked (advisory)
- [x] 5.13 Smoke: `test_pre_tool_use_fires_acp_session_pool` — assert pre_tool_use hook callback invoked (advisory)
- [x] 5.14 Smoke: `test_post_tool_use_fires_native_standalone` — assert post_tool_use hook callback invoked
- [x] 5.15 Smoke: `test_post_tool_use_fires_native_session_pool` — assert post_tool_use hook callback invoked
- [x] 5.16 Smoke: `test_post_tool_use_fires_acp_standalone` — assert post_tool_use hook callback invoked
- [x] 5.17 Smoke: `test_post_tool_use_fires_acp_session_pool` — assert post_tool_use hook callback invoked

## 6. Integration Tests (End-to-End Behavioral)

- [x] 6.1 Create `tests/hooks/test_hook_integration.py`
- [x] 6.2 Integration: `test_pre_turn_deny_blocks_turn_native` — pre_turn hook returns deny → turn does not execute, RunFailedEvent published
- [x] 6.3 Integration: `test_pre_turn_deny_blocks_turn_acp` — pre_turn hook returns deny → ACP turn does not execute
- [x] 6.4 Integration: `test_pre_tool_use_deny_blocks_native` — pre_tool_use hook returns deny → tool not executed, ModelRetry raised
- [x] 6.5 Integration: `test_pre_tool_use_deny_advisory_acp` — pre_tool_use hook returns deny on ACP → warning logged, tool proceeds
- [x] 6.6 Integration: `test_pre_tool_use_deny_blocks_acp_permission` — pre_tool_use hook returns deny on ACP permission request → tool blocked
- [x] 6.7 Integration: `test_post_tool_use_modifies_output_native` — post_tool_use hook returns modified_output → tool output replaced
- [x] 6.8 Integration: `test_post_tool_use_modifies_output_acp` — post_tool_use hook returns modified_output → ACP tool output replaced in event
- [x] 6.9 Integration: `test_post_tool_use_additional_context_injected` — post_tool_use hook returns additional_context → context injected into conversation
- [x] 6.10 Integration: `test_command_hook_subprocess_receives_correct_json` — CommandHook spawns subprocess, sends correct JSON via stdin, reads exit code
- [x] 6.11 Integration: `test_command_hook_deny_exit_code_2` — CommandHook subprocess exits with code 2 → deny
- [x] 6.12 Integration: `test_command_hook_allow_exit_code_0` — CommandHook subprocess exits with code 0 → allow
- [x] 6.13 Integration: `test_hook_with_condition_matching` — hook with tool_name regex + input_match condition fires only when condition matches
- [x] 6.14 Integration: `test_hook_with_condition_no_match` — hook with condition that doesn't match is skipped
- [x] 6.15 Integration: `test_post_turn_fires_on_error` — post_turn hook fires even when turn raises exception
- [x] 6.16 Integration: `test_pre_turn_fires_per_turn_in_multi_turn` — in a 3-turn run, pre_turn fires 3 times and post_turn fires 3 times

## 7. Phase 2: Deprecate Old Names + AgentHooks.as_capability()

- [x] 7.1 Verify `DeprecationWarning` emitted for `pre_run`/`post_run` config fields (added in task 1.4)
- [x] 7.2 Verify `DeprecationWarning` emitted for `run_pre_run_hooks()`/`run_post_run_hooks()` aliases (added in task 1.3)
- [x] 7.3 Add `DeprecationWarning` to `AgentHooks.as_capability()` with message recommending HookAwareTurn firing path
- [x] 7.4 Add `DeprecationWarning` to `_wrap_before_run()`, `_wrap_after_run()`, `_wrap_before_tool_execute()`, `_wrap_after_tool_execute()` methods
- [x] 7.5 Write test: deprecation warning emitted when `as_capability()` is called
- [x] 7.6 Write test: `as_capability()` still returns functional Hooks capability (backward compat)
- [x] 7.7 Update documentation: mark `as_capability()` and old hook names as deprecated, recommend migration path

## 8. Phase 3: Slim NativeAgentHookManager + Remove Dead Code/Tests

- [x] 8.1 Check for subclasses of `NativeAgentHookManager` — if any exist, add delegation shims with deprecation warnings
- [x] 8.2 Remove `run_pre_run_hooks()` and `run_post_run_hooks()` (now `run_pre_turn_hooks`/`run_post_turn_hooks`) delegation methods from `NativeAgentHookManager`
- [x] 8.3 Remove hook-stripping logic from `NativeAgentHookManager.as_capability()` method (lines 483-486 of `hook_manager.py` — no longer needed)
- [x] 8.4 Remove `pre_turn`/`post_turn` firing from `BaseAgent._run_stream_once()` **for native agents only** (ACP standalone retains firing — see Future Work in design.md)
- [x] 8.5 Remove double-firing guard (`hooks_fired` set) from RunContext for native agents (retain for ACP until standalone refactored — see Future Work)
- [x] 8.6 Identify and remove tests that assert hooks DON'T fire in SessionPool mode
- [x] 8.7 Identify and remove tests that validate the stripping hack behavior
- [x] 8.8 Identify and remove tests that mock around the broken hook path instead of testing real firing
- [x] 8.9 Verify `_ToolInterceptCapability` still works correctly for native tool hooks
- [x] 8.10 Write test: verify hooks fire correctly after slimming (no double-firing, no missing hooks)
- [x] 8.11 Write test: verify `_ToolInterceptCapability` tool hooks still block/modify as expected
- [x] 8.12 Verify `NativeAgentHookManager` is ~200 LOC (down from 661)

## 9. Phase 4: Remove Deprecated APIs (breaking, v0.5.0)

- [x] 9.1 Remove `pre_run`/`post_run` aliases from `HooksConfig` (config fields)
- [x] 9.2 Remove `run_pre_run_hooks()`/`run_post_run_hooks()` alias methods from `AgentHooks`
- [x] 9.3 Remove `AgentHooks.as_capability()` method entirely
- [x] 9.4 Remove `_wrap_before_run()`, `_wrap_after_run()`, `_wrap_before_tool_execute()`, `_wrap_after_tool_execute()` methods
- [x] 9.5 Remove `as_capability` from `__init__.py` exports if present
- [x] 9.6 Search and remove any remaining references to removed methods/names in source and tests
- [x] 9.7 Write test: verify clean import (no ImportError) after removal
- [x] 9.8 Update migration documentation for v0.5.0 release notes

## 10. Documentation

- [x] 10.1 Update AGENTS.md: document unified hook system architecture, Turn.execute() firing, per-turn semantics, and test strategy
- [ ] 10.2 Document ACP limitations: advisory vs blocking hooks, subprocess execution visibility
- [ ] 10.3 Document the three-tier test strategy (core/smoke/integration) and the smoke coverage matrix
- [x] 10.4 Document the `pre_run`→`pre_turn` / `post_run`→`post_turn` rename and migration path
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
