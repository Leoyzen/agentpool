# acp-server Specification

## Purpose
TBD - created by archiving change cancel-turn-not-run. Update Purpose after archive.
## Requirements
### Requirement: ACP cancel_session does not kill the RunHandle

`cancel_session()` SHALL only call `SessionController.cancel_run_for_session()`. It SHALL NOT call `run_handle.fail()`. Legacy clients blocking on `_turn_complete_event.wait()` SHALL unblock when the cancelled turn finishes — `NativeTurn.execute()` returns WITHOUT yielding `StreamCompleteEvent`, and `start()` publishes `RunFailedEvent` then sets `_turn_complete_event`.

- `cancel_session()` SHALL NOT publish `RunFailedEvent` directly — `start()` publishes it when it detects `run_ctx.cancelled` after the turn
- The event consumer SHALL still send `session/update` with `turn_complete` and `stop_reason="cancelled"` after the cancelled turn finishes
- `handle_prompt()` SHALL wait on `run_handle._turn_complete_event` instead of `run_handle.complete_event` for legacy clients

### Requirement: PartDeltaEvent handler does not generate new tool call IDs

The ACPEventConverter PartDeltaEvent handler SHALL NOT call `delta.as_part()`. When a `PartDeltaEvent` arrives with a `tool_call_id`, the handler SHALL look up the existing `_ToolState` by that ID. If no state exists, the handler SHALL yield no ACP session updates. The handler SHALL NOT create a new `_ToolState` from a `PartDeltaEvent`.

#### Scenario: PartDeltaEvent with known tool_call_id
- **WHEN** a `PartDeltaEvent` arrives with a `tool_call_id` that matches an existing `_ToolState`
- **THEN** the handler SHALL look up the existing state
- **AND** SHALL yield no ACP session updates (streaming deltas are not forwarded)
- **AND** SHALL NOT call `delta.as_part()`
- **AND** SHALL NOT create a new `_ToolState`
- **AND** SHALL NOT emit a `ToolCallStart` notification

#### Scenario: PartDeltaEvent with unknown tool_call_id
- **WHEN** a `PartDeltaEvent` arrives with a `tool_call_id` that does not match any existing `_ToolState`
- **THEN** the handler SHALL yield no ACP session updates
- **AND** SHALL NOT create a new `_ToolState`

### Requirement: ToolCallProgressEvent handler extracts tool_input and tool_name

The ACPEventConverter ToolCallProgressEvent handler SHALL extract `tool_input` and `tool_name` from the event. When `tool_input` is not `None`, the handler SHALL update `_ToolState.raw_input` and `_ToolState.title`. When `tool_name` is not `None` and the state has `"unknown"` as tool name, the handler SHALL update `_ToolState.tool_name`. The handler SHALL include `raw_input` in the emitted ACP `tool_call_update` notification.

#### Scenario: ToolCallProgressEvent with tool_input
- **WHEN** a `ToolCallProgressEvent` arrives with `tool_input` containing complete arguments
- **THEN** the handler SHALL update `_ToolState.raw_input` with the `tool_input` value
- **AND** SHALL update `_ToolState.title` if applicable
- **AND** SHALL emit a `tool_call_update` notification with `status="in_progress"` and `raw_input` containing the complete arguments

#### Scenario: ToolCallProgressEvent with tool_input=None
- **WHEN** a `ToolCallProgressEvent` arrives with `tool_input=None`
- **THEN** the handler SHALL preserve existing `_ToolState.raw_input` unchanged
- **AND** SHALL emit a `tool_call_update` notification with `status="in_progress"` and existing `raw_input`

### Requirement: ACP tool call lifecycle emits up to three notifications

The ACP tool call lifecycle SHALL emit up to three `session/update` notifications per tool call: `pending` (ToolCallStart), `in_progress` (ToolCallProgress), and `completed` (ToolCallComplete). The `in_progress` notification SHALL be skipped when the tool call arguments are identical between `PartStartEvent` and `FunctionToolCallEvent` (dedup). All notifications for a single tool call SHALL share the same `toolCallId`.

#### Scenario: Streaming tool call with argument changes
- **WHEN** a tool call has streaming arguments that differ between `PartStartEvent` and `FunctionToolCallEvent`
- **THEN** the system SHALL emit up to three `session/update` notifications
- **AND** the first notification SHALL have `status="pending"` with empty `raw_input`
- **AND** the second notification SHALL have `status="in_progress"` with complete `raw_input`
- **AND** the third notification SHALL have `status="completed"` with `raw_input` preserved

#### Scenario: No-args tool call with identical raw_input
- **WHEN** a tool call has no arguments (`raw_input={}`) and `PartStartEvent` and `FunctionToolCallEvent` carry identical `raw_input`
- **THEN** the system SHALL emit exactly two `session/update` notifications
- **AND** the first notification SHALL have `status="pending"` with empty `raw_input`
- **AND** the second notification SHALL have `status="completed"` with `raw_input` preserved
- **AND** the `in_progress` notification SHALL be skipped (dedup)

### Requirement: Dead FunctionToolCallEvent handler removed

The ACPEventConverter SHALL NOT contain a `FunctionToolCallEvent` handler. The `FunctionToolCallEvent` is intercepted by EventMapper before reaching the converter, making any handler dead code.

#### Scenario: FunctionToolCallEvent never reaches ACPEventConverter
- **WHEN** a `FunctionToolCallEvent` is emitted during agent execution
- **THEN** EventMapper SHALL intercept it and emit `ToolCallStartEvent` or `ToolCallProgressEvent`
- **AND** ACPEventConverter SHALL NOT have a matching `case FunctionToolCallEvent` branch

#### Scenario: Cancel unblocks legacy client
- **WHEN** a legacy client (no `turn_complete` capability) has a prompt in progress
- **AND** `cancel_session()` is called
- **THEN** `cancel_run_for_session()` sets `run_ctx.cancelled = True` and cancels `_iteration_task`
- **AND** `NativeTurn.execute()` catches the cancellation, returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` detects `run_ctx.cancelled`, publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter emits a single `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `start()` sets `_turn_complete_event`
- **AND** `handle_prompt()` unblocks from `_turn_complete_event.wait()`
- **AND** returns `PromptResponse` with `stop_reason="cancelled"`
- **AND** the RunHandle remains alive and idle

#### Scenario: Cancel with turn_complete-capable client
- **WHEN** a `turn_complete`-capable client has a prompt in progress
- **AND** `cancel_session()` is called
- **THEN** `cancel_run_for_session()` sets `run_ctx.cancelled = True` and cancels `_iteration_task`
- **AND** `NativeTurn.execute()` catches the cancellation, returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter receives `RunFailedEvent` and emits `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `handle_prompt()` returns `PromptResponse` immediately (no blocking)
- **AND** the RunHandle remains alive and idle

#### Scenario: Cancel then new prompt on same session
- **WHEN** a run is cancelled via `cancel_session()`
- **AND** the user sends a new prompt on the same session
- **THEN** `handle_prompt()` calls `receive_request()`
- **AND** `session.current_run_id` is still valid (RunHandle is alive)
- **AND** `receive_request()` finds the existing RunHandle
- **AND** calls `steer()` to inject the new prompt
- **AND** `start()` wakes from idle, resets `run_ctx.cancelled`, and processes the new prompt
- **AND** events are published normally — no hang

