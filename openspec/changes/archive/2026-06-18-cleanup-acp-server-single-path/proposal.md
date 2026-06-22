## Why

The ACP server and event system have accumulated redundant code paths and parallel implementations over multiple migration cycles. The SessionPool migration introduced `SessionController`/`TurnRunner`/`EventBus` as the unified orchestration layer, but legacy paths were kept behind feature flags. The `EventBusHooksAdapter` self-admits redundancy in its own docstring, `SessionStatusBridge` duplicates EventBus subscription, and the AG-UI/OpenAI API servers have no-op consumers that add per-session overhead without processing events. Cleaning these up reduces cognitive load, maintenance burden, and per-session overhead.

## What Changes

- **BREAKING**: Remove `EventBusHooksAdapter` — its `before_run`, `before_tool_execute`, and `after_tool_execute` hooks are redundant with `RunExecutor`'s event publishing
- **BREAKING**: Remove the non-SessionPool fallback path in `ACPSession.process_prompt()` — all ACP prompt processing goes through `SessionPool.run_stream()`. Also remove the dead legacy `acp_agent.prompt()` path (lines 656-698)
- Add `_skip_event_processing` flag to `ProtocolEventConsumerMixin`; AG-UI and OpenAI API servers set it to `True` to skip wasteful no-op event processing while retaining spawn detection
- Merge `SessionStatusBridge` into `OpenCodeSessionPoolIntegration._handle_event()`, fixing the existing double-broadcast bug of `SessionStatusEvent(type="busy")`
- Rename `ACPSessionManager._active` to `_acp_sessions`, delegate lifecycle queries to `SessionController` while keeping protocol-specific `ACPSession` objects
- Consolidate duplicate `RunFailedEvent` types: remove `BaseAgent.RunFailedEvent` (signal-based), keep only `events.py:RunFailedEvent` (EventBus-based)

## Capabilities

### New Capabilities
- `acp-single-execution-path`: ACP prompt processing uses SessionPool exclusively; no fallback to direct `agent.run_stream()`
- `eventbus-single-subscriber-per-session`: Each session has exactly one EventBus subscriber; status tracking is handled inline, not via a parallel `SessionStatusBridge` subscription

### Modified Capabilities
<!-- No existing specs to modify — this is a cleanup, not a behavioral change to external APIs -->

## Impact

- **Affected code**: `src/agentpool/agents/native_agent/eventbus_hooks_adapter.py` (removal), `src/agentpool_server/acp_server/session.py` (remove fallback), `src/agentpool_server/acp_server/session_manager.py` (remove `_active`), `src/agentpool_server/opencode_server/status_bridge.py` (remove), `src/agentpool_server/opencode_server/session_pool_integration.py` (absorb status handling), `src/agentpool_server/agui_server/server.py` (remove no-op consumer), `src/agentpool_server/openai_api_server/server.py` (remove no-op consumer), `src/agentpool/agents/base_agent.py` (remove `RunFailedEvent` inner class)
- **Dependencies**: None added or removed
- **Breaking**: Removal of `EventBusHooksAdapter` breaks any code that directly instantiates it (only used internally by native agent creation). Removal of ACP non-SessionPool fallback breaks any test that creates `ACPSession` without a `SessionPool`.
