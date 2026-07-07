## ADDED Requirements

### Requirement: Hooks fire from Turn.execute() via HookAwareTurn for all agent types

`Turn.execute()` SHALL fire `pre_turn` hooks before the LLM/ACP prompt and `post_turn` hooks after the response completes (or on error/cancellation). Tool hooks (`pre_tool_use`/`post_tool_use`) SHALL fire during tool execution within the turn. This firing SHALL occur regardless of agent type (native or ACP) and execution mode (standalone or SessionPool), because `Turn.execute()` is called in both paths.

- `pre_turn` hooks SHALL fire after turn setup but before the LLM call / ACP prompt
- `post_turn` hooks SHALL fire in the `finally` block of `Turn.execute()`, after the response completes
- `post_turn` hooks SHALL fire even if the turn was cancelled or errored
- `pre_tool_use` hooks SHALL fire before each tool execution within the turn
- `post_tool_use` hooks SHALL fire after each tool execution within the turn
- HookInput for `pre_turn` SHALL be constructed using `AgentHooks.run_pre_turn_hooks()` keyword parameters (`agent_name`, `prompt`, `session_id`). No new `HookInput` dataclass is needed — the existing parameter pattern is used.
- HookInput for `post_turn` SHALL include `agent_name`, `session_id`, `result`, and `duration_ms` via `AgentHooks.run_post_turn_hooks()` keyword parameters. The `run_post_turn_hooks()` method signature SHALL accept `duration_ms: float = 0.0` (currently `duration_ms` is a tool-only field in `HookInput`; this extends it to turn-level usage).
- A double-firing guard using `run_ctx.hooks_fired: set[str]` SHALL prevent hooks from firing twice during the migration period when both old and new firing paths coexist
- The `hooks_fired` set SHALL be cleared at the start of each turn to support multi-turn runs (in `RunHandle.start()` turn loop for Path A, in `_run_stream_once()` for Path B)
- **Guard direction**: In Path B (standalone), the old path (`_run_stream_once()`) fires FIRST and adds keys to `hooks_fired`. The new path (`Turn.execute()`) checks `hooks_fired` and skips if the key is present. In Path A (SessionPool), `Turn.execute()` is the only path, so the guard is empty and hooks fire normally.
- `RunHandle.start()` SHALL NOT fire any hooks — it only manages the turn loop

#### Scenario: pre_turn fires in SessionPool mode
- **WHEN** a native agent runs through SessionPool (RunHandle.start() → turn.execute())
- **THEN** `pre_turn` hooks fire before the LLM call in `Turn.execute()`
- **AND** HookInput contains `agent_name`, `session_id`, and `prompt`

#### Scenario: post_turn fires on cancellation
- **WHEN** a turn is cancelled mid-execution
- **THEN** `post_turn` hooks fire in the `finally` block of `Turn.execute()`
- **AND** HookInput contains the cancellation context

#### Scenario: pre_turn fires for ACP agent
- **WHEN** an ACP agent runs through RunHandle.start() → ACPTurn.execute()
- **THEN** `pre_turn` hooks fire before the ACP prompt is sent
- **AND** the hook receives the same HookInput structure as native agents

#### Scenario: pre_turn fires per-turn in multi-turn run
- **WHEN** a run has 3 turns (initial prompt + 2 followups)
- **THEN** `pre_turn` hooks fire 3 times (once per turn)
- **AND** `post_turn` hooks fire 3 times (once per turn)

#### Scenario: Double-firing guard prevents duplicate hooks
- **WHEN** the old firing path (BaseAgent._run_stream_once) and new path (Turn.execute) both exist (Path B standalone)
- **AND** `pre_turn` was already fired by the old path in `_run_stream_once()`
- **THEN** `Turn.execute()` SHALL check `run_ctx.hooks_fired` and skip firing `pre_turn`
- **AND** `run_ctx.hooks_fired` contains `"pre_turn"` indicating it was already fired

#### Scenario: hooks_fired cleared per turn in multi-turn run
- **WHEN** a run has 3 turns (initial prompt + 2 followups)
- **THEN** `hooks_fired` SHALL be cleared at the start of each turn
- **AND** hooks fire correctly in all 3 turns (not just the first)

### Requirement: pre_run/post_run renamed to pre_turn/post_turn

The hook event names `pre_run`/`post_run` SHALL be renamed to `pre_turn`/`post_turn` throughout the codebase. The old names SHALL be kept as deprecated aliases that emit `DeprecationWarning` when used. The old names SHALL be removed in v0.5.0.

- `HookInput.event` values: `"pre_run"` → `"pre_turn"`, `"post_run"` → `"post_turn"`
- `AgentHooks.run_pre_run_hooks()` → `run_pre_turn_hooks()` (old name calls new + emits warning)
- `AgentHooks.run_post_run_hooks()` → `run_post_turn_hooks()` (old name calls new + emits warning)
- `HooksConfig.pre_run` → `pre_turn`, `post_run` → `post_turn` (old names map to new + emit warning)
- All spec and documentation references updated

#### Scenario: Deprecated pre_run alias emits warning
- **WHEN** a user configures `hooks.pre_run:` in YAML
- **THEN** the config is accepted and mapped to `pre_turn`
- **AND** a `DeprecationWarning` is emitted: "pre_run is deprecated, use pre_turn"

#### Scenario: Deprecated run_pre_run_hooks emits warning
- **WHEN** `AgentHooks.run_pre_run_hooks()` is called
- **THEN** it delegates to `run_pre_turn_hooks()`
- **AND** a `DeprecationWarning` is emitted

### Requirement: HookAwareTurn mixin provides shared hook helpers for all 4 hook types

The system SHALL provide a `HookAwareTurn` mixin class in `orchestrator/turn.py` (alongside the existing `Turn` ABC at line 19) that Turn implementations can inherit to get hook firing helpers for ALL 4 hook types.

- `HookAwareTurn` SHALL expose `_hooks: AgentHooks | None = None` and `_run_ctx: AgentRunContext | None = None` as class variable annotations (NOT properties — properties prevent subclass `__init__` from setting via simple assignment). Subclasses (`NativeTurn`, `ACPTurn`) set these in `__init__`. Mock/test turns inherit the `None` default (guard is skipped, hooks always fire).
- `fire_pre_turn_hooks(prompt, **extra) -> HookResult | None` SHALL construct HookInput and call `_hooks.run_pre_turn_hooks()`
- `fire_post_turn_hooks(result, **extra) -> HookResult | None` SHALL construct HookInput and call `_hooks.run_post_turn_hooks()`
- `fire_pre_tool_hooks(tool_name, tool_input, **extra) -> HookResult | None` SHALL construct HookInput and call `_hooks.run_pre_tool_hooks()`
- `fire_post_tool_hooks(tool_name, tool_output, **extra) -> HookResult | None` SHALL construct HookInput and call `_hooks.run_post_tool_hooks()`
- All methods SHALL return `HookResult | None` (None when no hooks configured)
- `NativeTurn` SHALL inherit from `HookAwareTurn`
- `ACPTurn` SHALL inherit from `HookAwareTurn`
- Future Turn implementations for new agent types SHALL inherit from `HookAwareTurn`
- `NativeTurn` SHALL delegate tool hooks to `_ToolInterceptCapability` (existing pydantic-ai capability)
- `ACPTurn` SHALL implement tool hooks directly (advisory on ToolCallStart, modifying on ToolCallComplete)

#### Scenario: NativeTurn uses HookAwareTurn for pre_turn/post_turn
- **WHEN** a native agent's NativeTurn.execute() runs
- **THEN** `fire_pre_turn_hooks()` is called before the LLM call
- **AND** `fire_post_turn_hooks()` is called after the response
- **AND** tool hooks are handled by `_ToolInterceptCapability` (not HookAwareTurn's tool methods)

#### Scenario: ACPTurn uses HookAwareTurn for all 4 hooks
- **WHEN** an ACP agent's ACPTurn processes events
- **THEN** `fire_pre_turn_hooks()` is called before the ACP prompt
- **AND** `fire_post_turn_hooks()` is called after the response
- **AND** `fire_pre_tool_hooks()` is called on ToolCallStart (advisory)
- **AND** `fire_post_tool_hooks()` is called on ToolCallComplete (can modify output)

### Requirement: AgentHooks.as_capability() is deprecated

`AgentHooks.as_capability()` and its `_wrap_*` helper methods SHALL emit a `DeprecationWarning` when called. The methods SHALL remain functional during the deprecation period but SHALL be removed in v0.5.0.

- The deprecation warning message SHALL recommend using the new firing path (HookAwareTurn in Turn.execute() for all 4 hooks, _ToolInterceptCapability for native tool hooks)
- The `_wrap_before_run`, `_wrap_after_run`, `_wrap_before_tool_execute`, `_wrap_after_tool_execute` methods SHALL each emit the warning
- The hook-stripping logic in `NativeAgentHookManager.as_capability()` SHALL remain during deprecation to prevent double-firing

#### Scenario: Deprecation warning on as_capability() call
- **WHEN** `AgentHooks.as_capability()` is called
- **THEN** a `DeprecationWarning` is emitted
- **AND** the warning message recommends the new firing path
- **AND** the method still returns a functional `Hooks` capability

### Requirement: Comprehensive hook test coverage with three tiers

The system SHALL maintain three tiers of hook tests that collectively ensure hooks fire correctly and produce the expected effects across all agent types and execution modes.

**Core (unit tests)** SHALL verify:
- `HookAwareTurn` mixin constructs correct HookInput and handles HookResult for all 4 hook types
- `AgentHooks._run_hooks()` parallel dispatch with deny>ask>allow priority
- Double-firing guard prevents duplicate hook invocation
- Advisory vs blocking semantics are correctly applied
- Deprecated `pre_run`/`post_run` aliases emit warnings and delegate correctly

**Smoke tests** SHALL verify hooks fire at all for every agent type x execution mode x hook event combination:
- Native standalone: pre_turn, post_turn, pre_tool_use, post_tool_use
- Native SessionPool: pre_turn, post_turn, pre_tool_use, post_tool_use
- ACP standalone: pre_turn, post_turn, pre_tool_use (advisory), post_tool_use
- ACP SessionPool: pre_turn, post_turn, pre_tool_use (advisory), post_tool_use
- Each smoke test SHALL assert the hook callback was invoked at least once

**Integration tests** SHALL verify hook results take effect end-to-end:
- `decision="deny"` on pre_turn blocks turn execution
- `decision="deny"` on pre_tool_use (native) blocks tool execution
- `decision="deny"` on pre_tool_use (ACP advisory) is logged but does not block
- `decision="deny"` on pre_tool_use (ACP permission) blocks tool execution
- `modified_output` on post_tool_use replaces tool output
- `additional_context` on post_tool_use is injected into conversation

#### Scenario: Smoke test catches SessionPool hook breakage
- **WHEN** a regression causes pre_turn hooks to stop firing in SessionPool mode
- **THEN** the smoke test `test_pre_turn_fires_native_session_pool` SHALL fail
- **AND** CI SHALL block the merge

#### Scenario: Integration test verifies deny blocks native tool
- **WHEN** a pre_tool_use hook returns `decision="deny"` for a native agent
- **THEN** the tool execution SHALL be blocked
- **AND** a `ModelRetry` SHALL be raised with the hook's reason

#### Scenario: Integration test verifies ACP advisory deny is logged
- **WHEN** a pre_tool_use hook returns `decision="deny"` for an ACP agent (advisory mode)
- **THEN** a warning SHALL be logged that the deny cannot be enforced
- **AND** tool execution SHALL proceed (subprocess already executing)

### Requirement: Dead code and broken tests are removed

The system SHALL remove code that is dead or broken as a result of the hook system unification, and SHALL remove tests that validated the old broken behavior.

**Dead code to remove:**
- `NativeAgentHookManager.as_capability()` stripping hack (lines 483-486 of `hook_manager.py` — sets `_registry` entries to empty lists) — dead because it makes the Hooks capability a no-op. Note: `AgentHooks.as_capability()` itself does NOT strip; the stripping is in `NativeAgentHookManager.as_capability()`.
- `NativeAgentHookManager` delegate methods (`run_pre_run_hooks`, `run_post_run_hooks`, `run_pre_tool_hooks`, `run_post_tool_hooks`) — dead after Turn takes over firing
- `BaseAgent._run_stream_once()` pre_turn/post_turn hook firing blocks — dead after Turn takes over firing

**Broken tests to remove:**
- Tests that assert hooks DON'T fire in SessionPool mode (validating broken behavior as correct)
- Tests that mock around the broken hook path instead of testing real firing
- Tests that validate the stripping hack behavior (asserting registry entries are emptied)

**Replacement:** Removed tests SHALL be replaced by the comprehensive three-tier test suite (core + smoke + integration).

#### Scenario: Dead stripping hack removed
- **WHEN** the hook-stripping code in `NativeAgentHookManager.as_capability()` (lines 483-486 of `hook_manager.py`) is removed
- **THEN** no code SHALL set `_registry` entries to empty lists in `NativeAgentHookManager`
- **AND** the Hooks capability produced by `AgentHooks.as_capability()` SHALL retain its registered hooks

#### Scenario: Broken test replaced
- **WHEN** a test that asserted "hooks don't fire in SessionPool" is removed
- **THEN** a new smoke test SHALL assert "hooks DO fire in SessionPool"
- **AND** the new test SHALL use real hook callbacks, not mocks
