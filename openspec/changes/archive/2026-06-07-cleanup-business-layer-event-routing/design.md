## Context

The AgentPool event routing currently has **three parallel paths** for subagent events:

1. **Protocol layer** (`session_pool_integration.py`): `_event_consumer_loop()` subscribes to EventBus with `scope="descendants"` and forwards all events (including nested subagents) to the frontend via SSE. This is the correct, unified approach.

2. **Business layer — async mode** (`subagent_tools.py`): Manually subscribes to EventBus with `scope="session"` and runs `_consume_events_to_fs()` to write events to the filesystem. This duplicates what the protocol layer already does.

3. **Business layer — sync mode** (`workers.py`, `agentpool_commands/pool.py`): Manually wraps every event in `SubAgentEvent` and emits via `ctx.events.emit_event()` (the local `MessageNode` event system). These events may reach EventBus indirectly through `StreamEventEmitter._emit()`, but the wrapping is redundant.

The protocol layer solved the event routing problem in the `unify-tool-event-paths` change. Now the business layer carries redundant code from before that solution existed.

## Goals / Non-Goals

**Goals:**
- Remove all manual event routing from the business layer
- Ensure all subagent events flow exclusively through EventBus → protocol layer → SSE
- Make business layer agnostic to how events reach the frontend
- Reduce maintenance surface (one event path instead of three)

**Non-Goals:**
- No changes to the protocol layer (`session_pool_integration.py`)
- No changes to EventBus or EventProcessor
- No changes to frontend behavior (events must still arrive the same way)
- Not adding new features, only removing redundant ones

## Decisions

### Decision 1: Remove `_consume_events_to_fs()` from `subagent_tools.py`

**Rationale**: The protocol layer already subscribes with `scope="descendants"` and receives all child session events. The manual subscription in `subagent_tools.py` duplicates this and only adds filesystem output, which is not needed for frontend streaming.

**Alternative considered**: Keep filesystem output as a side effect. Rejected because filesystem persistence should be explicit (e.g., a dedicated tool) rather than a side effect of event routing.

### Decision 2: Remove manual `ctx.events.emit_event()` calls from `workers.py` and `pool.py`

**Rationale**: `ctx.events.emit_event()` emits to the local `MessageNode` connection system, not directly to EventBus. In SessionPool mode, `run_stream()` already flows through TurnRunner which publishes to EventBus. The manual `emit_event()` calls create duplicate events in the local system.

**Important**: We do NOT remove `SubAgentEvent` entirely. The protocol layer (both ACP and OpenCode) relies on `SubAgentEvent` for proper event attribution and routing. `SubAgentEvent` wrapping is still needed — it just shouldn't be done manually in the business layer. Instead, TurnRunner will handle this automatically when publishing to EventBus.

**Note**: If TurnRunner does not currently wrap events in `SubAgentEvent`, this change reveals a gap that needs to be addressed separately. The business layer cleanup should proceed, and the protocol layer should be updated to handle raw child session events correctly (see Open Questions).

### Decision 3: Use `session_pool.run_stream()` for sync mode, `receive_request()` for async mode

**Rationale**: 
- **Sync mode** (workers, tools that return results): Must block until completion and extract the final result. `session_pool.run_stream(child_session_id, prompt)` blocks, yields events, and allows extracting `final_content` from `StreamCompleteEvent`. Events naturally flow to EventBus via TurnRunner.
- **Async mode** (background tasks): Fire-and-forget is the desired behavior. `session_pool.receive_request(child_session_id, prompt)` starts the run in background and returns immediately.

**Correction from initial design**: The initial design incorrectly proposed `receive_request()` for sync mode. This was identified as a critical flaw in review — `receive_request()` is fire-and-forget and cannot return a synchronous result to the calling agent.

### Decision 4: Explicit non-SessionPool fallback

**Rationale**: Some deployments may use agents standalone without SessionPool. We need a clear policy.

**Decision**: Require SessionPool for subagent tools. If `session_pool` is None, raise a clear error. Standalone agent usage should use direct `agent.run()` instead of subagent tools. This simplifies the code by eliminating the dual-path branching.

## Risks / Trade-offs

| Risk | Level | Mitigation |
|------|-------|------------|
| Sync mode result extraction | High | Use `session_pool.run_stream()` which blocks and yields events; extract final content from `StreamCompleteEvent` |
| SubAgentEvent removal breaks protocol layer | High | **Do not remove SubAgentEvent** — TurnRunner should wrap events. If not yet implemented, add protocol-layer task to handle raw child events (see Open Questions) |
| Duplicate event processing | Medium | Parent consumer uses `scope="descendants"` and child consumers also receive events. This is existing behavior, not new. Protocol layer should filter or the design should use `scope="session"` for child consumers |
| Filesystem output lost | Medium | Explicit post-run write after `receive_request()` completes |
| Non-SessionPool deployments break | Medium | Require SessionPool; provide clear error message |
| Test regressions | Low | Update tests to assert EventBus-based routing |

## Migration Plan

1. **Phase 1**: Remove `_consume_events_to_fs()` from `subagent_tools.py` async mode; add explicit post-run filesystem write
2. **Phase 2**: Update `workers.py` to use `session_pool.run_stream()` instead of `worker.run_stream()`; remove `ctx.events.emit_event()` calls
3. **Phase 3**: Update `agentpool_commands/pool.py` similarly
4. **Phase 4**: Update tests to verify EventBus-based routing
5. **Phase 5**: Verify frontend behavior unchanged

Rollback: Revert commit and re-enable manual paths if frontend events are lost.

## Open Questions

1. **Does TurnRunner wrap events in `SubAgentEvent`?** If not, the protocol layer may need updates to handle raw child session events. The ACP converter and OpenCode event processor both rely on `SubAgentEvent` for proper attribution. This should be verified before implementing this change.

2. **Should child consumers use `scope="session"` instead of `scope="descendants"`?** If parent consumer uses `scope="descendants"`, it receives all child events. If child consumers also exist, events are processed twice. Consider changing child consumers to `scope="session"` to avoid duplicate processing.

3. **How should standalone agents work?** If SessionPool is required, standalone usage must be documented. Alternatively, a minimal fallback could be kept that runs the agent without event routing (for testing/CLI usage).