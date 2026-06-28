## Why

When a user cancels an in-progress agent run (e.g. via ACP `session/cancel`), the entire `RunHandle` is killed — including the long-running `start()` loop that is designed to persist for the session's lifetime. This leaves `session.current_run_id` pointing at a dead run handle, causing subsequent prompts to silently return `None` from `receive_request()` with no events published, which hangs the client indefinitely.

The root cause is that `cancel()` calls `current_task.cancel()`, which raises `CancelledError` into the `start()` generator. `NativeTurn.execute()` re-raises `CancelledError` (line 204-206), which propagates through `start()` and kills the generator. The `_iteration_task` field — intended to hold a separate task for the LLM API call so it can be cancelled independently — is declared but never assigned, so there is no way to interrupt only the current turn without killing the entire run loop.

## What Changes

- **Wire up `_iteration_task`**: Run the pydantic-ai `agent_run.next(node)` call inside a dedicated asyncio task (`_iteration_task`) so it can be cancelled independently of the `start()` loop.
- **`_interrupt()` only cancels `_iteration_task`**: Stop cancelling `run_ctx.current_task` (the `start()` task). Only cancel the LLM iteration task. This lets the `start()` loop survive cancellation and return to idle.
- **`NativeTurn.execute()` handles cancellation gracefully**: When `CancelledError` is caught from the iteration task, check `run_ctx.cancelled` — if true, break out of the node loop and **return without yielding `StreamCompleteEvent`**. This prevents double `turn_complete` emission: the event converter emits `TurnCompleteUpdate(stop_reason="end_turn")` on `StreamCompleteEvent` and `TurnCompleteUpdate(stop_reason="cancelled")` on `RunFailedEvent` — yielding both would send conflicting stop reasons to the client. Instead, `start()` publishes `RunFailedEvent` which produces a single `TurnCompleteUpdate(stop_reason="cancelled")`.
- **`ACP agent` cancellation**: The ACP agent's stream loop already checks `run_ctx.cancelled` (line 536) and breaks gracefully. No changes needed for ACP turn execution.
- **`RunHandle.cancel()` no longer cancels `current_task`**: Remove the `current_task.cancel()` call. `cancel()` sets `run_ctx.cancelled = True`, wakes `_idle_event`, and calls `agent._interrupt()` — which now only cancels `_iteration_task`.
- **Remove `fail()` from ACP `cancel_session()`**: The `fail()` call was needed to unblock `handle_prompt()`'s `complete_event.wait()` for legacy clients. Replace with a per-turn completion event so legacy clients unblock when the *turn* finishes (cancelled), not when the *run* dies.
- **Add `_turn_complete_event` to `RunHandle`**: A new `asyncio.Event` that is set at the end of each turn (whether completed, cancelled, or errored). `handle_prompt()` waits on this instead of `complete_event` for legacy clients. Reset at the start of each new turn in `start()`.
- **Clear `current_run_id` in `_cleanup_run()`**: As a safety net, clear `session.current_run_id` when a run is cleaned up. This handles edge cases where the run does die (e.g. unrecoverable error) and a new run needs to be created.
- **Stale-run detection in `receive_request()`**: If `current_run_id` is set but the run handle is missing or in a terminal status, clear it and start a new run. Defense-in-depth against any future stale-reference bugs.

## Capabilities

### New Capabilities

_None_

### Modified Capabilities

- `session-orchestration`: Cancel semantics change from "kill entire RunHandle" to "interrupt current turn, keep RunHandle alive and return to idle". `RunHandle.cancel()` no longer cancels the `start()` task. `receive_request()` gains stale-run detection. `_cleanup_run()` clears `current_run_id`.
- `acp-server`: `cancel_session()` no longer calls `run_handle.fail()`. Legacy client blocking in `handle_prompt()` uses `_turn_complete_event` instead of `complete_event`.

## Impact

- **`src/agentpool/orchestrator/run.py`**: `cancel()` — remove `current_task.cancel()`. Add `_turn_complete_event` field. `start()` — set/reset `_turn_complete_event` per turn. `_cleanup_run()` — clear `session.current_run_id`.
- **`src/agentpool/agents/native_agent/agent.py`**: `_interrupt()` — only cancel `_iteration_task`, not `current_task`. Wire up `_iteration_task` assignment.
- **`src/agentpool/agents/native_agent/turn.py`**: `execute()` — catch `CancelledError` from iteration task, check `run_ctx.cancelled`, break gracefully instead of re-raising.
- **`src/agentpool/orchestrator/core.py`**: `receive_request()` — add stale-run detection. `cancel_run_for_session()` — no longer kills the run.
- **`src/agentpool_server/acp_server/handler.py`**: `cancel_session()` — remove `fail()` call. `handle_prompt()` — wait on `_turn_complete_event` instead of `complete_event`.
- **`tests/orchestrator/test_receive_request_acp.py`**: Add test for cancel-then-prompt scenario.
- **`tests/orchestrator/test_run_handle.py`**: Add tests for per-turn completion event, cancel-returns-to-idle, and stale-run detection.
