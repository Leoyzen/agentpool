## 1. TurnRunner Core Changes

- [x] 1.1 Remove `run_handle.complete_event.set()` from `_run_turn_unlocked()` finally block
- [x] 1.2 Add `run_handle.complete_event.set()` at the end of `run_loop()` after `_process_queued_work()` returns
- [x] 1.3 Handle error paths in `run_loop()`: ensure `complete_event` is set in finally block
- [x] 1.4 Verify `_cleanup_run()` in `SessionController` still works correctly with delayed `complete_event`

## 2. OpenCodeSessionPoolIntegration Consumer Management

- [x] 2.1 Add `_event_consumers: dict[str, asyncio.Task[Any]]` field to `OpenCodeSessionPoolIntegration`
- [x] 2.2 Implement `_start_event_consumer(session_id: str)` method
- [x] 2.3 Implement `_event_consumer_loop(session_id: str)` with EventBus subscription and event conversion
- [x] 2.4 Implement `_stop_event_consumer(session_id: str)` method
- [x] 2.5 Call `_start_event_consumer()` in `create_session()` alongside `_start_status_bridge()`
- [x] 2.6 Call `_stop_event_consumer()` in `shutdown()` alongside `_stop_status_bridge()`
- [x] 2.7 Handle `SpawnSessionStart` events in consumer loop for child session tracking

## 3. message_routes.py Simplification

- [x] 3.1 Remove `SessionStatusBridge` creation from `_process_message_locked()`
- [x] 3.2 Remove `event_bus.subscribe()` call from `_process_message_locked()`
- [x] 3.3 Remove `_consume_events()` task creation from `_process_message_locked()`
- [x] 3.4 Remove `event_bus.unsubscribe()` from finally block
- [x] 3.5 Remove `status_bridge.stop()` from finally block
- [x] 3.6 Keep `run_handle.complete_event.wait()` as the primary synchronization mechanism
- [x] 3.7 Verify assistant message finalization still works without local consumer

## 4. Testing

- [x] 4.1 Write unit test for delayed `complete_event` in `TurnRunner`
- [x] 4.2 Write unit test for session-scoped consumer lifecycle (start/stop)
- [x] 4.3 Write integration test: auto-resume events are consumed and broadcast
- [x] 4.4 Write integration test: multiple requests to same session share one consumer
- [x] 4.5 Write integration test: child session events are consumed via descendant scope
- [x] 4.6 Verify existing OpenCode server tests still pass (orchestrator tests all pass)
- [ ] 4.7 Run manual test with OpenCode TUI to verify event streaming

## 5. Cleanup and Verification

- [x] 5.1 Remove unused imports from `message_routes.py` (SessionStatusBridge, etc.)
- [x] 5.2 Verify no memory leaks from long-running consumers
- [x] 5.3 Verify `close_session` properly cleans up both bridge and consumer
- [x] 5.4 Update docstrings to reflect new session-scoped architecture
- [ ] 5.5 Run full test suite: `uv run pytest`
