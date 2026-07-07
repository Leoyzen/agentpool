## Context

AgentPool's hook system provides lifecycle interception (`pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) via three hook types: `CallableHook` (Python callables), `CommandHook` (subprocess with JSON stdin/stdout), and `PromptHook` (LLM evaluation). Hooks are collected in `AgentHooks` which dispatches them in parallel with deny>ask>allow priority combination.

**Current state has three critical gaps:**

1. **SessionPool path missing run hooks**: `pre_run`/`post_run` fire in `BaseAgent._run_stream_once()` (standalone mode only). When running through `RunHandle.start()` (SessionPool mode), this method is bypassed, so hooks never fire. All protocol servers (ACP, OpenCode, AG-UI, OpenAI API) use the SessionPool path.

2. **ACP agents have no tool hooks**: ACP agents run tools in a subprocess. The subprocess emits `session/update` events (ToolCallStart/Progress/Complete) that agentpool can observe, but cannot intercept before execution. The only blocking point is `session/request_permission`.

3. **Test coverage is fundamentally broken**: The existing test suite only tests `AgentHooks._run_hooks()` dispatch logic in isolation and standalone-mode hook firing. No integration test verifies hook firing through the SessionPool execution path. No smoke test checks hook coverage for ACP agents. The `NativeAgentHookManager.as_capability()` method strips all pydantic-ai hook entries to prevent double-firing (making `Hooks` capability a no-op), and no test catches this. Hooks were silently broken for all server-driven runs.

**Semantic correction**: The hook names `pre_run`/`post_run` imply per-run-loop semantics, but the actual intent is per-turn (one prompt → one response). In multi-turn `RunHandle.start()` loops, `pre_turn` should fire before each turn's prompt and `post_turn` after each turn's response. This change renames `pre_run`/`post_run` → `pre_turn`/`post_turn` to reflect the correct per-turn semantic.

**Existing infrastructure:**
- `Turn` ABC at `orchestrator/turn.py:19` — both `NativeTurn` and `ACPTurn` inherit from it
- `ACPTurn.execute()` event streaming at `turn.py:152` — processes ACP events
- `NativeAgentHookManager` (661 LOC) already bridges to pydantic-ai via `_ToolInterceptCapability` (an `AbstractCapability` subclass)
- `NativeAgentHookManager.as_capability()` (lines 483-486 of `hook_manager.py`) calls `AgentHooks.as_capability()` then strips `_registry` entries to prevent double-firing, making the `Hooks` capability a no-op. Note: `AgentHooks.as_capability()` itself does NOT strip — the stripping is in `NativeAgentHookManager`.
- 7 custom capabilities already exist in `src/agentpool/capabilities/`
- pydantic-ai's `CombinedCapability` handles topological dispatch of multiple capabilities
- Native agent users can already access advanced pydantic-ai hooks via the `capabilities:` config section

**Stakeholders:** Native agent users (full hook coverage), ACP agent users (limited but functional hooks), future agent type implementors (clear extension pattern).

## Goals / Non-Goals

**Goals:**
- Rename `pre_run`/`post_run` → `pre_turn`/`post_turn` throughout (deprecate old names, remove in v0.5.0)
- ALL 4 hook types fire in `Turn.execute()` via `HookAwareTurn` mixin — the single convergence point for both standalone and SessionPool paths
- `RunHandle.start()` does NOT fire hooks — it only manages the turn loop
- Hooks fire consistently across all agent types (native + ACP) and execution modes (standalone + SessionPool)
- ACP agents gain advisory tool hooks (can observe but not block) and blocking permission hooks
- `NativeAgentHookManager` slimmed from 661 → ~200 LOC by removing redundant delegation and stripping hacks
- Dead/broken tests that validated the old broken behavior are removed
- Comprehensive test coverage: core (unit), smoke (coverage verification), and integration (end-to-end behavioral verification) — ensuring hook breakage is caught by CI
- Net code reduction of ~195 LOC

**Non-Goals:**
- Expanding `hooks:` YAML config to support all 38 pydantic-ai hook methods (native agent users can use `capabilities:` config for advanced hooks)
- Adding `HookSupport` enum or config-time validation (the 4 hook points are the scope)
- Redesigning the Hook type hierarchy (`CallableHook`/`CommandHook`/`PromptHook` stay as-is)
- Changing the parallel dispatch + deny>ask>allow priority combination logic
- Making ACP tool hooks blocking (fundamentally impossible — subprocess already executing)
- Updating `acp-proxy-chain-refactor` — that change has its own `HookProxy` design that will reference the renamed hooks

## Decisions

### D1: Fire ALL hooks in Turn.execute() via HookAwareTurn, not RunHandle

**Decision**: Move ALL 4 hook firing points (`pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) to `Turn.execute()` via the `HookAwareTurn` mixin. `RunHandle.start()` does NOT fire any hooks — it only manages the turn loop.

**Rationale**: `Turn.execute()` is the true convergence point — it is called in both execution paths:
```
Path A (SessionPool): RunHandle.start() → turn.execute()  ← hooks fire here
Path B (standalone, native): run_stream() → _run_stream_once() → _stream_events() → NativeTurn.execute()  ← hooks fire here
Path B (standalone, ACP): run_stream() → _run_stream_once() → _stream_events()  ← hooks fire in _run_stream_once() (retained, see Future Work)
```

This fixes the root cause directly: hooks were in `_run_stream_once()` which is bypassed by `RunHandle.start()`. Moving to `Turn.execute()` means hooks fire regardless of which path initiated the run.

**Per-turn semantics**: `pre_turn` fires before the LLM call / ACP prompt in each turn. `post_turn` fires after the response completes in each turn. In a multi-turn run (steer/followup), each turn triggers its own `pre_turn`/`post_turn` pair.

**Hooks access**: `Turn` implementations access hooks via `self._hooks` property (provided by `HookAwareTurn`). For native agents, hooks come from `BaseAgent.hooks` (line 264). For ACP agents, `ACPAgent` already accepts `hooks` param (line 159) and passes it to `ACPTurn`.

**Standalone mode (native agents)**: No special handling needed — `run_stream()` Path B calls `_stream_events()` which creates `NativeTurn` and calls `turn.execute()` (verified at `native_agent/agent.py:1154-1163`). Hooks fire via `HookAwareTurn`.

**Standalone mode (ACP agents) — known gap**: `ACPAgent._stream_events()` (`acp_agent.py:412-511`) is an inline implementation that does **NOT** use `ACPTurn.execute()`. This means ACP standalone mode will not fire hooks via `HookAwareTurn`. Additionally, `ACPAgent.create_turn()` (`acp_agent.py:648-652`) has a TODO noting that `ACPAgentAPI` does not fully implement `ACPClientProtocol` (missing `stream_events()` and `get_messages()`), so `ACPTurn.execute()` may fail at runtime in SessionPool mode until an adapter is built.

**Mitigation**: During Phase 1-3, `_run_stream_once()` hook firing is kept for ACP standalone (guarded by `hooks_fired` to prevent double-firing with the old path). Phase 3 only removes `_run_stream_once()` firing for **native** agents (where `NativeTurn.execute()` is confirmed working). ACP `_run_stream_once()` firing is retained until the ACP standalone path is refactored.

**Future work** (out of scope for this change):
- Refactor `ACPAgent._stream_events()` to delegate to `ACPTurn.execute()`, making it the true convergence point for ACP standalone
- Build the `ACPAgentAPI` adapter implementing `ACPClientProtocol` fully (`stream_events()`, `get_messages()`)
- Once ACP standalone routes through `ACPTurn.execute()`, remove `_run_stream_once()` firing for ACP agents
- Tracked as a follow-up issue, not blocked by this change

**Alternatives considered**:
- Fire `pre_turn`/`post_turn` in `RunHandle.start()` — rejected because (a) `RunHandle.start()` manages the turn loop, not individual turns; (b) per-turn hooks would need to be inside the loop anyway, duplicating logic; (c) `RunHandle.start()` is not called in standalone Path B
- Fire in `BaseAgent.run()` — rejected because `BaseAgent.run()` doesn't cover all SessionPool execution paths (RunHandle.start() is the actual convergence point for server-driven runs, and RunHandle calls turn.execute() not BaseAgent.run())
- Keep in `_run_stream_once()` and also add to RunHandle — rejected because it causes double-firing and requires stripping hacks (the current problem)
- Route standalone through `create_run_stream()` → `RunHandle.start()` — considered as future cleanup but too large for Phase 1
- Refactor `ACPAgent._stream_events()` to use `ACPTurn.execute()` — future work, requires `ACPAgentAPI` adapter implementing full `ACPClientProtocol`

### D2: ACP tool hooks are advisory, permission hooks are blocking

**Decision**: ACP `pre_tool_use` hooks fire on `ToolCallStart` event (advisory only — cannot block). ACP `post_tool_use` hooks fire on `ToolCallComplete` (can modify output by replacing the event before yielding). Blocking pre-tool interception uses `session/request_permission`.

**Permission hook priority**: In `ACPClientHandler.request_permission()` (line 208, `client_handler.py`), hooks SHALL fire **before** the `auto_approve` check (line 217). Priority chain: hooks → auto_approve → callback → input_provider. Hooks represent explicit security policy that should override convenience settings.

**ACP output modification**: In `ACPTurn.execute()` (line 152, `turn.py`), events from `acp_to_native_event(update)` are intercepted before yielding. If `post_tool_use` hooks return `modified_output`, the `ToolCallCompleteEvent`'s output field is replaced before the event is yielded to the consumer. This modifies the event stream, not the subprocess's internal state.

**Rationale**: ACP tool execution is a black box. By the time `ToolCallStart` is emitted, the subprocess has already started executing the tool. The only pre-execution interception point is `request_permission`, which the ACP agent emits before running certain tools.

**Alternatives considered**:
- No ACP tool hooks — rejected because users need observability even if they can't block
- Block via `request_permission` only — rejected because not all tools trigger permission requests
- Proxy architecture (intercept JSON-RPC frames) — future work in `acp-proxy-chain-refactor`, which adds `HookProxy` for wire-level blocking. This change provides process-level advisory hooks as a baseline.

### D3: Keep Hook types, retire AgentHooks.as_capability() adapter

**Decision**: Keep `Hook`/`CallableHook`/`CommandHook`/`PromptHook` types and `AgentHooks._run_hooks()` parallel dispatch. Remove `AgentHooks.as_capability()` bridge methods. All 4 hook types fire from `Turn.execute()` (via `HookAwareTurn`) — run hooks AND tool hooks.

**Rationale**: The Hook types provide value pydantic-ai lacks: subprocess hooks (`CommandHook`), LLM-evaluated hooks (`PromptHook`), regex input matching, and parallel deny>ask>allow priority combination. The `as_capability()` adapter is a transitional bridge that creates the double-firing problem.

**Tool hooks for native agents**: `_ToolInterceptCapability` remains as the native-only tool hook implementation (it IS a pydantic-ai `AbstractCapability`). But `pre_turn`/`post_turn` firing moves from `_run_stream_once()` / `BaseAgent` to `NativeTurn.execute()` via `HookAwareTurn`.

**Alternatives considered**:
- Port everything to pydantic-ai capabilities — rejected because `CommandHook`/`PromptHook` have no pydantic-ai equivalents, and parallel deny>ask>allow combination doesn't exist in pydantic-ai (sequential dispatch only)
- Keep `as_capability()` — rejected because it requires stripping hooks to prevent double-firing, which is fragile and confusing

### D4: HookAwareTurn mixin handles ALL 4 hook types

**Decision**: Create `HookAwareTurn` mixin in `orchestrator/turn.py` (alongside existing `Turn` ABC at line 19) that handles ALL 4 hook types:
- `fire_pre_turn_hooks(prompt, **extra) -> HookResult | None` — called before LLM/ACP prompt
- `fire_post_turn_hooks(result, **extra) -> HookResult | None` — called after response
- `fire_pre_tool_hooks(tool_name, tool_input, **extra) -> HookResult | None` — called before tool execution
- `fire_post_tool_hooks(tool_name, tool_output, **extra) -> HookResult | None` — called after tool execution

Both `NativeTurn` and `ACPTurn` inherit from `HookAwareTurn`. `NativeTurn` delegates tool hooks to `_ToolInterceptCapability` (which already handles them via pydantic-ai capability). `ACPTurn` implements tool hooks directly (advisory on ToolCallStart, modifying on ToolCallComplete).

**Rationale**: `Turn.execute()` is the single convergence point. Having all 4 hook types in one mixin ensures:
1. Consistent firing across agent types
2. No hooks lost when switching execution modes
3. Future agent types inherit all 4 hooks by inheriting `HookAwareTurn`
4. Per-turn semantics are correct (pre_turn/post_turn fire per turn, not per run-loop)

**Alternatives considered**:
- Put helpers in base `Turn` class — rejected because not all Turn types need hooks (e.g., mock test turns)
- Separate mixins for run hooks vs tool hooks — rejected because it fragments the hook lifecycle and makes inheritance complex
- Standalone utility functions — rejected because they need access to `self._hooks` and `self._session_id`

### D5: Rename pre_run/post_run → pre_turn/post_turn

**Decision**: Rename `pre_run`/`post_run` to `pre_turn`/`post_turn` throughout the codebase: hook event names, `AgentHooks` method names (`run_pre_run_hooks` → `run_pre_turn_hooks`), config fields, `HookInput.event` values, docs. Old names kept as deprecated aliases during transition (emit `DeprecationWarning`), removed in v0.5.0.

**Rationale**: The names `pre_run`/`post_run` were correct when "run" = "one agent execution" = one prompt → one response. With multi-turn `RunHandle.start()` loops, "run" now means the entire run loop. The correct semantic is per-turn: each turn's prompt triggers `pre_turn`, each turn's response triggers `post_turn`. The rename makes this explicit and prevents confusion.

**Affected names**:
- `HookInput.event`: `"pre_run"` → `"pre_turn"`, `"post_run"` → `"post_turn"`
- `AgentHooks.run_pre_run_hooks()` → `run_pre_turn_hooks()`
- `AgentHooks.run_post_run_hooks()` → `run_post_turn_hooks()`
- `HooksConfig.pre_run` → `pre_turn`, `post_run` → `post_turn` (YAML config)
- All spec/doc references

**Deprecated aliases** (removed v0.5.0):
- `AgentHooks.run_pre_run_hooks()` calls `run_pre_turn_hooks()` + emits `DeprecationWarning`
- `HooksConfig.pre_run` maps to `pre_turn` + emits `DeprecationWarning`

**Alternatives considered**:
- Keep old names, document per-turn semantics — rejected because names carry semantic weight; "pre_run" will always suggest "before the run loop" not "before each turn"
- Add `pre_turn`/`post_turn` as new hooks alongside `pre_run`/`post_run` — rejected because they're the same concept, just renamed; having both creates confusion

### D6: Comprehensive test strategy with three tiers

**Decision**: Design and implement a three-tier test strategy that catches hook breakage at multiple levels:

1. **Core (unit tests)**: Test individual components in isolation — `HookAwareTurn` mixin, `HookInput` construction, `HookResult` handling, deny>ask>allow combination, advisory vs blocking semantics, double-firing guard.

2. **Smoke (coverage verification)**: Verify that hooks **fire at all** for each agent type × execution mode combination. A smoke test that simply asserts "pre_turn hook was called once" would have caught the current breakage. These tests are cheap, fast, and specifically designed to catch "hooks silently stopped firing" regressions.

3. **Integration (end-to-end behavioral)**: Verify that hook **results** actually take effect — deny blocks execution, modified_output replaces output, additional_context is injected, CommandHook subprocess receives correct JSON, PromptHook LLM evaluation returns correct decision.

**Rationale**: The current breakage (hooks not firing in SessionPool) was never caught because tests only covered dispatch logic in isolation. A smoke test asserting "hook was called when running through SessionPool" would have immediately caught it. The three-tier approach ensures both presence (smoke) and correctness (integration) of hook behavior.

**Test matrix**:

| Test Level | What It Catches | Agent Types | Execution Modes |
|---|---|---|---|
| Core | Dispatch logic, result combination, input construction | Mock agents | N/A (unit) |
| Smoke | Hooks fire at all (presence check) | Native + ACP | Standalone + SessionPool |
| Integration | Hook results take effect (deny blocks, output modified) | Native + ACP | Standalone + SessionPool |

**Smoke test hook coverage matrix** (the "never again" tests):

| Hook Event | Native Standalone | Native SessionPool | ACP Standalone | ACP SessionPool |
|---|---|---|---|---|
| pre_turn | smoke | smoke | smoke | smoke |
| post_turn | smoke | smoke | smoke | smoke |
| pre_tool_use | smoke | smoke | smoke (advisory) | smoke (advisory) |
| post_tool_use | smoke | smoke | smoke | smoke |

Each cell is a single test that asserts the hook callback was invoked. If any cell fails, CI blocks the merge.

### D7: Dead code and broken test cleanup

**Decision**: Identify and remove code that is dead, broken, or testing broken behavior.

**Categories of dead/broken code to remove:**
1. `NativeAgentHookManager.as_capability()` stripping hack (lines 483-486 of `hook_manager.py` — sets `_registry` entries to empty lists) — dead because it makes Hooks a no-op. Note: `AgentHooks.as_capability()` itself does NOT strip; the stripping is in `NativeAgentHookManager.as_capability()`.
2. `NativeAgentHookManager` delegate methods (`run_pre_run_hooks`, `run_post_run_hooks`, `run_pre_tool_hooks`, `run_post_tool_hooks`) — dead after Turn takes over firing
3. `BaseAgent._run_stream_once()` pre_run/post_run hook firing blocks — dead after Turn takes over firing
4. Tests that assert hooks DON'T fire in SessionPool mode (testing broken behavior as correct)
5. Tests that mock around the broken path instead of testing real firing
6. Tests that validate the stripping hack behavior

**Rationale**: Dead code confuses readers and broken tests give false confidence. Removing them as part of this change ensures the codebase reflects the new unified architecture.

## Risks / Trade-offs

**[Double-firing during migration]** → Guard with `run_ctx.hooks_fired: set[str]` field on `AgentRunContext` (line 76, `agents/context.py`). During transition, if a hook point was already fired in the new path (Turn), skip it in the old path (`_run_stream_once()`). Remove the guard when old path is removed.

**[ACP advisory hooks confuse users]** → Document prominently that ACP `pre_tool_use` is advisory. Log a warning when a deny result is returned but cannot be enforced.

**[Breaking change in v0.5.0]** → `AgentHooks.as_capability()` removal AND `pre_run`/`post_run` name removal are breaking. Provide deprecation warnings before removal. Document migration path.

**[NativeAgentHookManager slimming may break subclassers]** → Check for subclasses before removing methods. If any exist, provide delegation shims with deprecation warnings.

**[Test cleanup removes coverage]** → Only remove tests that validate broken behavior. Replace with new smoke/integration tests that validate correct behavior. Net test coverage should increase, not decrease.

**[HookProxy interaction with acp-proxy-chain-refactor]** → When `HookProxy` is active in the ACP proxy chain, it handles hooks at the wire level (blocking). `HookAwareTurn` on `ACPTurn` SHALL disable itself via the `hooks_fired` guard when `HookProxy` is active. This prevents double-firing. The `acp-proxy-chain-refactor` change is responsible for implementing this guard interaction.

## Migration Plan

### Phase 1 (non-breaking): Fix hook firing + rename + add tests
- Add `hooks_fired: set[str]` to `AgentRunContext` for double-firing guard
- Rename `pre_run`/`post_run` → `pre_turn`/`post_turn` (add deprecated aliases)
- Create `HookAwareTurn` mixin with ALL 4 hook firing helpers
- Make `NativeTurn` and `ACPTurn` inherit from `HookAwareTurn`
- Fire `pre_turn`/`post_turn` in `NativeTurn.execute()` and `ACPTurn.execute()` around the LLM/ACP call
- Add ACP tool hooks in `ACPTurn.execute()` (advisory on ToolCallStart, modifying on ToolCallComplete) and `request_permission` (blocking)
- Pass hooks from `ACPAgent` to `ACPTurn`
- Add guard in `BaseAgent._run_stream_once()`: skip firing if already in `hooks_fired`
- Add core + smoke + integration tests for all hook × agent × mode combinations
- **This is the critical phase** — it fixes the broken behavior, renames hooks, and adds tests to prevent regression

### Phase 2 (deprecation): Deprecate old names + AgentHooks.as_capability()
- Add `DeprecationWarning` to `pre_run`/`post_run` aliases (config + method names)
- Add `DeprecationWarning` to `as_capability()` and `_wrap_*` methods
- Update documentation to recommend new names and firing path
- Tests: verify deprecation warnings emitted

### Phase 3 (cleanup): Slim NativeAgentHookManager + remove dead code/tests
- Remove `pre_turn`/`post_turn` delegation methods from `NativeAgentHookManager`
- Remove hook-stripping hack (no longer needed)
- Remove `pre_turn`/`post_turn` from `BaseAgent._run_stream_once()`
- Remove double-firing guard (no longer needed)
- Remove dead/broken tests
- Replace removed tests with new comprehensive tests
- Tests: verify hooks still fire correctly, no double-firing

### Phase 4 (breaking, v0.5.0): Remove deprecated APIs
- Remove `pre_run`/`post_run` aliases entirely (config + method names)
- Remove `AgentHooks.as_capability()` and `_wrap_*` methods entirely
- Remove `as_capability()` import from `__init__.py`
- Tests: verify clean import, no references to removed methods/names

### Rollback Strategy
- Each phase is independently revertible via git revert
- Phase 1 is non-breaking and includes the test coverage — safe to merge independently
- Phase 2-3 can be reverted together if issues arise
- Phase 4 is gated behind v0.5.0 release

## Open Questions

1. Should advisory ACP hooks be able to log/notify even if they can't block? (Current proposal: yes, log warning)
2. Should the `hooks:` config support conditional hooks (only fire if condition matches)? (Already supported via `hook_conditions.py` — no change needed)
3. Should smoke tests run in CI's fast path or only on PRs? (Current proposal: fast path, they're cheap)
