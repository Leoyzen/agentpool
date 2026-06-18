## ADDED Requirements

### Requirement: Client-initiated mcp/message requests are synchronously correlated
When an ACP client sends an `mcp/message` request (outer JSON-RPC with an `id` and a `method`), the agent SHALL register a pending request, forward the inner MCP message to the MCP client session, await the corresponding MCP response, and return the inner MCP result as the ACP response. If the message cannot be written to the session stream within 30 seconds, the agent SHALL raise a timeout error. If the inner MCP response contains an error, the agent SHALL raise a `RequestError` with the sanitized error code, message, and data payload.

#### Scenario: Elicitation passthrough returns correct result
- **WHEN** the MCP server sends `elicitation/create` with id `3` to the MCP client session
- **AND** the MCP client generates response `{"action": "accept"}`
- **AND** the agent's `ext_method("mcp/message")` processes the client-initiated request
- **THEN** the ACP response result SHALL be `{"action": "accept"}`
- **AND** the MCP server SHALL NOT receive an empty `{}` result

#### Scenario: Fastmcp elicitation end-to-end passthrough
- **WHEN** a fastmcp Server defines a tool `confirm_with_form2` that triggers `elicitation/create` with schema `{confirmed: boolean}`
- **AND** the fastmcp Client connects to the server via `AcpMcpTransport` over the ACP channel
- **AND** the ACP client sends an `mcp/message` request containing the `elicitation/create` inner message with id `3`
- **AND** the agent's `ext_method("mcp/message")` registers pending request id `3` and awaits the response
- **AND** the fastmcp Client's elicitation handler returns `{"action": "accept", "content": {"confirmed": true}}`
- **THEN** the ACP response result SHALL be `{"action": "accept", "content": {"confirmed": true}}`
- **AND** the fastmcp Server SHALL receive the elicitation result and complete the tool call successfully
- **AND** the tool call result SHALL contain `confirmed: true`

#### Scenario: Concurrent pending requests are isolated
- **WHEN** the ACP client sends two concurrent `mcp/message` requests with ids `1` and `2`
- **AND** the MCP client generates responses `{"result": "A"}` for id `1` and `{"result": "B"}` for id `2`
- **THEN** the ACP response for request `1` SHALL be `"A"`
- **AND** the ACP response for request `2` SHALL be `"B"`

#### Scenario: Timeout on pending request
- **WHEN** the ACP client sends an `mcp/message` request with id `5`
- **AND** the MCP client does not generate a response within 30 seconds
- **THEN** the agent SHALL raise a timeout error to the ACP client
- **AND** when the late response arrives, it SHALL be dropped and SHALL NOT be forwarded to the ACP client

#### Scenario: Duplicate request ID is rejected
- **WHEN** the ACP client sends an `mcp/message` request with id `1`
- **AND** before the response arrives, another request with id `1` is sent
- **THEN** the second registration SHALL raise a `RequestError` with code `-32600`

#### Scenario: MCP error response mapped to RequestError
- **WHEN** the ACP client sends an `mcp/message` request with id `6`
- **AND** the MCP client generates an error response `{"jsonrpc": "2.0", "id": 6, "error": {"code": -32601, "message": "Method not found", "data": {"method": "unknown"}}}`
- **THEN** the agent SHALL raise a `RequestError` with code `-32601`, message "Method not found", and data `{"method": "unknown"}`

#### Scenario: id:null treated as notification
- **WHEN** the ACP client sends `mcp/message` with `{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 50}, "id": null}`
- **THEN** the message SHALL be written to `_to_session_send`
- **AND** the ACP response SHALL be `{}` returned immediately

#### Scenario: Non-dict message handled gracefully
- **WHEN** the ACP client sends `mcp/message` with a non-dict payload (e.g., a string)
- **THEN** the agent SHALL write the message to `_to_session_send`
- **AND** the ACP response SHALL be `{}` returned immediately without crashing

### Requirement: MCP responses are not forwarded back to the ACP client
When the MCP client sends a response message (containing `result` or `error`) through the `from_session` stream, the agent SHALL check if it matches a pending client-initiated request. If matched, the agent SHALL fulfill the pending Future and SHALL NOT forward the message to the ACP client via `_send_to_client`. If the response does not match any pending request, the agent SHALL drop the message and SHALL NOT forward it.

#### Scenario: Response fulfillment prevents fake response injection
- **WHEN** a client-initiated `mcp/message` request with id `3` is pending
- **AND** the MCP client writes response `{"jsonrpc": "2.0", "id": 3, "result": {"action": "accept"}}` to `from_session`
- **THEN** the pending Future SHALL be fulfilled with the response
- **AND** `_send_to_client` SHALL NOT be called for this message
- **AND** the MCP session stream SHALL NOT receive a fake `{"jsonrpc": "2.0", "id": 3, "result": {}}` response

#### Scenario: Late response after timeout is dropped
- **WHEN** a client-initiated `mcp/message` request with id `3` times out
- **AND** the pending Future is cancelled and unregistered
- **AND** the MCP client later writes response `{"jsonrpc": "2.0", "id": 3, "result": {"action": "accept"}}` to `from_session`
- **THEN** the response SHALL be dropped
- **AND** `_send_to_client` SHALL NOT be called for this message

#### Scenario: Unmatched response is dropped
- **WHEN** the MCP client writes response `{"jsonrpc": "2.0", "id": 99, "result": {}}` to `from_session`
- **AND** there is no pending request with id `99`
- **THEN** the response SHALL be dropped
- **AND** `_send_to_client` SHALL NOT be called for this message

#### Scenario: Duplicate response handled gracefully
- **WHEN** a client-initiated `mcp/message` request with id `7` is pending
- **AND** the MCP client writes two responses with id `7`
- **THEN** the first response SHALL fulfill the pending Future
- **AND** the second response SHALL be consumed without crashing the forwarder task
- **AND** `_send_to_client` SHALL NOT be called for either response

### Requirement: Agent-initiated mcp/message requests use existing fire-and-forget path
When the MCP client session sends a request (not a response) through the `from_session` stream, the agent SHALL forward it to the ACP client via `_send_to_client` as before. The ACP client's response SHALL be written back to the MCP session stream.

#### Scenario: Tools/list from agent still works
- **WHEN** the MCP client session sends `{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}` to `from_session`
- **THEN** the agent SHALL forward this message to the ACP client via `_send_to_client`
- **AND** when the ACP client returns `{"tools": [...]}`, the agent SHALL write `{"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}` back to `_to_session_send`

### Requirement: Notifications remain fire-and-forget
When an ACP client sends an `mcp/message` without an `id` (notification), the agent SHALL write the message to the MCP session stream and immediately return `{}` without waiting for a response.

#### Scenario: Client sends notification
- **WHEN** the ACP client sends `mcp/message` with `{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 50}}`
- **THEN** the message SHALL be written to `_to_session_send`
- **AND** the ACP response SHALL be `{}` returned immediately
