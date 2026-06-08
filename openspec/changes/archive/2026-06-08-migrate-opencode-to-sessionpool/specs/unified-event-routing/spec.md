## MODIFIED Requirements

### Requirement: All events flow through EventBus with stream bridge
The system SHALL publish all agent stream events and tool events to `EventBus`. `run_ctx.event_queue` SHALL NOT be used as an event channel between tools and the stream consumer. `TurnRunner` SHALL create a per-run EventBus subscriber that feeds events back into the stream. *(Note: Removing the existing `_consume_event_queue` background task in `TurnRunner` requires coordinated changes to `AgentContext.report_progress`, `StreamEventEmitter._emit`, and `ClaudeCodeAgent` event routing. This is deferred to a follow-up infrastructure cleanup change — not part of the OpenCode Server migration.)*

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

#### Scenario: OpenCode SSE events from EventBus with replay
- **WHEN** an OpenCode client opens an SSE connection for a session
- **THEN** the server creates an EventBus subscriber for that session (with `scope="descendants"`)
- **AND** recent historical events are replayed from the EventBus replay buffer
- **AND** subsequent live events are streamed from the subscriber
- **AND** `RichAgentStreamEvent` objects are converted to OpenCode protocol events via `OpenCodeEventAdapter`
- **AND** OpenCode protocol events (`MessageUpdatedEvent`, `PartUpdatedEvent`, `SessionStatusEvent`) that are NOT `RichAgentStreamEvent` types continue to flow through `state.broadcast_event()` and `state.event_subscribers` during Migration A
- **AND** an `OpenCodeEventBridge` converts OpenCode protocol events to `RichAgentStreamEvent` wrappers and republishes them to EventBus (preparing for Migration B when SSE will subscribe to EventBus directly). During Migration A, SSE subscribers continue to receive all events through `state.event_subscribers`, not EventBus.
- **AND** `ServerState.messages[session_id]` is NOT used as the live event source (but may be retained as a backup until Migration B)

#### Scenario: OpenCodeEventBridge converts protocol events
- **WHEN** a route calls `state.broadcast_event(MessageUpdatedEvent.create(...))`
- **THEN** the `OpenCodeEventBridge` intercepts the event (via a lightweight wrapper around `broadcast_event` or via separate subscription)
- **AND** converts it to a `RichAgentStreamEvent` wrapper
- **AND** republishes it to the EventBus for the session
- **AND** the original OpenCode event is still delivered to `state.event_subscribers` for backward compatibility during Migration A

### Requirement: Streaming endpoints subscribe with descendants scope
Streaming endpoints that invoke subagents (`_execute_slashed_command`, `_execute_skill_command`) SHALL subscribe to EventBus with `scope="descendants"` so child session events are visible.

#### Scenario: Subagent events visible in slash command stream
- **WHEN** `_execute_slashed_command()` calls `SessionPool.run_stream(session_id, ...)`
- **AND** the agent uses a subagent tool that creates a child session
- **THEN** the `run_stream()` subscription uses `scope="descendants"`
- **AND** child session events appear in the parent's stream
- **AND** the `OpenCodeStreamAdapter` receives tool events from the child session

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

## ADDED Requirements

### Requirement: EventBus provides replay buffer for SSE subscribers
The EventBus or `SessionState` SHALL maintain a bounded replay buffer of recent events so that new SSE subscribers receive historical messages before live events.

#### Scenario: New SSE subscriber receives history
- **GIVEN** a session has produced 50 events in the current run
- **WHEN** a new SSE subscriber connects
- **THEN** the subscriber receives the last N events from the replay buffer (where N is configurable)
- **AND** then receives subsequent live events

#### Scenario: Replay buffer is bounded
- **GIVEN** the replay buffer size is configured to 100 events
- **WHEN** more than 100 events are produced
- **THEN** oldest events are discarded from the buffer
- **AND** new subscribers do not receive discarded events

### Requirement: ServerState.messages is retained until replacement API exists
`ServerState.messages` SHALL NOT be removed until `SessionPool` exposes a message history API (`get_messages`, `append_message`, `truncate_messages`, `copy_messages`). During Migration A, `messages` continues to serve as the canonical message store.

#### Scenario: Message history available during Migration A
- **WHEN** `share_session()` or `revert_session()` is called during Migration A
- **THEN** the endpoint reads from `ServerState.messages` as before
- **AND** no SessionPool message history API is required

#### Scenario: Message history API prerequisite for Migration B
- **GIVEN** Migration A is complete and all routes use SessionPool
- **WHEN** the team begins Migration B
- **THEN** the first task is to design and implement the SessionPool message history API
- **AND** `ServerState.messages` is removed only after all 56+ references are migrated
