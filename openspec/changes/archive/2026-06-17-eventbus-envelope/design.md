## Context

AgentPool's EventBus is a shared pub/sub channel where events from all sessions (parent and child) flow through. When a consumer subscribes with `scope="descendants"`, it receives events from child sessions, but the consumer has no way to know which session produced each event.

Currently, multiple ad-hoc fixes attempt to work around this:
- `TurnRunner._publish_event` uses `setattr(event, "session_id", ...)` on arbitrary objects
- `StreamEventEmitter._emit` uses `hasattr` checks and `setattr`
- `RunExecutor` manually passes `session_id` to event constructors
- `ACPProtocolHandler._handle_event` falls back to consumer's session ID

These fixes are fragile (PydanticAI events may not support setattr consistently), scattered across 4+ files, and violate the principle that routing metadata should be owned by the transport layer.

## Goals / Non-Goals

**Goals:**
- Event consumers can always determine the source session of any received event
- Remove all ad-hoc session ID injection from producers
- Make EventBus API type-safe for routing metadata
- Ensure protocol handlers (ACP, OpenCode) can route child session events correctly

**Non-Goals:**
- Changing event content/schema (events remain unchanged)
- Changing EventBus subscription/dispatch semantics (scope logic stays the same)
- Adding new session management features
- Supporting non-session event sources

## Decisions

### Decision 1: EventEnvelope as wrapper
**Choice**: Introduce `EventEnvelope` dataclass that wraps every event published to EventBus.

**Rationale**:
- Separates routing metadata (owned by EventBus) from event payload (owned by producers)
- No need to mutate arbitrary event objects
- Type-safe: `envelope.source_session_id: str` is always present
- Transparent to consumers via `__getattr__` forwarding

**Alternative considered**: Inject session_id into event objects.
- Rejected: Requires setattr on third-party types (PydanticAI), fragile, scatters responsibility.

### Decision 2: EventBus owns metadata injection
**Choice**: `EventBus.publish(session_id, event)` internally creates `EventEnvelope(source_session_id=session_id, event=event)`.

**Rationale**:
- Producers simply publish events; they don't need to know about routing
- Single point of truth for how metadata is attached
- Consumers receive consistent structure regardless of producer

### Decision 3: Transparent attribute forwarding
**Choice**: `EventEnvelope.__getattr__` forwards attribute access to the wrapped event.

**Rationale**:
- Consumers can still write `envelope.event_kind` or `envelope.delta` without unwrapping
- Minimizes migration effort for existing code
- Maintains duck-typing compatibility

**Trade-off**: Shadowing risk if event has `source_session_id` attribute. Mitigation: Use `ev.source_session_id` (envelope's own field) and `ev.event` (explicit access) when needed.

### Decision 4: Consumer signature change
**Choice**: All event consumers receive `EventEnvelope` instead of raw events.

**Rationale**:
- Forces consumers to be aware of routing metadata
- Prevents silent bugs where consumer ignores source session

**Migration**: Update all `async for event in queue:` to `async for envelope in queue:`, then use `envelope.event` or `envelope.<attr>` for event properties.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Large blast radius (11+ subscribe points across 10 files) | Staged migration: update core first, then protocol handlers, then tests |
| Third-party consumers outside repo may break | This is a breaking API change documented in proposal |
| Replay buffer stores envelopes instead of raw events | Envelope is lightweight; memory impact negligible |
| Type checkers may complain about `__getattr__` | Add `EventEnvelope` type annotations; consumers can cast if needed |

## Migration Plan

1. **Phase 1**: Define `EventEnvelope`, update `EventBus.publish()` and `subscribe()`
2. **Phase 2**: Update all consumers (ACP handler, OpenCode handlers, mixins, internal adapters)
3. **Phase 3**: Remove ad-hoc session_id injection from producers (`_publish_event`, `_emit`, `RunExecutor`, `process_tool_event`)
4. **Phase 4**: Update all tests to work with `EventEnvelope`
5. **Phase 5**: Run full test suite, verify no regressions

Rollback: Revert commit. Envelope is additive at API level but breaking at type level.

## Open Questions

- Should `EventEnvelope` include `timestamp` or `trace_id` for future observability?
- Should we provide a helper `unwrap_envelope(events)` for consumers that don't care about routing?
