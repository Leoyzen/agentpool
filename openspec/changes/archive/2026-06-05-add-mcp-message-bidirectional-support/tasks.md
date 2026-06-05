## 1. Schema Update

- [x] 1.1 Add `"mcp/message"` to `AgentMethod` literal in `src/acp/schema/messages.py`
- [x] 1.2 Verify `ClientMethod` already contains `"mcp/message"` (should be unchanged)
- [x] 1.3 Run type checker (`mypy src/acp/`) to confirm no type regressions

## 2. Handler Dispatch

- [x] 2.1 Add `case "mcp/message"` to `_agent_handler` in `src/acp/agent/connection.py`
- [x] 2.2 Implement dispatch logic: call `agent.ext_method("mcp/message", params)` and return the result
- [x] 2.3 Ensure both request and notification paths work (check `is_notification` flag)

## 3. Agent Integration

- [x] 3.1 Verify `AcpAgent.ext_method` in `src/agentpool_server/acp_server/acp_agent.py` handles inbound `mcp/message` correctly
- [x] 3.2 Ensure `AcpMcpConnectionManager.get_connection` is used to look up the connection by `connectionId`
- [x] 3.3 Add warning log for unknown `connectionId` (if not already present)

## 4. Testing

- [x] 4.1 Write unit test for `_agent_handler` receiving `mcp/message` from client
- [x] 4.2 Write integration test for bidirectional `mcp/message` flow through ACP connection
- [x] 4.3 Run existing ACP/MCP test suite to ensure no regressions (`uv run pytest tests/acp/`)

## 5. Verification

- [x] 5.1 Run linter (`ruff check src/acp/`)
- [x] 5.2 Run type checker (`mypy src/acp/`)
- [x] 5.3 Verify end-to-end with SEED or test client sending `mcp/message` to AgentPool
