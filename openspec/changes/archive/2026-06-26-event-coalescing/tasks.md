## 1. EventBus Coalescing (no producer changes)

- [ ] 1.1 Add `_buffers`, `_last_keys`, `_buf_lock`, `_max_buffer` fields to `EventBus.__init__()`
- [ ] 1.2 Implement `_is_immediate()` — classify events as immediate vs batchable
- [ ] 1.3 Implement `_merge_key()` — return merge key tuple for `itertools.groupby`
- [ ] 1.4 Implement `_merge_text_deltas()`, `_merge_thinking_deltas()`, `_merge_tool_call_deltas()` — concatenate delta content
- [ ] 1.5 Implement `_merge_progress_events()` — merge `ToolCallProgressEvent` items and title
- [ ] 1.6 Implement `_merge_envelopes()` — use `itertools.groupby` to group and merge
- [ ] 1.7 Implement `_drain_buffer(session_id)` — atomic pop-under-lock, idempotent
- [ ] 1.8 Implement `_rebind()` — create new EventEnvelope with merged event, preserving source_session_id
- [ ] 1.9 Extract `_send(session_id, envelope)` — existing publish body (replay buffer + subscriber iteration + send)
- [ ] 1.10 Modify `publish()` — route batchable events through buffer with type-change trigger + buffer cap
- [ ] 1.11 Modify `close_session()` — drain coalescing buffer before closing subscribers
- [ ] 1.12 Add lock hierarchy comment block in EventBus: `_buf_lock` → `_lock`, never nested
- [ ] 1.13 Add unit tests: text delta merging, thinking delta merging, tool_call delta merging
- [ ] 1.14 Add unit tests: ToolCallProgressEvent merging by (tool_call_id, status)
- [ ] 1.15 Add unit tests: type-change flush, buffer cap flush, immediate event drain
- [ ] 1.16 Add unit tests: per-session isolation, idempotent drain, PlanUpdateEvent last-wins
- [ ] 1.17 Add unit tests: `close_session()` drains coalescing buffer
- [ ] 1.18 Add unit tests: `PartDeltaEvent` with `None` delta (dropped, not merged)
- [ ] 1.19 Verify existing EventBus tests pass unchanged

## 2. RunExecutor Direct Publishing

- [ ] 2.1 Add `event_bus` parameter to `RunExecutor.__init__()`
- [ ] 2.2 Replace `event_queue.put(event)` with `event_bus.publish(session_id, event)` in `agent_iteration_task`
- [ ] 2.3 Publish `RunErrorEvent` in `except` block BEFORE exception propagates
- [ ] 2.4 Publish `StreamCompleteEvent(cancelled=True)` in fallback path for pre-response cancellation
- [ ] 2.5 Remove `event_queue` (asyncio.Queue) field and initialization
- [ ] 2.6 Remove consumer poll loop (`asyncio.wait_for(event_queue.get(), timeout=0.1)` and related `TimeoutError` handling)
- [ ] 2.7 Change `execute()` return type from `AsyncIterator[RunExecutorEvent]` to `ChatMessage`
- [ ] 2.8 Remove `run_ctx.event_queue` pre-drain logic (events now go directly to EventBus)
- [ ] 2.9 Update RunExecutor unit tests for fire-and-forget pattern

## 3. SessionController & run_stream() Adaptation

- [ ] 3.1 Simplify `_run_turn_unlocked()` — replace `async for event in agent.run_stream()` yield/publish loop with `await agent.run()`
- [ ] 3.2 Remove conditional `_consume_event_queue()` task for non-native agents (EventBus handles all events)
- [ ] 3.3 Reimplement `Agent.run_stream()` as EventBus subscription wrapper:
  - Subscribe to EventBus before starting execution
  - Yield `envelope.event` from `async for envelope in stream`
  - Exit on `StreamCompleteEvent` or `RunErrorEvent`
  - Cancel TaskGroup after consumer exits
- [ ] 3.4 Simplify `StreamEventEmitter._emit()` — remove conditional EventBus vs `run_ctx.event_queue` routing; always publish to EventBus
- [ ] 3.5 Simplify `AgentContext.report_progress()` — same dual-routing removal as `_emit()` (context.py:167)
- [ ] 3.6 Simplify `_emit_deferred_event()` — same dual-routing removal (deferred_bridge.py:38)
- [ ] 3.7 Set `run_ctx.event_bus` in `BaseAgent.run_stream()` standalone Path B for non-native agent fallback
- [ ] 3.8 Update SessionController tests for direct EventBus publishing
- [ ] 3.9 Update `run_stream()` integration tests — verify SessionPool delegation preserved for pool-registered agents

## 4. Cleanup & Verification

- [ ] 4.1 Remove `batch_stream_deltas()` from `processors.py` (replaced by EventBus coalescing). Run `rg batch_stream_deltas` to verify no remaining references.
- [ ] 4.2 Remove `SubagentToolsetConfig.batch_stream_deltas` config flag from `agentpool_config/toolsets.py:192` and unused wiring in `subagent_tools.py`
- [ ] 4.3 Remove `AgentRunContext.event_queue` field from `context.py` (dead code after path unification)
- [ ] 4.4 Add debug-level logging for coalescing: buffer drain (event count, merge count), buffer cap hit (WARNING), immediate event flush, type-change flush. Use logger `agentpool.orchestrator.eventbus.coalescing`.
- [ ] 4.5 Run full test suite: `uv run pytest`
- [ ] 4.6 Run type checking: `uv run mypy src/`
- [ ] 4.7 Run linting: `uv run ruff check src/`
