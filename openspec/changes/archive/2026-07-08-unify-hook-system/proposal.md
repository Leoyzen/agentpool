## Why

The hook system is broken across agent types. `pre_run`/`post_run` hooks don't fire in SessionPool mode (RunHandle.start() bypasses BaseAgent._run_stream_once()). ACP agents have zero tool-level hook interception because tools execute in a subprocess. The `NativeAgentHookManager.as_capability()` method strips all pydantic-ai hook entries to prevent double-firing, making the `Hooks` capability a no-op. This makes hooks unreliable and incomplete.

Critically, **these failures were never caught by tests**. The existing test suite only covers standalone mode hooks and `AgentHooks._run_hooks()` dispatch logic in isolation. No integration test verifies that hooks actually fire through the SessionPool execution path, and no smoke test checks hook coverage for ACP agents. The hook system was silently broken for all protocol-server-driven runs.

Additionally, the hook names `pre_run`/`post_run` are semantically misleading. In the original design "run" meant "one agent execution" = one prompt → one response. But with multi-turn `RunHandle.start()` loops, "run" now means the entire run loop (potentially many turns). The correct semantic is **per-turn**: each turn's prompt triggers `pre_turn`, each turn's response triggers `post_turn`. This change renames to `pre_turn`/`post_turn` and deprecates the old names.

## What Changes

- Rename `pre_run`/`post_run` → `pre_turn`/`post_turn` throughout the codebase (hooks, config, specs, docs). Old names kept as deprecated aliases during transition, removed in v0.5.0.
- Move ALL 4 hook firing points (`pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) to `Turn.execute()` via `HookAwareTurn` mixin — `Turn.execute()` is called in both standalone and SessionPool paths, making it the true convergence point
- `RunHandle.start()` does NOT fire any hooks — it only manages the turn loop
- `HookAwareTurn` handles ALL 4 hook types (not just tool hooks as in the previous design)
- Both `NativeTurn` and `ACPTurn` inherit from `HookAwareTurn`
- Add advisory tool hooks (`pre_tool_use`/`post_tool_use`) for ACP agents by intercepting `session/update` events (ToolCallStart/ToolCallComplete) in `ACPTurn.execute()`
- Add blocking tool hooks for ACP via `session/request_permission` interception in `ACPClientHandler`
- **BREAKING (v0.5.0)**: Remove `AgentHooks.as_capability()` adapter methods (deprecated with warning in Phase 3)
- **BREAKING (v0.5.0)**: Remove `pre_run`/`post_run` aliases (replaced by `pre_turn`/`post_turn`)
- Slim `NativeAgentHookManager` from 661 → ~200 LOC by removing pre/post turn delegation methods and the hook-stripping hack
- Remove `pre_run`/`post_run` firing from `BaseAgent._run_stream_once()` for native agents (ACP standalone retains firing — see Future Work)
- Remove dead/broken tests that validated the old broken behavior or tested no-op code paths
- Design and implement comprehensive test coverage: core (unit), smoke (coverage verification), and integration (end-to-end behavioral verification)
- Net code reduction: ~-195 LOC (code) + new test coverage

**Non-goal**: Expanding the `hooks:` YAML config to support all 38 pydantic-ai hook methods. Native agent users can access advanced pydantic-ai hooks via the existing `capabilities:` config. The `hooks:` section keeps its 4 hook points (renamed `pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) — the focus is on making those 4 actually work reliably.

**Known gap (future work)**: ACP agents in standalone mode use `ACPAgent._stream_events()` which does NOT call `ACPTurn.execute()`. For ACP standalone, `pre_turn`/`post_turn` hooks continue to fire in `_run_stream_once()` (retained, not removed). Refactoring `ACPAgent._stream_events()` to use `ACPTurn.execute()` requires building an `ACPAgentAPI` adapter implementing full `ACPClientProtocol` (missing `stream_events()` and `get_messages()` — see TODO at `acp_agent.py:648-652`). This is tracked as future work in design.md and tasks.md section 11.

## Capabilities

### New Capabilities
- `unified-hook-system`: Unified hook firing, per-turn semantics, and comprehensive test coverage across native and ACP agent types

### Modified Capabilities
- `session-orchestration`: Turn.execute() gains all 4 hook firing responsibilities via HookAwareTurn; RunHandle.start() no longer fires hooks
- `acp-server`: ACP agents gain advisory tool hooks and blocking permission-based hooks via ACPTurn

## Impact

**Affected files:**
- `src/agentpool/orchestrator/turn.py` — `HookAwareTurn` mixin handles ALL 4 hook types (+60 LOC)
- `src/agentpool/agents/native_agent/turn.py` — `NativeTurn` inherits `HookAwareTurn`, fires pre_turn/post_turn around LLM call (+30 LOC)
- `src/agentpool/agents/acp_agent/turn.py` — `ACPTurn` inherits `HookAwareTurn`, fires all 4 hooks around ACP prompt/response (+50 LOC)
- `src/agentpool/agents/acp_agent/client_handler.py` — `request_permission()` gains hook integration (+15 LOC)
- `src/agentpool/hooks/agent_hooks.py` — Rename methods, deprecate then remove `as_capability()` and `_wrap_*` methods (-130 LOC in v0.5.0)
- `src/agentpool/hooks/base.py` — Rename `pre_run`/`post_run` → `pre_turn`/`post_turn` in HookInput/HookResult
- `src/agentpool/agents/native_agent/hook_manager.py` — Slim `NativeAgentHookManager` (-200 LOC, 661→~200)
- `src/agentpool/agents/base_agent.py` — Remove pre_run/post_run from `_run_stream_once()` (-20 LOC)
- `src/agentpool/agents/acp_agent/acp_agent.py` — Pass hooks to ACPTurn (+5 LOC)
- `src/agentpool/agents/context.py` — Add `hooks_fired: set[str]` for double-firing guard
- `src/agentpool_config/hooks.py` — Rename config fields, add deprecated aliases

**Affected tests (removal/cleanup):**
- Any tests that assert hooks DON'T fire in SessionPool mode (testing broken behavior)
- Any tests that validate the `as_capability()` stripping hack
- Any tests that mock around the broken hook path instead of testing real firing

**Affected APIs:**
- `pre_run`/`post_run` → `pre_turn`/`post_turn` (deprecated aliases during transition)
- `AgentHooks.as_capability()` deprecated → removed in v0.5.0
- `NativeAgentHookManager` public API simplified (delegate methods removed)

**Dependencies:**
- No new dependencies
- Relies on existing pydantic-ai `AbstractCapability` / `Hooks` / `CombinedCapability` system

**GitHub issues:**
- Implements #124 (sub-issue of #123 audit)
- Related: `thin-wrapper-refactor` OpenSpec (Phase 5/6 overlap)
