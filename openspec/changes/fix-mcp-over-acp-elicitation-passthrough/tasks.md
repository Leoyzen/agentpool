## 1. AcpMcpConnection Correlation Registry

- [x] 1.1 Add `_pending_client_requests: dict[Any, asyncio.Future]` and `_pending_lock: asyncio.Lock` to `AcpMcpConnection.__init__`
- [x] 1.2 Implement `register_pending_request(request_id: Any) -> asyncio.Future` that acquires `_pending_lock`, checks if `request_id` already exists and raises `RuntimeError` if so, creates a Future, stores it under the id, and returns it
- [x] 1.3 Implement `fulfill_pending_request(request_id: Any, response: dict[str, Any]) -> bool` that acquires `_pending_lock`, pops the Future from the dict (not just lookups), and:
  - If no Future found: return `False` (unknown request)
  - If Future is already done (timed out/cancelled): log warning, return `True` (consumed, do not forward)
  - If Future is pending: call `set_result(response)`, catching `InvalidStateError` gracefully (log warning, return `True`), otherwise return `True`
- [x] 1.4 Implement `unregister_pending_request(request_id: Any) -> None` that pops the Future from the registry without setting a result
- [x] 1.5 Add cleanup in `AcpMcpConnection.close()` to: cancel any remaining pending Futures, **clear the `_pending_client_requests` dict**, then close streams to unblock any pending sends

## 2. send_to_client Response Filtering

- [x] 2.1 In `AcpMcpConnection.send_to_client()`, at the top of the method, check if the message is a dict with `"result"` or `"error"` key AND `msg.get("id") is not None` (not just `"id" in msg`, to exclude `id: null`)
- [x] 2.2 If it is a response, call `fulfill_pending_request` with the id and the full message dict
- [x] 2.3 If fulfillment returns `True` (consumed — either fulfilled or already-done), return immediately without calling `_send_to_client` or writing to `_to_session_send`
- [x] 2.4 If fulfillment returns `False` (unknown request), **drop the message** (log warning, return without forwarding). Do NOT fall back to existing forwarding logic for responses.
- [x] 2.6 Extract `_sanitize_error` from `send_to_client` to a module-level helper in `acp_mcp_manager.py` (e.g., `_sanitize_jsonrpc_error`) so it can be imported and reused in `acp_agent.py` for `ext_method` error mapping

## 3. ext_method Synchronous Request Handling

- [x] 3.1 In `AgentPoolACPAgent.ext_method("mcp/message")`, inspect the inner `message` dict; verify `isinstance(message, dict)` before key inspection
- [x] 3.2 If the message is a dict with `"method" in message` AND `message.get("id") is not None` (request), get the connection via `_mcp_manager.get_connection(connection_id)`. If `conn is None`, raise `RequestError.invalid_params({"connectionId": connection_id})` and return early. If the message has `"id"` but no `"method"`, raise `RequestError.invalid_params({"message": "Invalid MCP message: response without method"})`
- [x] 3.3 Call `await conn.register_pending_request(message["id"])` to get a Future; handle `RuntimeError` (duplicate ID) by **raising** `RequestError.invalid_request({"message": f"Duplicate request ID: {message['id']}"})`
- [x] 3.4 Use `try/finally` to ensure `conn.unregister_pending_request(message["id"])` is always called
- [x] 3.5 Inside the try block, wrap `conn.handle_client_message(message)` in `anyio.fail_after(30)` to prevent indefinite blocking on a dead session
- [x] 3.6 Await the Future with `asyncio.wait_for(future, timeout=30)`; catch `asyncio.TimeoutError` and raise `RequestError` with code `-32000` and message "MCP request timed out"; also catch `asyncio.CancelledError` (from connection close) and raise `RequestError` with code `-32001` and message "Connection closed while awaiting MCP response"
- [x] 3.7 Extract the response dict; if it contains `"error"`, sanitize the error code via `_sanitize_error` (extract to module-level helper if needed), then raise `RequestError(code, message, data)` preserving the `data` payload
- [x] 3.8 If it contains `"result"`, return that result dict as the ACP response
- [x] 3.9 If the message has no `"id"` key or `id` is `None` (notification), retain the existing fire-and-forget behavior using `self.tasks.create_task(conn.handle_client_message(message))` and return `{}` immediately

## 4. Tests

### 4.1 New Regression Tests

- [ ] 4.1 Add `test_elicitation_passthrough_returns_correct_result` in `test_acp_mcp_red_flags.py`: simulate client-initiated elicitation/create, verify ext_method returns the inner result
- [ ] 4.2 Add `test_concurrent_pending_requests_are_isolated` in `test_acp_mcp_end_to_end.py`: verify two concurrent requests with different ids return correct respective results
- [ ] 4.3 Add `test_pending_request_timeout` in `test_acp_mcp_end_to_end.py`: verify that when no response arrives within timeout, ext_method raises TimeoutError mapped to RequestError
- [ ] 4.4 Add `test_late_response_after_timeout_is_dropped` in `test_acp_mcp_red_flags.py`: verify that a response arriving after timeout is consumed by fulfill_pending_request and NOT forwarded to _send_to_client
- [ ] 4.5 Add `test_duplicate_request_id_rejected` in `test_acp_mcp_end_to_end.py`: verify that registering a second request with the same ID raises RuntimeError
- [ ] 4.6 Add `test_unmatched_response_is_dropped` in `test_acp_mcp_red_flags.py`: verify that a response with no matching pending request is dropped and not forwarded
- [ ] 4.7 Add `test_response_fulfillment_prevents_fake_response_injection` in `test_acp_mcp_red_flags.py`: verify that when a response matches a pending request, it is not forwarded to _send_to_client and no fake response is written to _to_session_send
- [ ] 4.8 Add `test_agent_initiated_request_path_unchanged` in `test_acp_mcp_end_to_end.py`: verify that agent-initiated requests (tools/list) still forward through _send_to_client and write ACP response back to stream
- [ ] 4.9 Add `test_notification_fire_and_forget` in `test_acp_mcp_end_to_end.py`: verify that client-initiated notifications return {} immediately
- [ ] 4.10 Add `test_pending_request_mcp_error_mapped_to_request_error` in `test_acp_mcp_end_to_end.py`: verify that when the inner MCP response contains an error, ext_method raises RequestError with sanitized code, message, and data payload
- [ ] 4.11 Add `test_notification_with_null_id_is_fire_and_forget` in `test_acp_mcp_end_to_end.py`: verify that a message with `"id": null` is treated as a notification, not a request
- [ ] 4.12 Add `test_non_dict_message_handled_gracefully` in `test_acp_mcp_end_to_end.py`: verify that a non-dict message in ext_method falls through to the notification path without crashing
- [ ] 4.13 Add `test_duplicate_response_invalid_state_handled_gracefully` in `test_acp_mcp_red_flags.py`: verify that when a buggy MCP client sends two responses with the same ID, the second response is consumed (not forwarded) and does not crash the forwarder task
- [ ] 4.14 **Add mock-based elicitation integration test**: Create a synthetic test that manually injects `elicitation/create` into `_to_session_send`, mocks the ClientSession response through `_from_session_send`, and verifies the full correlation roundtrip through `ext_method`
- [ ] 4.15 **Add fastmcp end-to-end elicitation integration test** (if feasible, mark `@pytest.mark.slow`): Use `fastmcp.Server` and `fastmcp.Client` with `AcpMcpTransport` to verify the complete elicitation passthrough chain
- [ ] 4.16 Add `test_connection_closed_while_pending` in `test_acp_mcp_end_to_end.py`: verify that closing the connection while ext_method is awaiting cancels the Future gracefully and raises RequestError with code `-32001`

### 4.2 Update Existing Tests for New Behavior

The fix changes behavior in two ways that break existing tests:
1. **MCP responses** (messages with `"result"` or `"error"` + `"id"`) are no longer forwarded to the ACP client unless they match a pending request
2. **Client-initiated requests** (messages with `"method"` + `"id"`) now block `ext_method` until a response arrives, instead of returning `{}` immediately

- [ ] 4.17 Update `tests/agentpool_server/acp_server/test_acp_mcp_transport.py`:
  - `test_message_forwarding_from_session_to_client`: Change the test message from a response (`"result"`) to a **request** (`"method": "tools/list"`) so it tests the agent-initiated forwarding path
  - `test_multiple_messages_forwarded`: Change test messages from responses to **requests** with `"method"`
  - `test_forwarder_task_cleanup_on_session_exit`: Change test message from response to **request**
  - `test_transport_reusable_across_sessions`: Change test messages from responses to **requests**
  - `test_each_session_has_isolated_forwarder`: Change test messages from responses to **requests**
  - `test_message_after_forwarder_cancelled_not_delivered`: Change test messages from responses to **requests**
- [ ] 4.18 Update `tests/agentpool_server/acp_server/test_acp_mcp_end_to_end.py`:
  - `test_full_connection_lifecycle`: Change the message at line 73 from a response (`"result"`) to a **request** (`"method": "tools/list"`)
  - `test_multiple_messages_over_same_connection`: Remove or change the response message (lines 127-130) to a **request**; only requests should be forwarded in this test
- [ ] 4.19 Update `tests/agentpool_server/acp_server/test_acp_mcp_manager.py`:
  - `test_connection_send_to_client_formats_message`: Change the test message from a response (`"result": "ok"`) to a **request** (`"method": "test"`) so it tests forwarding of requests, not responses
- [ ] 4.20 Update `tests/agentpool_server/acp_server/test_acp_mcp_agent_integration.py`:
  - `test_ext_method_routes_message`: Change the test message from a **request** (`"method": "tools/list"`) to a **notification** (remove `"id"`) so it tests the fire-and-forget path, or mock the correlation registry and inject a response through `_from_session_send` to test the synchronous path
  - `test_ext_method_concurrent_messages`: Same as above — change messages to **notifications** (remove `"id"`) or mock the full correlation roundtrip

### 4.3 Test Execution

- [ ] 4.21 Run full test suite: `pytest tests/agentpool_server/acp_server/ -v`
- [ ] 4.22 Run regression tests for existing stdio/SSE/HTTP MCP paths

## 5. Verification

- [ ] 5.1 Run `mypy src/agentpool_server/acp_server/acp_mcp_manager.py src/agentpool_server/acp_server/acp_agent.py` with zero errors
- [ ] 5.2 Run `ruff check src/agentpool_server/acp_server/acp_mcp_manager.py src/agentpool_server/acp_server/acp_agent.py`
- [ ] 5.3 Run all MCP-over-ACP tests: `pytest tests/agentpool_server/acp_server/test_acp_mcp_*.py -v`
- [ ] 5.4 Verify no regressions in existing ACP server tests: `pytest tests/servers/acp_server/ -v`
