## ADDED Requirements

### Requirement: ACPServer SHALL negotiate protocol version during initialize

`ACPServer` SHALL inspect the `protocolVersion` field in the `initialize` request and route to the corresponding v1 or v2 agent implementation. Version 1 requests SHALL use the v1 path; version >= 2 requests SHALL use the v2 path.

#### Scenario: v1 client gets v1 path

- **WHEN** client sends `initialize` with `protocolVersion=1`
- **THEN** server SHALL create `AgentPoolACPAgent` (v1) and return `protocolVersion=1` in the response

#### Scenario: v2 client gets v2 path

- **WHEN** client sends `initialize` with `protocolVersion=2`
- **THEN** server SHALL create `AgentPoolACPAgentV2` (v2) and return `protocolVersion=2` in the response

### Requirement: v1 code SHALL remain unmodified in logic

The v1 agent implementation (`AgentPoolACPAgent`, `ACPEventConverter`, `ACPProtocolHandler`) SHALL be moved to `acp_server/v1/` subdirectory but their logic SHALL NOT be modified. Only import paths in `server.py` SHALL change.

#### Scenario: v1 tests pass unchanged after move

- **WHEN** v1 files are moved to `acp_server/v1/` and imports are updated
- **THEN** all existing v1 tests SHALL pass without modification

### Requirement: Shared modules SHALL be version-agnostic

`session_manager.py`, `session.py`, `input_provider.py`, `acp_mcp_manager.py`, `provider_router.py`, `converters.py`, and `commands/` SHALL remain at their current location and be imported by both v1 and v2 code without modification.

#### Scenario: v2 agent imports shared session manager

- **WHEN** `AgentPoolACPAgentV2` needs session management
- **THEN** it SHALL import `ACPSessionManager` from `agentpool_server.acp_server.session_manager` (same path as v1)

#### Scenario: v2 agent imports shared input provider

- **WHEN** `AgentPoolACPAgentV2` needs permission/elicitation handling
- **THEN** it SHALL import `ACPInputProvider` from `agentpool_server.acp_server.input_provider` (same path as v1)

### Requirement: VersionNegotiator SHALL be the single routing decision point

`shared/version_negotiator.py` SHALL encapsulate the version routing logic. Given a requested `protocolVersion`, it SHALL return the negotiated version (1 or 2). Unsupported versions SHALL raise an error.

#### Scenario: Version 1 requested

- **WHEN** `VersionNegotiator.negotiate(requested=1)` is called
- **THEN** it SHALL return `1`

#### Scenario: Version 2 requested

- **WHEN** `VersionNegotiator.negotiate(requested=2)` is called
- **THEN** it SHALL return `2`

#### Scenario: Unsupported version rejected

- **WHEN** `VersionNegotiator.negotiate(requested=0)` is called
- **THEN** it SHALL raise `ValueError`

### Requirement: v2 schema SHALL be in independent package acp_v2

v2 protocol types SHALL be defined in `src/acp_v2/` as a separate top-level package, NOT as a subpackage of `src/acp/`. Import paths SHALL clearly distinguish v1 (`from acp.schema import ...`) from v2 (`from acp_v2.schema import ...`).

#### Scenario: v1 and v2 imports are distinguishable

- **WHEN** a module needs v1 types
- **THEN** it SHALL use `from acp.schema import ToolCallStart`
- **WHEN** a module needs v2 types
- **THEN** it SHALL use `from acp_v2.schema import ToolCallUpdate`

### Requirement: v1 protocol library src/acp/ SHALL NOT be modified

The `src/acp/` package (schema, agent, client, connection, transports, etc.) SHALL NOT be modified in any way. v2 implementations SHALL define their own schema in `src/acp_v2/`.

#### Scenario: src/acp/ files unchanged

- **WHEN** comparing `src/acp/` files before and after the change
- **THEN** no file in `src/acp/` SHALL have been modified
