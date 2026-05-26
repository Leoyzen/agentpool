# RFC-0030 implementation plan

## Goal

Implement the Phase 1 server-side WebSocket subset of the ACP Streamable HTTP WebSocket Transport profile described in `docs/rfcs/draft/RFC-0030-acp-streamable-http-websocket-transport.md`, using the existing ACP stack and repo patterns.

## TODOs

- [x] Phase 0 - settle two code-level decisions before editing broadly
- [x] Phase 1 - add the new transport type and server runtime
- [x] Phase 2 - implement lifecycle enforcement and cleanup guarantees
- [x] Phase 3 - wire YAML config and CLI to the new transport
- [x] Phase 4 - tests and verification

## Final Verification Wave

- [x] F1: New tests pass (41/41)
- [x] F2: Existing ACP RPC tests pass (9/9, no regressions)
- [x] F3: ruff lint clean on all changed files
- [x] F4: lsp_diagnostics clean on all modified files

## Grounded decisions from the current codebase

1. **Keep the new transport in `src/acp/transports.py`.**
   That file already owns transport dataclasses, string normalization, transport dispatch, and the legacy WebSocket server.

2. **Place the initialize guard on the agent side, not in generic `Connection`.**
   `src/acp/connection.py` is shared protocol infrastructure. The guard is specific to server-side agent lifecycle, so it belongs in `src/acp/agent/connection.py` near `_agent_handler()` or a small wrapper around request execution.

3. **Use Starlette + uvicorn only for the new transport path.**
   `uvicorn` is already a core dependency and the repo already instantiates `uvicorn.Server` directly in multiple servers. `starlette` should be promoted from optional-only usage to a core dependency if ACP transport is intended as a first-class server feature.

4. **Treat shutdown bridging as explicit work, not incidental behavior.**
   `ACPServer._start_async()` already passes `self._shutdown_event` into `acp.serve()`, but `BaseServer.stop()` cancels the server task without setting that event. The new transport needs an ACP-side shutdown path that closes connections deterministically.

5. **Update the Toad helper path during the same change.**
   `src/agentpool_cli/ui.py` still shells out to `serve-acp --transport websocket --ws-port ...`; leaving that unchanged would preserve an internal caller on the deprecated transport.

## Files to touch

### Transport and protocol

- `src/acp/transports.py`
- `src/acp/__init__.py`
- `src/acp/agent/connection.py`

### Server integration

- `src/agentpool_server/acp_server/server.py`
- `src/agentpool_server/base.py` or an ACP-specific override in `server.py`

### Config and CLI

- `src/agentpool_config/pool_server.py`
- `src/agentpool_cli/serve_acp.py`
- `src/agentpool_cli/ui.py`

### Dependency management

- `pyproject.toml`

### Tests

- `tests/acp/test_streamable_http_transport.py` (new)
- `tests/servers/acp_server/test_streamable_http_integration.py` (new)
- `tests/cli/test_serve_acp_streamable_http.py` (new or merged into existing CLI coverage)

## Implementation phases

### Phase 0 - settle two code-level decisions before editing broadly

1. **Initialize guard location**
   - Preferred: add per-connection initialized state inside `AgentSideConnection`.
   - Reject: modifying generic `Connection` lifecycle in a way that also affects non-server/client paths.

2. **Shutdown behavior ownership**
   - Preferred: make `ACPServer.stop()` set `_shutdown_event` before delegating to base stop behavior, or otherwise ensure the serving coroutine sees the event before cancellation.
   - Verify this against current `BaseServer.start_in_background()` / `stop()` semantics before implementation.

Exit criteria:
- We know exactly where the initialize guard flips from false to true.
- We know exactly which stop path triggers uvicorn shutdown and connection cleanup.

### Phase 1 - add the new transport type and server runtime

1. Add `ACPWebSocketTransport` to `src/acp/transports.py` with:
   - `host: str = "localhost"`
   - `port: int = 8080`

2. Extend `Transport` to include:
   - `ACPWebSocketTransport`
   - `Literal["streamable-http"]`

3. Extend `serve()` normalization so:
   - `"stdio"` -> `StdioTransport()`
   - `"websocket"` -> legacy `WebSocketTransport()`
   - `"streamable-http"` -> `ACPWebSocketTransport()`

4. Add a new dispatch arm for `ACPWebSocketTransport` calling `_serve_streamable_http(...)`.

5. Implement `_serve_streamable_http(...)` in `src/acp/transports.py`:
   - Build a Starlette app with a WebSocket route at `/acp`.
   - Instantiate `uvicorn.Server(uvicorn.Config(...))` directly, matching current repo practice.
   - Bridge the passed `shutdown_event` to `server.should_exit = True`.
   - Keep a local set of active `AgentSideConnection` objects for final cleanup.

6. Add Starlette-specific stream adapters:
   - `_StarletteWebSocketReadStream`
   - `_StarletteWebSocketWriteStream`
   Requirements:
   - `receive_text()` / `send_text()` based transport
   - newline compatibility with the existing JSON-RPC line protocol
   - `WebSocketDisconnect` translated to `anyio.EndOfStream`

7. During handshake, return `Acp-Connection-Id` using `websocket.accept(headers=[...])`.

Exit criteria:
- The new transport can be selected internally.
- A Starlette-backed server can accept a WebSocket at `/acp` and create an `AgentSideConnection`.

### Phase 2 - implement lifecycle enforcement and cleanup guarantees

1. Add per-connection initialized state on the agent side.

2. Reject any request before `initialize` with JSON-RPC error `-32002`.
   - Keep behavior request-scoped.
   - Do not add byte-stream interception.

3. Mark the connection initialized only after a successful `initialize` handling path.
   - If initialize fails, keep the connection uninitialized.

4. Ensure endpoint cleanup is symmetric:
   - graceful disconnect
   - `WebSocketDisconnect`
   - uvicorn shutdown
   - task cancellation / abnormal close

5. Call `AgentSideConnection.close()` exactly once per live connection path.

Exit criteria:
- Non-initialize requests fail correctly before session bootstrap.
- Disconnect and shutdown paths do not leak live ACP connections.

### Phase 3 - wire YAML config and CLI to the new transport

1. Extend `ACPPoolServerConfig` in `src/agentpool_config/pool_server.py` with:
   - `transport: Literal["stdio", "streamable-http"] = "stdio"`
   - `host: str = "localhost"`
   - `port: int = 8080`

2. Update `ACPServer.from_config()` so YAML `pool_server.transport: streamable-http` resolves to `ACPWebSocketTransport(host, port)`.

3. Update `src/agentpool_cli/serve_acp.py`:
   - extend transport choices to `stdio | websocket | streamable-http`
   - add `--host` and `--port` for the new transport
   - keep `--ws-host` and `--ws-port` for legacy compatibility only
   - emit a deprecation warning when `--transport websocket` is used

4. Update `src/agentpool_cli/ui.py` so Toad helper startup migrates to the new transport unless there is a concrete compatibility reason not to.

5. Re-export the new transport from `src/acp/__init__.py` if external imports rely on the public transport surface.

Exit criteria:
- CLI and YAML both resolve to the same transport object model.
- Internal helper flows stop depending on the deprecated transport.

### Phase 4 - tests and verification

#### Unit coverage

1. Transport normalization and dispatch
   - `"streamable-http"` resolves correctly
   - legacy `"websocket"` still resolves, but warns

2. `ACPWebSocketTransport` defaults
   - host default `localhost`
   - port default `8080`

3. Initialize guard behavior
   - pre-initialize non-initialize request => `-32002`
   - successful initialize flips state
   - failed initialize does not flip state

#### Integration coverage

1. WebSocket connection to `/acp` succeeds.
2. Handshake includes `Acp-Connection-Id`.
3. Post-initialize normal ACP requests still flow through `AgentSideConnection`.
4. `WebSocketDisconnect` maps cleanly to end-of-stream behavior.
5. Connection shutdown closes `AgentSideConnection`.
6. `ACPServer` stop path triggers orderly server shutdown.
7. CLI transport selection and warnings behave as expected.
8. YAML config resolution produces the correct runtime transport.

#### Verification commands

Run at minimum:

1. `uv run pytest tests/acp/test_streamable_http_transport.py tests/servers/acp_server/test_streamable_http_integration.py tests/cli/test_serve_acp_streamable_http.py`
2. `uv run --no-group docs mypy src/`
3. `uv run ruff check src/ tests/`
4. `uv run pytest` if targeted tests pass cleanly and change surface stays moderate.

Also run `lsp_diagnostics` on all touched files before closing the work.

## Sequencing notes for implementation

1. Start with transport/runtime plumbing in `src/acp/transports.py`.
2. Add the agent-side initialize guard next, because its behavior shapes integration tests.
3. Then wire config and CLI.
4. Update the Toad helper before considering the migration complete.
5. Finish with tests, because shutdown semantics will likely require one adjustment pass.

## Known risks and mitigations

### Risk 1: shutdown race between event signaling and task cancellation

Why it matters:
- Current server base behavior may cancel before uvicorn exits cleanly.

Mitigation:
- Make the ACP server stop path explicitly signal shutdown first.
- Integration-test shutdown through the real server object, not only the transport helper.

### Risk 2: initialize guard leaks into non-agent connection paths

Why it matters:
- `Connection` is shared plumbing.

Mitigation:
- Keep guard state in `AgentSideConnection` or a wrapper only used by the server.

### Risk 3: hidden legacy dependency on `--transport websocket`

Why it matters:
- Internal helper code already uses it.

Mitigation:
- Migrate `src/agentpool_cli/ui.py` in the same change.
- Grep for remaining websocket transport literals before finishing.

### Risk 4: dependency scope mismatch for `starlette`

Why it matters:
- ACP server should not rely on an unrelated optional extra.

Mitigation:
- Promote `starlette` to the main dependency set if this RFC is accepted for core ACP functionality.

## Definition of done

The work is done when all of the following are true:

1. `agentpool serve-acp ... --transport streamable-http` serves `/acp` successfully.
2. The handshake returns `Acp-Connection-Id`.
3. Pre-initialize requests fail with `-32002`.
4. Post-initialize ACP traffic still uses existing `AgentSideConnection` flow.
5. Shutdown closes live connections deterministically.
6. YAML config can select the new transport.
7. Legacy websocket transport warns clearly and internal helpers stop depending on it.
8. Targeted tests, type-checking, lint, and diagnostics are clean.
