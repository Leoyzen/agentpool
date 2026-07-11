## ADDED Requirements

### Requirement: SessionConnectionPool SHALL provide get_client method for client injection

The `SessionConnectionPool` SHALL provide `get_client(config: BaseMCPServerConfig) -> MCPClient` that returns a pooled `MCPClient` for the given config. The method SHALL reuse an existing transport if one exists for the config, or create a new one. The returned `MCPClient` wraps the pooled transport — the pool retains ownership of transport lifecycle.

#### Scenario: Reuse existing transport
- **WHEN** `get_client(config)` is called for a config that already has an active transport
- **THEN** a new `MCPClient` SHALL be created wrapping the existing transport
- **AND** no new subprocess or connection SHALL be created

#### Scenario: Create new transport
- **WHEN** `get_client(config)` is called for a config with no existing transport
- **THEN** a new transport SHALL be created and pooled
- **AND** a new `MCPClient` wrapping the transport SHALL be returned
