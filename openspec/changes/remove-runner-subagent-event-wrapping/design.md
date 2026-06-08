## Context

Currently, `TurnRunner._maybe_wrap_event` in `agentpool/orchestrator/core.py` intercepts every event published for a child session and wraps it in a `SubAgentEvent` envelope. This was introduced so that protocol layers (opencode server, ACP server) could distinguish events belonging to child sessions from those of the parent session.

However, this design has several problems:

1. **Runner layer overreach**: The runner should not know about subagent hierarchies. Its job is to execute agents and emit raw events. Session hierarchy is a protocol-layer concern.

2. **Event transparency loss**: Components like `BackgroundTaskProvider._task_sync` expect to match raw completion events (`StreamCompleteEvent`, `ToolCallStartEvent`) from the event stream. When these are wrapped inside `SubAgentEvent`, the match logic fails, causing the lead agent to hang waiting for a result that was already emitted.

3. **Redundancy**: Both opencode and ACP servers already use `scope="descendants"` to subscribe to child session events on the EventBus. They receive child events directly — the `SubAgentEvent` wrapping is an extra layer that duplicates what the EventBus scope mechanism already provides.

4. **ACP complexity**: The ACP event converter has three subagent display modes (inline, tool_box, legacy) that depend on `SubAgentEvent` to extract `source_name` and `depth`. The user has decided to remove inline and tool_box modes, simplifying to legacy only.

All stream events already carry a `session_id` field (verified in `agentpool/agents/events/events.py`). This provides sufficient information for protocol layers to route events to the correct session context without wrapper envelopes.

## Goals / Non-Goals

**Goals:**
- Remove `TurnRunner._maybe_wrap_event` so runner layer emits raw events
- Update opencode event processor to route events using `event.session_id`
- Update ACP event converter to handle raw child session events, removing inline/tool_box modes
- Restore `BackgroundTaskProvider._task_sync` to match raw completion events directly
- Ensure all protocol layers can still render subagent output correctly

**Non-Goals:**
- Redesign the EventBus (already supports `scope="descendants"`)
- Change how child sessions are created (SessionController logic stays)
- Introduce new subagent rendering features in ACP (deferred to official RFD)
- Modify pydantic-graph integration or MessageNode abstraction

## Decisions

### Decision 1: Remove SubAgentEvent wrapping from runner layer
**Rationale**: Runner layer should be session-agnostic. Protocol layers own session hierarchy.
**Alternative considered**: Keep wrapping but fix _task_sync to unwrap — rejected because it perpetuates the architectural violation.

### Decision 2: Protocol layers route by event.session_id
**Rationale**: All events already carry `session_id`. Protocol consumers (opencode/ACP) can maintain a map of session_id → context and dispatch accordingly.
**Alternative considered**: Add a separate session header/metadata channel — rejected as over-engineering when session_id already exists.

### Decision 3: ACP removes inline and tool_box subagent display modes
**Rationale**: User explicitly requested removal. These modes depend heavily on SubAgentEvent structure and will be replaced by official RFD implementation later.
**Impact**: Only `_convert_subagent_legacy` remains for subagent rendering in ACP.

### Decision 4: opencode event_processor removes _process_subagent_event
**Rationale**: With raw events, the processor no longer needs to unwrap SubAgentEvent. Instead, `convert_event` checks `event.session_id` against `ctx.session_id` and switches to the appropriate child context.

## Risks / Trade-offs

- **[Risk] Protocol rendering regression** → Mitigation: Comprehensive integration tests for subagent rendering in both opencode and ACP
- **[Risk] Session context lookup overhead** → Mitigation: Cache session_id → context mappings in event processor/converter
- **[Risk] Events without session_id** → Mitigation: Audit all event types to ensure session_id is populated; add assertion in debug builds
- **[Risk] Parallel team events interleaving** → Mitigation: Ensure context map is thread-safe (async-safe) and keyed by session_id

## Migration Plan

1. **Phase 1**: Remove `_maybe_wrap_event` from TurnRunner, verify EventBus scope="descendants" still delivers child events
2. **Phase 2**: Update opencode event_processor — add session_id-based context routing, remove SubAgentEvent handling
3. **Phase 3**: Update ACP event_converter — remove inline/tool_box modes, add session_id-based state isolation
4. **Phase 4**: Restore BackgroundTaskProvider._task_sync match logic
5. **Phase 5**: Run full test suite, fix regressions

## Open Questions

- Should `event.session_id` be required (enforced at type level) for all RichAgentStreamEvent subclasses?
- How should parallel team events be handled when multiple child sessions emit events simultaneously — does the current context map pattern handle this?
