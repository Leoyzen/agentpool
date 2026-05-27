# RFC-0033 MCP-over-ACP Test Findings

## Date: 2026-05-26

## What Was Tested

Created comprehensive unit tests for RFC-0033 schema changes in the ACP protocol.

### Files Created
- `tests/acp/schema/test_mcp.py` - 10 tests for AcpMcpServer
- `tests/acp/schema/test_messages.py` - 8 tests for AgentMethod/ClientMethod

### Files Updated
- `tests/acp/schema/test_capabilities.py` - Added 8 tests for McpCapabilities.acp and AgentCapabilities.create

## Key Findings

### AcpMcpServer (src/acp/schema/mcp.py)
- Model has `type: Literal["acp"] = Field(default="acp", init=False)`
- `init=False` does NOT prevent passing `type` to constructor in Pydantic - it just hides from signature
- Invalid type values (e.g., "http") ARE rejected with ValidationError during both construction and deserialization
- Required fields: `name` (inherited from BaseMcpServer) and `id` (new)
- JSON serialization includes all three fields: `name`, `type`, `id`

### McpCapabilities.acp (src/acp/schema/capabilities.py)
- Defaults to `False` as expected
- Can be set to `True` explicitly
- JSON serialization includes `acp` field alongside `http` and `sse`
- Round-trip serialization/deserialization works correctly

### AgentCapabilities.create() (src/acp/schema/capabilities.py)
- Already has `acp_mcp_servers: bool = False` parameter
- Correctly sets `mcp_capabilities.acp` when `acp_mcp_servers=True`
- All three MCP server types (http, sse, acp) can be enabled together

### AgentMethod / ClientMethod (src/acp/schema/messages.py)
- **IMPORTANT GAP**: RFC-0033 specifies that AgentMethod should include "mcp/connect" and "mcp/disconnect", and ClientMethod should include "mcp/message"
- These methods are NOT yet present in the source
- The Literal types are unioned with `str` (`AgentMethod | str`, `ClientMethod | str`), so arbitrary method strings are accepted at runtime
- Tests document current state; will need updating when RFC-0033 methods are added

## Test Style Decisions

- Used function-based tests without classes (following project AGENTS.md standard)
- Added `@pytest.mark.unit` decorator to all new tests
- Used `model_dump(mode="json")` for serialization and `model_validate()` for deserialization
- Used `pytest.raises(ValidationError)` for error cases
- Existing test_capabilities.py class structure was left intact for backward compatibility

## Test Count
- 35 tests total in tests/acp/schema/
- All passing

## AcpMcpTransport Unit Tests (2026-05-26)

### Files Created
- `src/agentpool_server/acp_server/acp_mcp_transport.py` - AcpMcpTransport implementing fastmcp ClientTransport
- `tests/agentpool_server/acp_server/test_acp_mcp_transport.py` - 11 unit tests

### Files Updated
- `src/agentpool_server/acp_server/acp_mcp_manager.py` - Added stream fields and open()/close() to AcpMcpConnection

### Key Design Decisions

1. **fastmcp ClientTransport interface**: Uses `connect_session()` async context manager yielding `ClientSession`, not the older connect/send/receive/close pattern.

2. **Stream creation in connect_session()**: Created read_stream_writer/read_stream and write_stream/write_stream_reader using `anyio.create_memory_object_stream(0)` - matching the stdio_client pattern in mcp library.

3. **Forwarder task**: Reads from `connection.from_session_receive` (created by `connection.open()`) and calls `_send_to_client()` for each message. Runs as an `asyncio.Task` alongside a drainer task for the ClientSession write stream.

4. **Connection state check**: `connect_session()` raises `RuntimeError("Connection not opened")` if `connection._is_open` is False. This prevents using a connection before streams are initialized.

5. **Task cleanup**: Both forwarder and drainer tasks are cancelled in the `finally` block of `connect_session()`. `transport._forwarder_task` is reset to `None` after cleanup.

### Testing Patterns

- Used `AsyncMock` for `_send_to_client` callable
- Created `opened_connection` fixture that calls `await conn.open()` and `await conn.close()` for cleanup
- Buffer size 0 on memory streams means `send()` blocks until `receive()` is called - this naturally synchronizes the test with the forwarder task
- For stream forwarding tests, simply writing to `_from_session_send` and exiting context is sufficient to verify `send_to_client` was called
- No need for `asyncio.sleep()` or events due to the synchronous handoff with buffer size 0

### LSP Gotchas

- `anyio.streams.memory.MemoryObjectSendStream` / `MemoryObjectReceiveStream` type annotations trigger false-positive LSP errors ("Object of type `type[BrokenWorkerInterpreter]` has no attribute `memory`"). These are benign - the code imports and runs correctly.
- Workaround: Use `Any` for parameter types in the transport, or add `# type: ignore` comments in tests.

### Test Results

- 11/11 transport tests passing
- 12/12 existing manager tests still passing (no regression)
