## ADDED Requirements

### Requirement: ProtocolEventConsumerMixin provides shared event consumer pattern
The `ProtocolEventConsumerMixin` SHALL provide lifecycle management for EventBus consumers shared by all protocol servers.

#### Class Skeleton
```python
class ProtocolEventConsumerMixin(ABC):
    """Mixin providing EventBus consumer lifecycle management.
    
    Subclasses MUST call super().__init__() if they override __init__.
    """
    
    def __init__(self) -> None:
        """Initialize mixin state."""
        super().__init__()
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._consumer_queues: dict[str, asyncio.Queue[Any]] = {}
        self._consumer_locks: dict[str, asyncio.Lock] = {}
        self._consumer_lock_creation_lock: asyncio.Lock = asyncio.Lock()  # Atomic lock creation
    
    @abstractmethod
    async def _handle_event(self, session_id: str, event: RichAgentStreamEvent[Any]) -> None:
        ...
    
    async def _on_spawn_session_start(self, session_id: str, event: SpawnSessionStart) -> None:
        """No-op default. Subclass MAY override to start child consumers."""
    
    async def _before_consumer_loop(self, session_id: str) -> None:
        """No-op default. Called before loop starts reading from queue."""
    
    async def _after_consumer_loop(self, session_id: str) -> None:
        """No-op default. Called after loop exits and unsubscribes."""
    
    def _get_subscription_scope(self) -> str:
        return "descendants"
    
    async def start_event_consumer(self, session_id: str) -> None:
        ...
    
    async def stop_event_consumer(self, session_id: str) -> None:
        ...
```

#### Scenario: Mixin starts consumer on demand
- **WHEN** `start_event_consumer(session_id)` is called
- **THEN** it SHALL atomically create or retrieve the per-session lock using `_consumer_lock_creation_lock`
- **AND** it SHALL acquire the per-session lock
- **AND** it SHALL check if a consumer is already running (idempotent)
- **AND** it SHALL subscribe to the EventBus for that session with configurable scope
- **AND** it SHALL start an async loop reading from the subscription queue
- **AND** concurrent calls for the same session_id SHALL be serialized by the lock

#### Scenario: Mixin stops consumer cleanly
- **WHEN** `stop_event_consumer(session_id)` is called
- **THEN** it SHALL cancel the consumer task
- **AND** it SHALL unsubscribe from the EventBus
- **AND** it SHALL clean up internal state (`_consumer_tasks`, `_consumer_queues`, `_consumer_locks`)
- **AND** it SHALL be safe to call even if no consumer is running

#### Scenario: Hook exceptions propagate and trigger cleanup
- **WHEN** `_before_consumer_loop()`, `_on_spawn_session_start()`, or `_after_consumer_loop()` raises an exception
- **THEN** the exception SHALL propagate out of the mixin
- **AND** the mixin SHALL still perform cleanup (unsubscribe, call `_after_consumer_loop` if applicable) in its `finally` block
- **AND** `_after_consumer_loop()` SHALL be called even if the loop exited via exception, provided the consumer had started

### Requirement: SpawnSessionStart notifies via hook, mixin does not auto-create child consumers
The mixin SHALL detect `SpawnSessionStart` events and notify the subclass via hook. The mixin SHALL NOT automatically start child-session consumers.

#### Scenario: SpawnSessionStart detected in consumer loop
- **WHEN** the consumer loop receives a `SpawnSessionStart` event
- **THEN** it SHALL call `_on_spawn_session_start(session_id, event)` hook
- **AND** the default implementation SHALL be a no-op
- **AND** the subclass MAY call `start_event_consumer(child_session_id)` if it wants child consumers

#### Rationale
ACP does not create child consumers (all descendant events flow through parent converter). OpenCode creates child consumers but manages them separately. Auto-creating child consumers would impose OpenCode's architecture on ACP.

### Requirement: EventBus events are dispatched to protocol-specific handler
The mixin SHALL dispatch each event to an abstract `_handle_event()` hook implemented by the protocol handler.

#### Scenario: RichAgentStreamEvent received from queue
- **WHEN** a non-None event is received from the EventBus queue
- **THEN** if it is a `SpawnSessionStart`, it SHALL call `_on_spawn_session_start(session_id, event)` hook
- **AND** it SHALL call `_handle_event(session_id, event)` for all non-None events (including `SpawnSessionStart`)
- **AND** the mixin SHALL NOT catch exceptions from `_handle_event()` EXCEPT `ConsumerShutdown`
- **AND** if `_handle_event()` raises `ConsumerShutdown`, the mixin SHALL gracefully exit the loop
- **AND** the subclass SHALL handle its own exception recovery for all other exceptions (continue, break, or log)

#### Scenario: Subclass signals loop shutdown via ConsumerShutdown
- **WHEN** `_handle_event()` raises `ConsumerShutdown` (defined in `agentpool_server.mixins`)
- **THEN** the mixin SHALL gracefully exit the consumer loop
- **AND** it SHALL call `_after_consumer_loop(session_id)` before exiting
- **AND** `ConsumerShutdown` SHALL inherit from `Exception` (not `BaseException`)
- **NOTE**: `ConsumerShutdown` is ONLY caught when raised from `_handle_event()`. If raised from `_on_spawn_session_start()` or other hooks, it SHALL be treated as a regular exception and propagate out.

```python
class ConsumerShutdown(Exception):
    """Signal raised by _handle_event() to request graceful consumer loop shutdown."""
```

#### Scenario: None sentinel stops consumer
- **WHEN** `None` is received from the queue
- **THEN** the consumer loop SHALL exit gracefully
- **AND** it SHALL call `_after_consumer_loop(session_id)` before exiting

### Requirement: Subscription scope is configurable per handler
The mixin SHALL support configurable EventBus subscription scope.

#### Scenario: Default scope is descendants
- **WHEN** a handler does not override `_get_subscription_scope()`
- **THEN** the mixin SHALL use `scope="descendants"`

#### Scenario: Handler overrides scope
- **WHEN** a handler overrides `_get_subscription_scope()` to return `"session"`
- **THEN** the mixin SHALL use `scope="session"`
- **AND** only the exact session's events are received (no child events)

### Requirement: ACP handler implements mixin hooks for subagent events
The `ACPProtocolHandler` SHALL inherit from `ProtocolEventConsumerMixin` and implement the required hooks.

#### Scenario: ACP initializes per-session converter in _before_consumer_loop
- **WHEN** `_before_consumer_loop(session_id)` is called
- **THEN** it SHALL create an `ACPEventConverter` instance for this session
- **AND** it SHALL store the converter in `self._converters[session_id]`
- **AND** the converter SHALL be derived from `self._event_converter_template`

#### Scenario: ACP converts events to session/update
- **WHEN** `_handle_event()` is called with an event
- **THEN** it SHALL retrieve the converter from `self._converters[session_id]`
- **AND** it SHALL convert the event via the converter
- **AND** it SHALL emit `session/update` notifications to the ACP client
- **AND** on `ConnectionResetError` / `BrokenPipeError` / `anyio.ClosedResourceError`, it SHALL catch the error and raise `ConsumerShutdown`

#### Scenario: ACP handles SpawnSessionStart as no-op
- **WHEN** `_on_spawn_session_start()` is called
- **THEN** the default no-op implementation SHALL be used
- **AND** ACP SHALL NOT create child consumers
- **AND** child session events SHALL flow through the parent consumer's `scope="descendants"` subscription

#### Scenario: ACP converter handles SpawnSessionStart
- **WHEN** `ACPEventConverter.convert()` receives a `SpawnSessionStart` event
- **THEN** it SHALL return the appropriate ACP update (not `...` placeholder)
- **AND** the converter SHALL update its internal state to track the child session

#### Scenario: ACP cleans up converter in _after_consumer_loop
- **WHEN** `_after_consumer_loop(session_id)` is called
- **THEN** it SHALL remove the converter from `self._converters` (if present)

### Requirement: Mixin shutdown interaction with SessionPool.close_session
The mixin's `stop_event_consumer()` and `SessionPool.close_session()` SHALL have well-defined interaction.

#### Scenario: Caller coordinates shutdown sequence
- **WHEN** ACP `close_session()` is called
- **THEN** it SHALL first call `self.stop_event_consumer(session_id)` (cancels task, unsubscribes)
- **AND** it SHALL then call `await session_pool.close_session(session_id)` (SessionPool cleanup)
- **AND** `SessionPool.close_session()` MAY send EventBus sentinel as part of its cleanup
- **AND** the mixin SHALL handle gracefully if the sentinel arrives after unsubscribing (no-op)

### Requirement: Mixin interface is compatible with future OpenCode adoption
The `ProtocolEventConsumerMixin` interface SHALL be designed so that `OpenCodeSessionPoolIntegration` can adopt it in a future change without interface changes.

#### Scenario: OpenCode can use _before_consumer_loop for setup
- **WHEN** `OpenCodeSessionPoolIntegration` adopts the mixin
- **THEN** it SHALL implement `_before_consumer_loop()` to create `EventProcessorContext`, `OpenCodeEventAdapter`, and `assistant_msg`

#### Scenario: OpenCode can use _on_spawn_session_start for ToolPart registration
- **WHEN** `OpenCodeSessionPoolIntegration` adopts the mixin
- **THEN** it SHALL implement `_on_spawn_session_start()` to create subagent `ToolPart`, register in `EventProcessorContext`, and start child consumer

#### Scenario: OpenCode can track child tasks independently
- **WHEN** `OpenCodeSessionPoolIntegration` creates child consumers in `_on_spawn_session_start()`
- **THEN** it SHALL track them in its own `child_tasks: dict[str, asyncio.Task]`
- **AND** the mixin SHALL NOT interfere with this tracking
- **AND** on parent consumer stop, OpenCode SHALL cancel its child tasks in `_after_consumer_loop()`

### Requirement: No leaked EventBus subscriptions
All protocol handlers using the mixin SHALL unsubscribe from the EventBus when their consumer stops.

#### Scenario: Normal stop unsubscribes
- **WHEN** `stop_event_consumer(session_id)` is called
- **THEN** it SHALL unsubscribe from the EventBus
- **AND** the session SHALL NOT appear in EventBus subscribers

#### Scenario: Exception during loop unsubscribes
- **WHEN** the consumer loop crashes with an unhandled exception
- **THEN** the `finally` block SHALL unsubscribe from the EventBus
- **AND** no subscription SHALL leak

#### Scenario: CancelledError during loop unsubscribes
- **WHEN** the consumer task is cancelled
- **THEN** the `finally` block SHALL unsubscribe from the EventBus
- **AND** `asyncio.CancelledError` SHALL be re-raised after cleanup
