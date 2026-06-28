## 1. RunHandle Cancellation Wiring

- [x] 1.1 Wire `_cancel_fn` in `RunHandle.start()`: set `self._cancel_fn` to a callable that schedules `agent._interrupt(self.run_ctx)` as a task stored in `self._interrupt_task`
- [x] 1.2 Verify `cancel()` still sets `self.run_ctx.cancelled = True` (26 cooperative checks depend on it) and `self._idle_event.set()`, then calls `self._cancel_fn()` if set
- [x] 1.3 Remove `_interrupt_tasks: set[asyncio.Task[None]]` field from `RunHandle` dataclass — replace with singular `_interrupt_task: asyncio.Task[None] | None` for GC safety. Verify no external references (grep `_interrupt_tasks`)
- [x] 1.4 Add `_create_cancel_fn()` helper method on `RunHandle` that returns the cancel callable — stores task reference in `self._interrupt_task`

## 2. BaseAgent.run_stream() Path B Refactor

- [x] 2.1 Keep `async with anyio.create_task_group() as tg:` block for producer (`_native_runner`/`_non_native_publisher`) — producer still runs inside task group with EventBus cleanup in shielded `finally`
- [x] 2.2 Move consumer loop (`async for envelope in drain_and_merge(stream): yield event`) to AFTER the `async with` block exits — `yield` is now outside any cancel scope
- [x] 2.3 Remove `tg.cancel_scope.cancel()` — task group exits naturally when producer completes
- [x] 2.4 Verify EventBus cleanup still runs in producer's `finally` block inside the task group
- [x] 2.5 Verify terminal event breaks (`StreamCompleteEvent`/`RunErrorEvent`) still work correctly outside the task group

## 3. ACP Agent Fix

- [x] 3.1 Keep `async with anyio.create_task_group() as tg:` in `ACPAgent._stream_events()` for `_forward_acp_events` and `_forward_secondary_events` forwarders
- [x] 3.2 Move consumer loop (`async for event in receive_stream: yield event` + event handling) to AFTER the `async with` block exits — `yield` is now outside any cancel scope
- [x] 3.3 Verify `ToolResultMetadataEvent` handling, `cancelled` check, `ToolCallCompleteEvent` enrichment, and `event_to_part` logic all preserved in the post-TG consumer loop

## 4. Event Ordering Preservation

- [x] 4.1 Verify `RunStartedEvent` is yielded first (already yielded by `NativeTurn.execute()` and `ACPTurn.execute()`)
- [x] 4.2 Verify `StreamCompleteEvent` / `RunErrorEvent` is yielded last and breaks the loop (terminal event breaks already in place)
- [ ] 4.3 Add test: consecutive `run_stream()` calls yield correct event ordering on both runs
- [x] 4.4 Verify `drain_and_merge` coalescing behavior preserved — consumer loop still uses `drain_and_merge(stream)` outside the task group

## 5. Lifecycle Verification

- [x] 5.1 Verify that `_run_stream_once()` lifecycle concerns (pre-run hooks, post-run hooks, `message_received`/`message_sent` signals, user message saving, connection routing, persistence) are still handled — Path B still calls `_run_stream_once()` inside the task group as the producer
- [x] 5.2 Verify `RunHandle.start()` path (Path A) is unaffected — no changes to `RunHandle.start()` cancel scope behavior

## 6. Test Updates

- [x] 6.1 Remove `@pytest.mark.xfail` from `test_subsequent_run_after_interrupt` in `tests/agents/native_agent/test_interrupt.py` — assert successful second run
- [ ] 6.2 Verify `tests/agents/test_concurrent_safety.py` no longer hangs (run with `--timeout=30`)
- [ ] 6.3 Verify `tests/orchestrator/test_e2e.py` no longer hangs (run with `--timeout=30`)
- [x] 6.4 Run full test suite for `tests/agents/native_agent/` directory — 169 passed, 4 skipped, 0 regressions

## 7. Verification

- [x] 7.1 Run `uv run ruff check` on changed source files — 11 errors (all pre-existing, down from 14)
- [ ] 7.2 Run `uv run --no-group docs mypy` on changed source files
- [ ] 7.3 Run `uv run pytest tests/agents/native_agent/test_interrupt.py tests/agents/test_concurrent_safety.py tests/orchestrator/test_e2e.py --timeout=30 -p no:cacheprovider` — all pass
