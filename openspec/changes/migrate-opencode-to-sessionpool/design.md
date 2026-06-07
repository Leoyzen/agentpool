## Context

OpenCode Server (`src/agentpool_server/opencode_server/`) is the TUI/desktop protocol server that bridges OpenCode clients (CLI, Zed extension) with AgentPool agents. Currently it has a hybrid architecture:

- **Main message path** (`send_message`, `send_message_async`): routes through `SessionPool.receive_request()` → EventBus → TurnRunner. This is the SessionPool-native path.
- **Auxiliary paths** (skill commands, session init, summarize, MCP prompts, plain slash commands): call `state.agent.run()` or `state.agent.run_stream()` directly, bypassing SessionPool. These use the deprecated `ServerState.get_or_create_agent()` which returns a shared agent instance, causing state corruption under concurrency. Other auxiliary paths (permission handling, list sessions) do not invoke the agent but still read from `ServerState` fields that duplicate SessionPool tracking.
- **State fields**: `ServerState` maintains `messages` (canonical message history, 56+ refs), `session_status`, `input_providers`, `pending_questions`, `todos`, `reverted_messages`. Some of these are dual-state (session_status duplicates SessionController), but `messages` is the primary store with no SessionPool equivalent.
- **Safety mechanism**: `BaseAgent._should_bypass_session_pool()` uses stack inspection to prevent deadlocks when SessionPool internals call `agent.run_stream()`. It also preserves AG-UI's direct streaming path. There is no OpenCode-specific inspection; the check covers AG-UI modules and SessionPool internal turn functions only.
- **Pre-existing bugs**: `ensure_session` in `session_pool_integration.py` mutates `target_agent._input_provider` on the shared agent (affecting all sessions). The main message path (`message_routes.py:411`) performs the same mutation: `agent._input_provider = input_provider` on the shared agent. Model switching in `_process_message_locked` switches the shared agent's model, but SessionPool uses a per-session agent for native agents, so the switch has no effect.

This design document specifies how to collapse all execution paths into the SessionPool-native model in two sequential migrations, while preserving safety mechanisms, fixing pre-existing bugs, and avoiding breaking changes.

## Goals / Non-Goals

**Goals (Migration A — Route Unification):**
- All OpenCode route handlers that execute agents use SessionPool as the exclusive execution entry point
- Remove `ServerState.get_or_create_agent()`, `_session_agents`, and all in-memory agent caching
- Migrate auxiliary operations (slash commands, skill commands, init, summarize, permissions) to SessionPool turns
- Streaming endpoints that need event-level access use `SessionPool.run_stream()` or EventBus subscription, not `receive_request()`
- Replace `_should_bypass_session_pool()` stack inspection with a `ContextVar` mechanism
- Keep shell execution as direct passthrough (non-LLM-mediated)
- Fix `ensure_session` shared agent mutation — store `input_provider` on `SessionState` only
- Fix model switching to target the per-session agent
- Add feature flags for incremental rollout

**Goals (Migration B — State Consolidation):**
- Design and implement SessionPool message history API to replace `ServerState.messages`
- Design and implement global permission/question listing APIs
- Eliminate `ServerState` in-memory stores after replacement APIs exist
- Migrate SSE to EventBus-only with replay buffer support
- Remove legacy bypass paths after AG-UI audit

**Non-Goals:**
- Changing the OpenCode wire protocol or client-visible API
- Introducing new user-facing features
- Migrating AG-UI, OpenAI API, or A2A servers (out of scope)
- Rewriting core SessionPool, TurnRunner, or EventBus infrastructure
- Changing how native vs non-native agents execute internally
- Migrating shell execution to LLM-mediated tool calls
- Fixing all pre-existing bugs unrelated to SessionPool migration

## Decisions

### Decision 1: Two sequential migrations instead of one atomic change
**Rationale**: A single atomic migration touching 10+ files with 56+ message references is infeasible. Splitting into (A) route unification and (B) state consolidation allows incremental delivery, easier rollback, and validation at each milestone.
**Alternative considered**: Single PR with all changes. Rejected due to blast radius and difficulty of debugging failures across all subsystems simultaneously.

### Decision 2: Streaming endpoints use `SessionPool.run_stream()` with turn lock, not `receive_request()`
**Rationale**: `summarize_session`, `_execute_slashed_command`, and `_execute_skill_command` need event-level access to incrementally build `MessageWithParts` and broadcast `PartDeltaEvent`s. `receive_request()` is fire-and-forget and returns a `RunHandle` (or `None`), denying the caller access to the event stream. For these endpoints, the correct entry point is `SessionPool.run_stream()` (which yields events from an EventBus subscription) or direct EventBus subscription. **Turn isolation is already enforced internally by `SessionPool.run_stream()`**; the caller should NOT acquire `SessionState.turn_lock` manually.
**Alternative considered**: Use `receive_request()` for all endpoints. Rejected because it would break event processing and response construction for streaming endpoints.
**Alternative considered**: Allow streaming endpoints to bypass turn isolation. Rejected because PydanticAI agents are not safe for concurrent runs and concurrent turns would corrupt agent state.

### Decision 3: Slash commands become SessionPool streaming turns
**Rationale**: `_execute_slashed_command()` calls `agent.run_stream()` for ALL slash commands (both plain and skill) after executing `command.execute()`. Similarly, `_execute_skill_command()` streams via `agent.run_stream()`. Both MUST migrate to `SessionPool.run_stream()` for per-session agent isolation. There is no "non-streaming" slash command path in the current codebase.
**Alternative considered**: Route only skill commands through SessionPool and leave plain commands direct. Rejected because plain commands also invoke `agent.run_stream()` and would continue using the shared agent, perpetuating the state corruption bug.

### Decision 4: Shell execution remains direct passthrough
**Rationale**: `session_routes.py` directly calls `agent.env.execute_command()`. Routing through the `bash` tool within a SessionPool turn would fundamentally change semantics — shell commands become LLM-mediated operations with latency and potential refusal. Users expect immediate deterministic execution.
**Alternative considered**: Route through SessionPool tool framework. Rejected because it is a UX-breaking product change, not an architecture refactor.

### Decision 5: Permission handling uses `OpenCodeInputProvider` on `SessionState`
**Rationale**: `ACPInputProvider` uses ACP protocol event schemas that do not match OpenCode's `PermissionRequestEvent`/`PermissionReplyEvent`. OpenCode server already has `OpenCodeInputProvider`. The migration moves it from `ServerState.input_providers[session_id]` to `SessionState.input_provider`.
**Alternative considered**: Use `ACPInputProvider` and adapt events. Rejected because it would break OpenCode client's permission UI.

### Decision 6: Session CRUD delegates to `SessionController`
**Rationale**: `ServerState.sessions` and `ServerState.session_status` are parallel in-memory caches. `SessionController` already maintains `SessionState` objects with the same metadata. OpenCode session routes should call `SessionController.get_or_create_session()`, `SessionPool.close_session()`, and read session status from `SessionController`.
**Alternative considered**: Keep `ServerState.sessions` as a read-only cache. Rejected because it perpetuates dual-state and risks stale data.

### Decision 7: SSE event streaming uses EventBus subscriber with replay buffer
**Rationale**: Currently OpenCode SSE produces events from `ServerState.messages[session_id]`. This provides historical message replay for new SSE subscribers. EventBus alone does not buffer events for late subscribers. The migration adds a replay buffer (last N events) to EventBus or `SessionState`. 
**Mitigation for bounded buffer**: For sessions with >N messages, new subscribers miss events older than the buffer. During Migration A, `ServerState.messages` is retained as fallback for full history. During Migration B, the message history API (`SessionPool.get_messages()`) provides full historical replay, making the bounded buffer a latency optimization (recent events from buffer) with full history from the API.
**Alternative considered**: Remove historical replay. Rejected because OpenCode clients rely on receiving message history when reconnecting.

### Decision 8: Replace SessionPool-internal stack inspection with ContextVar; keep AG-UI inspection during Migration A
**Rationale**: `_should_bypass_session_pool()` serves two purposes: (1) deadlock prevention when `TurnRunner._run_turn_unlocked()` internally calls `agent.run_stream()`, and (2) AG-UI direct streaming bypass. Purpose (1) involves detecting SessionPool-internal frames (`_run_turn_unlocked`, `run_loop`, `run_turn`). Replacing this with a `ContextVar` set by TurnRunner before calling `agent._run_stream_once()` handles purpose (1) cleanly. 

**Why `_current_run_ctx_var` is insufficient**: `TurnRunner` already sets `_current_run_ctx_var` for RunContext propagation. However, this ContextVar carries the `RunContext` object (or `None`), not a boolean bypass flag. Reusing it would require `_should_bypass_session_pool()` to check `if _current_run_ctx_var.get() is not None`, but this check would incorrectly return `True` for ALL callers that set a RunContext — including standalone agent callers that are NOT inside SessionPool internals. A dedicated `_bypass_session_pool` ContextVar (boolean) is required to distinguish SessionPool-internal calls (bypass=True) from external calls (bypass=False or unset).

For purpose (2), AG-UI still needs bypass during Migration A until the B0.4 audit confirms SessionPool compatibility. We use a hybrid approach: ContextVar for TurnRunner, stack inspection for AG-UI during Migration A. In Migration B (B5.1), AG-UI inspection is removed post-audit.
**Alternative considered**: Replace ALL stack inspection with ContextVar. Rejected because AG-UI doesn't set the ContextVar and would break.
**Alternative considered**: Keep all stack inspection. Rejected because SessionPool-internal frame detection is fragile and couples BaseAgent to orchestrator internals.

### Decision 9: Feature flags for incremental rollout
**Rationale**: Each route category (slash commands, skill commands, init, summarize, MCP prompts) gets a startup-time configuration flag on `agentpool_config.session_pool.OpenCodeConfig` (e.g., `use_session_pool_for_commands: bool`). Flags default to `False` and are read from environment variables (`AGENTPOOL_USE_SESSION_POOL_FOR_COMMANDS`, etc.) at server initialization. Note: `OpenCodeConfig` is frozen (`ConfigDict(frozen=True)`), so flags are set once at startup and require restart to change. This enables gradual enablement in staging and A/B comparison across deployments.
**Alternative considered**: Atomic migration with branch rollback. Rejected because partial failures would require reverting all progress.

### Decision 10: Post-stream cleanup via `finally` block for streaming endpoints
**Rationale**: `summarize_session` needs to call `compact_conversation()` after the stream completes. Streaming endpoints use `SessionPool.run_stream()` which yields events but does not expose a `RunHandle`. The cleanup is performed in a `finally` block after the `async for` loop over `run_stream()` completes, or by obtaining the per-session agent from `SessionController` after the stream and calling `compact_conversation()` on its conversation.

### Decision 11: Fix `ensure_session` shared agent mutation
**Rationale**: `session_pool_integration.py` sets `target_agent._input_provider = input_provider` on the shared agent, corrupting state for all sessions. The fix is to store the input provider on `SessionState` only and ensure the per-session agent (via `SessionController.get_or_create_session_agent()`) receives its own input provider.
**Alternative considered**: Leave as pre-existing bug. Rejected because the migration actively exercises this code path and would make the bug more severe.

### Decision 12: Model switching targets per-session agent via SessionPool
**Rationale**: `_process_message_locked` currently switches the shared agent's model, but SessionPool uses a per-session agent for native agents. The fix is to obtain the per-session agent via `SessionController.get_or_create_session_agent(session_id)` and call `set_model()` on it directly.
**Alternative considered**: Pass the desired model as a parameter to `SessionPool.receive_request()`. Rejected because it would require adding a new parameter to `receive_request()` and doesn't match existing API patterns.
**Alternative considered**: Leave as pre-existing bug. Rejected because it's in the critical message path and the migration must not regress behavior.

## Risks / Trade-offs

- **[Risk] Performance regression from EventBus subscription overhead for SSE** → **Mitigation**: EventBus is already used by ACP server; measured overhead is negligible. Subscriber creation is lazy.
- **[Risk] Breaking tests that rely on `ServerState.get_or_create_agent()`** → **Mitigation**: Search and update all test references. Add shim methods that delegate to `SessionPool` during Migration A.
- **[Risk] Slash command behavior change when injected as "asap" vs direct run** → **Mitigation**: "asap" drains before the next model request. Commands that need post-turn execution use `priority="when_idle"`. Add behavior parity tests.
- **[Risk] Message history API design delays Migration B** → **Mitigation**: Migration A delivers value independently. Migration B is blocked only on API design.
- **[Risk] Non-native agents share instances across sessions** → **Mitigation**: Document limitation. Per-session non-native agent support is a follow-up change.
- **[Risk] Double-locking: route-level lock + turn_lock** → **Mitigation**: Evaluate removing route-level `state.get_session_lock(session_id)` once routes are pure SessionPool delegates. Document decision.
- **[Trade-off] ServerState becomes thinner** → `ServerState` will lose agent resolution and some state fields. This is intentional.

## Migration Plan

### Migration A: Route Unification

1. **Phase A1 (Prep)**: Replace `_should_bypass_session_pool()` with `ContextVar`. Verify `input_provider` on `SessionState` is populated correctly (already present in `core.py:86`). Add `SessionController.list_sessions()`, `list_pending_questions()`, `list_pending_permissions()`. Add feature flags on `agentpool_config.session_pool.OpenCodeConfig`. Add shim methods on `ServerState`. Mark `get_or_create_agent()` deprecated.
2. **Phase A2 (Core Routes)**: Verify `send_message` and `send_message_async` already use `SessionPool.receive_request()`. Fix `OpenCodeStreamAdapter` finalization bug. Fix model switching to target per-session agent. Fix `ensure_session` shared agent mutation.
3. **Phase A3 (Streaming Routes)**: Migrate `summarize_session`, `_execute_slashed_command`, `_execute_skill_command` to use `SessionPool.run_stream()` or EventBus subscription (not `receive_request()`). These endpoints need event-level access.
4. **Phase A4 (Fire-and-Forget Routes)**: Migrate `init_session`, MCP prompt commands to use `SessionPool.receive_request()` behind feature flags. Handle background `RunHandle` lifecycle via `SessionController._runs`.
5. **Phase A5 (Permissions)**: Migrate permission handling to `OpenCodeInputProvider` on `SessionState`. Add fast-path Future resolution (HTTP POST sets `asyncio.Future` on `OpenCodeInputProvider`, SSE endpoint awaits it). Update global listing endpoints to query `SessionController`.
6. **Phase A6 (Shell)**: Replace `state.agent.env.execute_command()` with standalone `Env`/`ProcessManager`. Keep direct execution semantics.
7. **Phase A7 (Session CRUD)**: Migrate `list_sessions`, `abort_session`, `get_or_load_session` to use `SessionController`. For `abort_session`, expose per-session agent from `SessionController` for `interrupt()` calls.
8. **Phase A8 (Cleanup)**: Remove `ServerState._session_agents`, `get_or_create_agent()`. Remove route-level locks if redundant.
9. **Phase A9 (Tests)**: Update all OpenCode server tests. Add behavior parity tests. Add integration tests for each migrated endpoint.
10. **Phase A10 (Verification)**: Run full integration test suite. Manual end-to-end tests. Performance benchmarks. Concurrency tests.

### Migration B: State Consolidation (Blocked on Prerequisite APIs)

1. **Phase B0 (Prerequisite Design)**: Design message history API. Design global permission/question listing APIs. Design EventBus replay buffer. AG-UI audit. OpenCode client compatibility audit. Decide fate of `todos` endpoints. Contingency planning for audit failures.
2. **Phase B1 (Message History API)**: Implement `SessionPool.get_messages()`, `append_message()`, `truncate_messages()`, `copy_messages()`.
3. **Phase B2 (EventBus Replay)**: Add bounded replay buffer. Implement subscriber replay protocol.
4. **Phase B3 (SSE Migration)**: Update SSE to use EventBus + replay. Verify `OpenCodeEventAdapter`.
5. **Phase B4 (State Cleanup)**: Migrate `share_session()`, `revert_session()`, `get_or_load_session()` to message history API. Remove `messages`, `reverted_messages`, `session_status`, `input_providers`, `pending_questions`, `todos` from `ServerState`.
6. **Phase B5 (BaseAgent Final Cleanup)**: Remove AG-UI bypass post-audit. Remove legacy fallback paths.
7. **Phase B6 (Tests)**: Integration tests for message history API, replay buffer, SSE.
8. **Phase B7 (Verification)**: Manual end-to-end tests. Performance benchmarks.

## Open Questions

- Should `ServerState` be renamed to `OpenCodeServerContext` after it loses agent resolution and state fields?
- Do slash commands that spawn background tasks (e.g., `/ralph-loop`, `/ulw-loop`) need special handling beyond "asap" injection? **→ Decision: Background-task commands use `priority="when_idle"` instead of "asap" so they execute after the current turn completes, avoiding interference with active model requests. Commands that need immediate results (like `/git status`) use `priority="asap"`.**
- What is the maximum replay buffer size for EventBus SSE subscribers? **→ Decision: Default 100 events, configurable via `agentpool_config.session_pool.OpenCodeConfig.eventbus_replay_buffer_size`. Bounded to prevent unbounded memory growth.**
- Should route-level `state.get_session_lock(session_id)` be removed once routes are pure SessionPool delegates? **→ Decision: Keep during Migration A; add in A2.4 for multi-phase endpoints (stream + post-process). Evaluate removal for single-phase endpoints after route migration is complete. Route-level locks may be redundant once SessionPool.turn_lock handles all synchronization, but removing them prematurely risks race conditions in legacy fallback paths.**
