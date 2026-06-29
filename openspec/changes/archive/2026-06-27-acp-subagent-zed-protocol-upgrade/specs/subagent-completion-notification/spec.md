## ADDED Requirements

### Requirement: Child session completion emits ToolCallProgress to parent
When a child session's EventBus consumer loop exits, the system SHALL emit a `ToolCallProgress(status="completed")` notification to the parent session's ACP client, carrying `_meta.subagent_session_info` with the child's `session_id` and `tool_call_id`.

#### Scenario: Normal child completion
- **WHEN** a child session's consumer loop exits normally (EventBus stream reaches EndOfStream)
- **THEN** the parent session's ACP client receives a `ToolCallProgress` with `status="completed"` and `field_meta` containing `subagent_session_info.session_id` matching the child session

#### Scenario: Child consumer already exited (race condition)
- **WHEN** `_on_spawn_session_start` calls `self._consumer_done_events.get(child_sid)` and it returns `None` (consumer exited before handler could grab the reference)
- **THEN** the system SHALL immediately call `_notify_completed()` to emit the completion notification
- **AND** `_parent_of` entry for the child SHALL be cleaned up

#### Scenario: Concurrent child sessions
- **GIVEN** two child sessions `child-A` and `child-B` spawned from the same parent
- **WHEN** both child consumer loops exit
- **THEN** each child's completion notification SHALL carry the correct `tool_call_id` matching its own `SpawnSessionStart`

### Requirement: Completion notification closure handles errors
The background task awaiting child completion SHALL catch and log exceptions from `client.session_update()`. No exception SHALL be silently swallowed.

#### Scenario: Client connection closed
- **WHEN** `self.client.session_update()` raises `ConnectionResetError` or `BrokenPipeError`
- **THEN** the exception SHALL be caught and logged at debug level
- **AND** the closure task SHALL complete without re-raising

#### Scenario: Unexpected exception
- **WHEN** `self.client.session_update()` raises an unexpected exception
- **THEN** the exception SHALL be caught and logged at exception level
- **AND** the closure task SHALL complete without re-raising

### Requirement: Completion notification task cleans up from _consumer_task_refs
When the closure task completes (normally or via exception), it SHALL remove itself from `_consumer_task_refs` to prevent memory leak in long-running servers.

#### Scenario: Task completion cleanup
- **WHEN** the `_await_child_and_notify` closure task completes
- **THEN** `self._consumer_task_refs.remove(task)` SHALL be called in a `finally` block
- **AND** `ValueError` from `remove()` (if task not in list) SHALL be suppressed via `contextlib.suppress`
