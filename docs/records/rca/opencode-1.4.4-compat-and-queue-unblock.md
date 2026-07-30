# RCA: OpenCode 1.4.4+ compatibility and queued prompt unblock

## Summary

This issue was caused by **three separate but related problems**:

1. **Protocol compatibility gaps for OpenCode 1.4.4+**
   - missing OTLP compatibility endpoints
   - missing `/global/*` compatibility routes

2. **Incorrect completion signaling for queued prompts**
   - the server only emitted completion when the **entire async queue** drained
   - the client expected a **per-turn completion signal** (`session.idle`)

3. **A route-owned timeout on synchronous `/message` processing**
   - the server wrapped the whole streamed turn in a hard `asyncio.timeout(...)`
   - long silent waits (especially question / permission waits) were treated as route failures

Together, these caused:
- `405 Method Not Allowed` during OTLP export
- startup/runtime compatibility issues with newer OpenCode clients
- delayed exit after the first interaction
- queued prompts appearing stuck until much later
- `500 Internal Server Error` when a sync OpenCode turn stayed silent for too long
- follow-up `/question/{id}/reply` requests returning `404` after the server had already torn down the pending question

---

## Symptoms

- OpenCode `1.4.3` worked normally
- OpenCode `1.4.4+` showed failures
- logs included:
  - `Failed to export metrics batch code: 405, reason: Method Not Allowed`
- after the first interaction:
  - the client could not exit promptly
  - later prompts were queued
  - the previous turn was not considered finished in real time
- after a longer silent wait during `/message` processing:
  - the route failed with `TimeoutError`
  - the agent stream saw `CancelledError`
  - later question replies could hit `404 Not Found`

---

## Root Cause

### 1. Missing OTLP endpoints
Newer OpenCode clients send:
- `POST /v1/metrics`
- `POST /v1/traces`
- `POST /v1/logs`

These requests were not explicitly handled and fell through to a catch-all route that only supported `GET/HEAD/OPTIONS`, resulting in `405`.

### 2. Missing global compatibility routes
The server did not provide:
- `GET /global/config`
- `PATCH /global/config`
- `POST /global/dispose`
- `POST /global/upgrade`

This created compatibility issues for newer client lifecycle flows.

### 3. Completion signaling was too coarse
Queued async prompts only triggered `session.idle` when the **whole queue** drained.

However, the client depends on `session.idle` as the signal that:
- the current turn is done
- input can be unblocked
- the session can continue or exit

As a result, a completed queued turn could still appear unfinished to the client.

### 4. Busy-session enqueue did not guarantee worker startup
If an async prompt was enqueued while a synchronous `/message` turn was already running, the queue item could exist without a guaranteed worker handoff immediately after the sync turn completed.

### 5. `/message` used request/response timeout semantics for an event-driven interaction
OpenCode session turns are not purely request/response.

During a single `/message` turn, the server may legitimately spend a long time with no streamed model output while it is:
- waiting for a tool approval
- waiting for a question reply
- waiting for other user-driven side-channel events

The server wrapped the entire sync turn in a hard timeout:

- `async with asyncio.timeout(STREAM_TIMEOUT_SECONDS)`

When that timeout fired, it cancelled the active stream, which produced the observed chain:

- `TimeoutError` at the route level
- `CancelledError` inside the agent's event queue wait
- `500` returned from `POST /session/{id}/message`
- cleanup of pending question state, causing later `/question/{id}/reply` to return `404`

The key mistake was treating "no stream output yet" as equivalent to "the turn is broken". In this protocol, a silent turn can still be healthy because progress may be happening through separate events.

---

## Fix

### OTLP compatibility
Added minimal compatibility sinks:
- `POST /v1/metrics`
- `POST /v1/traces`
- `POST /v1/logs`

These return success and intentionally discard payloads.

### Global compatibility routes
Added minimal safe compatibility routes:
- `GET /global/config`
- `PATCH /global/config`
- `POST /global/dispose`
- `POST /global/upgrade`

Behavior is intentionally minimal:
- config routes reuse existing config behavior
- dispose/upgrade are safe stubs with no destructive side effects

### Queued prompt unblock
Updated queue/session lifecycle handling so that:
- each queued async turn emits a **per-turn** completion signal
- full `mark_session_idle()` only happens when the queue is truly empty
- sync `/message` completion immediately hands off to queued async work
- async enqueue always ensures a worker exists

This was implemented by:
- adding queue/worker helper methods in `ServerState`
- adding `emit_session_turn_complete(session_id)`
- updating async queue draining logic in `message_routes.py`
- updating sync-to-async handoff behavior

### Long-wait sync turn handling
Updated synchronous `/message` processing so that it no longer applies a route-owned hard timeout to the entire agent stream.

Behavior now:
- the sync turn remains alive as long as the underlying work is still active
- question / permission flows can complete through the normal event endpoints
- the route no longer converts a legitimate silent wait into `CancelledError` + `TimeoutError` + `500`

This was implemented by:
- removing the `asyncio.timeout(...)` wrapper around `adapter.process_stream(iterator)` in `message_routes.py`
- preserving the existing event-driven lifecycle instead of forcing request-timeout semantics onto it

---

## Validation

Targeted regression coverage was added for:
- OTLP compatibility endpoints
- `/global/*` compatibility routes
- queued async prompt worker startup
- per-turn `session.idle` signaling
- long-running sync `/message` turns that stay silent before resuming
- concurrency behavior
- SSE/global event compliance
- OpenCode storage/project persistence

### Result
- **256 passed**
- **6 skipped**

---

## Key Lessons

1. **End of streamed output is not the same as turn completion**
   - clients often depend on explicit lifecycle events, not just stream exhaustion

2. **Queue completion and turn completion are different concepts**
   - signaling only when the whole queue drains is too coarse for interactive clients

3. **A silent turn is not necessarily a hung turn**
   - OpenCode uses side-channel events (`question`, permission, SSE lifecycle) during active work
   - route-level hard timeouts can break valid interactions by destroying state the client still depends on

4. **Compatibility fixes should start with minimal safe behavior**
   - protocol recovery first, full functionality later

5. **Do not trust delegated implementation blindly**
   - route-level compatibility fixes must be verified by reading actual code and running focused tests

---

## Related commits

- `5537d2d7f` `fix(opencode-storage): add project persistence support for OpenCode storage`
- `6de476f92` `fix(opencode-server): add OTLP compatibility sinks for 1.4.4+`
- `e28090479` `fix(opencode-server): add global compatibility routes for newer clients`
- `c28e2f3f1` `fix(opencode-server): unblock queued prompts on per-turn completion`
