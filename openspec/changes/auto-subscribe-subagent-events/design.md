## Context

AgentPool exposes agents through multiple protocol servers (ACP, OpenCode, AG-UI, OpenAI API). Each protocol server needs to forward subagent events to connected clients. Before this change, both OpenCode and ACP protocol handlers implemented their own event consumer loops with near-identical patterns: EventBus subscription, `while True` consumer loops, cleanup on session close, and recursive child session handling via `SpawnSessionStart`.

This duplication made it easy for the two handlers to diverge. A concrete gap was identified in the ACP handler: it subscribed with `scope="descendants"` but never created per-child-session converters, so raw child events were silently dropped. The OpenCode handler handled this correctly but with ~130 lines of boilerplate that could be shared.

## Goals / Non-Goals

**Goals:**
- Extract the common auto-subscription pattern into a reusable `ProtocolEventConsumerMixin`
- Refactor OpenCode server to use the mixin with zero behavior change
- Refactor ACP server to use the mixin and fix the raw child event handling gap
- Ensure recursive child session consumers are started automatically for both protocols
- Provide TDD test coverage for the mixin and integration tests for both protocols

**Non-Goals:**
- BackgroundTaskProvider / DelegationProvider simplification (lives in parent repo `../xeno-agent`)
- Full AG-UI or OpenAI API subagent event implementation (the mixin supports them, but no handler refactor is included)
- Changes to BaseServer (it remains a minimal lifecycle manager)
- Changes to the EventBus implementation
- Changes to business-layer providers (SubagentTools, WorkersTools)

## Decisions

### D1: Mixin pattern over BaseServer inheritance

We chose a mixin (`ProtocolEventConsumerMixin`) rather than adding behavior to `BaseServer` because:

1. **BaseServer is minimal by design** — it only starts/stops servers and does not know about sessions or the EventBus.
2. **Not all servers need event consumption** — the HTTP server and MCP server do not subscribe to agent stream events.
3. **Protocol-specific conversion stays in protocol modules** — the mixin handles subscription and lifecycle; each protocol handler implements `_handle_event()` for its own conversion logic.

### D2: Raw events forwarded directly (no SubAgentEvent wrapping at protocol layer)

The original spec draft required wrapping child events in `SubAgentEvent` at the protocol layer. This contradicts the actual architecture:

- `SubAgentEvent` is a **business-layer** wrapper used by `team.py` and `teamrun.py` for legacy path compatibility.
- Both OpenCode and ACP converters already handle raw `RichAgentStreamEvent` objects directly.
- Adding `SubAgentEvent` wrapping at the protocol layer would introduce an unnecessary translation step and duplicate what the converters already do.

**Decision**: Protocol handlers forward raw events. Converters decide how to present them to clients.

### D3: `descendants` scope with recursive consumer spawning

The mixin subscribes with `scope="descendants"` (configurable via `_get_subscription_scope()`). When a `SpawnSessionStart` event arrives, the mixin:

1. Calls the optional `_handle_spawn_session_start()` hook
2. Calls `start_event_consumer(event.child_session_id)` to start a child consumer

This means child events are received on the parent queue (via EventBus routing) AND on a dedicated child queue. The handler's `_handle_event()` implementation can choose which queue to process. The ACP handler routes by `event.session_id`; the OpenCode handler currently routes all events through the parent session's ToolPart.

### D4: Error resilience in the mixin loop

Converter errors (e.g., malformed events, client disconnects) must not crash the consumer loop. The mixin catches exceptions from `_handle_event()`, logs them, and continues. This was previously duplicated in both protocol handlers; now it lives in one place.

## Architecture

```
+-------------------+        +------------------------+
|  EventBus         |        |  ProtocolEventConsumerMixin  |
|  (orchestrator)   |<------>|  - start_event_consumer()    |
|                   | queue  |  - stop_event_consumer()     |
|  scope="descendants"       |  - _event_consumer_loop()    |
|                   |        |  - _handle_event() [abstract]|
+-------------------+        |  - _handle_spawn_session_start|
         ^                   +------------------------+
         |                              ^
         |                              | inherits
         |          +-------------------+-------------------+
         |          |                                       |
+--------|----------+-----------+  +------------------------|--------+
| OpenCodeProtocolHandler       |  | ACPProtocolHandler      |
| - _handle_event() -> SSE      |  | - _handle_event() ->    |
| - _handle_spawn_session_start |  |   session_update()      |
|   -> ToolPart creation        |  | - _handle_spawn_session_start |
|                               |  |   -> converter cache    |
+-------------------------------+  +-------------------------+
```

### Lifecycle

1. **Handler receives a prompt** (OpenCode message or ACP prompt request)
2. **Handler calls `start_event_consumer(session_id)`** before delegating to `SessionPool.receive_request()`
3. **Mixin subscribes to EventBus** with `scope="descendants"` and starts an asyncio Task
4. **Agent runs and emits events** to the EventBus
5. **Consumer loop dispatches events** to `_handle_event()`
6. **On `SpawnSessionStart`**, mixin starts a child consumer automatically
7. **On session close**, handler calls `stop_event_consumer(session_id)` which cancels the task and unsubscribes

### Key Files

- `src/agentpool_server/mixins.py` — `ProtocolEventConsumerMixin`
- `src/agentpool_server/opencode_server/handler.py` — OpenCode handler (refactored)
- `src/agentpool_server/acp_server/handler.py` — ACP handler (refactored + raw child fix)
- `tests/servers/test_subagent_event_mixin.py` — TDD unit tests for mixin
- `tests/servers/opencode_server/test_subagent_events.py` — OpenCode integration tests
- `tests/servers/acp_server/test_subagent_events.py` — ACP integration tests

## Out of Scope

- **BackgroundTaskProvider**: Lives in parent repo (`../xeno-agent`). Any simplification there is tracked separately.
- **AG-UI / OpenAI API full implementation**: The mixin can be adopted by these handlers, but no refactor is included in this change.
- **SubAgentEvent business-layer changes**: `SubAgentEvent` remains in use for legacy team runs.
