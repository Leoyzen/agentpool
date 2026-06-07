## 1. Audit and Preparation

- [x] 1.1 Grep all `run_stream()` call sites in business layer to identify non-SessionPool paths
- [x] 1.2 Grep all `ctx.events.emit_event()` call sites to identify manual event emission
- [x] 1.3 Grep all `event_bus.subscribe()` call sites in business layer to identify manual subscriptions
- [x] 1.4 Review `subagent_tools.py` async mode — document current dual-path logic (SessionPool vs fallback)
- [x] 1.5 Review `workers.py` sync mode — document `SubAgentEvent` wrapping and `ctx.events.emit_event()` usage
- [x] 1.6 Review `agentpool_commands/pool.py` — document same manual event routing pattern
- [x] 1.7 Verify TurnRunner publishes `SubAgentEvent` to EventBus (if not, protocol layer will break)
- [x] 1.8 Run existing tests to establish baseline: `uv run pytest tests/toolsets/ -v`

## 2. Cleanup subagent_tools.py

- [x] 2.1 Remove `_consume_events_to_fs()` coroutine and all its helper logic
- [x] 2.2 Remove manual EventBus subscribe/unsubscribe in async mode (`event_queue = await session_pool.event_bus.subscribe(...)`)
- [x] 2.3 Simplify async mode to use only `session_pool.receive_request()` path
- [x] 2.4 Remove fallback non-SessionPool path in async mode (or verify it's truly unused)
- [x] 2.5 If filesystem output is required, add explicit post-run write using final message content
- [x] 2.6 Verify `subagent_tools.py` no longer contains any `event_bus.subscribe()` calls

## 3. Cleanup workers.py

- [x] 3.1 Replace `worker.run_stream()` with `session_pool.run_stream()` in `_create_agent_tool()` — sync mode needs blocking execution that returns final result
- [x] 3.2 Replace `worker.run_stream()` with `session_pool.run_stream()` in `_create_node_tool()` — same blocking requirement
- [x] 3.3 Remove manual `SubAgentEvent` wrapping loop (`async for event in stream: ... SubAgentEvent(...) ... emit_event()`) — TurnRunner handles this
- [x] 3.4 Remove `ctx.events.emit_event()` calls for subagent events — events flow through EventBus naturally
- [x] 3.5 Keep `SpawnSessionStart` emission (needed for protocol layer to detect child session creation)
- [x] 3.6 Verify `workers.py` no longer contains any `ctx.events.emit_event()` calls for stream events

## 4. Cleanup agentpool_commands/pool.py

- [x] 4.1 Remove manual `SubAgentEvent` wrapping and `ctx.events.emit_event()` calls
- [x] 4.2 Replace direct `agent.run_stream()` with `session_pool.run_stream()` where SessionPool is available
- [x] 4.3 Verify `pool.py` no longer contains any `SubAgentEvent` instantiation or `emit_event()` calls for stream events

## 5. Testing

- [x] 5.1 Update `tests/toolsets/test_subagent_tools.py` — remove assertions about filesystem output from async mode
- [x] 5.2 Update `tests/toolsets/test_subagent_tools.py` — add assertions that events reach EventBus (not filesystem)
- [x] 5.3 Update `tests/toolsets/test_workers.py` — remove assertions about manual `SubAgentEvent` emission
- [x] 5.4 Update `tests/toolsets/test_workers.py` — add assertions that child session events appear on EventBus
- [x] 5.5 Update `tests/toolsets/test_subagent_child_session.py` — update assertions that previously checked for `SubAgentEvent` in local event stream
- [x] 5.6 Update `tests/commands/test_pool.py` — remove assertions about manual event emission
- [x] 5.7 Run `tests/toolsets/` tests: `uv run pytest tests/toolsets/ -v`
- [x] 5.8 Run integration tests for subagent flows: `uv run pytest tests/servers/opencode_server/test_spawn_session_start.py -v`

## 6. Verification

- [x] 6.1 Run full unit tests: `uv run pytest -m unit`
- [x] 6.2 Run type checking: `uv run --no-group docs mypy src/agentpool_toolsets/ src/agentpool_commands/`
- [x] 6.3 Run lint: `uv run ruff check src/agentpool_toolsets/ src/agentpool_commands/`
- [x] 6.4 Verify no manual `event_bus.subscribe()` remains in business layer: `grep -r "event_bus.subscribe" src/agentpool_toolsets/ src/agentpool_commands/`
- [x] 6.5 Verify no manual `SubAgentEvent` wrapping remains in business layer: `grep -r "SubAgentEvent(" src/agentpool_toolsets/ src/agentpool_commands/`
- [x] 6.6 Verify no manual `ctx.events.emit_event()` for stream events remains: `grep -r "emit_event.*SubAgentEvent\|emit_event.*event" src/agentpool_toolsets/ src/agentpool_commands/`
- [ ] 6.7 Manual verification: run agent with subagent tool call, confirm events appear in opencode TUI

## 7. Documentation

- [x] 7.1 Update `subagent_tools.py` docstring to document simplified architecture
- [x] 7.2 Update `workers.py` docstring to document simplified architecture
- [x] 7.3 Update `agentpool_commands/pool.py` docstring to document simplified architecture
- [x] 7.4 Add code comment explaining why business layer does not handle events (reference EventBus descendants scope)
