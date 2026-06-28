## ADDED Requirements

### _cancel_fn wired to agent._interrupt()

The `_cancel_fn` field SHALL be assigned in `RunHandle.start()` to a callable that invokes `agent._interrupt(self.run_ctx)`, enabling subclass-specific cancellation (ACP `CancelNotification`, native `_iteration_task` cancel).

**Scenarios:**

1. **WHEN** `RunHandle.start()` begins, **THEN** `self._cancel_fn` SHALL be set to a callable that schedules `agent._interrupt(self.run_ctx)` as a fire-and-forget task, storing the reference in `self._interrupt_task` to prevent GC.

2. **WHEN** `cancel()` is called and `_cancel_fn` is set, **THEN** `agent._interrupt()` SHALL be called, sending `CancelNotification` to ACP remote servers or cancelling the native agent's `_iteration_task`.

### RunHandle.cancel() preserves cooperative cancellation

The `RunHandle.cancel()` method SHALL preserve all existing cooperative cancellation mechanisms.

**Scenarios:**

3. **WHEN** `cancel()` is called, **THEN** it SHALL set `self.run_ctx.cancelled = True` (for 26 cooperative cancellation checks across 7 files), set `self._idle_event.set()`, and call `self._cancel_fn()` if wired.

### _interrupt_tasks field removed

The `_interrupt_tasks: set[asyncio.Task[None]]` field SHALL be removed from the `RunHandle` dataclass. Cancellation is handled by `_cancel_fn` with a singular `_interrupt_task: asyncio.Task[None] | None` for GC safety.

**Scenarios:**

4. **WHEN** the `_interrupt_tasks` field is removed, **THEN** no external code SHALL reference it — all fire-and-forget interrupt logic SHALL be encapsulated in `_cancel_fn`. The singular `_interrupt_task` field SHALL store the task reference to prevent GC.
