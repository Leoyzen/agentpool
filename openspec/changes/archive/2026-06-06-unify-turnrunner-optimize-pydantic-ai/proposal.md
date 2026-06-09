## Why

SessionPool maintains two nearly identical turn execution implementations — `TurnRunner` (for native agents) and `LegacyTurnRunner` (for non-native agents). Both manage `_post_turn_injections`, `_post_turn_prompts`, per-session locks, auto-resume loops, and `RunHandle` lifecycle with ~480 and ~494 lines of copy-pasted logic. Any bug fix or behavior change must be applied in both places, creating a guaranteed source of drift.

Separately, the Pydantic-AI tool confirmation call chain is excessively indirect: `HandleDeferredToolCalls` → `approval_bridge` → `AgentContext.get_input_provider()` → `BaseAgent._input_provider` → `InputProvider.get_tool_confirmation()`. This chain relies on `_input_provider` being set on a potentially shared `BaseAgent` instance, creating a race condition when multiple sessions use the same agent concurrently. The multi-layer fallback in `get_active_run_context()` (ContextVar → instance attr → SessionPool → background ctx) further obscures which run context receives injected prompts.

## What Changes

- **Unify TurnRunner**: Merge `TurnRunner` and `LegacyTurnRunner` into a single `TurnRunner` class that dispatches to native vs non-native execution strategies internally. Delete `orchestrator/legacy_runner.py`.
- **Bind InputProvider to SessionState**: Move the authoritative `InputProvider` reference from `BaseAgent._input_provider` to `SessionState.input_provider`. `AgentContext.get_input_provider()` reads from the session, eliminating the shared-agent race condition.
- **Optimize Pydantic-AI tool confirmation chain**: Shorten the approval path by passing `InputProvider` directly into `create_approval_bridge_capability()` rather than traversing `AgentContext` indirection. Ensure the bridge receives the per-session provider at agentlet construction time.
- **Simplify `get_active_run_context()`**: Remove the four-layer fallback (ContextVar → instance → SessionPool → background). Retain only SessionPool lookup when pooled, ContextVar when standalone.
- **Update `sessionpool-only-execution` spec**: Reflect that TurnRunner is now unified and InputProvider binding has changed.

## Capabilities

### New Capabilities
- `unified-turnrunner`: Single TurnRunner class handling both native and non-native agent execution with strategy-based dispatch.
- `session-bound-input-provider`: InputProvider is bound to SessionState rather than BaseAgent, ensuring per-session isolation.

### Modified Capabilities
- `sessionpool-only-execution`: Update to reflect unified TurnRunner and new InputProvider binding behavior.
- `runctx-session-binding`: Update `get_active_run_context()` fallback rules to remove instance-level and background-context fallbacks.

## Impact

- **`orchestrator/core.py`**: `TurnRunner` expanded with strategy dispatch; `LegacyTurnRunner` removed.
- **`orchestrator/legacy_runner.py`**: Deleted.
- **`agents/base_agent.py`**: `_input_provider` field removed or deprecated; `get_active_run_context()` simplified.
- **`agents/context.py`**: `get_input_provider()` reads from session state.
- **`agents/native_agent/approval_bridge.py`**: Constructor receives `InputProvider` directly.
- **`agents/native_agent/agent.py`**: `get_agentlet()` passes per-session provider into capability construction.
- **Tests**: All tests referencing `LegacyTurnRunner` or relying on `_input_provider` on agent updated.
