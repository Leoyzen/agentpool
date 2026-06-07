## ADDED Requirements

### Requirement: Protocol layer auto-subscribes to subagent events
The OpenCode protocol layer SHALL automatically subscribe to EventBus events for a child session when it receives a `SpawnSessionStart` event.

#### Scenario: SpawnSessionStart triggers auto-subscription
- **WHEN** the protocol layer receives a `SpawnSessionStart` event with `child_session_id`
- **THEN** it SHALL subscribe to the EventBus for that session ID with `scope="session"`
- **AND** it SHALL forward all received events to the frontend via SSE

### Requirement: Events are wrapped as SubAgentEvent
All events from the child session SHALL be wrapped in `SubAgentEvent` before being sent to the frontend.

#### Scenario: Text delta from subagent reaches frontend
- **WHEN** a `PartDeltaEvent` is received from the child session's EventBus subscription
- **THEN** it SHALL be wrapped as `SubAgentEvent`
- **AND** it SHALL be broadcast to all SSE subscribers

#### Scenario: Tool call from subagent reaches frontend
- **WHEN** a `ToolCallStartEvent` is received from the child session
- **THEN** it SHALL be wrapped as `SubAgentEvent`
- **AND** the frontend SHALL display the tool call in the subagent's card

### Requirement: Subscription is cleaned up on completion
The protocol layer SHALL cancel the EventBus subscription when the child session completes or errors.

#### Scenario: StreamCompleteEvent cancels subscription
- **WHEN** a `StreamCompleteEvent` is received from the child session
- **THEN** the protocol layer SHALL unsubscribe from the EventBus
- **AND** it SHALL emit a final `SubAgentEvent` with the completion status

#### Scenario: RunErrorEvent cancels subscription
- **WHEN** a `RunErrorEvent` is received from the child session
- **THEN** the protocol layer SHALL unsubscribe from the EventBus
- **AND** it SHALL emit a `SubAgentEvent` with the error details

### Requirement: BackgroundTaskProvider no longer manually handles events
When running via SessionPool, the `BackgroundTaskProvider` SHALL NOT manually subscribe to EventBus or emit `SubAgentEvent`.

#### Scenario: SessionPool path delegates to protocol layer
- **WHEN** a background task uses the SessionPool path (`_session_pool_available = True`)
- **THEN** `_consume_events_to_fs` SHALL only write to the filesystem
- **AND** it SHALL NOT emit `SubAgentEvent` to the parent stream
- **AND** the protocol layer SHALL handle all event forwarding
