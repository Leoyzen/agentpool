---
rfc_id: RFC-0037
title: Unify Steer and Followup Message Injection in AgentPool
status: DRAFT
author: yuchen.liu
reviewers: []
created: 2026-06-15
last_updated: 2026-06-15
decision_date:
related_rfcs:
  - RFC-0029 (Agent Reactivation via Pending Prompt Queue — legacy `inject_prompt` / `queue_prompt`)
related_specs:
  - openspec/changes/unify-steer-followup/ (implementation spec)
---

# RFC-0037: Unify Steer and Followup Message Injection in AgentPool

## Overview

AgentPool currently has two parallel message injection systems for delivering messages to running agents: `PromptInjectionManager` (per-run, tool-hook-consumed) and `TurnRunner._post_turn_*` (per-session, external-caller-facing). Both exist alongside pydantic-ai's built-in `PendingMessageDrainCapability`, which already handles the same concerns with `asap`/`when_idle` priority queuing.

This RFC proposes exposing a unified `steer()`/`followup()` API on `TurnRunner` that maps directly to pydantic-ai's proven enqueue mechanism for native agents, while keeping the legacy `PromptInjectionManager` for non-native (ACP) agents where pydantic-ai's agent graph is unavailable. The change reduces code complexity, eliminates dual-write bugs, and provides a clean semantic interface for protocol handlers.

## Background & Context

### Current Architecture

AgentPool supports two agent types with fundamentally different execution models:

- **Native agents** (PydanticAI-based): Use a graph-based execution loop (`UserPromptNode → ModelRequestNode → CallToolsNode → End`). PydanticAI auto-injects `PendingMessageDrainCapability` as the outermost capability, providing `asap` (drain before next `ModelRequest`) and `when_idle` (drain when agent would otherwise `End`, via `after_node_run` redirect) priorities.

- **Non-native agents** (ACP): Communicate via JSON-RPC subprocess. No pydantic-ai graph, no capability system. Use `PromptInjectionManager` for both tool result augmentation and follow-up queuing.

The message injection currently flows through two parallel systems:

1. **`PromptInjectionManager`** (in `agents/prompt_injection.py`): Dual-purpose — `inject()`/`consume()` for tool result augmentation (wrapping in `<injected-context>` XML, consumed by `after_tool_execute` hooks), and `queue()`/`pop_queued()` for follow-up prompts (processed by manual `while` loop after turn completion).

2. **`TurnRunner._post_turn_*`** (in `orchestrator/core.py`): Per-session dicts (`_post_turn_injections`, `_post_turn_prompts`) for external callers, drained by `_process_queued_work()` and `_trigger_auto_resume()`.

### Key Terms

| Term | Definition |
|------|------------|
| **Steer** | Inject a message into an active agent run that the model sees at the earliest opportunity (before the next LLM call). Used for urgent guidance, corrections, mid-task instructions. |
| **Followup** | Queue a message to be processed only after the agent's current work is naturally complete. Used for post-processing, continuation, non-urgent additions. |
| **`PendingMessageDrainCapability`** | PydanticAI auto-injected capability that drains `asap` messages at `before_model_request` and `when_idle` messages at `after_node_run` (redirecting `End` to new `ModelRequestNode`). |
| **`PromptInjectionManager`** | AgentPool's legacy injection manager with dual queues for tool augmentation and follow-up. |

### Existing Related Work

- **RFC-0029** introduced the original `inject_prompt()`/`queue_prompt()` mechanism for agent reactivation. This RFC supersedes that design for native agents by replacing the manual queue with pydantic-ai's built-in mechanism.
- **OpenSpec change `pending-message-queue`** defined the Phase 1/Phase 2 migration plan for adopting `PendingMessageDrainCapability`.
- **OpenSpec change `sessionpool-only-execution`** removed the manual turn-lock serialization for native agents.

## Problem Statement

### Problem 1: Dual-System Redundancy

For native agents, both `PromptInjectionManager` and `PendingMessageDrainCapability` handle message queuing and delivery. This creates:

- **Dual-write bugs**: `BaseAgent.inject_prompt()` writes to BOTH `injection_manager` AND `session_pool.receive_request()` — the same message is queued in two places and could be processed twice.
- **Manual loop duplication**: `_run_turn_unlocked()` has a `while has_queued(): pop_queued() + _run_stream_once()` loop that duplicates `PendingMessageDrainCapability.after_node_run()` redirect behavior.
- **Maintenance burden**: Changes to message delivery must be made in two places.

### Problem 2: No Clean Steer/Followup API

External callers (protocol handlers, `BackgroundTaskProvider`) must understand the internal distinction between `inject_prompt` and `queue_prompt` and manually decide which to call. There is no semantic `steer()` or `followup()` method that maps to the caller's intent.

### Problem 3: `_run_agentlet_core()` Uses Bare `async for`

The native agent's `_run_agentlet_core()` uses `async for node in agent_run:` which calls `__anext__` — this does NOT fire `after_node_run` capability hooks, so `PendingMessageDrainCapability.when_idle` drain never activates. This means `followup()` messages are never delivered on native agents routed through the standalone streaming path.

### Impact of Not Solving

- Continued risk of dual-write bugs causing duplicate message processing
- Protocol handlers must understand internal implementation details
- `followup()` semantics are unreliable for native agents due to the `async for` bug

## Goals & Non-Goals

**Goals:**
- Expose `steer()`/`followup()` on `TurnRunner` that maps to `enqueue(priority='asap'/'when_idle')` for native agents
- Remove the manual follow-up prompt loop from `_run_turn_unlocked()` for native agents
- Keep `PromptInjectionManager.inject()`/`consume()` for tool result augmentation on ALL agents
- Keep non-native agent (ACP) execution path unchanged
- Fix `_run_agentlet_core()` bare `async for` → `next()` loop

**Non-Goals:**
- Changing the non-native agent execution path
- Replacing `PromptInjectionManager.inject()`/`consume()` for tool augmentation (semantically different from `enqueue()`)
- Changing `RunExecutor` (already uses `next()` correctly)
- Adding a new module — logic lives in `TurnRunner` and `SessionController`

## Evaluation Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Correctness | High | Messages must not be lost, duplicated, or misrouted |
| Backward Compatibility | High | Existing callers must continue working |
| Maintainability | High | Clear separation of concerns, no dual systems |
| Implementation Effort | Medium | Should be implementable in 1-2 days |
| Migration Risk | Medium | Protocol handlers must update to new API |

## Options Analysis

### Option 1: Unify via PydanticAI's PendingMessageDrainCapability (Recommended)

Expose `steer()`/`followup()` on `TurnRunner` that directly calls `agent_run.enqueue()` with the appropriate priority. Remove the manual follow-up loop from `_run_turn_unlocked()` for native agents. Gate `flush_pending_to_queue()` behind `agent.AGENT_TYPE != "native"`. Fix `_run_agentlet_core()` `async for` → `next()`. Keep `PromptInjectionManager` for non-native agents and for tool result augmentation on all agents.

**Advantages:**
- Eliminates dual-system redundancy for native agents
- Leverages proven pydantic-ai mechanism (`PendingMessageDrainCapability`)
- Clean semantic API (`steer` = mid-run, `followup` = post-run)
- No new capability needed — `PendingMessageDrainCapability` is auto-injected
- Backward compatible: deprecated methods delegate to new ones

**Disadvantages:**
- Agent type detection must be reliable (uses `agent.AGENT_TYPE` ClassVar)
- `TurnRunner` needs access to `AgentRun` (via `RunHandle.active_agent_run`)
- Requires `_run_agentlet_core()` fix to make `when_idle` drain work
- Two different code paths for native vs non-native (inherent, not avoidable)

**Effort Estimate**: Medium (1-2 days). Core changes in 5 files: `TurnRunner`, `RunHandle`, `_run_agentlet_core()`, `RunExecutor`, `BaseAgent`.

**Risk Assessment**: Medium. Main risks are: (1) `AgentRun` lifecycle management — solved by `RunHandle.active_agent_run`, (2) timing race at run completion boundary — solved by fallback to `receive_request()`, (3) agent type detection — solved by using `agent.AGENT_TYPE` ClassVar.

### Option 2: Keep Both Systems, Add Facade API

Add `steer()`/`followup()` as a facade layer that internally routes to `inject_prompt()`/`queue_prompt()` for all agent types. Keep the manual follow-up loop. Do not fix `_run_agentlet_core()`.

**Advantages:**
- Minimal code changes — pure additive
- No risk of breaking existing behavior
- Single code path for all agent types

**Disadvantages:**
- Does not solve the dual-system redundancy problem
- `when_idle` semantics still unreliable due to `async for` bug
- Manual loop continues duplicating `PendingMessageDrainCapability` behavior
- No reduction in maintenance burden

**Effort Estimate**: Small (0.5 days). Add facade methods only.

**Risk Assessment**: Low risk, but does not achieve the goals. The dual-system problem persists, and `followup()` semantics remain broken for native agents.

### Option 3: Full Refactor — Replace PromptInjectionManager Entirely

Replace `PromptInjectionManager` completely with `ctx.enqueue()` for both tool augmentation and follow-up on all agent types. Remove `PromptInjectionManager` entirely.

**Advantages:**
- Cleanest architecture — single mechanism for all message injection
- Eliminates all redundant code
- Consistent semantics across agent types

**Disadvantages:**
- Breaking change: tool augmentation currently uses `<injected-context>` XML wrapping, which `enqueue()` does not support (it inserts conversation messages, not tool result modifications)
- Non-native agents cannot use `ctx.enqueue()` (no pydantic-ai context)
- Higher risk: every code path that calls `injection_manager.inject()` must be migrated
- Significantly higher effort

**Effort Estimate**: Large (3-5 days). Requires migrating all tool augmentation call sites.

**Risk Assessment**: High. Tool augmentation semantics are fundamentally different from conversation injection. Replacing one with the other changes model-facing behavior.

### Evaluation Matrix

| Criterion | Weight | Option 1 | Option 2 | Option 3 |
|-----------|--------|----------|----------|----------|
| Correctness | High | ✅ | ⚠️ (when_idle broken) | ✅ |
| Backward Compatibility | High | ✅ | ✅ | ❌ (breaking) |
| Maintainability | High | ✅ | ❌ (dual system) | ✅ |
| Implementation Effort | Medium | 1-2 days | 0.5 days | 3-5 days |
| Migration Risk | Medium | Low | None | High |

## Recommendation

**Option 1** is recommended. It solves the core problems (dual-system redundancy, missing API, `async for` bug) while maintaining backward compatibility. The approach is conservative: `PromptInjectionManager` is not removed, only bypassed for native agents. Non-native agents are unaffected. Deprecated methods delegate to new ones.

The three critical risks are all mitigated:
1. **Agent type detection**: Use `agent.AGENT_TYPE` ClassVar (always correct) instead of `session.metadata.get("agent_type", "unknown")` (unreliable for native agents)
2. **`AgentRun` access**: Expose via `RunHandle.active_agent_run`, set by `RunExecutor`, read by `TurnRunner`
3. **`when_idle` drain**: Fix `_run_agentlet_core()` `async for` → `next()` loop to fire `after_node_run` hooks

## Technical Design

### Architecture

```
External Caller (protocol handler, BackgroundTaskProvider)
    │
    ▼
SessionController.receive_request(session_id, content, priority="steer"|"followup")
    │
    ├── [idle session] → create RunHandle + run loop
    │
    └── [active session] → TurnRunner.steer() / TurnRunner.followup()
                              │
                              ├── [native] → run_handle.active_agent_run.enqueue(priority='asap'/'when_idle')
                              │                   │
                              │                   ▼
                              │           PendingMessageDrainCapability
                              │           ├── before_model_request → drain 'asap' into next LLM call
                              │           └── after_node_run → drain 'when_idle', redirect End → ModelRequestNode
                              │
                              └── [non-native] → injection_manager.inject() / injection_manager.queue()
                                                    │
                                                    ▼
                                            after_tool_execute hook / manual follow-up loop
```

### Key Components

#### `RunHandle.active_agent_run` (orchestrator/run.py)

```python
@dataclass
class RunHandle:
    # ... existing fields ...
    active_agent_run: AgentRun | None = None  # renamed from _native_run_ref
```

Set by `RunExecutor` at `agentlet.iter()` entry, cleared in `finally` block.

#### `TurnRunner.steer()` / `TurnRunner.followup()` (orchestrator/core.py)

```python
async def steer(self, session_id: str, message: str) -> None:
    session = self.sessions.get(session_id)
    if session is None or session.current_run_id is None:
        await self.receive_request(session_id, message, priority="steer")
        return
    run_handle = self.sessions._runs.get(session.current_run_id)
    if run_handle is None:
        await self.receive_request(session_id, message, priority="steer")
        return
    agent = self._get_session_agent(session_id)
    if agent is None:
        return
    if agent.AGENT_TYPE == "native":
        if run_handle.active_agent_run is not None:
            run_handle.active_agent_run.enqueue(message, priority='asap')
        else:
            await self.receive_request(session_id, message, priority="steer")
    else:
        run_handle.run_ctx.injection_manager.inject(message)

async def followup(self, session_id: str, message: str) -> None:
    # Symmetric logic with priority='when_idle' and injection_manager.queue()
```

#### `_run_agentlet_core()` Fix (agents/native_agent/agent.py)

Replace:
```python
async for node in agent_run:
    if isinstance(node, ModelRequestNode | CallToolsNode):
        async with node.stream(agent_run.ctx) as stream:
            ...
```

With:
```python
node = agent_run.next_node
while True:
    if isinstance(node, ModelRequestNode | CallToolsNode):
        async with node.stream(agent_run.ctx) as stream:
            ...
    node = await agent_run.next(node)
    if isinstance(node, End):
        break
```

#### Agent Type Detection

Use `agent.AGENT_TYPE` (ClassVar) instead of `session.metadata.get("agent_type", "unknown")`:

```python
# In _run_turn_unlocked():
agent = ...  # already resolved
if agent.AGENT_TYPE != "native":
    run_ctx.injection_manager.flush_pending_to_queue()
    while run_ctx.injection_manager.has_queued():
        ...
```

### API Surface

```python
# TurnRunner
async def steer(session_id: str, message: str) -> None
async def followup(session_id: str, message: str) -> None

# SessionController.receive_request (priority aliases)
async def receive_request(session_id, content, priority="followup")
    # "steer" → "asap", "followup" → "when_idle"
    # "asap"/"when_idle" still accepted for backward compatibility
```

### Data Flow

```
steer("fix the analysis")
    → enqueue(priority='asap')
    → PendingMessageDrainCapability.before_model_request()
    → message appears in next ModelRequest context
    → model sees it in the next LLM call

followup("also check tests")
    → enqueue(priority='when_idle')
    → message sits in queue while agent processes tools
    → agent would otherwise End
    → PendingMessageDrainCapability.after_node_run()
    → redirects End → ModelRequestNode
    → agent continues with the followup message
```

## Security Considerations

- **Message injection boundary**: `steer()`/`followup()` on `TurnRunner` is called by protocol handlers that already have session access. No new privilege escalation.
- **`enqueue()` thread safety**: If called from a different asyncio event loop, must use `loop.call_soon_threadsafe()`. All current callers are in the same event loop.

## Implementation Plan

### Phase 1: Foundation (Tasks 1-3)

1. **Rename `_native_run_ref` → `active_agent_run`** in `RunHandle`. Wire `RunExecutor` to set/clear it.
2. **Fix `_run_agentlet_core()` `async for` → `next()`** loop. Preserve both streaming branches (with/without `event_bus`).
3. **Change agent type detection** in `_run_turn_unlocked()` to use `agent.AGENT_TYPE`.

### Phase 2: API + Loop Removal (Tasks 4-7)

4. **Add `steer()`/`followup()`** to `TurnRunner` with agent-type-aware routing.
5. **Add `priority="steer"|"followup"` aliases** to `SessionController.receive_request()`.
6. **Gate manual follow-up loop** in `_run_turn_unlocked()` behind `agent.AGENT_TYPE != "native"`. Gate `flush_pending_to_queue()` similarly.
7. **Verify existing gating** in `_run_stream_direct()` (already correct for native agents).

### Phase 3: Migration + Tests (Tasks 8-10)

8. **Deprecate `inject_prompt()`/`queue_prompt()`** for native agents. Delegate to `steer()`/`followup()`.
9. **Update external callers**: `BackgroundTaskProvider`, protocol handlers.
10. **Tests**: Unit tests for `steer()`/`followup()` routing, integration tests for `when_idle` drain and tool augmentation preservation.

**Dependencies**: Phase 2 depends on Phase 1. Phase 3 depends on Phase 2.

**Rollback Strategy**: Revert `_run_turn_unlocked()` and `_run_agentlet_core()` to previous state. Keep `steer()`/`followup()` API (additive, backward-compatible). `PromptInjectionManager` is never removed, only bypassed for native agents.

## Open Questions

1. **Standalone path `steer()`/`followup()`**: The standalone streaming path (via `_stream_events()`, not `RunExecutor`) has no `RunHandle.active_agent_run`. Can `steer()` reach this path? Currently no — standalone agents are not managed by `TurnRunner`. If needed in the future, a `ContextVar`-based approach could be added. This is documented as intentional.

2. **`_create_run()` agent type detection**: `_create_run()` is synchronous and cannot resolve the agent. It currently falls back to `session.metadata.get("agent_type", "unknown")`. The actual gating happens later in `_run_turn_unlocked()` where the agent IS resolved. Is this acceptable, or should `_create_run()` be refactored to accept an agent reference?

3. **`followup()` with zero tool calls**: If a native agent produces a final result without any tool calls, `after_node_run` fires after `CallToolsNode`. A `when_idle` message enqueued during the agent's first response (before any tool calls) would be drained correctly — `CallToolsNode` still runs and its `run()` method returns the next node or `End`, triggering the hook. No special handling needed.

## Decision Record

| Decision | Date | Rationale |
|----------|------|-----------|
| Adopt Option 1 (unify via PendingMessageDrainCapability) | 2026-06-15 | Best balance of correctness, backward compatibility, and effort |
| Use `agent.AGENT_TYPE` (not session metadata) | 2026-06-15 | Metadata is unreliable for native agents (defaults to "unknown") |
| Expose `AgentRun` via `RunHandle` (not ContextVar) | 2026-06-15 | `TurnRunner` and `RunExecutor` may run in different asyncio tasks |
| Fix `_run_agentlet_core()` instead of switching to `RunExecutor` | 2026-06-15 | `RunExecutor` doesn't handle prompt setup that `_run_stream_once()` does |

## References

- [OpenSpec Change: unify-steer-followup](openspec/changes/unify-steer-followup/) — detailed implementation spec and tasks
- [RFC-0029: Agent Reactivation](./RFC-0029-agent-reactivation-pending-prompt-queue.md) — legacy inject_prompt/queue_prompt design
- [PydanticAI PendingMessageDrainCapability source](../../../pydantic-ai/pydantic_ai_slim/pydantic_ai/capabilities/_pending_messages.py) — auto-injected capability with asap/when_idle priority
- [Pi-Agent Steering/Followup Design](https://github.com/pi/pi-agent) — reference implementation with two-queue system (steering vs followup separation)