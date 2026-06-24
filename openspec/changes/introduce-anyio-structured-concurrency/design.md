## Context

AgentPool is an agent orchestration framework managing multiple AI agent sessions concurrently across 6 protocol servers (ACP, OpenCode, AG-UI, OpenAI API, MCP, A2A). Each session creates multiple async tasks: event consumers, MCP connections, agent runs, subagent runs, and background auto-resume tasks. Currently, all tasks are managed through manual `asyncio.create_task()` with ad-hoc tracking (dicts, sets, done-callbacks) — no structured concurrency exists.

The codebase already imports `anyio` in 52 files for I/O primitives (memory streams, subprocess management), but does not use its structured concurrency primitives (`CancelScope`, `TaskGroup`).

A production shutdown bug (`RuntimeError: SessionPool not available`) demonstrates the fragility: consumer tasks' `finally` blocks access `EventBus` after `SessionPool` has been destroyed, because there's no lifecycle coupling between the two.

## Goals / Non-Goals

**Goals:**
- Replace all manual task tracking (`_consumer_tasks` dict, `_background_tasks` set, `TaskManager._pending_tasks`) with `anyio.TaskGroup` and `anyio.CancelScope`
- Establish a hierarchical `CancelScope` structure: pool → session → agent run → subagent run
- Ensure deterministic shutdown ordering: consumer tasks complete → MCP connections close → SessionPool teardown
- Make subagent cancellation cascade automatically from parent agent cancellation
- Remove `TaskManager` class (dead priority queue code, zero callers with non-default `priority`/`delay`)
- Fix the shutdown crash without breaking existing protocol server tests

**Non-Goals:**
- Changing the public API of `ProtocolEventConsumerMixin`, `SessionController`, or `SessionPool`
- Adding anyio to protocol server layers that don't already use it (ACP agent binary protocol, WebSocket transport)
- Replacing `AsyncExitStack` usage (it's complementary to TaskGroup, not redundant)
- Changing `SessionLifecyclePolicy` semantics (behavior preserved, implementation changes)
- Replacing `asyncio.Queue` in places other than `EventBus` subscriber management (e.g., `RunExecutor.event_queue`, `GraphStreamingAdapter._event_queue` remain asyncio.Queue; only the EventBus pub/sub backbone migrates to memory streams)
- Migrating non-BaseServer `TaskManager` instances (`MessageNode.task_manager`, `EventManager.task_manager`, `StorageManager.task_manager`, `PromptManager.task_manager`, `StorageProvider.task_manager`, `ACPConnectionManager.tasks`, test harness) — these are scoped to a follow-up change to avoid scope creep. They will continue to use `TaskManager` until that change.

## Decisions

### Decision 1: CancelScope hierarchy

```
AgentPool.__aexit__
├─ Phase 1: _stop_all_consumers() — await all protocol-level consumer TaskGroups
├─ Phase 2: SessionPool.shutdown()
│  └─ Per-session CancelScope (one per active session)
│     ├─ TaskGroup: Event consumer tasks (replaces _consumer_tasks dict)
│     ├─ TaskGroup: Session MCP connections (replaces manual AcpMcpConnection tracking)
│     ├─ CancelScope: Active agent run
│     │  ├─ TaskGroup: Background iteration task (RunExecutor._iteration_task)
│     │  ├─ CancelScope: Subagent run (nested, auto-cancelled with parent)
│     │  └─ TaskGroup: Tool executions
│     └─ TaskGroup: Background auto-resume tasks (TurnRunner._background_tasks)
└─ CancelScope(shield=True): Critical cleanup (DB writes, log flushing, complete_event.set)
```

**Rationale**: `CancelScope` for cancellation boundaries (session, agent run, subagent run), `TaskGroup` for task nurseries (consumer tasks, MCP connections, tool executions). This separation reflects their different semantics: CancelScope says "everything in here should stop together"; TaskGroup says "I spawned these tasks and need to know when they're done".

**Alternatives considered**:
- *TaskGroup everywhere*: Would work but conflates task spawning with cancellation boundaries. Sessions don't spawn tasks directly — subsystems within sessions do. CancelScope is the right abstraction for the session boundary.
- *Single pool-level TaskGroup*: Too coarse — can't cancel individual sessions without affecting others.

### Decision 2: ProtocolEventConsumerMixin refactor

Replace the `_consumer_tasks: dict[str, asyncio.Task]` and `_consumer_queues: dict[str, asyncio.Queue]` with per-session `TaskGroup` + `CancelScope`.

```python
class ProtocolEventConsumerMixin(ABC):
    def __init__(self):
        self._session_scopes: dict[str, anyio.CancelScope] = {}
        self._session_groups: dict[str, anyio.TaskGroup] = {}
        self._consumer_lock: asyncio.Lock = asyncio.Lock()

    async def start_event_consumer(self, session_id: str) -> None:
        async with self._consumer_lock:
            if session_id in self._session_scopes:
                return
            scope = anyio.CancelScope()
            tg = anyio.create_task_group()
            self._session_scopes[session_id] = scope
            self._session_groups[session_id] = tg
            await tg.__aenter__()
            async with scope:
                tg.start_soon(self._event_consumer_loop, session_id)

    async def stop_event_consumer(self, session_id: str) -> None:
        scope = self._session_scopes.pop(session_id, None)
        tg = self._session_groups.pop(session_id, None)
        if scope is not None:
            scope.cancel()
        if tg is not None:
            with suppress(Exception):
                await tg.__aexit__(None, None, None)
```

**Key change**: The `finally` block in `_event_consumer_loop` no longer needs to call `self.event_bus.unsubscribe()` — the `TaskGroup` guarantees the task is complete, and `stop_event_consumer` handles the unsubscribe after the group exits. This eliminates the crash path (no more accessing `event_bus` from within a task whose parent scope is already gone).

**Rationale**: The public API (`start_event_consumer`, `stop_event_consumer`) is unchanged. Only internals change. All four protocol servers using this mixin (ACP, OpenCode, AG-UI, OpenAI API) benefit without code changes.

**Exception isolation for non-critical tasks**: `anyio.TaskGroup` cancels ALL children when ANY child raises an unhandled exception. This is different from `asyncio.TaskGroup` (which only cancels on scope cancellation). To prevent one non-critical task failure from cancelling all siblings, every `tg.start_soon()` call that spawns a task whose failure should NOT kill the group MUST use an exception-catching wrapper:

```python
async def _safe_auto_resume(self, session_id: str, **kwargs: Any) -> None:
    """Wrapper that prevents auto-resume failures from cancelling sibling tasks."""
    try:
        await self._trigger_auto_resume(session_id, **kwargs)
    except Exception:
        logger.exception("Auto-resume task failed", session_id=session_id)

# In inject_prompt, queue_prompt, steer, followup:
tg.start_soon(self._safe_auto_resume, session_id, **kwargs)
```

**Task classification by criticality**:

| Task | Criticality | Needs Wrapper? |
|---|---|---|
| Event consumer loop | Critical (losing it means session events are lost) | No |
| Agent iteration task | Critical (agent run cannot continue without it) | No |
| Auto-resume tasks (`_trigger_auto_resume`) | Non-critical (fire-and-forget; one failure shouldn't kill session) | **Yes** |
| Tool executions | Non-critical (one tool failure shouldn't abort the run) | **Yes** |
| `_consume_event_queue` (TurnRunner event publisher) | Critical (losing it means tool events are lost) | No |

### Decision 3: TaskManager deprecation

`TaskManager` (`src/agentpool/utils/tasks.py`) is **deprecated but NOT removed** in this change. Its usage in `BaseServer` and all 6 protocol server subclasses is replaced by `anyio.TaskGroup`. Seven non-BaseServer instances continue to use `TaskManager` until a follow-up change migrates them.

- `create_task()` → `tg.start_soon()` (for BaseServer + 6 protocol servers)
- `fire_and_forget()` → `tg.start_soon()` (TaskGroup tracks all children)
- `cleanup_tasks()` → `await tg.__aexit__(None, None, None)`
- Priority queue → kept (needed by non-BaseServer instances)
- Delayed execution → `anyio.sleep()` before `tg.start_soon()`
- Deprecation: `TaskManager.__init__()` emits `DeprecationWarning`

**Rationale**: Removing `TaskManager` entirely would break 7 modules that still depend on it (MessageNode, EventManager, StorageManager, PromptManager, StorageProvider, ACPConnectionManager, test harness). Deprecation is safer — it allows phased migration while keeping these modules working. The class is removed in a follow-up change once all instances are migrated.

**Affected callers (in scope)**: `BaseServer` and all subclasses (ACPServer, OpenCodeServer, AGUIServer, OpenAIServer, MCPServer, A2AServer). Migration is mechanical: `self.task_manager.create_task(coro)` → `self._task_group.start_soon(coro)`.

**Not in scope**: `MessageNode.task_manager`, `EventManager.task_manager`, `StorageManager.task_manager`, `PromptManager.task_manager`, `StorageProvider.task_manager`, `ACPConnectionManager.tasks`, test harness. These continue using `TaskManager`.

### Decision 4: Subagent CancelScope nesting

When `SessionController.spawn_session()` creates a child session, the child gets a `CancelScope` that is a child of the parent session's scope:

```python
# In SessionController.spawn_session():
parent_scope = self._session_scopes.get(parent_session_id)
if parent_scope is not None:
    child_scope = anyio.CancelScope()
    await child_scope.__aenter__()
    parent_scope.add_cancel_callback(lambda: child_scope.cancel())
    self._session_scopes[child_session_id] = child_scope
```

**Rationale**: This natively implements `SessionLifecyclePolicy.cascade` and `bound` — cancelling the parent scope automatically cancels the child scope. No manual iteration over `_children` dict needed. The existing `child-session-policy` spec requirements are preserved; only the implementation changes.

### Decision 5: event_bus property resilience

The `event_bus` property in `ACPProtocolHandler` (handler.py:77-83) currently raises `RuntimeError("SessionPool not available")` when `session_pool is None`. With the TaskGroup refactor, this crash path is eliminated because consumer tasks' cleanup happens inside the TaskGroup scope, which exits before SessionPool teardown. However, as a defense-in-depth measure, the property is also made resilient:

```python
@property
def event_bus(self) -> EventBus:
    if self._cached_event_bus is not None:
        return self._cached_event_bus
    session_pool = self.agent_pool.session_pool
    if session_pool is None:
        raise RuntimeError("SessionPool not available")
    self._cached_event_bus = session_pool.event_bus
    return self._cached_event_bus
```

### Decision 6: Shutdown ordering in AgentPool.__aexit__

Current (broken):
```python
await self._session_pool.shutdown()
self._session_pool = None  # ← consumer tasks may still be cleaning up
```

Fixed:
```python
# Phase 1: Stop all protocol-level consumer tasks
await self._stop_all_consumers()
# Phase 2: Shut down session pool (agents, runs, sessions)
await self._session_pool.shutdown()
# Phase 3: All consumers guaranteed done → safe to nullify
self._session_pool = None
```

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|---|
| `anyio.TaskGroup` differs from `asyncio.TaskGroup` in cancellation semantics (anyio cancels all children on any exception) | Classify all `tg.start_soon()` calls as "critical" or "non-critical". Non-critical tasks (auto-resume, tool executions) are wrapped in exception-catching helpers. See Decision 2 exception isolation table. |
| Migration breaks protocol servers that depend on `TaskManager` API | Mechanical replacement — `TaskManager.create_task()` → `TaskGroup.start_soon()` — verified by existing test suite. Non-BaseServer TaskManager instances are explicitly out of scope for this change (follow-up change). |
| Nested `CancelScope` adds overhead for short-lived sessions | `CancelScope` is lightweight (no thread/process overhead); only created for sessions, not per-turn |
| `TaskGroup.__aexit__` blocks until all children complete — could hang on stuck tasks | Add timeout `CancelScope` around `TaskGroup.__aexit__` in shutdown paths (5s default) |
| Existing tests may rely on fire-and-forget task timing | Use `anyio.Event` or `await tg.__aexit__()` to make task completion deterministic in tests |
| EventBus memory stream backpressure could cause `publish()` to block on slow subscribers | Hybrid approach: 0.1s timeout per send, event-drop fallback on first 2 timeouts, subscriber-drop on 3rd consecutive timeout. Parallel publishing prevents one slow subscriber from delaying others. |
| `merge_queue_into_iterator` removal breaks ACP agent for existing sessions | The replacement is a drop-in at `_run_stream_once()` — observable behavior is identical, only the internal merging mechanism changes |
| `GraphStreamingAdapter` timeout polling removal could hide cancellation latency | `TaskGroup` propagates cancellation through `CancelledError` immediately; no polling needed — verified by parity tests between old and new patterns |
| anyio is only a transitive dependency — could be removed by upstream | Add `anyio>=4.0` as a direct dependency in `pyproject.toml` in Phase 1 |

## Migration Plan

### Phase 1: Foundation (lowest risk)
1. Add `anyio>=4.0` as a direct dependency in `pyproject.toml`
2. Add `cancel_scope` field to `SessionState`
3. Refactor `RunExecutor.execute()` to use `TaskGroup` for `_iteration_task`
4. Verify all existing RunExecutor tests pass

### Phase 2: Protocol Consumers
5. Refactor `ProtocolEventConsumerMixin` to use per-session `TaskGroup` + `CancelScope`
6. Update `ACPProtocolHandler._event_consumer_loop()` override to work with TaskGroup/CancelScope lifecycle (deferred memory stream adaptation handled in Phase 4 task 4.7a)
7. Update `OpenCodeSessionPoolIntegration.shutdown()` to iterate `_session_groups` instead of `_consumer_tasks`
8. Run full test suite for ACP, OpenCode, AG-UI, OpenAI API protocol servers

### Phase 3: Session Lifecycle
9. Replace `TurnRunner._background_tasks` set with session `TaskGroup`; use `_safe_auto_resume` wrappers
10. Add subagent `CancelScope` nesting in `SessionController.spawn_session()`
11. Fix `AgentPool.__aexit__` shutdown ordering

### Phase 4: EventBus Memory Streams
12. Refactor `EventBus` subscriber management: replace `asyncio.Queue` with `anyio.create_memory_object_stream`
13. Update `EventBus.subscribe()` to return `MemoryObjectReceiveStream`
14. Update `EventBus.unsubscribe()` to close stream instead of dict removal
15. Update `EventBus.publish()` to use `await send.send()` with backpressure
16. Update `ProtocolEventConsumerMixin._event_consumer_loop()` to use `async for` over stream
17. Run `uv run pytest tests/ -k "event_bus" -x` and full ACP/OpenCode test pass

### Phase 5: merge_queue_into_iterator Removal
18. Replace `merge_queue_into_iterator` in `ACPAgent._run_stream_once()` with `TaskGroup` + memory stream pattern
19. Create `_forward_acp_events()` and `_forward_queue_events()` helper coroutines
20. Remove `merge_queue_into_iterator` from `src/agentpool/utils/streams.py`
21. Verify `tests/delegation/test_break_behavior.py` documented bugs are resolved
22. Run `uv run pytest tests/ -k "acp_agent" -x`

### Phase 6: GraphStreamingAdapter TaskGroup
23. Refactor `GraphStreamingAdapter.__aiter__()`: replace `_iteration_task` with `TaskGroup`
24. Remove timeout-based polling; use direct `event_queue.get()`
25. Remove `asyncio.shield()` + `wait_for()` cleanup pattern
26. Align with `RunExecutor` pattern (shared structural approach)
27. Run `uv run pytest tests/ -k "streaming_adapter" -x`

### Phase 7: TaskManager Deprecation
28. Replace `TaskManager` usage in `BaseServer` and all 6 protocol server subclasses with `anyio.TaskGroup`
29. Mark `TaskManager` class as deprecated (add `DeprecationWarning`); keep the class for non-BaseServer instances
30. Full integration test pass

### Phase 8: Shielded Cleanup + Verification
31. Add `CancelScope(shield=True)` around database write operations in session persistence
32. Add `CancelScope(shield=True)` with 5s timeout around MCP connection close in `MCPManager.cleanup()`
33. Add `CancelScope(shield=True)` around `complete_event.set()` on `RunHandle`
34. Verify shutdown crash is fixed
35. Run full test suite and code quality checks

### Rollback
Each phase is independently revertible. Phases 1-3 can ship without later phases. Phase 7 (TaskManager deprecation) can coexist with earlier phases during transition. Non-BaseServer TaskManager instances (`MessageNode`, `EventManager`, `StorageManager`, `PromptManager`, `StorageProvider`, `ACPConnectionManager`, test harness) continue to use `TaskManager` until a follow-up change migrates them.

### Out of Scope for This Change

**Non-BaseServer TaskManager instances**: The following `TaskManager` instances are NOT migrated in this change. They continue to use the `TaskManager` class until a follow-up change:

| Instance | File | Line | Follow-up |
|---|---|---|---|
| `MessageNode.task_manager` | `messagenode.py` | 113 | `MessageNode` + `base_team.py` + `connection_manager.py` migration |
| `EventManager.task_manager` | `event_manager.py` | 68 | Event lifecycle migration |
| `StorageManager.task_manager` | `storage/manager.py` | 92 | Storage persistence migration |
| `PromptManager.task_manager` | `prompts/manager.py` | 74 | Prompt hub migration |
| `StorageProvider.task_manager` | `storage/base.py` | 62 | Storage provider migration |
| `ACPConnectionManager.tasks` | `acp_agent.py` | 248 | ACP session command/rule loading migration |
| Test harness | `tool_call_harness.py` | 108 | Test infrastructure migration |

These are explicitly scoped out to keep this change focused on the orchestrator + protocol server layer. The `TaskManager` class is NOT removed in this change — it is marked as deprecated but kept for these remaining callers. Removal happens in the follow-up change.

### Decision 7: EventBus memory object streams

Replace `asyncio.Queue[EventEnvelope | None]` in `EventBus` subscriber management with `anyio.create_memory_object_stream`.

```python
class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[tuple[MemoryObjectSendStream[EventEnvelope], str]]] = {}
        self._lock = anyio.Lock()
        self._replay_buffers: dict[str, deque[EventEnvelope]] = {}

    async def subscribe(self, session_id: str, scope: str = "session") -> MemoryObjectReceiveStream[EventEnvelope]:
        send, receive = anyio.create_memory_object_stream[EventEnvelope](max_buffer_size=1000)
        async with self._lock:
            # Replay historical events
            for envelope in self._replay_buffers.get(session_id, []):
                send.send_nowait(envelope)
            self._subscribers.setdefault(session_id, []).append((send, scope))
        return receive

    async def unsubscribe(self, session_id: str, receive: MemoryObjectReceiveStream) -> None:
        async with self._lock:
            self._subscribers[session_id] = [
                (s, sc) for s, sc in self._subscribers.get(session_id, []) if s is not send_for(receive)
            ]
        receive.close()  # EndOfStream to consumer

    async def publish(self, session_id: str, event: Any) -> None:
        envelope = EventEnvelope(source_session_id=session_id, event=event)
        async with self._lock:
            self._replay_buffers.setdefault(session_id, deque(maxlen=100)).append(envelope)
            targets = list(self._subscribers.items())
        for subscriber_sid, subscribers in targets:
            for send, scope in subscribers:
                if self._should_receive(session_id, subscriber_sid, scope):
                    try:
                        await send.send(envelope)  # Backpressure: blocks if buffer full
                    except anyio.ClosedResourceError:
                        pass  # Subscriber already disconnected
```

**Key changes from current architecture**:
- `subscribe()` returns a `MemoryObjectReceiveStream` — no more `asyncio.Queue`
- `unsubscribe()` closes the receive stream — consumer gets `EndOfStream` instead of `None` sentinel
- `publish()` uses `await send.send()` with backpressure — no more drop-oldest fallback
- `ClosedResourceError` replaces manual dead-queue detection

**Backward compatibility**: The `ProtocolEventConsumerMixin._event_consumer_loop()` changes from `await queue.get()` with `None` check to `async for envelope in receive_stream:` with `EndOfStream` exit. Both support `ConsumerShutdown` via the existing exception mechanism.

**Buffer size**: The memory stream's `max_buffer_size` defaults to `0` (unbuffered — rendezvous). We set `max_buffer_size=1000` to match the current `DEFAULT_QUEUE_MAXSIZE` while adding backpressure when the buffer is full.

**Backpressure strategy (hybrid)**: Pure backpressure (`await send.send()` blocking) risks one slow subscriber blocking `publish()` for ALL subscribers. Pure drop-oldest silently loses events. The hybrid approach:
1. `send.send()` is wrapped in `anyio.fail_after(0.1)` — matching the current polling interval
2. On timeout: call `send.send_nowait(envelope)` which raises `WouldBlock` if the buffer is still full. Catch `WouldBlock` — this counts as a "dropped event." Log a warning.
3. After 3 consecutive timeouts (3 dropped events): close the send stream and drop the subscriber entirely. Log an error.
4. Track consecutive timeout count per subscriber; reset to 0 on successful `send()`

This preserves the existing degradation mode (event drop is less destructive than subscriber drop) while adding backpressure for short bursts.

**Parallel publishing**: To prevent one slow subscriber from delaying delivery to others, `publish()` fans out to subscribers concurrently using `anyio.create_task_group()` instead of sequential iteration under lock. The lock is only held for the replay buffer append + subscriber list snapshot, then released before concurrent `send()` calls.

### Decision 8: merge_queue_into_iterator deprecation

`merge_queue_into_iterator` (`src/agentpool/utils/streams.py`) is removed and its sole caller in `ACPAgent._run_stream_once()` is refactored.

**Why remove, not fix**: The function has exactly one caller and is the documented cause of critical bugs:
- `RuntimeError: Attempted to exit cancel scope in a different task`
- `ValueError: Token was created in a different Context`
- Agent state corruption after `break` from iteration

These bugs arise from the inherent complexity of merging a `GeneratorExit`-sensitive context manager with cross-task async iteration. The function's 144 lines of manual `shutdown_event`, `end_signaled`, `asyncio.shield(gather(...))`, and `is_generator_exit_cleanup` checks attempt to work around fundamental asyncio limitations that `anyio.TaskGroup` solves natively.

**Replacement pattern**:

```python
# Before (buggy):
async with merge_queue_into_iterator(poll_acp_events(), event_source) as merged:
    async for event in merged:
        ...

# After (using TaskGroup + memory stream):
send, receive = anyio.create_memory_object_stream[Any]()
async with anyio.create_task_group() as tg:
    tg.start_soon(_forward_acp_events, poll_acp_events(), send.clone())
    tg.start_soon(_forward_queue_events, event_source, send.clone())
    send.close()
    async with receive:
        async for event in receive:
            ...
```

**Forwarder coroutines**:
```python
async def _forward_acp_events(acp_iter: AsyncIterator, send: MemoryObjectSendStream) -> None:
    try:
        async for event in acp_iter:
            await send.send(event)
    except anyio.ClosedResourceError:
        pass
    finally:
        await send.aclose()

async def _forward_queue_events(queue: asyncio.Queue, send: MemoryObjectSendStream) -> None:
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await send.send(event)
    except anyio.ClosedResourceError:
        pass
    finally:
        await send.aclose()
```

**Why TaskGroup fixes the bugs**: `TaskGroup.__aexit__` runs in the task that entered the context manager. When the consumer breaks from iteration (GeneratorExit), `TaskGroup.__aexit__` cancels all child tasks within the same task context — no cross-task scope switching. The `memory_object_stream`'s `ClosedResourceError` propagates cleanly through both cancellation and normal exit paths.

### Decision 9: GraphStreamingAdapter TaskGroup alignment

`GraphStreamingAdapter` (`src/agentpool/messaging/streaming_adapter.py`) currently follows the same `asyncio.create_task()` + `asyncio.shield()` + `asyncio.wait_for()` pattern as `RunExecutor`. It is refactored to use `TaskGroup`, creating a shared pattern across both classes.

**Current pattern** (both classes):
```python
self._iteration_task = asyncio.create_task(background_iteration())
try:
    while True:
        event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
        ...
finally:
    task.cancel()
    await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
```

**Refactored pattern**:
```python
async with anyio.create_task_group() as tg:
    tg.start_soon(background_iteration)
    while True:
        event = await event_queue.get()  # No timeout needed; cancellation via TaskGroup
        if event is None:
            break
        yield event
# TaskGroup.__aexit__ guarantees background_iteration is done
```

**Key difference**: `TaskGroup.__aexit__` blocks until ALL child tasks have completed or been cancelled. This eliminates the need for `asyncio.shield()` — the iteration task's cleanup (`finally` block) runs before `__aexit__` returns. The 0.1s timeout polling is no longer needed because cancellation is detected through the `TaskGroup` rather than periodic polling.

**Timeout polling removal rationale**: The polling pattern existed to detect `current_task().cancelling() > 0` (Python 3.13's API for checking if the current task is being cancelled). With `TaskGroup`, cancellation propagates directly through `CancelledError` raised by `event_queue.get()` — the task is cancelled by the group, so `CancelledError` is raised immediately at the next `await` point. No polling needed.

## Open Questions

1. **Should `SessionState.cancel_scope` be exposed via the public API?** Currently scopes are internal. External callers (e.g., protocol handlers) use `stop_event_consumer()` or `close_session()`. The scope itself should remain internal to prevent misuse.

2. **Timeout for TaskGroup exit during shutdown?** Currently `SessionController.close_session()` uses a 30s timeout for `turn_lock`. Should the TaskGroup exit also have a timeout? Proposed: 5s for consumer tasks, 30s for agent runs (matching existing behavior).

3. **EventBus memory stream buffer size vs current queue behavior?** RESOLVED: Hybrid approach — 0.1s timeout per send, event-drop fallback for first 2 timeouts, subscriber-drop on 3rd consecutive timeout. See Decision 7.

4. **Should `asyncio.Queue` in `GraphStreamingAdapter._event_queue` and `RunExecutor.event_queue` also migrate to memory streams?** The queue is used for internal single-producer-single-consumer communication within one `TaskGroup`. `asyncio.Queue` is simpler for this case since no cross-subscriber coordination is needed. Keeping `asyncio.Queue` avoids unnecessary churn.

5. **How to handle the 7 non-BaseServer TaskManager instances?** They are explicitly out of scope for this change. The `TaskManager` class is marked as deprecated but NOT removed. A follow-up change will migrate each instance. See "Out of Scope for This Change" section above.
