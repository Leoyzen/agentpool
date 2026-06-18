## Context

The AgentPool ACP server implements MCP-over-ACP transport per RFC-0033. When an MCP server sends a request to the MCP client (e.g., `elicitation/create`), the message flows through the ACP channel as a client-initiated `mcp/message` request. The ACP protocol requires the agent (AgentPool) to synchronously return the inner MCP result as the ACP response.

Currently, `AgentPoolACPAgent.ext_method("mcp/message")` treats all messages as fire-and-forget notifications: it spawns `handle_client_message` as a background task and immediately returns `{}`. This violates the RFD for request messages (those with an `id` field), causing the client to receive an empty response instead of the actual MCP result.

Additionally, `AcpMcpConnection.send_to_client()` unconditionally writes the ACP acknowledgment response back into the MCP session stream. When the message being forwarded is itself an MCP response (from the MCP client back to the MCP server), this creates a fake JSON-RPC response that corrupts the session.

The codebase uses `fastmcp.ClientSession` with memory object streams bridged by `AcpMcpTransport`. The transport's `_forward_to_client` task reads from `from_session_receive` and calls `send_to_client` for each message. This architecture is sound for agent-initiated requests but lacks the correlation mechanism needed for client-initiated requests.

## Goals / Non-Goals

**Goals:**
- Make `ext_method("mcp/message")` return the inner MCP result for client-initiated requests, per the MCP-over-ACP RFD.
- Prevent `send_to_client` from writing ACP acknowledgments back into the MCP session stream when the forwarded message is an MCP response.
- Ensure the fix is minimal, localized to `AcpMcpConnection` and `AgentPoolACPAgent`, and does not alter the `AcpMcpTransport` architecture.
- Support concurrent pending requests through per-message-id correlation.
- Provide proper timeout and cleanup to prevent Future leaks and spurious message forwarding.

**Non-Goals:**
- No changes to `AcpMcpTransport`, `fastmcp.ClientSession`, or the stdio/SSE/HTTP MCP transports.
- No changes to the ACP protocol schema or JSON-RPC framing.
- No rate limiting, payload validation, or security hardening (deferred to future work).
- No support for out-of-order response delivery (responses must match pending requests by id).

## Decisions

**Decision 1: Inline Correlation Registry in `AcpMcpConnection`**
- *Choice*: Add `_pending_client_requests: dict[Any, asyncio.Future]` to `AcpMcpConnection` rather than introducing a new router class or modifying `AcpMcpTransport`.
- *Rationale*: This is the minimal surface area. The connection already owns the memory streams and the `send_to_client` method. Adding correlation here localizes the fix without cross-cutting changes. A separate router class (Option C) would require refactoring the transport's `_forward_to_client` task and increase regression risk.
- *Alternatives considered*: Option B (only fix `send_to_client`) was rejected because it does not fix the primary protocol violation. Option C (Unified Message Dispatcher) was rejected as over-engineering for a targeted bugfix.

**Decision 2: Fulfillment in `send_to_client` before forwarding**
- *Choice*: In `send_to_client`, check if the message is a response (`result` or `error` key) and if its `id` matches a pending request. If so, fulfill the Future and return without calling `_send_to_client`.
- *Rationale*: This cleanly separates the two paths:
  - **Agent-initiated request/response**: `_forward_to_client` reads from `from_session`, calls `send_to_client`, which forwards to ACP client via `_send_to_client`, receives ACP response, writes it back to `_to_session_send`.
  - **Client-initiated request**: `ext_method` writes to `_to_session_send`, MCP client processes it, writes response to `from_session`, `_forward_to_client` reads it, `send_to_client` fulfills the pending Future instead of forwarding.
- *Risk mitigation*: Zero-buffer streams (`anyio.create_memory_object_stream(0)`) create a strict happens-before chain: `ext_method`'s `send()` blocks until `ClientSession._receive_loop` reads, guaranteeing the Future is registered before any response can be generated.

**Decision 3: Synchronous await with timeout in `ext_method`**
- *Choice*: For client-initiated requests, `ext_method` registers a Future, writes the message (with `anyio.fail_after(30)` to prevent indefinite blocking on a dead session), then `await asyncio.wait_for(future, timeout=30)`. The request detection requires both `"method" in msg` and `msg.get("id") is not None` to distinguish requests from responses.
- *Rationale*: This is the only way to return the inner result synchronously to the ACP client. The timeout matches the existing `send_to_client` ACP request timeout (30s). Adding `"method" in msg` prevents a buggy/malicious client from sending an inner MCP response (which has `id` but no `method`) through the request path, which would deadlock for 30 seconds.
- *Risk*: A blocked `ext_method` blocks the ACP handler thread. However, ACP handlers are already async, and 30s is the same timeout used for agent-initiated `mcp/message` requests. Total worst-case timeout for a completely dead session is up to 60s (30s for send + 30s for response wait), though in practice the send timeout dominates for dead sessions.

**Decision 4: Error mapping**
- *Choice*: If the inner MCP response contains an `error` field, raise `acp.exceptions.RequestError` with the sanitized inner error code, message, and optional data payload.
- *Rationale*: This maps inner MCP errors to outer ACP JSON-RPC errors correctly, preserving error semantics across the boundary. Error codes are sanitized via the existing `_sanitize_error` logic to handle non-standard string codes.

**Decision 5: Timed-out responses are consumed, not forwarded**
- *Choice*: `fulfill_pending_request` pops the Future from the registry under lock. If the Future is already done (e.g., timed out or cancelled), it logs a warning and returns `True` so the caller knows to drop the message without forwarding.
- *Rationale*: Without this guard, a late response arriving after `asyncio.wait_for` has timed out would be forwarded to the ACP client as a spurious unsolicited request, reintroducing the exact class of bug being fixed.

**Decision 6: Duplicate ID registration is rejected**
- *Choice*: `register_pending_request` raises `RuntimeError` if the ID already exists in the registry.
- *Rationale*: JSON-RPC IDs should be unique within a session. Silent overwrites would cause cross-talk where the first `ext_method` call awaits a Future that gets fulfilled by the second response.

## Risks / Trade-offs

- **[Risk]** Future leak if `ext_method` crashes after registering but before awaiting. → *Mitigation*: Use `try/finally` to always unregister the pending request.
- **[Risk]** ACP client sends a response with an `id` that does not match any pending request. → *Mitigation*: Drop the message (do not forward). A response with no matching pending request is semantically meaningless as a forwarded request.
- **[Risk]** Concurrent pending requests with colliding ids. → *Mitigation*: `register_pending_request` raises `RuntimeError` on duplicate IDs. The correlation registry is per-connection; ids only need to be unique within a single MCP session, which is already guaranteed by the MCP client.
- **[Risk]** Timeout during elicitation (user may take longer than 30s to respond). → *Mitigation*: 30s is the same timeout used for agent-initiated mcp/message. If longer timeouts are needed, this can be parameterized later. Document as known limitation.
- **[Risk]** `ext_method` hangs indefinitely if `AcpMcpTransport.connect_session()` was never called. → *Mitigation*: Wrap `handle_client_message` in `anyio.fail_after(30)` so it times out instead of deadlocking.
- **[Risk]** Second response with same id (buggy MCP client) crashes forwarder task. → *Mitigation*: `fulfill_pending_request` pops the Future atomically under lock and catches `InvalidStateError` gracefully (logs warning, does not crash).
- **[Risk]** `asyncio.CancelledError` from connection close propagates out of `ext_method` and cancels the ACP handler task. → *Mitigation*: Explicitly catch `asyncio.CancelledError` alongside `asyncio.TimeoutError` and map to `RequestError` with code `-32001` ("Connection closed while awaiting MCP response").

## Migration Plan

This is a bugfix with no migration required. Existing ACP sessions are unaffected. The change only affects the bidirectional routing logic within active MCP-over-ACP connections.

## Open Questions

- Should the timeout for client-initiated requests be configurable per-connection or per-server?
- Should we add metrics/logging for pending request queue depth to detect stalls?
