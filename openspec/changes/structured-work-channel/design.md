## Context

`TurnRunner.steer()` and `TurnRunner.followup()` route post-turn messages (background task results, inject prompt requests) based on TOCTOU-prone `is None` checks:

```python
run_id = session.current_run_id
if run_id is not None:
    agent_run = run_handle.active_agent_run
    if agent_run is not None:
        # → enqueue ASAP (in-turn)
    # agent_run is None → window state, queue for later
# run_id is None → idle, start new run
```

There are two race windows: **BOOTING** (run_id set, agent_run not yet started) and **TEARDOWN** (agent_run cleared, run_id not yet cleared). Both cause `steer()` to either recursively re-enter `receive_request()` (pre-fix, caused `RecursionError`) or schedule work that gets processed in a new `run_loop` after the original one exits (post-fix, causes ACP client to miss events).

Additionally, the current architecture uses **three separate queuing mechanisms** to route messages across these windows:
- `_post_turn_injections: dict[str, list[str]]` — steer messages for non-native agents
- `_post_turn_prompts: dict[str, list[tuple[...]]]` — followup messages
- `_safe_auto_resume` + `_trigger_auto_resume` — fire-and-forget tasks that drain these dicts

These manual dict queues lack backpressure, structural ordering guarantees, and natural lifecycle integration with `run_loop`.

## Goals / Non-Goals

**Goals:**
- Eliminate the TOCTOU race condition between `current_run_id` and `active_agent_run`
- Unify all three queuing mechanisms into a single structured channel
- Extend `run_loop` lifecycle to cover background task completion naturally
- Make steer/followup routing a simple `match on current state` rather than a cascade of `is None` checks
- Pass the existing red-flag test: `test_background_task_wakeup_within_turn`
- All existing steer/followup/auto-resume tests continue to pass with minimal adaptation

**Non-Goals:**
- No changes to the EventBus or agent run execution pipeline
- No changes to ACP, OpenCode, or other protocol converters
- No changes to `_run_turn_unlocked()` (the turn execution itself is unchanged)
- Minimal changes to `RunExecutor` — only `turn_state` transitions at existing `active_agent_run` set/clear points (no behavioral logic changes)
- No new event types or public API changes

## Decisions

### Decision 0: Scope — non-native agents keep agent-type branching
Non-native (ACP) agents do not use `RunExecutor` and therefore never reach `TurnState.RUNNING` (no `active_agent_run`). The `steer()` and `followup()` methods retain existing `agent_type` detection: native agents use the `match session.turn_state` pattern exclusively; non-native agents use `TurnState` to replace TOCTOU checks but keep `injection_manager.inject()` for the active-run case and fall through to work stream for idle/queue cases. `inject_prompt()` and `queue_prompt()` for non-native agents are updated to write to the work stream instead of the removed `_post_turn_injections` dicts.

### Decision 0a: BOOTING transition unconditionally in _run_turn_unlocked
`receive_request()` sets `session.current_run_id` before `_run_turn_unlocked` is called, so `_run_turn_unlocked`'s conditional `if _session.current_run_id is None` would skip the BOOTING transition. Fix: set `session.turn_state = BOOTING` unconditionally at the top of `_run_turn_unlocked` (after the lock is acquired, before any agent logic).

### Decision 0b: Session close closes the work stream
When `close_session()` is called, the work stream's send half MUST be closed (`work_send.aclose()`) to signal `EndOfStream` to consumers. This is added to both `SessionController.close_session()` and the session cleanup path in `SessionPool`.

### Decision 1: Replace dict queues with anyio.MemoryObjectStream

**Current state:** Three dict-based queues (`_post_turn_injections`, `_post_turn_prompts`, and the implicit "enqueue ASAP" path via `agent_run.enqueue()`).

**Proposed:** A single `anyio.create_memory_object_stream[WorkItem]` on `SessionState`:

```python
class SessionState:
    # ... existing fields ...
    work_send: anyio.abc.ObjectSendStream[WorkItem]
    work_receive: anyio.abc.ObjectReceiveStream[WorkItem]
```

`WorkItem` is a union type:
```python
@dataclass
class SteerItem:
    message: str
    kwargs: dict[str, Any] = field(default_factory=dict)

@dataclass
class FollowupItem:
    prompts: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)

type WorkItem = SteerItem | FollowupItem
```

**Why `MemoryObjectStream` over `asyncio.Queue` or `Event`:**

| Feature | MemoryObjectStream | asyncio.Queue | asyncio.Event |
|---------|-------------------|---------------|---------------|
| Backpressure | ✅ max_buffer_size | ✅ maxsize | ❌ |
| Typed items | ✅ send(WorkItem) | ✅ | ❌ |
| End-of-stream signaling | ✅ aclose() → EndOfStream | ❌ | ❌ |
| async for support | ✅ | ❌ | ❌ |
| Concurrent senders | ✅ | ✅ | ✅ |
| Structured concurrency | ✅ integrates with TaskGroup | ❌ | ❌ |

`MemoryObjectStream` provides exactly one `WorkItem` channel that can be consumed with `async for`, has built-in backpressure, and naturally signals session closure via `EndOfStream`.

### Decision 2: Explicit TurnState state machine

**Current state:** Two booleans checked independently (`current_run_id is None`, `active_agent_run is None`) creating 4 implicit state combinations, only 2 of which are valid — the other 2 are race windows.

**Proposed:** A single `TurnState` enum:

```python
class TurnState(enum.Enum):
    IDLE = "idle"          # No run in progress
    BOOTING = "booting"    # run_id set, agent_run not yet established
    RUNNING = "running"    # agent_run active and delivering events
    TEARDOWN = "teardown"  # agent_run released, run_id not yet cleared
```

Transitions:
```
IDLE → BOOTING: _run_turn_unlocked sets current_run_id
BOOTING → RUNNING: RunExecutor sets active_agent_run
RUNNING → TEARDOWN: RunExecutor clears active_agent_run
TEARDOWN → IDLE: _run_turn_unlocked clears current_run_id
```

**Why enum over booleans:** The 4 states map directly to what the code actually means:
- `IDLE` → start new run
- `BOOTING` / `TEARDOWN` → queue via work stream (race windows)
- `RUNNING` → enqueue directly via `agent_run.enqueue()`

### Decision 3: run_loop consumes from work stream instead of polling dicts

**Current state:** `run_loop` → `_run_turn_unlocked` → then `_process_queued_work` which drains dicts in a fixed-iteration loop.

**Proposed:** `run_loop` uses a consuming loop:

```python
async def run_loop(self, session_id: str, *initial_prompts: Any, **kwargs: Any) -> None:
    """Run a turn loop consuming from the work stream."""
    session, _was_created = await self.sessions.get_or_create_session(session_id)
    async with session.turn_lock:
        if session.is_closing:
            return
        try:
            # Initial turn with the provided prompts
            if initial_prompts:
                await self._run_turn_unlocked(session_id, *initial_prompts, **kwargs)

            # Consume from work stream until timeout or EndOfStream
            while True:
                if session.is_closing:
                    break
                try:
                    with anyio.move_on_after(self._max_work_timeout):
                        item = await session.work_receive.receive()
                except anyio.EndOfStream:
                    break
                # Timeout: move_on_after leaves scope without raising
                if anyio.current_effective_deadline() == float("inf"):
                    # No timeout triggered — we got an item
                    match item:
                        case SteerItem(msg, kw):
                            await self._run_turn_unlocked(session_id, msg, **kw)
                        case FollowupItem(prompts, kw):
                            await self._run_turn_unlocked(session_id, *prompts, **kw)
                else:
                    break  # Timeout — no more work expected
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run_loop failed")
        finally:
            # Signal completion
            run_id = session.current_run_id
            if run_id is not None:
                run_handle = self.sessions._runs.get(run_id)
                if run_handle is not None:
                    run_handle.complete_event.set()
```

Key changes from current:
- Removes `_process_queued_work` call — work consumption is inline
- Uses `anyio.move_on_after` instead of `asyncio.wait_for` (idiomatic anyio)
- `EndOfStream` from work stream signals session close
- `complete_event.set()` preserved in `finally` for backward compat

Key differences:
- No more `_process_queued_work` — work consumption is inline in the loop
- No more `_safe_auto_resume` or `_trigger_auto_resume` — the work stream is always consumed
- No more fixed-iteration auto-resume — timeout-based: 30s of no activity → exit

### Decision 4: steer/followup simplified to single-channel writes

**Current state:** `steer()` has 4-branch logic (native/non-native × active/idle). `followup()` has similar complexity.

**Proposed:** Both methods write to the work stream and return immediately:

```python
# Native agents:
async def steer(self, session_id, message, **kwargs):
    session = self.sessions.get_session(session_id)
    if session is None or session.agent is None or session.is_closing:
        return False
    match session.turn_state:
        case TurnState.RUNNING:
            run_id = session.current_run_id
            run_handle = self.sessions._runs[run_id]
            run_handle.active_agent_run.enqueue(message, priority="asap")
            return True
        case TurnState.IDLE:
            # Write to work stream AND start new run_loop so the
            # work is consumed immediately.  Without wake-up, the
            # SteerItem would sit in the stream buffer indefinitely.
            session.work_send.send_nowait(SteerItem(message=message, kwargs=kwargs))
            await self.sessions.receive_request(
                session_id, message, priority="steer", **kwargs
            )
            return False
        case _:  # BOOTING | TEARDOWN
            session.work_send.send_nowait(SteerItem(message=message, kwargs=kwargs))
            return False
```

For **non-native agents**, steer() retains agent-type branching but uses TurnState to replace the TOCTOU checks. The RUNNING case is only reachable if the non-native agent sets `active_agent_run` (via TurnRunner integration), which few do — so BOOTING/TEARDOWN handling mirrors the current queue-to-`_post_turn_injections` behavior.

`followup()` keeps the `when_idle` enqueue for RUNNING state to preserve PydanticAI's `PendingMessageDrainCapability` timing, but writes to the work stream for all other states:

```python
async def followup(self, session_id, prompts, **kwargs):
    session = self.sessions.get_session(session_id)
    if session is None or session.agent is None or session.is_closing:
        return
    agent = session.agent
    if session.turn_state == TurnState.RUNNING:
        run_id = session.current_run_id
        run_handle = self.sessions._runs[run_id]
        if run_handle.active_agent_run is not None:
            run_handle.active_agent_run.enqueue(prompts, priority="when_idle")
            return
    # BOOTING, TEARDOWN, IDLE → queue for after current turn
    session.work_send.send_nowait(FollowupItem(prompts=prompts, kwargs=kwargs))
```

### Decision 5: Add work_stream_capacity config parameter

`SessionState` initializes the work stream with a configurable `max_buffer_size` (default 256). This provides backpressure: if the work stream fills up (e.g., background tasks produce results faster than the agent can consume them), `send_nowait` raises `WouldBlock`, giving the background task provider an opportunity to apply its own backpressure.

## Risks / Trade-offs

- **[Behavior change]** The 30s timeout replaces the current `_max_auto_resume` (default 10 iterations). If work is produced at intervals > 30s, the current loop would catch it (since it iterates 10 times with no delay), while the new code would time out. → Mitigation: The timeout can be configured. Default 30s covers all reasonable background task patterns.
- **[Memory]** MemoryObjectStream buffers items in memory. With `max_buffer_size=256`, worst-case memory is bounded. → Mitigation: Backpressure via `send_nowait(WouldBlock)` is the correct mechanism.
- **[Behavior change]** `steer()` no longer calls `receive_request()` for the idle case. Instead, it writes to the work stream. The `run_loop` that is about to idle (timeout) picks it up, or a new `run_loop` starts if the previous one already timed out. This means `receive_request` no longer needs the `"steer"` priority alias.
- **[Existing tests]** steer/followup tests that rely on the current dict-queue + auto-resume pattern need minor updates to work with the stream pattern. The assertions about event ordering and `run_loop` exit timing change.
