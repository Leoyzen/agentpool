## MODIFIED Requirements

### Requirement: Each session SHALL have at most one EventBus subscriber that processes events

Each session SHALL have exactly one primary EventBus consumer that handles all event types for that session. There SHALL be no parallel subscribers (such as `SessionStatusBridge`) that independently subscribe to the same session's EventBus events. The consumer SHALL perform subscriber-side drain coalescing: after receiving the first event via `await stream.get()`, the consumer SHALL drain all immediately-available events via `stream.get_nowait()` until `queue.Empty`, merge consecutive same-type events, and deliver merged events to `_handle_event()`.

- EventBus stream SHALL be an `asyncio.Queue`
- Consumer SHALL use `await stream.get()` (NOT `stream.receive()`) to await the next event
- Consumer SHALL use `stream.get_nowait()` (NOT `stream.receive_nowait()`) to drain immediately-available events
- Producer SHALL use `await stream.put()` (NOT `stream.send()`) to enqueue events
- Producer SHALL use `stream.put_nowait()` (NOT `stream.send_nowait()`) for non-blocking enqueue
- `global_routes.py` event generator SHALL use `get()` on the EventBus stream
- `core.py` event coalescing SHALL use `get_nowait()` and `put_nowait()` on EventBus streams

#### Scenario: Single subscriber per session with drain coalescing
- **WHEN** a session is created and event consumption begins
- **THEN** exactly one consumer SHALL subscribe to that session's EventBus events
- **AND** the consumer SHALL drain and merge events before calling `_handle_event()`
- **AND** the consumer SHALL use `get()` and `get_nowait()` (not `receive()` / `receive_nowait()`)

#### Scenario: Status events handled inline
- **WHEN** `RunStartedEvent`, `StreamCompleteEvent`, or `RunFailedEvent` are published for a session
- **THEN** the session's single EventBus consumer SHALL handle these events directly in its `_handle_event` method, broadcasting `SessionStatusEvent` / `SessionErrorEvent` as appropriate, without a separate `SessionStatusBridge` subscription

#### Scenario: No duplicate status broadcasts
- **WHEN** a `RunStartedEvent` is published for a session
- **THEN** `SessionStatusEvent(type="busy")` SHALL be broadcast exactly once, not duplicated from both the adapter and the bridge

#### Scenario: Global event endpoint serves events
- **WHEN** a client connects to the global event SSE endpoint
- **THEN** the event generator SHALL use `await stream.get()` to receive events from the EventBus
- **AND** no `AttributeError: 'Queue' object has no attribute 'receive'` SHALL occur
- **AND** events SHALL be serialized and delivered to the client

#### Scenario: Event coalescing uses correct Queue methods
- **WHEN** the event coalescing logic in `core.py` drains available events
- **THEN** it SHALL use `stream.get_nowait()` (not `stream.receive_nowait()`)
- **AND** it SHALL catch `asyncio.QueueEmpty` (not `anyio.WouldBlock`)
- **AND** merged events SHALL be delivered to `_handle_event()`

#### Scenario: Event processing skipped with drain still active
- **WHEN** `_skip_event_processing` is `True` and a non-`SpawnSessionStart` event is received
- **THEN** the consumer loop SHALL drain all available events from the queue (including calling `get_nowait()`)
- **AND** merged events SHALL NOT be passed to `_handle_event()`
