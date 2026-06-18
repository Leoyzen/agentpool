## Why

Currently, when child session events (e.g., subagent runs) are published through the shared EventBus and routed to parent consumers via `scope="descendants"`, the consumer cannot determine the event's actual source session. This forces protocol handlers (ACP, OpenCode) to route all events under the parent session ID, breaking subagent UI rendering and session isolation. Multiple ad-hoc fixes (`setattr` on arbitrary objects, `hasattr` checks, fallback logic) have been applied but remain fragile and error-prone.

## What Changes

- **Introduce `EventEnvelope`** as a first-class wrapper for all EventBus events, carrying routing metadata (`source_session_id`) alongside the event payload.
- **BREAKING**: Change `EventBus.subscribe()` to return `Queue[EventEnvelope]` instead of `Queue[Any]`.
- **BREAKING**: All event consumers (ACP handler, OpenCode handlers, internal adapters) must consume `EventEnvelope` and access `envelope.source_session_id` for routing.
- Remove all ad-hoc session ID injection (`setattr`, `hasattr` patches) from producers (`TurnRunner._publish_event`, `StreamEventEmitter._emit`, `RunExecutor`).
- Remove manual `session_id` passing through `RunExecutor` and `process_tool_event`; EventBus owns routing metadata.
- Provide transparent attribute forwarding (`__getattr__`) on `EventEnvelope` so consumers can still access event properties directly.

## Capabilities

### New Capabilities
- `eventbus-envelope`: EventBus envelope wrapping with source session tracking and transparent event access.

### Modified Capabilities
- *(none — this is a pure infrastructure refactor with no spec-level behavior changes)*

## Impact

- `agentpool/orchestrator/core.py` — `EventBus.publish()` and `EventBus.subscribe()` signatures
- `agentpool/orchestrator/run_executor.py` — Remove manual `session_id` passing
- `agentpool/orchestrator/core.py` — `TurnRunner._publish_event` simplified
- `agentpool/agents/events/event_emitter.py` — `StreamEventEmitter._emit` simplified
- `agentpool/agents/native_agent/helpers.py` — `process_tool_event` signature simplified
- `agentpool_server/acp_server/handler.py` — Consume `EventEnvelope`, use `source_session_id`
- `agentpool_server/opencode_server/` — All event consumers adapted
- `agentpool_server/mixins.py` — `ProtocolEventConsumerMixin` adapted
- All tests that mock EventBus or directly consume events
