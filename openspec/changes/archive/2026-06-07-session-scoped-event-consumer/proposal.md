## Why

The current OpenCode SessionPool integration creates temporary EventBus consumers and SessionStatusBridge instances per HTTP request in `message_routes.py`. However, `SessionPool.receive_request()` triggers `TurnRunner.run_loop()` which may execute **auto-resume** turns after the first turn completes. The `RunHandle.complete_event` is set when the first turn finishes, causing the request handler to tear down its temporary consumer before auto-resume events are published. This results in **lost events** for any post-turn work including subagent completion notifications, injected prompts, and queued messages.

This is a critical regression from the legacy direct-execution path where `agent.run_stream()` handled all turns within a single request context.

## What Changes

- **Session-scoped EventBus consumer**: Move EventBus subscription from per-request (`message_routes.py`) to per-session (`OpenCodeSessionPoolIntegration`). The consumer starts when a session is created and runs until the session is closed.
- **Session-scoped SessionStatusBridge**: Similarly move the status bridge to session-scoped lifecycle, ensuring status sync covers auto-resume periods.
- **Delay `RunHandle.complete_event`**: Change `TurnRunner` so `complete_event` represents the full `run_loop()` completion (including auto-resume), not just the first turn.
- **Simplify `message_routes.py`**: Remove temporary consumer/bridge creation from `_process_message_locked()`. The handler only waits for `complete_event` and returns the assistant message.
- **Update `OpenCodeSessionPoolIntegration`**: Add `_start_event_consumer()` and `_stop_event_consumer()` methods, track consumers in `_event_consumers` dict alongside `_status_bridges`.

## Capabilities

### New Capabilities
- `session-scoped-event-routing`: EventBus consumers and status bridges are tied to session lifecycle rather than HTTP request lifecycle, ensuring no events are dropped during auto-resume.

### Modified Capabilities
- `opencode-session-pool-routing`: Update to remove per-request consumer creation and rely on session-scoped consumers managed by `OpenCodeSessionPoolIntegration`.

## Impact

- **OpenCode server**: `message_routes.py` simplified; `OpenCodeSessionPoolIntegration` gains consumer management.
- **SessionPool core**: `TurnRunner.run_loop()` semantics change — `complete_event` now covers full run loop including auto-resume.
- **ACP handler**: May benefit from same `complete_event` semantics if it also waits for `run_loop` completion.
- **Tests**: Need new tests for auto-resume event delivery and session-scoped consumer lifecycle.
