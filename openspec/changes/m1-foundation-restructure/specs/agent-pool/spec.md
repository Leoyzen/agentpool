## MODIFIED Requirements

### Requirement: AgentPool serves as facade wrapping AgentFactory and HostContext

AgentPool SHALL remain the primary user-facing entry point (`async with AgentPool(...)`). It SHALL delegate agent creation to AgentFactory and expose HostContext via `get_context()`. It SHALL NO LONGER contain agent instantiation logic directly — that responsibility moves to AgentFactory.

#### Scenario: AgentPool context manager works unchanged

- **WHEN** `async with AgentPool("config.yml") as pool:` is used
- **THEN** the pool SHALL initialize infrastructure (MCP, storage, skills) as before
- **AND** SHALL construct a HostContext from the initialized infrastructure
- **AND** SHALL create an AgentFactory and use it for agent creation
- **AND** `pool.get_agent("name")` SHALL return the same agent as before M1

#### Scenario: AgentPool exposes HostContext

- **WHEN** `pool.get_context()` is called
- **THEN** a HostContext dataclass SHALL be returned with all infrastructure handles
- **AND** the HostContext SHALL be usable independently of the AgentPool instance

#### Scenario: AgentPool delegates to AgentFactory

- **WHEN** `pool.get_agent("coder")` is called
- **THEN** AgentPool SHALL delegate to `self._factory.compile()` or retrieve from the compiled registry
- **AND** AgentPool SHALL NOT contain direct agent instantiation code (model creation, tool injection, etc.)

### Requirement: AgentPool preserves all existing public API surface

AgentPool SHALL preserve all existing public methods and properties. This includes: `get_agent()`, `get_team()`, `agents` property, `manifest` property, `storage` property, `mcp` property, and all context manager methods.

#### Scenario: Existing code using AgentPool API works unchanged

- **WHEN** any existing test or user code calls `pool.get_agent()`, `pool.manifest`, `pool.storage`, or `pool.mcp`
- **THEN** the same results SHALL be returned as before M1
- **AND** no ImportError, AttributeError, or TypeError SHALL occur

#### Scenario: All existing tests pass

- **WHEN** `uv run pytest` is run after M1 changes
- **THEN** all tests that passed before M1 SHALL still pass
- **AND** no new test failures SHALL be introduced
