## Why

MCP-over-ACP elicitation passthrough fails when the MCP server sends `elicitation/create` to the client through the ACP channel. The root cause is a protocol violation: `AgentPoolACPAgent.ext_method("mcp/message")` returns `{}` for client-initiated requests instead of the inner MCP result payload, as required by the MCP-over-ACP RFD. This causes the MCP server-side validation to fail with `Invalid elicitation result: action expected one of "accept"|"decline"|"cancel"`. Additionally, `AcpMcpConnection.send_to_client()` incorrectly writes the ACP acknowledgment response back into the MCP session stream, creating a fake JSON-RPC response that corrupts the message flow.

## What Changes

- **Modify `AcpMcpConnection`**: Add an inline correlation registry (`_pending_client_requests: dict[Any, asyncio.Future]`) to track pending MCP requests originating from the client. Implement `register_pending_request(id)`, `fulfill_pending_request(id, response)`, and `unregister_pending_request(id)` methods.
- **Modify `AcpMcpConnection.send_to_client()`**: Before forwarding a message to the ACP client, check if it is a response to a pending client-originated request. If so, fulfill the Future and return immediately without forwarding to the ACP client.
- **Modify `AgentPoolACPAgent.ext_method("mcp/message")`**: For messages containing an `id` (requests), register a pending request, write the message to the MCP session via `handle_client_message`, await the corresponding response through the correlation registry with a timeout, and return the inner MCP `result` as the ACP response. For notifications (no `id`), retain the existing fire-and-forget behavior.
- **Update tests**: Extend `test_acp_mcp_red_flags.py` and add new regression tests for concurrent pending requests, timeout handling, and error propagation.

## Capabilities

### New Capabilities
- *(none)*

### Modified Capabilities
- `mcp-over-acp`: Change the bidirectional `mcp/message` routing behavior so that agent-initiated requests use fire-and-forget (with ACP response forwarded back to the MCP session), while client-initiated requests are synchronously correlated and their inner MCP results are returned as the ACP response.

## Impact

- **`src/agentpool_server/acp_server/acp_mcp_manager.py`**: Add correlation registry and response fulfillment logic to `AcpMcpConnection`.
- **`src/agentpool_server/acp_server/acp_agent.py`**: Rewrite `ext_method("mcp/message")` to support synchronous request-response correlation for client-initiated MCP requests.
- **`tests/agentpool_server/acp_server/test_acp_mcp_red_flags.py`**: Add regression tests for the elicitation passthrough scenario.
- **`tests/agentpool_server/acp_server/test_acp_mcp_end_to_end.py`**: Add tests for concurrent pending requests and timeout handling.
- No breaking changes to public APIs or existing stdio/SSE/HTTP MCP transports.
