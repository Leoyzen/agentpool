## Context

AgentPool currently maintains two turn execution implementations with nearly identical logic:

- `TurnRunner` in `orchestrator/core.py` (~480 lines) — used for native agents via `SessionPool`
- `LegacyTurnRunner` in `orchestrator/legacy_runner.py` (~494 lines) — used for non-native agents (ACP, ClaudeCode, AGUI)

Both manage `_post_turn_injections`, `_post_turn_prompts`, per-session injection locks, auto-resume loops, `RunHandle` lifecycle, event queue consumption, and timing tracking. The duplication guarantees drift: any bug fix or behavior change must be applied in both places.

Separately, the Pydantic-AI tool confirmation call chain is excessively indirect:

```
PydanticAI HandleDeferredToolCalls
  → _resolve_deferred_approvals()
    → RunContext[AgentContext].deps
      → AgentContext.get_input_provider()
        → BaseAgent._input_provider
          → InputProvider.get_tool_confirmation()
```

This chain has two critical weaknesses:
1. **Race condition**: `BaseAgent._input_provider` is set on potentially shared agent instances in `SessionController.get_or_create_session_agent()`:
   ```python
   if input_provider is not None:
       base_agent._input_provider = input_provider  # Affects ALL sessions using this agent!
   ```
2. **Opaque fallback**: `get_active_run_context()` has four fallbacks (ContextVar → instance attr → SessionPool → background ctx), making it impossible to predict which run context receives injected prompts.

## Goals / Non-Goals

**Goals:**
- Merge `TurnRunner` and `LegacyTurnRunner` into a single class with strategy-based dispatch
- Bind `InputProvider` to `SessionState` instead of `BaseAgent`, eliminating shared-agent race conditions
- Shorten the Pydantic-AI tool confirmation chain by passing `InputProvider` directly into the approval bridge capability
- Simplify `get_active_run_context()` to at most two levels of fallback
- Preserve all existing behavior for both native and non-native agents

**Non-Goals:**
- Changing Pydantic-AI's internal `HandleDeferredToolCalls` mechanism
- Modifying OpenCode/ACP protocol handler event conversion logic
- Removing `PromptInjectionManager` (still used by non-native agents and hook manager)
- Changing SessionPool's public API (`receive_request()`, `run_stream()`, etc.)

## Decisions

### Decision 1: Merge TurnRunners via strategy dispatch

**Rationale**: The two classes differ only in how they execute a single turn:
- Native: uses `RunExecutor` which drives `agentlet.iter()` with `next()` calls
- Non-native: calls `agent._run_stream_once()` directly and manually drains queued prompts

All other logic (lock management, auto-resume, event queue consumption, RunHandle lifecycle) is identical.

**Implementation**:
- Extract a `TurnExecutionStrategy` protocol/class with two implementations:
  - `NativeExecutionStrategy`: wraps `RunExecutor` logic
  - `NonNativeExecutionStrategy`: wraps the manual `_run_stream_once()` + queue drain loop
- `TurnRunner` holds a strategy registry mapping `AGENT_TYPE` to strategy
- `_run_turn_unlocked()` delegates to `strategy.execute_turn(...)`
- Delete `legacy_runner.py`

**Alternative considered**: Keep both classes but extract shared mixins. Rejected because the shared logic is the majority of the code; the turn execution is the only difference.

### Decision 2: Bind InputProvider to SessionState

**Rationale**: `InputProvider` is inherently per-session (it knows the session ID, manages pending permissions/questions for that session). Binding it to `BaseAgent` creates a category error when the agent is shared.

**Implementation**:
- Add `input_provider: Any | None` to `SessionState` (already exists, make it the *authoritative* source)
- Remove `BaseAgent._input_provider` or mark it deprecated
- Update `AgentContext.get_input_provider()`:
  ```python
  def get_input_provider(self):
      # 1. Session-bound provider (authoritative)
      session = self._get_session_state()
      if session and session.input_provider is not None:
          return session.input_provider
      # 2. Legacy fallback (deprecated)
      return self.input_provider
  ```
- Update `SessionController.get_or_create_session_agent()` to NOT set `base_agent._input_provider`
- Ensure `TurnRunner._run_turn_unlocked()` passes `input_provider` from `SessionState` into `AgentContext` construction

**Alternative considered**: Keep `_input_provider` on agent but use per-session agent instances. Rejected because non-native agents are explicitly shared (ACP, ClaudeCode agents are not recreated per-session).

### Decision 3: Pass InputProvider directly into approval bridge

**Rationale**: The approval bridge currently traverses `AgentContext.get_input_provider()` which adds an unnecessary indirection layer. Since we know the provider at agentlet construction time, we can pass it directly.

**Implementation**:
- Change `create_approval_bridge_capability(agent)` signature to:
  ```python
  def create_approval_bridge_capability(agent: Agent, input_provider: InputProvider | None)
  ```
- In `Agent.get_agentlet()`, pass `input_provider` from `SessionState` (or kwargs) into the bridge
- The bridge handler uses the passed provider directly instead of `ctx.deps.get_input_provider()`

**Alternative considered**: Keep indirection for flexibility. Rejected because `AgentContext` already has all other context; `input_provider` is the only field that benefits from being on `SessionState` instead.

### Decision 4: Simplify get_active_run_context() to two levels

**Rationale**: Four fallbacks create unpredictability. The correct source depends on execution mode:
- SessionPool mode: `SessionPool.get_run()` via `session.current_run_id`
- Standalone mode: `_current_run_ctx_var` (ContextVar)

**Implementation**:
- If `self.agent_pool` and `self.agent_pool.session_pool` exist:
  - Use `SessionPool` lookup exclusively
- Else:
  - Use `_current_run_ctx_var` exclusively
- Remove `_active_run_ctx` and `_background_run_ctx` fallbacks
- Update `run_stream()` to not set `_active_run_ctx`
- Update `run_in_background()` to use ContextVar or a dedicated background context

**Breaking change**: Code relying on `_active_run_ctx` or `_background_run_ctx` instance attributes will need to use `get_active_run_context()` or ContextVar directly.

### Decision 5: Non-native agents retain manual queue system

**Rationale**: Non-native agents (ACP, ClaudeCode, AGUI) do not use PydanticAI's agent loop. They cannot benefit from `PendingMessageDrainCapability`. Their turn continuation must remain manual.

**Implementation**:
- `NonNativeExecutionStrategy` continues to use `PromptInjectionManager` and the manual `while has_queued()` loop
- `NativeExecutionStrategy` delegates queue handling to PydanticAI's capability
- The unified `TurnRunner` remains agnostic to queue mechanics; each strategy handles its own

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Shared-agent race condition persists during migration | SessionState.input_provider takes precedence immediately; legacy `_input_provider` is fallback only |
| Non-native agent behavior changes subtly | Thorough test coverage for ACP and ClaudeCode agent streaming; both strategies are thin wrappers around existing logic |
| `get_active_run_context()` callers broken | Audit all callers in codebase; only two fallbacks makes behavior predictable |
| Standalone agent mode regresses | Standalone path uses ContextVar exclusively; test standalone `agent.run_stream()` |
| Approval bridge signature change | Update all call sites in `Agent.get_agentlet()` and tests |
| Background task context lost | Background runs use dedicated `AgentRunContext` stored on the task, accessible via `asyncio.current_task()` context |

## Migration Plan

1. **Phase 1**: Create `TurnExecutionStrategy` protocol and `NativeExecutionStrategy`
2. **Phase 2**: Move `LegacyTurnRunner` logic into `NonNativeExecutionStrategy`
3. **Phase 3**: Update `TurnRunner` to use strategy dispatch, delete `LegacyTurnRunner`
4. **Phase 4**: Make `SessionState.input_provider` authoritative; update `AgentContext.get_input_provider()`
5. **Phase 5**: Update approval bridge to receive provider directly
6. **Phase 6**: Simplify `get_active_run_context()` fallbacks
7. **Phase 7**: Run full test suite (native, ACP, ClaudeCode paths)
8. **Phase 8**: Update OpenCode handler to use SessionPool path (if still on legacy)

## Open Questions

- Should `_active_run_ctx` be fully removed or kept as a deprecated property for backward compatibility?
- How should `Agent.run_in_background()` store its context without `_background_run_ctx`?
