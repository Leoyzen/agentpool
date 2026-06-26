## 1. Foundation: WorkItem types + SessionState fields

- [ ] 1.1 Define `WorkItem ` union type (`SteerItem` | `FollowupItem`) in `core.py` near `SessionState`
- [ ] 1.2 Define `TurnState` enum (`IDLE`, `BOOTING`, `RUNNING`, `TEARDOWN`) in `core.py`
- [ ] 1.3 Add `work_send: ObjectSendStream[WorkItem]`, `work_receive: ObjectReceiveStream[WorkItem]`, `turn_state: TurnState = IDLE` fields to `SessionState.__init__`
- [ ] 1.4 Initialize `MemoryObjectStream[WorkItem]` with `max_buffer_size=256` in session creation path
- [ ] 1.5 Add `max_work_timeout: float = 30.0` config parameter to `TurnRunner.__init__`

## 2. State transitions in turn lifecycle

- [ ] 2.1 In `_run_turn_unlocked()`: set `session.turn_state = BOOTING` UNCONDITIONALLY at the top of the method (after turn_lock is held, before any agent logic). This is necessary because `receive_request()` sets `current_run_id` before `_run_turn_unlocked` is called, so the `if current_run_id is None` conditional would skip the transition.
- [ ] 2.2 In `RunExecutor.execute()` (`run_executor.py`): set `session.turn_state = RUNNING` when `active_agent_run` is set (via `run_handle` reference). Pass `session` reference to RunExecutor or access it through `run_handle`.
- [ ] 2.3 In `RunExecutor.execute()` (`run_executor.py`): set `session.turn_state = TEARDOWN` when `active_agent_run` is cleared (in `finally` block).
- [ ] 2.4 In `_run_turn_unlocked()`: set `session.turn_state = IDLE` when `current_run_id` is cleared (in `finally` block).

## 3. steer/followup simplification (with wake-up mechanism)

- [ ] 3.1 Rewrite native `steer()` to use `match session.turn_state`: `RUNNING` → `agent_run.enqueue(asap)`. `IDLE` → write `SteerItem` to work stream AND call `receive_request()` to start new `run_loop`. `BOOTING`/`TEARDOWN` → write `SteerItem` to work stream (existing run_loop will consume it). Remove TOCTOU `is None` checks.
- [ ] 3.2 Rewrite native `followup()` to keep `when_idle` enqueue via `agent_run.enqueue(priority="when_idle")` for `RUNNING` state. For all other states, write `FollowupItem` to work stream.
- [ ] 3.3 Update non-native `steer()` branch to use `turn_state` for TOCTOU elimination while keeping `injection_manager.inject()` for active-run case. Replace `_post_turn_injections` dict queue with work stream write for idle states.
- [ ] 3.4 Update non-native `followup()` branch to replace `_post_turn_prompts` dict queue with work stream write.
- [ ] 3.5 Update `inject_prompt()` and `queue_prompt()` in TurnRunner: replace `_post_turn_injections`/`_post_turn_prompts` dict writes with work stream writes (wrapping as `SteerItem`/`FollowupItem` respectively).
- [ ] 3.6 Update `SessionPool.inject_prompt()`: for non-native fallback, ensure it writes to work stream instead of calling removed dict helpers.
- [ ] 3.7 Remove `_post_turn_injections: dict` and `_post_turn_prompts: dict` fields from `TurnRunner.__init__`
- [ ] 3.8 Remove `_injection_locks` and `_injection_locks_lock` fields if no longer referenced

## 4. run_loop rewrite to consume from work stream

- [ ] 4.1 Rewrite `run_loop()` to: (a) run initial `_run_turn_unlocked`, (b) consume `WorkItem`s from `session.work_receive` in a `while True` with `asyncio.wait_for(..., timeout=self._max_work_timeout)`, (c) `match item` to call `_run_turn_unlocked` with appropriate args, (d) break on `TimeoutError` or `EndOfStream`
- [ ] 4.2 Replace internal call to `_process_queued_work` with inline work stream consumption
- [ ] 4.3 Remove `_process_queued_work()` method entirely
- [ ] 4.4 Remove `_safe_auto_resume()` and `_trigger_auto_resume()` methods entirely
- [ ] 4.5 Remove `_max_auto_resume` and `_enable_auto_resume` fields from `TurnRunner.__init__`

## 5. Cleanup: remove unused code + session close stream cleanup

- [ ] 5.1 Call `work_send.aclose()` in `close_session()` and `_close_session_unlocked()` to send `EndOfStream` to consumers
- [ ] 5.2 Remove `_drain_post_turn_injections()` and `_drain_post_turn_prompts()` helpers
- [ ] 5.3 Remove `_post_turn_injections`/`_post_turn_prompts` drain calls in `run_loop` `except` block
- [ ] 5.4 Remove `"steer"` → `"asap"` priority alias from `receive_request()` (steer no longer calls receive_request for active-run case)
- [ ] 5.5 Remove `_session_task_groups` cleanup if no longer referenced (check `inject_prompt`/`queue_prompt` still use it)

## 6. Test adaptation

- [ ] 6.1 Run `pytest tests/orchestrator/test_background_task_wakeup.py` — must PASS instead of FAIL
- [ ] 6.2 Update `test_steer_followup_integration.py` — fix tests that relied on `_post_turn_injections` dict or `_safe_auto_resume`
- [ ] 6.3 Update `test_steer_followup_edge_cases.py` — fix tests that check auto-resume behavior
- [ ] 6.4 Update `test_turn_runner.py` — fix tests that reference removed dict/managers
- [ ] 6.5 Update `test_session_controller.py` — fix tests that pass `enable_auto_resume` or check old behavior
- [ ] 6.6 Update `test_session_lifecycle.py` — fix any session lifecycle tests
- [ ] 6.7 Run `pytest tests/orchestrator/ -x --timeout=30` — confirm 0 new failures

## 7. Verification

- [ ] 7.1 Run `uv run ruff check src/agentpool/orchestrator/core.py` — 0 new violations
- [ ] 7.2 Run `uv run ruff format --check src/agentpool/orchestrator/core.py` — passes
- [ ] 7.3 Run `uv run --no-group docs mypy src/agentpool/orchestrator/core.py` — no new type errors
