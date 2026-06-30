## ADDED Requirements

### Requirement: RunHandle implements session-level persistent execution context

`RunHandle` SHALL be restructured from a per-turn lifecycle handle to a session-level persistent execution context. It SHALL own the idle/turn cycle, message queue, steer/followup routing, and `async with` lifecycle. The class name `RunHandle` SHALL be preserved for API stability — existing callers (`close_session()`, `cancel_run()`, `SessionPool._runs`, protocol servers) require no import changes.

- `RunHandle` SHALL have `RunStatus` enum with states: `idle`, `running`, `done`
- `RunHandle` SHALL own `_idle_event` (`asyncio.Event` with `clear()` for reusable multi-cycle signaling), `_message_queue` (`list[str]`), `_message_history` (`list[ModelMessage]`), and `_closing` (`bool`)
- `RunHandle.start(initial_prompt)` SHALL be an async generator yielding `RichAgentStreamEvent`. It SHALL execute Turns in a `while True` loop, entering idle between Turns via `await self._idle_event.wait()`
- `RunHandle` SHALL implement `async with` protocol via `__aenter__` / `__aexit__`. `__aexit__` SHALL call `self.close()`
- `RunHandle.close()` SHALL set `_closing=True` and wake idle. Idempotent.
- `RunHandle.cancel()` SHALL set `run_ctx.cancelled=True` and wake idle. Idempotent.
- `RunHandle._cleanup_run()` SHALL set `complete_event` in `anyio.CancelScope(shield=True)` to ensure event fires even during CancelScope cascade
- `RunHandle` SHALL NOT have an `idle_timeout` parameter — Run waits indefinitely until woken. Timeout is caller's policy via `anyio.move_on_after(N)`
- Existing fields (`run_id`, `session_id`, `agent_type`, `status`, `run_ctx`, `complete_event`, `_cancel_fn`, `active_agent_run`) SHALL be preserved

#### Scenario: RunHandle starts and enters idle after first Turn
- **WHEN** `RunHandle.start("prompt")` is called
- **THEN** the RunHandle publishes `RunStartedEvent`
- **AND** creates a Turn via `agent.create_turn()`
- **AND** yields events from `Turn.execute()`
- **AND** after Turn completes, publishes `StreamCompleteEvent`
- **AND** if no queued messages, enters idle via `await self._idle_event.wait()`

#### Scenario: RunHandle wakes from idle on steer
- **WHEN** RunHandle is idle and `steer(message)` is called from a separate task
- **THEN** the message is appended to `_message_queue`
- **AND** `_idle_event.set()` wakes the RunHandle
- **AND** the RunHandle creates a new Turn with the queued message
- **AND** new Turn events flow through the same `async for` loop

#### Scenario: RunHandle closes via async with exit
- **WHEN** the caller exits the `async with agent.run(...) as run:` block
- **THEN** `__aexit__` calls `self.close()`
- **AND** `close()` sets `_closing=True` and wakes idle
- **AND** `start()` checks `_closing` and breaks the while loop
- **AND** `complete_event` is set in shielded scope

#### Scenario: RunHandle cancelled during idle
- **WHEN** `cancel()` is called while RunHandle is idle
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `_idle_event.set()` wakes the RunHandle
- **AND** `start()` checks `run_ctx.cancelled` and breaks
- **AND** `complete_event` is set

#### Scenario: RunHandle handles Turn failure gracefully
- **WHEN** `Turn.execute()` raises an unexpected exception
- **THEN** the exception is caught by `except Exception`
- **AND** `RunErrorEvent` is published with `message=str(exc)`, `agent_name`, `run_id`
- **AND** `run_failed` flag is set to `True`
- **AND** `StreamCompleteEvent` is published with fallback `ChatMessage(role="assistant", content="[Run failed]")`
- **AND** `start()` breaks the while loop

#### Scenario: RunHandle checks child_done_events between Turns
- **WHEN** a Turn completes and `run_ctx.child_done_events` is non-empty
- **THEN** the RunHandle waits for each child event
- **AND** processes `queued_steer_messages` as next Turn prompts
- **AND** if steer messages found, continues to next Turn without entering idle

### Requirement: RunHandle exposes status for protocol servers

`RunHandle` SHALL expose `_status` (`RunStatus` enum) as a queryable property. Protocol servers SHALL be able to query RunHandle status via `SessionPool.get_run(session_id)` to determine if a session is idle, running, or done.

- `RunStatus` SHALL have values: `idle`, `running`, `done`
- During idle, `session.current_run_id` SHALL remain set (the RunHandle is alive)
- `close_session()` SHALL check `RunStatus` to determine wake strategy (force-wake if idle)

#### Scenario: Protocol server queries RunHandle status
- **WHEN** a protocol server calls `session_pool.get_run(session_id).status`
- **THEN** it returns `RunStatus.idle`, `RunStatus.running`, or `RunStatus.done`
- **AND** idle status indicates no active Turn but RunHandle is alive

### Requirement: RunHandle is extensible for multi-server distribution

`RunHandle` SHALL be designed with swappable primitives for future multi-server support. The `_idle_event`, `_message_queue`, and `_status` fields SHALL be accessed only via well-defined operations that can be replaced with distributed equivalents without changing `start()` / `steer()` / `followup()` control flow.

- `_message_queue` SHALL be accessed only via `append()`, `copy()`, and `clear()` — swappable to any FIFO queue with `put()`/`drain()`
- `_idle_event` SHALL be accessed only via `set()`, `clear()`, and `wait()` — swappable to any async event with same interface
- `Turn` SHALL be a separate object with no back-references to RunHandle state — can be serialized for remote execution
- `EventBus` SHALL be injected in constructor (not created) — distributed implementation can be injected without code changes
- `steer()`/`followup()` SHALL be async methods — allow future distributed queue operations without signature changes
- `close()`/`cancel()` SHALL be sync idempotent — safe for retry-based distributed coordination

#### Scenario: Future DistributedRunHandle subclass
- **WHEN** a future `DistributedRunHandle` subclass overrides `_idle_event` and `_message_queue`
- **THEN** the `start()` control flow does not change
- **AND** steer/followup logic does not change
- **AND** only the primitive implementations differ
