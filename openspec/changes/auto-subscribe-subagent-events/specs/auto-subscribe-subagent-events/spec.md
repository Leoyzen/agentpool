## ADDED Requirements

### Requirement: Protocol handlers use shared mixin for event consumption
All protocol handlers that consume agent stream events SHALL inherit from `ProtocolEventConsumerMixin`. The mixin SHALL manage EventBus subscription, the consumer loop, cleanup, and recursive child session consumer spawning. Protocol handlers SHALL implement `_handle_event()` for protocol-specific conversion and delivery.

#### Scenario: Mixin provides subscription lifecycle
- **WHEN** a protocol handler calls `start_event_consumer(session_id)`
- **THEN** the mixin subscribes to the EventBus with `scope="descendants"`
- **AND** an asyncio Task is created to run the consumer loop
- **AND** calling `start_event_consumer()` again for the same session is a no-op

#### Scenario: Mixin stops consumer cleanly
- **WHEN** a protocol handler calls `stop_event_consumer(session_id)`
- **THEN** the consumer task is cancelled
- **AND** the EventBus subscription is removed
- **AND** internal state (`_consumer_tasks`, `_consumer_queues`) is cleaned up

#### Scenario: Mixin handles SpawnSessionStart recursively
- **WHEN** a `SpawnSessionStart` event arrives on the consumer queue
- **THEN** the mixin calls `_handle_spawn_session_start()`
- **AND** the mixin starts a new consumer for `event.child_session_id`
- **AND** child events are received and dispatched independently

#### Scenario: Mixin is resilient to handler errors
- **WHEN** `_handle_event()` raises an exception
- **THEN** the exception is logged
- **AND** the consumer loop continues processing subsequent events

### Requirement: OpenCode handler uses mixin with zero behavior change
The OpenCode protocol handler SHALL inherit from `ProtocolEventConsumerMixin`. It SHALL implement `_handle_event()` to convert events to OpenCode SSE events and broadcast them. It SHALL implement `_handle_spawn_session_start()` if ToolPart lifecycle requires it. All existing OpenCode tests SHALL pass without modification.

#### Scenario: OpenCode backward compatibility
- **WHEN** the OpenCode handler processes a prompt with a subagent
- **THEN** all events are forwarded to the OpenCode client
- **AND** the event sequence is identical to the pre-refactor behavior

### Requirement: ACP handler uses mixin and handles raw child events
The ACP protocol handler SHALL inherit from `ProtocolEventConsumerMixin`. It SHALL implement `_handle_event()` to convert events to ACP `session/update` notifications. It SHALL distinguish parent vs child events by `event.session_id` and route each to the correct per-session converter. It SHALL implement `_handle_spawn_session_start()` to pre-create converters for child sessions.

#### Scenario: ACP forwards raw child events
- **WHEN** a subagent creates a child session and emits a `PartDeltaEvent`
- **THEN** the ACP handler receives the event via the descendant scope
- **AND** the handler routes it to the child session's converter
- **AND** a `session/update` notification is sent to the ACP client

#### Scenario: ACP nested subagents
- **WHEN** a subagent spawns another subagent
- **THEN** the mixin starts a consumer for the grandchild session
- **AND** events from all levels are forwarded as separate `session/update` notifications

### Requirement: No SubAgentEvent wrapping at protocol layer
Protocol handlers SHALL NOT wrap raw events in `SubAgentEvent`. Converters SHALL receive raw `RichAgentStreamEvent` objects and perform protocol-specific formatting directly.

#### Scenario: Raw PartDeltaEvent reaches converter
- **WHEN** a child session emits a `PartDeltaEvent`
- **THEN** the protocol handler receives the raw `PartDeltaEvent`
- **AND** it is passed directly to the protocol converter
- **AND** no `SubAgentEvent` wrapping occurs

### Requirement: Subscription cleanup guarantees
No EventBus subscription SHALL leak after session close. The mixin SHALL unsubscribe in `finally` blocks, on cancellation, and on `None` sentinel.

#### Scenario: No leaked subscriptions after repeated start/stop
- **WHEN** `start_event_consumer()` and `stop_event_consumer()` are called 3 times for the same session
- **THEN** subscribe and unsubscribe counts match exactly
- **AND** no internal state remains after the final stop
