## ADDED Requirements

### Requirement: Subagent tools SHALL register target agents in a runtime registry

When pool-level agent registration is removed, subagent tools SHALL register their target agent config in a `RuntimeAgentRegistry` at tool-creation time. `SessionController.get_or_create_session_agent()` SHALL check the runtime registry before falling back to manifest lookup. This ensures programmatically-created agents (not in YAML manifest) are discoverable for session creation.

- The `RuntimeAgentRegistry` SHALL be a simple `dict[str, AgentConfig]` with `register()` and `lookup()` methods
- Subagent tools SHALL call `registry.register(agent_name, agent_config)` when the tool is created
- `get_or_create_session_agent()` SHALL check `_session_agents` cache first, then `RuntimeAgentRegistry`, then `manifest.agents`
- If the agent is not found in any source, `RuntimeError` SHALL be raised with a clear message

#### Scenario: Subagent tool creates child session
- **WHEN** a subagent tool creates a child session for an agent not in the YAML manifest
- **THEN** the agent config SHALL be found in the `RuntimeAgentRegistry`
- **AND** `get_or_create_session_agent()` SHALL return the agent without raising `RuntimeError`
- **AND** the child session SHALL be created with the correct agent instance

#### Scenario: Subagent tool with YAML manifest agent
- **WHEN** a subagent tool creates a child session for an agent defined in the YAML manifest
- **THEN** the agent config SHALL be found in the manifest
- **AND** the runtime registry lookup SHALL be skipped (cache hit or manifest hit)
- **AND** no duplicate registration SHALL occur

#### Scenario: Unknown agent name
- **WHEN** `get_or_create_session_agent()` is called with an agent name not in cache, runtime registry, or manifest
- **THEN** `RuntimeError` SHALL be raised with message `"Agent config not found: '<name>'"`
- **AND** the error message SHALL list available agents from manifest and runtime registry

### Requirement: BaseAgent SHALL generate ephemeral sessions without pool

When `agent_pool is None`, `BaseAgent.run()` and `BaseAgent.run_stream()` SHALL generate an ephemeral session ID using `uuid4()`. The run context (`get_active_run_context()`, `is_turn_active()`) SHALL work with a local `_run_context` variable when no pool session exists. The ephemeral session SHALL be cleaned up when the run completes.

- `BaseAgent.run()` SHALL check `self.agent_pool is None` and use standalone path if so
- The standalone path SHALL create a `RunContext` with a generated ephemeral session ID
- `get_active_run_context()` SHALL return the local `_run_context` when no pool session exists
- `is_turn_active()` SHALL return `True` when `_run_context` is set and the run is in progress

#### Scenario: Standalone agent run
- **WHEN** `BaseAgent.run()` is called on an agent with `agent_pool is None`
- **THEN** an ephemeral session ID SHALL be generated
- **AND** a `RunContext` SHALL be created with the ephemeral session ID
- **AND** `get_active_run_context()` SHALL return the local run context
- **AND** `is_turn_active()` SHALL return `True` during the run
- **AND** after the run completes, `is_turn_active()` SHALL return `False`

#### Scenario: Pool-backed agent run
- **WHEN** `BaseAgent.run()` is called on an agent with `agent_pool` set
- **THEN** the pool session path SHALL be used (unchanged behavior)
- **AND** `get_active_run_context()` SHALL return the pool session's run context
