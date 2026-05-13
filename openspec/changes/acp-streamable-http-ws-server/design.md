## Context

ACP agents currently support three transport modes: stdio (subprocess), WebSocket (via `websockets` library), and custom streams. The existing `_serve_websocket()` in `src/acp/transports.py` is a bare WebSocket server — it accepts connections, wraps them in `AgentSideConnection`, and processes JSON-RPC messages. However, it lacks:

- No `Acp-Connection-Id` header during upgrade
- No `initialize` lifecycle enforcement
- No ASGI integration (uses `websockets` library directly, not uvicorn/Starlette)
- No connection to the RFC's Streamable HTTP WebSocket Transport profile

The ACP RFC (`streamable-http-websocket-transport.mdx`) defines a unified `/acp` endpoint that supports both Streamable HTTP and WebSocket upgrade. Per RFC: **Clients MUST support both transports, servers MAY support only WebSocket.** This justifies a Phase 1 WebSocket-only server.

The existing `ACPBridge` (`src/acp/bridge/bridge.py`) is an independent code path that spawns stdio subprocesses and exposes a simple POST `/acp` proxy. It has no overlap with the new transport and requires no changes.

## Goals / Non-Goals

**Goals:**
- Implement a WebSocket server transport compliant with the RFC's WebSocket profile
- Return `Acp-Connection-Id` header during WebSocket upgrade at `/acp`
- Enforce `initialize` lifecycle: client must send `initialize` request before other requests
- Reuse existing `AgentSideConnection` for ACP protocol handling over WebSocket
- Integrate with uvicorn/Starlette for production-grade ASGI serving
- Extend `Transport` type union, `ACPServer`, and CLI to support the new transport
- HTTP/1.1 initially (document deviation from RFC's HTTP/2 requirement)

**Non-Goals:**
- Streamable HTTP transport (POST/GET/DELETE with SSE) — deferred to Phase 2
- Multi-session support over a single WebSocket connection — Phase 1 is 1 connection = 1 session
- HTTP/2 support — will migrate to Hypercorn later, zero app-code change
- Changes to `ACPBridge` or stdio transport
- Client-side implementation — server only

## Decisions

### D1: Use Starlette + uvicorn instead of raw `websockets` library

**Choice**: Build an ASGI app with Starlette WebSocket endpoint, serve via uvicorn.

**Rationale**: The existing `_serve_websocket()` uses the `websockets` library directly. While functional, it can't integrate with the ASGI ecosystem (middleware, HTTP routes for future SSE, CORS, etc.). Starlette + uvicorn gives us:
- Standard ASGI app that can add HTTP routes later (Phase 2 Streamable HTTP)
- Middleware support (CORS, auth, logging)
- Production-grade HTTP server (uvicorn)
- Upgrade header control for `Acp-Connection-Id`

**Alternative considered**: Keep using `websockets` library + add a separate HTTP server for future SSE. Rejected because maintaining two server frameworks is unnecessary complexity and they can't share middleware/routing.

**Alternative considered**: Use FastAPI instead of Starlette. Rejected — FastAPI adds Pydantic validation overhead we don't need for a WebSocket endpoint. Starlette is the minimal ASGI toolkit.

### D2: WebSocket upgrade at `/acp` path with `Acp-Connection-Id` response header

**Choice**: WebSocket connections upgrade at `/acp`. Server generates a UUID `Acp-Connection-Id` and returns it in the upgrade response headers.

**Rationale**: Per RFC, the `/acp` endpoint is shared between HTTP and WebSocket transports. The connection ID is required for the Streamable HTTP profile (correlating POST requests with SSE streams), but returning it for WebSocket is still valuable for logging, debugging, and future session management.

### D3: Enforce `initialize` before other requests

**Choice**: After WebSocket upgrade, the server requires the first message to be an `initialize` request. Any other request before `initialize` receives a JSON-RPC error response.

**Rationale**: The RFC mandates `initialize` as the handshake. Enforcing it at the transport layer (before delegating to `AgentSideConnection`) ensures protocol compliance and provides a clean extension point for capability negotiation.

**Implementation**: Wrap `AgentSideConnection` in a thin guard that tracks initialization state. Once `initialize` succeeds, all subsequent messages pass through unmodified.

### D4: Reuse existing stream adapters

**Choice**: Reuse `_WebSocketReadStream` and `_WebSocketWriteStream` patterns from current `_serve_websocket()`, adapted for Starlette's WebSocket API.

**Rationale**: These adapters already handle the WebSocket ↔ `ByteReceiveStream`/`ByteSendStream` bridge with newline protocol for JSON-RPC. Starlette's WebSocket API is similar enough to `websockets` library that minimal changes are needed.

### D5: `StreamableHTTPTransport` dataclass with host/port

**Choice**: New `StreamableHTTPTransport(host, port)` dataclass. Named "StreamableHTTP" (not "WebSocket") because the RFC uses this name for the combined transport profile. WebSocket is the first profile we implement.

**Rationale**: Aligning naming with the RFC avoids confusion when Phase 2 adds the HTTP transport profile under the same configuration type.

## Risks / Trade-offs

- **[HTTP/1.1 deviation]** → RFC may require HTTP/2. Mitigation: HTTP/1.1 works for WebSocket upgrade. Document deviation. Migration path: swap uvicorn for Hypercorn (ASGI-compatible, zero app-code change).
- **[Starlette dependency]** → New dependency. Mitigation: Starlette is lightweight (no Pydantic dependency), already commonly available in ASGI ecosystems, and uvicorn is already in the project.
- **[Connection lifecycle complexity]** → Starlette WebSocket lifecycle differs from `websockets` library. Mitigation: Write integration tests covering connect/disconnect/abnormal-close scenarios.
- **[initialize enforcement may break existing clients]** → Clients that skip `initialize` will get errors. Mitigation: This is the correct behavior per RFC. The existing `_serve_websocket()` doesn't enforce it, so this is a net improvement, not a regression.
