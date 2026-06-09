## Migration A: Route Unification

### A1. Prep and Safety Mechanisms

- [ ] A1.1 Replace **SessionPool-internal** stack inspection in `_should_bypass_session_pool()` with `ContextVar` mechanism in `BaseAgent`. Preserve AG-UI stack inspection until AG-UI audit completes (see A1.3 and B0.4).
- [ ] A1.2 Set `_bypass_session_pool` ContextVar in `TurnRunner._run_turn_unlocked()` before `agent._run_stream_once()`
- [ ] A1.3 Preserve AG-UI bypass in `_should_bypass_session_pool()` until AG-UI audit completes
- [ ] A1.4 Add category feature flags to `agentpool_config.session_pool.OpenCodeConfig`: `use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp` (default `False`). Note: `use_session_pool` (default `True`, global master switch) already exists in `OpenCodeConfig` at `agentpool_config/session_pool.py:55`; verify it is present and functional. Read values from environment variables (`AGENTPOOL_USE_SESSION_POOL_FOR_*`) when `OpenCodeConfig` is instantiated (via `default_factory` during manifest loading). Document env vars in `docs/configuration/index.md`.
- [ ] A1.5 Define generic `PendingQuestion` and `PendingPermission` Protocol types (or ABCs) in `agentpool/models/` with fields: `id`, `session_id`, `tool_name`, `content`, `created_at`. These are used by `SessionController` APIs. Also define concrete dataclasses `OpenCodePendingQuestion` and `OpenCodePendingPermission` in `agentpool_server/opencode_server/models/question_permission.py` that implement these Protocols. Re-export them via `agentpool_server/opencode_server/models/__init__.py` for route imports.
- [ ] A1.6 Add `SessionController.list_pending_questions() -> list[PendingQuestion]` and `list_pending_permissions() -> list[PendingPermission]` stub methods using the generic Protocol types
- [ ] A1.7 Update `OpenCodeInputProvider` in `input_provider.py` to use the generic `PendingQuestion`/`PendingPermission` Protocol types instead of OpenCode-specific types. Add `event_bus: EventBus` parameter to the constructor (alongside existing `state: ServerState` and `session_id: str`) for future Migration B use — do NOT remove `state` during Migration A. Keep `self.state.broadcast_event()` calls as-is during Migration A because SSE endpoints still consume from `state.event_subscribers`. The `event_bus` parameter is stored on the provider but unused until Migration B when SSE migrates to EventBus-only. Replace `self.state.pending_questions` dict access with an internal `dict[str, PendingQuestion]` stored on the provider instance. Add `get_pending_questions() -> list[PendingQuestion]` method to return all pending questions from the internal dict. Add `cancel_pending_questions() -> list[str]` method to cancel all pending questions and return their IDs (replaces `ServerState.cancel_session_pending_questions()`).
- [ ] A1.8 Add `SessionController.get_session_agent(session_id)` method that returns the per-session agent for native agents, or the shared singleton for non-native agents (with a warning log). Raises `KeyError` if the session has no associated agent. Also set `session.is_per_session_agent: bool` when creating the agent (True for native agents where `session.agent` is a unique instance, False for non-native shared singletons). Add `ServerState` shim methods: `get_session()`, `list_sessions()`, `get_session_status()` that delegate to `SessionController`. Store a direct `SessionController` reference on `ServerState` during initialization: add `session_controller: SessionController | None = field(default=None, repr=False)` to the `ServerState` dataclass. Update the production call site that constructs `ServerState` (in `server.py`) to pass the `SessionController`. Tests that construct `ServerState` without passing `session_controller` will use the default `None` and continue to work during Migration A. **These shim methods are temporary and removed in Migration B when ServerState is fully cleaned up.**
- [ ] A1.9 Verify `SessionState.is_per_session_agent` field (already exists in `agentpool/orchestrator/core.py:79`) is set to `True` by `SessionController.get_or_create_session_agent()` when creating native per-session agents. No code changes needed if already implemented; add regression test if missing.
- [ ] A1.10 Add `SessionController.list_sessions() -> list[SessionState]` stub method (returns `list(self._sessions.values())`) so shim methods in A1.8 can compile immediately
- [ ] A1.11 Add `warnings.warn("Use SessionController.get_or_create_session_agent()", DeprecationWarning)` to `ServerState.get_or_create_agent()` (already has deprecation docstring; this adds the runtime warning)
- [ ] A1.12 Replace TOCTOU `get_session() is None` + `create_session()` patterns with `SessionController.get_or_create_session()` in route handlers. **IMPORTANT**: Only replace patterns that call `session_pool.create_session()` (on the `SessionPool` instance) or `session_pool.sessions.get_or_create_session()` directly. Do NOT replace calls to `integration.create_session()` / `self.create_session()` (on `OpenCodeSessionPoolIntegration`) — these are OpenCode-specific and also start the `SessionStatusBridge` and `_event_consumer_loop`, which `SessionController.get_or_create_session()` does NOT do. The integration methods are refactored separately in A1.14.
- [ ] A1.13 Modify `SessionController.get_or_create_session()` to return `tuple[SessionState, bool]` where the bool indicates whether the session was newly created (True) or already existed (False). Update all existing call sites of `get_or_create_session()` to unpack the tuple. **IMPORTANT**: Also update `SessionPool.create_session()` (in `agentpool/orchestrator/core.py:1481`) to unpack the tuple returned by `self.sessions.get_or_create_session()` and return just the `SessionState` for backward compatibility with non-OpenCode callers (ACP, teams, base_agent, etc.).
- [ ] A1.14 Refactor `OpenCodeSessionPoolIntegration.create_session()` (and any other integration methods that start bridge/consumer) to: (1) call `SessionController.get_or_create_session()` and check the returned boolean, then (2) start `SessionStatusBridge` and `_event_consumer_loop` only if the session was newly created. Ensure bridge/consumer startup is NOT skipped during the A1.12 migration.
- [ ] A1.15 In OpenCode server routes, after calling `SessionController.get_or_create_session()`, unpack the returned tuple: `session, was_created = SessionController.get_or_create_session(...)`. Set `session.input_provider = OpenCodeInputProvider(state=state, session_id=session_id, event_bus=session_pool.event_bus)` for OpenCode sessions. The `was_created` boolean can be used by routes to conditionally run setup logic. Do NOT modify generic `SessionController.get_or_create_session()` — keep protocol-specific initialization in protocol routes.
- [ ] A1.16 Inventory all tests touching `ServerState` dicts — run `pytest --collect-only` filtered by `opencode_server`; output the test list to `tests/opencode_server_test_inventory.md` for reference during Migration A
- [ ] A1.17 Add test: `BaseAgent.run_stream()` emits `DeprecationWarning` and delegates to `SessionPool` (already implemented; this test prevents regression)
- [ ] A1.18 Define feature flag interaction logic: category flags (`use_session_pool_for_*`) are only evaluated when the global `use_session_pool` flag is `True`. If `use_session_pool=False`, all SessionPool routing is disabled regardless of category flags. Document this in `agentpool_config/session_pool.py` and `docs/configuration/index.md`.
- [ ] A1.19 Add `scope: str = "session"` as a **keyword-only** parameter after `*prompts` in `SessionPool.run_stream()` signature: `async def run_stream(self, session_id: str, *prompts: str, scope: str = "session", **kwargs: Any)`. Pass `scope` through to `self.event_bus.subscribe(session_id, scope=scope)`. This allows streaming endpoints that invoke subagents to call `run_stream(session_id, "prompt", scope="descendants")` without breaking existing call sites. Update all existing call sites of `run_stream()` to use the default (no code change needed for backward compatibility).

### A2. Fix Pre-Existing Bugs in Core Routes

- [ ] A2.1 Fix shared agent `_input_provider` mutation in `session_pool_integration.py` (both `ensure_session` at line 162 and `_create_and_persist_session` at line 250) and `message_routes.py:411`. Store input provider on `SessionState` only and ensure per-session agents receive their own input provider via `SessionController`.
- [ ] A2.2 Fix model switching in `_process_message_locked`: obtain per-session agent from `SessionController.get_or_create_session_agent(session_id)` and call `set_model()` on it directly
- [ ] A2.3 Fix orphaned `OpenCodeStreamAdapter` in `_process_message_locked`: the adapter is created at line 368 but neither `process_stream()` nor `convert_event()` is ever called in that function, so `adapter.finalize()` (line 541) produces a `StepFinishPart` with zero tokens. The background `_event_consumer_loop` uses its own separate `EventProcessorContext` and adapter. Fix: connect the adapter in `_process_message_locked` to the event flow by either (a) passing it to the event consumer loop so the same adapter instance receives events, or (b) calling `adapter.convert_event()` in the consumer loop for events belonging to this session. After the fix, `run_handle.complete_event.wait()` guarantees the turn has finished before `adapter.finalize()` is called. Add test verifying adapter receives all events before finalize and that finalize produces non-zero tokens.
- [ ] A2.4 Add route-level `state.get_session_lock(session_id)` for ALL endpoints that perform multi-phase operations (stream + post-process): `summarize_session`, `_execute_slashed_command`, `_execute_skill_command`. These endpoints call `SessionPool.run_stream()` and then perform post-stream work (e.g., `compact_conversation`, `state.broadcast_event()`). Without route-level locks, concurrent requests could interleave. **Lock ordering rule**: route-level lock is acquired FIRST, then `SessionPool.run_stream()` acquires `turn_lock` internally. This ordering is safe because route-level locks are per-session and `turn_lock` is also per-session — a single thread of execution holds both, so no circular wait can occur. Evaluate removal only for single-phase endpoints (e.g., `send_message` which is fully handled by SessionPool). Document the decision in `docs/opencode-server/locks.md`.
- [ ] A2.5 Remove dead `agent.session_id` fallback in `StreamEventEmitter._emit()`: `_emit()` should read `session_id` from `run_ctx.session_id` directly and not check `getattr(self._context.agent, "session_id", None)` first. Add test verifying `_emit()` uses `run_ctx.session_id`.

### A3. Migrate Streaming Endpoints (Need Event-Level Access)

- [ ] A3.1 Migrate `summarize_session()` to use `SessionPool.run_stream()` (not `receive_request()`) behind `use_session_pool_for_summarize` flag — endpoint needs `PartStartEvent`, `PartDeltaEvent`, `StreamCompleteEvent` to build text parts and extract token usage. Turn isolation is handled internally by SessionPool.
- [ ] A3.2 Add post-stream cleanup for `summarize_session()`: after `SessionPool.run_stream()` completes (async for loop finishes), obtain the per-session agent via `SessionController.get_or_create_session_agent(session_id)`, get `agent.conversation`, and call `compact_conversation(pipeline, agent.conversation)` in a `finally` block or after loop exit. Add test verifying compact_conversation runs after stream completion.
- [ ] A3.3 Migrate `_execute_slashed_command()` to use `SessionPool.run_stream(session_id, scope="descendants", ...)` behind `use_session_pool_for_commands` flag — plain slash commands call `command.execute()` and then `agent.run_stream()` to process the loaded skill context. The entire function must route through SessionPool. Use `scope="descendants"` because slash commands may invoke subagent tools that create child sessions; child session events must be visible in the parent stream. Turn isolation is handled internally by SessionPool.
- [ ] A3.4 Migrate `_execute_skill_command()` to use `SessionPool.run_stream(session_id, scope="descendants", ...)` behind `use_session_pool_for_skills` flag — same adapter pattern as slash skill commands. Use `scope="descendants"` because skill commands may invoke subagent tools that create child sessions. Turn isolation is handled internally by SessionPool.
- [ ] A3.5 Implement `OpenCodeEventBridge` in `agentpool_server/opencode_server/event_bridge.py`: instantiate one bridge per `ServerState` in `ServerState.__post_init__()` only when `self.session_controller is not None` (skip bridge creation in tests that don't pass a controller). Pass `self` and `self.session_controller` to the bridge constructor. Routes access it via `state.event_bridge.broadcast_event(event)` instead of `state.broadcast_event(event)`. The bridge method: (1) calls the original `state.broadcast_event(event)` to maintain backward compatibility with `state.event_subscribers` (which SSE still consumes from during Migration A), (2) converts OpenCode protocol events (`MessageUpdatedEvent`, `PartUpdatedEvent`, `SessionStatusEvent`, etc.) to `RichAgentStreamEvent` wrappers, (3) republishes them to EventBus via `session_pool.event_bus.publish(session_id, wrapped_event)`. During Migration A, the bridge ensures EventBus contains both agent events and protocol events *in preparation for* Migration B (when SSE will migrate to EventBus-only). SSE subscribers continue to receive events through `state.event_subscribers`, not EventBus. Add test verifying bridge republishes protocol events to EventBus.
- [ ] A3.6 Add behavior parity tests for streaming endpoints comparing legacy vs SessionPool paths

### A4. Migrate Fire-and-Forget Routes (Agent-Mediated)

- [ ] A4.1 Migrate `init_session()` to use `SessionPool.receive_request()` behind `use_session_pool_for_init` flag
- [ ] A4.2 Handle background task lifecycle for `init_session` — `receive_request()` returns a `RunHandle` which is already stored in `SessionController`; ensure `abort_session` can cancel it via `SessionPool.cancel_run(run_id)`
- [ ] A4.3 Migrate MCP prompt command execution to use `SessionPool.receive_request()` behind `use_session_pool_for_mcp` flag

### A5. Migrate Permissions and Questions

- [ ] A5.1 Move `OpenCodeInputProvider` registration from `ServerState.input_providers[session_id]` to `SessionState.input_provider`
- [ ] A5.2 Update permission routes to read from `SessionState` via `SessionController`
- [ ] A5.3 Update question routes to read from `SessionState` via `SessionController`
- [ ] A5.4 Implement `SessionController.list_pending_questions()` for global question listing endpoint
- [ ] A5.5 Implement `SessionController.list_pending_permissions()` for global permission listing endpoint
- [ ] A5.6 Ensure permission resolution uses fast-path `asyncio.Future` resolution: HTTP POST endpoint sets `Future` on `OpenCodeInputProvider`, tool awaits the same `Future` — no turn queue blocking
- [ ] A5.7 Update permission tests to mock `SessionState` instead of `ServerState.input_providers`
- [ ] A5.8 Implement `SessionController.cancel_all_pending_questions()` that iterates all sessions' `input_provider`s and calls `cancel_pending_questions()` on each. Migrate `global_routes.py:292` (SSE disconnect handler) to use `SessionController.cancel_all_pending_questions()` instead of `state.cancel_all_pending_questions()`.

### A6. Migrate Shell Execution (Direct Passthrough)

- [ ] A6.1 Create standalone `Env`/`ProcessManager` for shell execution (independent of `state.agent.env`)
- [ ] A6.2 Replace `state.agent.env.execute_command()` in shell route with standalone env
- [ ] A6.3 Preserve immediate execution semantics — shell does NOT create a SessionPool turn
- [ ] A6.4 Update shell route tests to assert direct execution (not LLM-mediated)

### A7. Migrate Session CRUD and Cleanup

- [ ] A7.1 Migrate `get_or_load_session()` to use `SessionController.get_or_create_session_agent()` instead of `ServerState.get_or_create_agent()`
- [ ] A7.2 Define `SessionInfo` DTO in `agentpool_server/opencode_server/models/session_info.py` with fields: `session_id: str`, `status: str`, `created_at: float`, `last_activity: float`, `message_count: int`. Re-export via `agentpool_server/opencode_server/models/__init__.py`. Update the existing `SessionController.list_sessions()` stub (added in A1.10) to return `list[SessionInfo]` with conversion logic from `SessionState` to `SessionInfo`. **For `message_count` during Migration A**: read from `ServerState.messages[session_id]` since `SessionState` does not yet track message history (this is temporary until Migration B implements `SessionPool.get_messages()`). Update `ServerState.list_sessions()` shim (added in A1.8) to delegate to the updated method. Migrate `list_sessions` route to use the DTO.
- [ ] A7.3 Use `SessionController.get_session_agent(session_id)` (added in A1.8) in route handlers. For non-native (shared) agents, the method returns the shared instance with a warning log. Raises `KeyError` if the session has no associated agent (e.g., session was created but no turn has run yet).
- [ ] A7.4 Migrate `abort_session` to use `SessionController.get_session_agent(session_id)` and call `interrupt()` for native agents only. Use `session.is_per_session_agent` (True for native agents with dedicated instances, False for non-native shared singletons) to determine whether `interrupt()` is safe. For non-native shared agents (`session.is_per_session_agent == False`), cancel the RunHandle without calling `interrupt()` (to avoid killing all sessions using that agent). Obtain the active `run_id` from `session.current_run_id` via `SessionController.get_session(session_id)`, then cancel the associated `RunHandle` via `SessionPool.cancel_run(run_id)`.
- [ ] A7.5 Remove `ServerState._session_agents` cache entirely
- [ ] A7.6 Remove deprecated `ServerState.get_or_create_agent()` entirely
- [ ] A7.7 Audit `ServerState` for any remaining direct agent references; replace with SessionPool delegation

### A8. Test Updates for Migration A

- [ ] A8.1 Update message route tests — mock `SessionController` instead of `ServerState` for session operations
- [ ] A8.2 Add test: slash command uses `SessionPool.run_stream()` and `OpenCodeStreamAdapter` produces correct events
- [ ] A8.3 Add test: skill command routes through SessionPool behind feature flag
- [ ] A8.4 Add test: init session creates `RunHandle` stored in `SessionController._runs` for cancellation
- [ ] A8.5 Add test: `summarize_session()` calls `compact_conversation()` in a `finally` block after `SessionPool.run_stream()` completes
- [ ] A8.6 Add test: MCP prompt routes through `SessionController.receive_request()`
- [ ] A8.7 Add test: shell execution uses standalone env, not `state.agent.env`
- [ ] A8.8 Add test: permission handling uses `OpenCodeInputProvider` on `SessionState`
- [ ] A8.9 Add test: global question listing queries `SessionController.list_pending_questions()`
- [ ] A8.10 Add test: `BaseAgent.run_stream()` does not bypass SessionPool for regular callers
- [ ] A8.11 Add test: `BaseAgent.run_stream()` bypasses SessionPool when `ContextVar` is set (deadlock prevention)
- [ ] A8.12 Add test: model switching targets per-session agent, not shared agent
- [ ] A8.13 Add test: `ensure_session` does not mutate shared agent `_input_provider`
- [ ] A8.14 Run full OpenCode integration test suite and fix failures
- [ ] A8.15 Run `pytest tests/servers/opencode_server/` — all tests pass
- [ ] A8.16 Run `mypy src/agentpool_server/opencode_server/` — no type errors
- [ ] A8.17 Run `ruff check src/agentpool_server/opencode_server/` — no lint errors

### A9. Verification for Migration A

- [ ] A9.1 Manual end-to-end test: OpenCode CLI connects, sends message, receives SSE events
- [ ] A9.2 Manual end-to-end test: slash command executes correctly with feature flag enabled
- [ ] A9.3 Manual end-to-end test: permission request pauses turn and resolves correctly
- [ ] A9.4 Manual end-to-end test: shell command returns immediately (not LLM-mediated)
- [ ] A9.5 Performance benchmark: compare SSE event latency before/after Migration A
- [ ] A9.6 Concurrency test: two sessions run slash commands simultaneously without state corruption
- [ ] A9.7 Create rollback branch from main before merging Migration A

## Migration B: State Consolidation (Blocked on Prerequisite APIs)

### B0. Prerequisite Design (Concrete Deliverables Required)

**Goal**: Produce design documents that B1-B7 can implement against. Each B0 task must produce a concrete artifact (API signatures, sequence diagrams, or decision records).

- [x] B0.1 Design SessionPool message history API: produce `docs/design/message-history-api.md` with exact method signatures (`get_messages(session_id) -> list[ChatMessage]`, `append_message(session_id, message)`, `truncate_messages(session_id, before_message_id)`, `copy_messages(from_session_id, to_session_id)`), error handling (KeyError for missing session), persistence integration points, and a sequence diagram for `copy_messages()`.
- [x] B0.2 Design SessionPool global permission/question listing APIs: produce `docs/design/permission-question-api.md` with exact signatures using the generic `PendingQuestion`/`PendingPermission` types from A1.5, filtering options (by session_id, by tool_name), and pagination strategy.
- [x] B0.3 Design EventBus replay buffer API: produce `docs/design/eventbus-replay.md` with buffer data structure (ring buffer vs. linked list), event retention policy (time-based vs. count-based), subscriber replay protocol (how new subscribers receive historical events before live events), and memory bounds.
- [x] B0.4 AG-UI audit: verify AG-UI server routes do not depend on `_should_bypass_session_pool()`. Produce `docs/audit/agui-bypass-audit.md` documenting: (a) all AG-UI routes that call `agent.run_stream()`, (b) whether each route sets the ContextVar or relies on stack inspection, (c) pass/fail verdict for each route.
- [x] B0.5 OpenCode client protocol compatibility audit: verify TUI/Desktop handles async permission changes and event ordering. Produce `docs/audit/opencode-client-audit.md` with test scenarios (permission granted during streaming, event ordering after reconnect, SSE replay behavior).
- [x] B0.6 Decide fate of `todos` endpoints: produce `docs/decisions/todos-endpoints.md` with two options analyzed (remove entirely vs. persist via StorageProvider), including migration effort, backward compatibility impact, and recommendation.
- [ ] B0.7 Contingency: if AG-UI audit reveals dependency on bypass, update `docs/audit/agui-bypass-audit.md` with mitigation plan and update spec to document AG-UI bypass as permanent.
- [ ] B0.8 Contingency: if OpenCode client audit reveals event ordering issues, add mitigation tasks to `docs/audit/opencode-client-audit.md` (e.g., event sequencing buffer, client-side reordering).

### B1. Message History API Implementation

- [ ] B1.1 Implement `SessionPool.get_messages(session_id) -> list[ChatMessage]`
- [ ] B1.2 Implement `SessionPool.append_message(session_id, message)`
- [ ] B1.3 Implement `SessionPool.truncate_messages(session_id, before_message_id)`
- [ ] B1.4 Implement `SessionPool.copy_messages(from_session_id, to_session_id)`
- [ ] B1.5 Add persistence integration: messages stored via StorageProvider (SQL, Zed, etc.)
- [ ] B1.6 Add caching layer for frequently accessed message histories

### B2. EventBus Replay Buffer

- [ ] B2.1 Add bounded replay buffer to `EventBus` or `SessionState`
- [ ] B2.2 Implement subscriber replay protocol: new subscribers receive last N events before live events
- [ ] B2.3 Add `eventbus_replay_buffer_size: int = 100` field to `agentpool_config.session_pool.OpenCodeConfig`. Use this value when creating the EventBus replay buffer in `EventBus` or `SessionState`.
- [ ] B2.4 Add tests for replay buffer correctness and bounds

### B3. SSE Migration to EventBus-Only

- [ ] B3.1 Update SSE endpoint to create EventBus subscriber with `scope="descendants"`
- [ ] B3.2 Implement historical message replay for new SSE subscribers from replay buffer
- [ ] B3.3 Verify `OpenCodeEventAdapter` converts `RichAgentStreamEvent` to OpenCode protocol events correctly
- [ ] B3.4 Remove manual `broadcast_event()` path where redundant
- [ ] B3.5 Verify child session events propagate to parent SSE subscribers
- [ ] B3.6 Add SSE event ordering tests: `PartStartEvent` -> `PartDeltaEvent` -> `PartEndEvent`

### B4. State Cleanup

- [ ] B4.1 Migrate `share_session()` to use SessionPool message history API instead of `ServerState.messages`
- [ ] B4.2 Migrate `revert_session()` to use SessionPool message history API instead of `ServerState.messages`
- [ ] B4.3 Migrate `get_or_load_session()` to use SessionPool message history API
- [ ] B4.4 Migrate session fork to use `SessionPool.copy_messages()`
- [ ] B4.5 Remove `ServerState.messages` dictionary (messages accessed via direct dict operations like `state.messages[session_id].append(...)`, not through a dedicated method)
- [ ] B4.6 Remove `ServerState.reverted_messages` dictionary
- [ ] B4.7 Remove `ServerState.session_status` dictionary (after confirming SessionPool status API covers all use cases)
- [ ] B4.8 Remove `ServerState.pending_questions` dictionary (after global API is verified)
- [ ] B4.9 Remove `ServerState.todos` dictionary (per B0.6 decision: remove endpoints or persist via StorageProvider)
- [ ] B4.10 Remove `ServerState._active_message_tasks` dictionary (replaced by `SessionController._runs`)
- [ ] B4.11 Audit `ServerState` for any remaining in-memory state that duplicates `SessionController`; remove or delegate
- [ ] B4.12 Remove temporary `ServerState` shim methods added in A1.8 (`get_session()`, `list_sessions()`, `get_session_status()`); all callers must use `SessionController` directly

### B5. BaseAgent Final Cleanup

- [ ] B5.1 Remove AG-UI bypass from `_should_bypass_session_pool()` **only if B0.4 audit passes** (if audit fails, skip this task and document AG-UI bypass as permanent per B0.7)
- [ ] B5.2 Remove legacy fallback path in `BaseAgent.run_stream()` that skips SessionPool
- [ ] B5.3 Verify no internal code path still calls `agent.run_stream()` directly (grep for `agent\.run_stream\(` in `src/agentpool_server/opencode_server/` — exclude `session_pool.run_stream()` which is the correct call after migration)
- [ ] B5.4 Verify no internal code path still calls `agent.run()` directly (grep for `agent\.run\(` in `src/agentpool_server/opencode_server/` — exclude `session_pool.run()` and stdlib calls like `asyncio.run()` or `subprocess.run()`)

### B6. Tests for Migration B

- [ ] B6.1 Add integration tests for message history API: get, append, truncate, copy
- [ ] B6.2 Add integration tests for EventBus replay buffer
- [ ] B6.3 Add integration tests for share/revert using new message history API
- [ ] B6.4 Add integration tests for SSE replay behavior
- [ ] B6.5 Run full OpenCode integration test suite — all tests pass
- [ ] B6.6 Run `mypy src/agentpool_server/opencode_server/` — no type errors
- [ ] B6.7 Run `ruff check src/agentpool_server/opencode_server/` — no lint errors

### B7. Final Verification for Migration B

- [ ] B7.1 Manual end-to-end test: SSE reconnect receives historical messages from replay buffer
- [ ] B7.2 Manual end-to-end test: share session works with SessionPool message history API
- [ ] B7.3 Manual end-to-end test: revert session works with SessionPool message history API
- [ ] B7.4 Performance benchmark: compare message history API latency vs old `ServerState.messages` access
- [ ] B7.5 Concurrency test: multiple sessions share/revert simultaneously without corruption
- [ ] B7.6 Create rollback branch before merging Migration B
