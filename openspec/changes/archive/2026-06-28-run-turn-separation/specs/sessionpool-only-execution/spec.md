## MODIFIED Requirements

### Requirement: SessionController is a pure session registry

`SessionController` SHALL be simplified to a pure session registry. It SHALL own session CRUD, agent provisioning, hierarchy management, storage persistence, TTL cleanup, and cross-session resource tracking. It SHALL NOT own run creation, run cleanup, or run cancellation logic — those are absorbed by `RunHandle`.

- `SessionController._create_run()` SHALL be removed — `RunHandle` constructs itself
- `SessionController._cleanup_run()` SHALL be removed — `RunHandle._cleanup_run()` handles cleanup
- `SessionController.cancel_run_for_session()` SHALL be removed — callers use `RunHandle.cancel()` directly
- `SessionController.receive_request()` SHALL be simplified to ~15 lines: session validation + delegate to `RunHandle.start()` (idle) or `RunHandle.steer()`/`.followup()` (busy)
- `SessionController.close_session()` SHALL call `RunHandle.close()` before cancelling scope, with 30s timeout fallback to `RunHandle.cancel()`
- All other methods (session registry CRUD, agent factory, hierarchy, storage, cleanup loop, MCP tracking, pending questions) SHALL remain unchanged
- `SessionController._runs` dict SHALL be retained as the active run registry (RunHandle self-registers on creation)

#### Scenario: SessionController creates session and delegates to RunHandle
- **WHEN** `receive_request(session_id, content)` is called on an idle session
- **THEN** SessionController validates session exists, not closing, concurrency limit not exceeded
- **AND** constructs `RunHandle(agent, run_ctx, event_bus, session)`
- **AND** registers in `self._runs[run.run_id] = run`
- **AND** sets `session.current_run_id = run.run_id`
- **AND** launches `run.start(content)` as background task
- **AND** does NOT call `_create_run()` (removed)

#### Scenario: SessionController closes session with idle RunHandle
- **WHEN** `close_session(session_id)` is called on a session with idle RunHandle
- **THEN** it calls `run_handle.close()` (sets `_closing=True`, wakes idle)
- **AND** cancels session's CancelScope (cascades to RunHandle)
- **AND` awaits `complete_event` with 30s timeout
- **AND** on timeout, calls `run_handle.cancel()` + `cancel_run()`
- **AND** proceeds with session cleanup (agent `__aexit__`, MCP decrement, store marking)

#### Scenario: SessionController delegates busy-path to RunHandle
- **WHEN** `receive_request(session_id, content, priority="steer")` is called on a session with active run
- **THEN** SessionController retrieves the active `RunHandle` from `self._runs`
- **AND** calls `await run_handle.steer(content)`
- **AND` does NOT call `TurnRunner.steer()` (deprecated/deleted)
