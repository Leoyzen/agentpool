## ADDED Requirements

### Requirement: Real fastmcp ClientSession initializes over AcpMcpTransport (Direction B)
The system SHALL provide a test that creates a real `fastmcp.ClientSession` via `AcpMcpTransport` and successfully completes `session.initialize()`.

#### Scenario: ClientSession initialize roundtrip
- **WHEN** an `AcpMcpConnection` is created with a stateful mock `_send_to_client` that returns JSON-RPC initialize response
- **AND** `AcpMcpTransport.connect_session()` yields a `ClientSession`
- **AND** `session.initialize()` is called
- **THEN** the initialize request flows through `ClientSession` → `_from_session` → forwarder → `send_to_client()` → `_send_to_client`
- **AND** the stateful mock returns a proper JSON-RPC initialize result response
- **AND** `send_to_client()` forwards the response back to `_to_session_send`
- **AND** `ClientSession._receive_loop` reads the response
- **AND** `session.initialize()` completes without timeout

### Requirement: ClientSession tools/list via transport (Direction B)
The system SHALL provide a test that verifies `ClientSession.list_tools()` sends a request through the forwarder and receives the correct response.

#### Scenario: tools/list roundtrip via AcpMcpTransport
- **WHEN** a `ClientSession` is initialized over `AcpMcpTransport`
- **AND** `session.list_tools()` is called
- **THEN** the tools/list request flows through `ClientSession` → `_from_session` → forwarder → `send_to_client()` → `_send_to_client`
- **AND** the stateful mock returns a proper JSON-RPC tools/list result response
- **AND** `send_to_client()` forwards the response back to `_to_session_send`
- **AND** `ClientSession` receives the response
- **AND** `session.list_tools()` returns the expected tools list
- **AND** the correlation registry is NOT involved in this path

### Requirement: ext_method blocks via correlation registry with simulated MCP server (Direction A)
The system SHALL provide a test that verifies when `ext_method("mcp/message")` is called with a client-initiated request, it blocks until a simulated MCP server writes a response, using the correlation registry to match them.

#### Scenario: Request-response roundtrip through correlation registry
- **WHEN** `ext_method("mcp/message")` is called with a request containing `"method": "tools/list"` and `"id": "req-1"`
- **AND** the connection has a simulated MCP server task reading from `to_session`
- **THEN** the request is written to `_to_session_send` via `handle_client_message`
- **AND** the simulated server reads the request from `_to_session_receive`
- **AND** the simulated server writes a response `{"jsonrpc": "2.0", "id": "req-1", "result": {"tools": []}}` to `_from_session_send`
- **AND** the forwarder task reads from `_from_session_receive` and calls `send_to_client()`
- **AND** `send_to_client()` detects a response with matching `"id"` and calls `fulfill_pending_request`
- **AND** the response is consumed by the correlation registry and NOT forwarded to `_send_to_client`
- **AND** `ext_method` returns `{"tools": []}` (the inner result)
- **AND** the entire operation completes without `ext_method` returning `{}` immediately

### Requirement: ext_method handles elicitation/create via input provider
The system SHALL provide a test that verifies `ext_method("mcp/message")` routes `elicitation/create` to `_handle_mcp_elicitation` instead of the correlation registry.

#### Scenario: Elicitation/create bypasses correlation registry
- **WHEN** `ext_method("mcp/message")` is called with a message containing `"method": "elicitation/create"` (with or without inner `"id"`)
- **AND** the agent has a mocked `input_provider` that returns an `ElicitResult`
- **THEN** the message is routed to `_handle_mcp_elicitation` instead of the correlation registry
- **AND** `_handle_mcp_elicitation` calls `input_provider.get_elicitation()` with the correct params
- **AND** `ext_method` returns the elicitation result directly without blocking on the correlation registry

### Requirement: ext_method returns {} immediately for notifications
The system SHALL provide a test that verifies messages without `"id"` or with `"id": null` do not trigger the correlation registry and return `{}` immediately.

#### Scenario: Notification fire-and-forget
- **WHEN** `ext_method("mcp/message")` is called with a message containing `"method": "notifications/initialized"` but no `"id"`
- **THEN** `ext_method` creates a background task via `self.tasks.create_task()` and returns `{}` immediately
- **AND** no entry is created in the correlation registry
- **AND** the message is eventually delivered to `handle_client_message`

### Requirement: Error responses from correlation registry raise RequestError
The system SHALL provide a test that verifies when the MCP response contains an error, `ext_method` raises `RequestError` with the sanitized error code, message, and data.

#### Scenario: Error response via correlation registry
- **WHEN** `ext_method("mcp/message")` is called with a request containing `"id": "req-err"`
- **AND** the simulated MCP server writes an error response to `_from_session_send`
- **THEN** `send_to_client()` fulfills the pending request with the error response
- **AND** `ext_method` raises `RequestError` with the error code, message, and data from the response

### Requirement: Slow tests are properly marked
The system SHALL mark all tests that use real fastmcp `ClientSession` with `@pytest.mark.slow` so they can be excluded from fast CI runs.

#### Scenario: Fast CI skips slow tests
- **WHEN** running `pytest -m "not slow"`
- **THEN** no fastmcp integration tests are executed
- **AND** running `pytest -m slow`
- **THEN** only fastmcp integration tests are executed
