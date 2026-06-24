## 1. Phase 1: Foundation — Session CancelScope + RunExecutor TaskGroup

- [x] 1.1 Add `anyio>=4.0` as a direct dependency in `pyproject.toml`; run `uv lock --upgrade-package anyio`
- [x] 1.2 Add `cancel_scope: anyio.CancelScope` field to `SessionState` in `src/agentpool/orchestrator/core.py`, initialized via `field(default_factory=anyio.CancelScope)`
- [x] 1.3 Add `_session_scopes: dict[str, anyio.CancelScope]` tracking to `SessionController`; populate on `spawn_session()` and `create_session()`
- [x] 1.4 Refactor `RunExecutor.execute()` in `src/agentpool/orchestrator/run_executor.py`: wrap `_iteration_task` creation in `anyio.create_task_group()`, spawn iteration task via `tg.start_soon()`
- [x] 1.5 Replace manual `_iteration_task.cancel()` + `asyncio.shield()` + `asyncio.wait_for()` in RunExecutor finally block with `TaskGroup.__aexit__` (shielded cleanup remains for `active_agent_run` ContextVar reset)
- [x] 1.6 Run `uv run pytest tests/ -k "run_executor" -x` to verify Phase 1 changes (NOTE: 1 pre-existing test failure in test_cancelled_before_response_fallback - test expects "[Interrupted]" but PydanticAI agents now return their own "Request interrupted by user" message. This is a test expectation issue, not a regression from TaskGroup refactor.)

## 2. Phase 2: Protocol Consumer Mixin Refactor

- [x] 2.1 Refactor `ProtocolEventConsumerMixin.__init__()` in `src/agentpool_server/mixins.py`: replace `_consumer_tasks` dict and `_consumer_queues` dict with `_session_scopes: dict[str, anyio.CancelScope]` and `_session_groups: dict[str, anyio.TaskGroup]`
- [x] 2.2 Refactor `start_event_consumer()`: create per-session `CancelScope` + `TaskGroup`, spawn consumer loop via `tg.start_soon()`, store in tracking dicts
- [x] 2.3 Refactor `stop_event_consumer()`: cancel session scope, exit TaskGroup, call `event_bus.unsubscribe()` after group exits
- [x] 2.4 Refactor `_event_consumer_loop()`: remove `event_bus.unsubscribe()` from finally block (now handled by `stop_event_consumer`); keep `_after_consumer_loop()` call
- [x] 2.5 Add defense-in-depth: cache `EventBus` reference in `event_bus` property on `ACPProtocolHandler` (`src/agentpool_server/acp_server/handler.py`) to prevent crash if accessed after SessionPool teardown
- [x] 2.6 Update `ACPProtocolHandler._event_consumer_loop()` override (handler.py line 206): adapt lazy subscribe fallback to work with the new TaskGroup/CancelScope-based consumer lifecycle (still uses `asyncio.Queue` in this phase; memory stream adaptation deferred to Phase 4 task 4.7a)
- [x] 2.7 Update `OpenCodeSessionPoolIntegration.shutdown()` (session_pool_integration.py line 866): iterate `_session_groups` instead of `_consumer_tasks.keys()`
- [x] 2.8 Run `uv run pytest tests/ -k "acp" -x` and `uv run pytest tests/ -k "opencode" -x` to verify ACP and OpenCode protocol consumers

## 3. Phase 3: Session Lifecycle — TurnRunner + Subagent CancelScope

- [x] 3.1 Replace `TurnRunner._background_tasks` set in `src/agentpool/orchestrator/core.py` with session-scoped `TaskGroup`; spawn auto-resume tasks via `tg.start_soon()`
- [x] 3.2 Add exception-catching wrapper `_safe_auto_resume()` for auto-resume tasks spawned via `inject_prompt()`, `queue_prompt()`, `steer()`, `followup()` — one auto-resume failure MUST NOT cancel sibling auto-resume tasks
- [x] 3.3 Add exception-catching wrapper for tool executions in run-scoped `TaskGroup` — one tool failure MUST NOT cancel sibling tool executions
- [x] 3.4 Write test: spawn 2 auto-resume tasks in TaskGroup, verify sibling isolation
- [x] 3.5 Update `TurnRunner._run_turn_unlocked()` finally block: remove manual cleanup, rely on session `TaskGroup`
- [x] 3.6 Implement subagent `CancelScope` nesting in `SessionController.spawn_session()`
- [x] 3.6a Scope subagent MCP connections within subagent's `CancelScope`
- [x] 3.7 Update `SessionController.close_session()`: cancel session's `CancelScope` before state cleanup; ensure child scopes are cancelled before parent via nesting
- [x] 3.8 Fix `AgentPool.__aexit__()` in `src/agentpool/delegation/pool.py`: call `_stop_all_consumers()` before `session_pool.shutdown()`; set `self._session_pool = None` only after all consumers have exited
- [x] 3.9 Add `_stop_all_consumers()` helper to `AgentPool` (`src/agentpool/delegation/pool.py`): iterate `self._protocol_servers` list (maintained by `add_server()` calls from CLI/server entry points), call `server.stop_event_consumers()` on each protocol handler that implements it; store `self._protocol_servers: list = []` and add `add_server(server)` method if not already present
- [x] 3.10 Run `uv run pytest tests/ -k "session" -x` and `uv run pytest tests/ -k "subagent" -x` to verify session lifecycle changes (NOTE: 1 pre-existing test failure in test_subagent_child_session_parent_id_in_session_data - confirmed pre-existing via git stash. 1 pre-existing error in test_load_session_calls_api_with_correct_params - unrelated to our changes.)

## 4. Phase 4: EventBus Memory Streams

- [x] 4.1 Refactor `EventBus.__init__()` in `src/agentpool/orchestrator/core.py`: replace `_subscribers: dict[str, list[tuple[asyncio.Queue, str]]]` with `_subscribers: dict[str, list[tuple[MemoryObjectSendStream, str]]]`
- [x] 4.2 Refactor `EventBus.subscribe()`: create `anyio.create_memory_object_stream(max_buffer_size=1000)`, replay historical events via `send.send_nowait()`, return `MemoryObjectReceiveStream` instead of `asyncio.Queue`
- [x] 4.3 Refactor `EventBus.unsubscribe()`: find and close the corresponding send stream via `aclose()` (consumer gets `EndOfStream`); remove sentinel `None` pattern
- [x] 4.4 Implement hybrid backpressure in `publish()`: wrap `send.send()` in `anyio.fail_after(0.1)`; on first 2 timeouts, drop oldest buffered event via `send_nowait(get_nowait)`; on 3rd consecutive timeout, close and drop subscriber
- [x] 4.5 Add parallel publishing: fan out to subscribers concurrently using `anyio.create_task_group()` instead of sequential iteration under lock
- [x] 4.6 Add `anyio.Lock` to `EventBus` for thread safety (replaces `asyncio.Lock`)
- [x] 4.7 Update `ProtocolEventConsumerMixin._event_consumer_loop()` in `src/agentpool_server/mixins.py`: use `async for envelope in receive_stream:` instead of `while True: envelope = await queue.get()`; exit on `EndOfStream` instead of `None` sentinel
- [x] 4.7a Update `ACPProtocolHandler._event_consumer_loop()` override (handler.py line 206): adapt lazy subscribe fallback to work with `MemoryObjectReceiveStream` instead of `asyncio.Queue` (deferred from Phase 2 task 2.6; now that memory streams exist)
- [x] 4.8 Update `ACPAgent._run_stream_once()` in `src/agentpool/agents/acp_agent/acp_agent.py`: work with `MemoryObjectReceiveStream` from `event_bus.subscribe()` instead of `asyncio.Queue`
- [x] 4.9 Update OpenCode `global_routes.py` SSE subscription: adapt to new `subscribe()` return type
- [x] 4.10 Write test: subscribe to EventBus, delay consumption, publish 50 events, verify backpressure doesn't deadlock and event-drop + subscriber-drop thresholds work correctly
- [x] 4.11 Run `uv run pytest tests/ -k "event_bus" -x` to verify EventBus changes

## 5. Phase 5: merge_queue_into_iterator Removal

- [x] 5.1 Create `_forward_acp_events(async_iter, send_stream)` helper coroutine and `_forward_queue_events(queue, send_stream)` helper coroutine in `src/agentpool/agents/acp_agent/acp_agent.py`
- [x] 5.2 Refactor `ACPAgent._run_stream_once()`: replace `async with merge_queue_into_iterator(poll_acp_events(), event_source) as merged:` with `async with anyio.create_task_group() as tg:` + two `tg.start_soon()` forwarders + `async for event in receive_stream:`
- [x] 5.3 Remove `merge_queue_into_iterator` function and all related imports from `src/agentpool/utils/streams.py`
- [x] 5.3a Verify `streams.py` retains all other exports unchanged: assert `from agentpool.utils.streams import async_tee, stream_to_queue, buffer_stream` still works; verify no other callers import `merge_queue_into_iterator`
- [x] 5.4 Verify `tests/delegation/test_break_behavior.py` documented bugs are fixed: run the test script and confirm no `RuntimeError: Attempted to exit cancel scope in a different task` or `ValueError: Token was created in a different Context`
- [x] 5.5 Run `uv run pytest tests/ -k "acp_agent" -x` to verify ACP agent changes

## 6. Phase 6: GraphStreamingAdapter TaskGroup

- [x] 6.1 Refactor `GraphStreamingAdapter.__aiter__()` in `src/agentpool/messaging/streaming_adapter.py`: wrap `_graph_iteration_task()` in `anyio.create_task_group()` via `tg.start_soon()`
- [x] 6.2 Remove `_iteration_task: asyncio.Task | None` field from `GraphStreamingAdapter.__init__()`
- [x] 6.3 Remove timeout-based polling: replace `await asyncio.wait_for(event_queue.get(), timeout=0.1)` with `await event_queue.get()` (no timeout; cancellation via TaskGroup)
- [x] 6.4 Remove `asyncio.shield()` + `asyncio.wait_for()` cleanup pattern from `__aiter__()` finally block; rely on `TaskGroup.__aexit__`
- [x] 6.5 Keep `_iteration_done` event and `_iteration_error` for downstream compatibility
- [x] 6.6 Write test: break from `__aiter__` mid-stream, verify `_iteration_task` completes within 2s, verify no `asyncio.shield()` or `wait_for()` in implementation
- [x] 6.7 Run `uv run pytest tests/ -k "streaming_adapter" -x` to verify adapter changes

## 7. Phase 7: TaskManager Deprecation (BaseServer + 6 Protocol Servers)

- [ ] 7.1 Add pool-level `anyio.TaskGroup` to `BaseServer.__init__()` in `src/agentpool_server/base.py`; enter group in `start()` and exit in `shutdown()`
- [ ] 7.2 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `BaseServer`
- [ ] 7.3 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `ACPServer` (`src/agentpool_server/acp_server/server.py`)
- [ ] 7.4 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `OpenCodeServer` (`src/agentpool_server/opencode_server/server.py`)
- [ ] 7.5 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `AGUIServer` (`src/agentpool_server/agui_server/server.py`)
- [ ] 7.6 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `OpenAIServer` (`src/agentpool_server/openai_api_server/server.py`)
- [ ] 7.7 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `MCPServer` (`src/agentpool_server/mcp_server/server.py`)
- [ ] 7.8 Replace `self.task_manager.create_task(coro)` with `self._task_group.start_soon(coro)` in `A2AServer` (`src/agentpool_server/a2a_server/server.py`)
- [ ] 7.9 Mark `TaskManager` class as deprecated (add `DeprecationWarning` in `__init__`); keep the class for non-BaseServer instances (MessageNode, EventManager, StorageManager, etc.) until follow-up change
- [ ] 7.10 Remove all `from agentpool.utils.tasks import TaskManager` imports from `BaseServer` and 6 protocol server files
- [ ] 7.11 Run full test suite: `uv run pytest -x` to verify no regressions

## 8. Phase 8: Shielded Cleanup + Verification

- [ ] 8.1 Add `CancelScope(shield=True)` around database write operations in session persistence (`src/agentpool/storage/`)
- [ ] 8.2 Add `CancelScope(shield=True)` with 5s timeout around MCP connection close in `MCPManager.cleanup()` (`src/agentpool/mcp_server/manager.py`)
- [ ] 8.3 Add `CancelScope(shield=True)` around `complete_event.set()` on `RunHandle` in `TurnRunner.run_loop()` finally block
- [ ] 8.4 Write automated regression test for shutdown crash: create ACP server, create session with consumer task, trigger `AgentPool.__aexit__`, assert no `RuntimeError("SessionPool not available")` is raised
- [ ] 8.5 Write automated test for subagent cancellation cascade: create parent agent, spawn subagent, cancel parent, assert subagent receives `CancelledError` within 5s
- [ ] 8.6 Verify `merge_queue_into_iterator` removal: assert `from agentpool.utils.streams import merge_queue_into_iterator` raises `ImportError`
- [ ] 8.7 Run `uv run pytest -m slow` for full integration test pass
- [ ] 8.8 Run `uv run ruff check src/` and `uv run ruff format --check` for code quality
