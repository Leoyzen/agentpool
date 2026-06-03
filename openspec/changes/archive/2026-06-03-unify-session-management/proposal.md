## Why

Session lifecycle management is currently split between `BaseAgent` (which generates its own `session_id` in `run_stream()`) and `SessionPool` (which only tracks top-level sessions from protocol handlers). This split causes child sessions (created by `BackgroundTaskProvider` via `AgentContext.create_child_session()`) to be invisible to the `EventBus`, so their events never reach protocol subscribers like the ACP client. Unifying session creation under `SessionPool` fixes this architectural gap and enables proper event routing for all sessions.

## What Changes

- **SessionPool becomes the single authority for all session creation** — both top-level sessions (from protocol handlers) and child sessions (from subagent delegation) go through `SessionPool.create_session(parent_session_id=...)`.
- **BaseAgent is demoted to a pure execution engine** — it no longer generates or manages `session_id`. `run_stream()` receives a `session_id` assigned by `SessionPool` (or an ephemeral one when used standalone without a pool).
- **EventBus supports scoped subscriptions** — `subscribe(session_id, scope="session" | "descendants" | "subtree")` allows protocol handlers to automatically receive events from child sessions without manual subscription management.
- **AgentContext.create_child_session() routes through SessionPool** — instead of calling storage-layer `SessionManager`, it delegates to `SessionPool` to ensure the child session is tracked and its events are routable.
- **Child session lifecycle is configurable** — per-session `SessionLifecyclePolicy` controls whether child sessions are independent, cascade-closed with parent, or bound to parent lifetime (enabling A2A-style long-running subagents).

## Capabilities

### New Capabilities

- `unified-session-lifecycle`: Single `SessionPool` API for creating all sessions (top-level and child) with parent-child relationship tracking, unified cleanup, and TTL management.
- `event-bus-scoped-subscription`: EventBus subscriber scopes (`session`, `descendants`, `subtree`) that automatically route events from related sessions without per-child manual subscription.
- `child-session-policy`: Configurable `SessionLifecyclePolicy` per session (independent, cascade, bound) controlling how child sessions behave when parent closes or reaches TTL.

### Modified Capabilities

- *(none — this is primarily an internal architecture refactor with no external protocol behavior changes)*

## Impact

- `agentpool/agents/base_agent.py`: Removes `session_id` generation logic from `run_stream()`; accepts externally-provided `session_id`.
- `agentpool/orchestrator/core.py`: Expands `SessionPool`, `SessionController`, `EventBus`, and `SessionState` with parent-child tracking, scoped subscriptions, and lifecycle policies.
- `agentpool/agents/context.py`: Changes `create_child_session()` to delegate to `SessionPool` instead of `SessionManager`.
- `agentpool_server/acp_server/handler.py`: Event consumer uses `scope="descendants"` subscription to receive child session events automatically.
- `agentpool/agents/events.py`: `StreamEventEmitter._emit()` publishes to `ctx.pool.session_pool.event_bus` (unified) instead of `run_ctx.event_bus` (turn-local).
