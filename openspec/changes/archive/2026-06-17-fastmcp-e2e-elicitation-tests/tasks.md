## 1. Test Infrastructure Setup

- [x] 1.1 Create `tests/agentpool_server/acp_server/test_acp_mcp_fastmcp_integration.py` with module docstring and imports
- [x] 1.2 Add `pytestmark = [pytest.mark.asyncio, pytest.mark.slow]` to mark all tests in the file
- [x] 1.3 Create shared test fixture `acp_mcp_connection_with_stateful_mock` that returns `AcpMcpConnection` with a stateful `_send_to_client` mock
- [x] 1.4 Create helper `create_stateful_mock_send_to_client()` that inspects incoming messages and returns appropriate JSON-RPC responses based on method name
- [x] 1.5 Create helper async context manager `simulated_mcp_server(conn)` that reads from `to_session`, inspects requests, and writes responses to `from_session`

## 2. Direction B: Real ClientSession Transport Tests

- [x] 2.1 Implement `test_client_session_initialize_roundtrip`: create `AcpMcpConnection` with stateful mock, use `AcpMcpTransport.connect_session()` to yield real `ClientSession`, call `session.initialize()`, verify it completes and mock received correct request
- [x] 2.2 Implement `test_client_session_list_tools_roundtrip`: initialize `ClientSession`, call `session.list_tools()`, verify stateful mock received tools/list request and returned correct response, verify tools list returned
- [x] 2.3 Implement `test_client_session_handles_notification_from_server`: verify `ClientSession` sending `notifications/initialized` (after initialize) flows through forwarder and stateful mock handles it gracefully

## 3. Direction A: Correlation Registry with Simulated Server

- [x] 3.1 Implement `test_ext_method_blocks_on_client_request`: call `ext_method("mcp/message")` with request containing `"method": "tools/list"` and `"id": "req-1"`, start `simulated_mcp_server` in background that reads request and writes response, verify `ext_method` returns result (not `{}`)
- [x] 3.2 Implement `test_ext_method_error_response_raises_request_error`: call `ext_method` with request, have simulated server return error response, verify `RequestError` is raised with correct code/message/data
- [x] 3.3 Implement `test_ext_method_response_not_forwarded_to_acp_client`: call `ext_method` with request, have simulated server return response, verify `send_to_client` consumes response via `fulfill_pending_request` and does NOT call `_send_to_client`
- [x] 3.4 Implement `test_correlation_registry_isolates_concurrent_requests`: call `ext_method` twice with different ids concurrently, have simulated server return responses out of order, verify each `ext_method` call returns its own correct result

## 4. Elicitation and Notification Paths

- [x] 4.1 Implement `test_ext_method_elicitation_create_bypasses_registry`: call `ext_method` with `"method": "elicitation/create"`, mock `input_provider.get_elicitation()` to return `ElicitResult`, verify `_handle_mcp_elicitation` is called (not correlation registry), verify result returned directly
- [x] 4.2 Implement `test_elicitation_create_forwarded_to_acp_client`: simulate MCP server sending elicitation/create to `from_session`, verify it flows through forwarder and `_send_to_client` receives the wrapped mcp/message with correct content
- [x] 4.3 Implement `test_ext_method_notification_fire_and_forget`: call `ext_method` with `"method": "notifications/initialized"` and no `"id"`, verify it returns `{}` immediately, verify no correlation registry entry created, verify message eventually delivered to `handle_client_message`
- [x] 4.4 Implement `test_ext_method_notification_with_null_id`: call `ext_method` with `"id": null`, verify it treats it as notification (fire-and-forget)

## 5. Verification and Cleanup

- [x] 5.1 Run new tests: `pytest tests/agentpool_server/acp_server/test_acp_mcp_fastmcp_integration.py -v -m slow`
- [x] 5.2 Run existing MCP tests to verify no regressions: `pytest tests/agentpool_server/acp_server/test_acp_mcp_*.py -v`
- [x] 5.3 Verify fast CI path skips slow tests: `pytest tests/agentpool_server/acp_server/ -m "not slow" -v`
- [x] 5.4 Run lint: `ruff check tests/agentpool_server/acp_server/test_acp_mcp_fastmcp_integration.py`
- [x] 5.5 Reviewed lines 213-219 in `acp_mcp_manager.py` — reachable code (JSON-RPC response validation error handling), not dead code. No removal needed.
