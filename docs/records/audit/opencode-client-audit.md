# OpenCode Client Protocol Compatibility Audit

## Objective

Audit the actual OpenCode client-server protocol implementation to identify what works today and what gaps exist for reconnect/resilience scenarios. This document reflects the ACTUAL codebase state, not desired future behavior.

## Scope

The OpenCode client protocol consists of:
1. **SSE event streaming** from server to client (`GET /event`, `GET /global/event`)
2. **HTTP request/response** for user actions (send message, grant permission, etc.)
3. **Permission UI** for handling async tool permission requests

## Current Implementation State

### SSE Event Streaming (`global_routes.py:207-329`)

**Event format:**
```python
# _event_generator yields ONLY {"data": data} dicts
yield {"data": data}  # line 258, 267, 278
```

**No event IDs:** SSE events do NOT have `id` fields. The `sse_starlette.EventSourceResponse` receives dicts without `id` keys, so emitted SSE events have no `id:` lines.

**No replay:** The SSE endpoint (`get_events`, `get_global_events`) accepts no `last_event_id` parameter and does not read `Last-Event-ID` headers. The `_event_generator` has no replay logic.

**No deduplication:** The broadcast path (`state._broadcast_event_impl`) is a simple queue fan-out with no `seen_event_ids` tracking.

**EventBus state:** `EventBus.subscribe()` (core.py:131) returns a bare `asyncio.Queue` with no replay buffer. `EventBus.publish()` (core.py:211) forwards live events only.

## Test Scenarios

### Scenario 1: Permission Granted During Streaming

**Setup:**
- Client starts a streaming request (e.g., "Write a Python script")
- Agent executes a tool that requires permission (e.g., `write_file`)
- Permission request is sent while text is still streaming

**Actual behavior:**
1. Permission request is broadcast via `state.broadcast_event()` → `state.event_subscribers` → SSE
2. Client receives `permission.asked` event in the SSE stream
3. Client POSTs to `/session/{id}/permissions/{permissionID}` to grant/deny
4. Agent continues execution

**Code path:**
```
input_provider.py:87   get_tool_confirmation() creates permission
       ↓
state.py:360          _broadcast_event_impl() broadcasts to SSE queues
       ↓
global_routes.py:258  _event_generator() yields to client
       ↓
client                Receives permission.asked event
       ↓
session_routes.py:1351  Client POSTs grant/deny (/session/{id}/permissions/{permissionID})
       ↓
input_provider.py:185 resolve_permission() resolves the future
```

**Verdict:** ✅ **PASS** — Permission streaming works correctly today.

---

### Scenario 2: Event Ordering After Reconnect

**Setup:**
- Client is receiving SSE events
- Connection drops (network issue, server restart, etc.)
- Client reconnects

**Actual behavior:**
1. Client reconnects to `/event` or `/global/event`
2. Server creates a NEW `asyncio.Queue` and appends it to `state.event_subscribers`
3. Client receives ONLY events published AFTER reconnection
4. Events published during disconnect are LOST

**Root cause:**
- SSE events have NO IDs, so client cannot track last received event
- EventBus has NO replay buffer
- SSE endpoint has NO `last_event_id` parameter

**Verdict:** ❌ **FAIL** — This is a **pre-existing limitation**. Events are lost on reconnect. This is NOT a Migration B regression; it has never worked.

**Gap to close:**
- Add event IDs to SSE payload
- Accept `Last-Event-ID` header or `last_event_id` query parameter
- Implement EventBus replay buffer (`docs/design/eventbus-replay.md`)
- Replay historical events on reconnect

---

### Scenario 3: SSE Replay on New Connection

**Setup:**
- Client connects to SSE endpoint for the first time (or reconnects)
- Server has EventBus replay buffer with historical events

**Actual behavior:**
1. Client connects to `/event`
2. Server creates new queue
3. Client receives NO historical events
4. Client only receives events published after connection

**Root cause:**
- EventBus has NO replay buffer
- SSE endpoint has NO replay logic

**Verdict:** ❌ **FAIL** — Historical events are NOT replayed. This is a **pre-existing limitation**, not a Migration B regression.

**Gap to close:**
- Implement EventBus replay buffer (`docs/design/eventbus-replay.md`)
- Modify SSE endpoint to trigger replay on subscription
- Consider client-side handling of replayed events

---

## Summary

| Scenario | Status | Issue |
|----------|--------|-------|
| Permission during streaming | ✅ PASS | Works correctly today |
| Event ordering after reconnect | ❌ FAIL | No replay buffer, no event IDs |
| SSE replay on connection | ❌ FAIL | No replay buffer |

## Recommendations

### For Migration B

1. **Implement EventBus replay buffer** (`docs/design/eventbus-replay.md`)
   - Add replay buffer to `EventBus.subscribe()`
   - Store last N events per session
   - Replay on new subscription

2. **Add SSE event IDs**
   - Generate monotonic IDs for each SSE event
   - Include `id` field in yielded dicts

3. **Add `last_event_id` support to SSE endpoint**
   - Accept `Last-Event-ID` header or query parameter
   - Replay events from that ID forward

### For Client (Future)

1. **Implement reconnect with `last_event_id`** once server supports it
2. **Handle duplicate events** after reconnect (server may replay events client already saw)
3. **Handle historical events** on initial connection if replay is enabled

## Open Questions

1. **Should replay include `StreamCompleteEvent`?**
   - If a previous stream completed, should replay include the completion event?
   - *Recommendation:* Yes, so client knows stream state

2. **Should replay include tool results?**
   - Tool call events may contain large data (file contents)
   - *Recommendation:* Limit replay buffer size by event count, not byte size

3. **Event ID format?**
   - Monotonic integer (simple, comparable)
   - UUID (globally unique, not comparable)
   - *Recommendation:* Monotonic integer per session for ordering
