## ADDED Requirements

### Requirement: Agent accepts mcp/message from client
The AgentPool ACP server SHALL accept `mcp/message` requests and notifications sent by ACP clients on established MCP-over-ACP connections.

#### Scenario: Client sends mcp/message request
- **WHEN** an ACP client sends a JSON-RPC request with `method: "mcp/message"` and `params.connectionId` set to an active connection
- **THEN** the AgentPool ACP server SHALL NOT return a "Method not found" error
- **AND** the server SHALL forward the inner MCP message to the agent's extension method handler

#### Scenario: Client sends mcp/message notification
- **WHEN** an ACP client sends a JSON-RPC notification with `method: "mcp/message"` and `params.connectionId` set to an active connection
- **THEN** the AgentPool ACP server SHALL NOT return a "Method not found" error
- **AND** the server SHALL forward the inner MCP message to the agent's extension method handler without expecting a response

### Requirement: Agent routes inbound mcp/message to MCP session manager
The AgentPool ACP agent implementation SHALL route inbound `mcp/message` to the `AcpMcpConnectionManager` so that the inner MCP payload is delivered to the correct MCP session.

#### Scenario: Inbound mcp/message routed to existing connection
- **WHEN** the agent's extension method handler receives `mcp/message` with a valid `connectionId`
- **THEN** the agent SHALL look up the `AcpMcpConnection` by `connectionId`
- **AND** forward the inner MCP message to that connection's `handle_client_message` method

#### Scenario: Inbound mcp/message with unknown connectionId
- **WHEN** the agent's extension method handler receives `mcp/message` with an unknown or closed `connectionId`
- **THEN** the agent SHALL log a warning with the `connectionId`
- **AND** return an empty response without raising an exception

### Requirement: Schema declares mcp/message as bidirectional
The ACP schema type definitions SHALL list `mcp/message` under both `AgentMethod` and `ClientMethod` to reflect the bidirectional nature of the method per the MCP-over-ACP specification.

#### Scenario: Type checking accepts mcp/message from either direction
- **WHEN** code is type-checked against `AgentMethod` or `ClientMethod`
- **THEN** `"mcp/message"` SHALL be accepted as a valid literal value for both types
