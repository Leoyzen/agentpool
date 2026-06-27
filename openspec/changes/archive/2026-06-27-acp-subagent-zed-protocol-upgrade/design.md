## Context

RFC-0027 Phase 1 implemented basic Zed subagent compatibility: `SubagentSessionInfo` model, `_build_subagent_field_meta()`, `zed` display mode, and `SpawnSessionStart → ToolCallStart` with `_meta`. However, Oracle evaluation (4 rounds, 2026-06-26 to 2026-06-27) found 10 gaps. The two most significant are:

1. **`tool_call_id` disconnect**: `event_converter.py:649` generates `uuid4()` ignoring `SpawnSessionStart.tool_call_id`. No downstream code can correlate `tool_call_id` to `child_session_id`. This blocks all completion notification approaches.

2. **No completion notification**: Child session's `ToolCallStart` stays in pending forever. Zed's subagent card shows infinite loading. The challenge is that each child session has its own `ACPEventConverter` instance — cross-converter coordination is needed.

The mixin (`ProtocolEventConsumerMixin`) already has `_consumer_done_events: dict[str, anyio.Event]` (line 60) that is set when a consumer loop exits (line 248-250, in `finally` block). This existing infrastructure can be leveraged for completion notification without new state management.

ACP v1.0.0 released 2026-06-24. Wire protocol stable at version 1. Zed SDK at `=1.0.0`. ACP Subagents RFD (PR #855) resumed 2026-06-25 — may eventually standardize subagent at protocol level, superseding `_meta` extension.

## Goals / Non-Goals

**Goals:**
- Zed correctly displays subagent completion (no infinite loading)
- `tool_call_id` flows from `ctx.tool_call_id` → `SpawnSessionStart` → converter → handler, no disconnect
- `SpawnSessionStart` auto-emitted by `create_child_session()`, eliminating 3 × 15-line boilerplate
- `MAX_SUBAGENT_DEPTH=5` enforced at framework level
- Recursive cancellation propagates to child sessions
- Error handling in closure (no silent exception swallowing)
- Memory cleanup (no `_consumer_task_refs` leak)

**Non-Goals:**
- Multi-turn reprompting (high risk, depends on session_manager resume capability)
- Foreground→background promotion (OpenCode innovation, not needed yet)
- ACP Proxy Chains (RFD still draft)
- Zed Parallel Agents (user-level parallelism, not subagent nesting)
- ACP v2 migration (still scaffolding)
- Modifying team.py/teamrun.py (use `yield` pattern, don't call `create_child_session()`)

## Decisions

### D1: Event + closure for completion notification (not dict, not EventBus event)

**Choice**: Use mixin's existing `_consumer_done_events: dict[str, anyio.Event]`. In `_on_spawn_session_start`, after `start_event_consumer(child_sid)`, grab `done_event` reference. Closure captures `parent_sid` and `tool_call_id`. Background task `await done_event.wait()` then emits `ToolCallProgress(completed)` via parent converter.

**Alternatives considered**:
- `_subagent_map: dict[str, tuple[str, str]]` on handler: Rejected — requires dict maintenance and cleanup, less elegant than closure capture
- New `SpawnSessionComplete` EventBus event: Rejected — overengineered, requires event type definition and EventBus scope changes
- Converter-internal tracking: Rejected — converter has no handler context, can't access parent converter

**Why**: Closure naturally captures context (no dict needed), `anyio.Event` is equivalent to OpenCode's `Deferred` (cross-framework validation), `_consumer_task_refs` (line 61) already exists for GC prevention, `_after_consumer_loop` needs no modification.

### D2: `create_child_session()` auto-emits `SpawnSessionStart`

**Choice**: `create_child_session()` in `context.py` automatically constructs and emits `SpawnSessionStart` with `tool_call_id` from `self.tool_call_id`, `depth` from `self.run_ctx.depth`, and `MAX_SUBAGENT_DEPTH` check. Callers simplified to 1 line.

**Why**: Eliminates 3 × 15-line boilerplate. `tool_call_id` filled from source (no disconnect). `depth` and `MAX_SUBAGENT_DEPTH` checked at framework level (not scattered across handlers). Modeled after Zed's `ThreadEnvironment::create_subagent()`.

**Note**: `self.events.emit_event()` used (not `self.node._events.emit_event()`) — `self.events` creates `StreamEventEmitter` with EventBus, `self.node._events` might bypass EventBus.

### D3: Race condition handling for `done_event`

**Choice**: When `done_event = self._consumer_done_events.get(child_sid)` returns `None` (consumer already exited), call `_notify_completed()` immediately instead of silently returning.

**Why**: Mixin's `finally` block does pop-then-set (line 248-250). If consumer exits quickly, `done_event` is popped before handler can grab it. Silent return would miss the completion notification — Zed never receives `ToolCallProgress(completed)`.

### D4: `kind="subagent"` not `"other"` or `"think"`

**Choice**: Use `kind="subagent"` in `ToolCallStart` for zed mode.

**Why**: Zed checks `kind` to trigger subagent UI rendering. OpenCode uses `kind="think"` because it's not an ACP client. AgentPool is an ACP server serving Zed — must use `"subagent"`.

### D5: MAX_SUBAGENT_DEPTH=5 (not 1, not configurable)

**Choice**: Set `MAX_SUBAGENT_DEPTH = 5` as a module-level constant. Depth 0 = root agent, depth 5 = 5th-level subagent. This allows 6 total agents in a chain (root + 5 children).

**Why**: The original limit of 1 was too restrictive for background task scenarios — a background task might need to spawn its own background tasks. 5 balances utility vs. resource consumption (each child session consumes memory, file descriptors, and potentially model API connections). This matches common subagent depth limits in production agent frameworks. Not made configurable via YAML to avoid premature optimization — can be added if users request it.

**Risk**: Deep nesting could cause resource exhaustion if a misbehaving agent recursively spawns children. Mitigated by the hard limit — `SubagentDepthError` at depth 6 stops recursion.

### D6: Background task completion via child_done_events dict (not counter)

**Choice**: Replace `pending_background_tasks: int` + `background_tasks_complete: asyncio.Event` with `child_done_events: dict[str, anyio.Event]` on `AgentRunContext`. Each child session gets its own `anyio.Event` registered at `create_child_session()` time. The `complete_background_task(child_session_id, message)` helper calls `steer_callback` first (queues message), then sets+pops the event (wakes RunExecutor). Framework safety net in `_run_turn_unlocked()` finally block sets the parent's event when child turn completes.

**Alternatives considered**:
- Keep `pending_background_tasks: int` counter + auto-increment in `create_child_session()` + auto-decrement in `_run_turn_unlocked()` finally: Rejected — decrement in finally fires BEFORE tool's steer callback (race condition: RunExecutor wakes with empty `queued_steer_messages`)
- `TaskGroup`-managed `_wait_and_steer` tasks: Rejected — `TaskGroup` cannot cross iteration boundaries (TG1 for first iteration exits before child completes; re-iteration loop creates TG2)
- EventBus-driven `ChildSessionComplete` event: Rejected — overengineered, requires new event type and subscription management

**Why**: `dict[str, anyio.Event]` is simpler than `int + Event` (no manual increment/decrement/clear/set). `complete_background_task()` ensures correct steer-then-signal ordering. Framework safety net in `_run_turn_unlocked()` handles tools that don't call the helper. `anyio.Event` aligns with `_consumer_done_events` pattern already used by `ProtocolEventConsumerMixin`.

**Key ordering**: `complete_background_task()` calls `steer_callback` (step 1, message queued) THEN pops the event from `child_done_events` via `.pop(key, None)` (step 2, removes key from dict) THEN sets the popped event (step 3, wakes RunExecutor, if event is not None). This guarantees RunExecutor always sees queued messages when it wakes. The pop-then-set pattern ensures graceful handling when the key was already popped by another path. The `_run_turn_unlocked()` finally safety net sets the event without steer — but this only fires if the tool didn't call `complete_background_task()`, meaning no result to deliver anyway.

## Risks / Trade-offs

- [done_event race condition] → Handled: None check + immediate notification via `_notify_completed()` helper
- [Closure captures `self`, handler destruction while task awaits] → Mitigated: `stop_event_consumer` cancels consumer → `done_event.set()` → closure wakes. Add 5-min timeout for crash recovery.
- [_consumer_task_refs memory leak] → Fixed: `contextlib.suppress(ValueError): self._consumer_task_refs.remove(task)` in finally block
- [_parent_of not cleaned on normal exit] → Fixed: `self._parent_of.pop(child_sid, None)` in closure after `done_event.wait()` and in immediate notification path
- [No error vs normal exit distinction] → Open question: `done_event` doesn't carry exit status. Future: check `RunHandle.status` before deciding `completed` vs `failed`.
- [PR #855 may deprecate `_meta` extension] → Mitigated: implement behind `zed` display mode feature flag, can migrate to native protocol when RFD merges
- [Consumer loop exit timing] → Need verification: `SessionPool` must close child session after `StreamCompleteEvent` for `_after_consumer_loop` to fire
- [child_done_events dict unbounded growth] → Mitigated: events are popped in `complete_background_task()`, `_run_turn_unlocked()` finally, and `close_session()`. If a tool creates a child session but never starts a run, the event lingers — caught by `close_session()` cleanup.
- [_run_turn_unlocked finally lookup chain failure] → Handled: if parent's `current_run_id` is None or RunHandle not found, the lookup is a no-op (graceful degradation). This happens when parent run already completed — the background task's result goes to `_post_turn_injections` fallback (existing behavior for late steer calls).
- [RunExecutor wait has no timeout] → Future consideration: if a background task hangs (e.g., child subprocess crashes without triggering the finally safety net), the RunExecutor blocks indefinitely. The `close_session()` safety net mitigates this for session closure, but a stuck background task during normal operation would hang the agent. Consider adding `anyio.fail_after(300)` with a warning log on timeout in a future iteration.
