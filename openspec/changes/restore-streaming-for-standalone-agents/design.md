## Context

### Current Architecture

After the pydantic-graph migration (`bf58c0740`), `NativeAgent._stream_events()` wraps the agent in a single-node graph for all execution paths:

```
start_node ──► _agent_step ──► end_node
                    │
                    └─ _run_agentlet_core()
                       └─ agentlet.iter() → events → state.event_queue
```

The graph runner (`GraphRun.__anext__()`) yields only after `_agent_step` fully returns. Events pushed to `state.event_queue` during Step execution are invisible to consumers until the Step boundary is crossed.

### Root Cause

pydantic-graph's `Step.call` is a `StepFunction` — an `await`-style async function. The graph runner executes it with a plain `await`:

```python
# graph_builder.py _run_task()
output = await node.call(step_context)
```

There is no mechanism for Step-internal events to escape the `await` boundary. This is by design: a Step is an atomic execution unit in pydantic-graph's concurrency model.

### Affected Scenarios

**ALL streaming scenarios are affected** because `_stream_events()` is the single entry point for agent streaming:

| Scenario | Entry Point | Path | Streaming Status |
|---|---|---|---|
| **Standalone** | `agent.run_stream()` | `BaseAgent.run_stream()` → `_stream_events()` | ❌ Delayed |
| **Team parallel** | `team.run_stream()` | `Team.run_stream()` → `node.run_stream()` → `_stream_events()` | ❌ Delayed |
| **Team sequential** | `teamrun.run_stream()` | `TeamRun.run_stream()` → `node.run_stream()` → `_stream_events()` | ❌ Delayed |
| **Subagent** | `subagent_tool()` | `agent.run_stream()` → `_stream_events()` | ❌ Delayed |
| **Graph execution** | `MessageNode.run()` | `MessageNodeStep._execute()` → `_execute_node()` | ✅ Coarse-grained (by design) |

### pydantic-ai's Similar Problem

PR #4977 (pydantic-ai) documents the exact same constraint for durable execution:

> "model streaming happens inside an activity/step rather than in the outer agent loop... `wrap_run_event_stream` hook fires for tool-call events and the final post-streaming batch, but it does not see individual model-response events live"

This confirms the structural nature of the problem.

## Goals / Non-Goals

**Goals:**
- Restore real-time streaming for ALL `_stream_events()` callers (standalone, team, subagent)
- Maintain graph-based execution for `MessageNode.run()` / `MessageNode.run_stream()` (via `MessageNodeStep`)
- Keep `_run_agentlet_core()` as the shared streaming core
- Preserve all existing tests and backward compatibility

**Non-Goals:**
- Revert the pydantic-graph migration for team execution
- Add event streaming to graph-based `MessageNode.run()` (out of scope — coarse-grained is acceptable)
- Modify pydantic-graph upstream
- Add global event bus or register/subscribe pattern

## Decisions

### Decision 1: Revert `_stream_events()` to direct background task iteration

**Choice**: Remove the pydantic-graph wrapping from `_stream_events()` entirely. Restore the pre-migration pattern where a background task directly runs `agentlet.iter()` and pushes events to an async queue in real-time.

**Why remove graph wrapping entirely**:
- `_stream_events()` is **never called in graph context** — graph execution routes through `MessageNodeStep._execute()` → `_execute_node()`
- The graph wrapping inside `_stream_events()` was an implementation artifact of the migration that broke all streaming uniformly
- All streaming callers (standalone, team parallel, teamrun sequential, subagent) need real-time events
- Keeping the graph wrapping would require all of them to accept batched events, which defeats the purpose of streaming

```python
# Restored _stream_events() pattern
async def _stream_events(self, ...):
    # ... setup ...
    yield RunStartedEvent(...)
    
    # Background task directly runs agentlet.iter()
    event_queue = asyncio.Queue()
    
    async def iteration_task():
        result = await self._run_agentlet_core(
            event_queue=event_queue,
            # ... other args ...
        )
        await event_queue.put(None)  # sentinel
    
    task = asyncio.create_task(iteration_task())
    
    # Consumer drains queue in real-time
    async for event in _drain_queue(event_queue):
        yield event
    
    yield StreamCompleteEvent(message=result)
```

**Rationale**: Minimal, surgical change. Only `_stream_events()` is modified. `_run_agentlet_core()` (shared core), `_execute_node()` (graph path), and `MessageNodeStep` (graph adapter) remain untouched.

### Decision 2: Keep `_execute_node()` for graph execution

**Choice**: Leave `_execute_node()` unchanged. It continues to be called by `MessageNodeStep._execute()` for `MessageNode.run()` / `MessageNode.run_stream()` generic graph execution.

**Rationale**: Graph execution (`MessageNode.run()`) is non-streaming or coarse-grained by design. The current `_execute_node()` → `_run_agentlet_core()` → `state.event_queue` pattern is acceptable for graph contexts where consumers are `GraphRun` iterators that expect per-step granularity.

### Decision 3: No `_state` detection needed

**Choice**: Do not add `_state` kwarg detection or dual-path logic.

**Rationale**: Both Metis and Oracle reviews independently confirmed that `_stream_events()` is never invoked with `_state`. The call graph is already separated:
- Streaming: `_stream_events()` ← `BaseAgent.run_stream()` ← all streaming scenarios
- Graph: `_execute_node()` ← `MessageNodeStep._execute()` ← `MessageNode.run()` / `run_stream()`

Adding `_state` detection would be dead code and misleading to future maintainers.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| **Graph path regression** | Graph path is untouched (`_execute_node()` unchanged). Only the dead-code graph wrapping inside `_stream_events()` is removed. |
| **Code divergence** | Single path for streaming. `_run_agentlet_core()` remains shared. Less divergence than dual-path approach. |
| **Cancellation semantics** | Reuse pre-migration cancellation pattern (already battle-tested before bf58c0740). Add test coverage. |
| **Team streaming behavior change** | This is a **fix**, not a regression. Team member streaming was broken by the migration and will be restored. |
| **Future confusion** | Add clear docstrings explaining why `_stream_events()` uses direct iteration while `_execute_node()` uses graph queue. |

## Migration Plan

No migration needed — this is a backward-compatible bugfix. Existing code using `agent.run_stream()` will automatically benefit from restored real-time streaming.

## Open Questions

1. **Event timing test**: Should we add a test asserting first event arrives within N ms? → **Yes, add as a required test task.**
2. **Graph streaming improvement**: Should `MessageNode.run_stream()` support fine-grained streaming in the future? → **File as follow-up issue**, out of scope for this bugfix.