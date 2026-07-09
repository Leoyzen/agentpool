## 1. Lifecycle Types, Protocols & EventEnvelope

- [x] 1.1 Create `src/agentpool/lifecycle/__init__.py` with public exports for all six dimension Protocols, their default implementations, and shared types
- [x] 1.2 Create `src/agentpool/lifecycle/types.py` defining `RunState` enum (`IDLE`, `RUNNING`, `DONE`), `Prompt` dataclass (`content: str`, `priority: str = "normal"`, `metadata: dict[str, Any] = {}`), `Feedback` dataclass (`content: str`, `is_steer: bool`), `ResumeResult` dataclass (`is_inflight: bool`, `state: RunState | None`, `events: list[Any]`, `inflight_turn_id: str | None`), `ToolExecutionRecord` dataclass (`turn_id: str`, `tool_name: str`, `args: dict`, `result: Any | None`, `status: str`), and `EventEnvelope` dataclass with M6-compatible fields: `schema_version: str = "1.0.0"`, `event_type: str`, `session_id: str`, `turn_id: str | None = None`, `timestamp: str` (ISO 8601 format), `payload: dict[str, Any]`, `seq: int | None = None` (optional, set by Journal-backed transports, not set by InProcessTransport), `metadata: dict[str, Any]` (optional, for extensible metadata). M2 uses a dataclass; M6 will upgrade to a Pydantic model with the same field names and types.
- [x] 1.3 Create `src/agentpool/lifecycle/protocols.py` defining all five `@runtime_checkable` Protocols: `TriggerSource` (`subscribe`, `poll`, `close`), `Journal` (`append`, `upsert`, `replay`, `resume`, `compact`, `clear`, `log_tool_execution`, `get_tool_executions`), `SnapshotStore` (`save`, `load`, `save_turn_result`, `has_turn_result`, `clear`), `CommChannel` (`attach`, `on_state_change`, `publish`, `recv`, `close`, `_replaying`), `EventTransport` (`publish`, `subscribe`, `ack`, `close`)
- [x] 1.4 Add `StateUpdate` event class to `src/agentpool/agents/events/events.py` (`session_id: str`, `state: RunState`, `stop_reason: str | None = None`) and add to `RichAgentStreamEvent` union; add to `src/agentpool/agents/events/__init__.py` exports
- [x] 1.5 Write unit tests in `tests/lifecycle/test_types.py`: RunState enum values, Prompt/Feedback/ResumeResult/ToolExecutionRecord/EventEnvelope construction and defaults, EventEnvelope JSON serializability (with `timestamp` as ISO 8601 string, `seq` as None by default, `payload` as dict), StateUpdate construction and union membership, Protocol `isinstance` conformance for dummy implementations

## 2. TriggerSource Dimension

- [x] 2.1 Create `src/agentpool/lifecycle/triggers.py` implementing `ImmediateTrigger` (constructor takes `prompt: str`, `poll()` returns `Prompt` once then `None`, `subscribe()` no-op, `close()` no-op) and `ProtocolTrigger` (internal `asyncio.Queue`, `deliver(content, priority="normal")` enqueues, `poll()` non-blocking dequeue, `subscribe()` stores RunLoop ref, `close()` cancels queue)
- [x] 2.2 Define `ScheduledTrigger` and `ChannelTrigger` stubs in same file — constructors store config, methods raise `NotImplementedError`, docstrings note deferred implementation
- [x] 2.3 Write unit tests in `tests/lifecycle/test_trigger_source.py`: TriggerSource protocol conformance, ImmediateTrigger single-delivery then None, ProtocolTrigger deliver+poll round-trip, ProtocolTrigger empty poll returns None, ScheduledTrigger/ChannelTrigger stubs raise NotImplementedError

## 3. Journal Dimension

- [x] 3.1 Create `src/agentpool/lifecycle/journal.py` implementing `MemoryJournal`: in-memory lists for append entries and upsert entries, monotonic `_seq` counter, `append()` creates new entry, `upsert()` replaces by key, `replay()` async iterator with upsert dedup, `resume()` delegates to `snapshot_store.load()` returning `ResumeResult` or `None`, `compact()` removes entries below threshold, `clear()` resets all, `log_tool_execution()` / `get_tool_executions()` store/retrieve by turn_id
- [x] 3.2 Implement `DurableJournal` in same file: SQL backend with WAL mode, crash-safe `append()`/`upsert()` with fsync, `replay()` queries from DB, `resume()` loads snapshot and replays detecting in-flight Turns, `compact()` deletes old entries, `log_tool_execution()` writes to separate tool_log table — uses existing SQL patterns from `src/agentpool_storage/sql_provider/`
- [x] 3.3 Write unit tests in `tests/lifecycle/test_journal.py`: Journal protocol conformance, MemoryJournal append monotonic seq, upsert replaces by key, replay ordering with dedup, resume returns None without snapshot, tool execution log round-trip, compact removes old entries, clear resets, DurableJournal data survives re-instantiation, DurableJournal crash recovery scenario

## 4. SnapshotStore Dimension

- [x] 4.1 Create `src/agentpool/lifecycle/snapshot_store.py` implementing `MemorySnapshotStore`: in-memory dict for latest snapshot `(state, last_journal_seq)`, dict for turn results, `save()` stores and returns seq, `load()` returns tuple or None, `save_turn_result()` / `has_turn_result()` by turn_id, `clear()` resets both dicts
- [x] 4.2 Implement `DurableSnapshotStore` in same file: SQL backend with crash-safe atomic writes, `save()` writes state blob with fsync, `load()` reads latest rejecting partial/corrupt, `save_turn_result()` / `has_turn_result()` persisted to DB, data survives re-instantiation
- [x] 4.3 Write unit tests in `tests/lifecycle/test_snapshot_store.py`: SnapshotStore protocol conformance, MemorySnapshotStore save+load round-trip, load returns None when empty, save_turn_result+has_turn_result, clear resets, DurableSnapshotStore persistence, independent composability (MemoryJournal + DurableSnapshotStore works)

## 5. CommChannel Dimension

- [x] 5.1 Create `src/agentpool/lifecycle/comm_channel.py` implementing `DirectChannel`: constructor takes `journal: Journal`, internal `asyncio.Queue` for events, `_replaying: bool = False`, `publish()` journals (append/upsert) when not replaying then enqueues, `recv()` always returns None (unidirectional), `attach()` / `on_state_change()` store refs, `close()` cancels queue
- [x] 5.2 Implement `ProtocolChannel` in same file: constructor takes `journal: Journal` and `event_bus: EventBus`, `publish()` journals then publishes to EventBus, internal feedback queue, `recv()` dequeues feedback, `on_state_change()` tracks RunLoop state for steer/followup routing, `close()` cleans up
- [x] 5.3 Implement upsert key derivation `_derive_upsert_key(event) -> str | None`: `ToolCallUpdateEvent` → `f"tool_call:{tool_call_id}"`, `StateUpdate` → `f"state:{session_id}"`, `MessageReplacementEvent` → `f"msg:{message_id}"`, `PlanUpdateEvent` → `f"plan:{plan_id}"`, returns None for delta events (use append)
- [x] 5.4 Write unit tests in `tests/lifecycle/test_comm_channel.py`: CommChannel protocol conformance, DirectChannel publish enqueues + journals, DirectChannel publish skips journaling when `_replaying=True`, DirectChannel recv returns None, upsert key derivation for each event type, ProtocolChannel publish delivers to EventBus, ProtocolChannel feedback queue round-trip

## 6. EventTransport Dimension

- [x] 6.1 Create `src/agentpool/lifecycle/event_transport.py` implementing `InProcessTransport`: per-topic `asyncio.Queue`, optional replay buffer (`replay_buffer_size` param), `publish()` pushes to queue and replay buffer, `subscribe()` returns AsyncIterator (yields replayed events first if `from_seq > 0`, then new), `ack()` no-op, `close()` cancels all queues
- [x] 6.2 Write unit tests in `tests/lifecycle/test_event_transport.py`: EventTransport protocol conformance, publish+subscribe round-trip, replay buffer for late subscribers, ack is no-op, close prevents further publish
- [x] 6.3 Wire `InProcessTransport` into RunLoop: RunLoop constructor accepts `event_transport: EventTransport | None = None` (defaults to `InProcessTransport`), RunLoop owns EventTransport lifecycle (`start()` makes it available to CommChannel, `close()` calls `event_transport.close()`), CommChannel receives EventTransport reference for optional cross-process delivery delegation

## 7. RunLoop (Restructured RunHandle)

- [x] 7.1 Create `src/agentpool/lifecycle/run_loop.py` defining `RunLoop` class: constructor takes `agent`, optional `trigger_source`, `journal`, `snapshot_store`, `comm_channel`, `event_transport`, `session_id` — defaults to `ImmediateTrigger`, `MemoryJournal`, `MemorySnapshotStore`, `DirectChannel(journal)`, `InProcessTransport`, `"default"`. Inject journal into custom CommChannel via `comm_channel._journal = self._journal`. Inject event_transport into CommChannel for optional cross-process delivery delegation.
- [x] 7.2 Implement state machine: `_state: RunState = RunState.IDLE`, `is_running` property, `_transition(new_state)` updates state, calls `comm_channel.on_state_change(state)`, publishes `StateUpdate` via `comm_channel.publish()`. For IDLE after crash recovery, includes `stop_reason="crash_recovery"`.
- [x] 7.3 Implement `start(initial_prompt: str | None = None)` as async method (not generator): call `journal.resume(snapshot_store)` first, handle fresh start (None → save initial snapshot), handle in-flight recovery (set `_replaying=True`, replay events to comm_channel, publish `StateUpdate(IDLE, stop_reason="crash_recovery")`, skip interrupted Turn), handle normal recovery (resume from snapshot), then call `trigger_source.subscribe(self)`, `comm_channel.attach(self)`, enter main loop
- [x] 7.4 Implement `_main_loop()`: poll trigger for prompt, transition to RUNNING, call `agent.create_turn()` with message queue and generated `turn_id`, iterate `turn.execute()` publishing each event via `comm_channel.publish()`, after exhaustion call `snapshot_store.save()` + `snapshot_store.save_turn_result()`, check `comm_channel.recv()` for feedback routing, check `has_turn_result()` for idempotency skip, loop until trigger returns None and queue empty
- [x] 7.5 Implement `steer(content: str)`: if RUNNING inject into active Turn context, if IDLE queue as followup and wake
- [x] 7.6 Implement `followup(content: str)`: append to `_message_queue` without interrupting active Turn
- [x] 7.7 Implement `close()`: drain pending messages as final Turns, transition to DONE, call `comm_channel.close()`, `trigger_source.close()`, and `event_transport.close()`
- [x] 7.8 Write unit tests in `tests/lifecycle/test_run_loop.py`: default dimensions construction, journal injection into custom CommChannel, fresh start with ImmediateTrigger (idle→running→done), multi-Turn with ProtocolTrigger (idle→running→idle cycles), steer during active Turn, steer when idle queues as followup, followup does not interrupt, close drains pending then DONE, snapshot at Turn boundary, idempotency skip, StateUpdate on every transition, crash recovery in-flight replay sets `_replaying`, feedback routing from `comm_channel.recv()` to steer/followup

## 8. Lifecycle Config, Dimension Factory & Agent Integration

- [x] 8.1 Create `src/agentpool_config/lifecycle.py` defining `LifecycleConfig` Pydantic model: `journal: Literal["memory", "durable"] = "memory"`, `snapshot: Literal["memory", "durable"] = "memory"`, `recover_strategy: Literal["mark_interrupted", "retry"] = "mark_interrupted"`
- [x] 8.2 Add `lifecycle: LifecycleConfig | None = None` field to `BaseAgentConfig` in `src/agentpool_config/nodes.py`
- [x] 8.3 Create `src/agentpool/lifecycle/factory.py` with `create_dimensions(lifecycle_config, session_id)` — maps config to dimension implementations, returns defaults when None or all-defaults
- [x] 8.4 Modify `BaseAgent.run()` in `src/agentpool/agents/base_agent.py` to create a `RunLoop` with default dimensions (`ImmediateTrigger`, `MemoryJournal`, `MemorySnapshotStore`, `DirectChannel`) and call `start()` — public API unchanged
- [x] 8.5 Modify `BaseAgent.run_stream()` to create a `RunLoop` with default dimensions, iterate `start()` yielding events from `DirectChannel` — public API unchanged
- [x] 8.6 Modify `AgentFactory.compile()` in `src/agentpool/host/factory.py` to read `lifecycle:` config and pass `LifecycleConfig` to agent for RunLoop construction
- [x] 8.7 Write unit tests in `tests/lifecycle/test_config_and_integration.py`: LifecycleConfig defaults, durable config mapping, BaseAgentConfig accepts lifecycle field, create_dimensions returns correct implementations, agent.run() routes through RunLoop with identical result, agent.run_stream() yields identical events, lifecycle durable config produces DurableJournal+DurableSnapshotStore

## 9. SessionController & Protocol Server Migration

- [x] 9.1 Modify `SessionController.receive_request()` in `src/agentpool/orchestrator/session_controller.py` to create/reuse a `RunLoop` with `ProtocolTrigger` and `ProtocolChannel` instead of `RunHandle` — deliver prompt via `ProtocolTrigger.deliver()`
- [x] 9.2 Modify `SessionController.steer()` and `followup()` to deliver `Feedback` to `ProtocolChannel`'s feedback queue; ensure `close_session()` calls `RunLoop.close()`
- [x] 9.3 Update `SessionPool` to manage `RunLoop` instances instead of `RunHandle` — adapt `_runs` dict type, `close_session()`, `cancel_run()`
- [x] 9.4 Update ACP server handler (`src/agentpool_server/acp_server/handler.py`) to use `RunLoop` API — adapt event consumer, steer/followup via `ProtocolTrigger`/`ProtocolChannel`
- [x] 9.5 Update OpenCode server (`src/agentpool_server/opencode_server/session_pool_integration.py`) to use `RunLoop` API
- [x] 9.6 Update AG-UI server (`src/agentpool_server/agui_server/server.py`) and OpenAI API server (`src/agentpool_server/openai_api_server/server.py`) to use `RunLoop` API
- [x] 9.7 Write unit tests in `tests/lifecycle/test_session_migration.py`: receive_request creates RunLoop with ProtocolTrigger/ProtocolChannel, steer/followup deliver Feedback to ProtocolChannel, close_session calls RunLoop.close(), ACP/OpenCode/AG-UI/OpenAI event delivery works through ProtocolChannel

## 10. Crash Recovery & Tool Execution Log

- [x] 10.1 Wire `DurableJournal.resume()` to coordinate with `DurableSnapshotStore.load()`: load snapshot, replay journal since snapshot seq, detect in-flight Turn (entries after snapshot with no `turn_result`), return `ResumeResult` with `is_inflight` and `inflight_turn_id`
- [x] 10.2 Implement `recover_strategy: "mark_interrupted"`: after in-flight recovery, publish `StateUpdate(IDLE, stop_reason="crash_recovery")`, skip interrupted Turn, mark result as interrupted in journal
- [x] 10.3 Implement `recover_strategy: "retry"`: after in-flight recovery, re-queue interrupted Turn's prompt for re-execution
- [x] 10.4 Wire `Journal.log_tool_execution()` into tool execution path in `src/agentpool/orchestrator/turn.py` — after each tool call completes, log `ToolExecutionRecord(turn_id, tool_name, args, result, status)`; ensure RunLoop passes `turn_id` to `AgentRunContext`
- [x] 10.5 Write unit tests in `tests/lifecycle/test_crash_recovery.py`: fresh start returns None, normal recovery returns `is_inflight=False`, crash during in-flight Turn returns `is_inflight=True` with events, mark_interrupted skips and publishes crash_recovery, retry re-queues prompt, tool calls logged to journal with correct turn_id/status/result

## 11. agent_pool Backdoor Deprecation & Migration

- [x] 11.1 Add `DeprecationWarning` to `MessageNode.agent_pool` property in `src/agentpool/messaging/messagenode.py` — warning message includes "HostContext" and migration guidance referencing AGENTS.md; property still returns compatibility shim
- [x] 11.2 Migrate `agent_pool` call sites in `src/agentpool/agents/base_agent.py` (~35 sites), `src/agentpool/agents/native_agent/agent.py` (~35 sites), and `src/agentpool/delegation/base_team.py` (~21 sites) to use `HostContext` from constructor injection. NOTE: The actual codebase has ~211 references across 21 files (not 25 across 4 files as stated in M1 design). This migration may need to be split into more granular sub-tasks or phased (e.g., migrate core agents first, then protocol servers, then tools).
- [x] 11.3 Migrate remaining call sites across ~21 files totaling ~211 references (not ~129 as previously estimated). Key files beyond 11.2 include `src/agentpool/messaging/messagenode.py` (~10), `src/agentpool/talk/talk.py` (~6), `src/agentpool/agents/acp_agent/acp_agent.py` (~4), `src/agentpool/agents/context.py` (~3), and other files (~15+ across models, commands, servers). Consider phasing: (1) core agents, (2) protocol servers (ACP, OpenCode, AG-UI, OpenAI API), (3) tools and remaining modules. Use `HostContext` from constructor injection.
- [x] 11.4 Write unit tests in `tests/lifecycle/test_deprecation.py`: DeprecationWarning emitted on agent_pool access, warning message contains "HostContext", shim returns correct objects, no warnings from migrated source code (`uv run pytest -W error::DeprecationWarning` on key suites)

## 12. Integration Verification

- [x] 12.1 Run full test suite: `uv run pytest` — all tests must pass without modification
- [x] 12.2 Run mypy: `uv run --no-group docs mypy src/agentpool/lifecycle/ src/agentpool/orchestrator/ src/agentpool/agents/base_agent.py` — no type errors
- [x] 12.3 Run ruff: `uv run ruff check src/agentpool/lifecycle/ src/agentpool_config/lifecycle.py` — no lint errors
- [x] 12.4 Run ruff format check: `uv run ruff format --check src/agentpool/lifecycle/` — no formatting errors
- [x] 12.5 Verify standalone run: `agentpool run assistant "Hello"` produces identical output to pre-M2 behavior with default dimensions
- [x] 12.6 Verify streaming: `async for event in agent.run_stream("prompt")` yields identical event stream as pre-M2
- [x] 12.7 Verify ACP server: `agentpool serve-acp config.yml` starts, handles requests, steer/followup through CommChannel
- [x] 12.8 Verify crash recovery: agent with `lifecycle: {journal: durable, snapshot: durable}` — start run, simulate crash mid-Turn, restart, verify `RunLoop.start()` recovers via `journal.resume()` and publishes `StateUpdate(IDLE, stop_reason="crash_recovery")`
- [x] 12.9 Verify DeprecationWarning: running existing tests shows warning for `agent_pool` access but no failures
- [x] 12.10 Verify public API unchanged: `pool.get_agent("name")`, `agent.run()`, `agent.run_stream()`, `agent.process()`, `agent.add_connection()` all work without code changes
