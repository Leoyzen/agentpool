## Context

The OpenCode server has been integrated with SessionPool in PR #47 (`opencode/session-pool-integration`). The integration routes messages through `SessionPool.receive_request()` and consumes events from the EventBus using temporary consumers created per HTTP request.

However, this design has a critical flaw: `TurnRunner.run_loop()` may execute auto-resume turns after the first turn completes, but the per-request consumer is torn down when the first turn's `RunHandle.complete_event` fires. This causes auto-resume events to be lost.

Current architecture:
```
HTTP Request
  └─ _process_message_locked()
      ├─ create temporary EventBus consumer
      ├─ create temporary SessionStatusBridge
      ├─ SessionPool.receive_request() → run_loop()
      │   ├─ _run_turn_unlocked() → complete_event.set()  ← consumer torn down here!
      │   └─ _process_queued_work() → auto-resume events lost
      └─ finally: tear down consumer/bridge
```

## Goals / Non-Goals

**Goals:**
- Ensure ALL events from a session (including auto-resume turns) are delivered to the frontend
- Simplify `message_routes.py` by removing temporary consumer/bridge management
- Make EventBus consumer and SessionStatusBridge session-scoped resources
- Delay `RunHandle.complete_event` to represent full `run_loop()` completion

**Non-Goals:**
- Changing ACP protocol handler (it already has its own consumer pattern)
- Modifying EventBus or TurnRunner internal architecture beyond `complete_event` timing
- Removing backward compatibility with `use_session_pool=False`
- Changing SSE broadcasting mechanism

## Decisions

### Decision 1: Session-scoped EventBus consumer
**Rationale**: EventBus consumers must outlive individual HTTP requests because session execution (via auto-resume) continues after a single request returns.

**Implementation**:
- `OpenCodeSessionPoolIntegration` manages consumers in `_event_consumers: dict[str, asyncio.Task]`
- Consumer starts in `create_session()` via `_start_event_consumer(session_id)`
- Consumer stops in `close_session()` via `_stop_event_consumer(session_id)`
- Consumer subscribes with `scope="descendants"` to receive child session events

### Decision 2: Session-scoped SessionStatusBridge
**Rationale**: Status synchronization must cover auto-resume periods. A bridge torn down after the first turn would miss status changes during auto-resume.

**Implementation**:
- Bridge remains in `_status_bridges` dict (already session-scoped)
- Bridge starts in `create_session()` alongside consumer
- Bridge stops in `close_session()`

### Decision 3: Delay `complete_event` to `run_loop` completion
**Rationale**: `complete_event` currently signals "first turn done" which is incorrect for a method named `run_loop`. External waiters (like the sync endpoint) need to wait for the entire loop including auto-resume.

**Implementation**:
- Remove `run_handle.complete_event.set()` from `_run_turn_unlocked()` finally block
- Add it at the end of `run_loop()` after `_process_queued_work()` returns
- Also handle error paths: set `complete_event` in `run_loop` except blocks

### Decision 4: Per-request only waits, doesn't manage resources
**Rationale**: The HTTP request handler's only job is to initiate the run and wait for completion. Resource lifecycle is a session concern.

**Implementation**:
- `_process_message_locked()` removes:
  - `SessionStatusBridge` creation
  - `event_bus.subscribe()` call
  - `_consume_events()` task creation
  - `event_bus.unsubscribe()` in finally
  - `status_bridge.stop()` in finally
- Keeps: `run_handle.complete_event.wait()`, message finalization, error handling

### Decision 5: Consumer uses EventProcessorContext from OpenCodeEventAdapter
**Rationale**: The existing `OpenCodeEventAdapter` already handles event conversion. The session-scoped consumer should reuse this.

**Implementation**:
- Consumer creates `EventProcessorContext` once at startup
- Uses `OpenCodeEventAdapter` to convert events
- Calls `state.broadcast_event()` for each converted event
- Handles `SpawnSessionStart` to track child sessions

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Consumer leaks if session not properly closed | `shutdown()` method cleans all consumers; session TTL cleanup ensures eventual cleanup |
| Memory growth from long-lived consumers | Consumers only exist for active sessions; closed sessions remove consumers |
| Multiple consumers on same session | `create_session` checks `_event_consumers` dict before starting new consumer |
| `complete_event` semantic change affects ACP | ACP handler also benefits from waiting for full run; verify ACP tests pass |
| Child session events not properly routed | Use `scope="descendants"` subscription; handle `SpawnSessionStart` in consumer |
| Status bridge double-reporting | Bridge only started in `create_session`, not per-request |

## Migration Plan

1. Update `OpenCodeSessionPoolIntegration` with consumer management methods
2. Modify `TurnRunner.run_loop()` to set `complete_event` at loop end
3. Remove temporary consumer/bridge from `message_routes.py`
4. Verify `create_session` starts consumer/bridge
5. Verify `close_session` stops consumer/bridge
6. Run OpenCode server tests
7. Manual test: trigger auto-resume and verify events reach frontend

## Open Questions

- Should the consumer be started lazily (on first message) or eagerly (on session creation)?
- How should the consumer handle `RunErrorEvent` vs `StreamCompleteEvent` for cleanup?
- Should child session consumers be nested tasks or separate session-scoped consumers?
