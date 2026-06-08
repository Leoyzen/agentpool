## ADDED Requirements

### Requirement: OpenCode Server routes all execution through SessionPool
The OpenCode Server SHALL NOT invoke `agent.run()` or `agent.run_stream()` directly on any agent instance for LLM-mediated operations. All **agent-mediated** execution paths — including message handling, slash commands, skill commands, session initialization, summarization, and MCP prompt commands — SHALL route through `SessionPool` (`receive_request()` for fire-and-forget operations, `run_stream()` for streaming endpoints). `SessionController.receive_request()` is the equivalent fire-and-forget entry point; `SessionController` does not provide a streaming equivalent to `SessionPool.run_stream()`.

#### Scenario: Message send routes through SessionPool
- **WHEN** a client sends a message via the OpenCode `send_message` endpoint
- **THEN** the request is processed by `SessionController.receive_request()`
- **AND** the agent execution is orchestrated by `TurnRunner` or `RunExecutor`
- **AND** the response events are published to `EventBus`

#### Scenario: Slash command uses SessionPool.run_stream
- **WHEN** a user invokes a slash command (via `_execute_slashed_command()`)
- **THEN** the command executes via `SessionPool.run_stream()` or EventBus subscription
- **AND** the command does NOT bypass turn isolation
- **AND** the response is streamed back to the caller
- **AND** the command executes within the session's turn isolation (enforced internally by SessionPool)
- **AND** there is no non-streaming skill slash command path in the current codebase

#### Scenario: Skill command uses SessionPool.run_stream
- **WHEN** a user invokes a skill command (via `_execute_skill_command()`)
- **THEN** the command executes via `SessionPool.run_stream()` or EventBus subscription
- **AND** the command executes within the session's turn isolation (enforced internally by SessionPool)
- **AND** there is no non-streaming skill command path in the current codebase

#### Scenario: Session initialization routes through SessionPool
- **WHEN** a client requests session initialization
- **THEN** `SessionPool.receive_request()` initiates a background turn for initialization
- **AND** the client receives immediate acknowledgment (session created, initialization started)
- **AND** initialization events are streamed via SSE as the turn progresses

#### Scenario: Shell execution does NOT route through SessionPool
- **WHEN** a client requests shell command execution
- **THEN** the command is executed directly via a standalone `Env` or `ProcessManager`
- **AND** the execution does NOT create a SessionPool turn or involve LLM reasoning
- **AND** the response is returned immediately to the caller

### Requirement: OpenCode Server eliminates shared agent usage for native agents
The OpenCode Server SHALL NOT use a shared native agent instance across multiple sessions for LLM-mediated operations. Each session SHALL have its own native agent instance managed by `SessionController.get_or_create_session_agent()`.

**Note on non-native agents**: ACP, ClaudeCode, and AGUI agents use shared singleton instances across sessions (this is a current limitation, not changed by this migration). SessionPool routes turns correctly for non-native agents, but per-session state isolation is only guaranteed for native agents.

#### Scenario: No shared native agent across sessions
- **WHEN** two OpenCode clients connect to different sessions
- **THEN** each session receives a distinct native agent instance from SessionPool
- **AND** agent state mutations in one session do not affect the other

#### Scenario: Non-native agent abort handles shared instance
- **WHEN** `abort_session` is called for a session using a non-native (shared) agent
- **THEN** the route cancels the session's `RunHandle` via `SessionPool.cancel_run(run_id)`
- **AND** the route does NOT call `agent.interrupt()` on the shared agent instance (to avoid killing all sessions using that agent)
- **AND** the route documents that abort for non-native agents only cancels the turn, not the underlying agent process

#### Scenario: Deprecated get_or_create_agent is removed
- **WHEN** code calls `ServerState.get_or_create_agent()`
- **THEN** the method raises `NotImplementedError` or is removed entirely
- **AND** all callers use `SessionController.get_or_create_session_agent()` instead

### Requirement: OpenCode Server delegates session CRUD to SessionController
OpenCode session lifecycle operations (create, fork, load, delete, list) SHALL delegate to `SessionController` APIs. `ServerState` SHALL NOT maintain parallel in-memory session tracking for agent resolution.

#### Scenario: Session creation delegates to SessionController
- **WHEN** a client requests a new OpenCode session
- **THEN** `SessionController.get_or_create_session()` is called (which creates sessions directly; `SessionPool.create_session()` delegates to it)
- **AND** the resulting `SessionState` is stored in `SessionController`
- **AND** `ServerState.sessions` is not used as a source of truth for agent resolution

#### Scenario: Session listing queries SessionController
- **WHEN** a client requests the list of active sessions
- **THEN** the route queries `SessionController` for active sessions
- **AND** does not read from `ServerState.sessions`

#### Scenario: Session status from SessionController
- **WHEN** a client queries session status
- **THEN** the status is read from `SessionState` via `SessionController`
- **AND** `ServerState.session_status` is not consulted for agent-related status

### Requirement: Feature flags enable incremental rollout
Each route category migrated to SessionPool SHALL be guarded by a startup-time feature flag on `agentpool_config.session_pool.OpenCodeConfig`. Flags are read from environment variables at server initialization and require restart to change.

#### Scenario: Slash commands behind feature flag
- **WHEN** the `use_session_pool_for_commands` flag is `False`
- **THEN** plain slash commands execute via the legacy direct path (`agent.run_stream()`)
- **WHEN** the `use_session_pool_for_skills` flag is `False`
- **THEN** skill commands execute via the legacy direct path (`agent.run_stream()`)
- **WHEN** either flag is `True`
- **THEN** the corresponding slash commands execute via `SessionPool.run_stream()` or EventBus subscription

#### Scenario: Gradual flag enablement
- **GIVEN** flags exist for commands, skills, init, summarize, and MCP prompts
- **WHEN** a flag is enabled in staging
- **THEN** only that route category uses SessionPool
- **AND** other categories continue using legacy paths
