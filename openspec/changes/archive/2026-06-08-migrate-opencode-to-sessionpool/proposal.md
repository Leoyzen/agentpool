## Why

OpenCode Server currently maintains a dual-track architecture: the main message ingestion path routes through `SessionPool.receive_request()`, but numerous auxiliary paths (slash commands, skill commands, session initialization, summarization, MCP prompt commands, permission handling) bypass SessionPool entirely and operate directly on a shared `state.agent`. This creates state redundancy (`ServerState` duplicates SessionPool's session/agent tracking for some fields), concurrency risks (shared agent mutations without turn isolation), and maintenance burden (legacy code paths that circumvent EventBus, TurnRunner, and per-session agent isolation).

However, not all `ServerState` fields are mere "duplication." `messages` is the canonical OpenCode message store with 56+ references across routes for share, revert, fork, and load operations. `input_providers` and `pending_questions` support global listing endpoints that SessionPool does not yet replicate. Removing these without replacement APIs would break multiple endpoints.

This change is therefore split into two sequential migrations:
1. **Migration A — Route Unification**: Route all auxiliary execution through `SessionPool.receive_request()` while preserving existing `ServerState` fields.
2. **Migration B — State Consolidation**: Replace `ServerState` in-memory stores with SessionPool-native APIs (requires prerequisite API design).

## What Changes

### Migration A: Route Unification (Immediate)
- **Route all auxiliary `agent.run()` / `agent.run_stream()` calls** in OpenCode routes (`session_routes.py`, `message_routes.py`) through `SessionPool.receive_request()`
- **Delete deprecated `ServerState.get_or_create_agent()`** and `ServerState._session_agents` — all agent resolution goes through `SessionController.get_or_create_session_agent()`
- **Migrate slash command execution** (both plain and skill) to `SessionPool.run_stream()` (streaming endpoints that need event-level access for `OpenCodeStreamAdapter`). All slash commands call `agent.run_stream()` after `command.execute()` and must route through SessionPool.
- **Migrate session init** to `SessionPool.receive_request()` and **summarize** to `SessionPool.run_stream()`
- **Migrate MCP prompt command execution** to use `SessionPool.receive_request()`
- **Migrate permission handling** to use `OpenCodeInputProvider` registered on `SessionState` (not `ACPInputProvider`)
- **Preserve `_should_bypass_session_pool()` safety mechanism** — replace stack inspection with a `ContextVar` instead of removing it
- **Keep shell execution as direct passthrough** — remove dependency on `state.agent.env` but do not route through SessionPool (preserves immediate execution semantics)
- **Add feature flags** for incremental rollout of each route category

### Migration B: State Consolidation (After Prerequisite APIs)
- **Design SessionPool message history API** (`get_messages`, `append_message`, `truncate_messages`, `copy_messages`) to replace `ServerState.messages`
- **Design SessionPool global permission/question listing APIs** to replace `ServerState.input_providers` and `pending_questions`
- **Eliminate `ServerState` in-memory state** (`messages`, `session_status`, `input_providers`, `pending_questions`, `todos`, `reverted_messages`) after replacement APIs are implemented
- **Migrate SSE event streaming** to EventBus subscriber with `scope="descendants"` (after message history API provides historical replay)
- **Remove legacy fallback paths** in `BaseAgent.run_stream()` (after AG-UI audit confirms bypass is no longer needed)

## Capabilities

### New Capabilities
- `opencode-sessionpool-unification`: Unified OpenCode Server execution model where all server-side operations (commands, summaries, permissions) are orchestrated through SessionPool turns rather than ad-hoc agent invocations

### Modified Capabilities
- `sessionpool-only-execution`: Expand scope to include OpenCode Server alongside ACP Server. Add explicit requirement that OpenCode routes use SessionPool. Preserve `_should_bypass_session_pool()` as deadlock prevention, replacing stack inspection with `ContextVar`.
- `unified-session-lifecycle`: Update requirements to cover OpenCode session CRUD delegating to SessionPool `SessionController`. Add requirement for `SessionPool.list_sessions()` API. Add requirement for per-session `OpenCodeInputProvider` on `SessionState`.
- `unified-event-routing`: Update requirements for OpenCode SSE. Add requirement for EventBus replay buffer to support historical message replay. Add requirement that `ServerState.messages` is NOT removed until replacement API exists.

## Impact

- **Files affected**:
  - Migration A: `session_routes.py`, `message_routes.py`, `global_routes.py`, `state.py` (agent resolution only), `base_agent.py` (ContextVar bypass mechanism), `session_pool_integration.py` (bridge/consumer refactoring, TOCTOU fixes), `input_provider.py` (OpenCodeInputProvider refactoring), `agentpool/orchestrator/core.py` (SessionController new methods), `agentpool_config/session_pool.py` (feature flags)
  - New files in Migration A: `event_bridge.py` (OpenCodeEventBridge), `models/question_permission.py` (PendingQuestion/PendingPermission DTOs), `models/session_info.py` (SessionInfo DTO)
  - Migration B: `state.py` (full cleanup), `stream_adapter.py` (SSE EventBus-only migration)
- **API impact**: No external API changes; OpenCode protocol compatibility preserved
- **Dependencies**: Relies on existing `SessionPool`, `TurnRunner`, `EventBus`, `RunExecutor`, `OpenCodeInputProvider`
- **Breaking (internal)**: `ServerState.get_or_create_agent()` removed in Migration A; `ServerState.messages` and other dicts removed in Migration B (after replacement APIs exist)
