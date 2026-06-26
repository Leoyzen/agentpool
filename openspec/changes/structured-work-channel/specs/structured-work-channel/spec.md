## ADDED Requirements

### Requirement: Session-level work stream for post-turn messages
Each session SHALL have an `anyio.MemoryObjectStream[WorkItem]` as the sole channel for delivering post-turn messages (steer, followup, background task results). The stream SHALL be created when the session is initialized and closed when the session is destroyed.

#### Scenario: Work stream created on session init
- **WHEN** a `SessionState` is created
- **THEN** it SHALL have a `work_send`/`work_receive` pair of `anyio.MemoryObjectStream`

#### Scenario: Work stream closed on session destroy
- **WHEN** a session is closed
- **THEN** the `work_send` stream SHALL be closed, causing `EndOfStream` on the receive side

### Requirement: WorkItem typed union for all post-turn messages
All post-turn messages SHALL be one of a typed `WorkItem` union: `SteerItem` (for steer/background-task messages) or `FollowupItem` (for followup/inject messages).

#### Scenario: SteerItem carries message and kwargs
- **WHEN** `steer()` is called
- **THEN** a `SteerItem(message, kwargs)` SHALL be written to the work stream

#### Scenario: FollowupItem carries prompts and kwargs
- **WHEN** `followup()` is called
- **THEN** a `FollowupItem(prompts, kwargs)` SHALL be written to the work stream

### Requirement: steer() writes to work stream instead of TOCTOU branching
The `steer()` method SHALL use `match session.turn_state` to route messages. In `RUNNING` state, it SHALL enqueue directly via `agent_run.enqueue()`. In all other states, it SHALL write a `SteerItem` to the work stream. It SHALL NOT call `receive_request()` for the idle case.

#### Scenario: steer during RUNNING state enqueues directly
- **WHEN** `steer()` is called and `turn_state == RUNNING`
- **THEN** the message SHALL be enqueued via `agent_run.enqueue(priority="asap")`

#### Scenario: steer during non-RUNNING state writes to work stream
- **WHEN** `steer()` is called and `turn_state != RUNNING`
- **THEN** a `SteerItem` SHALL be written to `session.work_send`

#### Scenario: steer returns True for RUNNING, False otherwise
- **WHEN** `steer()` is called
- **THEN** it SHALL return `True` if the message was delivered in-turn, `False` if queued

### Requirement: followup() always writes to work stream
The `followup()` method SHALL always write a `FollowupItem` to the work stream, regardless of turn state.

#### Scenario: followup writes FollowupItem
- **WHEN** `followup()` is called
- **THEN** a `FollowupItem` SHALL be written to `session.work_send`

### Requirement: run_loop consumes from work stream with timeout
The `run_loop()` method SHALL consume `WorkItem`s from the work stream after each `_run_turn_unlocked()` call, using a configurable timeout (default 30s). If no item arrives before the timeout, `run_loop` SHALL exit.

#### Scenario: run_loop processes SteerItem from work stream
- **WHEN** `run_loop` receives a `SteerItem` from the work stream
- **THEN** it SHALL call `_run_turn_unlocked(session_id, message)` with the item's message

#### Scenario: run_loop processes FollowupItem from work stream
- **WHEN** `run_loop` receives a `FollowupItem` from the work stream
- **THEN** it SHALL call `_run_turn_unlocked(session_id, *prompts)` with the item's prompts

#### Scenario: run_loop exits on timeout
- **WHEN** no `WorkItem` arrives within the configured timeout
- **THEN** `run_loop` SHALL exit cleanly

#### Scenario: run_loop exits on EndOfStream
- **WHEN** the work stream is closed (session destroyed)
- **THEN** `run_loop` SHALL exit cleanly

### Requirement: Work stream provides backpressure via max_buffer_size
The work stream SHALL have a configurable `max_buffer_size` (default 256). If the stream buffer is full, `send_nowait` SHALL raise `anyio.WouldBlock`, allowing callers to apply their own backpressure.

#### Scenario: Full stream raises WouldBlock
- **WHEN** the work stream buffer is full and a caller tries to send
- **THEN** the caller SHALL receive `anyio.WouldBlock`

### Requirement: _post_turn_injections, _post_turn_prompts, _safe_auto_resume, _trigger_auto_resume removed
The dictionary-based queuing mechanisms and the auto-resume task spawning SHALL be removed. The work stream and the inline consume loop in `run_loop` replace all of them.

#### Scenario: Queued work processed within same run_loop
- **WHEN** a background task calls `steer()` during `run_loop`'s consume loop
- **THEN** the `SteerItem` SHALL be processed within the same `run_loop`, before it exits
