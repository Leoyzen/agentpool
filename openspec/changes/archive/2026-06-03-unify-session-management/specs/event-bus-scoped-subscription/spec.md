## ADDED Requirements

### Requirement: EventBus supports scoped subscriptions
The system SHALL allow subscribers to specify a `scope` when subscribing to a session, controlling which sessions' events they receive.

#### Scenario: Session scope receives only own events
- **WHEN** a subscriber calls `event_bus.subscribe("s1", scope="session")`
- **AND** an event is published to `s1`
- **THEN** the subscriber receives the event

#### Scenario: Session scope excludes child events
- **WHEN** a subscriber calls `event_bus.subscribe("s1", scope="session")`
- **AND** an event is published to child session `s1.1`
- **THEN** the subscriber does NOT receive the event

### Requirement: Descendants scope routes child events to parent subscribers
The system SHALL automatically forward events from child sessions to subscribers of the parent session with `scope="descendants"`.

#### Scenario: Parent subscriber receives child events
- **WHEN** a subscriber calls `event_bus.subscribe("s1", scope="descendants")`
- **AND** an event is published to child session `s1.1`
- **THEN** the subscriber receives the event

#### Scenario: Deep descendant routing
- **WHEN** a subscriber calls `event_bus.subscribe("s1", scope="descendants")`
- **AND** an event is published to grandchild session `s1.1.1`
- **THEN** the subscriber receives the event

#### Scenario: Parent events still received with descendants scope
- **WHEN** a subscriber calls `event_bus.subscribe("s1", scope="descendants")`
- **AND** an event is published to `s1` itself
- **THEN** the subscriber receives the event

### Requirement: Subtree scope routes full tree events
The system SHALL route events from the target session, its parent, and all siblings/children when `scope="subtree"` is used.

#### Scenario: Subtree scope receives sibling events
- **GIVEN** sessions `s1`, `s1.1`, `s1.2` (children of `s1`)
- **WHEN** a subscriber calls `event_bus.subscribe("s1.1", scope="subtree")`
- **AND** an event is published to `s1.2`
- **THEN** the subscriber receives the event

#### Scenario: Subtree scope receives parent events
- **GIVEN** sessions `s1`, `s1.1`
- **WHEN** a subscriber calls `event_bus.subscribe("s1.1", scope="subtree")`
- **AND** an event is published to `s1`
- **THEN** the subscriber receives the event

### Requirement: StreamEventEmitter publishes to SessionPool EventBus
The system SHALL route agent events through the unified `SessionPool.event_bus` instead of the per-turn `run_ctx.event_bus`.

#### Scenario: Background task events reach EventBus
- **GIVEN** a turn has completed and `run_ctx` event consumer has stopped
- **WHEN** a background task emits an event via `ctx.events.emit_event()`
- **THEN** the event is published to `session_pool.event_bus` using the agent's current session ID
- **AND** subscribers with matching scope receive the event

#### Scenario: EventBus fallback when no SessionPool
- **GIVEN** a `BaseAgent` is used standalone without an `AgentPool`
- **WHEN** an event is emitted during `run_stream()`
- **THEN** the event falls back to `agent._event_queue` or `run_ctx.event_queue`
- **AND** no `SessionPool` interaction is attempted
