## MODIFIED Requirements

### Requirement: Each session SHALL have at most one EventBus subscriber that processes events
Each session SHALL have exactly one primary EventBus consumer that handles all event types for that session. There SHALL be no parallel subscribers (such as `SessionStatusBridge`) that independently subscribe to the same session's EventBus events. The consumer SHALL perform subscriber-side drain coalescing: after receiving the first event via `await stream.receive()`, the consumer SHALL drain all immediately-available events via `stream.receive_nowait()` until `WouldBlock`, merge consecutive same-type events, and deliver merged events to `_handle_event()`.

#### Scenario: Single subscriber per session with drain coalescing
- **WHEN** a session is created and event consumption begins
- **THEN** exactly one consumer SHALL subscribe to that session's EventBus events
- **AND** the consumer SHALL drain and merge events before calling `_handle_event()`

#### Scenario: Status events handled inline
- **WHEN** `RunStartedEvent`, `StreamCompleteEvent`, or `RunFailedEvent` are published for a session
- **THEN** the session's single EventBus consumer SHALL handle these events directly in its `_handle_event` method, broadcasting `SessionStatusEvent` / `SessionErrorEvent` as appropriate, without a separate `SessionStatusBridge` subscription

#### Scenario: No duplicate status broadcasts
- **WHEN** a `RunStartedEvent` is published for a session
- **THEN** `SessionStatusEvent(type="busy")` SHALL be broadcast exactly once, not duplicated from both the adapter and the bridge

### Requirement: EventBusHooksAdapter SHALL be removed
The `EventBusHooksAdapter` class SHALL be removed. Its `before_run` hook (which publishes `RunStartedEvent`) is redundant with `RunExecutor`'s own `RunStartedEvent` publishing. Its `before_tool_execute` and `after_tool_execute` hooks are already self-admitted as redundant in their own docstring.

#### Scenario: RunStartedEvent publishing
- **WHEN** a run starts
- **THEN** `RunExecutor.execute()` SHALL be the sole publisher of `RunStartedEvent`, and no `EventBusHooksAdapter` shall duplicate this

#### Scenario: Tool event publishing
- **WHEN** a tool call starts or completes
- **THEN** `RunExecutor` SHALL handle event conversion and publishing, without any `EventBusHooksAdapter` wrapping

### Requirement: Protocol servers with no-op handlers SHALL skip event processing
Protocol servers (AG-UI, OpenAI API) that do not process events themselves SHALL set `_skip_event_processing = True` on `ProtocolEventConsumerMixin`. The consumer loop SHALL still subscribe to EventBus to detect `SpawnSessionStart` for child consumer lifecycle, but SHALL skip `_handle_event()` for all other events. The drain-and-merge coalescing SHALL still occur (events are drained from the queue), but merged events are discarded when `_skip_event_processing` is `True`.

#### Scenario: AG-UI child consumer management
- **WHEN** a `SpawnSessionStart` event indicates a child session should be created for AG-UI
- **THEN** the child consumer SHALL be started via `_on_spawn_session_start()`, and `_handle_event()` SHALL NOT be called for non-spawn events

#### Scenario: OpenAI API child consumer management
- **WHEN** a `SpawnSessionStart` event indicates a child session should be created for OpenAI API
- **THEN** the child consumer SHALL be started via `_on_spawn_session_start()`, and `_handle_event()` SHALL NOT be called for non-spawn events

#### Scenario: Event processing skipped with drain still active
- **WHEN** `_skip_event_processing` is `True` and a non-`SpawnSessionStart` event is received
- **THEN** the consumer loop SHALL drain all available events from the queue (including calling `receive_nowait()`)
- **AND** merged events SHALL NOT be passed to `_handle_event()`
