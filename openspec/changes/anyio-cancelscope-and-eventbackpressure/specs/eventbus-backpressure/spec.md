# Spec: EventBus Backpressure

## ADDED Requirements

### Requirement: Producer-side backpressure signaling

The EventBus SHALL provide bounded memory channels with configurable capacity limits to prevent queue overflow and implement producer-side backpressure.

#### Scenario: Consumer slower than producer

- **WHEN** event producer publishes faster than consumer can process
- **THEN** producer SHALL block when channel reaches configured maximum capacity
- **THEN** producer SHALL automatically resume when consumer frees space in channel
- **THEN** no events SHALL be dropped due to overflow

#### Scenario: Channel full with timeout option

- **WHEN** producer attempts to publish to a full channel with send_timeout configured
- **THEN** producer SHALL raise TimeoutError after timeout period if no capacity becomes available
- **THEN** other producers SHALL continue to publish successfully when capacity allows
- **THEN** system SHALL not deadlock waiting for unavailable capacity

#### Scenario: Multiple producers contending for limited capacity

- **WHEN** multiple parallel producers attempt to publish to a bounded channel simultaneously
- **THEN** producers SHALL acquire semaphore slots in FIFO order
- **THEN** each producer SHALL wait its turn when channel is full
- **THEN** no producer SHALL starve due to unfair semaphore acquisition

### Requirement: Parallel publishing safety

The EventBus SHALL support concurrent publishing from multiple event sources while maintaining FIFO ordering.

#### Scenario: Parallel event emission from different sources

- **WHEN** two or more event sources emit to the same EventBus channel concurrently
- **THEN** all events SHALL be published in correct order (no reordering)
- **THEN** no race conditions SHALL occur in stream operations
- **THEN** system SHALL handle burst publishing without event loss

#### Scenario: High-frequency event stream

- **WHEN** agent emits many rapid events (e.g., tool progress updates)
- **THEN** all events SHALL be queued successfully until channel capacity is reached
- **THEN** producer SHALL block gracefully at capacity boundary
- **THEN** consumer SHALL receive all events in emission order despite producer backpressure

### Requirement: Configurable queue sizing

The EventBus SHALL allow configuration of channel sizes per event type to tune memory usage and backpressure characteristics.

#### Scenario: High-frequency events (progress updates)

- **WHEN** EventBus is initialized with custom queue size for high-frequency events
- **THEN** system SHALL use larger queue to accommodate burst publishing
- **THEN** memory usage SHALL scale with configured queue size
- **THEN** producers shall rarely encounter backpressure under normal operation

#### Scenario: Low-frequency events (lifecycle events)

- **WHEN** EventBus is initialized with smaller queue size for rare events
- **THEN** system SHALL use minimal memory for these channels
- **THEN** overflow protection SHALL still apply with tighter limits
- **THEN** producers SHALL block on full queues regardless of frequency category

### Requirement: Graceful degradation under overload

The EventBus SHALL provide predictable behavior when system is overloaded rather than unbounded growth or silent failures.

#### Scenario: Sustained overload condition

- **WHEN** consumers are consistently slower than producers across extended period
- **THEN** producers SHALL be throttled naturally via blocking on full queues
- **THEN** system SHALL not allocate unbounded memory
- **THEN** consumers SHALL continue processing at their maximum rate
- **THEN** no OOM errors SHALL occur due to event queue growth

#### Scenario: Consumer recovery after backlog

- **WHEN** blocked producer resumes after consumer backlog clears
- **THEN** producer SHALL immediately start enqueuing new events
- **THEN** backlog SHALL clear without data corruption
- **THEN** system SHALL return to normal operation once equilibrium is restored