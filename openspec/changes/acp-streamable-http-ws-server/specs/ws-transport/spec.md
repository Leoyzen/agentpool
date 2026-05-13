## ADDED Requirements

### Requirement: WebSocket upgrade at /acp endpoint
The server SHALL accept WebSocket upgrade requests at the `/acp` path. Upon successful upgrade, the server SHALL return an `Acp-Connection-Id` header containing a unique identifier (UUID v4) for the connection.

#### Scenario: Successful WebSocket upgrade
- **WHEN** a client sends a WebSocket upgrade request to `/acp`
- **THEN** the server SHALL complete the WebSocket handshake and include an `Acp-Connection-Id` header with a UUID v4 value in the upgrade response

#### Scenario: Multiple concurrent connections
- **WHEN** multiple clients simultaneously upgrade to WebSocket at `/acp`
- **THEN** each connection SHALL receive a unique `Acp-Connection-Id` and operate independently

### Requirement: Initialize lifecycle enforcement
The server SHALL require clients to send an `initialize` JSON-RPC request as the first message after WebSocket upgrade. The server SHALL reject any other JSON-RPC request received before a successful `initialize` by returning a JSON-RPC error response with code `-32600` (Invalid Request).

#### Scenario: Initialize sent first
- **WHEN** a client sends an `initialize` request as the first message after WebSocket upgrade
- **THEN** the server SHALL process the `initialize` request normally via `AgentSideConnection`

#### Scenario: Non-initialize request before initialization
- **WHEN** a client sends a JSON-RPC request other than `initialize` before completing initialization
- **THEN** the server SHALL respond with a JSON-RPC error with code `-32600` and message indicating initialization is required

#### Scenario: Requests after successful initialization
- **WHEN** a client has completed `initialize` successfully and sends subsequent JSON-RPC requests
- **THEN** the server SHALL process all requests normally via `AgentSideConnection` without restriction

### Requirement: WebSocket-to-ByteStream adaptation
The server SHALL adapt WebSocket frames to `ByteReceiveStream` and `ByteSendStream` interfaces compatible with `AgentSideConnection`. Each WebSocket message SHALL be treated as a single JSON-RPC message. Outgoing messages SHALL be sent as complete WebSocket messages (one JSON-RPC message per frame).

#### Scenario: Incoming message adaptation
- **WHEN** a client sends a WebSocket text message containing a JSON-RPC request
- **THEN** the server SHALL deliver the message content (without framing) to `AgentSideConnection` via `ByteReceiveStream` with a trailing newline for JSON-RPC line protocol compliance

#### Scenario: Outgoing message adaptation
- **WHEN** `AgentSideConnection` writes a JSON-RPC response to `ByteSendStream`
- **THEN** the server SHALL send the message content as a single WebSocket text message (stripping the trailing newline)

### Requirement: Connection cleanup on disconnect
The server SHALL clean up all resources associated with a WebSocket connection when the client disconnects (graceful close, abnormal close, or network failure). The server SHALL call `AgentSideConnection.close()` during cleanup.

#### Scenario: Graceful client disconnect
- **WHEN** a client closes the WebSocket connection normally
- **THEN** the server SHALL call `AgentSideConnection.close()` and release all connection resources

#### Scenario: Abnormal connection loss
- **WHEN** the WebSocket connection is lost (network failure, client crash)
- **THEN** the server SHALL detect the disconnection and call `AgentSideConnection.close()` within a reasonable timeframe

### Requirement: ASGI application serving
The server SHALL implement the WebSocket endpoint as a Starlette ASGI application served by uvicorn. The server SHALL respect the configured host and port from `StreamableHTTPTransport`.

#### Scenario: Server starts on configured host and port
- **WHEN** `StreamableHTTPTransport(host="0.0.0.0", port=8080)` is provided
- **THEN** uvicorn SHALL bind to `0.0.0.0:8080` and serve the ASGI application

#### Scenario: Server graceful shutdown
- **WHEN** the shutdown event is set or the process receives SIGINT
- **THEN** the server SHALL close all active WebSocket connections and stop accepting new ones before exiting
