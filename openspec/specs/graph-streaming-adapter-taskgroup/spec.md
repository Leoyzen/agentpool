## ADDED Requirements

### Requirement: GraphStreamingAdapter uses TaskGroup for iteration task
The system SHALL replace `GraphStreamingAdapter._iteration_task: asyncio.Task | None` with a `TaskGroup`-based approach. The graph iteration coroutine SHALL be spawned via `tg.start_soon()`. Cleanup SHALL be guaranteed by `TaskGroup.__aexit__` instead of manual `task.cancel()` + `asyncio.shield()` + `asyncio.wait_for()`.

#### Scenario: Graph iteration spawned in TaskGroup
- **WHEN** `GraphStreamingAdapter.__aiter__()` starts
- **THEN** an `anyio.create_task_group()` is created
- **AND** `_graph_iteration_task()` is spawned via `tg.start_soon()`
- **AND** the task group is entered as a context manager

#### Scenario: TaskGroup guarantees iteration task cleanup
- **WHEN** graph iteration completes (normally or via cancellation)
- **THEN** `TaskGroup.__aexit__` awaits the iteration task's completion
- **AND** no manual `task.cancel()`, `asyncio.shield()`, or `asyncio.wait_for()` is needed
- **AND** the `_iteration_done` event is still set for downstream consumers

#### Scenario: Cancellation propagation through TaskGroup
- **WHEN** the consumer task is cancelled
- **THEN** `CancelledError` propagates through the `TaskGroup`
- **AND** the `_graph_iteration_task()` receives `CancelledError`
- **AND** the task's `finally` block runs before `TaskGroup.__aexit__` returns

### Requirement: Event queue polling replaced by direct get
The `asyncio.wait_for(event_queue.get(), timeout=0.1)` polling pattern in `GraphStreamingAdapter.__aiter__()` SHALL be replaced with `event_queue.get()` (no timeout). Cancellation detection SHALL use the `TaskGroup`'s cancellation mechanism instead of timeout-based polling with `current_task.cancelling() > 0`.

#### Scenario: No more timeout-based polling
- **WHEN** the consumer loop waits for events from the graph iteration task
- **THEN** it calls `await event_queue.get()` without a timeout
- **AND** cancellation is detected via `CancelledError` from the `TaskGroup`
- **AND** no 0.1s polling interval is needed

#### Scenario: Cancellation during queue get is handled
- **WHEN** the consumer is cancelled while waiting on `event_queue.get()`
- **THEN** `CancelledError` from the `TaskGroup` unwinds the consumer
- **AND** the iteration task's `finally` block runs to put the sentinel

### Requirement: GraphStreamingAdapter pattern aligns with RunExecutor
The `GraphStreamingAdapter` and `RunExecutor` SHALL use the same `TaskGroup` + event queue pattern. Both SHALL:
- Spawn a background iteration task via `tg.start_soon()`
- Use a shared `asyncio.Queue` (or `anyio.MemoryObjectSendStream`) for event streaming
- Rely on `TaskGroup.__aexit__` for lifecycle management
- Avoid `asyncio.shield()` and timeout-based polling

#### Scenario: Common pattern is extractable
- **WHEN** both `GraphStreamingAdapter` and `RunExecutor` are refactored
- **THEN** they share the same structural pattern:
  ```python
  async with anyio.create_task_group() as tg:
      tg.start_soon(background_iteration_task)
      # consumer loop
      while True:
          event = await event_queue.get()
          if event is None:
              break
          yield event
  ```
- **AND** both produce identical cleanup guarantees (no task leaks, no shielded fallback timeout)
