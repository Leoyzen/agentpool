# Spec: Unified Event Routing — Gap Closure

## Requirements

### REQ-1: McpToolsChangedEvent Wiring

`McpToolsChangedEvent` (currently at `src/agentpool_server/opencode_server/models/events.py:850`) MUST be:
1. Emitted from `MCPCapability.on_change()` stream when MCP tool list changes (server added, removed, or tool list diff detected)
2. Handled by `EventProcessor` to trigger a tool list refresh notification

If the event remains in the OpenCode server models (not promoted to core), the cross-layer wiring MUST be explicitly documented.

**Rationale**: The event type exists with a TODO comment but is never emitted or consumed. MCP tool changes (server add/remove) should propagate to clients.

### REQ-2: StreamCompleteEvent Cancellation Distinction

`EventProcessor` MUST distinguish `StreamCompleteEvent(cancelled=True)` from normal completion:
- `cancelled=True` → emit `SessionStatusEvent(status="cancelled")`
- `cancelled=False` → emit `SessionStatusEvent(status="idle")`

**Rationale**: Currently both paths emit the same status, making it impossible for clients to distinguish a completed run from a cancelled one.

### REQ-3: Deprecated stream_adapter Removal

`stream_adapter._handle_event` (kept only for test compatibility) MUST be removed. Tests MUST be updated to use `EventProcessor` directly.

**Rationale**: Dead code maintained for test convenience adds maintenance burden without value.

> Note: `RunStartedEvent` handling was moved to the `m4-multi-config` change (task 18.3) because it modifies the same `EventProcessor._handle_event()` method that M4's OpenCode hardening touches.

## Verification

- `EventProcessor` handles `McpToolsChangedEvent` and distinguishes `StreamCompleteEvent.cancelled`
- `grep -rn '_handle_event' src/agentpool_server/opencode_server/stream_adapter.py` returns 0 (or file is removed)
- Tool list refresh notification fires when MCP tools change
