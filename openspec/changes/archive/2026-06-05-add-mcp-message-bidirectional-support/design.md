## Context

AgentPool implements the ACP (Agent Client Protocol) server role. In the ACP protocol, `mcp/message` is defined as a **bidirectional** method (`x-side = "both"`). However, the current AgentPool implementation only recognizes `mcp/message` as a `ClientMethod` (methods sent from agent to client). When an ACP client (e.g., SEED) sends `mcp/message` to AgentPool, the `_agent_handler` in `acp/agent/connection.py` does not have a matching case and falls through to raise `RequestError.method_not_found("mcp/message")`.

This breaks legitimate MCP-over-ACP flows where the client sends MCP server-originated requests or notifications back to the agent. A concrete example is bridging MCP `elicitation/create` to ACP `elicitation/create`: the client may need to send MCP messages through the established tunnel to the agent's MCP session manager.

The fix requires schema-level recognition and handler-level dispatch for `mcp/message` on the agent side.

## Goals / Non-Goals

**Goals:**
- Make AgentPool's ACP server accept `mcp/message` requests/notifications from ACP clients.
- Route received `mcp/message` to the agent's `ext_method` handler so existing MCP-over-ACP infrastructure (`AcpMcpConnection`, `AcpMcpConnectionManager`) can process it.
- Stay compliant with the official MCP-over-ACP spec where `mcp/message` is bidirectional.

**Non-Goals:**
- Changing the client-side behavior of `mcp/message` (it already works).
- Implementing new MCP methods or tools.
- Modifying the ACP spec itself (this is an implementation fix).

## Decisions

### 1. Add `"mcp/message"` to `AgentMethod` in `acp/schema/messages.py`

**Rationale**: The ACP spec defines `mcp/message` as `x-side = "both"`. It must appear in both `AgentMethod` and `ClientMethod` literals so that type-checking and runtime validation accept it from either direction. This is a purely additive, non-breaking change.

### 2. Route agent-side `mcp/message` through `agent.ext_method()`

**Rationale**: `ext_method` is the existing extension point on `AcpAgent` (and `Agent` base class) for handling non-standard ACP methods. `AcpAgent.ext_method` already handles `mcp/message` when the agent sends it to the client. Reusing the same method for the reverse direction keeps the surface area minimal and consistent.

When `_agent_handler` receives `mcp/message`, it will call `agent.ext_method("mcp/message", params)` and return the result. `AcpAgent.ext_method` already contains the logic to look up the `AcpMcpConnection` by `connectionId` and forward the inner MCP message.

**Alternative considered**: Introduce a dedicated `handle_mcp_message` method on the agent interface. Rejected because `ext_method` is already the generic handler for methods not in the core ACP set, and `AcpAgent` already implements `mcp/message` logic there.

## Risks / Trade-offs

- **[Risk]** Adding `mcp/message` to `AgentMethod` could cause validation logic that assumes `AgentMethod` is core-only to break.  
  → **Mitigation**: `AgentMethod` is only used for literal typing and handler dispatch; no core agent logic iterates over `AgentMethod` values to filter methods.

- **[Risk]** Reusing `ext_method` for bidirectional routing could conflate agent-initiated and client-initiated MCP messages if the handler needs to distinguish direction.  
  → **Mitigation**: The `params` already contain `connectionId`, which uniquely identifies the connection and its directionality. The `AcpMcpConnectionManager` stores enough context to route correctly.

- **[Trade-off]** This change only fixes the ACP server-side reception. If the agent's `ext_method` does not properly handle inbound `mcp/message`, messages will be accepted but silently dropped or mishandled.  
  → **Mitigation**: `AcpAgent.ext_method` already implements `mcp/message` forwarding to the session MCP manager; the implementation is verified by existing MCP-over-ACP tests.
