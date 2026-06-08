## ADDED Requirements

### Requirement: Runner layer emits raw events without SubAgentEvent wrapping
The TurnRunner SHALL NOT wrap child session events in SubAgentEvent envelopes. All events emitted by the runner layer SHALL be raw event types (PartDeltaEvent, StreamCompleteEvent, ToolCallStartEvent, etc.).

#### Scenario: Child session event emission
- **WHEN** a child session agent emits a StreamCompleteEvent
- **THEN** the TurnRunner publishes the raw StreamCompleteEvent to the EventBus without wrapping

#### Scenario: Parent session event emission
- **WHEN** a parent session agent emits a PartDeltaEvent
- **THEN** the TurnRunner publishes the raw PartDeltaEvent to the EventBus without wrapping

### Requirement: Protocol layers route events by session_id
Protocol layer event consumers SHALL use the `session_id` field on each event to determine which session context should process the event. Events with a `session_id` different from the consumer's primary session SHALL be routed to the corresponding child session context.

#### Scenario: opencode server receives child session event
- **WHEN** the opencode event processor receives a PartDeltaEvent with session_id="child-123"
- **THEN** it routes the event to the EventProcessorContext for session "child-123"

#### Scenario: ACP server receives child session event
- **WHEN** the ACP event converter receives a ToolCallStartEvent with session_id="child-456"
- **THEN** it routes the event to the converter state for session "child-456"

### Requirement: EventBus descendants scope delivers raw child events
The EventBus SHALL deliver raw child session events to subscribers using scope="descendants" without requiring SubAgentEvent wrapping.

#### Scenario: Parent subscriber receives child events
- **WHEN** a subscriber subscribes to session_id="parent-abc" with scope="descendants"
- **THEN** it receives all raw events from "parent-abc" and its child sessions

### Requirement: Background task sync path matches raw completion events
BackgroundTaskProvider._task_sync SHALL match raw StreamCompleteEvent and ToolCallStartEvent/ToolCallCompleteEvent directly without unwrapping SubAgentEvent.

#### Scenario: Sync task completes with StreamCompleteEvent
- **WHEN** a sync task run_stream yields a StreamCompleteEvent
- **THEN** _task_sync captures the result and returns it to the lead agent

#### Scenario: Sync task completes with attempt_completion tool call
- **WHEN** a sync task run_stream yields a ToolCallStartEvent for "attempt_completion"
- **THEN** _task_sync captures the result and returns it to the lead agent

### Requirement: ACP subagent rendering uses legacy mode only
The ACP event converter SHALL remove inline and tool_box subagent display modes. Subagent events SHALL be rendered using the legacy mode only until official RFD implementation.

#### Scenario: ACP converter receives subagent event
- **WHEN** the ACP converter receives an event from a child session
- **THEN** it renders the event using the legacy subagent conversion path
