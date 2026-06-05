## Context

AgentPool's session orchestration currently lives in `src/agentpool/orchestrator/core.py`, which combines `SessionController` (session CRUD) and `TurnRunner` (turn execution + manual queue management). `TurnRunner` manages:
- `_post_turn_injections`: steering messages queued after a turn ends
- `_post_turn_prompts`: follow-up prompts queued after a turn ends
- `_injection_locks`: per-session locks for safe dict mutation
- `inject_prompt()` / `queue_prompt()`: methods that check `session.active_run_ctx` to decide whether to inject into an active turn or queue for later
- `_process_queued_work()` / `_trigger_auto_resume()`: auto-resume loop that drains queues and runs new turns

This system was necessary because PydanticAI previously had no native mid-run message queueing. In v1.101.0+, PydanticAI added:
- `ctx.enqueue(*content, priority='asap' | 'when_idle')` for tools/hooks
- `AgentRun.enqueue(*content, priority=...)` for external drivers
- `PendingMessageDrainCapability` auto-injected at `before_model_request` and `after_node_run`

`'asap'` maps to AgentPool's "inject into active turn"; `'when_idle'` maps to "queue for next turn". PydanticAI's capability handles the exact timing AgentPool currently encodes manually for **follow-up prompts only**.

**Important**: PydanticAI's `enqueue()` only replaces the **`queue()`/`pop_queued()`** part of `PromptInjectionManager` (follow-up prompts after a turn ends). It does NOT replace the **`inject()`/`consume()`** part, which tools use to augment their own results via `after_tool_execute`. That mechanism must be preserved.

**Critical scoping note**: PydanticAI's pending message queue is **only available to native agents** (those using PydanticAI's `Agent` class). Non-native agents (ACP, ClaudeCode, AGUI) use their own streaming implementations (subprocess JSON-RPC, Claude SDK, HTTP/SSE respectively) and do not expose PydanticAI's `Agent.iter()` API. Therefore, this change **scopes the PydanticAI queue adoption to native agents only**; non-native agents continue using the existing manual queue system via a compatibility layer.

## Goals / Non-Goals

**Goals:**
- Add pool-level run tracking via `SessionPool._runs: dict[str, RunHandle]` for all active agent runs (native and non-native)
- Eliminate the fragile `SessionState.active_run_ctx` pointer; use per-session `asyncio.Lock` (`_request_lock`) for atomic check-and-create, and `current_run_id` for run-state checking
- Eliminate `TurnRunner`'s manual **follow-up prompt queue** (`_post_turn_prompts`, `_process_queued_work`, `_trigger_auto_resume`) **for native agents** and delegate to PydanticAI's `PendingMessageDrainCapability`
- Preserve `PromptInjectionManager.inject()`/`consume()` for **tool result augmentation** across all agents (this is NOT replaced by PydanticAI)
- Refactor `SessionController` into the unified request entry point (receive → route → create run or enqueue), with agent-type-aware routing
- Introduce `RunHandle` as a first-class ephemeral object to track execution state, with agent-type-specific run references
- Keep `SessionState.turn_lock` for **non-native agents** (they still need turn serialization)

**Non-Goals:**
- Changing non-native agents' queueing/injection behavior (they keep existing manual system)
- Changing `PromptInjectionManager.inject()`/`consume()` behavior (tool result augmentation is preserved)
- Historical run tracking / persistence (runs are ephemeral, only current run is tracked in `SessionPool._runs`; historical runs may be added later)
- Changes to `EventBus` API shape (event types and subscriber interface remain the same)
- Changes to `SessionData` persistence schema (session metadata is unchanged)

## Decisions

### Decision: Scope PydanticAI queue adoption to native agents only
**Rationale**: PydanticAI's `PendingMessageDrainCapability`, `ctx.enqueue()`, and `AgentRun.enqueue()` are APIs provided by PydanticAI's `Agent` class. Non-native agents (ACP, ClaudeCode, AGUI) do not use PydanticAI's agent loop at all — they have their own `_stream_events()` implementations that communicate with external systems. Attempting to apply PydanticAI's queue to non-native agents would require reimplementing their entire streaming architecture, which is infeasible. The pragmatic approach is to let native agents use PydanticAI's native queue while non-native agents continue with the existing manual queue system.

**Alternatives considered**:
- Reimplement non-native agents to use PydanticAI. Rejected: infeasible; ACP uses subprocess JSON-RPC, Claude uses Claude SDK, AGUI uses HTTP/SSE.
- Remove manual queues for all agents and leave non-native agents without queueing. Rejected: would break mid-run injection and follow-up queueing for non-native agents, which is critical functionality.
- Implement an agent-type-agnostic abstraction layer. Rejected: adds complexity; the manual queue system already works for non-native agents.

### Decision: Keep manual queue system as `LegacyTurnRunner` for non-native agents
**Rationale**: The existing `TurnRunner` queue logic (`_post_turn_injections`, `_post_turn_prompts`, `_injection_locks`) works correctly for non-native agents. Rather than deleting it entirely and breaking non-native agents, extract it into a `LegacyTurnRunner` compatibility class that `SessionController` delegates to when the session's agent is non-native.

**Alternatives considered**:
- Delete all queue logic and reimplement for non-native agents. Rejected: wasteful; existing code works.
- Keep `TurnRunner` as-is and only add native-agent path. Rejected: confusing; `TurnRunner` would have dual responsibilities.

### Decision: Per-session `_request_lock` instead of global lock
**Rationale**: A single global `SessionController._request_lock` would serialize `receive_request()` across ALL sessions simultaneously, creating a bottleneck. Each `SessionState` should have its own `_request_lock` that guards only that session's check-and-create sequence. This provides the same mutual exclusion without cross-session contention.

**Alternatives considered**:
- Global `SessionController` lock. Rejected: serializes all sessions; unacceptable for high-concurrency scenarios.
- No lock, rely on `current_run_id` alone. Rejected: TOCTOU race where two concurrent calls both see `None` and create duplicate runs.

### Decision: Remove TurnRunner follow-up prompt queue for native agents only
**Rationale**: For native agents, PydanticAI's `PendingMessageDrainCapability` handles follow-up prompt queuing (`'when_idle'` messages at `after_node_run`). This replaces `_post_turn_prompts`, `_process_queued_work()`, and `_trigger_auto_resume()`. However, `PromptInjectionManager.inject()`/`consume()` (tool result augmentation via `after_tool_execute`) is NOT replaced by PydanticAI's queue — it serves a different purpose (modifying tool results, not adding conversation messages). Keep `inject()`/`consume()` for native agents.

**Alternatives considered**:
- Remove all PromptInjectionManager functionality for native agents. Rejected: would break tool result augmentation.
- Implement a hybrid where AgentPool queues and PydanticAI queues coexist. Rejected: double bookkeeping, race conditions.

### Decision: Keep `SessionState.turn_lock` for non-native agents; remove for native agents
**Rationale**: Native agents no longer need `turn_lock` because PydanticAI's `AgentRun` handles serialization internally and `current_run_id` + `_request_lock` prevent duplicate run creation. Non-native agents still need `turn_lock` for turn serialization in `LegacyTurnRunner`. Removing it for all agents would break non-native concurrency guarantees.

**Alternatives considered**:
- Remove `turn_lock` for all agents. Rejected: breaks non-native agent turn serialization.
- Move turn lock into `LegacyTurnRunner`. Rejected: `SessionState` already holds session-level locks; moving it creates indirection without benefit.

### Decision: Switch native agents from `_run_stream_once()` to `agent.iter()` + `next()`
**Rationale**: `PendingMessageDrainCapability` with `'when_idle'` priority only drains at `after_node_run`, which is a capability hook invoked by `_run_node_with_hooks` (used by `AgentRun.next()` and `Agent.run()`). The bare `async for node in agent_run:` path uses `__anext__` which calls the graph runner directly without firing capability hooks. Therefore, `when_idle` messages would never be drained in bare iteration mode. The `asap` drain still works because it fires in `before_model_request` which runs inside `ModelRequestNode.run()` regardless of driving mode.

**Alternatives considered**:
- Keep `_run_stream_once()` and implement manual `when_idle` drain. Rejected: defeats the purpose of migrating to PydanticAI's native capability.
- Use `agent.run()` instead of `agent.iter()`. Rejected: `agent.run()` blocks until completion; AgentPool needs streaming event production via EventBus.

### Decision: `RunHandle` managed by `SessionPool._runs` — pool-level run tracking
**Rationale**: `RunHandle` encapsulates the runtime state of a single execution: `run_id`, `status` (pending|running|completed|failed), `run_ctx` (AgentRunContext), `session_id`, `agent_type`, and an agent-type-specific run reference (PydanticAI `AgentRun` for native, compatibility queue for non-native). It lives only during execution and is destroyed afterward. `SessionPool._runs` is a simple `dict[str, RunHandle]` that provides unified visibility: iterate for `active_runs`, `get_run(run_id)`, `cancel_run(run_id)`. This directly addresses the original question of whether AgentPool should have unified run tracking at the orchestration layer, similar to xeno-agent's `BackgroundTaskManager`.

**Why not a separate `RunRegistry` class?**: A separate registry class adds a new lifecycle to manage (who creates it? who destroys it?) and an extra layer of delegation (`AgentPool` → `RunRegistry` → `dict`). `SessionPool` already owns session lifecycle; adding `_runs` to it is a natural extension with zero new abstractions. The dict operations are atomic in CPython; no complex locking needed.

**Alternatives considered**:
- Separate `RunRegistry` class. Rejected: adds unnecessary abstraction; `SessionPool._runs` provides the same capability with less code.
- Session-scoped only (no pool dict). Rejected: doesn't provide unified view or cross-session operations; the original question explicitly asks for pool-level tracking.
- Merge `RunHandle` into `SessionState`. Rejected: `RunHandle` holds asyncio primitives (`AgentRun` reference, `Task`) that shouldn't be serialized with session metadata.
- Historical run persistence in `SessionPool._runs`. Rejected: out of scope; dict tracks only active runs to avoid memory leaks.

### Decision: SessionController becomes the unified request router
**Rationale**: All requests (initial prompt, inject, follow-up) currently flow through `TurnRunner` methods. After migration, `SessionController` should be the single entry point that decides: (a) create new `RunHandle` if session is idle, or (b) call `pydantic_ai_run.enqueue(...)` with the appropriate priority if a run is active (for native agents), or (c) delegate to `LegacyTurnRunner` for non-native agents.

**Alternatives considered**:
- Keep `TurnRunner` as the entry point but delegate queueing to PydanticAI. Rejected: `TurnRunner` becomes a thin wrapper; collapsing it into `SessionController` reduces indirection.

### Decision: `receive_request()` is fire-and-forget; EventBus remains the event consumption path
**Rationale**: Protocol handlers currently subscribe to EventBus and call `process_prompt()`. Changing this to a returned stream handle would require restructuring all protocol handlers. Keeping EventBus as the consumption path minimizes disruption. `receive_request()` returns `None`; handlers continue subscribing to EventBus before calling it.

**Alternatives considered**:
- Return an async iterator from `receive_request()`. Rejected: major protocol handler refactor.
- Return a future/awaitable from `receive_request()`. Rejected: doesn't match streaming event model.

### Decision: `close_session()` awaits `RunHandle` completion event instead of `turn_lock`
**Rationale**: The current `close_session()` acquires `session.turn_lock` with a 30-second timeout to gracefully wait for the active turn before exiting the agent context. For native agents, removing `turn_lock` eliminates this graceful wait mechanism. `RunHandle` exposes an `asyncio.Event` (`complete_event`) that is set when the run finishes. `close_session()` awaits this event with a timeout, preserving graceful shutdown semantics. If the timeout expires, it falls back to `cancel_run()`.

**Race condition mitigation**: `complete_event` must be set AFTER the run task's `finally` block completes cleanup (e.g., unsetting `current_run_id`, removing from `SessionPool._runs`) and AFTER releasing `_request_lock`. Otherwise `close_session()` may proceed to call `agent.__aexit__()` while the run task is still cleaning up.

**Close guard**: `close_session()` must set `SessionState.closing = True` before waiting, and `receive_request()` must reject new requests when `closing = True` to prevent a new run from starting during graceful shutdown.

**Alternatives considered**:
- Cancel the run immediately without waiting. Rejected: behavioral regression; long-running tool operations could leave external resources in an inconsistent state.
- Poll `current_run_id` with `asyncio.sleep()`. Rejected: inefficient; `asyncio.Event` is the idiomatic solution.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| `SessionPool._runs` dict operations under high concurrency | Dict ops are atomic in CPython; no additional locking needed for simple get/set/remove |
| Memory leak: `SessionPool._runs` retains completed runs if cleanup fails | Cleanup callback removes from dict on run completion; add `max_history` limit if historical tracking added later |
| No limit on concurrent runs | Add optional `max_concurrent_runs` to `SessionPool`; reject or queue when exceeded |
| PydanticAI `enqueue()` from Temporal activities drops silently (known limitation) | Document limitation; for Temporal workflows, enqueue from the workflow context (not tool activities) |
| `SystemPromptPart` mid-run behavior differs across providers (Anthropic/Google hoist to top) | Avoid `SystemPromptPart` in `enqueue()`; use `UserPromptPart` or wrap in `ModelRequest` passthrough per PydanticAI docs |
| `when_idle` drain only fires with `AgentRun.next()`, not bare `async for` | Native agents MUST drive iteration via `agent_run.next()` in a loop. The bare `async for` is not an option. |
| Breaking change to protocol handlers that call `TurnRunner.inject_prompt()` / `queue_prompt()` | Update native-agent call sites in `acp_server`, `opencode_server`, `mcp_server` to use `SessionController.receive_request()`. Non-native call sites unchanged. |
| PydanticAI v1.101.0+ may have undiscovered bugs in `PendingMessageDrainCapability` | Pin to exact version, add integration tests for enqueue/drain scenarios, monitor upstream issues |
| Event streaming model changes from `_run_stream_once()` tokens to `agent.iter()` node events | Prototype event mapping (task 1.3) before implementing `RunExecutor`; ensure identical event stream |
| MetricsCollector depends on `turn_lock.locked()` | Update to use `SessionPool.active_runs` before removing `turn_lock` for native agents |
| `BaseAgent._run_stream_once()` internal prompt loop conflicts with PydanticAI queue | Remove the internal loop for native agents; PydanticAI's `PendingMessageDrainCapability` handles continuation |
| Non-native agents continue using manual queues, creating two queue systems | Accept as necessary complexity; non-native agents cannot use PydanticAI's queue. Document the split clearly. |
| `close_session()` race: `complete_event` set before run task cleanup finishes | Ensure `complete_event` is set in the run task's `finally` block AFTER all cleanup |
| `PromptInjectionManager` conflated: `inject()`/`consume()` is tool result augmentation, not queuing | Preserve `inject()`/`consume()` for all agents; only replace `queue()`/`pop_queued()` for native agents |

## Event Mapping (Native Agents)

**Note**: The current native agent code calls `node.stream(agent_run.ctx)` which yields fine-grained PydanticAI streaming events. The `RunExecutor` must replicate this exact event stream to avoid breaking protocol handlers. The table below maps node-level events to AgentPool EventBus events, but the actual implementation must use `node.stream()` or equivalent to preserve event granularity.

| PydanticAI Node Event | AgentPool EventBus Event | When Emitted |
|---|---|---|
| `AgentRun` created (before first `next()`) | `RunStartedEvent` | Once per run |
| `ModelRequestNode` (first only) | `RunStartedEvent` | Once, at first model request |
| `ModelRequestNode` (subsequent, from `when_idle` drain) | — | Silent; no event needed — but see open question below |
| `ModelResponseNode` start | `PartStartEvent` | When model begins responding |
| `ModelResponseNode` text chunks | `PartDeltaEvent` | For each text delta |
| `ModelResponseNode` end | `PartEndEvent` | When model response completes |
| `FunctionToolNode` start | `ToolCallStartEvent` | When tool execution begins |
| `FunctionToolNode` end | `ToolCallCompleteEvent` | When tool execution completes |
| `EndNode` | `StreamCompleteEvent` | When agent run terminates normally |
| Run cancelled | `StreamCompleteEvent(cancelled=True)` | On cancellation — adds `cancelled: bool = False` to `StreamCompleteEvent` |

**Open question**: When a `when_idle` message causes a subsequent `ModelRequestNode`, protocol handlers may need to reset state. Currently, `RunStartedEvent` is used for this. Should subsequent `ModelRequestNode`s from `when_idle` drains emit any event? Options:
- Emit `RunStartedEvent` again (but handlers might reset state incorrectly)
- Emit a new `TurnContinuedEvent`
- Emit nothing (handlers continue with existing state)
**Recommendation**: Start with "emit nothing" (silent). If handlers break, add `TurnContinuedEvent` later.

## Migration Plan (Two-Phase)

### Phase 1: Run Tracking Foundation (Lower Risk)
1. Add `RunHandle` class in `orchestrator/run.py`
2. Add `SessionPool._runs: dict[str, RunHandle]` for pool-level tracking
3. Refactor `SessionState` to hold `current_run_id` and `_request_lock`
4. Refactor `SessionController` with agent-type-aware `receive_request()` that creates `RunHandle` and adds to `SessionPool._runs`
5. Update `close_session()` to await `RunHandle.complete_event` with timeout
6. Make `AgentRunContext.injection_manager` optional (`PromptInjectionManager | None`)
7. Update `MetricsCollector` to use `SessionPool.active_runs`
8. Keep existing manual queues for ALL agents during Phase 1
9. Add tests for `SessionPool._runs`, `RunHandle`, pool-level cancellation, enumeration

### Phase 2: Native Agent PydanticAI Queue (Higher Risk)
1. Prototype `agent.iter()` + `next()` event mapping in a standalone script (BLOCKS Phase 2 until passing)
2. Add `RunExecutor` for native agents: drives `agent.iter()` + `next()` loop with event mapping
3. Extract non-native queue logic from current `TurnRunner` into `LegacyTurnRunner`
4. Remove `TurnRunner._post_turn_prompts`, `_process_queued_work()`, `_trigger_auto_resume()` for native agents only
5. Update `BaseAgent._run_stream_once()` to remove internal prompt continuation loop for native agents
6. Update protocol handlers (native-agent paths only)
7. Replace red-flag auto-resume tests with PydanticAI-native equivalents for native agents
8. Update documentation

**Rollback**: Phase 1 is safe to keep (adds tracking without changing execution). If Phase 2 has issues, revert `RunExecutor` to use manual queues while keeping `SessionPool._runs`.

## Error Propagation

**Current behavior**: `SessionPool.process_prompt()` blocks until turn completion. Exceptions propagate to the caller (protocol handler).

**Proposed behavior**: `receive_request()` is fire-and-forget, returns `None`.

**Question**: How do run failures reach callers?

**Resolution**: 
- Run failures are published as `RunFailedEvent` on the EventBus with `run_id`, `session_id`, and `exception` details
- Protocol handlers already subscribe to EventBus; they can handle `RunFailedEvent`
- `RunHandle.fail()` sets status to `failed`, sets `complete_event`, and publishes `RunFailedEvent`
- Callers that need synchronous error handling can await `RunHandle.complete_event` and check `RunHandle.status == 'failed'`
- For standalone mode (no SessionPool), exceptions continue to propagate as before

## Open Questions (Resolved)

- **Q**: Should `receive_request()` support a "wait" strategy?
  - **A**: No. Start with "enqueue" only. If a run is active, messages are enqueued with `priority='when_idle'`. Callers do not block.
- **Q**: Does `RunHandle` need an explicit `cancel()` method?
  - **A**: Yes. `BaseAgent.interrupt()` delegates to `SessionController.cancel_run()` which finds the active `RunHandle` and calls `run_handle.cancel()`. For standalone mode (no SessionPool), fall back to canceling `run_ctx.current_task` directly.
- **Q**: What happens to `BaseAgent.inject_prompt()` / `queue_prompt()`?
  - **A**: For native agents, these methods delegate to `SessionController.receive_request()` with appropriate priority, which calls `pydantic_ai_run.enqueue(...)` for active runs. For non-native agents, they retain existing behavior (delegating to `injection_manager` or `SessionPool` queues).
- **Q**: What about `PromptInjectionManager.inject()`/`consume()` for tool result augmentation?
  - **A**: Preserved for all agents. Only `queue()`/`pop_queued()` (follow-up prompts) is replaced by PydanticAI's `enqueue()` for native agents.
- **Q**: Should we add `cancelled` to `StreamCompleteEvent` or create `RunCancelledEvent`?
  - **A**: Add `cancelled: bool = False` to `StreamCompleteEvent`. This is a small, backward-compatible change.
- **Q**: Is `PendingMessageDrainCapability` auto-injected? Do we need to add it manually?
  - **A**: PydanticAI auto-injects `PendingMessageDrainCapability` outermost by default. No manual registration needed. Verify `NativeAgentHookManager.as_capability()` doesn't conflict.
