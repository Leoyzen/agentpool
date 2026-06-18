## Context

AgentPool currently uses `PromptInjectionManager` as a dual-purpose mechanism:

1. **Tool result augmentation** (`inject()`/`consume()`): Mid-turn messages are injected into tool results, wrapped in `<injected-context>` XML. Used by `NativeAgentHookManager.after_tool_execute`. This is used by ALL agent types.

2. **Follow-up prompt queue** (`queue()`/`pop_queued()`/`flush_pending_to_queue()`): Messages queued after a turn are processed by a manual `while` loop in `_run_turn_unlocked()`. This is the follow-up mechanism.

Pydantic-ai already has `PendingMessageDrainCapability`, which is auto-injected outermost and provides `asap` (drain before next model request) and `when_idle` (drain when agent would otherwise end) priorities via `before_model_request` and `after_node_run` hooks. This capability is already used by `RunExecutor` (which correctly calls `agent_run.next(node)` to trigger `after_node_run`), but the legacy `_run_turn_unlocked()` still runs its own manual follow-up loop, creating a dual-system problem.

Additionally, the `PendingMessageDrainCapability` cannot be used for non-native agents (ACP), which communicate via JSON-RPC subprocess and don't use pydantic-ai's agent graph. Those agents need the manual queue system.

This design proposes: for native agents, remove the manual follow-up loop and expose `steer()`/`followup()` that maps to `enqueue(priority='asap'/'when_idle')`; for non-native agents, keep the existing `PromptInjectionManager` + manual loop as the sole mechanism.

## Goals / Non-Goals

**Goals:**
- Expose `steer()` and `followup()` on `TurnRunner` that directly maps to pydantic-ai's `enqueue(priority='asap'/'when_idle')` for native agents
- Remove the manual follow-up prompt loop from `_run_turn_unlocked()` for native agents — `PendingMessageDrainCapability.after_node_run()` handles it via graph-node redirection
- Keep `PromptInjectionManager.inject()`/`consume()` for tool result augmentation on ALL agents (this is separate from enqueue semantics)
- Deprecate `inject_prompt()`/`queue_prompt()` in favor of `steer()`/`followup()` for native agents
- Expose `steer`/`followup` at `SessionController` level via `receive_request(session_id, content, priority="steer"|"followup")`
- Non-native agents (ACP) retain the full `PromptInjectionManager` + manual follow-up loop unchanged

**Non-Goals:**
- Changing the non-native agent (ACP) execution path — ACP agents keep the existing `_run_stream_direct()` manual loop
- Replacing `PromptInjectionManager.inject()`/`consume()` for tool result augmentation — this is a different semantic (modifies tool results, not conversation messages) and is kept for all agents
- Changing `RunExecutor` — it already uses `next()` correctly and works with `PendingMessageDrainCapability`
- Adding a new `agentpool/agents/steer_followup.py` file — the logic lives in `TurnRunner` and `SessionController`

## Decisions

### Decision 1: `steer()` = `enqueue(priority='asap')`, `followup()` = `enqueue(priority='when_idle')`

The steer/followup split maps directly to pydantic-ai's existing priority system:

| Concept | pydantic-ai priority | Effect |
|---------|---------------------|--------|
| `steer()` | `'asap'` | Drained at `before_model_request` — injected into next LLM call |
| `followup()` | `'when_idle'` | Drained at `after_node_run` — only when agent would otherwise end |

**Rationale**: No new capability needed. `PendingMessageDrainCapability` already implements exactly this split. The comment in pydantic-ai's code explicitly says: "keeps the priority split visible (matches pi-mono's separate steering / follow-up turns)".

**Alternative considered**: Adding a new capability class in agentpool that wraps the steer/followup logic. Rejected because it would duplicate what `PendingMessageDrainCapability` already does perfectly.

### Decision 2: Agent type detection uses `agent.AGENT_TYPE`, not session metadata

`_run_turn_unlocked()` currently reads `session.metadata.get("agent_type", "unknown")` to determine agent type. However, only ACP sessions set `agent_type="acp"` in metadata; native sessions created via `SessionPool.create_session()` do NOT set `agent_type` — it defaults to `"unknown"`.

All agent classes have a `ClassVar AGENT_TYPE` (e.g., `Agent.AGENT_TYPE == "native"`, `ACPAgent.AGENT_TYPE == "acp"`). Since the agent instance is already resolved by the time `_run_turn_unlocked()` gates the manual loop, the implementation SHALL read `agent.AGENT_TYPE` directly instead of `session.metadata`.

In `SessionController.receive_request()` (where the agent may not be resolved yet), the session metadata check is used as a fallback, but all code paths that reach agent-type-specific routing inside `_run_turn_unlocked()` and `_create_run()` SHALL use `agent.AGENT_TYPE`.

**Rationale**: More reliable — eliminates metadata synchronization errors. `agent.AGENT_TYPE` is always correct because it's a class-level constant.

### Decision 3: `_active_agent_run` exposed via RunHandle for TurnRunner access

The current `_run_turn_unlocked()` has:
```python
run_ctx.injection_manager.flush_pending_to_queue()
while run_ctx.injection_manager.has_queued():
    current_prompts = run_ctx.injection_manager.pop_queued()
    async for event in agent._run_stream_once(run_ctx, *current_prompts):
        ...
    run_ctx.injection_manager.flush_pending_to_queue()
```

For native agents, this loop is redundant: `PendingMessageDrainCapability.after_node_run()` already detects `End` → checks queue → returns `ModelRequestNode` to redirect execution. Removing it eliminates the dual-write pattern (both systems trying to handle follow-up).

For non-native agents, the loop stays because there's no pydantic-ai graph to redirect.

**Alternative considered**: Keeping the loop and relying on `PendingMessageDrainCapability` to handle first-level follow-up while the loop catches edge cases. Rejected because it creates race conditions (both systems process the same message).

### Decision 4: `_active_agent_run` exposed via RunHandle for TurnRunner access

`steer()`/`followup()` need access to the PydanticAI `AgentRun` to call `enqueue()`. However, the `AgentRun` is created deep inside `_run_agentlet_core()` / `RunExecutor.execute()` — not directly accessible from `TurnRunner`.

The implementation SHALL expose the `AgentRun` through `RunHandle`, which is the shared lifecycle object between `TurnRunner` and `RunExecutor`:

- `RunHandle` SHALL gain an `active_agent_run: AgentRun | None` field
- `RunExecutor` SHALL set `run_handle.active_agent_run = agent_run` when the native run starts (at `agentlet.iter()` context manager entry)
- `TurnRunner` SHALL read `run_handle.active_agent_run` in `steer()`/`followup()` to call `enqueue()`
- `RunHandle.active_agent_run` SHALL be cleared in the `finally` block when the run completes

This avoids threading `agent_run` through `_run_turn_unlocked()` or using a `ContextVar`, both of which would add complexity. The `RunHandle` is already the canonical reference for active runs.

**Alternative considered**: Using a `ContextVar` set by native agent code. Rejected because `TurnRunner` and `RunExecutor` may run in different asyncio tasks, making `ContextVar` unreliable.

### Decision 5: `PromptInjectionManager.inject()`/`consume()` kept for all agents

`inject()`/`consume()` modifies tool results (wrapping in `<injected-context>` XML), while `enqueue()` inserts conversation messages. These are semantically different:

- `inject()`: "Take this message and attach it to the next tool result as augmented context"
- `enqueue()`: "Take this message and insert it into the conversation as a new user message"

Both are useful. Tool augmentation (e.g., background task noticing) should stay as `inject()`. New user messages or explicit steer instructions should use `enqueue()`.

**Alternative considered**: Replacing `inject()` with `enqueue(priority='asap')` everywhere. Rejected because tool augmentation and conversation injection have different model-facing behavior. A tool-augmented message appears as part of a tool result; an enqueued message appears as a new user turn.

### Decision 6: `SessionController.receive_request()` gains `priority="steer"|"followup"` aliases

Currently `receive_request()` accepts `priority="asap"|"when_idle"`. External callers shouldn't need to know pydantic-ai's internal priority names. The new aliases:

```python
async def receive_request(self, session_id, content, priority="followup"):
    # "steer" → "asap"
    # "followup" → "when_idle"
    # existing "asap"/"when_idle" still accepted for backward compat
```

**Rationale**: Protocol handlers (ACP, AG-UI, OpenCode) and xeno-agent's `BackgroundTaskProvider` should call `steer` or `followup` by semantic intent, not by knowing pydantic-ai internals.

### Decision 7: Non-native agents keep `_post_turn_injections` / `_post_turn_prompts`

The `_post_turn_injections` and `_post_turn_prompts` dicts on `TurnRunner` (keyed by `session_id`) are the external-caller-facing API for non-native agents. They remain because:
1. ACP agents don't have an `AgentRun` to call `enqueue()` on
2. The `_trigger_auto_resume()` / `_process_queued_work()` loop is the only way to continue an ACP agent session
3. ACP agents still use `PromptInjectionManager` for both tool augmentation AND follow-up

For native agents, `steer()`/`followup()` bypass these queues entirely and call `agent_run.enqueue()` directly.

## Risks / Trade-offs

- **[CRITICAL] `_run_agentlet_core()` bare `async for` prevents `when_idle` drain** → This path (used by standalone streaming) uses `async for node in agent_run:` which calls `__anext__`, not `next()`. `__anext__` does NOT fire `after_node_run` capability hooks, so `PendingMessageDrainCapability.when_idle` drain never activates. **Mitigation**: Replace `async for node in agent_run:` with an explicit `while True: node = await agent_run.next(node)` loop, mirroring `RunExecutor.execute()`. This fix is REQUIRED for `followup()` to work at all on native agents.

- **[Risk] `_run_turn_unlocked()` does NOT use `RunExecutor`** → The session-pool path through `_run_turn_unlocked()` calls `agent._run_stream_once()` which goes through `_stream_events()` → `_run_agentlet_core()`. It does NOT use `RunExecutor`. **Mitigation**: Fix `_run_agentlet_core()` `async for` (fixes all paths at once). This is simpler than switching to `RunExecutor` because `RunExecutor` doesn't handle the prompt conversion, history resolution, pre-run hooks, and event dispatch that `_run_stream_once()` does.

- **[CRITICAL] `steer()`/`followup()` need `AgentRun` reference but can't access it** → The `AgentRun` is created inside `_run_agentlet_core()` / `RunExecutor.execute()`, scoped to the `async with agentlet.iter(...)` block. `TurnRunner` has no way to call `enqueue()` without this reference. **Mitigation**: Expose `AgentRun` through `RunHandle.active_agent_run`. `RunExecutor` sets it when the run starts; `TurnRunner` reads it for `steer()`/`followup()`.

- **[Risk] Agent type detection via `session.metadata` is unreliable** → `session.metadata.get("agent_type", "unknown")` returns `"unknown"` for all native agents (only ACP sessions set `agent_type`). Without fix, native agents retain the manual follow-up loop and the simplification fails silently. **Mitigation**: Use `agent.AGENT_TYPE` (ClassVar) instead of `session.metadata` for gating in `_run_turn_unlocked()`. For `_create_run()` and `receive_request()` where the agent is not yet resolved, accept the `"unknown"` default — the actual gating happens later in `_run_turn_unlocked()` where `agent.AGENT_TYPE` is available.

- **[Risk] `steer()`/`followup()` timing race at run completion boundary** → If `steer()`/`followup()` is called when `_run_turn_unlocked()` has exited `_run_stream_once()` but hasn't started the next turn yet, `run_handle.active_agent_run` is `None`. **Mitigation**: Both `steer()` and `followup()` SHALL check if `run_handle.active_agent_run` is `None` and, if so, delegate to `receive_request(session_id, content, priority="steer"/"followup")`. They SHALL NOT fall back to `_post_turn_prompts` for native agents, because `_process_queued_work()` is gated behind non-native.

- **[Risk] `flush_pending_to_queue()` creates zombie data for native agents** → After removing the manual follow-up loop, unconsumed injections that get flushed to `_queued_prompts` have no consumer. **Mitigation**: `flush_pending_to_queue()` SHALL also be gated behind `agent.AGENT_TYPE != "native"` in `_run_turn_unlocked()`. For native agents, unconsumed injections are intentionally dropped (they should have been consumed by `after_tool_execute` hooks — if not, it's a bug in the tool/hook chain).

- **[Risk] Dual-write between `injection_manager.inject()` and `agent_run.enqueue()` during migration** → If both paths are active simultaneously, messages could be processed twice. **Mitigation**: The removal of the manual follow-up loop for native agents is an atomic change — the loop is removed, and all enqueue goes through `steer()`/`followup()` → `agent_run.enqueue()`.

- **[Risk] Non-native agents accidentally receive `steer()`/`followup()` calls that expect pydantic-ai behavior** → `TurnRunner.steer()` must check `agent_type` and fall back to `inject_prompt()` for non-native agents. **Mitigation**: `steer()` internally routes: native → `agent_run.enqueue(priority='asap')`, non-native → `injection_manager.inject()`.

- **[Risk] Backward compatibility**: existing callers of `inject_prompt()`/`queue_prompt()` still work. **Mitigation**: These methods are deprecated with a `DeprecationWarning` but not removed in this change. They delegate to `steer()`/`followup()` internally.

- **[Trade-off] Steer/followup API is on `TurnRunner` not on `AgentRun` directly** → `TurnRunner` owns the session lifecycle and knows whether the agent is native or non-native. Direct `AgentRun.enqueue()` would require callers to know the agent type. **Rationale**: `TurnRunner` is the right abstraction boundary.

## Migration Plan

1. **Add `RunHandle.active_agent_run` field**: Add `active_agent_run: AgentRun | None = None` to `RunHandle`. `RunExecutor` SHALL set it when the native `agent_run` starts (at `agentlet.iter()` entry) and clear it in the `finally` block.

2. **Fix `_run_agentlet_core()` bare `async for`**: Replace `async for node in agent_run:` with explicit `while True: node = await agent_run.next(node)` loop. This is REQUIRED for `PendingMessageDrainCapability.when_idle` drain to work. Mirror the pattern from `RunExecutor.execute()`.

3. **Add `steer()`/`followup()` to `TurnRunner`**: New methods that:
   - Check agent type via `session.AGENT_TYPE` (from resolved agent) or `agent.AGENT_TYPE`
   - Native: `run_handle.active_agent_run.enqueue(message, priority='asap'/'when_idle')`
   - Non-native: `injection_manager.inject()`/`injection_manager.queue()`
   - Idle native: fall back to `receive_request(priority="steer"/"followup")` instead of dropping

4. **Change agent type detection to use `agent.AGENT_TYPE`**: In `_run_turn_unlocked()` and `_create_run()`, read `agent.AGENT_TYPE` instead of `session.metadata.get("agent_type", "unknown")`.

5. **Gate manual follow-up loop and `flush_pending_to_queue()`**: In `_run_turn_unlocked()`, gate both `flush_pending_to_queue()` and the `while has_queued()` loop behind `agent.AGENT_TYPE != "native"`.

6. **Fix `_run_agentlet_core()` `async for → next()`**: Fix `_run_agentlet_core()`'s bare `async for` → `next()` loop. Keep `_run_turn_unlocked()` calling `_run_stream_once()` for all agents — no need to switch to `RunExecutor` since `_run_stream_once()` handles prompt conversion, history resolution, pre-run hooks, and event dispatch that `RunExecutor` doesn't.

7. **Add `priority="steer"|"followup"` aliases to `SessionController.receive_request()`**: Map to `"asap"`/`"when_idle"` internally. Route active native runs to `steer()`/`followup()` instead of `inject_prompt()`/`queue_prompt()`.

8. **Deprecate `inject_prompt()`/`queue_prompt()`**: Add `DeprecationWarning` for native agents, delegate to `steer()`/`followup()`. Remove dual-write to `injection_manager`.

9. **Update external callers**: `BackgroundTaskProvider`, ACP handlers → use `steer()`/`followup()`.

**Rollback**: If issues arise, revert `_run_turn_unlocked()` and `_run_agentlet_core()` to their previous state. Keep the `steer()`/`followup()` API (it's additive and backward-compatible). The `PromptInjectionManager` is never removed, only bypassed for native agents.