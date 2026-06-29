## MODIFIED Requirements

### Requirement: All protocol sessions SHALL be managed by SessionPool
The system SHALL ensure that session creation, teardown, and lifecycle management for all protocols are handled exclusively through SessionPool. Protocol handlers SHALL NOT create or close sessions through legacy direct agent methods when SessionPool is available. OpenCode server SHALL NOT use `getattr(state, "session_status", None)` as a fallback for session status when `SessionStatusBridge` is available.

#### Scenario: OpenCode session close through SessionPool
- **WHEN** an OpenCode client closes a session
- **THEN** the OpenCode protocol handler invokes `SessionPool.close_session()`
- **AND** the handler does NOT fall back to direct `state.sessions.pop(session_id)` or other in-memory cleanup

#### Scenario: OpenCode session operations through SessionPool
- **WHEN** an OpenCode session is created, initialized, or closed
- **THEN** the OpenCode protocol handler uses SessionPool APIs for lifecycle management
- **AND** the handler does NOT use direct agent session methods bypassing SessionPool

#### Scenario: OpenCode message storage uses SessionPool as authoritative source
- **WHEN** the OpenCode server retrieves messages for a session
- **THEN** it uses SessionPool's message API (`get_messages`) as the authoritative source
- **AND** `state.messages` is retained as a streaming buffer for subagent ToolPart updates and checkpoint restoration, but is NOT used as an authoritative fallback for message retrieval
- **AND** the subagent streaming fast-path (in-memory `state.messages` for live ToolPart updates during streaming) continues to function

#### Scenario: OpenCode session status uses SessionPool exclusively
- **WHEN** the OpenCode server reads or updates session status (busy/idle)
- **THEN** it uses `OpenCodeSessionPoolIntegration.get_session_status()` and `SessionStatusBridge` exclusively
- **AND** the `getattr(state, "session_status", None)` fallback pattern is removed
- **AND** no dynamic attribute injection of `session_status` on `ServerState` is used

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
