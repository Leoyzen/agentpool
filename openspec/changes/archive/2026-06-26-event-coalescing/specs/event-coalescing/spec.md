## ADDED Requirements

### Requirement: EventBus coalesces consecutive same-type events
The EventBus SHALL buffer consecutive batchable events per session and merge them before dispatching to subscribers. Merging SHALL use `itertools.groupby` grouped by merge key. The merge key for `PartDeltaEvent` SHALL be the delta type (text, thinking) or `(tool_call, tool_call_id)` for tool_call deltas. The merge key for `ToolCallProgressEvent` SHALL be `(tool_call_id, status)`. Merged `PartDeltaEvent` instances SHALL concatenate their `content_delta`/`args_delta` strings and use the first event's `index`. Merged `ToolCallProgressEvent` instances SHALL concatenate their `items` sequences and preserve the last event's `title`, `status`, `replace_content`, and `tool_name`. Events separated by a different merge key SHALL NOT be merged, even if they share the same merge key. Coalescing operates on consecutive runs only.

#### Scenario: Consecutive text deltas merged
- **WHEN** three consecutive `PartDeltaEvent` with `TextPartDelta` are published for the same session
- **THEN** a single `PartDeltaEvent` with concatenated `content_delta` is dispatched to subscribers

#### Scenario: Type change triggers flush
- **WHEN** a `PartDeltaEvent` with `TextPartDelta` is followed by a `PartDeltaEvent` with `ThinkingPartDelta` for the same session
- **THEN** the text delta batch is flushed and dispatched before the thinking delta begins a new buffer

#### Scenario: Buffer cap triggers flush
- **WHEN** 20 consecutive `PartDeltaEvent` with `TextPartDelta` are published without type change for the same session
- **THEN** the buffer is flushed and dispatched when the 20th event arrives, even though the type has not changed

### Requirement: Immediate events bypass coalescing buffer
Lifecycle events SHALL bypass the coalescing buffer and be dispatched immediately. Before dispatching, any pending buffered events for the session SHALL be drained (flushed and sent). Lifecycle events include `RunStartedEvent`, `RunErrorEvent`, `RunFailedEvent`, `StreamCompleteEvent`, `SpawnSessionStart`, `CompactionEvent`, `SessionResumeEvent`, `ToolCallStartEvent`, `ToolCallCompleteEvent`, and `ToolCallDeferredEvent`.

#### Scenario: StreamCompleteEvent drains buffer
- **WHEN** a `StreamCompleteEvent` is published for a session that has buffered events
- **THEN** all buffered events are merged and dispatched before the `StreamCompleteEvent` is sent

#### Scenario: Immediate event with empty buffer
- **WHEN** a `ToolCallStartEvent` is published for a session with an empty buffer
- **THEN** the `ToolCallStartEvent` is dispatched immediately with no drain overhead

### Requirement: Per-session coalescing isolation
Each session's coalescing buffer SHALL be independent. Buffered events for session A SHALL NOT affect or be merged with events for session B. The `_buffers` and `_last_keys` dicts SHALL be keyed by `session_id`.

#### Scenario: Independent session buffers
- **WHEN** session A has 5 buffered text deltas and session B has 3 buffered text deltas
- **THEN** a type change on session A flushes only session A's buffer; session B's buffer is unaffected

### Requirement: Coalescing does not change event types
Merged events SHALL retain their original event type (`PartDeltaEvent`, `ToolCallProgressEvent`, etc.). No new event types (e.g., `EventBatch`) SHALL be introduced. Downstream consumers SHALL receive the same event types as before, with potentially larger content payloads.

#### Scenario: Merged PartDeltaEvent retains type
- **WHEN** five text deltas are merged
- **THEN** the dispatched event is a `PartDeltaEvent` with `TextPartDelta`, not a new wrapper type

### Requirement: Non-batchable events pass through unchanged
Events that are neither batchable nor immediate (e.g., `SubAgentEvent`, `CustomEvent`, `ToolResultMetadataEvent`) SHALL be dispatched individually without buffering or merging. These events SHALL trigger a buffer drain of any pending batchable events before being dispatched.

#### Scenario: SubAgentEvent passes through
- **WHEN** a `SubAgentEvent` is published
- **THEN** any pending buffered events are drained and dispatched first, then the `SubAgentEvent` is dispatched individually

#### Scenario: CustomEvent passes through
- **WHEN** a `CustomEvent` is published
- **THEN** any pending buffered events are drained and dispatched first, then the `CustomEvent` is dispatched individually

### Requirement: PartDeltaEvent with None delta is dropped
The coalescing system SHALL drop `PartDeltaEvent` instances where `delta` is `None`. Such events SHALL NOT be buffered, merged, or dispatched.

#### Scenario: None delta dropped
- **WHEN** a `PartDeltaEvent` with `delta=None` is published
- **THEN** the event is discarded without affecting the buffer or subscribers

### Requirement: Coalescing buffer drained on session close
When `EventBus.close_session(session_id)` is called and the session has buffered events, the system SHALL merge and dispatch all buffered events before closing subscriber streams.

#### Scenario: Session close drains buffer
- **WHEN** `close_session(session_id)` is called for a session with 5 buffered text deltas
- **THEN** the buffered deltas are merged and dispatched before the session's subscriber streams are closed
