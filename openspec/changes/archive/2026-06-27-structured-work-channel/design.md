## Context

### Problem Space

Background tasks spawned during an agent turn (subagents, code review, web search) complete asynchronously. Their results need to reach the agent **within the same turn** (before `StreamCompleteEvent`), not in a new turn that the ACP client never sees.

### Current Architecture

```
RunExecutor.execute()
  ├─ agent_iteration_task()     ← drives PydanticAI agent_run.next()
  │   ├─ agent_run active       ← steer() can enqueue(asap) → mid-turn injection ✓
  │   └─ finally: agent_run=None ← steer() falls to _post_turn_injections → new turn ✗
  ├─ (gap: no wait for bg tasks)
  └─ publish StreamCompleteEvent ← ACP sends end_turn, client stops listening
```

**Two windows for background task completion:**

| Window | agent_run | steer() behavior | Result |
|--------|-----------|------------------|--------|
| During iteration | active | `enqueue(asap)` → PydanticAI drains at `before_model_request` | ✓ Mid-turn injection works |
| After iteration, before StreamCompleteEvent | None (cleared in `finally`) | Falls to `_post_turn_injections` or `receive_request()` | ✗ Lost — new turn client never sees |

The gap between "iteration exits" and "StreamCompleteEvent published" is where background task results are lost.

### Constraints

1. **ACP v1**: `StopReason` is a closed `oneOf` enum (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`). No custom stop reasons. Turn must emit `end_turn` only when truly done.
2. **ACP v2** (future): Open `anyOf` with `"other"` string type, `_` prefix for implementation-specific extensions. Will support `_deferred_pending` for split turns.
3. **No timeout**: User explicitly rejected timeout — "超时显得很奇怪". Turn stays alive until all background work completes or session closes.
4. **Mid-turn injection required**: Background task results must be injectable while the agent is still iterating (via `agent_run.enqueue(asap)`).

## Goals / Non-Goals

**Goals:**
- Close the post-iteration gap: wait for background tasks before `StreamCompleteEvent`
- Support mid-turn injection (steer during active iteration — already works, preserve it)
- Support post-iteration re-iteration: if background task result arrives after iteration exits, re-iterate with it as a new prompt within the same `execute()` call
- No timeout — blocking `Event.wait()`, session close as sole exit
- Forward-compatible with ACP v2 `_deferred_pending` stop reason
- Minimal changes to `run_loop` and `_process_queued_work` (only `steer()` routing and `close_session()` unblock)

**Non-Goals:**
- No MemoryObjectStream or work stream (overengineered for this problem)
- No TurnState state machine (TOCTOU is not the issue — the issue is timing)
- No changes to `steer()`/`followup()` routing for the active-iteration case
- No changes to pydantic-ai's `PendingMessageDrainCapability`
- No new event types

## Decisions

### Decision 1: Counter + Event on AgentRunContext

Add four fields to `AgentRunContext`:

```python
def _create_set_event() -> asyncio.Event:
    """Create an asyncio.Event that is initially set (0 pending = complete)."""
    e = asyncio.Event()
    e.set()
    return e

class AgentRunContext:
    # ... existing fields ...
    pending_background_tasks: int = 0
    background_tasks_complete: asyncio.Event = field(default_factory=_create_set_event)
    queued_steer_messages: list[str] = field(default_factory=list)
    steer_callback: Callable[[str, str], Awaitable[bool]] | None = None
```

**Key points:**
- `background_tasks_complete` is **initially set** via `_create_set_event()` factory (not `asyncio.Event()` which defaults to unset). When a tool increments the counter, the event is cleared. When the counter decrements to 0, the event is set.
- `steer_callback` is set by `TurnRunner` when creating the run. Tools call `await run_ctx.steer_callback(session_id, message)` instead of needing a direct `TurnRunner` reference. This solves the access path problem (tools have `run_ctx` via `AgentContext`, but no path to `TurnRunner`).

**Why not a work stream?** The problem is not message routing (steer already routes correctly during iteration). The problem is a timing gap. A counter + event is the minimal primitive to close that gap. A work stream would require restructuring `run_loop`, `steer()`, `followup()`, and `_process_queued_work` — all for a problem that only exists in the ~0ms window between iteration exit and `StreamCompleteEvent`.

### Decision 2: Re-iteration loop in RunExecutor.execute()

After `agent_iteration_task` completes and before `StreamCompleteEvent`, add a loop. **The loop must be inserted AFTER the `if response_msg is None` early return** (line ~379) and BEFORE the main `StreamCompleteEvent` publication (line ~381):

```python
# After agent_iteration_task completes, response_msg is ready:

# Early return: cancelled before any response was produced
if response_msg is None:
    response_msg = _make_interrupted_msg()
    await event_bus.publish(
        session_id,
        StreamCompleteEvent(message=response_msg, cancelled=True),
    )
    return response_msg

# === RE-ITERATION LOOP STARTS HERE ===
while True:
    if run_ctx.cancelled:
        break

    # Wait for background tasks if any are pending
    if run_ctx.pending_background_tasks > 0:
        logger.info(
            "Waiting for background tasks before StreamCompleteEvent",
            pending=run_ctx.pending_background_tasks,
        )
        await run_ctx.background_tasks_complete.wait()
        if run_ctx.cancelled:
            break

    # Check for steer messages queued during the wait
    if not run_ctx.queued_steer_messages:
        break  # All clear — ready for StreamCompleteEvent

    # Re-iterate with queued steer messages as new prompts
    steer_msgs = run_ctx.queued_steer_messages.copy()
    run_ctx.queued_steer_messages.clear()
    # Reset counter for new iteration (bg tasks from new iteration)
    run_ctx.pending_background_tasks = 0
    run_ctx.background_tasks_complete.set()

    logger.info("Re-iterating with queued steer messages", count=len(steer_msgs))

    # Update message history with prior iteration's messages
    if iteration_messages:
        history = iteration_messages

    # Run a new iteration with steer messages
    iteration_error = None
    iteration_messages = None
    async with anyio.create_task_group() as tg:
        tg.start_soon(agent_iteration_task, steer_msgs)

    # CancelledError during re-iteration propagates to execute()'s
    # existing except asyncio.CancelledError handler (line ~397),
    # which publishes StreamCompleteEvent(cancelled=True) and re-raises.
    # No additional error handling needed here.
    if iteration_error is not None:
        raise iteration_error

    if response_msg is None:
        response_msg = _make_interrupted_msg()
        break

# Publish StreamCompleteEvent only after all background work is done
await event_bus.publish(
    session_id,
    StreamCompleteEvent(message=response_msg, cancelled=run_ctx.cancelled),
)
return response_msg
```

**Key properties:**
- `StreamCompleteEvent` is published **once**, after all background work and re-iterations are complete
- Each re-iteration can spawn new background tasks (counter resets, loop continues)
- No timeout — `event.wait()` blocks indefinitely
- Session close: `close_session()` sets `run_ctx.cancelled = True` and `background_tasks_complete.set()` → loop breaks → `StreamCompleteEvent(cancelled=True)` (see Decision 5)
- **Message history propagation**: `agent_iteration_task` captures `agent_run.all_messages()` **inside** the `async with` block (before `__aexit__` is called — see Decision 2a), stores it in `iteration_messages` (nonlocal). The wait loop updates `history` from this before each re-iteration.
- **CancelledError during re-iteration** propagates to `execute()`'s existing `except asyncio.CancelledError` handler (line ~397), which publishes `StreamCompleteEvent(cancelled=True)` and re-raises. No additional error handling needed in the wait loop.
- **Known TOCTOU**: After the wait loop breaks (counter == 0, queue empty) and before `execute()` returns, a `steer()` call could route to `queued_steer_messages` but nobody reads it. Window is microseconds, only affects non-background-task steer calls. Accepted as minor — background task steer always happens before counter decrement, so the wait loop can't have exited yet.

### Decision 2a: `iteration_messages` capture inside `async with` block

`iteration_messages` must be captured **inside** the `async with agentlet.iter(...)` block, not after it. `AgentRun.__aexit__()` may clean up internal state, making `all_messages()` unreliable after exit.

```python
async def agent_iteration_task(steer_prompts: list[str] | None = None) -> None:
    nonlocal iteration_error, response_msg, iteration_messages
    # ...
    prompts_to_use = steer_prompts if steer_prompts is not None else prompts
    try:
        async with agentlet.iter(
            prompts_to_use,
            deps=agent_deps,
            message_history=history,
            usage_limits=self._agent._default_usage_limits,
        ) as agent_run:
            if self._run_handle is not None:
                self._run_handle.active_agent_run = agent_run
            # ... iteration loop ...
            # Capture messages BEFORE exiting the async with block
            iteration_messages = agent_run.all_messages()

        # Build response message (existing code)
        # ...
```

If an exception occurs inside the `async with` block, `iteration_messages` may not be captured. Re-iteration with message history only works for the success path. This is acceptable — error paths don't need re-iteration.

### Decision 3: steer() routing for post-iteration window

When `steer()` is called and `agent_run is None` (iteration has exited), check if `run_ctx` is still alive (RunExecutor in wait loop):

```python
# In TurnRunner.steer(), native agent branch, agent_run is None:
if run_handle is not None and run_handle.run_ctx is not None:
    run_ctx = run_handle.run_ctx
    if not run_ctx.completed:
        # RunExecutor is still in execute() — queue for re-iteration
        run_ctx.queued_steer_messages.append(message)
        return False

# RunExecutor has exited — fall through to existing _post_turn_injections
self._post_turn_injections.setdefault(session_id, []).append(message)
# ... existing auto-resume logic ...
return False
```

This is a **~5 line change** in `steer()`. The existing `_post_turn_injections` path is preserved as fallback for when `execute()` has already returned.

**Pre-iteration window**: If `steer()` is called between run start and `agent_run` being set (pre-iteration), the message goes to `queued_steer_messages` instead of `_post_turn_injections`. This is actually **better** — the message is processed as re-iteration within the same `execute()` call (single `StreamCompleteEvent`), instead of in a new turn that the ACP client might not see.

**`steer_callback` wiring**: `TurnRunner` sets `run_ctx.steer_callback = lambda sid, msg: self.steer(sid, msg)` when creating the `RunHandle`. Tools call `await run_ctx.steer_callback(session_id, message)` instead of needing a direct `TurnRunner` reference.

### Decision 4: Tool integration pattern

Tools that spawn background tasks use a simple pattern with `steer_callback`:

```python
async def my_tool(ctx: AgentContext):
    run_ctx = ctx.run_ctx

    async def bg_task():
        try:
            result = await do_work()
            # Use steer_callback to deliver result back to the agent
            if run_ctx.steer_callback is not None:
                await run_ctx.steer_callback(run_ctx.session_id, f"Background result: {result}")
        finally:
            run_ctx.pending_background_tasks -= 1
            if run_ctx.pending_background_tasks == 0:
                run_ctx.background_tasks_complete.set()

    run_ctx.pending_background_tasks += 1
    run_ctx.background_tasks_complete.clear()
    asyncio.create_task(bg_task())

    return "Background task started"
```

This is opt-in: tools that don't spawn background tasks are unaffected. The counter defaults to 0, `background_tasks_complete` defaults to set, and the wait loop is a no-op.

**Counter safety**: The `-= 1` and `if == 0` check are synchronous (no `await` between them), so they're atomic in Python's single-threaded asyncio. No lock needed. The counter cannot go negative because: (1) increment always happens before `create_task`, (2) decrement always happens in `finally`, (3) the wait loop only resets to 0 after all tasks have completed (counter already 0).

**Existing tools**: This is opt-in. Existing background task tools (e.g., `subagent_tools.py` which writes results to files) are NOT required to adopt this pattern. Task 5.2 only adds the counter increment/decrement — it does NOT change the result delivery mechanism.

### Decision 5: Session close unblocks wait

**NOT in `_run_turn_unlocked()`'s finally block** — the finally block runs AFTER `execute()` returns, so it cannot unblock the wait loop. Setting `cancelled = True` there would incorrectly mark every normal completion as cancelled.

Instead, unblocking must happen in `SessionController.close_session()`, **BEFORE** the existing 30-second `complete_event.wait()`:

```python
# In SessionController.close_session(), BEFORE the 30s wait:
session.closing = True  # existing
if run_handle is not None and run_handle.run_ctx is not None:
    run_handle.run_ctx.cancelled = True
    run_handle.run_ctx.background_tasks_complete.set()
# Then existing: await asyncio.wait_for(run_handle.complete_event.wait(), timeout=30.0)
```

This immediately unblocks the `event.wait()` in `execute()`, causing the wait loop to break on the `cancelled` check and publish `StreamCompleteEvent(cancelled=True)`. Then `execute()` returns, `complete_event` fires, and `close_session()` proceeds without waiting 30 seconds.

**`session.is_closing` vs `session.closing` race**: Setting `run_ctx.cancelled = True` before the wait also fixes the race where `steer()` checks `session.is_closing` (not yet set) and routes to `queued_steer_messages` during shutdown. With `cancelled = True`, the wait loop breaks immediately and `StreamCompleteEvent(cancelled=True)` is published, preventing re-iteration during shutdown.

### Decision 6: V2 forward compatibility

When migrating to ACP v2:

1. **`_deferred_pending` stop reason**: If a background task is long-running and we want to split the turn (emit partial response, then continue), we can publish `StreamCompleteEvent` with a `_deferred_pending` stop reason instead of `end_turn`. The client knows more is coming. This is a future change — current design emits `end_turn` only when all work is done.

2. **Hybrid approach**: The counter + re-iteration loop is the default. For tools that need durable execution (Temporal/DBOS/Prefect integration via pydantic-ai's `DeferredTool`), the counter can be used alongside deferred tool results — the counter tracks non-durable background tasks, while deferred tools handle their own lifecycle.

3. **No API changes needed**: The `pending_background_tasks` counter, `queued_steer_messages` list, and `steer_callback` are internal to `AgentRunContext`. No public API changes. V2 migration only changes the `StreamCompleteEvent` stop reason, which is already a field on the event.

## Risks / Trade-offs

- **[Background task never completes]** If a background task's `finally` block never executes (task silently dropped), `event.wait()` blocks forever. → Mitigation: Session close sets `cancelled=True` and `event.set()`. Background task result is lost, but the session can be closed cleanly. This is the user's explicit choice ("no timeout").

- **[Re-iteration complexity]** The re-iteration loop adds ~30 lines to `RunExecutor.execute()`. Each re-iteration creates a new `agentlet.iter()` call with the steer message as a prompt and the accumulated message history. → Mitigation: The loop is straightforward (while/break pattern), and re-iteration only happens when background tasks actually complete after iteration exits (rare in practice — most complete during iteration or before iteration starts).

- **[Multiple StreamCompleteEvents suppressed]** The re-iteration loop suppresses intermediate `StreamCompleteEvent`s — only the final one is published. This means the ACP client sees a single turn completion, which is the desired behavior. → No mitigation needed.

- **[Steer message ordering]** If multiple background tasks complete simultaneously, their steer messages are appended to `queued_steer_messages` in completion order. Re-iteration processes them as a single batch (all messages in one iteration). → This is correct behavior — the agent sees all results at once.

- **[Counter leak]** If a tool increments the counter but never decrements (bug in tool code), the wait loop hangs. → Mitigation: The `finally` pattern in the tool template ensures decrement. Tools that don't use the pattern are unaffected (counter stays at 0). Session close is the ultimate safety net.

- **[agentlet.iter() reusability]** The design assumes the same `agentlet` instance can have `.iter()` called multiple times. PydanticAI's `Agent.iter()` returns a new `AgentRun` each time, so this should work. Task 7.3 includes a verification step. If it doesn't work, `get_agentlet()` would need to be called before each re-iteration.

- **[TOCTOU after wait loop]** After the wait loop breaks (counter == 0, queue empty) and before `execute()` returns, a `steer()` call could route to `queued_steer_messages` but nobody reads it. Window is microseconds, only affects non-background-task steer calls. Accepted as minor — background task steer always happens before counter decrement.
