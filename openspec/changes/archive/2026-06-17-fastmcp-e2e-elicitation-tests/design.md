## Context

The MCP-over-ACP elicitation passthrough was fixed by introducing a correlation registry (`AcpMcpConnection`) and synchronous request handling in `AgentPoolACPAgent.ext_method("mcp/message")`. The fix ensures that when an ACP client sends a request via `mcp/message` (e.g., `tools/list`, `elicitation/create`), `ext_method` blocks until the MCP response arrives, matching requests and responses by JSON-RPC `id`.

Current tests (`test_acp_mcp_red_flags.py`, `test_acp_mcp_end_to_end.py`) use mocked `_send_to_client` and manually inject messages. They validate the correlation registry and `ext_method` behavior in isolation.

`test_acp_mcp_agent_integration.py` already contains real `ClientSession` tests (Tests 8 and 9) that verify `session.initialize()` and `provider.get_tools()` through `AcpMcpTransport`. These cover **Direction B** (MCP client initiates requests).

The remaining gap: no test verifies the **Direction A** roundtrip with a real `ClientSession` on the receiving end — i.e., `ext_method("mcp/message")` → `to_session` → `ClientSession` reads → `ClientSession` responds → `from_session` → `send_to_client` → correlation registry → `ext_method` returns.

## Message Directions

```
Direction A (Bug fix path — ACP client → MCP session):
  ACP client → ext_method("mcp/message") → handle_client_message()
    → _to_session_send → ClientSession reads → ClientSession writes response
    → _from_session → forwarder → send_to_client() → fulfill_pending_request()
    → ext_method() returns result (NOT {})
  
  Key: ext_method blocks. Correlation registry matches request/response by id.

Direction B (Normal MCP client requests):
  ClientSession.list_tools() → writes to _from_session → forwarder
    → send_to_client() → _send_to_client() → response written to _to_session_send
    → ClientSession receives → list_tools() returns
  
  Key: ClientSession initiates. No correlation registry involved.
```

## Goals / Non-Goals

**Goals:**
- Add tests that exercise **Direction A** with a real `ClientSession` receiving requests from `ext_method`
- Add tests that exercise **Direction B** with real `ClientSession` through `AcpMcpTransport` (complementing existing Tests 8/9)
- Verify correlation registry works when responses come from a real `ClientSession`
- Ensure tests are runnable with `pytest -m slow` and skipped in fast CI

**Non-Goals:**
- Replacing existing mock-based correlation registry tests (they remain for fine-grained control)
- Testing stdio/SSE MCP transports (already covered)
- Testing actual IDE integration (Zed, Toad) — out of scope
- Load testing or performance benchmarking

## Decisions

### Split test scope: Direction A (mock stream injection) + Direction B (real ClientSession)

**Rationale**: A single `ClientSession` cannot naturally test both directions simultaneously. `ClientSession` is a client — it initiates requests (Direction B) and responds to incoming requests only by erroring or ignoring them. To test Direction A, we need to:
1. Call `ext_method("mcp/message")` with a request
2. Have a background task read from `to_session` (simulating what `ClientSession._receive_loop` does)
3. Write a response back to `from_session`
4. Verify `ext_method` returns the response via correlation registry

**Alternative considered**: Using real `ClientSession._receive_loop` for Direction A. Rejected — `ClientSession` will receive the request but won't know how to handle it (it's not a server). It may send an error response, which still exercises the correlation registry, but this is non-deterministic.

### Use stateful mock for `_send_to_client`

**Rationale**: The mock must inspect incoming messages and return appropriate JSON-RPC responses. A simple `AsyncMock(return_value={})` causes double-response issues (the mock returns `{}`, then `send_to_client` wraps it as a JSON-RPC response and writes it to `_to_session_send`, while the test also manually injects a response).

### Test file: `test_acp_mcp_fastmcp_integration.py`

**Rationale**: Separate file for real `ClientSession` tests (Direction B) to distinguish from mock-based Direction A tests. The existing `test_acp_mcp_end_to_end.py` will be enhanced with better Direction A coverage.

## Risks / Trade-offs

- **Zero-buffer deadlock**: `create_memory_object_stream(0)` means `send()` blocks until `receive()`. If the consumer task crashes or isn't started, writes deadlock. Mitigation: always start consumer before producer; use timeouts.
- **Test flakiness from async timing**: Real `ClientSession` has internal timeouts. Mitigation: generous timeouts (5s), stateful mock returns immediately.
- **fastmcp version drift**: `ClientSession` internals may change. Mitigation: use public API only (`initialize()`, `list_tools()`).
- **Slow test suite**: Real `ClientSession` initialization takes ~100ms. Mitigation: `@pytest.mark.slow`, run in parallel.

## Open Questions

- Should we implement a minimal MCP "server" task that reads from `to_session` and writes responses to `from_session`? This would make Direction A tests more realistic without relying on `ClientSession` error responses.
