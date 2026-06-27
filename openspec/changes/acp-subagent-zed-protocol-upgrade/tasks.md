## 1. Framework-Level Auto-Emit (context.py)

- [ ] 1.1 Add `spawn_mechanism`, `description`, `tool_call_id` keyword params to `create_child_session()`
- [ ] 1.2 Add `MAX_SUBAGENT_DEPTH = 5` constant and `SubagentDepthError` exception
- [ ] 1.3 Implement depth check: `child_depth = self.run_ctx.depth + 1`; raise `SubagentDepthError` if exceeds limit
- [ ] 1.4 Auto-construct `SpawnSessionStart` with `tool_call_id=self.tool_call_id`, `depth=child_depth`, `spawn_mechanism`, `description`
- [ ] 1.5 Emit via `await self.events.emit_event(spawn_event)` (NOT `self.node._events`)
- [ ] 1.6 Verify no `getattr` usage — use `self.tool_call_id` and `self.run_ctx.depth` directly

## 2. Simplify Call Sites (remove manual SpawnSessionStart boilerplate)

- [ ] 2.1 Simplify `subagent_tools.py:247-259` — remove 15-line manual `SpawnSessionStart` + `emit_event`, replace with 1-line `create_child_session(description=...)` call
- [ ] 2.2 Simplify `workers.py:165-176` — same simplification
- [ ] 2.3 Simplify `workers.py:283` — same simplification
- [ ] 2.4 Verify team.py and teamrun.py are NOT modified (they use `yield` pattern)

## 3. Event Converter Fixes (event_converter.py)

- [ ] 3.1 Fix `kind="other"` → `kind="subagent"` at line 656
- [ ] 3.2 Fix `tool_call_id = str(uuid.uuid4())` → `tool_call_id = event.tool_call_id or str(uuid.uuid4())` at line 649
- [ ] 3.3 Add `field_meta` (with `subagent_session_info` + `tool_name`) to `ToolCallProgress` for subagent tool calls
- [ ] 3.4 Add `build_subagent_completed()` method to `ACPEventConverter`
- [ ] 3.5 Populate `SubagentRunInfo(child_session_id=..., run_mode="foreground", display_name=...)` on `ToolCallStart` (P2)

## 4. Event + Closure Completion Notification (handler.py)

- [ ] 4.1 Add `_parent_of: dict[str, str]` to `ACPProtocolHandler.__init__`
- [ ] 4.2 In `_on_spawn_session_start`: after `start_event_consumer(child_sid)`, grab `done_event = self._consumer_done_events.get(child_sid)`
- [ ] 4.3 Define `_notify_completed()` async helper that calls `parent_converter.build_subagent_completed()` + `client.session_update()`
- [ ] 4.4 Handle `done_event is None` race: call `_notify_completed()` immediately + `_parent_of.pop()` in finally
- [ ] 4.5 Define `_await_child_and_notify()` closure: `await done_event.wait()` → `_parent_of.pop()` → `_notify_completed()`
- [ ] 4.6 Add try/except in closure: catch `ConnectionResetError`/`BrokenPipeError` (debug log), catch `Exception` (exception log)
- [ ] 4.7 Add `finally: contextlib.suppress(ValueError): self._consumer_task_refs.remove(task)` in closure
- [ ] 4.8 Register `self._parent_of[child_sid] = parent_sid` before starting closure task
- [ ] 4.9 Spawn closure via `asyncio.ensure_future()` + append to `_consumer_task_refs`

## 5. Recursive Cancellation (handler.py)

- [ ] 5.1 Implement `_cancel_subagents(parent_sid)` — walk `_parent_of` tree, recursively `stop_event_consumer` each child
- [ ] 5.2 Pop `_parent_of` entries during cancellation
- [ ] 5.3 Wire `_cancel_subagents` into `stop_event_consumer` or session close flow

## 7. Background Task Completion — child_done_events (context.py, run_executor.py, core.py)

- [ ] 7.1 Replace `pending_background_tasks: int` and `background_tasks_complete: asyncio.Event` with `child_done_events: dict[str, anyio.Event]` on `AgentRunContext` (use `anyio.Event`, not `asyncio.Event`, to align with `_consumer_done_events` on `ProtocolEventConsumerMixin`)
- [ ] 7.2 Remove `_create_set_event()` module-level function (no longer needed)
- [ ] 7.3 In `create_child_session()`: create `anyio.Event()` and register on `run_ctx.child_done_events[child_sid]` (after session creation, before return). Handle `run_ctx is None` case (skip registration, session still created)
- [ ] 7.4 Add `async def complete_background_task(self, child_session_id: str, message: str)` on `AgentRunContext` — calls `steer_callback` first (skip if None, log warning), then pops event via `.pop(child_session_id, None)` and sets it (if not None), catching and logging any `steer_callback` exception to prevent RunExecutor hang
- [ ] 7.5 Update `RunExecutor.execute()` re-iteration loop: check `bool(run_ctx.child_done_events)` instead of `pending_background_tasks > 0`; snapshot values (`list(run_ctx.child_done_events.values())`) before awaiting to prevent dict mutation during iteration; wait on all snapshotted events; update reset logic to `run_ctx.child_done_events.clear()` instead of `pending_background_tasks = 0` + `background_tasks_complete.set()`
- [ ] 7.6 Update `SessionPool.close_session()` in `core.py`: snapshot `list(run_handle.run_ctx.child_done_events.values())`, set all events, clear dict (replaces `background_tasks_complete.set()`). Verify `SessionController.close_session()` needs no changes (it doesn't access `background_tasks_complete`)
- [ ] 7.7 In `_run_turn_unlocked()` finally block: if `_session.parent_session_id` is set, look up parent session via `sessions.get_session(parent_id)`, then parent's `RunHandle` via `sessions._runs.get(parent_session.current_run_id)`, then `run_handle.run_ctx.child_done_events.pop(child_sid, None)` — if event is not None, set it. Framework safety net for tools that don't call `complete_background_task`. Any None in the lookup chain → no-op (no exception)
- [ ] 7.8 Update existing tests: `test_background_task_wakeup.py`, `test_session_lifecycle.py`, `test_steer_followup.py` — replace `pending_background_tasks` assertions with `child_done_events` assertions

## 8. Background Task Completion — Tests

- [ ] 8.1 Test: `create_child_session` registers `done_event` on parent `run_ctx.child_done_events`
- [ ] 8.2 Test: `complete_background_task()` calls `steer_callback` before setting `done_event` (ordering)
- [ ] 8.3 Test: `complete_background_task()` with unknown child_session_id still calls `steer_callback` (graceful `.pop(key, None)`)
- [ ] 8.4 Test: `complete_background_task()` when `steer_callback` is None — skips steer, still sets event, logs warning
- [ ] 8.5 Test: `complete_background_task()` when `steer_callback` raises — catches exception, logs error, still sets event
- [ ] 8.6 Test: `complete_background_task()` called twice for same child — second call finds key missing (`.pop` returns None), still calls `steer_callback`
- [ ] 8.7 Test: RunExecutor waits on `child_done_events` when non-empty after first iteration (snapshots values before waiting)
- [ ] 8.8 Test: RunExecutor skips wait when `child_done_events` is empty
- [ ] 8.9 Test: RunExecutor reset logic uses `child_done_events.clear()` (not `pending_background_tasks = 0`)
- [ ] 8.10 Test: `close_session()` snapshots values, sets all remaining `child_done_events`, clears dict (no dict mutation race)
- [ ] 8.11 Test: `_run_turn_unlocked` finally sets parent `done_event` for child sessions via `.pop(key, None)`
- [ ] 8.12 Test: `_run_turn_unlocked` finally is no-op when `complete_background_task` already popped the key
- [ ] 8.13 Test: `_run_turn_unlocked` finally is no-op when parent run already completed (`current_run_id` is None)
- [ ] 8.14 Test: `_run_turn_unlocked` finally is no-op when parent session not found, RunHandle not found, or run_ctx is None
- [ ] 8.15 Test: `_run_turn_unlocked` finally is no-op when `parent_session_id` is None (top-level session)
- [ ] 8.16 Test: Synchronous child session — `done_event` set before RunExecutor reaches re-iteration (no harm)
- [ ] 8.17 Test: Safety net fires without steer when tool didn't call `complete_background_task()`
- [ ] 8.18 Test: Multiple concurrent children — all must complete before RunExecutor wakes

## 9. Tests

- [ ] 9.1 Test: `create_child_session` auto-emits `SpawnSessionStart` with correct `tool_call_id`
- [ ] 9.2 Test: `tool_call_id` flows ctx → event → converter consistently
- [ ] 9.3 Test: `kind="subagent"` in zed mode `ToolCallStart`
- [ ] 9.4 Test: `ToolCallProgress` carries `_meta.subagent_session_info` + `tool_name`
- [ ] 9.5 Test: Event + closure completion notification (mock `done_event`)
- [ ] 9.6 Test: `done_event is None` race — immediate notification fired
- [ ] 9.7 Test: Concurrent child sessions — each gets correct `tool_call_id` completion
- [ ] 9.8 Test: Closure error handling — `session_update` raises, exception logged not swallowed
- [ ] 9.9 Test: `_consumer_task_refs` cleanup after task completion
- [ ] 9.10 Test: `_parent_of` cleanup on normal child exit
- [ ] 9.11 Test: `MAX_SUBAGENT_DEPTH` enforcement — `SubagentDepthError` raised at depth 6
- [ ] 9.12 Test: Recursive cancellation — parent stop cascades to children and grandchildren
- [ ] 9.13 Test: Legacy mode unchanged — `subagent_display_mode != "zed"` behavior identical to before
- [ ] 9.14 Test: team.py yield pattern unaffected by auto-emit changes
