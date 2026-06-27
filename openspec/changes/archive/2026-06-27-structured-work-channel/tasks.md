## 1. Core: counter + event + queued_steer_messages + steer_callback on AgentRunContext

- [x] 1.1 Add `pending_background_tasks: int = 0` field to `AgentRunContext` in `src/agentpool/agents/context.py`
- [x] 1.2 Add `background_tasks_complete: asyncio.Event` field with a custom factory `_create_set_event()` that creates an `asyncio.Event()` and calls `.set()` on it (so it's initially set — 0 pending = complete). Do NOT use `default_factory=asyncio.Event` directly (that creates an unset event, contradicting the spec).
- [x] 1.3 Add `queued_steer_messages: list[str] = field(default_factory=list)` to `AgentRunContext`
- [x] 1.4 Add `steer_callback: Callable[[str, str], Awaitable[bool]] | None = None` to `AgentRunContext` — set by `TurnRunner` when creating the `RunHandle`, allows tools to call `steer()` without direct `TurnRunner` reference
- [x] 1.5 Verify fields are per-run isolated (new `AgentRunContext` per turn — no cross-turn leakage)

## 2. Re-iteration loop in RunExecutor.execute()

- [x] 2.1 Add `iteration_messages: list[Any] | None = None` as a nonlocal variable in `execute()`. Inside `agent_iteration_task`, capture `iteration_messages = agent_run.all_messages()` **INSIDE** the `async with agentlet.iter(...)` block (before `__aexit__` is called — do NOT capture after the block exits, as `all_messages()` may be unreliable after context manager cleanup). If an exception occurs inside the block, `iteration_messages` may not be captured — this is acceptable (error paths don't need re-iteration).
- [x] 2.2 In `src/agentpool/orchestrator/run_executor.py`, **AFTER** the `if response_msg is None` early return block (line ~379) and **BEFORE** the main `StreamCompleteEvent` publication (line ~381), add a `while True` loop that: (a) checks `run_ctx.cancelled` → break, (b) checks `run_ctx.pending_background_tasks > 0` → `await run_ctx.background_tasks_complete.wait()`, (c) checks `run_ctx.queued_steer_messages` → if empty, break, (d) if non-empty, copy+clear the list, reset counter to 0 + set event, update `history = iteration_messages` from prior iteration, re-enter `agent_iteration_task` with steer messages as prompts, update `response_msg`. **IMPORTANT**: Insert the loop AFTER the early return, not before it — the early return handles the case where the run was cancelled before producing a response (no need to wait for background tasks in that case).
- [x] 2.3 Refactor `agent_iteration_task` to accept an optional `steer_prompts: list[str] | None = None` parameter — when provided, use `steer_prompts` as the `prompts` argument to `agentlet.iter()` instead of the original `prompts`. When `None`, use the original `prompts` (first iteration).
- [x] 2.4 After the while loop, publish `StreamCompleteEvent` with the final `response_msg` (existing code at line ~381)
- [x] 2.5 Ensure `StreamCompleteEvent` is published exactly once per `execute()` call (no intermediate events from re-iterations)
- [x] 2.6 Note: `CancelledError` during re-iteration propagates to `execute()`'s existing `except asyncio.CancelledError` handler (line ~397), which publishes `StreamCompleteEvent(cancelled=True)` and re-raises. No additional error handling needed in the wait loop.

## 3. steer() routing for post-iteration window

- [x] 3.1 In `src/agentpool/orchestrator/core.py`, `TurnRunner.steer()`, native agent branch (line ~2316), when `agent_run is None` (line ~2330): before falling through to `_post_turn_injections`, check `run_handle.run_ctx is not None and not run_handle.run_ctx.completed`. If true, append message to `run_ctx.queued_steer_messages` and return False. If false (execute() already returned), fall through to existing `_post_turn_injections` logic.
- [x] 3.2 Verify the existing `agent_run.enqueue(priority="asap")` path (line ~2323) is unchanged — mid-turn injection during active iteration
- [x] 3.3 In `_run_turn_unlocked()`, when creating the `RunHandle` and `run_ctx`, set `run_ctx.steer_callback = lambda sid, msg: self.steer(sid, msg)` so tools can call `steer()` via `run_ctx.steer_callback(session_id, message)`

## 4. Session close unblocks wait

- [x] 4.1 **REMOVED** — Do NOT add `cancelled = True` to `_run_turn_unlocked()`'s finally block. The finally block runs AFTER `execute()` returns, so it cannot unblock the wait loop. Setting `cancelled = True` there would incorrectly mark every normal completion as cancelled.
- [x] 4.2 In `SessionController.close_session()` (core.py ~line 3142), **BEFORE** the existing `await asyncio.wait_for(run_handle.complete_event.wait(), timeout=30.0)` call (line ~3149), add: access `run_handle.run_ctx` and set `run_ctx.cancelled = True` and `run_ctx.background_tasks_complete.set()`. This immediately unblocks the `event.wait()` in `execute()`, causing the wait loop to break and publish `StreamCompleteEvent(cancelled=True)`. Then `execute()` returns, `complete_event` fires, and `close_session()` proceeds without waiting 30 seconds. Also fixes the `session.is_closing` vs `session.closing` race (steer() won't route to `queued_steer_messages` during shutdown because `cancelled` is checked first in the wait loop).

## 5. Tool integration pattern (documentation + opt-in)

- [x] 5.1 Document the increment/decrement + `steer_callback` pattern in a docstring near `pending_background_tasks` field. Show the full template: increment before `asyncio.create_task()`, `steer_callback` in `try`, decrement in `finally`, set event if counter reaches 0.
- [x] 5.2 **Opt-in only**: Existing background task tools (e.g., `subagent_tools.py` which writes results to files) are NOT required to adopt this pattern. If they do adopt it, only add `pending_background_tasks += 1` / `-= 1` — do NOT change the existing result delivery mechanism (file-based, not steer-based). This task is documentation only, not behavioral change.

## 6. Tests

- [x] 6.1 Rewrite `tests/orchestrator/test_background_task_wakeup.py` to test through `RunExecutor.execute()` directly. Setup steps: (a) Create a real native `Agent` with `TestModel`, (b) Register a tool that increments `pending_background_tasks`, spawns `asyncio.create_task(bg_task())`, and returns immediately, (c) The bg_task sleeps 200ms, calls `run_ctx.steer_callback(session_id, "bg result")`, decrements counter in `finally`, sets event if counter==0, (d) Configure `TestModel` to call the tool, then produce a response, (e) Create `EventBus`, subscribe, call `await executor.execute(..., event_bus=event_bus)`, (f) Drain events via `event_bus.close_session()`, (g) Assert: exactly 1 `StreamCompleteEvent` on EventBus, `execute()` returned a `ChatMessage`, and the response reflects re-iteration with the steer message. Also assert `execute()` took at least 200ms (proving it waited).
- [x] 6.2 Run `uv run pytest tests/orchestrator/test_run_executor.py -v --timeout=30` — no regressions
- [x] 6.3 Add test: background task completes during active iteration → steer enqueued via `agent_run.enqueue(asap)` → mid-turn injection works (agent processes steer in same iteration, no re-iteration needed). This tests existing behavior preserved by the change.
- [x] 6.4 Add test: background task completes after iteration → steer queued to `queued_steer_messages` → re-iteration with steer message → single `StreamCompleteEvent` with combined response. Verify message history is propagated (agent sees prior iteration's response in re-iteration context).
- [x] 6.5 Add test: session close during background task wait → `close_session()` sets `cancelled=True` + `background_tasks_complete.set()` → `StreamCompleteEvent(cancelled=True)` published. Verify `close_session()` returns quickly (not 30s).
- [x] 6.6 Add test: re-iteration has correct message history — agent in re-iteration can reference its prior response. Use `TestModel` with sequence of responses to verify the agent sees the history.

## 7. Verification

- [x] 7.1 `uv run ruff check src/agentpool/orchestrator/run_executor.py src/agentpool/agents/context.py src/agentpool/orchestrator/core.py` — 0 new violations
- [x] 7.2 `uv run ruff format --check src/agentpool/orchestrator/run_executor.py src/agentpool/agents/context.py src/agentpool/orchestrator/core.py` — passes
- [x] 7.3 Verify `agentlet.iter()` can be called multiple times on the same `agentlet` instance (PydanticAI `Agent.iter()` returns a new `AgentRun` each time). If this doesn't work, add `agentlet = await self._agent.get_agentlet(...)` before each re-iteration.
- [x] 7.4 `uv run pytest tests/orchestrator/ -x --timeout=30 --deselect tests/orchestrator/test_close_checkpoint.py` — 0 new failures
