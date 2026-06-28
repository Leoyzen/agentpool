## MODIFIED Requirements

### Requirement: RunHandle exposes unified steer() and followup() with no agent-type branching

`RunHandle` SHALL expose `steer()` and `followup()` methods that route messages without agent-type branching. The 4-branch native/non-native × active/idle routing SHALL be eliminated. All routing SHALL be handled by RunHandle state (idle vs running) and the `active_agent_run` field.

- `steer(message)` SHALL be an async method returning `bool` (True if delivered, False if closing)
- `steer(message)` when RunHandle is idle: append to `_message_queue`, set `_idle_event` to wake
- `steer(message)` when RunHandle is running and `active_agent_run` is not None: call `agent_run.enqueue(message, priority="asap")` (native agents)
- `steer(message)` when RunHandle is running and `active_agent_run` is None: append to `_message_queue` (non-native agents, queued for next Turn)
- `steer(message)` when `_closing` is True: return False (message rejected)
- `followup(message)` SHALL be an async method returning `bool`
- `followup(message)` SHALL always append to `_message_queue`. If idle, set `_idle_event` to wake
- `followup(message)` when `_closing` is True: return False
- `TurnRunner.steer()` and `TurnRunner.followup()` SHALL emit `DeprecationWarning` in Phase 1-2 and delegate to `RunHandle.steer()`/`.followup()`. Deleted in Phase 3.

#### Scenario: Steer on idle RunHandle
- **WHEN** `run_handle.steer(message)` is called while RunHandle status is `idle`
- **THEN** the message is appended to `_message_queue`
- **AND** `_idle_event.set()` wakes the RunHandle
- **AND** `True` is returned
- **AND** the RunHandle creates a new Turn with the message

#### Scenario: Steer on running native RunHandle
- **WHEN** `run_handle.steer(message)` is called while RunHandle is running and `active_agent_run` is not None
- **THEN** `agent_run.enqueue(message, priority="asap")` is called
- **AND** the message is drained before the next LLM call via `PendingMessageDrainCapability`
- **AND** `True` is returned

#### Scenario: Steer on running non-native RunHandle
- **WHEN** `run_handle.steer(message)` is called while RunHandle is running and `active_agent_run` is None
- **THEN** the message is appended to `_message_queue` (queued for next Turn)
- **AND** `True` is returned

#### Scenario: Steer after close
- **WHEN** `run_handle.steer(message)` is called after `close()` was called
- **THEN** `False` is returned
- **AND** the message is not delivered

#### Scenario: Followup on idle RunHandle
- **WHEN** `run_handle.followup(message)` is called while RunHandle is idle
- **THEN** the message is appended to `_message_queue`
- **AND** `_idle_event.set()` wakes the RunHandle
- **AND** `True` is returned

#### Scenario: Followup on running RunHandle
- **WHEN** `run_handle.followup(message)` is called while RunHandle is running
- **THEN** the message is appended to `_message_queue` (processed after current Turn)
- **AND** `_idle_event` is NOT set (Turn is still running)
- **AND** `True` is returned

#### Scenario: TurnRunner.steer() deprecated delegation
- **WHEN** `TurnRunner.steer(session_id, message)` is called during Phase 1-2
- **THEN** a `DeprecationWarning` is emitted
- **AND** the call delegates to `RunHandle.steer(message)` on the session's active RunHandle
- **AND** the return value is propagated

### Requirement: SessionController.receive_request() delegates to RunHandle

`SessionController.receive_request()` SHALL be simplified to delegate to `RunHandle.start()` (idle) or `RunHandle.steer()`/`.followup()` (busy). The method SHALL perform session-state validation (exists, not closing, concurrency limit) then delegate. The `_create_run()`, `_cleanup_run()`, and `cancel_run_for_session()` methods SHALL be removed from SessionController — their logic moves to RunHandle.

- `receive_request()` SHALL check session exists, not closing, and `max_concurrent_runs` not exceeded
- If `session.current_run_id` is None (idle): construct `RunHandle`, register in `_runs`, set `current_run_id`, start `run.start()` as background task
- If `session.current_run_id` is not None (busy): delegate to `run_handle.steer()` (asap/steer priority) or `run_handle.followup()` (when_idle/followup priority)
- `priority="steer"` SHALL be mapped to `asap`, `priority="followup"` SHALL be mapped to `when_idle` (backward compat)
- `_create_run()` SHALL be removed (RunHandle constructs itself)
- `_cleanup_run()` SHALL be removed (RunHandle manages its own cleanup via `_cleanup_callback` + `complete_event`)
- `cancel_run_for_session()` SHALL be removed (callers use `RunHandle.cancel()` directly)

#### Scenario: Idle session receives request
- **WHEN** `receive_request(session_id, content)` is called on an idle session
- **THEN** a `RunHandle` is constructed and registered in `_runs`
- **AND** `session.current_run_id` is set
- **AND** `run.start(content)` is launched as a background task
- **AND** a done-callback removes the RunHandle from `_runs` on completion

#### Scenario: Active session receives steer
- **WHEN** `receive_request(session_id, content, priority="steer")` is called on a session with active run
- **THEN** the active `RunHandle` is retrieved from `_runs`
- **AND** `run_handle.steer(content)` is called
- **AND** no new RunHandle is created

#### Scenario: Active session receives followup
- **WHEN** `receive_request(session_id, content, priority="followup")` is called on a session with active run
- **THEN** the active `RunHandle` is retrieved from `_runs`
- **AND** `run_handle.followup(content)` is called
- **AND** no new RunHandle is created

## REMOVED Requirements

### Requirement: TurnRunner exposes steer() and followup() with agent-type awareness
**Reason**: The 4-branch native/non-native × active/idle routing is eliminated by unified `RunHandle.steer()`/`.followup()`. `TurnRunner` is deprecated in Phase 1-2 and deleted in Phase 3.
**Migration**: Use `RunHandle.steer()` and `RunHandle.followup()` directly. The `agent.AGENT_TYPE` detection, `active_agent_run` lookup, and `injection_manager` delegation are all handled internally by RunHandle.

### Requirement: TurnRunner._run_turn_unlocked() removes manual follow-up loop for native agents
**Reason**: `TurnRunner._run_turn_unlocked()` is deleted entirely. Its logic is replaced by `RunHandle.start()` + `Turn.execute()`. The native/non-native manual follow-up loop distinction no longer exists — RunHandle's idle/wake mechanism handles all follow-up continuation.
**Migration**: The manual follow-up loop (`while has_queued(): pop_queued() + _run_stream_once()`) is replaced by `RunHandle.start()`'s `while True` loop with `idle_event.wait()` between Turns. `PendingMessageDrainCapability` handles in-turn message drain for native agents. `_post_turn_injections` and `_post_turn_prompts` are replaced by `RunHandle._message_queue`.

### Requirement: RunHandle exposes active_agent_run for TurnRunner access
**Reason**: `TurnRunner` is deleted. `active_agent_run` is still set by `NativeTurn.execute()` but accessed only by `RunHandle.steer()` (not TurnRunner).
**Migration**: `run_ctx._run_handle.active_agent_run` is still set during `NativeTurn.execute()`. `RunHandle.steer()` reads it directly. No external callers need this field.

### Requirement: inject_prompt() and queue_prompt() deprecated for native agents
**Reason**: `inject_prompt()` and `queue_prompt()` are fully replaced by `RunHandle.steer()` and `RunHandle.followup()`. The deprecation period ends — these methods are removed in Phase 3.
**Migration**: Use `RunHandle.steer()` for asap injection, `RunHandle.followup()` for queued delivery. `SessionPool.inject_prompt()` and `SessionPool.queue_prompt()` delegate to RunHandle methods.
