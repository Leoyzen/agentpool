## ADDED Requirements

### Requirement: EventBus subscriber queues use anyio memory object streams
The system SHALL replace `asyncio.Queue[EventEnvelope | None]` in `EventBus` subscriber management with `anyio.create_memory_object_stream`. Each `subscribe()` call SHALL return a `MemoryObjectReceiveStream` instead of an `asyncio.Queue`. The `unsubscribe()` call SHALL close the corresponding `MemoryObjectSendStream`, causing the receiver to see `EndOfStream`.

#### Scenario: subscribe() returns a memory object stream
- **WHEN** `EventBus.subscribe(session_id)` is called
- **THEN** a `(send_stream, receive_stream)` pair is created via `anyio.create_memory_object_stream`
- **AND** the receive stream is returned to the caller
- **AND** the send stream is stored internally for future `publish()` calls

#### Scenario: unsubscribe() closes the send stream
- **WHEN** `EventBus.unsubscribe(session_id, receive_stream)` is called
- **THEN** the corresponding send stream is closed
- **AND** the receiver gets `EndOfStream` on the next `receive()` call
- **AND** no sentinel `None` value is needed to signal shutdown

#### Scenario: publish() sends via send stream
- **WHEN** `EventBus.publish(session_id, event)` is called
- **THEN** the event is wrapped in an `EventEnvelope`
- **AND** sent via `send_stream.send(envelope)` to all subscribers whose scope matches
- **AND** if the send stream's buffer is full, `send()` blocks (backpressure) instead of dropping the oldest event

#### Scenario: publish() handles closed streams gracefully
- **WHEN** a subscriber's send stream has been closed
- **AND** `publish()` attempts to send to it
- **THEN** `anyio.ClosedResourceError` is caught
- **AND** the closed stream is removed from the subscriber list

#### Scenario: Replay buffer delivery on subscribe
- **WHEN** a new subscriber calls `subscribe(session_id)`
- **THEN** historical events from the session's replay buffer are sent to the new receive stream before live events
- **AND** ordering is preserved: replay first, then events that arrived during replay

### Requirement: ProtocolEventConsumerMixin consumer loop uses async for over stream
The `_event_consumer_loop` in `ProtocolEventConsumerMixin` SHALL use `async for envelope in receive_stream:` instead of `while True: envelope = await queue.get()`. The loop exits naturally on `EndOfStream` instead of requiring a sentinel `None`.

#### Scenario: Consumer loop with async for
- **WHEN** the event consumer loop starts
- **THEN** it iterates with `async for envelope in receive_stream`
- **AND** the loop exits when `EndOfStream` is raised (stream closed by `unsubscribe()`)
- **AND** no `None` sentinel is needed to signal termination

#### Scenario: ConsumerShutdown still works
- **WHEN** `_handle_event()` raises `ConsumerShutdown`
- **THEN** the loop breaks before continuing to the next `async for` iteration
- **AND** cleanup proceeds as before

### Requirement: Backpressure replaces drop-oldest strategy
The EventBus SHALL use a hybrid backpressure strategy. `publish()` SHALL await `send.send()` with a 0.1s timeout (`anyio.fail_after(0.1)`). On the first 2 consecutive timeouts, the oldest buffered event SHALL be dropped (event-drop fallback). On the 3rd consecutive timeout, the subscriber SHALL be dropped entirely. This preserves the existing degradation mode (event drop is less destructive than subscriber drop) while adding backpressure for short bursts.

#### Scenario: Backpressure blocks publisher briefly, then drops events
- **WHEN** a subscriber is not consuming events fast enough
- **AND** the memory stream buffer is full
- **THEN** `publish()` blocks for up to 0.1s on `send.send()`
- **AND** on the 1st and 2nd consecutive timeouts, the oldest buffered event is dropped
- **AND** a warning is logged for each dropped event

#### Scenario: Persistent slow subscriber is dropped
- **WHEN** a subscriber has 3 consecutive 0.1s timeouts
- **THEN** the subscriber's send stream is closed
- **AND** the subscriber receives `EndOfStream`
- **AND** an error is logged

#### Scenario: Fast subscriber is not affected by slow subscriber
- **WHEN** `publish()` has multiple subscribers
- **AND** one subscriber is slow (triggers timeout)
- **THEN** other subscribers receive events without delay
- **AND** publishing is done concurrently using `anyio.create_task_group()`

### Requirement: Replay buffer format unchanged
The `EventBus` replay buffer (`_replay_buffers: dict[str, deque[EventEnvelope]]`) SHALL remain unchanged. Events are replayed by iterating the deque and calling `send_stream.send()` for each event before live events begin.

#### Scenario: Replay buffer delivery
- **WHEN** a new subscriber subscribes
- **THEN** replay buffer events are sent to the new stream before live events
- **AND** events arriving during replay are queued after historical events
