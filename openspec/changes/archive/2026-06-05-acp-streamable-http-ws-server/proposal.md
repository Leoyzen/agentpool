## Why

ACP agents currently only support stdio transport, requiring clients to spawn the agent as a subprocess. The ACP RFC's Streamable HTTP WebSocket Transport spec defines a remote transport profile that enables agents to be accessed over the network via WebSocket connections. This is essential for IDE integration (Zed, VS Code), multi-agent orchestration across processes, and any scenario where the client and agent are not in the same process tree.

## What Changes

- Add `StreamableHTTPTransport` transport type to `src/acp/transports.py` with host/port configuration
- Implement a WebSocket server in `src/acp/transports.py` that serves ACP protocol over a single `/acp` endpoint
- Return `Acp-Connection-Id` header during WebSocket upgrade handshake
- Enforce `initialize` lifecycle (client must send `initialize` before other requests)
- Integrate with `AgentSideConnection` for full ACP protocol handling over WebSocket
- Extend `Transport` type union to include `StreamableHTTPTransport` and `"streamable-http"` literal
- Add CLI support: `agentpool serve-acp config.yml --port 8080` triggers the new transport
- Update `ACPServer.from_config()` and `ACPServer.__init__()` to accept the new transport type

## Capabilities

### New Capabilities
- `ws-transport`: WebSocket server transport for ACP protocol — connection lifecycle (upgrade, initialize, teardown), `Acp-Connection-Id` header, stream adapters bridging WebSocket frames to `ByteReceiveStream`/`ByteSendStream`
- `ws-server-integration`: Integration of the WebSocket transport into the existing ACPServer/CLI/config pipeline — transport dispatch, CLI flags, config schema extension

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **Code**: `src/acp/transports.py` (new transport type + serve function), `src/agentpool_server/acp_server/server.py` (transport config passthrough), `src/agentpool_cli/` (CLI flags), `src/agentpool_config/` (transport config schema)
- **API**: New `StreamableHTTPTransport` dataclass, extended `Transport` union type, new CLI flag `--port`
- **Dependencies**: `uvicorn` (already in project), `websockets` (already in project)
- **Compatibility**: Fully backward-compatible — existing stdio and websocket transports are unchanged. New transport is opt-in via config/CLI
