## ADDED Requirements

### Requirement: EventBus publishes events directly to subscriber queues without buffering
The EventBus SHALL NOT maintain any per-session coalescing buffer. The `publish()` method SHALL send each event directly to matching subscriber queues via the existing `_send()` path. The only preprocessing SHALL be dropping `PartDeltaEvent` instances where `delta` is `None`.

#### Scenario: Events appear in subscriber queue immediately
- **WHEN** a `PartDeltaEvent` with `TextPartDelta` is published for session `s1`
- **THEN** the event is immediately available in all matching subscriber receive streams
- **AND** no intermediate buffer holds the event

#### Scenario: None-delta PartDeltaEvent still dropped
- **WHEN** a `PartDeltaEvent` with `delta=None` is published
- **THEN** the event is discarded without reaching any subscriber queue

#### Scenario: No coalescing state on EventBus
- **WHEN** the EventBus is initialized
- **THEN** it SHALL NOT create `_buffers`, `_last_keys`, `_buf_lock`, or `_max_buffer` attributes
- **AND** the `max_coalesce_buffer` parameter SHALL NOT be accepted

### Requirement: Subscriber-side drain coalesces consecutive same-type events
The event consumer loop SHALL drain all immediately-available events from the receive stream in a single batch using `receive_nowait()` until `WouldBlock` is raised. The drained events SHALL be merged using `itertools.groupby` grouped by merge key before delivery to `_handle_event()`.

#### Scenario: Consecutive text deltas merged at subscriber
- **WHEN** three `PartDeltaEvent` with `TextPartDelta` are published in rapid succession for session `s1`
- **AND** the subscriber wakes and drains all three from the receive stream
- **THEN** a single merged `PartDeltaEvent` with concatenated `content_delta` is delivered to `_handle_event()`

#### Scenario: Type change creates separate batches
- **WHEN** a `PartDeltaEvent` with `TextPartDelta` is followed by a `PartDeltaEvent` with `ThinkingPartDelta`
- **AND** both are drained in the same batch
- **THEN** two merged `PartDeltaEvent` instances are delivered to `_handle_event()` — one with text, one with thinking

#### Scenario: WouldBlock ends drain cycle
- **WHEN** the subscriber calls `receive_nowait()` and `WouldBlock` is raised
- **THEN** the drain loop exits
- **AND** all collected events are merged and delivered
- **AND** the consumer loop continues to the next `await stream.receive()`

#### Scenario: EndOfStream from receive_nowait during drain
- **WHEN** the subscriber has collected 2 events via `receive_nowait()` and then `receive_nowait()` raises `EndOfStream` (stream closed mid-drain)
- **THEN** the 2 collected events are merged and delivered to `_handle_event()`
- **AND** the consumer loop terminates after processing the batch

#### Scenario: EndOfStream from initial receive
- **WHEN** the subscriber calls `await stream.receive()` and `EndOfStream` is raised (stream closed, no items)
- **THEN** no events are delivered
- **AND** the consumer loop terminates immediately

### Requirement: Merge keys and merge semantics preserved
The merge key for `PartDeltaEvent` SHALL be the delta type (text, thinking) or `(tool_call, tool_call_id)` for tool_call deltas. The merge key for `ToolCallProgressEvent` SHALL be `(tool_call_id, status)`. The merge key for `PlanUpdateEvent` SHALL be `("plan", "")`. Merged `PartDeltaEvent` instances SHALL concatenate their `content_delta`/`args_delta` strings and use the first event's `index` and `tool_call_id`. Merged `ToolCallProgressEvent` instances SHALL concatenate their `items` sequences and preserve the last event's `title`, `status`, `replace_content`, and `tool_name`. Merged `PlanUpdateEvent` instances SHALL keep the last event (last-wins semantics). Events separated by a different merge key SHALL NOT be merged, even if they share the same merge key. Coalescing operates on consecutive runs only.

#### Scenario: Text deltas with same merge key merged
- **WHEN** five `PartDeltaEvent` with `TextPartDelta` appear in one drain batch
- **THEN** they are merged into one `PartDeltaEvent` with concatenated `content_delta`

#### Scenario: Tool call deltas keyed by tool_call_id
- **WHEN** two `PartDeltaEvent` with `ToolCallPartDelta` for `tcid="t1"` and one for `tcid="t2"` appear in one drain batch
- **THEN** two merged events are produced — one for `t1` (two deltas concatenated, first event's `tool_call_id` preserved) and one for `t2` (single delta)

#### Scenario: PlanUpdateEvent uses last-wins
- **WHEN** three `PlanUpdateEvent` instances appear in one drain batch
- **THEN** a single `PlanUpdateEvent` is produced, preserving the last event's content

### Requirement: Lifecycle events delivered without coalescing delay
Lifecycle events (`RunStartedEvent`, `RunErrorEvent`, `RunFailedEvent`, `StreamCompleteEvent`, `SpawnSessionStart`, `CompactionEvent`, `SessionResumeEvent`, `ToolCallStartEvent`, `ToolCallCompleteEvent`, `ToolCallDeferredEvent`) SHALL be delivered to `_handle_event()` as-is. If they appear in a drain batch alongside batchable events, they SHALL be delivered individually without merging, and SHALL NOT be merged with batchable events.

#### Scenario: StreamCompleteEvent in drain batch
- **WHEN** a drain batch contains two `PartDeltaEvent` with `TextPartDelta` followed by a `StreamCompleteEvent`
- **THEN** the two text deltas are merged into one `PartDeltaEvent`
- **AND** the `StreamCompleteEvent` is delivered as a separate event
- **AND** both are delivered to `_handle_event()` in order

#### Scenario: Lifecycle event alone in batch
- **WHEN** a drain batch contains only a `ToolCallStartEvent`
- **THEN** the `ToolCallStartEvent` is delivered to `_handle_event()` unchanged

### Requirement: Passthrough events delivered individually
Events that are neither batchable nor lifecycle (e.g., `SubAgentEvent`, `CustomEvent`, `ToolResultMetadataEvent`) SHALL be delivered to `_handle_event()` individually without merging. If they appear in a drain batch alongside batchable events, the batchable events SHALL still be merged among themselves.

#### Scenario: SubAgentEvent coexists with text deltas in batch
- **WHEN** a drain batch contains two `PartDeltaEvent` with `TextPartDelta` and one `SubAgentEvent`
- **THEN** the two text deltas are merged into one `PartDeltaEvent`
- **AND** the `SubAgentEvent` is delivered individually
- **AND** both are delivered in their original relative order

### Requirement: Coalescing does not change event types
Merged events SHALL retain their original event type (`PartDeltaEvent`, `ToolCallProgressEvent`). No new event types (e.g., `EventBatch`) SHALL be introduced. Downstream consumers SHALL receive the same event types as before, with potentially larger content payloads.

#### Scenario: Merged PartDeltaEvent retains type
- **WHEN** five text deltas are merged at subscriber side
- **THEN** the delivered event is a `PartDeltaEvent` with `TextPartDelta`, not a new wrapper type

### Requirement: Per-session drain isolation
Each session's consumer drain loop SHALL be independent. Events drained for session A SHALL NOT be merged with events for session B. Each consumer's receive stream is separate.

#### Scenario: Independent session drains
- **WHEN** session A's consumer drains 5 text deltas and session B's consumer drains 3 text deltas
- **THEN** session A's consumer delivers one merged event with 5 concatenated deltas
- **AND** session B's consumer delivers one merged event with 3 concatenated deltas

### Requirement: Reusable drain_and_merge utility
A `drain_and_merge(stream)` async utility function SHALL be provided that any EventBus consumer can use. It SHALL implement the drain-and-merge pattern: block on `await stream.receive()`, then drain via `receive_nowait()` until `WouldBlock` or `EndOfStream`, merge the batch, and yield merged envelopes. All EventBus consumer paths (`ProtocolEventConsumerMixin`, standalone `run_stream()` Path B, `serve_mcp.py` consumer) SHALL use this utility to ensure consistent coalescing behavior.

#### Scenario: drain_and_merge used by ProtocolEventConsumerMixin
- **WHEN** a protocol server's consumer loop processes events
- **THEN** it calls `drain_and_merge(stream)` to get merged batches

#### Scenario: drain_and_merge used by standalone run_stream
- **WHEN** an agent runs in standalone mode (no SessionPool) via `run_stream()` Path B
- **THEN** it calls `drain_and_merge(stream)` to get merged batches
- **AND** coalescing behavior matches the protocol server consumer

#### Scenario: drain_and_merge used by serve_mcp
- **WHEN** the MCP server consumes stream completion events
- **THEN** it calls `drain_and_merge(stream)` to get merged batches

### Requirement: Merge helpers are pure module-level functions
The merge key computation (`_merge_key`), immediate event classification (`_is_immediate`), and merge functions (`_merge_text_deltas`, `_merge_thinking_deltas`, `_merge_tool_call_deltas`, `_merge_progress_events`, `_merge_envelopes`) SHALL be module-level functions with no dependency on EventBus instance state.

#### Scenario: Merge function called without EventBus instance
- **WHEN** a test imports `_merge_envelopes` from the orchestrator module
- **THEN** it can be called with a list of `EventEnvelope` objects
- **AND** no EventBus instance is required

### Requirement: No buffer cap or cap warning
The system SHALL NOT impose a maximum buffer size on coalescing. The `max_coalesce_buffer` parameter SHALL be removed from EventBus constructor. The `"Coalescing buffer cap reached, flushing"` warning SHALL NOT exist.

#### Scenario: Long text generation without cap warning
- **WHEN** 100 consecutive `PartDeltaEvent` with `TextPartDelta` are published for a session
- **AND** the subscriber drains all 100 in one batch
- **THEN** all 100 are merged into a single `PartDeltaEvent`
- **AND** no warning is logged
