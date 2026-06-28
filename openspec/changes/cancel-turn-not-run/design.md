## Context

In the `feat/run-turn-separation` worktree, `RunHandle.start()` is a long-running async generator that implements an idle/wait/wake/turn loop. A single `RunHandle` is bound 1:1 to a session for its entire lifetime. `steer()` and `followup()` inject messages into the loop without creating new runs.

The cancel mechanism is broken: `cancel()` calls `run_ctx.current_task.cancel()`, which raises `CancelledError` into the `start()` task itself. `NativeTurn.execute()` catches `CancelledError` and **re-raises** it (line 204-206), causing the `start()` generator to die. This kills the RunHandle, leaving `session.current_run_id` pointing at a dead handle.

The `_iteration_task` field on `Agent` (agent.py:291) was designed to hold a separate task for the LLM API call so it can be cancelled independently — but it is never assigned. `_interrupt()` (agent.py:1098-1113) reads it but always finds `None`, so it falls through to cancelling `current_task` (the `start()` task).

Meanwhile, ACP `cancel_session()` (handler.py:562-576) calls both `cancel_run_for_session()` AND `run_handle.fail()`. The `fail()` call sets `complete_event` to unblock `handle_prompt()`'s legacy-client blocking path (`complete_event.wait()` at line 520). This `fail()` is the mechanism that kills the RunHandle after cancel.

## Goals / Non-Goals

**Goals:**
- Cancel interrupts only the current turn; the `start()` loop survives and returns to idle
- After cancel, the session accepts new prompts normally (no hang)
- Legacy ACP clients (without `turn_complete` capability) unblock correctly after cancel
- `current_run_id` stays valid throughout — no stale references
- Defense-in-depth: stale-run detection in `receive_request()` as safety net

**Non-Goals:**
- Changing the 1:1 session-to-RunHandle model (already correct by design)
- Changing how `steer()` / `followup()` work (they already inject into the loop correctly)
- Changing ACP protocol-level cancel semantics (still `session/cancel` notification, still `stopReason="cancelled"`)
- Handling multiple concurrent cancels (idempotent cancel is sufficient)
- Fixing the recursive `interrupt()` → `cancel_run_for_session()` → `cancel()` loop (harmless, out of scope)

## Decisions

### Decision 1: Wire up `_iteration_task` to enable independent LLM cancellation

**Choice**: Wrap `await agent_run.next(node)` in `NativeTurn.execute()` inside an `asyncio.Task` stored on `self._agent._iteration_task`.

**Rationale**: The `_iteration_task` field already exists (agent.py:291) but is never assigned. By running the LLM call in a dedicated task, `cancel()` can interrupt just the LLM call without killing the `start()` generator. The `start()` task continues running, catches the cancelled iteration, and returns to idle.

**Alternatives considered**:
- *Cancel `current_task` and catch in `start()`*: Would require re-architecting `start()` to handle CancelledError from turn execution differently from external cancellation. Fragile — asyncio may re-raise CancelledError at the next await point even after catching.
- *Use `asyncio.shield()` on the turn*: Would prevent cancellation entirely, defeating the purpose.

### Decision 2: `NativeTurn.execute()` catches CancelledError from `_iteration_task` and returns without yielding StreamCompleteEvent

**Choice**: When `CancelledError` is caught from the iteration task, check `run_ctx.cancelled`. If true, break out of the node loop and **return immediately without yielding `StreamCompleteEvent`**. Do not re-raise. `start()` detects `run_ctx.cancelled` after the `async for` loop exits and publishes `RunFailedEvent`, which the event converter handles to emit a single `TurnCompleteUpdate(stop_reason="cancelled")`.

**Rationale**: The existing code re-raises `CancelledError` (line 204-206), which kills `start()`. By catching it and checking `run_ctx.cancelled`, we distinguish "our cancel" (break gracefully) from "external cancellation" (re-raise). The 3 existing `run_ctx.cancelled` checks (lines 152, 159, 178) already implement cooperative cancellation — this change makes them actually reachable.

**Why not yield `StreamCompleteEvent` on cancel**: The ACP event converter (`event_converter.py:725-738`) unconditionally emits `TurnCompleteUpdate(stop_reason="end_turn")` when it receives `StreamCompleteEvent` — it does NOT check the `cancelled` field. If both `StreamCompleteEvent` and `RunFailedEvent` are published, the client receives **two `turn_complete` notifications** with conflicting stop reasons (`end_turn` then `cancelled`). By skipping `StreamCompleteEvent` on cancel, only `RunFailedEvent` reaches the converter, resulting in a single `TurnCompleteUpdate(stop_reason="cancelled")`.

**Edge case**: If `CancelledError` is raised and `run_ctx.cancelled` is False (external cancellation, e.g. session close), re-raise as before.

**ACP agent path**: The ACP agent's turn generator exits without yielding `StreamCompleteEvent` on cancel (it catches `CancelledError` at `acp_agent.py:562` and falls through to `finally` cleanup). This is already correct — `start()` handles a generator that exits without `StreamCompleteEvent` by detecting `run_ctx.cancelled` after the `async for` loop.

**Message history loss on cancel**: When a turn is cancelled, `start()` uses Python `continue` to skip post-turn processing, which includes `self._message_history = turn.message_history` (run.py:215-219). This means the partial message history from the cancelled turn is lost. This is an acceptable trade-off: the cancelled turn's output is incomplete and should not be persisted as if it were a full response. The conversation history will not include the cancelled turn's partial LLM output. If the user sends a new prompt, the agent starts fresh from the last completed turn's message history.

### Decision 3: `RunHandle.cancel()` no longer cancels `current_task`

**Choice**: Remove `current_task.cancel()` from `cancel()` (run.py:417-419). `cancel()` only sets `run_ctx.cancelled = True`, wakes `_idle_event`, and calls `agent._interrupt()` (which now only cancels `_iteration_task`).

**Rationale**: `current_task` is the `start()` task. Cancelling it kills the entire run loop. With `_iteration_task` wired up, we don't need to cancel `current_task` — the cooperative cancellation flag + `_iteration_task` cancellation is sufficient to stop the current turn.

### Decision 4: Per-turn completion event replaces `complete_event` for legacy client blocking

**Choice**: Add `_turn_complete_event: asyncio.Event` to `RunHandle`. Set it at the end of each turn (in `start()` after `turn.execute()` returns). Reset it at the start of each turn. `handle_prompt()` waits on `_turn_complete_event` instead of `complete_event` for legacy clients.

**Rationale**: `complete_event` signals "the entire RunHandle is done" — which never happens in normal operation (1:1 model). A per-turn event correctly signals "this turn finished" whether by completion, cancellation, or error. This lets `handle_prompt()` return `stopReason="cancelled"` after the cancelled turn finishes, without killing the RunHandle.

**Alternative considered**: *Keep `complete_event` and have `cancel_session()` call `fail()`*: This is the current approach. It works but kills the RunHandle, causing the hang. Replacing with per-turn event is cleaner.

### Decision 5: Remove `fail()` from ACP `cancel_session()`

**Choice**: Remove the `run_handle.fail()` call from `cancel_session()` (handler.py:572-581). `cancel_session()` only calls `cancel_run_for_session()`. The per-turn event (Decision 4) handles unblocking legacy clients.

**Rationale**: `fail()` sets `complete_event` and publishes `RunFailedEvent`. With the per-turn event, legacy clients unblock when the turn finishes. `RunFailedEvent` is published by `start()` when it detects `run_ctx.cancelled` after the turn (not by `fail()`), so the event consumer still sends `turn_complete(stop_reason="cancelled")`. The `RunFailedEvent` must include `exception=RuntimeError("Run cancelled")` so the event converter (`event_converter.py:882-883`) detects it as a cancellation via `isinstance(exc, RuntimeError) and "cancelled" in str(exc).lower()`.

### Decision 6: Clear `current_run_id` in `_cleanup_run()` as safety net

**Choice**: In `_cleanup_run()` (core.py:1584-1594), after popping the run handle, clear `session.current_run_id` if it matches the run being cleaned up.

**Rationale**: In the 1:1 model, `current_run_id` should never be stale. But if a RunHandle does die (unrecoverable error, session close), the reference must be cleared to allow new runs. This is defense-in-depth.

### Decision 7: Stale-run detection in `receive_request()`

**Choice**: In `receive_request()` (core.py:1557-1565), if `current_run_id` is set but the run handle is missing from `_runs` or in a terminal status, clear it and start a new run.

**Rationale**: Prevents any future stale-reference bug from causing a hang. If `current_run_id` points at a dead or missing run, the system self-heals by creating a new run instead of silently returning `None`.

## Risks / Trade-offs

- **[Risk] `_iteration_task` adds overhead**: Each `agent_run.next(node)` call now creates an asyncio Task. For fast nodes (e.g. `CallToolsNode` with no tools), this adds ~0.1ms. → Acceptable — LLM calls dominate latency.

- **[Risk] Race between `_iteration_task` assignment and `_interrupt()`**: If `_interrupt()` is called before `_iteration_task` is set, it finds `None` and does nothing. The cooperative `run_ctx.cancelled` flag catches this — the turn loop checks it before the next `next()` call. → Mitigated by existing cooperative checks.

- **[Risk] `CancelledError` from `_iteration_task` swallowed unintentionally**: If a non-cancel `CancelledError` propagates from the LLM call (e.g. session close), it must be re-raised. → Mitigated by checking `run_ctx.cancelled` — only swallow when WE initiated the cancel.

- **[Trade-off] `cancel()` becomes cooperative, not preemptive**: There may be a brief delay between `cancel()` and the turn actually stopping (until the next `run_ctx.cancelled` check or `_iteration_task` cancellation). → Acceptable — the delay is bounded by the next asyncio checkpoint, typically <1ms.

- **[Trade-off] Two event fields on RunHandle**: `complete_event` (run-level) and `_turn_complete_event` (turn-level) may cause confusion. → Mitigated by clear naming and documentation. `complete_event` is only used for session close; `_turn_complete_event` is used for per-turn blocking.

- **[Risk] `_interrupt()` is fire-and-forget**: `cancel()` schedules `agent._interrupt()` as a separate asyncio task (`run.py:411-415`). For native agents, `_interrupt()` calls `iteration_task.cancel()` which is synchronous, but it runs on the next event loop iteration. The `run_ctx.cancelled` flag (set synchronously in `cancel()`) provides the immediate signal — the cooperative checks in `NativeTurn.execute()` catch the cancellation on the next loop iteration. This is acceptable — the cancellation is eventually consistent, not immediate. The delay is bounded by the next asyncio checkpoint.

- **[Risk] `_close_session_run_turn()` fallback**: `_close_session_run_turn()` (core.py:1295-1385) calls `run_handle.cancel()` as a fallback when `complete_event` times out (line 1349). After the change, `cancel()` no longer cancels `current_task`, so the `start()` loop won't be killed by cancellation. The method already calls `run_handle.close()` first (line 1319) which sets `_closing = True`, so the `start()` loop should exit on the next idle check. If the loop is stuck in a turn (not idle), `cancel()` sets `cancelled = True` and calls `_interrupt()`, which should eventually unblock it. → Mitigated: verify session close still works within the 30s timeout in validation.
