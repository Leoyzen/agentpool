## Context

AgentPool serves as an ACP (Agent Client Protocol) server, exposing agents to ACP clients such as Zed, Toad, and custom frontends. The ACP protocol includes a `turn_complete` session update (draft RFD PR #644) that signals the explicit end of a prompt turn.

Currently:
- `AgentPoolACPAgent.initialize()` **always** advertises `turn_complete=True` in `InitializeResponse`.
- `ACPEventConverter` **always** emits `TurnCompleteUpdate` on `StreamCompleteEvent`.
- `ACPProtocolHandler.handle_prompt()` returns `PromptResponse` immediately after calling `SessionPool.receive_request()` (fire-and-forget), before any stream events are produced.

This causes legacy clients that do not understand `turn_complete` to experience UI desync: they receive `PromptResponse(stop_reason="end_turn")` first, then additional `session/update` events afterward, leaving the frontend in a "running" state that requires manual termination.

## Goals / Non-Goals

**Goals:**
- Allow ACP clients to explicitly declare `turn_complete` support via `ClientCapabilities`.
- Only advertise `turn_complete` capability when the client declares support.
- Only emit `TurnCompleteUpdate` when the client supports it.
- For clients that do NOT support `turn_complete`, block `PromptResponse` until the run completes (preserving the legacy event order).
- Maintain full backward compatibility — no breaking changes for existing clients.

**Non-Goals:**
- Changing the ACP spec itself (we follow the existing draft).
- Adding `turn_complete` support to non-ACP protocols (AG-UI, OpenCode, MCP).
- Modifying the internal `SessionPool` or `EventBus` architecture.
- Changing `turn_complete` behavior for native agents (they already work correctly).

## Decisions

### D1: Add `turn_complete: bool` to `ClientCapabilities`

**Decision**: Add a simple boolean field `turn_complete` to `ClientCapabilities`, defaulting to `False`.

**Rationale**: This mirrors how other capabilities (e.g., `terminal`, `fs`) are declared. It is the minimal, most explicit mechanism for capability negotiation.

**Alternative considered**: Infer support from protocol version — rejected because the `turn_complete` draft is not version-gated and may be backported to older clients.

### D2: `ACPEventConverter` receives `client_supports_turn_complete` at construction

**Decision**: Pass a `client_supports_turn_complete: bool` parameter into `ACPEventConverter.__init__`. The `StreamCompleteEvent` branch checks this flag before yielding `TurnCompleteUpdate`.

**Rationale**: The converter is stateful and created per-prompt. Passing the flag at construction keeps `convert()` signature stable and avoids threading session context through every event.

**Alternative considered**: Pass `ClientCapabilities` object directly — rejected because the converter only needs a single boolean, and holding a full capabilities object adds unnecessary coupling.

### D3: SessionPool path blocks `PromptResponse` for legacy clients

**Decision**: In `ACPProtocolHandler.handle_prompt()`, when `client_capabilities.turn_complete` is falsy, await the `RunHandle.complete_event` before returning `PromptResponse`.

**Rationale**: Legacy clients rely on `PromptResponse` as the definitive end-of-turn signal. Returning it before stream events causes UI desync. Blocking ensures `events → PromptResponse` ordering.

**Trade-off**: This adds latency to the `session/prompt` JSON-RPC response for legacy clients. However, the latency is bounded by the LLM turn itself, which is already the dominant factor.

### D4: Legacy `ACPSession.process_prompt()` path remains unchanged

**Decision**: The legacy session path (`session.py::process_prompt`) already returns `PromptResponse` after the stream completes. No changes needed there.

**Rationale**: The legacy path does not use `SessionPool` and does not suffer from the fire-and-forget timing issue.

## Risks / Trade-offs

- **[Risk] Awaiting `complete_event` in `handle_prompt` may deadlock if the run loop errors without setting the event** → Mitigation: Use `asyncio.wait_for()` with a generous timeout (e.g., 60s), and fall back to returning `PromptResponse(stop_reason="end_turn")` on timeout.

- **[Risk] Some existing tests construct `ClientCapabilities` without `turn_complete` and may break** → Mitigation: The field defaults to `False`, so existing code continues working. Only tests that explicitly assert on `ClientCapabilities` fields need updating.

- **[Trade-off] Blocking `PromptResponse` for legacy clients increases JSON-RPC response time** → Acceptable: the delay equals the LLM inference time, which is the natural duration of the turn. The client is already waiting for content.

## Migration Plan

1. Deploy schema change (`capabilities.py`) — backward-compatible (new optional field).
2. Deploy converter and handler changes — backward-compatible (legacy clients get blocking behavior).
3. Update ACP client implementations (if any internal clients exist) to declare `turn_complete=True`.
4. Monitor logs for timeout warnings in `handle_prompt`.

## Open Questions

- Should `turn_complete` default to `True` in `ClientCapabilities.create()` for test convenience, or explicitly `False` to enforce opt-in?
