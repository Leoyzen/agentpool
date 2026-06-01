## ADDED Requirements

### Requirement: AgentPool optionally composes SessionPool
The AgentPool SHALL conditionally create and manage a SessionPool instance based on configuration.

#### Scenario: SessionPool disabled by default
- **WHEN** AgentPool is initialized without enable_session_pool
- **THEN** self.session_pool is None and existing behavior is unchanged

#### Scenario: SessionPool enabled via constructor
- **WHEN** AgentPool is initialized with enable_session_pool=True
- **THEN** a SessionPool is created and stored in self.session_pool

#### Scenario: SessionPool started on context entry
- **WHEN** AgentPool enters async context (__aenter__)
- **THEN** SessionPool.start() is called if SessionPool is enabled

#### Scenario: SessionPool shutdown on context exit
- **WHEN** AgentPool exits async context (__aexit__)
- **THEN** SessionPool.shutdown() is called if SessionPool is enabled

### Requirement: YAML configuration supports session pool settings
The configuration schema SHALL accept session_pool settings in the YAML manifest.

#### Scenario: Minimal session pool config
- **WHEN** a config file contains session_pool: { enabled: true }
- **THEN** AgentPool creates a SessionPool with default settings

#### Scenario: Full session pool config
- **WHEN** a config file contains session_pool with all options
- **THEN** AgentPool creates a SessionPool with the specified settings

#### Scenario: Per-protocol feature flags
- **WHEN** a config file contains acp.use_session_pool: true
- **THEN** the ACP handler uses SessionPool for session management

### Requirement: AgentPool provides session creation shortcut
The AgentPool SHALL expose a convenience method for creating sessions through the SessionPool.

#### Scenario: Create session via AgentPool
- **WHEN** pool.create_session(session_id, agent_name) is called with SessionPool enabled
- **THEN** the session is created via SessionPool.create_session()

#### Scenario: Create session without SessionPool
- **WHEN** pool.create_session() is called without SessionPool enabled
- **THEN** a RuntimeError is raised with a clear message
