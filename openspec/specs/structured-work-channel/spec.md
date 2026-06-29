## ADDED Requirements

### Requirement: Background task registration via pending_background_tasks counter
Tools that spawn background tasks SHALL increment `run_ctx.pending_background_tasks` before spawning and decrement it in `finally` when the task completes. The `background_tasks_complete` asyncio.Event SHALL be initially set (via custom factory, not `default_factory=asyncio.Event` which creates an unset event) and cleared when counter > 0 and set when counter returns to 0. A `steer_callback` on `AgentRunContext` SHALL provide tools with a path to call `steer()` without direct `TurnRunner` access.

#### Scenario: Tool increments on spawn
- **WHEN** a tool spawns a background task
- **THEN** `run_ctx.pending_background_tasks` SHALL be incremented by 1 before `asyncio.create_task()`
- **AND** `run_ctx.background_tasks_complete` SHALL be cleared

#### Scenario: Tool decrements on completion
- **WHEN** a background task completes (success, error, or cancellation)
- **THEN** `run_ctx.pending_background_tasks` SHALL be decremented by 1 in a `finally` block
- **AND** if counter reaches 0, `run_ctx.background_tasks_complete` SHALL be set

#### Scenario: Counter defaults to 0
- **WHEN** an `AgentRunContext` is created
- **THEN** `pending_background_tasks` SHALL be 0
- **AND** `background_tasks_complete` SHALL be set (via custom factory `_create_set_event()`, NOT `default_factory=asyncio.Event` which creates an unset event)
- **AND** `steer_callback` SHALL be None (set by `TurnRunner` when creating the `RunHandle`)

### Requirement: RunExecutor waits for background tasks before StreamCompleteEvent
After `agent_iteration_task` completes and before `StreamCompleteEvent` is published, `RunExecutor.execute()` SHALL check `run_ctx.pending_background_tasks`. If > 0, it SHALL `await run_ctx.background_tasks_complete.wait()`. No timeout SHALL be used — the wait blocks indefinitely until the counter reaches 0 or the session is cancelled.

#### Scenario: No background tasks → immediate StreamCompleteEvent
- **WHEN** `run_ctx.pending_background_tasks == 0` after agent iteration
- **THEN** `StreamCompleteEvent` SHALL be published immediately (no wait)

#### Scenario: Background tasks pending → wait
- **WHEN** `run_ctx.pending_background_tasks > 0` after agent iteration
- **THEN** `RunExecutor` SHALL `await run_ctx.background_tasks_complete.wait()` before proceeding

#### Scenario: Session close during wait → cancelled StreamCompleteEvent
- **WHEN** session is closed while waiting for background tasks
- **THEN** `run_ctx.cancelled` SHALL be set to True
- **AND** `background_tasks_complete` SHALL be set (to unblock the wait)
- **AND** `StreamCompleteEvent(cancelled=True)` SHALL be published

#### Scenario: No timeout used
- **WHEN** RunExecutor is waiting for background tasks
- **THEN** no timeout SHALL be applied — `await event.wait()` blocks indefinitely

### Requirement: Re-iteration with queued steer messages
When background tasks complete and their steer messages were queued (because `agent_run` was None when `steer()` was called), `RunExecutor` SHALL re-iterate with the queued messages as new prompts. The re-iteration happens within the same `execute()` call, before `StreamCompleteEvent` is published.

#### Scenario: Steer message queued during wait → re-iterate
- **WHEN** a background task completes and calls `steer()` while `agent_run is None` (iteration has exited)
- **AND** `run_ctx` is not completed (RunExecutor still in execute())
- **THEN** the steer message SHALL be appended to `run_ctx.queued_steer_messages`
- **AND** after `background_tasks_complete` is set, RunExecutor SHALL re-iterate with queued messages as prompts

#### Scenario: No queued messages → proceed to StreamCompleteEvent
- **WHEN** `background_tasks_complete` is set and `queued_steer_messages` is empty
- **THEN** `StreamCompleteEvent` SHALL be published immediately

#### Scenario: Re-iteration spawns new background tasks → loop continues
- **WHEN** re-iteration with steer messages spawns new background tasks
- **THEN** the counter SHALL be reset to 0 before re-iteration
- **AND** the wait loop SHALL continue until all new background tasks complete and no more steer messages are queued

### Requirement: steer() routes to queued_steer_messages when RunExecutor is waiting
When `steer()` is called and `agent_run is None` but `run_ctx` is not completed (RunExecutor in wait loop), the message SHALL be written to `run_ctx.queued_steer_messages` instead of `_post_turn_injections`.

#### Scenario: Steer during active iteration → enqueue asap (unchanged)
- **WHEN** `steer()` is called and `agent_run is not None`
- **THEN** the message SHALL be enqueued via `agent_run.enqueue(priority="asap")` (existing behavior, unchanged)

#### Scenario: Steer during RunExecutor wait → queue for re-iteration
- **WHEN** `steer()` is called, `agent_run is None`, and `run_ctx.completed == False`
- **THEN** the message SHALL be appended to `run_ctx.queued_steer_messages`

#### Scenario: Steer after execute() returned → existing fallback (unchanged)
- **WHEN** `steer()` is called, `agent_run is None`, and `run_ctx.completed == True`
- **THEN** the message SHALL fall through to `_post_turn_injections` (existing behavior, unchanged)

### Requirement: Single StreamCompleteEvent per execute() call
`RunExecutor.execute()` SHALL publish exactly one `StreamCompleteEvent` per call, after all background tasks and re-iterations are complete. Intermediate iteration results SHALL NOT produce separate `StreamCompleteEvent`s.

#### Scenario: Initial iteration + re-iteration → single StreamCompleteEvent
- **WHEN** initial iteration completes, background task completes, re-iteration runs
- **THEN** exactly one `StreamCompleteEvent` SHALL be published with the final response

### Requirement: Session close unblocks background task wait
When a session is closed (via `close_session()`), `run_ctx.cancelled` SHALL be set to True and `background_tasks_complete` SHALL be set **BEFORE** the existing 30-second `complete_event.wait()` call in `close_session()`. This immediately unblocks any `event.wait()` in RunExecutor. The flags SHALL NOT be set in `_run_turn_unlocked()`'s finally block — the finally block runs AFTER `execute()` returns, so it cannot unblock the wait loop, and setting `cancelled = True` there would incorrectly mark every normal completion as cancelled.

#### Scenario: close_session during background task wait
- **WHEN** `close_session()` is called while RunExecutor is waiting for background tasks
- **THEN** `run_ctx.cancelled` SHALL be set to True BEFORE the 30-second `complete_event.wait()`
- **AND** `run_ctx.background_tasks_complete` SHALL be set
- **AND** RunExecutor SHALL exit the wait loop and publish `StreamCompleteEvent(cancelled=True)`
- **AND** `close_session()` SHALL return quickly (not wait 30 seconds)

### Requirement: Message history propagated to re-iteration
When re-iterating with queued steer messages, `RunExecutor` SHALL update the `message_history` passed to `agentlet.iter()` with the messages from the prior iteration (captured via `agent_run.all_messages()`). This ensures the agent sees the full conversation context including prior iterations' responses. The capture SHALL happen **INSIDE** the `async with agentlet.iter(...)` block (before `__aexit__` is called), not after the block exits, because `all_messages()` may be unreliable after context manager cleanup.

#### Scenario: Re-iteration sees prior iteration's response
- **WHEN** re-iteration runs with steer messages
- **THEN** the `message_history` passed to `agentlet.iter()` SHALL include all messages from the prior iteration
- **AND** the agent SHALL be able to reference its prior response in the new iteration

#### Scenario: iteration_messages captured inside async with block
- **WHEN** `agent_iteration_task` captures `iteration_messages`
- **THEN** the capture SHALL happen inside the `async with agentlet.iter(...)` block
- **AND** if an exception occurs inside the block, `iteration_messages` may not be captured (acceptable — error paths don't need re-iteration)
