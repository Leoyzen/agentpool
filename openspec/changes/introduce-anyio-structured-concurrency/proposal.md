## Why

AgentPool currently manages async task lifecycles through manual `asyncio.create_task()` with ad-hoc tracking dicts, done-callbacks, and `try/finally` blocks. This causes a production shutdown crash (`RuntimeError: SessionPool not available`) where consumer tasks attempt to access a SessionPool that was destroyed before their cleanup completed. More broadly, the lack of structured concurrency means subagent cancellation does not cascade from parent agents, background auto-resume tasks can leak, and MCP connection cleanup has no guaranteed ordering.

The codebase already imports `anyio` (52 files) but only for I/O primitives (streams, processes). Adopting `anyio.CancelScope` and `anyio.TaskGroup` provides structured concurrency — ensuring every async task is scoped to a well-defined lifecycle, cancellation propagates hierarchically, and cleanup is guaranteed at scope exit.

## What Changes

- **Add `anyio>=4.0` as a direct dependency**: Currently a transitive dependency. Structured concurrency primitives require a direct, versioned dependency.
- **Introduce per-session `anyio.CancelScope`**: Each session gets a cancellation boundary. Cancelling the session scope cancels all nested work (agent runs, subagents, MCP connections, consumer tasks).
- **Replace `ProtocolEventConsumerMixin` manual task dicts with per-session `anyio.TaskGroup`**: Consumer tasks become children of the session's task group. Cleanup is guaranteed at scope exit — no more fire-and-forget.
- **Replace `TaskManager` in `BaseServer` and 6 protocol servers with `anyio.TaskGroup`**: The `TaskManager` class is deprecated. Its priority queue is dead code (zero callers pass non-default `priority` or `delay`). `TaskGroup` provides all needed task tracking and cleanup. Non-BaseServer `TaskManager` instances (MessageNode, EventManager, StorageManager, etc.) are explicitly out of scope for this change — migrated in a follow-up.
- **Introduce nested `CancelScope` for subagent runs**: Subagent sessions get a `CancelScope` that is a child of the parent agent's scope. Cancelling the parent agent automatically cancels all subagents. This implements `SessionLifecyclePolicy.cascade` and `bound` natively via structured concurrency.
- **Fix shutdown ordering**: `AgentPool.__aexit__` ensures all consumer tasks have exited (via `TaskGroup.__aexit__`) before setting `self._session_pool = None`.
- **Shield critical cleanup**: Database writes, MCP connection close, log flushing, and `complete_event.set()` on `RunHandle` are wrapped in `CancelScope(shield=True)` to prevent interruption during teardown.
- **Migrate EventBus from `asyncio.Queue` to `anyio.create_memory_object_stream`**: Replace bounded `asyncio.Queue` with memory object streams in `EventBus` for backpressure (hybrid: 0.1s timeout + event-drop fallback before subscriber-drop), structured closing (no more sentinel `None`), and cleaner subscriber lifecycle.
- **Deprecate and remove `merge_queue_into_iterator`**: Remove the 144-line buggy utility and replace its sole caller in `ACPAgent._run_stream_once()` with `TaskGroup` + memory stream pattern, fixing documented `GeneratorExit`/cancel-scope bugs.
- **Unify `GraphStreamingAdapter` with `TaskGroup`**: Replace the manual `_iteration_task` + `asyncio.shield()` + `asyncio.wait_for()` pattern with `TaskGroup`, aligning with the `RunExecutor` pattern.

## Capabilities

### New Capabilities

- `structured-concurrency-lifecycle`: Per-session `CancelScope` and `TaskGroup` hierarchy for deterministic async task lifecycle management across all protocol servers.
- `taskmanager-removal`: Deprecation of `TaskManager` class in `BaseServer` and 6 protocol servers, replaced by `anyio.TaskGroup`. Non-BaseServer instances remain until follow-up change.
- `subagent-cancel-cascade`: Nested `CancelScope` for subagent runs, ensuring parent agent cancellation automatically cancels child subagents.
- `eventbus-memory-streams`: EventBus subscriber queues replaced with `anyio.create_memory_object_stream` — backpressure, structured closing, cleaner lifecycle.
- `deprecate-merge-queue-iterator`: Remove buggy `merge_queue_into_iterator`, replace with `TaskGroup` + memory stream pattern in ACP agent.
- `graph-streaming-adapter-taskgroup`: `GraphStreamingAdapter` iteration task managed by `TaskGroup` instead of manual `asyncio.shield()` + `wait_for()`.

### Modified Capabilities

- `child-session-policy`: The `cascade` and `bound` lifecycle policies gain structured concurrency enforcement via nested `CancelScope`. Existing behavior (children closed when parent closes) is preserved; implementation changes from manual iteration to scope cancellation.

## Impact

- **`pyproject.toml`**: Add `anyio>=4.0` as a direct dependency.
- **`src/agentpool_server/mixins.py`**: `ProtocolEventConsumerMixin` — replace `_consumer_tasks` dict + `_consumer_queues` dict with per-session `TaskGroup` + `CancelScope`. Public API (`start_event_consumer`, `stop_event_consumer`) unchanged.
- **`src/agentpool/utils/tasks.py`**: `TaskManager` class — deprecated but NOT removed. `BaseServer` and 6 protocol servers migrate to `TaskGroup`. Non-BaseServer instances (MessageNode, EventManager, StorageManager, PromptManager, StorageProvider, ACPConnectionManager) continue using `TaskManager` until follow-up change.
- **`src/agentpool_server/base.py`**: `BaseServer` — replace `self.task_manager` with pool-level `anyio.TaskGroup`. `cleanup_tasks()` replaced by `TaskGroup.__aexit__`.
- **`src/agentpool_server/acp_server/handler.py`**: `event_bus` property — made resilient to missing SessionPool during cleanup. `close_session()` ensures consumer tasks exit before SessionPool teardown.
- **`src/agentpool/orchestrator/core.py`**: `EventBus` — `asyncio.Queue` replaced with `anyio.create_memory_object_stream` for all subscriber queues; publish backpressure replaces drop-oldest; unsubscribe via stream close instead of sentinel `None`. `SessionState` — add `cancel_scope` field. `SessionController.close_session()` — cancel session scope before state cleanup. `TurnRunner` — replace `_background_tasks` set with session `TaskGroup`. `SessionPool.__aexit__` — ensure consumer tasks complete before `session_pool = None`.
- **`src/agentpool/orchestrator/run_executor.py`**: `RunExecutor.execute()` — wrap `_iteration_task` in run-scoped `TaskGroup`.
- **`src/agentpool/agents/base_agent.py`**: Subagent spawning — create nested `CancelScope` for child runs.
- **`src/agentpool/agents/acp_agent/acp_agent.py`**: `_run_stream_once()` — replace `merge_queue_into_iterator` with `TaskGroup` + `anyio.create_memory_object_stream`.
- **`src/agentpool/utils/streams.py`**: `merge_queue_into_iterator` function — removed entirely.
- **`src/agentpool/messaging/streaming_adapter.py`**: `GraphStreamingAdapter` — replace manual `_iteration_task` with `TaskGroup`.
- **`src/agentpool_server/acp_server/session.py`**: `ACPSession` — per-session `CancelScope` for MCP connections and agent runs.
- **`tests/delegation/test_break_behavior.py`**: The documented bugs (cancel scope task switching, token context mismatch, agent state corruption) are resolved by the `merge_queue_into_iterator` removal.
- **All 6 protocol servers** (ACP, OpenCode, AG-UI, OpenAI API, MCP, A2A): migrate from `TaskManager` to `TaskGroup`. Non-BaseServer `TaskManager` instances (MessageNode, EventManager, StorageManager, etc.) are out of scope.
