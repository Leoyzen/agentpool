## Context

`BaseAgent.run_stream()` Path B (standalone mode, lines ~1082-1169 of `base_agent.py`) creates an `anyio.create_task_group()` to run a background event producer (`_native_runner` or `_non_native_publisher`) while concurrently consuming events from an EventBus subscription and yielding them to the caller. The `yield` statement at line 1154 sits **inside** the `async with anyio.create_task_group()` context manager.

`ACPAgent._stream_events()` (acp_agent.py:527-561) has the identical pattern: `yield` at line 561 inside `async with anyio.create_task_group()` at line 527. (Note: the task group is in `_stream_events()`, not `_run_stream_once()` — the base class `_run_stream_once()` at base_agent.py:1171 calls `_stream_events()` at line 1279.)

AnyIO's `CancelScope` is task-affine: it records which task entered the scope and raises `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in` if `__aexit__` runs in a different task. When an async generator with `yield` inside a cancel scope is cleaned up (via `aclose()` or GC), the cleanup may execute in a different task context, triggering this error.

Meanwhile, `RunHandle.start()` (orchestrator/run.py:126-293) — which is the primary run lifecycle manager — already yields events **without** a task group:

```python
async def start(self, initial_prompt: str) -> AsyncGenerator[...]:
    while not self._closing:
        turn = agent.create_turn(...)
        async for event in turn.execute():
            await event_bus.publish(self.session_id, event)
            yield event  # ← NO task group, NO cancel scope
            if isinstance(event, (StreamCompleteEvent, RunErrorEvent)):
                break
```

The bug is **only** in Path B's redundant task group wrapper. Path A (SessionPool mode) already delegates to `RunHandle.start()` safely.

Additionally, `RunHandle._cancel_fn` (run.py:113) is declared but never assigned. `cancel()` always falls through to fire-and-forget `agent._interrupt()` at run.py:443-447. The `run_ctx.cancelled` flag is checked in 26 locations across 7 files and must be preserved.

## Goals / Non-Goals

**Goals:**
- Eliminate `yield` inside `anyio.create_task_group()` in `BaseAgent.run_stream()` Path B and `ACPAgent._run_stream_once()`
- Path B delegates to `RunHandle.start()` — which already has no task group and no cancel scope issue
- Wire `_cancel_fn` to `agent._interrupt()` so subclass-specific cancellation works correctly
- Preserve `run_ctx.cancelled` flag for all 26 cooperative cancellation checks
- Remove `_interrupt_tasks` set (subsumed by `_cancel_fn` wiring)

**Non-Goals:**
- Adding structured cancellation (task group + cancel scope) to `RunHandle` — cooperative cancellation via flags + `_interrupt()` is sufficient for all current scenarios
- Adding queue/stream-based event forwarding — `RunHandle.start()` already yields directly, no intermediary needed
- Changing public API signatures (`run()`, `run_stream()`, `cancel()`, `steer()`, `followup()`)
- Fixing the 4 assertion failures in `test_agent_basics.py` (separate run/turn refactoring issues)
- Migrating `test_break_behavior.py` to pytest (diagnostic suite with its own `main()` entry point)
- Removing Path B entirely (standalone mode) — requires migrating ~60 test files, ~15 source files, ~20 docs; future spec
- Fixing `@method_spawner` / `AsyncIteratorExecutor` in `anyenv` (compiled Cython, external dependency)

## Decisions

### D1: Path B delegates to `RunHandle.start()`

**Choice**: Path B in `BaseAgent.run_stream()` removes its `async with anyio.create_task_group()` block entirely. Instead, it creates a lightweight `RunHandle` with a synthetic `SessionState` (when SessionPool is unavailable), iterates `run_handle.start()` for events, and yields them — outside any cancel scope.

**Rationale**: `RunHandle.start()` already has no task group. Its `yield` at run.py:198 is not inside any `async with create_task_group()`. Path A (SessionPool mode) already delegates to `RunHandle.start()` safely. Path B just needs to do the same — create a minimal `RunHandle` and iterate it.

**What gets removed**:
- The `async with anyio.create_task_group() as tg:` block (lines 1084-1159)
- The `_native_runner` / `_non_native_publisher` background task setup
- The EventBus subscription consumer loop (`async for envelope in stream:`)
- The `tg.cancel_scope.cancel()` call

**What replaces it**:
```python
# Create minimal RunHandle with synthetic SessionState
run_handle = RunHandle(
    run_id=...,
    session_id=effective_session_id,
    agent_type=self.name,
    agent=self,
    event_bus=local_bus,
    session=synthetic_session,
)
gen = run_handle.start(initial_prompt)
try:
    async for event in gen:
        yield event
        if isinstance(event, (StreamCompleteEvent, RunErrorEvent)):
            break
finally:
    await gen.aclose()  # MANDATORY: Python's async for does NOT auto-close
```

**Why `finally: await gen.aclose()` is mandatory**: Python's `async for` does NOT automatically call `aclose()` on the async iterator when the loop exits (whether via `break`, `return`, exception, or GC). Without the explicit `finally` block, if the consumer abandons the `run_stream()` generator, `run_handle.start()` stays suspended inside `async with session.turn_lock:` forever — the lock is never released. The `finally` block ensures `GeneratorExit` is thrown into `start()`, which propagates through the `async with session.turn_lock:` (releasing the lock) and the `finally` block in `start()` (setting status to done, setting complete_event).

**Why no queue/stream needed**: The `yield` in `run_handle.start()` is already safe — no cancel scope wraps it. The `yield` in Path B's `run_stream()` delegates to `start()` via `async for`, which is also outside any cancel scope. No intermediary data structure needed.

**Lifecycle concerns from `_run_stream_once()`**: The base class `_run_stream_once()` (base_agent.py:1171-1346) handles pre-run hooks, post-run hooks, `message_received`/`message_sent` signals, user message saving, connection routing, and persistence. However, `_run_stream_once()` is ONLY called from Path B (lines 1094 and 1124) — it is never called from Path A (SessionPool mode). Path A already delegates to `RunHandle.start()` → `turn.execute()` without calling `_run_stream_once()`, and Path A works correctly. This means the lifecycle concerns are handled elsewhere (PydanticAI capabilities/hooks injected into the agentlet, the Turn abstraction, or the SessionController). Path B delegating to `RunHandle.start()` is consistent with Path A — no lifecycle concerns are lost that Path A doesn't also lack.

**drain_and_merge behavioral change**: Current Path B subscribes to EventBus and uses `drain_and_merge(stream)` which coalesces consecutive same-type events. `RunHandle.start()` yields events directly from `turn.execute()` without coalescing. This means lower latency (good) and no event coalescing for the primary consumer (behavioral change). Events from other EventBus publishers (e.g., subagent spawn events) won't be yielded to the primary consumer in standalone mode — they're only available to EventBus subscribers. This is consistent with Path A behavior and acceptable for standalone mode where subagent events are rare.

**EventBus cleanup**: The current Path B has cleanup in `_native_runner`/`_non_native_publisher` finally blocks that close/unsubscribe the local EventBus. `RunHandle.start()`'s finally block does NOT handle EventBus cleanup. The revised Path B must handle EventBus cleanup in its own `finally` block (after `gen.aclose()`), using the existing `_created_local_bus` flag to decide whether to close the session or just unsubscribe.

**Alternatives considered**:
- **A: Queue-based pattern** — Background task owns task group, pushes events to `asyncio.Queue`; `start()` drains queue. Rejected: unnecessary complexity. `RunHandle.start()` already yields safely without a task group.
- **B: AnyIO memory streams** — Same as queue but with `create_memory_object_stream()`. Rejected: same unnecessary complexity.
- **C: Move task group into `RunHandle.start()`** — Rejected by both Oracle and Momus: `start()` is an async generator with `yield`, recreating the same anti-pattern.
- **D: Remove Path B entirely** — Rejected for now: ~60 test files, ~15 source files, ~20 docs depend on standalone mode. Future spec.

### D2: ACP agent adopts same pattern

**Choice**: `ACPAgent._stream_events()` (acp_agent.py:527-561) removes its `async with anyio.create_task_group()` block. The two child tasks (`_forward_acp_events`, `_forward_secondary_events`) are restructured.

**Rationale**: The ACP agent has the identical `yield`-inside-`create_task_group()` pattern in `_stream_events()` (not `_run_stream_once()` — `_stream_events()` is called from the base class `_run_stream_once()` at line 1279). If left unfixed, ACP agents will hit the same cancel scope bug on consecutive runs.

**Implementation approach — two options**:

**Option A (preferred): Remove TG from `_stream_events()`, use `asyncio.create_task()` with manual cleanup.** Replace the task group with plain `asyncio.create_task()` calls for `_forward_acp_events` and `_forward_secondary_events`, storing task references to prevent GC. Clean up in a `finally` block that cancels and awaits both tasks. This preserves the existing event forwarding and tool metadata enrichment (`ToolResultMetadataEvent` handling at lines 533-552) without requiring `ACPTurn.execute()` to work.

**Option B (future): Route ACP through `RunHandle.start()` → `ACPTurn.execute()`.** This would be the cleanest approach, but `ACPAgent.create_turn()` (line 664-668) has an explicit TODO: "ACPAgentAPI does not implement ACPClientProtocol fully — it lacks `stream_events()` and `get_messages()`." `ACPTurn.execute()` calls both at lines 159 and 174, which would raise `AttributeError`. An adapter wrapping `ACPAgentAPI` with async futures / notification registry is needed first. This is a prerequisite task outside the scope of this change.

**Selected approach**: Option A. It fixes the cancel scope bug without depending on the unimplemented `ACPTurn.execute()` adapter. The `finally` block pattern:
```python
forward_tasks: list[asyncio.Task[None]] = []
try:
    forward_tasks.append(asyncio.create_task(_forward_acp_events()))
    forward_tasks.append(asyncio.create_task(_forward_secondary_events()))
    async for event in receive_stream:
        ...
        yield output_event
finally:
    for task in forward_tasks:
        task.cancel()
    for task in forward_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
```

**Secondary event forwarding preserved**: The `_forward_secondary_events` coroutine subscribes to the EventBus and forwards events (including `ToolResultMetadataEvent` used for tool call enrichment at lines 533-552). This functionality is preserved in Option A because the forwarding tasks run alongside the consumer loop, just without a task group.

### D3: Wire `_cancel_fn` and preserve cooperative cancellation

**Choice**: 
1. In `RunHandle.start()`, set `self._cancel_fn = self._create_cancel_fn()` where `_create_cancel_fn()` schedules `agent._interrupt(self.run_ctx)` as a fire-and-forget task.
2. `cancel()` continues to: set `run_ctx.cancelled = True`, set `_idle_event.set()`, call `_cancel_fn()` if set.
3. Remove `_interrupt_tasks` field — `_cancel_fn` handles fire-and-forget task creation internally. The task reference should be stored as `self._interrupt_task` (singular `asyncio.Task | None`) to prevent GC. In CPython, the event loop keeps references via `_all_tasks`, but explicit storage is safer and clearer.

**Rationale**: 
- `_cancel_fn` is currently dead code (declared at run.py:113, read at run.py:435, never assigned). By wiring it, we centralize the interrupt logic.
- `run_ctx.cancelled` must be preserved — it's checked in 26 locations: `run.py:174,219`, `native_agent/turn.py:162,169,188,220,269`, `acp_agent.py:536,575`, `base_agent.py:625,643,1061,1268,1580`, `core.py:2380`, `hooks/agent_hooks.py:350`.
- `agent._interrupt()` is essential: ACP sends `CancelNotification` to remote server, native cancels `_iteration_task` running blocking LLM API call.
- Cooperative cancellation (flags + `_interrupt()`) is sufficient for all current scenarios. No structured cancellation (cancel scope) needed.

### D4: Path B fallback creates synthetic SessionState

**Choice**: When `run_ctx.session_pool` is `None` (standalone mode), Path B creates a minimal `RunHandle` with a synthetic `SessionState` — no session store, no MCP connection pool, just a local EventBus and turn lock.

**Rationale**: This ensures Path B uses the same `RunHandle.start()` code path as Path A, just without the full SessionPool infrastructure. The local EventBus created in Path B is attached to the `RunHandle` so events flow through the same `turn.execute()` → `event_bus.publish()` → `yield` mechanism.

**Required attributes**: `RunHandle.start()` accesses `session.turn_lock` (line 156) and `session.input_provider` (lines 187, 191). The synthetic SessionState must have:
- `turn_lock`: A real `asyncio.Lock` — `start()` acquires it at line 156
- `input_provider`: `None` — `start()` checks `if session.input_provider is not None` at line 187
- `session_id`: The effective session ID string
- `agent_name`: The agent's name

All other `SessionState` fields can use their defaults (the dataclass has defaults for most fields).

## Risks / Trade-offs

- **[Risk] Path B behavior change** → Path B previously managed its own EventBus subscriptions and background runner. Moving to `RunHandle` changes the EventBus lifecycle. Mitigation: `_created_local_bus` flag already exists; Path B's `finally` block must close the local bus after `gen.aclose()`.

- **[Risk] drain_and_merge behavior change** → Current Path B uses `drain_and_merge(stream)` which coalesces consecutive same-type events. Direct delegation to `RunHandle.start()` skips this. Mitigation: `turn.execute()` should already produce well-ordered events. Verify no tests depend on coalesced events in standalone mode (Task 4.4).

- **[Risk] Cooperative cancellation latency** → Without structured cancellation (`cancel_scope.cancel()`), cancellation only takes effect at the next `run_ctx.cancelled` check point. Mitigation: `agent._interrupt()` directly cancels the blocking operation (native: `_iteration_task`; ACP: sends `CancelNotification`), providing immediate interruption regardless of check points.

- **[Trade-off] Path B creates a `SessionState` even for stateless runs** → Slight overhead for agents with `session=False`. Mitigation: The synthetic `SessionState` is minimal (no store, no MCP connection pool).

- **[Trade-off] `_interrupt_tasks` field removed** → Breaking change for any code that references this field. Mitigation: grep confirms no external references outside `run.py`. Replaced with singular `_interrupt_task: asyncio.Task | None` for GC safety.

- **[Trade-off] ACP agent uses `asyncio.create_task()` instead of task group** → Loses structured concurrency for ACP event forwarding. Mitigation: `finally` block cancels and awaits both tasks. The tasks are fire-and-forget forwarders, not critical-path computation.

- **[Known limitation] `ACPTurn.execute()` not functional** → `ACPAgentAPI` lacks `stream_events()` and `get_messages()` (TODO at acp_agent.py:664). D2 uses Option A (fix `_stream_events()`) instead of routing through `ACPTurn.execute()`. Future spec needed to implement the adapter.

## Rollback Strategy

If delegating Path B to `RunHandle.start()` introduces regressions:
1. Revert Path B to direct `async with create_task_group()` (accept the cancel scope bug for standalone mode)
2. Keep `_cancel_fn` wiring (D3) — strictly an improvement over dead code
3. Keep `run_ctx.cancelled` preservation (D3) — required for 26 checks
4. Keep ACP agent fix (D2) — same pattern, same fix

This rollback preserves the P0 fixes from rounds 1-2 while accepting the RC-1 cancel scope bug remains for Path B standalone mode.
