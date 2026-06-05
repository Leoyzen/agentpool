## Why

The AgentPool ACP server currently rejects `mcp/message` requests sent by ACP clients (e.g., SEED) with a "Method not found" error. According to the official MCP-over-ACP specification, `mcp/message` is **bidirectional** (`x-side = "both"`)—either the agent or the client can send MCP payloads on an established connection. The current implementation only lists `mcp/message` under `ClientMethod`, so when a client sends it to the agent (AgentPool), the `_agent_handler` falls through to the default case and raises `RequestError.method_not_found`. This breaks MCP-over-ACP tunnels initiated from the client side and prevents features like MCP elicitation bridging from working.

## What Changes

- Add `"mcp/message"` to `AgentMethod` in `acp/schema/messages.py`
- Add a `case "mcp/message"` handler in `acp/agent/connection.py:_agent_handler` that delegates to the agent's `ext_method`
- Ensure `ext_method` on the agent side (e.g., `AcpAgent`) can receive and forward MCP messages from the client
- Update any ACP method validation or routing logic that assumes `mcp/message` is client-only

## Capabilities

### New Capabilities
- `mcp-over-acp-bidirectional`: Enable AgentPool to accept `mcp/message` from ACP clients, completing the bidirectional MCP-over-ACP tunnel support per the ACP spec.

### Modified Capabilities
- *(none)*

## Impact

- **ACP layer** (`src/acp/`): Schema and handler changes to recognize `mcp/message` as a valid agent method.
- **ACP server** (`src/agentpool_server/acp_server/`): `AcpAgent.ext_method` may receive `mcp/message` from the client and needs to route it to the MCP session manager.
- **External integrations**: SEED and other ACP clients that send MCP server-originated requests/notifications via `mcp/message` will now work correctly.
