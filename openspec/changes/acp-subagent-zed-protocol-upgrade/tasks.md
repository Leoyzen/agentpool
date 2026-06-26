## 1. Framework-Level Auto-Emit (context.py)

- [ ] 1.1 Add `spawn_mechanism`, `description`, `tool_call_id` keyword params to `create_child_session()`
- [ ] 1.2 Add `MAX_SUBAGENT_DEPTH = 1` constant and `SubagentDepthError` exception
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

## 6. Tests

- [ ] 6.1 Test: `create_child_session` auto-emits `SpawnSessionStart` with correct `tool_call_id`
- [ ] 6.2 Test: `tool_call_id` flows ctx → event → converter consistently
- [ ] 6.3 Test: `kind="subagent"` in zed mode `ToolCallStart`
- [ ] 6.4 Test: `ToolCallProgress` carries `_meta.subagent_session_info` + `tool_name`
- [ ] 6.5 Test: Event + closure completion notification (mock `done_event`)
- [ ] 6.6 Test: `done_event is None` race — immediate notification fired
- [ ] 6.7 Test: Concurrent child sessions — each gets correct `tool_call_id` completion
- [ ] 6.8 Test: Closure error handling — `session_update` raises, exception logged not swallowed
- [ ] 6.9 Test: `_consumer_task_refs` cleanup after task completion
- [ ] 6.10 Test: `_parent_of` cleanup on normal child exit
- [ ] 6.11 Test: `MAX_SUBAGENT_DEPTH` enforcement — `SubagentDepthError` raised at depth 2
- [ ] 6.12 Test: Recursive cancellation — parent stop cascades to children and grandchildren
- [ ] 6.13 Test: Legacy mode unchanged — `subagent_display_mode != "zed"` behavior identical to before
- [ ] 6.14 Test: team.py yield pattern unaffected by auto-emit changes
