---
rfc_id: RFC-0030
title: ACP Streamable HTTP WebSocket Transport
status: REVIEW
author: AgentPool Team
reviewers: []
created: 2026-05-13
last_updated: 2026-05-13
decision_date:
related_rfcs: []
---
# RFC-0030: ACP Streamable HTTP WebSocket Transport
## Overview
ACP agents in AgentPool currently communicate over stdio or a bare WebSocket server. Neither transport aligns with the ACP RFC's Streamable HTTP WebSocket Transport profile, which defines a network-accessible endpoint at `/acp` with connection identification, lifecycle enforcement, and HTTP/SSE extensibility. This RFC proposes adding a `StreamableHTTPTransport` built on Starlette and uvicorn that serves the ACP protocol over WebSocket at `/acp`, returns `Acp-Connection-Id` headers, enforces the `initialize` handshake, and integrates with the existing CLI and YAML configuration pipeline. Phase 1 covers WebSocket only; Streamable HTTP (POST/GET/DELETE with SSE) is deferred.
## Table of Contents
1. [Background & Context](#background--context)
2. [Problem Statement](#problem-statement)
3. [Goals & Non-Goals](#goals--non-goals)
4. [Evaluation Criteria](#evaluation-criteria)
5. [Options Analysis](#options-analysis)
6. [Recommendation](#recommendation)
7. [Technical Design](#technical-design)
8. [Security Considerations](#security-considerations)
9. [Implementation Plan](#implementation-plan)
10. [Open Questions](#open-questions)
11. [Decision Record](#decision-record)
12. [References](#references)
## Background & Context
The AgentPool ACP implementation (`src/acp/`) supports three transport modes today:
- **Stdio**: subprocess communication via stdin/stdout. The default and most widely used mode, suitable for IDEs that spawn AgentPool as a child process.
- **WebSocket**: a bare server using the `websockets` library, defined in `_serve_websocket()`. It accepts connections, wraps them in `AgentSideConnection`, and processes JSON-RPC messages.
- **Custom streams**: direct `ByteReceiveStream`/`ByteSendStream` injection.
The existing WebSocket transport has significant gaps when measured against the ACP RFC's Streamable HTTP WebSocket Transport profile (`streamable-http-websocket-transport.mdx`):
| Feature | RFC Requirement | Current Status |
|---------|----------------|----------------|
| `/acp` endpoint path | Required | Not enforced; binds to any host:port |
| `Acp-Connection-Id` header | Required on upgrade | Not returned |
| `initialize` lifecycle guard | Required | Not enforced |
| ASGI compatibility | Needed for HTTP routes | Uses raw `websockets` library |
| Extensible to SSE routes | Phase 2 requirement | No HTTP routing possible |
The ACP RFC defines a unified `/acp` endpoint supporting both WebSocket upgrade and Streamable HTTP (POST/GET/DELETE with SSE). Per the RFC, clients MUST support both transports, but servers MAY support only WebSocket. This justifies a phased approach: Phase 1 implements the WebSocket profile, Phase 2 adds Streamable HTTP.
Key existing components that this work builds on:
- `AgentSideConnection` takes `ByteSendStream` + `ByteReceiveStream` and handles the full JSON-RPC line protocol.
- `_WebSocketReadStream` / `_WebSocketWriteStream` adapters already bridge WebSocket frames to anyio byte streams.
- `ACPBridge` is an independent code path (stdio subprocess + HTTP proxy) that requires no changes.
- `uvicorn` is already a project dependency.
- `websockets` is already a project dependency.
- `starlette` would be a new dependency.
## Problem Statement
The current WebSocket transport implementation cannot serve as a compliant ACP server for remote clients. It lacks connection identification, lifecycle enforcement, and the ability to extend to HTTP routes for Streamable HTTP. This blocks several use cases:
1. **IDE integration over the network**: Tools like Zed and VS Code need to connect to an ACP agent running on a remote host or in a container, not just as a subprocess.
2. **Multi-agent orchestration across processes**: Agents running in separate processes or machines need a network transport to communicate.
3. **Protocol compliance**: The ACP RFC's Streamable HTTP WebSocket Transport profile is the standard. The current implementation deviates in multiple ways, making it incompatible with clients that follow the spec.
Without this change, AgentPool can only serve ACP agents via stdio or a non-compliant WebSocket server, limiting its integration surface to in-process or local-subprocess scenarios.
## Goals & Non-Goals
### Goals
- Implement a WebSocket server transport compliant with the ACP RFC's WebSocket profile.
- Return `Acp-Connection-Id` header (UUID v4) during WebSocket upgrade at `/acp`.
- Enforce `initialize` lifecycle: client must send `initialize` before other requests.
- Reuse existing `AgentSideConnection` for ACP protocol handling over WebSocket.
- Integrate with uvicorn/Starlette for production-grade ASGI serving.
- Extend `Transport` type union, `ACPServer`, CLI, and YAML config to support the new transport.
- Document HTTP/1.1 deviation from RFC's HTTP/2 requirement and provide migration path.
### Non-Goals
- Streamable HTTP transport (POST/GET/DELETE with SSE). This is deferred to Phase 2.
- Multi-session support over a single WebSocket connection. Phase 1 is one connection equals one session.
- HTTP/2 support. Migration to Hypercorn is planned later; zero app-code change expected.
- Changes to `ACPBridge` or stdio transport.
- Client-side implementation. This RFC covers server-side transport only.
- Authentication or authorization at the transport layer. Phase 1 assumes trusted network.
### Success Criteria
- [ ] ACP server can be started with `agentpool serve-acp config.yml --port 8080` and accepts WebSocket connections at `/acp`
- [ ] WebSocket upgrade response includes `Acp-Connection-Id` header with UUID v4
- [ ] Pre-initialize requests receive JSON-RPC error code `-32600`
- [ ] Post-initialize requests process normally through `AgentSideConnection`
- [ ] Connection cleanup calls `AgentSideConnection.close()` on graceful and abnormal disconnect
- [ ] YAML config `transport: streamable-http` resolves to `StreamableHTTPTransport` instance
- [ ] All integration tests pass
## Evaluation Criteria
The following criteria will be used to objectively evaluate each option:
| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| RFC Compliance | High | Alignment with the ACP RFC's Streamable HTTP WebSocket Transport profile: `/acp` endpoint, `Acp-Connection-Id` header, `initialize` enforcement | Must support all three features |
| Extensibility | High | Ability to add Streamable HTTP transport (POST/GET/DELETE with SSE) in Phase 2 without rewriting the server framework | Must support shared routing, middleware, and connection state |
| Implementation Cost | Medium | Development effort: lines of code, new concepts to learn, testing surface area | Must be achievable in a single sprint |
| Production Readiness | Medium | Middleware support (CORS, logging), monitoring hooks, graceful shutdown, connection lifecycle management | Must support graceful shutdown and at least one middleware |
| Dependency Impact | Low | New dependencies introduced and their cost: bundle size, supply-chain risk, version compatibility | Must not add dependencies with known CVEs |
## Options Analysis
### Option 1: Starlette + uvicorn (WebSocket-only, Phase 1)
**Description**: Build an ASGI application with a Starlette WebSocket endpoint at `/acp`. Serve via uvicorn. Generate `Acp-Connection-Id` header during upgrade. Enforce `initialize` lifecycle with a thin guard wrapping `AgentSideConnection`. Reuse existing stream adapter patterns (`_WebSocketReadStream`/`_WebSocketWriteStream`) adapted for Starlette's WebSocket API.
**Advantages**:
- Standard ASGI app enables adding HTTP routes (SSE, health checks) in Phase 2 without framework changes. Starlette's Router composes WebSocket and HTTP routes naturally.
- Middleware ecosystem is available immediately: CORS, logging, error handling, rate limiting. Any ASGI middleware works.
- uvicorn is already a project dependency, so the HTTP serving layer adds no new runtime requirement.
- Starlette's WebSocket API provides full control over upgrade response headers, making `Acp-Connection-Id` return straightforward.
- Production-grade: uvicorn handles process management, signal handling, and graceful shutdown out of the box.
**Disadvantages**:
- Introduces `starlette` as a new dependency. While lightweight and well-maintained, it adds to the dependency tree and supply-chain surface.
- Starlette's WebSocket API differs from the `websockets` library API, requiring new stream adapter implementations rather than reusing the existing ones directly.
- The ASGI abstraction adds a thin layer of indirection compared to the raw `websockets` library. For a single WebSocket endpoint, this indirection is arguably unnecessary in Phase 1.
- HTTP/1.1 only with uvicorn. The RFC may expect HTTP/2. Migration to Hypercorn is possible but introduces another server framework later.
**Evaluation Against Criteria**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| RFC Compliance | 4 (Good) | Full control over upgrade headers, path routing, and lifecycle. Can implement all RFC requirements. |
| Extensibility | 5 (Excellent) | ASGI app can add HTTP routes and middleware. Phase 2 Streamable HTTP is a route addition, not a rewrite. |
| Implementation Cost | 3 (Adequate) | New stream adapters needed for Starlette API. Moderate effort for ASGI app scaffolding. |
| Production Readiness | 4 (Good) | Middleware, graceful shutdown, and monitoring all available through ASGI/uvicorn ecosystem. |
| Dependency Impact | 3 (Adequate) | One new dependency (starlette). Lightweight, no Pydantic requirement, widely used. |
**Effort Estimate**: Medium. Approximately 200-300 lines of new code for the ASGI app, stream adapters, initialize guard, and transport config. Integration with existing ACPServer and CLI is mechanical.
**Risk Assessment**:
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Starlette API drift between versions | Low | Low | Pin starlette version in pyproject.toml; Starlette follows semantic versioning. |
| HTTP/1.1 deviation causes client issues | Low | Medium | Document deviation; WebSocket upgrade works over HTTP/1.1; migration path to Hypercorn. |
| Stream adapter bugs in Starlette-WebSocket-to-anyio bridge | Medium | Medium | Write integration tests covering connect, disconnect, and abnormal-close scenarios. |
| Starlette adds transitive dependency weight | Low | Low | Starlette has minimal dependencies (no Pydantic). Audit dependency tree before pinning. |
---
### Option 2: Raw `websockets` library (enhance existing)
**Description**: Extend the current `_serve_websocket()` implementation to add `Acp-Connection-Id` header return and `initialize` enforcement. No ASGI framework. The `websockets` library provides hooks for custom headers during upgrade via its `process_request` callback.
**Advantages**:
- No new dependencies. The `websockets` library is already in the project.
- Minimal code changes to the existing `_serve_websocket()`. Incremental enhancement of working code.
- Familiar codebase. The team already understands the `websockets` library patterns used in the current implementation.
- Lower risk of regression since the existing implementation continues to work; changes are additive.
**Disadvantages**:
- No path to Streamable HTTP. The `websockets` library handles only WebSocket connections. Adding HTTP routes for SSE in Phase 2 would require a separate HTTP server framework, resulting in two parallel server implementations.
- No ASGI middleware. CORS, logging, rate limiting, and other cross-cutting concerns must be implemented manually as custom code within the `websockets` handler.
- The `websockets` library's `process_request` hook for custom headers is less ergonomic than Starlette's direct header access. It requires understanding the library's internal upgrade flow.
- Two server frameworks in the codebase (raw `websockets` for legacy, ASGI for Phase 2) create maintenance burden and conceptual overhead.
- Limited production tooling. The `websockets` library does not provide process management, signal handling, or monitoring hooks comparable to uvicorn.
**Evaluation Against Criteria**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| RFC Compliance | 3 (Adequate) | Can add `Acp-Connection-Id` and initialize guard, but no path routing enforcement. |
| Extensibility | 1 (Inadequate) | Adding HTTP routes requires a separate server. Two frameworks in the codebase. |
| Implementation Cost | 5 (Excellent) | Smallest change delta. Enhance existing code. |
| Production Readiness | 2 (Poor) | No middleware, no process management, limited monitoring. Must build manually. |
| Dependency Impact | 5 (Excellent) | No new dependencies. |
**Effort Estimate**: Low. Approximately 50-80 lines of changes to existing `_serve_websocket()` for header return and initialize guard.
**Risk Assessment**:
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Phase 2 requires full rewrite | High | High | Accept technical debt now, plan rewrite later. Or reject this option. |
| Custom header hook API is fragile | Medium | Medium | Write tests for `process_request` hook; pin `websockets` version. |
| Dual-framework maintenance burden | High | Medium | Document both code paths; plan deprecation of raw `websockets` server. |
| Missing middleware for production use | High | Medium | Build custom middleware as needed, increasing code in transports.py. |
---
### Option 3: FastAPI + uvicorn
**Description**: Use FastAPI's WebSocket support with Pydantic models for protocol message validation. Define request/response schemas for `initialize`, `session/new`, and other ACP methods. Serve via uvicorn.
**Advantages**:
- Automatic OpenAPI documentation generation for any HTTP routes added in Phase 2.
- Pydantic validation on JSON-RPC messages catches malformed requests before they reach `AgentSideConnection`.
- FastAPI is widely known in the Python ecosystem, potentially reducing onboarding time.
- Same ASGI foundation as Option 1, so middleware and routing benefits apply.
**Disadvantages**:
- FastAPI's WebSocket support does not perform Pydantic validation on WebSocket messages. The validation benefit is limited to HTTP routes, not the WebSocket transport that Phase 1 actually needs.
- Adds both `fastapi` and its `pydantic` dependency (if not already present at the required version). This is heavier than Starlette alone.
- FastAPI wraps Starlette, adding an abstraction layer that provides no value for a pure WebSocket endpoint. The overhead shows up in import time, startup time, and complexity.
- `AgentSideConnection` already handles JSON-RPC message parsing and validation. Adding Pydantic validation at the transport layer duplicates this logic and creates two validation points that can drift out of sync.
- FastAPI's opinionated patterns (dependency injection, path operation decorators) add ceremony that a single WebSocket endpoint does not need.
**Evaluation Against Criteria**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| RFC Compliance | 4 (Good) | Same header control and routing as Starlette. |
| Extensibility | 5 (Excellent) | Full ASGI app with HTTP routes and middleware. |
| Implementation Cost | 2 (Poor) | FastAPI boilerplate plus Pydantic message models that duplicate existing validation. |
| Production Readiness | 4 (Good) | ASGI middleware and uvicorn benefits, plus OpenAPI docs. |
| Dependency Impact | 2 (Poor) | Two new dependencies: fastapi plus pydantic version alignment. |
**Effort Estimate**: Medium-High. Approximately 250-350 lines for the FastAPI app, Pydantic message models, and stream adapters. The Pydantic models for JSON-RPC messages add significant upfront work that duplicates existing validation.
**Risk Assessment**:
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Pydantic validation drift from AgentSideConnection | High | Medium | Keep validation in one place (AgentSideConnection); don't duplicate in FastAPI. But this removes the primary benefit of Option 3. |
| FastAPI version coupling with Pydantic v2 | Medium | Medium | Pin both versions; test upgrade paths. |
| Unnecessary abstraction for a WebSocket endpoint | High | Low | Accept added complexity, or simplify to Starlette. |
| Import/startup time regression | Low | Low | Measure impact; FastAPI's import chain is heavier than Starlette's. |
---
### Options Comparison Summary
| Criterion | Weight | Option 1 (Starlette) | Option 2 (Raw websockets) | Option 3 (FastAPI) |
|-----------|--------|----------------------|--------------------------|-------------------|
| RFC Compliance | High | 4 | 3 | 4 |
| Extensibility | High | 5 | 1 | 5 |
| Implementation Cost | Medium | 3 | 5 | 2 |
| Production Readiness | Medium | 4 | 2 | 4 |
| Dependency Impact | Low | 3 | 5 | 2 |
| **Weighted Total** | | **3.9** | **2.8** | **3.6** |
---
## Recommendation
### Recommended Option
**Option 1: Starlette + uvicorn**
### Justification
The deciding factor is Extensibility, weighted High. Phase 2 requires adding Streamable HTTP routes (POST/GET/DELETE with SSE) alongside the WebSocket endpoint. Starlette's ASGI Router composes these naturally: the WebSocket route at `/acp` and HTTP routes at the same path share middleware, connection state, and the server framework. Option 2 (raw `websockets`) cannot serve HTTP routes, meaning Phase 2 would require introducing a second server framework or rewriting the transport entirely. Option 3 (FastAPI) provides the same extensibility but adds Pydantic validation overhead that the WebSocket transport cannot use, since FastAPI does not validate WebSocket message payloads.
On RFC Compliance, Options 1 and 3 score equally (4/5), while Option 2 scores lower (3/5) due to the inability to enforce path-based routing cleanly.
On Implementation Cost, Option 2 scores best (5/5) because it is the smallest change. However, this cost advantage is temporary: the Phase 2 rewrite needed under Option 2 would exceed the total cost of Option 1's Phase 1 plus Phase 2.
On Production Readiness, Option 1 scores well (4/5) due to ASGI middleware support, uvicorn's process management, and graceful shutdown. Option 2 scores poorly (2/5) because these capabilities must be built manually.
On Dependency Impact, Option 2 has no new dependencies. Option 1 adds starlette (lightweight, no Pydantic). Option 3 adds both fastapi and pydantic version constraints. Starlette is the smallest addition among the options that provide ASGI extensibility.
### Accepted Trade-offs
1. **HTTP/1.1 deviation**: Acceptable because WebSocket upgrade works over HTTP/1.1. The deviation is functionally harmless for Phase 1. Migration to Hypercorn (ASGI-compatible HTTP/2 server) requires zero app-code changes; only the server runner call changes.
2. **New starlette dependency**: Acceptable because starlette is lightweight (~30KB, no Pydantic), well-maintained (sibling project of FastAPI), and provides the ASGI foundation needed for Phase 2. The alternative of maintaining two server frameworks (Option 2 + separate HTTP server for Phase 2) carries higher long-term cost.
### Conditions
- HTTP/1.1 deviation must be documented in server startup logs.
- starlette dependency must be audited for transitive dependencies before pinning.
- Initialize enforcement must be configurable (allow opt-out for backward compatibility during migration).
---
## Technical Design
### Architecture Overview
```
                          ACP Streamable HTTP WebSocket Transport
                          ======================================
  Client (IDE, CLI, etc.)
       |
       |  HTTP/1.1 Upgrade: ws://host:port/acp
       |  Response Header: Acp-Connection-Id: <uuid-v4>
       v
  +----------------------------------------------------------+
  |                   Starlette ASGI App                      |
  |                                                           |
  |  WebSocket Route: /acp                                    |
  |  +-----------------------------------------------------+ |
  |  |                                                     | |
  |  |  1. Accept upgrade, generate Acp-Connection-Id     | |
  |  |  2. Initialize Guard (track state per connection)  | |
  |  |     - First message MUST be "initialize"           | |
  |  |     - Pre-initialize requests -> error -32600      | |
  |  |  3. Create stream adapters                         | |
  |  |     - _StarletteWebSocketReadStream -> ByteRecv    | |
  |  |     - _StarletteWebSocketWriteStream -> ByteSend   | |
  |  |  4. Create AgentSideConnection(agent_factory, ...) | |
  |  |  5. Monitor connection lifecycle                   | |
  |  |     - Graceful close -> AgentSideConnection.close()| |
  |  |     - Abnormal close -> AgentSideConnection.close()| |
  |  +-----------------------------------------------------+ |
  |                                                           |
  |  (Phase 2: HTTP Route: /acp for Streamable HTTP/SSE)     |
  +----------------------------------------------------------+
       |
       |  uvicorn serves the ASGI app
       v
  Network (host:port)
```
### Key Components
#### StreamableHTTPTransport
- Responsibility: Configuration dataclass for the transport
- Fields: `host: str = "localhost"`, `port: int = 8080`
- Type: `@dataclass`
#### _StarletteWebSocketReadStream
- Responsibility: Adapt Starlette WebSocket receive to `ByteReceiveStream`
- Pattern: Wraps `WebSocket.receive_text()`, appends trailing newline for JSON-RPC line protocol
- Base: `ByteReceiveStream`
#### _StarletteWebSocketWriteStream
- Responsibility: Adapt Starlette WebSocket send to `ByteSendStream`
- Pattern: Strips trailing newline, sends as complete WebSocket text message via `WebSocket.send_text()`
- Base: `ByteSendStream`
#### Initialize Guard
- Responsibility: Enforce `initialize` as first message after WebSocket upgrade
- State: `initialized: bool` per connection
- Behavior: Reject non-initialize requests with JSON-RPC error `-32600`; pass through after successful initialize
### Data Model
```python
@dataclass
class StreamableHTTPTransport:
    """Configuration for Streamable HTTP WebSocket transport.
    Implements the ACP RFC's Streamable HTTP WebSocket Transport
    profile. Phase 1 covers WebSocket only; Streamable HTTP
    (POST/GET/DELETE with SSE) is planned for Phase 2.
    Attributes:
        host: Host to bind the server to.
        port: Port for the server.
    """
    host: str = "localhost"
    port: int = 8080
```
### Type Union Extension
```python
Transport = (
    StdioTransport
    | WebSocketTransport
    | StreamTransport
    | StreamableHTTPTransport
    | Literal["stdio", "websocket", "streamable-http"]
)
```
The `"streamable-http"` string literal normalizes to `StreamableHTTPTransport()` with defaults.
### API Design
#### WebSocket Upgrade
```
GET /acp HTTP/1.1
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <key>
Sec-WebSocket-Version: 13
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: <accept>
Acp-Connection-Id: 550e8400-e29b-41d4-a716-446655440000
```
#### Initialize Lifecycle Flow
```
Client connects via WebSocket upgrade at /acp
        |
        v
  +------------------+
  | initialized=False |
  +------------------+
        |
        v
  Receive first JSON-RPC message
        |
        +---> method == "initialize" ?
        |          |
        |     YES  |         NO
        |          v          v
        |   Pass to       Return JSON-RPC error:
        |   AgentSide     {"jsonrpc":"2.0","error":
        |   Connection      {"code":-32600,
        |                    "message":"initialize required"},
        |                   "id":<request_id>}
        |          |
        |          v
        |   Initialize succeeds?
        |     |
        |  YES |      NO
        |     v        v
        |  Set         Connection remains
        |  initialized  uninitialized;
        |  = True       client may retry
        |     |
        v     v
  Subsequent messages pass through
  to AgentSideConnection unmodified
```
### CLI Integration
```
agentpool serve-acp config.yml                     # Default: stdio transport
agentpool serve-acp config.yml --port 8080          # WebSocket on localhost:8080
agentpool serve-acp config.yml --host 0.0.0.0 --port 9000  # WebSocket on 0.0.0.0:9000
```
When `--port` is provided, the CLI creates `StreamableHTTPTransport(host=host, port=port)` and passes it to `ACPServer.from_config()`. When neither flag is provided, the default stdio transport is used, preserving current behavior.
### YAML Config Schema Extension
```yaml
pool_server:
  transport: streamable-http    # or "stdio" (default)
  host: "0.0.0.0"              # optional, defaults to "localhost"
  port: 8080                    # optional, defaults to 8080
```
When `transport: streamable-http` is specified, `ACPServer.from_config()` resolves the config to a `StreamableHTTPTransport` instance with the provided or default host and port values.
---
## Security Considerations
### Threat Analysis
| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| Unauthenticated network access | High | High (if bound to 0.0.0.0) | Default to localhost; document risk of 0.0.0.0 binding |
| WebSocket connection flood (DoS) | Medium | Medium | Add `max_connections` parameter; reject with HTTP 503 when exceeded |
| Unencrypted transport (ws://) | High | Medium | Support TLS via uvicorn flags; recommend reverse proxy for production |
| CORS bypass for future SSE routes | Medium | Low | Add `cors_origins` config parameter; apply Starlette CORSMiddleware |
### Security Measures
- [ ] Default bind to `localhost`; document `0.0.0.0` risk
- [ ] Implement `max_connections` limit with configurable default (100)
- [ ] Support TLS via `--ssl-keyfile`/`--ssl-certfile` CLI flags
- [ ] Add `cors_origins` parameter for Phase 2 SSE routes
- [ ] Log connection ID on connect/disconnect for audit trail
### Compliance
No regulatory requirements apply in Phase 1. Production deployments should run behind a TLS-terminating reverse proxy with authentication middleware.
---
## Implementation Plan
### Phase 1: Transport Config + ASGI WebSocket Server
- **Scope**: New transport type, stream adapters, initialize guard, ASGI app
- **Deliverables**: `StreamableHTTPTransport` dataclass, `_StarletteWebSocketReadStream`/`_StarletteWebSocketWriteStream` adapters, initialize guard, Starlette ASGI app with `/acp` WebSocket route
- **Dependencies**: None (can start immediately)
Tasks:
- 1.1: Add `StreamableHTTPTransport` dataclass to `src/acp/transports.py`
- 1.2: Extend `Transport` type union
- 1.3: Add `"streamable-http"` normalization case in `serve()`
- 1.4: Add `StreamableHTTPTransport` dispatch case in `serve()`
- 2.1: Create `_StarletteWebSocketReadStream` adapter
- 2.2: Create `_StarletteWebSocketWriteStream` adapter
- 2.3: Implement initialize lifecycle guard
- 2.4: Create Starlette ASGI app with `/acp` WebSocket route
- 2.5: Implement connection cleanup
### Phase 2: Server Runner + ACPServer Integration
- **Scope**: Server runner function, dependency addition, ACPServer integration
- **Deliverables**: `_serve_streamable_http()` function, starlette dependency, verified ACPServer passthrough
- **Dependencies**: Phase 1 complete
Tasks:
- 3.1: Implement `_serve_streamable_http()` function
- 3.2: Add `starlette` dependency to `pyproject.toml`
- 3.3: Verify shutdown_event integration
- 4.1: Verify `ACPServer.__init__()` type hints
- 4.2: Verify `ACPServer.from_config()` type hints
- 4.3: Verify `_start_async()` transport passthrough
### Phase 3: CLI + YAML Config
- **Scope**: CLI flags, YAML schema extension
- **Deliverables**: `--port`/`--host` CLI flags, YAML `transport: streamable-http` support
- **Dependencies**: Phase 2 complete
Tasks:
- 5.1: Add `--port` and `--host` CLI flags
- 5.2: Create `StreamableHTTPTransport` from CLI flags
- 5.3: Default to stdio when no transport flags
- 6.1: Extend `PoolServerConfig` for `streamable-http`
- 6.2: Resolve YAML config to `StreamableHTTPTransport`
### Phase 4: Testing
- **Scope**: Unit and integration tests
- **Deliverables**: Test suite covering all requirements
- **Dependencies**: Phases 1-3 complete
Tasks:
- 7.1: Unit test `StreamableHTTPTransport` dataclass
- 7.2: Unit test `serve()` dispatch
- 7.3: Integration test: WebSocket upgrade returns `Acp-Connection-Id`
- 7.4: Integration test: initialize lifecycle enforcement
- 7.5: Integration test: stream adapter round-trip
- 7.6: Integration test: connection cleanup on disconnect
- 7.7: Integration test: CLI flags
- 7.8: LSP diagnostics clean on all changed files
### Milestones
| Milestone | Description | Target | Status |
|-----------|-------------|--------|--------|
| M1 | ASGI WebSocket server working end-to-end | Week 1 | Not Started |
| M2 | Server runner + ACPServer integration | Week 1-2 | Not Started |
| M3 | CLI + YAML config complete | Week 2 | Not Started |
| M4 | All tests passing | Week 2-3 | Not Started |
### Rollback Strategy
The new transport is opt-in (requires `--port` flag or `transport: streamable-http` in YAML). Removing the feature requires:
1. Revert CLI flag additions
2. Revert YAML config model changes
3. Remove `_serve_streamable_http()` and `StreamableHTTPTransport`
4. Remove starlette dependency
No existing functionality is affected since stdio remains the default.
---
## Open Questions
1. **HTTP/2 migration timeline**
   - Context: The RFC may expect HTTP/2. uvicorn supports HTTP/1.1 only. Hypercorn is an ASGI-compatible HTTP/2 server that could replace uvicorn with zero app-code changes.
   - Owner: Team lead
   - Status: Open
2. **Multi-session over single connection design**
   - Context: Phase 1 maps one WebSocket connection to one agent session. The RFC allows multiple sessions over a single connection. The initialize guard's state model may need extension.
   - Owner: Architect
   - Status: Open
3. **Authentication/authorization at the transport layer**
   - Context: Phase 1 has no auth. What mechanisms should Phase 2 support (API key, JWT, mTLS)? Should middleware pipeline be designed now?
   - Owner: Security lead
   - Status: Open
4. **Starlette version pinning strategy**
   - Context: Starlette follows semantic versioning but is developed alongside FastAPI, which can create pressure for rapid minor releases.
   - Owner: DevOps
   - Status: Open
---
## Decision Record
> Complete this section after RFC review is concluded.
### Decision
**Status**: Pending review
**Date**: TBD
**Approvers**: TBD
### Key Discussion Points
1. Framework choice: Starlette vs raw websockets vs FastAPI
2. HTTP/1.1 deviation acceptance
3. Phase 1 scope boundaries (no auth, no multi-session)
4. Initialize enforcement backward compatibility
### Conditions of Approval
- HTTP/1.1 deviation accepted with documented migration path
- starlette dependency audited for transitive dependencies
- Initialize enforcement has opt-out mechanism for migration period
### Dissenting Opinions
None recorded yet.
---
## References
### Related Documents
- OpenSpec proposal: `openspec/changes/acp-streamable-http-ws-server/proposal.md`
- OpenSpec design: `openspec/changes/acp-streamable-http-ws-server/design.md`
- OpenSpec ws-transport spec: `openspec/changes/acp-streamable-http-ws-server/specs/ws-transport/spec.md`
- OpenSpec ws-server-integration spec: `openspec/changes/acp-streamable-http-ws-server/specs/ws-server-integration/spec.md`
- OpenSpec tasks: `openspec/changes/acp-streamable-http-ws-server/tasks.md`
### External Resources
- ACP RFC: Streamable HTTP WebSocket Transport profile (`streamable-http-websocket-transport.mdx`)
- Starlette documentation: https://www.starlette.io/
- uvicorn documentation: https://www.uvicorn.org/
- Hypercorn documentation: https://hypercorn.readthedocs.io/
### Appendix
Current transport code: `src/acp/transports.py`
ACP server: `src/agentpool_server/acp_server/server.py`
