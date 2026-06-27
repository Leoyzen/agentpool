## Why

Background tasks (subagents, code review, web search) spawned during an agent turn often complete after the agent has finished its response. The current `steer()` mechanism has two windows:

1. **During iteration** (`agent_run` active): `steer()` → `agent_run.enqueue(asap)` → PydanticAI picks up the message mid-turn. This already works correctly.
2. **After iteration, before `StreamCompleteEvent`** (`agent_run` cleared in `finally`, RunExecutor still in `execute()`): `steer()` sees `agent_run is None` → falls through to `_post_turn_injections` or `receive_request()`. The result is processed in a **new turn** with its own `StreamCompleteEvent` → ACP client sees `end_turn` and stops listening. The background task's result is lost.

The root cause is that `RunExecutor.execute()` publishes `StreamCompleteEvent` immediately after iteration completes, without waiting for background tasks. The turn declares completion before all work spawned during it is done.

## What Changes

- **`pending_background_tasks: int` counter** on `AgentRunContext` — tools increment before spawning, decrement in `finally` on completion
- **`background_tasks_complete: asyncio.Event`** on `AgentRunContext` — set when counter reaches 0, cleared when counter > 0 (initially set via custom factory)
- **`queued_steer_messages: list[str]`** on `AgentRunContext` — collects steer messages that arrive after iteration exits but before `StreamCompleteEvent`
- **`steer_callback: Callable | None`** on `AgentRunContext` — set by `TurnRunner`, allows tools to call `steer()` without direct `TurnRunner` reference
- **Re-iteration loop in `RunExecutor.execute()`** — after iteration completes, if counter > 0, wait for `background_tasks_complete`. When event fires, check `queued_steer_messages`. If non-empty, start a new iteration with those messages as prompts. Repeat until counter=0 and queue empty. Then publish `StreamCompleteEvent`.
- **`steer()` routing unchanged for active `agent_run`** — mid-turn injection via `enqueue(asap)` already works
- **`steer()` routing for post-iteration window** — when `agent_run is None` but `run_ctx` is still alive (RunExecutor waiting), write to `run_ctx.queued_steer_messages` instead of `_post_turn_injections`
- **`close_session()` unblock** — set `cancelled=True` + `background_tasks_complete.set()` BEFORE the existing 30s wait, to immediately unblock the wait loop
- **No timeout** — `await event.wait()` blocks indefinitely. Session close is the sole exit
- **Zero changes to**: `run_loop`, `_process_queued_work`, EventBus, protocol converters

## Capabilities

### New Capabilities
- `background-task-lifecycle`: Counter-based background task tracking with re-iteration support in RunExecutor

### Modified Capabilities
- *(none)*

## Impact

- **`src/agentpool/agents/context.py`**: +4 fields (`pending_background_tasks`, `background_tasks_complete`, `queued_steer_messages`, `steer_callback`) + `_create_set_event()` factory
- **`src/agentpool/orchestrator/run_executor.py`**: ~35 lines added (wait loop + re-iteration + `iteration_messages` capture inside `async with` block)
- **`src/agentpool/orchestrator/core.py`**: ~10 lines changed — `steer()` routing (~5 lines for `queued_steer_messages` path) + `close_session()` unblock (~5 lines before the 30s wait) + `steer_callback` wiring in `_run_turn_unlocked`
- **Tool implementations**: +1 increment/decrement per background task spawn (opt-in, minimal)
- **No changes to**: `run_loop`, `_process_queued_work`, `_safe_auto_resume`, EventBus, ACP/OpenCode/AG-UI converters
