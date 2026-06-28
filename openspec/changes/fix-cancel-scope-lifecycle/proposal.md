## Why

`BaseAgent.run_stream()` (Path B — standalone mode) has `yield` inside `async with anyio.create_task_group()`. AnyIO's `CancelScope` is task-affine: it must exit in the same task that entered it. When the async generator from a first `run_stream()` call is cleaned up (via `aclose()` or GC) in a different task context than the one that entered the task group, AnyIO raises `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`. This makes consecutive `run_stream()` calls on the same agent fail, breaking the core agent reuse pattern and causing ~15 test failures (including `test_concurrent_safety.py` and `test_e2e.py` which hang entirely).

The same anti-pattern exists in `ACPAgent._run_stream_once()` (acp_agent.py:527-561), which also has `yield` inside `anyio.create_task_group()`.

Additionally, `RunHandle.cancel()` relies on `_cancel_fn` which is never assigned anywhere in the codebase — cancellation always falls through to fire-and-forget `agent._interrupt()`. The `run_ctx.cancelled` flag (checked in 26 locations across 7 files) must be preserved.

## What Changes

- **Remove `anyio.create_task_group()` from `BaseAgent.run_stream()` Path B** — the task group that wraps the background runner + consumer loop is eliminated entirely
- **Path B delegates to `RunHandle.start()`** — creates a lightweight `RunHandle` with synthetic `SessionState`, iterates `start()` for events, yields outside any cancel scope. `RunHandle.start()` already has no task group — its `yield` is already safe.
- **Fix ACP agent** — remove `async with anyio.create_task_group()` from `ACPAgent._stream_events()`, restructure event forwarding without task group (either via `asyncio.create_task()` with manual cleanup, or by implementing the missing `ACPClientProtocol` adapter to route through `ACPTurn.execute()`)
- **Wire `_cancel_fn`** — assign in `RunHandle.start()` to call `agent._interrupt(self.run_ctx)`, enabling subclass-specific cancellation (ACP `CancelNotification`, native `_iteration_task` cancel)
- **Preserve `run_ctx.cancelled`** — `cancel()` continues to set the flag for all 26 cooperative cancellation checks
- **Remove `_interrupt_tasks` set** — subsumed by `_cancel_fn` wiring
- **Update tests** — remove `@pytest.mark.xfail` from `test_subsequent_run_after_interrupt`, verify no hangs

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `unified-session-lifecycle`: `_cancel_fn` wired to `agent._interrupt()`, `_interrupt_tasks` field removed
- `lean-core-framework`: `BaseAgent.run_stream()` Path B and `ACPAgent._run_stream_once()` no longer create task groups; they delegate to `RunHandle.start()` which has no cancel scope
- `unified-event-routing`: Event ordering preserved through direct delegation (RunStartedEvent first, StreamCompleteEvent last)

## Impact

- **`src/agentpool/agents/base_agent.py`**: `run_stream()` Path B (lines ~1082-1169) rewritten — remove `async with anyio.create_task_group()`, replace with RunHandle delegation
- **`src/agentpool/agents/acp_agent/acp_agent.py`**: `_stream_events()` (lines ~527-561) rewritten — remove `async with anyio.create_task_group()`
- **`src/agentpool/orchestrator/run.py`**: `RunHandle` — wire `_cancel_fn` in `start()`, remove `_interrupt_tasks` field, preserve `run_ctx.cancelled` in `cancel()`
- **`tests/agents/native_agent/test_interrupt.py`**: Remove xfail from `test_subsequent_run_after_interrupt`
- **`tests/agents/test_concurrent_safety.py`**: Should no longer hang
- **`tests/orchestrator/test_e2e.py`**: Should no longer hang
- **Breaking changes**: `_interrupt_tasks` field removed from `RunHandle` dataclass. Public API signatures unchanged. ACP agent event forwarding restructured (internal change, no public API impact).
