## 1. Transport Configuration

- [ ] 1.1 Add `StreamableHTTPTransport` dataclass to `src/acp/transports.py` with `host: str = "localhost"` and `port: int = 8080` fields
- [ ] 1.2 Extend `Transport` type union to include `StreamableHTTPTransport | Literal["streamable-http"]`
- [ ] 1.3 Add `"streamable-http"` → `StreamableHTTPTransport()` normalization case in `serve()` match statement
- [ ] 1.4 Add `StreamableHTTPTransport` dispatch case in `serve()` match statement calling `_serve_streamable_http()`

## 2. ASGI WebSocket Server

- [ ] 2.1 Create `_StarletteWebSocketReadStream(ByteReceiveStream)` adapter class in `src/acp/transports.py` — wraps Starlette `WebSocket` receive as `ByteReceiveStream` with newline protocol
- [ ] 2.2 Create `_StarletteWebSocketWriteStream(ByteSendStream)` adapter class in `src/acp/transports.py` — wraps Starlette `WebSocket` send as `ByteSendStream` stripping trailing newlines
- [ ] 2.3 Implement `initialize` lifecycle guard — wrapper that tracks initialization state, rejects pre-initialize requests with JSON-RPC error code `-32600`, passes through all requests after successful `initialize`
- [ ] 2.4 Create Starlette ASGI app with WebSocket route at `/acp` — generates `Acp-Connection-Id` (UUID v4), returns it in upgrade response headers, creates `AgentSideConnection` per connection with initialize guard
- [ ] 2.5 Implement connection cleanup — call `AgentSideConnection.close()` on WebSocket disconnect (graceful and abnormal), remove from active connections list

## 3. Server Runner

- [ ] 3.1 Implement `_serve_streamable_http()` function in `src/acp/transports.py` — creates Starlette app, runs via uvicorn with configured host/port, handles shutdown event integration
- [ ] 3.2 Add `starlette` dependency to project (pyproject.toml) — uvicorn already available
- [ ] 3.3 Verify `_serve_streamable_http()` respects `shutdown_event` parameter for clean shutdown

## 4. ACPServer Integration

- [ ] 4.1 Update `ACPServer.__init__()` transport parameter type hint to include `StreamableHTTPTransport` and `"streamable-http"` (already handled by `Transport` union, verify no breakage)
- [ ] 4.2 Update `ACPServer.from_config()` transport parameter type hint similarly
- [ ] 4.3 Verify `_start_async()` passes `self.transport` correctly to `acp.serve()` for the new transport type

## 5. CLI Integration

- [ ] 5.1 Add `--port` and `--host` optional flags to `agentpool serve-acp` CLI command
- [ ] 5.2 When `--port` is provided, create `StreamableHTTPTransport(host=host, port=port)` and pass to `ACPServer.from_config()`
- [ ] 5.3 When neither flag is provided, default to stdio transport (preserve current behavior)

## 6. YAML Config Schema

- [ ] 6.1 Extend `PoolServerConfig` (or relevant config model in `agentpool_config/`) to accept `transport: streamable-http` with optional `host` and `port` fields
- [ ] 6.2 In `ACPServer.from_config()`, resolve YAML `transport: streamable-http` config to `StreamableHTTPTransport` instance

## 7. Testing

- [ ] 7.1 Unit test: `StreamableHTTPTransport` dataclass construction and `Transport` union type narrowing
- [ ] 7.2 Unit test: `serve()` dispatch for `"streamable-http"` literal and `StreamableHTTPTransport` instance
- [ ] 7.3 Integration test: WebSocket upgrade returns `Acp-Connection-Id` header
- [ ] 7.4 Integration test: `initialize` lifecycle enforcement — reject pre-initialize requests, allow post-initialize requests
- [ ] 7.5 Integration test: Stream adapters — message round-trip through `AgentSideConnection`
- [ ] 7.6 Integration test: Connection cleanup on disconnect — `AgentSideConnection.close()` called
- [ ] 7.7 Integration test: CLI `--port` and `--host` flags create correct transport
- [ ] 7.8 Verify `lsp_diagnostics` clean on all changed files
