## ADDED Requirements

### Requirement: All events flow through EventBus with stream bridge
The system SHALL publish all agent stream events and tool events to `EventBus`. `run_ctx.event_queue` SHALL NOT be used as an event channel between tools and the stream consumer. `TurnRunner` SHALL create a per-run EventBus subscriber that feeds events back into the stream. `TurnRunner` SHALL NOT start a `_consume_event_queue` background task.

#### Scenario: Tool event does not enter run_ctx.event_queue
- **WHEN** a tool emits an event via `StreamEventEmitter._emit()`
- **THEN** the event is published directly to `EventBus`
- **AND** the event is NOT put into `run_ctx.event_queue`

#### Scenario: No dual-consumer race
- **WHEN** a tool emits an event during an active turn
- **THEN** the event appears exactly once in the EventBus
- **AND** the event is NOT consumed by a competing `run_ctx.event_queue` reader

#### Scenario: Tool events visible in stream
- **WHEN** a tool emits events during agent execution
- **THEN** the events are yielded by `agent._run_stream_once()`
- **AND** the events are visible to the stream consumer (TurnRunner)

#### Scenario: TurnRunner stream forwarding
- **WHEN** `TurnRunner` executes `_run_stream_once()` and yields events
- **THEN** each yielded event is published to `EventBus` exactly once
- **AND** no fallback consumer duplicates the event

#### Scenario: NativeAgent process_tool_event works
- **WHEN** tool events flow through the TurnRunner-managed stream
- **THEN** `NativeAgent._stream_events()` calls `process_tool_event()` on those events
- **AND** combined tool call events are correctly generated

#### Scenario: ClaudeCodeAgent event flow
- **WHEN** a ClaudeCodeAgent runs through SessionPool
- **AND** a tool emits events
- **THEN** the events flow through EventBus and back into the stream
- **AND** no dual-consumer race occurs

#### Scenario: ACPAgent event flow
- **WHEN** an ACPAgent runs through SessionPool
- **AND** a tool emits events
- **THEN** the events flow through EventBus and back into the stream
- **AND** no dual-consumer race occurs

### Requirement: EventBus descendant scope routes child events to parent
Protocol handlers SHALL subscribe to `EventBus` with `scope="descendants"`. The system SHALL deliver events from child sessions to parent session subscribers automatically.

#### Scenario: ACP handler receives child events
- **WHEN** an ACP client subscribes to a parent session
- **AND** a subagent creates a child session and emits events
- **THEN** the ACP client receives the child session events

#### Scenario: OpenCode handler receives child events
- **WHEN** an OpenCode client subscribes to a parent session
- **AND** a subagent creates a child session and emits events
- **THEN** the OpenCode client receives the child session events

#### Scenario: AG-UI handler receives child events
- **WHEN** an AG-UI client subscribes to a parent session
- **AND** a subagent creates a child session and emits events
- **THEN** the AG-UI client receives the child session events
